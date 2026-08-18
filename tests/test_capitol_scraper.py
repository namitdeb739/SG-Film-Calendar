"""Tests for the Classics at Capitol scraper.

Uses fixture API/detail payloads (no network) so the tests are deterministic.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import capitol_scraper
from capitol_scraper import CapitolClassicsScraper
from venues import normalize_venue

BEFORE = datetime(2026, 8, 1)

DESCRIPTION = (
    "2001: A Space Odyssey & poster exhibition\n\n"
    "1968. Dir: Stanley Kubrick,149 mins. (with 10 min. intermission),Colour,4K DCP (PG)\n\n"
    "Starring Keir Dullea, Gary Lockwood, Douglas Rain\n\n"
    "One of the great masterpieces of cinema."
)

# The detail page embeds session data as escaped RSC flight JSON (\" for quotes).
DETAIL_HTML = (
    r'<script>self.__next_f.push([1,"...\"duration\":160,'
    r"\"externalLink\":\"https://www.sistic.com.sg/events/space0826\","
    r"\"startDate\":\"2026-08-02 08:00:00\",\"endDate\":\"2026-08-23 08:00:00\"..."
    r'"])</script>'
)


def _item(
    code="CTSO2026",
    name="CLASSICS AT CAPITOL - 2001: A SPACE ODYSSEY",
    slug="classics-at-capitol-2001-a-space-odyssey",
    description=DESCRIPTION,
    stop_sales=False,
):
    return {
        "code": code,
        "name": name,
        "stopSales": stop_sales,
        "content": {
            "slug": {"name": slug},
            "description": description,
            "cardImageUrl": "cdn-sea.bookmyshow.com/prod/x/500x500/poster.jpg",
        },
    }


class _FakeResponse:
    def __init__(self, body_bytes):
        self.body = body_bytes
        self.status = 200


def _patch_fetch(monkeypatch, items, detail=DETAIL_HTML):
    def fake_get(url):
        if "collections/movies" in url:
            return _FakeResponse(json.dumps({"data": items}).encode())
        return _FakeResponse(detail.encode())

    monkeypatch.setattr(
        capitol_scraper,
        "Fetcher",
        type("F", (), {"get": staticmethod(fake_get)}),
    )


def test_film_parsed_with_metadata_and_sessions(monkeypatch):
    _patch_fetch(monkeypatch, [_item()])
    [film] = CapitolClassicsScraper(reference_date=BEFORE).scrape()

    assert film["title"] == "2001: A Space Odyssey"  # prefix stripped, de-shouted
    assert film["year"] == "1968"  # production year, not the "2001" in the title
    assert film["director"] == "Stanley Kubrick"
    assert film["duration_mins"] == 149
    assert film["rating"] == "PG"
    assert film["cast"].startswith("Keir Dullea")
    assert film["tags"] == ["4K"]
    assert film["themes"] == ["Classics at Capitol"]
    assert film["source"] == "capitol"
    assert film["poster_url"].startswith("https://cdn-sea.bookmyshow.com/")
    assert "masterpieces of cinema" in film["synopsis"]
    assert "Starring" not in film["synopsis"]


def test_sessions_converted_utc_to_sgt(monkeypatch):
    _patch_fetch(monkeypatch, [_item()])
    [film] = CapitolClassicsScraper(reference_date=BEFORE).scrape()
    starts = [s["start"] for s in film["screenings"]]
    # 08:00 UTC == 16:00 SGT (4pm)
    assert starts == [datetime(2026, 8, 2, 16, 0), datetime(2026, 8, 23, 16, 0)]
    assert film["screenings"][0]["time_str"] == "4:00 PM"
    assert film["screenings"][0]["booking_url"].endswith("/events/space0826")
    # 4K badges per screening (that's where the web view reads visible tags).
    assert film["screenings"][0]["tags"] == ["4K"]


def test_venue_normalises_to_capitol_theatre(monkeypatch):
    _patch_fetch(monkeypatch, [_item()])
    [film] = CapitolClassicsScraper(reference_date=BEFORE).scrape()
    assert film["venue"] == "Capitol Theatre"
    assert normalize_venue(film["venue"]) == "Capitol Theatre"


def test_past_sessions_skipped(monkeypatch):
    _patch_fetch(monkeypatch, [_item()])
    # Reference date after the first session drops it, keeps the later one.
    films = CapitolClassicsScraper(reference_date=datetime(2026, 8, 10)).scrape()
    assert [s["start"] for s in films[0]["screenings"]] == [
        datetime(2026, 8, 23, 16, 0)
    ]


def test_film_with_no_future_sessions_dropped(monkeypatch):
    _patch_fetch(monkeypatch, [_item()])
    assert CapitolClassicsScraper(reference_date=datetime(2027, 1, 1)).scrape() == []


def test_sold_out_from_stop_sales(monkeypatch):
    _patch_fetch(monkeypatch, [_item(stop_sales=True)])
    [film] = CapitolClassicsScraper(reference_date=BEFORE).scrape()
    assert all(s["sold_out"] for s in film["screenings"])
