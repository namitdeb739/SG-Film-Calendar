"""Backfill missing film metadata from OMDb.

Several sources (NLB, SFS, the Asian Film Archive, Capitol) publish an event,
not a catalogued film, so their records lack director / genre / cast / country /
language / runtime and often a real poster. This enriches those thin records
against the OMDb API, matching by title (and production year when known) and
filling **only fields that are empty** — it never overwrites data a source
already provides (e.g. Filmhouse's own credits).

Lookups are cached to ``data/omdb_cache.json`` (committed like the history CSV)
so the daily run makes only a few new calls and stays within OMDb's free tier.
The API key comes from ``OMDB_API_KEY``; with no key the step is a no-op, so the
pipeline runs unchanged without it.
"""

import json
import os
import re
from typing import Dict, List, Optional
from urllib.parse import quote

from scrapling.fetchers import Fetcher

API_URL = "https://www.omdbapi.com/"
CACHE_PATH = "data/omdb_cache.json"

# OMDb field -> our film field, for fields we fill only when ours is empty.
_TEXT_FIELDS = {
    "Director": "director",
    "Genre": "genre",
    "Actors": "cast",
    "Country": "country",
    "Language": "language",
    "Plot": "synopsis",
    "Year": "year",
    "Poster": "poster_url",
}
# Runtimes we treat as "unknown" and therefore fill from OMDb.
_DEFAULT_DURATIONS = {0, 120}


class OMDbEnricher:
    """Fill missing film metadata from OMDb, only where a field is empty."""

    API_URL = API_URL

    def __init__(
        self, api_key: Optional[str] = None, cache_path: str = CACHE_PATH
    ) -> None:
        self.api_key = (
            api_key if api_key is not None else os.environ.get("OMDB_API_KEY", "")
        )
        self.cache_path = cache_path
        self.cache: Dict[str, Optional[Dict]] = self._load_cache()

    def enrich(self, films: List[Dict]) -> int:
        """Backfill every thin film in place. Returns how many were enriched."""
        if not self.api_key:
            return 0
        enriched = 0
        for film in films:
            if not self._is_thin(film):
                continue
            data = self._lookup(film.get("title", ""), film.get("year", ""))
            if data and self._apply(film, data):
                enriched += 1
        self._save_cache()
        return enriched

    # -- matching / application ---------------------------------------------

    @staticmethod
    def _is_thin(film: Dict) -> bool:
        """A film worth a lookup lacks its director or genre (rich sources don't)."""
        return not (film.get("director") and film.get("genre"))

    def _apply(self, film: Dict, data: Dict) -> bool:
        """Fill empty fields from an OMDb record. Returns True if anything changed."""
        changed = False
        for omdb_key, field in _TEXT_FIELDS.items():
            if not str(film.get(field) or "").strip():
                value = self._clean(data.get(omdb_key))
                if value:
                    film[field] = value
                    changed = True
        if film.get("duration_mins") in _DEFAULT_DURATIONS:
            runtime = self._runtime(data.get("Runtime"))
            if runtime:
                film["duration_mins"] = runtime
                changed = True
        return changed

    # -- lookup / cache -----------------------------------------------------

    def _lookup(self, title: str, year: str) -> Optional[Dict]:
        """Return the OMDb record for a title (cached), or None if not found."""
        title = (title or "").strip()
        if not title:
            return None
        key = f"{title.lower()}|{year or ''}"
        if key in self.cache:
            return self.cache[key]
        data = None
        for candidate in self._title_variants(title):
            # Prefer a year-qualified match; fall back to title-only (a source's
            # year may be the screening year rather than the production year).
            data = self._fetch(candidate, year) or (
                self._fetch(candidate, "") if year else None
            )
            if data:
                break
        self.cache[key] = data
        return data

    @staticmethod
    def _title_variants(title: str) -> List[str]:
        """Titles to try against OMDb, best match first.

        OMDb catalogues titles with "and" spelled out, and an "&" query does not
        simply miss — it matches a companion title instead (e.g. "Raya & the Last
        Dragon" returns the "Untold with the Filmmakers of..." featurette, whose
        credits and poster are the wrong film's). Try the spelled-out form first.
        """
        spelled = re.sub(r"\s*&\s*", " and ", title)
        return [spelled, title] if spelled != title else [title]

    def _fetch(self, title: str, year: str) -> Optional[Dict]:
        """One OMDb request; None on a miss or error."""
        url = f"{self.API_URL}?apikey={self.api_key}&type=movie&t={quote(title)}"
        if year:
            url += f"&y={quote(str(year))}"
        try:
            body = Fetcher.get(url).body.decode("utf-8", errors="ignore")
            data = json.loads(body)
        except Exception as exc:  # noqa: BLE001 - a bad lookup shouldn't be fatal
            print(f"  ! OMDb lookup failed for {title!r}: {exc}")
            return None
        return data if data.get("Response") == "True" else None

    def _load_cache(self) -> Dict[str, Optional[Dict]]:
        try:
            with open(self.cache_path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _save_cache(self) -> None:
        try:
            with open(self.cache_path, "w", encoding="utf-8") as fh:
                json.dump(self.cache, fh, ensure_ascii=False, indent=2, sort_keys=True)
        except OSError as exc:  # noqa: BLE001
            print(f"  ! could not write OMDb cache: {exc}")

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _clean(value) -> str:
        """OMDb uses the string "N/A" for missing values; treat it as empty."""
        text = str(value or "").strip()
        return "" if text in ("", "N/A") else text

    @staticmethod
    def _runtime(value) -> int:
        """Parse OMDb's "149 min" runtime into an int, or 0."""
        match = re.search(r"(\d+)", str(value or ""))
        return int(match.group(1)) if match else 0
