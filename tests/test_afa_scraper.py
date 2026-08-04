"""Tests for the Asian Film Archive (Oldham Theatre) scraper.

Uses fixture WP REST payloads (no network) so the tests are deterministic.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import afa_scraper
from afa_scraper import AsianFilmArchiveScraper
from venues import normalize_venue

# Well before the fixture screening dates so nothing is filtered as "past".
BEFORE = datetime(2026, 8, 1)


def _event(
    eid=1,
    title="André and His Olive Tree (2020)",
    excerpt="<p>A <em>portrait</em> of a chef.</p>",
    start="2026-08-16 14:00:00",
    end="2026-08-16 15:45:00",
    seat_left="30",
    linked="",
    feature_image="https://afa.example/olive.jpg",
    booking_url="",
    venue="Oldham Theatre",
):
    info = {
        "event_start_datetime": [start] if start else [],
        "event_end_datetime": [end] if end else [],
        "mep_total_seat_left": [seat_left],
        "event_feature_image": [feature_image],
        "booking_url": [booking_url],
        "mep_location_venue": [venue],
    }
    if linked:
        info["linked_events_or_items"] = [linked]
    return {
        "id": eid,
        "link": f"https://asianfilmarchive.org/event-calendar/event-{eid}/",
        "title": {"rendered": title},
        "excerpt": {"rendered": excerpt},
        "event_informations": info,
    }


class _FakeResponse:
    def __init__(self, body_bytes):
        self.body = body_bytes
        self.status = 200


def _patch_fetch(monkeypatch, events):
    calls = []

    def fake_get(url):
        calls.append(url)
        return _FakeResponse(json.dumps(events).encode())

    monkeypatch.setattr(
        afa_scraper, "Fetcher", type("F", (), {"get": staticmethod(fake_get)})
    )
    return calls


def test_rest_endpoint_queried(monkeypatch):
    calls = _patch_fetch(monkeypatch, [])
    AsianFilmArchiveScraper(reference_date=BEFORE).scrape()
    assert len(calls) == 1
    assert "/wp-json/wp/v2/mep_events" in calls[0]


def test_event_becomes_film_with_naive_local_screening(monkeypatch):
    _patch_fetch(monkeypatch, [_event()])
    [film] = AsianFilmArchiveScraper(reference_date=BEFORE).scrape()

    assert film["title"] == "André and His Olive Tree"  # year suffix stripped
    assert film["year"] == "2020"
    assert film["source"] == "afa"
    assert film["venue"] == "Oldham Theatre"
    assert film["duration_mins"] == 105  # from the 14:00–15:45 block, not a default
    assert film["themes"] == ["Asian Film Archive"]
    assert film["poster_url"] == "https://afa.example/olive.jpg"
    assert "portrait of a chef" in film["synopsis"].lower()

    [screening] = film["screenings"]
    assert screening["start"] == datetime(2026, 8, 16, 14, 0)
    assert screening["end"] == datetime(2026, 8, 16, 15, 45)
    assert screening["start"].tzinfo is None
    assert screening["time_str"] == "2:00 PM"
    assert screening["booking_url"].endswith("/event-1/")


def test_venue_normalises_to_oldham_theatre(monkeypatch):
    _patch_fetch(monkeypatch, [_event()])
    [film] = AsianFilmArchiveScraper(reference_date=BEFORE).scrape()
    assert normalize_venue(film["venue"]) == "Oldham Theatre"


def test_offsite_venue_is_honoured(monkeypatch):
    # AFA sometimes programmes away from Oldham; use the event's own venue.
    _patch_fetch(monkeypatch, [_event(venue="Gallery Theatre, National Museum")])
    [film] = AsianFilmArchiveScraper(reference_date=BEFORE).scrape()
    assert film["venue"] == "Gallery Theatre, National Museum"


def test_venue_falls_back_to_oldham_when_absent(monkeypatch):
    _patch_fetch(monkeypatch, [_event(venue="")])
    [film] = AsianFilmArchiveScraper(reference_date=BEFORE).scrape()
    assert film["venue"] == "Oldham Theatre"


def test_talks_are_skipped(monkeypatch):
    talk = _event(eid=2, title="[TALK] The Birth of a Nation on Screen")
    film = _event(eid=3)
    _patch_fetch(monkeypatch, [talk, film])

    films = AsianFilmArchiveScraper(reference_date=BEFORE).scrape()
    assert len(films) == 1
    assert films[0]["title"] == "André and His Olive Tree"


def test_programme_bundles_are_skipped(monkeypatch):
    # Umbrella "programme" posts link their child screenings and duplicate them.
    bundle = _event(eid=4, title="Off the Catalogue: August 2026", linked="3")
    film = _event(eid=5)
    _patch_fetch(monkeypatch, [bundle, film])

    films = AsianFilmArchiveScraper(reference_date=BEFORE).scrape()
    assert len(films) == 1
    assert films[0]["title"] == "André and His Olive Tree"


def test_past_screenings_skipped(monkeypatch):
    _patch_fetch(monkeypatch, [_event()])
    films = AsianFilmArchiveScraper(reference_date=datetime(2026, 9, 1)).scrape()
    assert films == []


def test_repeat_dates_group_and_qa_and_sold_out(monkeypatch):
    common = "Singapore Dreaming 美满人生 (2006) + cast & crew Q&A"
    e1 = _event(
        eid=6,
        title=common,
        start="2026-08-08 14:00:00",
        end="2026-08-08 15:45:00",
        seat_left="0",
    )
    e2 = _event(
        eid=7,
        title=common,
        start="2026-08-15 14:00:00",
        end="2026-08-15 15:45:00",
        seat_left="80",
    )
    _patch_fetch(monkeypatch, [e1, e2])

    films = AsianFilmArchiveScraper(reference_date=BEFORE).scrape()
    assert len(films) == 1
    film = films[0]
    # Year stripped and "+ cast & crew Q&A" dropped from the grouping title.
    assert film["title"] == "Singapore Dreaming 美满人生"
    assert film["year"] == "2006"
    assert [s["start"] for s in film["screenings"]] == [
        datetime(2026, 8, 8, 14, 0),
        datetime(2026, 8, 15, 14, 0),
    ]
    # Both screenings carry the Q&A tag; only the seatless one is sold out.
    assert all(s["tags"] == ["Q&A"] for s in film["screenings"])
    assert [s["sold_out"] for s in film["screenings"]] == [True, False]


def test_event_without_start_datetime_skipped(monkeypatch):
    _patch_fetch(monkeypatch, [_event(eid=8, start="", end="")])
    assert AsianFilmArchiveScraper(reference_date=BEFORE).scrape() == []
