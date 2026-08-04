"""Entry point for the SG Film Calendar sync script.

Scrapes screenings from multiple sources (Filmhouse.sg, Singapore Film Society)
and syncs them to a shared Google Calendar.
"""

import os
import sys

from calendar_sync import CalendarSync
from capitol_scraper import CapitolClassicsScraper
from eventive_scraper import EventiveScraper
from history import HistoryStore
from scraper import FilmhouseScraper
from sfs_scraper import SFSScraper
from site_export import export_screenings

HISTORY_CSV = os.environ.get("HISTORY_CSV", "data/films.csv")
SCREENINGS_JSON = os.environ.get("SCREENINGS_JSON", "docs/screenings.json")


def _require_env(name: str) -> str:
    """Get a required environment variable or exit."""
    val = os.environ.get(name)
    if not val:
        print(f"Error: {name} environment variable not set", file=sys.stderr)
        sys.exit(1)
    return val


def _env_flag(name: str) -> bool:
    """Return True when an environment flag is enabled."""
    return os.environ.get(name, "").lower() in {"1", "true", "yes", "on"}


def _scrape_filmhouse() -> list:
    """Scrape Filmhouse.sg and return films list."""
    print("Scraping Filmhouse.sg...")
    scraper = FilmhouseScraper()
    films = scraper.scrape()
    total = sum(len(f["screenings"]) for f in films)
    print(f"  → {len(films)} films with {total} screenings")
    return films


def _scrape_sfs() -> list:
    """Scrape Singapore Film Society and return films list.

    SFS has two live sources, both kept here: the original Peatix / Google Sheet
    method (still listing some screenings) and the newer Eventive schedule they
    migrated to (where new screenings are published). They tend not to overlap in
    time, and both feed the same calendar/history pipeline.
    """
    films: list = []

    print("Scraping Singapore Film Society (Peatix/Sheet)...")
    peatix = SFSScraper().scrape()
    total = sum(len(f["screenings"]) for f in peatix)
    print(f"  → {len(peatix)} events with {total} screenings")
    films.extend(peatix)

    print("Scraping Singapore Film Society (Eventive)...")
    eventive = EventiveScraper().scrape()
    total = sum(len(f["screenings"]) for f in eventive)
    print(f"  → {len(eventive)} films with {total} screenings")
    films.extend(eventive)

    return films


def _scrape_capitol() -> list:
    """Scrape Classics at Capitol (Capitol Theatre) and return films list."""
    print("Scraping Classics at Capitol (Capitol Theatre)...")
    films = CapitolClassicsScraper().scrape()
    total = sum(len(f["screenings"]) for f in films)
    print(f"  → {len(films)} films with {total} screenings")
    return films


def _update_history(all_films: list) -> None:
    """Merge scraped films from every source into the historic archive CSV."""
    print(f"\nUpdating film history ({HISTORY_CSV})...")
    store = HistoryStore(HISTORY_CSV)
    stats = store.update(all_films)
    print(f"  → {stats['total']} film-runs tracked ({stats['new']} new)")


def _export_site(all_films: list) -> None:
    """Write the flat screening feed that powers the static weekly view."""
    print(f"\nExporting screening feed ({SCREENINGS_JSON})...")
    count = export_screenings(all_films, SCREENINGS_JSON)
    print(f"  → {count} screenings written")


def main() -> None:
    """Run the full scrape-and-sync pipeline for all sources."""
    # Line-buffer stdout so progress messages interleave in real execution order
    # with the scraper library's stderr logs (instead of being block-buffered
    # and dumped all at once when output is piped, e.g. in GitHub Actions).
    sys.stdout.reconfigure(line_buffering=True)

    calendar_id = _require_env("GOOGLE_CALENDAR_ID")
    credentials_json = _require_env("GOOGLE_CALENDAR_CREDENTIALS")

    # Scrape all sources
    all_films = []
    all_films.extend(_scrape_filmhouse())
    all_films.extend(_scrape_sfs())

    # Capitol is a separate REST/HTML source; isolate it so an outage or markup
    # change there can't abort the established Filmhouse/SFS scrape.
    try:
        all_films.extend(_scrape_capitol())
    except Exception as exc:  # noqa: BLE001
        print(f"Capitol scrape failed: {exc}", file=sys.stderr)

    total_screenings = sum(len(f["screenings"]) for f in all_films)
    print(f"\nTotal: {len(all_films)} items with {total_screenings} screenings")

    if not all_films:
        print("No screenings found. Exiting.")
        return

    # Update the historic archive independently of the calendar sync, so an
    # archive failure doesn't block calendar updates (and vice versa). Both
    # Filmhouse and SFS films feed the same archive at the same grain.
    try:
        _update_history(all_films)
    except Exception as exc:  # noqa: BLE001
        print(f"History update failed: {exc}", file=sys.stderr)

    # Export the static-site feed independently too, so a write failure here
    # never blocks the calendar sync.
    try:
        _export_site(all_films)
    except Exception as exc:  # noqa: BLE001
        print(f"Screening feed export failed: {exc}", file=sys.stderr)

    print("\nSyncing to Google Calendar...")
    sync = CalendarSync(calendar_id, credentials_json)
    calendar_info = sync.target_calendar_info()
    print(
        "Target calendar: "
        f"timezone={calendar_info['time_zone']!r}, "
        f"id fingerprint={calendar_info['id_fingerprint']}"
    )
    stats = sync.sync_screenings(all_films)

    if _env_flag("CLEANUP_STALE_SFS"):
        print("\nCleaning up stale SFS calendar entries...")
        cleanup_stats = sync.cleanup_stale_sfs_events(all_films)
        print(
            f"Cleanup: Scanned: {cleanup_stats['scanned']}, "
            f"Deleted: {cleanup_stats['deleted']}, "
            f"Errors: {cleanup_stats['errors']}"
        )

    verification = sync.verify_sfs_events(all_films)
    print(
        "SFS verification: "
        f"Found: {verification['found']}/{verification['expected']}, "
        f"Missing: {len(verification['missing'])}, "
        f"Legacy aggregates remaining: {len(verification['legacy_aggregates'])}"
    )
    for missing in verification["missing"]:
        print(f"  MISSING: {missing}")
    for summary in verification["legacy_aggregates"]:
        print(f"  LEGACY AGGREGATE: {summary}")
    print("Latest SFS events returned by Google Calendar:")
    for event in verification["latest_events"]:
        print(
            f"  {event['summary']} | {event['start']} | {event['status']} | "
            f"{event['html_link']}"
        )

    print(
        f"Done! Created: {stats['created']}, "
        f"Updated: {stats['updated']}, "
        f"Errors: {stats['errors']}"
    )


if __name__ == "__main__":
    main()
