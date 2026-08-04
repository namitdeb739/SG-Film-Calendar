"""Tests for the NLB LibCal film-screening scraper.

Uses fixture search payloads (no network) so the tests are deterministic.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import libcal_scraper
from libcal_scraper import NLBLibCalScraper
from venues import normalize_venue

# Well before the fixture screening dates so nothing is filtered as "past".
BEFORE = datetime(2026, 8, 1)


def _result(
    eid=5913631,
    title="The Big Picture- Fortnightly Film Screening (13 August) | Central Arts Library",
    startdt="2026-08-13 18:30:00",
    enddt="2026-08-13 21:00:00",
    start="6:30 PM",
    location="CAL - National Library Building - Imagination Room (Level 5)",
    description="<p><b>Advisory: </b>PG</p><p>A <em>young</em> film.</p>",
    registration_enabled=True,
    seatsleft=48,
):
    return {
        "id": eid,
        "title": title,
        "startdt": startdt,
        "enddt": enddt,
        "start": start,
        "url": f"https://nlb.libcal.com/event/{eid}",
        "location": location,
        "description": description,
        "featured_image": f"https://cdn.example/{eid}.jpg",
        "registration_enabled": registration_enabled,
        "seatsleft": seatsleft,
    }


class _FakeResponse:
    def __init__(self, body_bytes):
        self.body = body_bytes
        self.status = 200


def _patch_fetch(monkeypatch, results):
    """Record fetched URLs; return the canned search JSON payload."""
    calls = []

    def fake_get(url):
        calls.append(url)
        return _FakeResponse(json.dumps({"status": 1, "results": results}).encode())

    monkeypatch.setattr(
        libcal_scraper, "Fetcher", type("F", (), {"get": staticmethod(fake_get)})
    )
    return calls


def test_search_endpoint_queried_with_site_and_keyword(monkeypatch):
    calls = _patch_fetch(monkeypatch, [])
    NLBLibCalScraper(reference_date=BEFORE).scrape()

    assert len(calls) == 1
    assert "process_search.php" in calls[0]
    assert f"site_id={NLBLibCalScraper.SITE_ID}" in calls[0]
    assert "film%20screening" in calls[0]


def test_result_becomes_film_with_naive_local_screening(monkeypatch):
    _patch_fetch(monkeypatch, [_result()])
    [film] = NLBLibCalScraper(reference_date=BEFORE).scrape()

    # " | Central Arts Library" suffix stripped from the title.
    assert film["title"] == "The Big Picture- Fortnightly Film Screening (13 August)"
    assert film["source"] == "nlb"
    assert film["themes"] == ["NLB Film Screenings"]
    assert film["poster_url"].endswith("5913631.jpg")

    [screening] = film["screenings"]
    assert screening["start"] == datetime(2026, 8, 13, 18, 30)
    assert screening["end"] == datetime(2026, 8, 13, 21, 0)
    assert screening["start"].tzinfo is None
    assert screening["time_str"] == "6:30 PM"
    assert screening["booking_url"].endswith("/event/5913631")


def test_duration_derived_from_start_and_end(monkeypatch):
    _patch_fetch(monkeypatch, [_result()])
    [film] = NLBLibCalScraper(reference_date=BEFORE).scrape()
    assert film["duration_mins"] == 150  # 18:30 -> 21:00


def test_advisory_rating_and_synopsis_extracted(monkeypatch):
    _patch_fetch(monkeypatch, [_result()])
    [film] = NLBLibCalScraper(reference_date=BEFORE).scrape()
    assert film["rating"] == "PG"
    assert "young film" in film["synopsis"].lower()
    assert "<" not in film["synopsis"]  # HTML stripped


def test_venue_normalises_to_national_library(monkeypatch):
    _patch_fetch(monkeypatch, [_result()])
    [film] = NLBLibCalScraper(reference_date=BEFORE).scrape()
    assert normalize_venue(film["venue"]) == "National Library"


def test_non_film_results_filtered_out(monkeypatch):
    workshop = _result(eid=1, title="Pi & Python Workshop | Tampines Library")
    film = _result(eid=2)
    _patch_fetch(monkeypatch, [workshop, film])

    films = NLBLibCalScraper(reference_date=BEFORE).scrape()
    assert len(films) == 1
    assert films[0]["url"].endswith("/event/2")


def test_past_screenings_skipped(monkeypatch):
    _patch_fetch(monkeypatch, [_result()])
    # Reference date after the screening -> dropped.
    films = NLBLibCalScraper(reference_date=datetime(2026, 9, 1)).scrape()
    assert films == []


def test_same_title_repeats_group_into_one_film(monkeypatch):
    common = "Raya and The Last Dragon Film Screening"
    e1 = _result(
        eid=10,
        title=f"{common} | Library A",
        startdt="2026-08-13 18:30:00",
        enddt="2026-08-13 20:30:00",
    )
    e2 = _result(
        eid=11,
        title=f"{common} | Library A",
        startdt="2026-08-20 18:30:00",
        enddt="2026-08-20 20:30:00",
    )
    _patch_fetch(monkeypatch, [e1, e2])

    films = NLBLibCalScraper(reference_date=BEFORE).scrape()
    assert len(films) == 1
    assert [s["start"] for s in films[0]["screenings"]] == [
        datetime(2026, 8, 13, 18, 30),
        datetime(2026, 8, 20, 18, 30),
    ]


def test_sold_out_when_registration_full(monkeypatch):
    full = _result(
        eid=20, title="Film A Screening", seatsleft=0, registration_enabled=True
    )
    open_seats = _result(
        eid=21, title="Film B Screening", seatsleft=5, registration_enabled=True
    )
    no_reg = _result(
        eid=22, title="Film C Screening", seatsleft=0, registration_enabled=False
    )
    _patch_fetch(monkeypatch, [full, open_seats, no_reg])

    by_url = {
        f["url"]: f["screenings"][0]["sold_out"]
        for f in NLBLibCalScraper(reference_date=BEFORE).scrape()
    }
    assert by_url["https://nlb.libcal.com/event/20"] is True
    assert by_url["https://nlb.libcal.com/event/21"] is False
    assert by_url["https://nlb.libcal.com/event/22"] is False
