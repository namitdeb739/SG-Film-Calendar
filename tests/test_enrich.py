"""Tests for OMDb metadata enrichment.

Uses fixture OMDb responses (no network) and a temp cache so tests are
deterministic and never touch the committed cache.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import enrich
from enrich import OMDbEnricher

FOUND = {
    "Response": "True",
    "Title": "2001: A Space Odyssey",
    "Year": "1968",
    "Rated": "G",
    "Runtime": "149 min",
    "Genre": "Adventure, Sci-Fi",
    "Director": "Stanley Kubrick",
    "Actors": "Keir Dullea, Gary Lockwood",
    "Language": "English, Russian",
    "Country": "United Kingdom, United States",
    "Plot": "A mysterious monolith.",
    "Poster": "https://omdb.example/2001.jpg",
}
NOT_FOUND = {"Response": "False", "Error": "Movie not found!"}


class _FakeResponse:
    def __init__(self, body):
        self.body = body


def _patch(monkeypatch, by_title=None, found=True):
    """Patch Fetcher; record request URLs, return FOUND/NOT_FOUND."""
    calls = []

    def fake_get(url):
        calls.append(url)
        payload = FOUND if found else NOT_FOUND
        if by_title is not None:
            payload = by_title(url)
        return _FakeResponse(json.dumps(payload).encode())

    monkeypatch.setattr(
        enrich, "Fetcher", type("F", (), {"get": staticmethod(fake_get)})
    )
    return calls


def _thin_film(**over):
    film = {
        "title": "2001: A Space Odyssey",
        "year": "",
        "director": "",
        "genre": "",
        "cast": "",
        "country": "",
        "language": "",
        "synopsis": "",
        "poster_url": "",
        "duration_mins": 120,
    }
    film.update(over)
    return film


def _enricher(tmp_path, key="KEY"):
    return OMDbEnricher(api_key=key, cache_path=str(tmp_path / "cache.json"))


def test_thin_film_backfilled(monkeypatch, tmp_path):
    _patch(monkeypatch)
    film = _thin_film()
    assert _enricher(tmp_path).enrich([film]) == 1
    assert film["director"] == "Stanley Kubrick"
    assert film["genre"] == "Adventure, Sci-Fi"
    assert film["cast"] == "Keir Dullea, Gary Lockwood"
    assert film["country"].startswith("United Kingdom")
    assert film["year"] == "1968"
    assert film["duration_mins"] == 149  # parsed from "149 min"
    assert film["poster_url"] == "https://omdb.example/2001.jpg"


def test_existing_fields_never_overwritten(monkeypatch, tmp_path):
    _patch(monkeypatch)
    film = _thin_film(
        genre="", director="", cast="Real Cast", poster_url="https://real/poster.jpg"
    )
    _enricher(tmp_path).enrich([film])
    # Empty fields filled; populated ones left alone.
    assert film["director"] == "Stanley Kubrick"
    assert film["cast"] == "Real Cast"
    assert film["poster_url"] == "https://real/poster.jpg"


def test_rich_film_is_not_looked_up(monkeypatch, tmp_path):
    calls = _patch(monkeypatch)
    rich = _thin_film(director="Olivia Wilde", genre="Drama")
    assert _enricher(tmp_path).enrich([rich]) == 0
    assert calls == []  # has director+genre, so no OMDb call


def test_no_api_key_is_a_noop(monkeypatch, tmp_path):
    calls = _patch(monkeypatch)
    film = _thin_film()
    assert (
        OMDbEnricher(api_key="", cache_path=str(tmp_path / "c.json")).enrich([film])
        == 0
    )
    assert calls == []
    assert film["director"] == ""


def test_not_found_leaves_film_unchanged(monkeypatch, tmp_path):
    _patch(monkeypatch, found=False)
    film = _thin_film()
    assert _enricher(tmp_path).enrich([film]) == 0
    assert film["director"] == ""


def test_na_values_treated_as_empty(monkeypatch, tmp_path):
    payload = dict(FOUND, Director="N/A", Poster="N/A")
    _patch(monkeypatch, by_title=lambda url: payload)
    film = _thin_film()
    _enricher(tmp_path).enrich([film])
    assert film["director"] == ""  # "N/A" not applied
    assert film["genre"] == "Adventure, Sci-Fi"


def test_year_fallback_to_title_only(monkeypatch, tmp_path):
    # A wrong (screening) year misses; retry without year matches.
    def by_title(url):
        return NOT_FOUND if "&y=" in url else FOUND

    calls = _patch(monkeypatch, by_title=by_title)
    film = _thin_film(year="2026")  # screening year, not production year
    assert _enricher(tmp_path).enrich([film]) == 1
    assert film["director"] == "Stanley Kubrick"
    assert any("&y=" in c for c in calls) and any("&y=" not in c for c in calls)


def test_ampersand_title_queries_spelled_out_form_first(monkeypatch, tmp_path):
    # OMDb answers an "&" query with a companion title rather than missing, so
    # the "and" form has to be tried first or the wrong film wins.
    companion = dict(FOUND, Title="Untold with the Filmmakers", Director="Ezra Edmond")

    def by_title(url):
        return FOUND if "%20and%20" in url else companion

    calls = _patch(monkeypatch, by_title=by_title)
    film = _thin_film(title="Raya & the Last Dragon")
    assert _enricher(tmp_path).enrich([film]) == 1
    assert film["director"] == "Stanley Kubrick"
    assert "%20and%20" in calls[0]


def test_ampersand_falls_back_to_literal_title(monkeypatch, tmp_path):
    # Some titles really do carry an "&"; the literal form is still tried.
    def by_title(url):
        return NOT_FOUND if "%20and%20" in url else FOUND

    calls = _patch(monkeypatch, by_title=by_title)
    film = _thin_film(title="Fire & Ice")
    assert _enricher(tmp_path).enrich([film]) == 1
    assert any("%26" in c for c in calls)


def test_lookup_is_cached(monkeypatch, tmp_path):
    calls = _patch(monkeypatch)
    enricher = _enricher(tmp_path)
    enricher.enrich([_thin_film()])
    n_after_first = len(calls)
    # A second enricher reading the persisted cache makes no new calls.
    _enricher(tmp_path).enrich([_thin_film()])
    assert len(calls) == n_after_first
    assert (tmp_path / "cache.json").exists()
