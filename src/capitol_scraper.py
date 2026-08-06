"""Scraper for the Classics at Capitol film series at Capitol Theatre.

Capitol Theatre sells tickets through BigTix (BookMyShow SEA), a single-page
app. Its film screenings are the site's public "movies" collection, exposed as
clean JSON at ``/api/v2/public/live/collections/movies/items`` — one entry per
film with a rich free-text ``description`` (year, director, runtime, rating,
cast). That listing carries no session times, so for each film we read its
server-rendered detail page, whose embedded data carries the screening
datetimes (UTC) and the external SISTIC booking link.

We emit the usual film/screening dicts (venue "Capitol Theatre", source
"capitol") so the downstream pipeline is unchanged.

Note: the detail page exposes a film's first and last screening datetimes; the
Classics series screens each title on one or two dates, so that is its full
schedule. A title ever shown on 3+ non-contiguous dates could miss the middle
ones — revisit if the programme changes shape.
"""

import html
import json
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from scrapling.fetchers import Fetcher

BASE_URL = "https://capitol-theatre.bigtix.io"
COLLECTION_URL = (
    f"{BASE_URL}/api/v2/public/live/collections/movies/items"
    "?lang=en-GB&states=Singapore"
)
VENUE = "Capitol Theatre"
THEME = "Classics at Capitol"
TIMEZONE = ZoneInfo("Asia/Singapore")

_YEAR = re.compile(r"(\d{4})\s*[.,]\s*Dir", re.IGNORECASE)
_DIRECTOR = re.compile(r"Dir(?:ector)?[:.]?\s*([^,\n]+)", re.IGNORECASE)
_DURATION = re.compile(r"(\d+)\s*min", re.IGNORECASE)
_RATING = re.compile(r"\((PG13|PG|NC16|M18|R21|G)\)")
_CAST = re.compile(r"Starring\s+([^\n]+)", re.IGNORECASE)
# The detail page embeds this JSON as escaped RSC flight data, so the quotes
# may be backslash-escaped (\") — tolerate either form.
_SESSION_DT = re.compile(
    r'(?:startDate|endDate)\\?":\\?"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)'
)
_EXTERNAL_LINK = re.compile(r'externalLink\\?":\\?"(https?://[^"\\]+)')


class CapitolClassicsScraper:
    """Scrape Classics at Capitol screenings from BigTix's public API."""

    COLLECTION_URL = COLLECTION_URL
    BASE_URL = BASE_URL
    VENUE = VENUE

    def __init__(self, reference_date: Optional[datetime] = None) -> None:
        self.reference_date = reference_date or datetime.now()

    def scrape(self) -> List[Dict]:
        """Fetch and parse upcoming Classics at Capitol screenings."""
        response = Fetcher.get(self.COLLECTION_URL)
        payload = json.loads(response.body.decode("utf-8", errors="ignore"))
        items = payload.get("data") or []
        films = []
        for item in items:
            film = self._film(item)
            if film and film["screenings"]:
                films.append(film)
        return films

    # -- per-film ------------------------------------------------------------

    def _film(self, item: Dict) -> Optional[Dict]:
        """Build a film dict (with screenings from its detail page)."""
        content = item.get("content") or {}
        slug = (content.get("slug") or {}).get("name") or ""
        code = item.get("code") or ""
        if not slug or not code:
            return None
        description = content.get("description") or ""
        detail = self._fetch_detail(slug, code)
        booking_url, session_starts = self._sessions(detail, slug, code)
        duration = self._int(self._search(_DURATION, description)) or 120
        tags = ["4K"] if re.search(r"\b4K\b", description) else []
        screenings = self._screenings(
            session_starts, booking_url, duration, bool(item.get("stopSales")), tags
        )
        if not screenings:
            return None
        return {
            "title": self._clean_title(item.get("name") or ""),
            "url": f"{self.BASE_URL}/en/events/{slug}/{code}",
            "year": self._search(_YEAR, description),
            "duration_mins": duration,
            "rating": self._search(_RATING, description),
            "genre": "",
            "director": self._search(_DIRECTOR, description).strip(),
            "cast": self._search(_CAST, description).strip(),
            "language": "",
            "country": "",
            "synopsis": self._synopsis(description),
            "poster_url": self._poster(content.get("cardImageUrl") or ""),
            "themes": [THEME],
            "tags": tags,
            "venue": self.VENUE,
            "source": "capitol",
            "screenings": screenings,
        }

    def _fetch_detail(self, slug: str, code: str) -> str:
        """Return the detail page HTML, or "" if it can't be fetched."""
        try:
            response = Fetcher.get(f"{self.BASE_URL}/en/events/{slug}/{code}")
            return response.body.decode("utf-8", errors="ignore")
        except Exception as exc:  # noqa: BLE001 - one bad detail page isn't fatal
            print(f"  ! failed to fetch Capitol detail for {code}: {exc}")
            return ""

    def _sessions(self, detail: str, slug: str, code: str):
        """Return (booking_url, [session start datetimes]) from a detail page."""
        booking = self._search(_EXTERNAL_LINK, detail)
        booking_url = booking or f"{self.BASE_URL}/en/events/{slug}/{code}"
        starts = []
        for raw in dict.fromkeys(_SESSION_DT.findall(detail)):
            dt = self._utc_to_local(raw)
            if dt is not None:
                starts.append(dt)
        return booking_url, sorted(starts)

    def _screenings(
        self,
        starts: List[datetime],
        booking_url: str,
        duration: int,
        sold_out: bool,
        tags: List[str],
    ) -> List[Dict]:
        """Build screening dicts for the future session datetimes."""
        screenings = []
        for start in starts:
            if start < self.reference_date:
                continue
            screenings.append(
                {
                    "start": start,
                    "end": start + timedelta(minutes=duration),
                    "booking_url": booking_url,
                    "time_str": start.strftime("%-I:%M %p").lstrip("0"),
                    "tags": list(tags),
                    "sold_out": sold_out,
                }
            )
        return screenings

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _utc_to_local(raw: str) -> Optional[datetime]:
        """Parse a ``YYYY-MM-DD HH:MM:SS`` UTC timestamp to naive local (SGT)."""
        try:
            utc = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=ZoneInfo("UTC")
            )
        except ValueError:
            return None
        return utc.astimezone(TIMEZONE).replace(tzinfo=None)

    @staticmethod
    def _clean_title(name: str) -> str:
        """Drop the "Classics at Capitol -" prefix; title-case shouty names."""
        title = re.sub(
            r"^\s*classics at capitol\s*[-:–]\s*", "", name, flags=re.IGNORECASE
        ).strip()
        if title and not any(c.islower() for c in title):
            title = title.title()
        return title or name.strip()

    @staticmethod
    def _synopsis(description: str) -> str:
        """The synopsis prose, dropping the credits block before it."""
        parts = re.split(
            r"Starring[^\n]*\n", description, maxsplit=1, flags=re.IGNORECASE
        )
        body = parts[1] if len(parts) > 1 else description
        return re.sub(r"\s+", " ", html.unescape(body)).strip()

    @staticmethod
    def _poster(url: str) -> str:
        """Absolutise a BookMyShow CDN image path."""
        url = url.strip()
        if url and not url.startswith("http"):
            return f"https://{url}"
        return url

    @staticmethod
    def _search(pattern: re.Pattern, text: str) -> str:
        """First capture group of ``pattern`` in ``text``, or ""."""
        match = pattern.search(text or "")
        return match.group(1) if match else ""

    @staticmethod
    def _int(value: str) -> int:
        """Parse an int, returning 0 when blank/invalid."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
