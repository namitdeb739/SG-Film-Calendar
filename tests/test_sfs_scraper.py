from datetime import datetime

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import sfs_scraper
from sfs_scraper import SFSScraper


SOMERSET_HTML = """
<div style="display:none" itemprop="offers" itemscope itemtype="http://schema.org/Offer">
<meta itemprop="name" content="Wed 24 Jun, 7.30pm (BLUE VELVET)"/>
<meta itemprop="name" content="Thu 25 Jun, 7.30pm (SATURDAY NIGHT FEVER)"/>
<meta itemprop="name" content="Fri 26 Jun, 7.30pm (BLUE VELVET)"/>
<meta itemprop="name" content="Sat 27 Jun, 1.30pm (ITAM: A SUN BEAR STORY)"/>
<meta itemprop="name" content="Sat 27 Jun, 7.30pm (THE WAVES WILL CARRY US)"/>
</div>
"""

LIVE_INFERNO_HTML = """
<div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
<meta itemprop="name" content="Fri 17 July, 7.30pm (BLUE VELVET)"/>
<meta itemprop="name" content="Sat 18 July, 1.30pm (THE OLD MAN AND HIS CAR)"/>
<meta itemprop="name" content="Sat 18 July, 4.00pm (ITAM: A SUN BEAR STORY)"/>
<meta itemprop="name" content="Sat 18 July, 7.30pm (THE TOWERING INFERNO)"/>
<meta itemprop="name" content="Sun 19 July, 4.00pm (MIRACULOUS LEAF)"/>
<meta itemprop="name" content="Sun 19 July. 7.30pm (THE WAVES WILL CARRY US)"/>
</div>
"""

LOCALIZED_PEATIX_HTML = """
<link rel="canonical" href="https://peatix.com/us/event/5080329">
"""

PEATIX_TICKET_SALES_HTML = """
<table><tbody>
<tr><td class="type">Fri 17 July, 7.30pm (BLUE VELVET)<span class="price-display">$15</span></td></tr>
<tr><td class="type">Sun 19 July. 7.30pm (THE WAVES WILL CARRY US)<span class="price-display">$15</span></td></tr>
</tbody></table>
"""


def test_fetch_event_page_uses_scrapling_response_body(monkeypatch):
    class FakeResponse:
        body = SOMERSET_HTML.encode()

    requested_urls = []

    class FakeFetcher:
        @staticmethod
        def get(url):
            requested_urls.append(url)
            return FakeResponse()

    monkeypatch.setattr(sfs_scraper, "Fetcher", FakeFetcher, raising=False)

    result = SFSScraper._fetch_event_page("https://example.peatix.com/view")

    assert result == SOMERSET_HTML
    assert requested_urls == ["https://example.peatix.com/view"]


def test_somerset_bundle_falls_back_to_ticket_sales_page_after_localized_redirect():
    scraper = SFSScraper()
    requested_urls = []

    def fetch(url):
        requested_urls.append(url)
        if url == "https://peatix.com/sales/event/5080329/tickets":
            return PEATIX_TICKET_SALES_HTML
        return LOCALIZED_PEATIX_HTML

    scraper._fetch_event_page = fetch
    row = {
        "Title": "17 Jul – 19 Jul Films | SFS Somerset",
        "Category": "SFS Somerset",
        "Type": "Discount",
        "Start Date": "17 Jul 2026",
        "End Date": "19 Jul 2026",
        "Day": "NA",
        "Time": "NA",
        "Venue": "Golden Village Cineleisure, Hall 6",
        "Public URL": "https://sfs-somerset-inferno1.peatix.com/view",
        "Member URL": "https://sfs-somerset-inferno1.peatix.com/view",
        "Code": "SOMERSET",
    }

    films = scraper._parse_row(row)

    assert requested_urls == [
        "https://sfs-somerset-inferno1.peatix.com/view",
        "https://peatix.com/sales/event/5080329/tickets",
    ]
    assert {film["title"] for film in films} == {
        "BLUE VELVET",
        "THE WAVES WILL CARRY US",
    }
    assert films[0]["screenings"][0]["start"] == datetime(2026, 7, 17, 19, 30)


def test_somerset_bundle_expands_peatix_ticket_names_into_movie_screenings():
    scraper = SFSScraper()
    scraper._fetch_event_page = lambda url: SOMERSET_HTML

    row = {
        "Title": "24 Jun – 28 Jun Films | SFS Somerset",
        "Category": "SFS Somerset",
        "Type": "Discount",
        "Start Date": "24 Jun 2026",
        "End Date": "28 Jun 2026",
        "Day": "NA",
        "Time": "NA",
        "Venue": "Golden Village Cineleisure, Hall 6",
        "Public URL": "https://sfs-somerset-waves.peatix.com/",
        "Member URL": "https://sfs-somerset-waves.peatix.com/",
        "Code": "SOMERSET",
    }

    films = scraper._parse_row(row)
    by_title = {film["title"]: film for film in films}

    assert set(by_title) == {
        "BLUE VELVET",
        "SATURDAY NIGHT FEVER",
        "ITAM: A SUN BEAR STORY",
        "THE WAVES WILL CARRY US",
    }
    assert [s["start"] for s in by_title["BLUE VELVET"]["screenings"]] == [
        datetime(2026, 6, 24, 19, 30),
        datetime(2026, 6, 26, 19, 30),
    ]
    assert by_title["SATURDAY NIGHT FEVER"]["screenings"][0]["start"] == datetime(
        2026, 6, 25, 19, 30
    )


def test_somerset_bundle_accepts_peatix_period_between_date_and_time():
    scraper = SFSScraper()
    scraper._fetch_event_page = lambda url: LIVE_INFERNO_HTML
    row = {
        "Title": "17 Jul – 19 Jul Films | SFS Somerset",
        "Category": "SFS Somerset",
        "Type": "Discount",
        "Start Date": "17 Jul 2026",
        "End Date": "19 Jul 2026",
        "Day": "NA",
        "Time": "NA",
        "Venue": "Golden Village Cineleisure, Hall 6",
        "Public URL": "https://sfs-somerset-inferno1.peatix.com/view",
        "Member URL": "https://sfs-somerset-inferno1.peatix.com/view",
        "Code": "SOMERSET",
    }

    films = scraper._parse_row(row)

    assert {film["title"] for film in films} == {
        "BLUE VELVET",
        "THE OLD MAN AND HIS CAR",
        "ITAM: A SUN BEAR STORY",
        "THE TOWERING INFERNO",
        "MIRACULOUS LEAF",
        "THE WAVES WILL CARRY US",
    }
    waves = next(film for film in films if film["title"] == "THE WAVES WILL CARRY US")
    assert waves["screenings"][0]["start"] == datetime(2026, 7, 19, 19, 30)


def test_somerset_bundle_does_not_fall_back_to_aggregate_when_tickets_are_unavailable():
    scraper = SFSScraper()
    scraper._fetch_event_page = lambda url: "<html>No ticket offers yet</html>"
    row = {
        "Title": "17 Jul – 19 Jul Films | SFS Somerset",
        "Category": "SFS Somerset",
        "Type": "Discount",
        "Start Date": "17 Jul 2026",
        "End Date": "19 Jul 2026",
        "Day": "NA",
        "Time": "NA",
        "Venue": "Golden Village Cineleisure, Hall 6",
        "Public URL": "https://sfs-somerset-inferno1.peatix.com/view",
        "Member URL": "https://sfs-somerset-inferno1.peatix.com/view",
        "Code": "SOMERSET",
    }

    assert scraper._parse_row(row) == []


def test_multiday_solo_movie_with_weekday_expands_to_each_matching_date():
    scraper = SFSScraper()
    row = {
        "Title": "THE FELLOW WHO REJECTED COLLEGE",
        "Category": "SFS Special Presentation",
        "Type": "Discount",
        "Start Date": "1 June 2026",
        "End Date": "29 June 2026",
        "Day": "Mon",
        "Time": "7.00pm",
        "Venue": "Golden Village Paya Lebar",
        "Public URL": "https://www.gv.com.sg/GVMovieDetails#/movie/2387",
        "Member URL": "https://www.gv.com.sg/GVMovieDetails#/movie/2387",
        "Code": "FELLOW45",
    }

    film = scraper._parse_event(row)

    assert [screening["start"] for screening in film["screenings"]] == [
        datetime(2026, 6, 1, 19, 0),
        datetime(2026, 6, 8, 19, 0),
        datetime(2026, 6, 15, 19, 0),
        datetime(2026, 6, 22, 19, 0),
        datetime(2026, 6, 29, 19, 0),
    ]


def test_category_feeds_themes_not_genre():
    scraper = SFSScraper()
    row = {
        "Title": "SOME FILM",
        "Category": "SFS Showcase",
        "Start Date": "10 August 2026",
        "Time": "7.30pm",
        "Venue": "The Projector",
        "Public URL": "https://example.com",
    }

    film = scraper._parse_event(row)

    # The SFS sheet has no genre column; the category is a curatorial theme, so
    # it must feed themes/category, never masquerade as the film's genre.
    assert film["genre"] == ""
    assert film["themes"] == ["SFS Showcase"]
    assert film["category"] == "SFS Showcase"
