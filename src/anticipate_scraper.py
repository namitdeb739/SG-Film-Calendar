"""Distributor attribution for Anticipate Pictures.

Anticipate Pictures is a *distributor*, not a venue: it acquires arthouse /
indie / documentary titles for Singapore and books them into whichever cinema
will host them — currently Filmhouse, but also Oldham Theatre, SFS Somerset,
the Esplanade and others over time. So it is not tied to one venue, and it
publishes no showtimes of its own; its site is a catalogue that links out to
each film's booking page at the hosting venue.

This therefore does not emit screenings. Instead it reads Anticipate's current
slate (the "Now Showing"/"Coming Soon" films on its home page, each an anchor
whose href is the venue booking link and whose text is the film title) and
*annotates* the already-scraped films with a ``distributor`` attribution — so an
Anticipate title screening at Filmhouse (or Oldham, or SFS) is tagged in place
rather than duplicated.

Matching is by two keys, most precise first:
  1. the booking URL (e.g. a Filmhouse ``/film/<id>`` link, or an event-page URL
     shared with the venue scrape), and
  2. the normalised film title, as a cross-venue fallback.
"""

import html
import re
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from scrapling.fetchers import Fetcher

HOME_URL = "https://anticipatepictures.com/"
DISTRIBUTOR = "Anticipate Pictures"

# Hosts Anticipate links to when sending viewers off to book a screening.
_BOOKING_HOSTS = re.compile(
    r"filmhouse\.sg|esplanade\.com|asianfilmarchive\.org|"
    r"singaporefilmsociety|eventive|sistic|peatix",
    re.IGNORECASE,
)
_ANCHOR = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_FILMHOUSE_ID = re.compile(r"filmhouse\.sg/film/(\d+)", re.IGNORECASE)
# Qualifiers Filmhouse/Anticipate append to a title that shouldn't block a match.
_TITLE_NOISE = re.compile(
    r"\b(4k|imax|restoration|restored|remastered|"
    r"director'?s cut|\d+(?:th|st|nd|rd)? anniversary)\b",
    re.IGNORECASE,
)


class AnticipatePicturesScraper:
    """Tag scraped films distributed by Anticipate Pictures."""

    HOME_URL = HOME_URL
    DISTRIBUTOR = DISTRIBUTOR

    def __init__(self, reference_date: Optional[datetime] = None) -> None:
        self.reference_date = reference_date or datetime.now()

    def annotate(self, films: List[Dict]) -> int:
        """Set ``distributor`` on every film Anticipate distributes. Returns count."""
        keys, titles = self._distributed_slate()
        tagged = 0
        for film in films:
            if self._is_anticipate(film, keys, titles):
                film["distributor"] = self.DISTRIBUTOR
                tagged += 1
        return tagged

    # -- fetch + parse -------------------------------------------------------

    def _distributed_slate(self) -> Tuple[Set[str], Set[str]]:
        """Return Anticipate's current (booking-url keys, normalised titles)."""
        response = Fetcher.get(self.HOME_URL)
        markup = response.body.decode("utf-8", errors="ignore")
        keys: Set[str] = set()
        titles: Set[str] = set()
        for href, text in _ANCHOR.findall(markup):
            if not _BOOKING_HOSTS.search(href):
                continue
            keys.add(self._booking_key(href))
            # The anchor text is the film title (call-to-action anchors like
            # "Find out more" simply won't match any real film title later).
            norm = self._norm_title(text)
            if norm:
                titles.add(norm)
        return keys, titles

    # -- matching ------------------------------------------------------------

    def _is_anticipate(self, film: Dict, keys: Set[str], titles: Set[str]) -> bool:
        """True if this scraped film is on Anticipate's slate (url or title)."""
        film_keys = {self._booking_key(u) for u in self._film_urls(film) if u}
        if film_keys & keys:
            return True
        return self._norm_title(film.get("title", "")) in titles

    @staticmethod
    def _film_urls(film: Dict) -> List[str]:
        """Every bookable URL a scraped film exposes (film page + screenings)."""
        urls = [film.get("url", "")]
        for scr in film.get("screenings") or []:
            urls.append(scr.get("booking_url", ""))
        return urls

    @staticmethod
    def _booking_key(url: str) -> str:
        """A venue-agnostic key for a booking URL.

        Filmhouse collapses to ``filmhouse:<id>`` (its stable film id); anything
        else to ``host/path`` so an event page shared with a venue scrape still
        matches.
        """
        match = _FILMHOUSE_ID.search(url)
        if match:
            return f"filmhouse:{match.group(1)}"
        stripped = re.sub(r"^https?://(?:www\.)?", "", url.strip(), flags=re.IGNORECASE)
        return stripped.split("?")[0].split("#")[0].rstrip("/").lower()

    @staticmethod
    def _norm_title(text: str) -> str:
        """Normalise a title for matching across sources' casing/qualifiers."""
        plain = html.unescape(re.sub(r"<[^>]+>", " ", text)).lower()
        plain = _TITLE_NOISE.sub(" ", plain)
        return re.sub(r"[^a-z0-9]+", " ", plain).strip()
