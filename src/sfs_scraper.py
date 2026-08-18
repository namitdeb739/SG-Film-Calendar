"""Scraper for Singapore Film Society events from their public Google Sheet."""

import csv
import html
import io
import re
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from scrapling.fetchers import Fetcher


class SFSScraper:
    """Scrape film/event data from Singapore Film Society's public Google Sheet.

    The SFS website embeds a custom widget that reads from a published Google Sheet
    CSV export. This scraper reads the same CSV directly.
    """

    CSV_URL = (
        "https://docs.google.com/spreadsheets/d/e/"
        "2PACX-1vTvhqremXNqIVCB3dv-pVLe5Tn3c1mrdY-83fqXt1c_WUEkQ9w0AazTAA457205oHM4p_R0X7uYjxdl"
        "/pub?gid=0&single=true&output=csv"
    )
    DEFAULT_LOCATION = "Singapore Film Society"

    def __init__(self, reference_date: Optional[datetime] = None) -> None:
        self.reference_date = reference_date or datetime.now()

    def scrape(self) -> List[Dict]:
        """Fetch and parse all SFS events from the Google Sheet CSV."""
        text = self._fetch_csv()
        reader = csv.DictReader(io.StringIO(text))

        films: List[Dict] = []
        for row in reader:
            for film in self._parse_row(row):
                if film and film["screenings"]:
                    films.append(film)

        return films

    def _fetch_csv(self) -> str:
        """Download the published CSV."""
        req = Request(self.CSV_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req) as resp:
            return resp.read().decode("utf-8-sig")

    def _parse_row(self, row: Dict) -> List[Dict]:
        """Parse one CSV row, expanding SFS Somerset bundles into films."""
        film = self._parse_event(row)
        if not film:
            return []

        if self._is_somerset_bundle(film["title"], film.get("category", "")):
            # Never publish the spreadsheet's date-range placeholder as a movie.
            # If ticket details are temporarily unavailable, retry on the next run.
            return self._expand_somerset_bundle(row, film)

        return [film]

    def _parse_event(self, row: Dict) -> Optional[Dict]:
        """Parse a single row from the CSV into a film-like dict."""
        title = (row.get("Title") or "").strip()
        if not title:
            return None

        start_date = self._parse_date(row.get("Start Date", ""))
        end_date = self._parse_date(row.get("End Date", ""))
        if not start_date:
            return None

        venue = (row.get("Venue") or "").strip()
        category = (row.get("Category") or "").strip()
        event_type = (row.get("Type") or "").strip()
        time_str = (row.get("Time") or "").strip()
        public_url = (row.get("Public URL") or "").strip()
        member_url = (row.get("Member URL") or "").strip()
        promo_code = (row.get("Code") or "").strip()
        poster_url = (row.get("Image") or "").strip()

        day_str = (row.get("Day") or "").strip()
        screenings = self._parse_screenings(
            start_date, end_date, time_str, public_url, day_str
        )

        if not screenings:
            return None

        return {
            "title": title,
            "url": public_url,
            "year": str(start_date.year),
            "duration_mins": 120,  # default; SFS sheet doesn't include duration
            "rating": "",
            "genre": "",  # sheet has no genre column; category is a theme, not a genre
            "director": "",
            "cast": "",
            "language": "",  # not stated in the SFS sheet
            "country": "",  # not stated in the SFS sheet
            "synopsis": "",  # not stated in the SFS sheet
            "poster_url": poster_url,
            # SFS programmes (SFS Spotlight, SFS Preview, SFS Somerset, ...) are
            # the curatorial equivalent of a Filmhouse season, so category feeds
            # the theme used to key film-runs in the historic archive.
            "themes": [category] if category else [],
            "tags": [],  # the sheet has no 4K / Q&A / premiere flags
            "venue": venue or self.DEFAULT_LOCATION,
            "category": category,
            "event_type": event_type,
            "promo_code": promo_code,
            "member_url": member_url,
            "screenings": screenings,
            "source": "sfs",
        }

    @staticmethod
    def _parse_date(date_str: str) -> Optional[date]:
        """Parse various date formats used in the SFS sheet."""
        date_str = date_str.strip()
        if not date_str or date_str.upper() == "NA" or date_str.upper() == "N/A":
            return None

        # Try formats like "24 Jun 2026", "24 June 2026", "12-June-2026"
        for fmt in ("%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%B-%Y"):
            try:
                return datetime.strptime(date_str, fmt).date()
            except ValueError:
                continue

        # Try m/d/yyyy (e.g. "5/11/2025")
        parts = date_str.split("/")
        if len(parts) == 3:
            try:
                return date(int(parts[2]), int(parts[0]), int(parts[1]))
            except (ValueError, IndexError):
                pass

        return None

    @staticmethod
    def _parse_time(time_str: str) -> Optional[datetime.time]:
        """Parse time strings like '7.00pm', '7:00:00 pm', '1.30pm'."""
        time_str = time_str.strip()
        if not time_str or time_str.upper() == "NA" or time_str.upper() == "N/A":
            return None

        # Normalise "." to ":" and ensure a space before am/pm.
        normalised = time_str.replace(".", ":")
        normalised = re.sub(r"\s*(am|pm)$", r" \1", normalised, flags=re.IGNORECASE)

        # Try HH:MM:SS am/pm, HH:MM am/pm, HH am/pm
        for fmt in ("%I:%M:%S %p", "%I:%M %p", "%I %p"):
            try:
                return datetime.strptime(normalised, fmt).time()
            except ValueError:
                continue

        return None

    @staticmethod
    def _is_somerset_bundle(title: str, category: str) -> bool:
        """Return True for aggregate rows like '1 Jul – 5 Jul Films | SFS Somerset'."""
        return (
            "sfs somerset" in category.lower()
            and "films" in title.lower()
            and "|" in title
        )

    def _expand_somerset_bundle(self, row: Dict, bundle: Dict) -> List[Dict]:
        """Expand one SFS Somerset Peatix bundle into individual film entries."""
        public_url = (row.get("Public URL") or "").strip()
        if not public_url:
            return []

        try:
            html_text = self._fetch_event_page(public_url)
        except (OSError, URLError):
            return []

        start_date = self._parse_date(row.get("Start Date", ""))
        end_date = self._parse_date(row.get("End Date", "")) or start_date
        if not start_date:
            return []

        offers = self._parse_peatix_offer_screenings(html_text, start_date, end_date)
        if not offers:
            event_id = self._extract_peatix_event_id(public_url, html_text)
            if event_id:
                ticket_url = f"https://peatix.com/sales/event/{event_id}/tickets"
                try:
                    ticket_html = self._fetch_event_page(ticket_url)
                except (OSError, URLError):
                    ticket_html = ""
                offers = self._parse_peatix_offer_screenings(
                    ticket_html, start_date, end_date
                )

        films_by_title: Dict[str, Dict] = {}
        for offer in offers:
            title = offer["title"]
            film = films_by_title.setdefault(
                title, {**bundle, "title": title, "screenings": []}
            )
            film["screenings"].append(
                {
                    "start": offer["start"],
                    "end": offer["end"],
                    "booking_url": public_url,
                    "time_str": offer["time_str"],
                }
            )

        return list(films_by_title.values())

    @staticmethod
    def _fetch_event_page(url: str) -> str:
        """Download a public Peatix page using Scrapling's browser-like fetcher."""
        try:
            response = Fetcher.get(url)
        except Exception as exc:
            raise OSError(f"Unable to fetch Peatix page: {url}") from exc
        return response.body.decode("utf-8", errors="ignore")

    @staticmethod
    def _extract_peatix_event_id(url: str, html_text: str) -> Optional[str]:
        """Extract Peatix's numeric event ID from a URL or localized page."""
        match = re.search(r"/(?:sales/)?event/(\d+)", f"{url}\n{html_text}")
        return match.group(1) if match else None

    def _parse_peatix_offer_screenings(
        self, html_text: str, start_date: date, end_date: Optional[date]
    ) -> List[Dict]:
        """Parse screening ticket names from Peatix event or sales pages."""
        meta_pattern = re.compile(
            r'<meta\s+itemprop=["\']name["\']\s+content=["\']([^"\']+)["\']\s*/?>'
        )
        sales_pattern = re.compile(
            r'<td\s+class=["\'][^"\']*\btype\b[^"\']*["\'][^>]*>'
            r"(.*?)<span\s+class=[\"']price-display",
            re.IGNORECASE | re.DOTALL,
        )
        raw_names = meta_pattern.findall(html_text)
        raw_names.extend(
            re.sub(r"<[^>]+>", "", match).strip()
            for match in sales_pattern.findall(html_text)
        )

        screenings: List[Dict] = []
        for raw_name in raw_names:
            name = html.unescape(raw_name).strip()
            parsed = self._parse_offer_name(name, start_date, end_date)
            if parsed:
                screenings.append(parsed)
        return screenings

    def _parse_offer_name(
        self, name: str, start_date: date, end_date: Optional[date]
    ) -> Optional[Dict]:
        """Parse one Peatix ticket/offer name into a screening dict."""
        match = re.search(
            r"(?:[A-Za-z]{3},?\s+)?(\d{1,2})\s+([A-Za-z]{3,9})[,.]\s*"
            r"(\d{1,2}(?:[.:]\d{2})?\s*(?:am|pm))\s*\((.+)\)\s*$",
            name,
            re.IGNORECASE,
        )
        if not match:
            return None

        day, month, time_part, title = match.groups()
        year = start_date.year
        for fmt in ("%d %b %Y", "%d %B %Y"):
            try:
                screening_date = datetime.strptime(
                    f"{day} {month} {year}", fmt
                ).date()
                break
            except ValueError:
                screening_date = None
        if not screening_date:
            return None

        # Handle rare bundles spanning New Year.
        if (
            end_date
            and screening_date < start_date
            and end_date.year > start_date.year
        ):
            screening_date = screening_date.replace(year=end_date.year)

        parsed_time = self._parse_time(time_part)
        if not parsed_time:
            return None

        start_dt = datetime.combine(screening_date, parsed_time)
        return {
            "title": title.strip(),
            "start": start_dt,
            "end": start_dt + timedelta(hours=2),
            "time_str": time_part,
        }

    @staticmethod
    def _parse_weekdays(day_str: str) -> List[int]:
        """Parse weekday labels like 'Tue' or 'Mon/Wed' into Python weekday ints."""
        if not day_str or day_str.upper() in {"NA", "N/A"}:
            return []

        weekday_by_name = {
            "mon": 0,
            "monday": 0,
            "tue": 1,
            "tues": 1,
            "tuesday": 1,
            "wed": 2,
            "wednesday": 2,
            "thu": 3,
            "thur": 3,
            "thurs": 3,
            "thursday": 3,
            "fri": 4,
            "friday": 4,
            "sat": 5,
            "saturday": 5,
            "sun": 6,
            "sunday": 6,
        }
        weekdays: List[int] = []
        for token in re.findall(r"[A-Za-z]+", day_str.lower()):
            weekday = weekday_by_name.get(token)
            if weekday is not None and weekday not in weekdays:
                weekdays.append(weekday)
        return weekdays

    @staticmethod
    def _dates_between(start_date: date, end_date: date) -> List[date]:
        """Return all dates in an inclusive date range."""
        days = (end_date - start_date).days
        return [start_date + timedelta(days=offset) for offset in range(days + 1)]

    def _parse_screenings(
        self,
        start_date: date,
        end_date: Optional[date],
        time_str: str,
        booking_url: str,
        day_str: str = "",
    ) -> List[Dict]:
        """Build screening entries from date / time info."""
        end_date = end_date or start_date
        parsed_time = self._parse_time(time_str)
        is_multi_day = start_date != end_date

        if parsed_time:
            weekdays = self._parse_weekdays(day_str)
            if is_multi_day and weekdays:
                screening_dates = [
                    screening_date
                    for screening_date in self._dates_between(start_date, end_date)
                    if screening_date.weekday() in weekdays
                ]
            else:
                screening_dates = [start_date]

            if not screening_dates:
                screening_dates = [start_date]

            return [
                {
                    "start": datetime.combine(screening_date, parsed_time),
                    "end": datetime.combine(screening_date, parsed_time)
                    + timedelta(hours=2),
                    "booking_url": booking_url,
                    "time_str": time_str,
                }
                for screening_date in screening_dates
            ]

        if is_multi_day:
            # Multi-day festival / season without a specific time
            # Use exclusive end (start of day after end_date) for clean calendar display
            start_dt = datetime.combine(start_date, datetime.min.time())
            end_dt = datetime.combine(
                end_date + timedelta(days=1), datetime.min.time()
            )
            return [
                {
                    "start": start_dt,
                    "end": end_dt,
                    "booking_url": booking_url,
                    "time_str": "Various",
                }
            ]

        # Single day, no time → all-day placeholder
        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = start_dt + timedelta(days=1)  # exclusive end
        return [
            {
                "start": start_dt,
                "end": end_dt,
                "booking_url": booking_url,
                "time_str": "",
            }
        ]
