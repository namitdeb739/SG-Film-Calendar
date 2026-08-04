"""Scraper for the Asian Film Archive's screenings at Oldham Theatre.

The AFA site is WordPress; its screenings are a WooCommerce Event Manager custom
post type exposed through the standard WP REST API at
``/wp-json/wp/v2/mep_events``. Each post carries a clean, structured
``event_informations`` block with a local start/end datetime, a poster image and
seat counts — far more reliable than the page's schema.org JSON-LD, whose dates
are systematically broken (1am starts, "Rescheduled" artefacts).

Two kinds of noise are filtered out: ``[TALK]`` events (not films) and umbrella
"programme" posts that bundle several screenings (these carry
``linked_events_or_items`` and duplicate the individual film posts). We emit one
``film`` dict per screening — grouping repeat dates of the same title — in the
shape the rest of the pipeline consumes, so downstream is unchanged.
"""

import html
import json
import re
from datetime import datetime
from typing import Dict, List, Optional

from scrapling.fetchers import Fetcher

API_URL = (
    "https://asianfilmarchive.org/wp-json/wp/v2/mep_events"
    "?per_page=100&orderby=date&order=desc"
)

# AFA is the resident programmer at Oldham Theatre (National Archives Building);
# normalize_venue maps this to its canonical name for the site's cinema list.
VENUE = "Oldham Theatre"

# Curatorial-programme label for the historic archive's theme axis.
THEME = "Asian Film Archive"


class AsianFilmArchiveScraper:
    """Scrape AFA / Oldham Theatre screenings from the WP REST API."""

    API_URL = API_URL
    VENUE = VENUE

    def __init__(self, reference_date: Optional[datetime] = None) -> None:
        self.reference_date = reference_date or datetime.now()

    def scrape(self) -> List[Dict]:
        """Fetch and parse upcoming AFA screenings."""
        response = Fetcher.get(self.API_URL)
        events = json.loads(response.body.decode("utf-8", errors="ignore"))
        return self._build_films(events if isinstance(events, list) else [])

    # -- normalisation -------------------------------------------------------

    def _build_films(self, events: List[Dict]) -> List[Dict]:
        """Group event posts into per-film dicts, one screening each."""
        films_by_title: Dict[str, Dict] = {}
        for event in events:
            self._merge_event(event, films_by_title)
        return list(films_by_title.values())

    def _merge_event(self, event: Dict, films_by_title: Dict[str, Dict]) -> None:
        """Add one screening to the film dict for its cleaned title."""
        screening = self._screening(event)
        if screening is None:
            return
        title = self._clean_title(self._raw_title(event))
        film = films_by_title.get(title)
        if film is None:
            film = self._film_dict(title, event)
            films_by_title[title] = film
        film["screenings"].append(screening)

    def _film_dict(self, title: str, event: Dict) -> Dict:
        """Build a pipeline-shaped film dict from one event post."""
        info = event.get("event_informations") or {}
        return {
            "title": title,
            "url": event.get("link") or "",
            "year": self._year(self._raw_title(event)),
            "duration_mins": 120,
            "rating": "",
            "genre": "",
            "director": "",
            "cast": "",
            "language": "",
            "country": "",
            "synopsis": self._strip_html(
                (event.get("excerpt") or {}).get("rendered") or ""
            ),
            "poster_url": self._first(info, "event_feature_image"),
            "themes": [THEME],
            "tags": [],
            "venue": self.VENUE,
            "source": "afa",
            "screenings": [],
        }

    def _screening(self, event: Dict) -> Optional[Dict]:
        """Build a screening dict for a dated, non-talk, non-bundle film event."""
        if not self._is_screening(event):
            return None
        info = event.get("event_informations") or {}
        start = self._parse_dt(self._first(info, "event_start_datetime"))
        if start is None or start < self.reference_date:
            return None
        end = self._parse_dt(self._first(info, "event_end_datetime")) or start
        booking = self._first(info, "booking_url") or event.get("link") or ""
        return {
            "start": start,
            "end": end,
            "booking_url": booking,
            "time_str": start.strftime("%-I:%M %p").lstrip("0"),
            "tags": self._screening_tags(self._raw_title(event)),
            "sold_out": self._sold_out(info),
        }

    # -- helpers -------------------------------------------------------------

    def _is_screening(self, event: Dict) -> bool:
        """Keep films only: drop talks and umbrella programme bundles."""
        title = self._raw_title(event).strip().upper()
        if title.startswith("[TALK"):
            return False
        # Bundles link their child screenings and duplicate them; skip them.
        info = event.get("event_informations") or {}
        linked = self._first(info, "linked_events_or_items")
        return not (linked and linked not in ("0", ""))

    @staticmethod
    def _raw_title(event: Dict) -> str:
        """The event title as plain text (HTML entities/tags removed)."""
        rendered = (event.get("title") or {}).get("rendered") or ""
        return html.unescape(re.sub(r"<[^>]+>", "", rendered)).strip()

    @staticmethod
    def _clean_title(title: str) -> str:
        """Drop the trailing "(YYYY)" and any "+ cast & crew Q&A" suffix."""
        title = re.split(r"\s+\+\s+", title, maxsplit=1)[0]
        title = re.sub(r"\s*\(\d{4}\)\s*$", "", title)
        return title.strip()

    @staticmethod
    def _year(title: str) -> str:
        """The production year from a trailing "(YYYY)" in the title, if any."""
        match = re.search(r"\((\d{4})\)", title)
        return match.group(1) if match else ""

    @staticmethod
    def _screening_tags(title: str) -> List[str]:
        """Visible labels for the screening (currently just a Q&A flag)."""
        return ["Q&A"] if re.search(r"q\s*&\s*a", title, re.IGNORECASE) else []

    @staticmethod
    def _parse_dt(value: str) -> Optional[datetime]:
        """Parse MEP's local ``YYYY-MM-DD HH:MM:SS`` timestamp (naive SGT)."""
        if not value:
            return None
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    @staticmethod
    def _sold_out(info: Dict) -> bool:
        """No seats left means sold out (seat count is a plain string)."""
        return AsianFilmArchiveScraper._first(info, "mep_total_seat_left") == "0"

    @staticmethod
    def _first(info: Dict, key: str) -> str:
        """MEP meta values arrive as single-element lists; return the value."""
        value = info.get(key)
        if isinstance(value, list):
            return str(value[0]).strip() if value else ""
        return str(value).strip() if value else ""

    @staticmethod
    def _strip_html(text: str) -> str:
        """Collapse rich-text HTML into plain synopsis prose."""
        if not text:
            return ""
        without_tags = re.sub(r"<[^>]+>", " ", text)
        unescaped = html.unescape(without_tags)
        return re.sub(r"\s+", " ", unescaped).strip()
