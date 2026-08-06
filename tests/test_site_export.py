"""Tests for the flat screening feed export."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from site_export import _flatten


def _film(themes=None, **over):
    film = {
        "title": "A Film",
        "source": "filmhouse",
        "venue": "Filmhouse Cinemas",
        "themes": themes if themes is not None else [],
        "screenings": [
            {"start": datetime(2026, 8, 10, 19, 0), "end": datetime(2026, 8, 10, 21, 0)}
        ],
    }
    film.update(over)
    return film


def test_themes_carried_through_to_feed():
    [row] = _flatten([_film(themes=["Music in Film", "Jazz in Film"])])
    assert row["themes"] == ["Music in Film", "Jazz in Film"]


def test_themes_default_to_empty_list():
    [row] = _flatten([_film(themes=None)])
    assert row["themes"] == []


def test_blank_themes_are_dropped():
    [row] = _flatten([_film(themes=["Real Season", "", None])])
    assert row["themes"] == ["Real Season"]
