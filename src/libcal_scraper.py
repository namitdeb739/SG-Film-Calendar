"""Scraper for the National Library Board's film screenings (LibCal).

NLB publishes its public programmes — including a recurring film series ("The Big
Picture", a fortnightly screening at the Central Arts Library) plus occasional
festival screenings — on Springshare **LibCal**. Most NLB events aren't films, so
this scraper discovers only the film screenings via LibCal's public search
endpoint, filtering on the "film screening" keyword the programme titles carry.

``process_search.php`` returns one JSON record per event with everything we need
already structured — a start/end datetime in local time, the venue, the poster,
and the synopsis — so, unlike the Eventive/Peatix sources, there is no HTML
parsing and no timezone conversion. We emit one ``film`` dict per event (grouping
any same-titled repeats into one dict with multiple screenings) in the exact
shape ``calendar_sync``/``history``/``site_export`` already consume, so the
downstream pipeline is unchanged.
"""

import html
import json
import re
from datetime import datetime
from typing import Dict, List, Optional

from scrapling.fetchers import Fetcher

BASE_URL = "https://nlb.libcal.com"

# Public LibCal site id for NLB's GoLibrary calendar (from the calendar page's
# client-side JS). ``cals=-1`` searches every NLB calendar.
SITE_ID = "27180"

# NLB's film programmes all title themselves "... Film Screening ...", so a
# keyword search is a precise, low-noise way to pull films out of a calendar
# that is overwhelmingly non-film (workshops, talks, exhibitions).
SEARCH_QUERY = "film screening"

# Curatorial-programme label for the historic archive's theme axis.
THEME = "NLB Film Screenings"

# NLB buries the film's real title, synopsis and monthly theme inside one HTML
# "description" blob with labelled sections; these pull the useful parts out and
# leave the registration/photography/accessibility boilerplate behind.
_FILM_TITLE_RE = re.compile(
    r"Film Title:\s*(.+?)(?:\s*Important Note:|\s*Synopsis:|$)", re.IGNORECASE
)
_QUOTED_TITLE_RE = re.compile(r'^\s*"(.+?)"\s+Film Screening\b')
_SYNOPSIS_RE = re.compile(r"(?:Film Synopsis|Synopsis)\s*:\s*(.+)", re.IGNORECASE)
_SYNOPSIS_STOP_RE = re.compile(
    r"\s*(?:Advisory:|Watch a trailer|About:|About the Programme|Important Note:|"
    r"Registration and Attendance|Registration is required|"
    r"Photography and Videography|Wheelchair)",
    re.IGNORECASE,
)
_THEME_RE = re.compile(
    r"This Month'?s Theme:\s*(.+?)\s*(?:Film Synopsis:|Synopsis:|$)", re.IGNORECASE
)


class NLBLibCalScraper:
    """Scrape NLB film screenings from LibCal's public search JSON."""

    BASE_URL = BASE_URL
    SITE_ID = SITE_ID
    SEARCH_QUERY = SEARCH_QUERY

    def __init__(self, reference_date: Optional[datetime] = None) -> None:
        self.reference_date = reference_date or datetime.now()

    def scrape(self) -> List[Dict]:
        """Fetch and parse upcoming NLB film screenings."""
        results = self._fetch_results()
        return self._build_films(results)

    # -- fetch ---------------------------------------------------------------

    def _fetch_results(self) -> List[Dict]:
        """Return the raw event records from LibCal's search endpoint."""
        url = (
            f"{self.BASE_URL}/process_search.php"
            f"?site_id={self.SITE_ID}&cals=-1&perpage=100&page=1"
            f"&q={self.SEARCH_QUERY.replace(' ', '%20')}"
            f"&audience=&cats=&camps=&inc=0"
        )
        response = Fetcher.get(url)
        payload = json.loads(response.body.decode("utf-8", errors="ignore"))
        return payload.get("results") or []

    # -- normalisation -------------------------------------------------------

    def _build_films(self, results: List[Dict]) -> List[Dict]:
        """Group event records into per-film dicts the pipeline expects.

        Each record is one screening. A recurring title (same cleaned name) with
        several dates collapses into one film dict with multiple screenings,
        matching the grain the other scrapers emit.
        """
        films_by_title: Dict[str, Dict] = {}
        for event in results:
            self._merge_event(event, films_by_title)
        return list(films_by_title.values())

    def _merge_event(self, event: Dict, films_by_title: Dict[str, Dict]) -> None:
        """Add one event's screening to the film dict for its film title."""
        if not self._is_film(event):
            return
        screening = self._screening(event)
        if screening is None:
            return

        title = self._film_title(event)
        film = films_by_title.get(title)
        if film is None:
            film = self._film_dict(title, event)
            films_by_title[title] = film
        film["screenings"].append(screening)

    def _film_dict(self, title: str, event: Dict) -> Dict:
        """Build a pipeline-shaped film dict from one LibCal event record."""
        description = event.get("description") or ""
        return {
            "title": title,
            "url": event.get("url") or "",
            "year": "",
            # LibCal only exposes the room booking slot, not the film's runtime,
            # so use the neutral default rather than mislead with the slot length.
            "duration_mins": 120,
            "rating": self._advisory_rating(description),
            "genre": "",
            "director": "",
            "cast": "",
            "language": self._language(event),
            "country": "",
            "synopsis": self._synopsis(description),
            "poster_url": event.get("featured_image") or "",
            "themes": self._themes(description),
            "tags": [],
            "venue": (event.get("location") or "").strip() or "National Library",
            "source": "nlb",
            "screenings": [],
        }

    def _screening(self, event: Dict) -> Optional[Dict]:
        """Build a screening dict from a dated event record."""
        start = self._parse_dt(event.get("startdt"))
        if start is None:
            return None
        # Skip screenings that have already happened, like the live scrapers.
        if start < self.reference_date:
            return None
        end = self._parse_dt(event.get("enddt")) or start
        return {
            "start": start,
            "end": end,
            "booking_url": event.get("url") or "",
            "time_str": (event.get("start") or "").strip(),
            "sold_out": self._sold_out(event),
        }

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _is_film(event: Dict) -> bool:
        """Keep only genuine film screenings (guard against keyword noise)."""
        title = (event.get("title") or "").lower()
        return "film" in title

    @staticmethod
    def _clean_title(title: str) -> str:
        """Strip LibCal's trailing " | <campus/series>" suffix from a title."""
        return title.split(" | ", 1)[0].strip()

    @staticmethod
    def _parse_dt(value: Optional[str]) -> Optional[datetime]:
        """Parse LibCal's local ``YYYY-MM-DD HH:MM:SS`` timestamp (naive SGT)."""
        if not value:
            return None
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    def _film_title(self, event: Dict) -> str:
        """Best available film title.

        Festival screenings label the real film ("Film Title: ...") or quote it
        in the event title ('"X" Film Screening'); the recurring Big Picture
        series names only itself, so we keep its series title (the actual film is
        only in the synopsis — see the Tier 3 enrichment issue).
        """
        description = self._strip_html(event.get("description") or "")
        match = _FILM_TITLE_RE.search(description)
        if match and match.group(1).strip():
            return match.group(1).strip()
        raw = event.get("title") or ""
        quoted = _QUOTED_TITLE_RE.search(raw)
        if quoted:
            return quoted.group(1).strip()
        return self._clean_title(raw)

    def _synopsis(self, description: str) -> str:
        """Just the labelled film synopsis, dropping NLB's registration boilerplate.

        Returns "" when no synopsis label is present rather than falling back to
        the whole description, which would surface the registration/photography
        boilerplate instead of a synopsis.
        """
        text = self._strip_html(description)
        match = _SYNOPSIS_RE.search(text)
        if not match:
            return ""
        return _SYNOPSIS_STOP_RE.split(match.group(1), maxsplit=1)[0].strip()

    def _themes(self, description: str) -> List[str]:
        """The programme label plus any "This Month's Theme" the series carries."""
        themes = [THEME]
        match = _THEME_RE.search(self._strip_html(description))
        if match and match.group(1).strip():
            themes.append(match.group(1).strip())
        return sorted(set(themes))

    @staticmethod
    def _language(event: Dict) -> str:
        """Screening language(s) from LibCal's "Language > X" categories."""
        langs = []
        for cat in event.get("categories_arr") or []:
            name = cat.get("name") or ""
            if name.lower().startswith("language >"):
                langs.append(name.split(">", 1)[1].strip())
        return "|".join(dict.fromkeys(langs))

    @staticmethod
    def _sold_out(event: Dict) -> bool:
        """A registrable event with no seats left is sold out."""
        if not event.get("registration_enabled"):
            return False
        seats_left = event.get("seatsleft")
        return isinstance(seats_left, int) and seats_left <= 0

    @staticmethod
    def _advisory_rating(description: str) -> str:
        """Pull the advisory rating (e.g. "PG", "PG-13") out of the description."""
        text = NLBLibCalScraper._strip_html(description)
        match = re.search(r"Advisory:\s*([A-Z0-9]+(?:-[A-Z0-9]+)?)", text)
        return match.group(1) if match else ""

    @staticmethod
    def _strip_html(text: str) -> str:
        """Collapse LibCal's rich-text HTML into plain synopsis prose."""
        if not text:
            return ""
        without_tags = re.sub(r"<[^>]+>", " ", text)
        unescaped = html.unescape(without_tags)
        return re.sub(r"\s+", " ", unescaped).strip()
