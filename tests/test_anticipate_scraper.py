"""Tests for the Anticipate Pictures distributor attribution.

Uses a fixture home page (no network) so the tests are deterministic.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import anticipate_scraper
from anticipate_scraper import AnticipatePicturesScraper

# Anticipate's slate: a Filmhouse-booked film, an Oldham-booked film (event-page
# link shared with the AFA scrape), plus a non-film call-to-action anchor.
HOME_HTML = """
<h2><a href="https://filmhouse.sg/film/152/silent-friend/">SILENT FRIEND</a></h2>
<h2><a href="https://asianfilmarchive.org/event-calendar/perfect-days/">PERFECT DAYS</a></h2>
<p><a href="https://filmhouse.sg/film/152/silent-friend/">FIND OUT MORE</a></p>
<p><a href="https://twitter.com/anticipate">Follow us</a></p>
"""


class _FakeResponse:
    def __init__(self, body_bytes):
        self.body = body_bytes
        self.status = 200


def _patch_fetch(monkeypatch, html=HOME_HTML):
    calls = []

    def fake_get(url):
        calls.append(url)
        return _FakeResponse(html.encode())

    monkeypatch.setattr(
        anticipate_scraper,
        "Fetcher",
        type("F", (), {"get": staticmethod(fake_get)}),
    )
    return calls


def _film(title, url="", source="filmhouse", booking=None):
    return {
        "title": title,
        "url": url,
        "source": source,
        "screenings": [{"booking_url": booking}] if booking else [{}],
    }


def test_matches_filmhouse_film_by_booking_id(monkeypatch):
    _patch_fetch(monkeypatch)
    films = [_film("Silent Friend", url="http://filmhouse.sg/film/152/silent-friend")]
    tagged = AnticipatePicturesScraper().annotate(films)
    assert tagged == 1
    assert films[0]["distributor"] == "Anticipate Pictures"


def test_matches_other_venue_by_shared_event_url(monkeypatch):
    # Anticipate at Oldham: the AFA scrape's booking_url is the same event page.
    _patch_fetch(monkeypatch)
    films = [
        _film(
            "Perfect Days",
            url="https://asianfilmarchive.org/event-calendar/perfect-days/",
            source="afa",
        )
    ]
    assert AnticipatePicturesScraper().annotate(films) == 1
    assert films[0]["distributor"] == "Anticipate Pictures"


def test_matches_across_venues_by_normalised_title(monkeypatch):
    # Same film screening at SFS (no shared URL) still matches on title, even
    # with a "4K" qualifier and different casing.
    _patch_fetch(monkeypatch)
    films = [_film("Silent Friend 4K", url="", source="sfs", booking="https://x")]
    assert AnticipatePicturesScraper().annotate(films) == 1


def test_unrelated_film_not_tagged(monkeypatch):
    _patch_fetch(monkeypatch)
    films = [_film("Some Other Movie", url="http://filmhouse.sg/film/999/other")]
    assert AnticipatePicturesScraper().annotate(films) == 0
    assert "distributor" not in films[0]


def test_only_booking_host_anchors_are_considered(monkeypatch):
    _patch_fetch(monkeypatch)
    keys, titles = AnticipatePicturesScraper()._distributed_slate()
    assert "filmhouse:152" in keys
    assert "asianfilmarchive.org/event-calendar/perfect-days" in keys
    assert "silent friend" in titles
    assert "follow us" not in titles  # twitter anchor excluded
