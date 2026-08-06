"""Export scraped screenings to a flat JSON feed for the static web view.

The historic archive (``data/films.csv``) unions venues and dates across a
whole film-run, which loses the per-screening date/venue link the weekly view
needs. This module instead flattens the live scrape into one record per
screening — the ``(film, datetime, cinema, booking link)`` grain the page
renders — and writes it to ``docs/screenings.json`` for GitHub Pages to serve.
"""

import json
from datetime import datetime
from typing import Dict, List

from venues import normalize_venue

DEFAULT_VENUE = "Filmhouse Cinemas, Singapore"


def _venue(film: Dict) -> str:
    """Canonical venue for a film, collapsing the sources' many labels."""
    venue = (film.get("venue") or "").strip()
    if not venue:
        venue = (
            DEFAULT_VENUE if film.get("source") != "sfs" else "Singapore Film Society"
        )
    return normalize_venue(venue)


def _flatten(films: List[Dict]) -> List[Dict]:
    """Turn nested film → screenings into one flat record per screening."""
    rows: List[Dict] = []
    for film in films:
        venue = _venue(film)
        for scr in film.get("screenings") or []:
            start = scr.get("start")
            end = scr.get("end")
            # Format/event tags are a property of the individual screening
            # (e.g. one 4K show among plain ones); carry the visible labels
            # straight through for the web view to badge and filter on.
            tags = [t for t in (scr.get("tags") or []) if t]
            # Themes are the film's curatorial programme/season (a Filmhouse
            # season, an SFS strand, an AFA/NLB programme); carry them so the
            # web view can filter a whole programme as a unit.
            themes = [t for t in (film.get("themes") or []) if t]
            rows.append(
                {
                    "title": film.get("title", ""),
                    "year": str(film.get("year") or ""),
                    "director": film.get("director", ""),
                    "genre": film.get("genre", ""),
                    "rating": film.get("rating", ""),
                    "duration_mins": film.get("duration_mins", 0),
                    "language": film.get("language", ""),
                    "subtitles": film.get("subtitles", ""),
                    "poster_url": film.get("poster_url", ""),
                    "source": film.get("source", "filmhouse"),
                    "distributor": film.get("distributor", ""),
                    "venue": venue,
                    "tags": tags,
                    "themes": themes,
                    "start": start.isoformat() if isinstance(start, datetime) else "",
                    "end": end.isoformat() if isinstance(end, datetime) else "",
                    "time_str": scr.get("time_str", ""),
                    "sold_out": bool(scr.get("sold_out")),
                    "booking_url": scr.get("booking_url", ""),
                    "film_url": film.get("url", ""),
                }
            )
    rows.sort(key=lambda r: (r["start"], r["title"]))
    return rows


def export_screenings(films: List[Dict], path: str) -> int:
    """Write the flat screening feed to ``path``. Returns the record count."""
    rows = _flatten(films)
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "screenings": rows,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return len(rows)
