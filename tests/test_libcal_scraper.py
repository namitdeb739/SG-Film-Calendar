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
from site_export import _flatten
from venues import normalize_venue

# Well before the fixture screening dates so nothing is filtered as "past".
BEFORE = datetime(2026, 8, 1)

# A realistic Big Picture description: labelled theme/synopsis/advisory followed
# by the registration/photography boilerplate the parser must discard.
BIG_PICTURE_DESC = (
    "<p><b>This Month's Theme: </b>Band Together<br/>"
    "<b>Film Synopsis: </b>Two musician brothers set out to save their old "
    "orphanage from closure.</p>"
    "<p><b>Advisory: </b>PG</p>"
    "<p>Watch a trailer of the movie <a href='https://youtu.be/x'>here</a></p>"
    "<p><b>About: </b>The Big Picture is a fortnightly series curated by "
    "librarians.</p><p>Registration is required for this programme.</p>"
)

# A festival screening names the real film explicitly and uses "Synopsis:".
FESTIVAL_DESC = (
    "<p>About the Programme: nocturnal screenings under the stars.</p>"
    "<p><b>Film Title: </b>Raya &amp; the Last Dragon</p>"
    "<p><b>Important Note: </b>Bring earphones.</p>"
    "<p><b>Synopsis: </b>In Kumandra, a lone warrior seeks the last dragon.</p>"
    "<p>Photography and Videography may be taken.</p>"
)

LANGUAGE_CATS = [
    {"cat_id": 45297, "name": "Areas of Interest > Art & Creativity"},
    {"cat_id": 45305, "name": "Language > English"},
]


def _result(
    eid=5913631,
    title="The Big Picture- Fortnightly Film Screening (13 August) | Central Arts Library",
    startdt="2026-08-13 18:30:00",
    enddt="2026-08-13 21:00:00",
    start="6:30 PM",
    location="CAL - National Library Building - Imagination Room (Level 5)",
    description=BIG_PICTURE_DESC,
    categories_arr=None,
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
        "categories_arr": LANGUAGE_CATS if categories_arr is None else categories_arr,
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

    # The recurring series names only itself (no real film title anywhere), so
    # its cleaned series title is kept; " | Central Arts Library" and the
    # per-instance "(13 August)" date are stripped.
    assert film["title"] == "The Big Picture- Fortnightly Film Screening"
    assert film["source"] == "nlb"
    # LibCal only has the event graphic, so the poster is left for enrichment
    # and the graphic is carried separately as a fallback.
    assert film["poster_url"] == ""
    assert film["fallback_image"].endswith("5913631.jpg")

    [screening] = film["screenings"]
    assert screening["start"] == datetime(2026, 8, 13, 18, 30)
    assert screening["end"] == datetime(2026, 8, 13, 21, 0)
    assert screening["start"].tzinfo is None
    assert screening["time_str"] == "6:30 PM"
    assert screening["booking_url"].endswith("/event/5913631")


def test_series_instances_group_under_one_dateless_title(monkeypatch):
    # Each instance of a recurring series is titled with its own date; stripping
    # it is what lets the series appear as one film with several screenings.
    _patch_fetch(
        monkeypatch,
        [
            _result(),
            _result(
                eid=5914353,
                title=(
                    "The Big Picture- Fortnightly Film Screening (27 August) "
                    "| Central Arts Library"
                ),
                startdt="2026-08-27 18:30:00",
                enddt="2026-08-27 21:00:00",
            ),
        ],
    )
    [film] = NLBLibCalScraper(reference_date=BEFORE).scrape()
    assert film["title"] == "The Big Picture- Fortnightly Film Screening"
    assert [s["start"] for s in film["screenings"]] == [
        datetime(2026, 8, 13, 18, 30),
        datetime(2026, 8, 27, 18, 30),
    ]


def test_export_prefers_enriched_poster_over_event_graphic(monkeypatch):
    # Enrichment fills poster_url; until then the export must still show the
    # event graphic rather than an empty poster.
    _patch_fetch(monkeypatch, [_result()])
    [film] = NLBLibCalScraper(reference_date=BEFORE).scrape()

    [row] = _flatten([film])
    assert row["poster_url"].endswith("5913631.jpg")

    film["poster_url"] = "https://img.omdb/real-poster.jpg"
    [row] = _flatten([film])
    assert row["poster_url"] == "https://img.omdb/real-poster.jpg"


def test_duration_defaults_to_neutral_not_room_slot(monkeypatch):
    # The room booking is 2.5h; that is the slot, not the film runtime, so we
    # must not surface it as the duration.
    _patch_fetch(monkeypatch, [_result()])
    [film] = NLBLibCalScraper(reference_date=BEFORE).scrape()
    assert film["duration_mins"] == 120


def test_synopsis_is_film_synopsis_only(monkeypatch):
    _patch_fetch(monkeypatch, [_result()])
    [film] = NLBLibCalScraper(reference_date=BEFORE).scrape()
    assert film["synopsis"] == (
        "Two musician brothers set out to save their old orphanage from closure."
    )
    # Theme, advisory and registration boilerplate must be excluded.
    assert "This Month" not in film["synopsis"]
    assert "Advisory" not in film["synopsis"]
    assert "Registration" not in film["synopsis"]


def test_synopsis_blank_when_no_label(monkeypatch):
    # Without a "Synopsis:" label, return "" rather than dumping boilerplate.
    no_label = _result(
        description="<p>Registration is required. Photography may be taken.</p>"
    )
    _patch_fetch(monkeypatch, [no_label])
    [film] = NLBLibCalScraper(reference_date=BEFORE).scrape()
    assert film["synopsis"] == ""


def test_advisory_rating_extracted(monkeypatch):
    _patch_fetch(monkeypatch, [_result()])
    [film] = NLBLibCalScraper(reference_date=BEFORE).scrape()
    assert film["rating"] == "PG"


def test_hyphenated_advisory_rating_not_truncated(monkeypatch):
    hyphenated = _result(
        description="<p><b>Film Synopsis: </b>A film.</p><p><b>Advisory: </b>PG-13</p>"
    )
    _patch_fetch(monkeypatch, [hyphenated])
    [film] = NLBLibCalScraper(reference_date=BEFORE).scrape()
    assert film["rating"] == "PG-13"


def test_monthly_theme_added_to_themes(monkeypatch):
    _patch_fetch(monkeypatch, [_result()])
    [film] = NLBLibCalScraper(reference_date=BEFORE).scrape()
    assert film["themes"] == ["Band Together", "NLB Film Screenings"]


def test_language_from_categories(monkeypatch):
    _patch_fetch(monkeypatch, [_result()])
    [film] = NLBLibCalScraper(reference_date=BEFORE).scrape()
    assert film["language"] == "English"


def test_festival_film_title_from_description(monkeypatch):
    festival = _result(
        eid=99,
        title='"Raya and The Last Dragon" Film Screening | All Things Singapore 2026',
        description=FESTIVAL_DESC,
    )
    _patch_fetch(monkeypatch, [festival])
    [film] = NLBLibCalScraper(reference_date=BEFORE).scrape()
    # The real film title, taken from the "Film Title:" label, not the wrapper.
    assert film["title"] == "Raya & the Last Dragon"
    assert film["synopsis"] == "In Kumandra, a lone warrior seeks the last dragon."


def test_quoted_event_title_used_when_no_film_title_label(monkeypatch):
    quoted = _result(
        eid=98,
        title='"We Can Save the World!!!" Film Screening | All Things Singapore 2026',
        description="<p><b>Synopsis: </b>A town council worker saves the world.</p>",
    )
    _patch_fetch(monkeypatch, [quoted])
    [film] = NLBLibCalScraper(reference_date=BEFORE).scrape()
    assert film["title"] == "We Can Save the World!!!"


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
