"""
Shared helpers for the inbox pipeline (validate.py + route_inbox.py).

Keeping this logic in one place avoids the two scripts drifting apart on
what counts as a "data file" vs a "log file" sitting in inbox/, or on how
a date string maps onto the on-disk folder layout.
"""

import os
import re

LOG_SUFFIX = ".log.json"

# Matches driver-XXXXXXX.json specifically. Used only as a best-effort
# fallback for extracting a driver_id from the filename when a payload is
# too broken to parse (see driver_id_from_filename()) -- NOT used to decide
# whether a file in inbox/ counts as a data file. Driver ID and date both
# live in the JSON body, not the filename, and some mobile browsers (e.g.
# iOS Share-sheet "Save to Files" flows) substitute their own generated
# filename -- often a UUID -- regardless of the page's requested download
# name. Gating is_data_file() on this pattern would silently skip those
# uploads, so it isn't.
DATA_FILENAME_RE = re.compile(r"^driver-(.+)\.json$")

# Matches a "YYYY-MM-DD" date string as stored in the JSON body (payload
# date, not a folder name -- data folders are nested yyyy/mm/dd on disk,
# see split_date() below).
DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

# Used by aggregate.py to walk the nested yyyy/mm/dd data tree and tell
# genuine date-path segments apart from structural folders like inbox/,
# rollups/, overall/, scripts/.
YEAR_RE = re.compile(r"^\d{4}$")
MONTH_RE = re.compile(r"^(0[1-9]|1[0-2])$")
DAY_RE = re.compile(r"^(0[1-9]|[12]\d|3[01])$")


def split_date(date_str):
    """
    Split a 'YYYY-MM-DD' date string into ('YYYY', 'MM', 'DD') string parts,
    suitable for building a nested yyyy/mm/dd folder path.

    Returns None if date_str isn't in the expected format. Callers should
    treat that as "can't route this" rather than crash -- date format is
    already enforced upstream by validate.py, so a None here should be
    rare/defensive rather than expected.
    """
    m = DATE_RE.match(date_str)
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)


def is_year_folder(name):
    """True if this directory name looks like a 4-digit year segment."""
    return bool(YEAR_RE.match(name))


def is_month_folder(name):
    """True if this directory name looks like a 2-digit month segment (01-12)."""
    return bool(MONTH_RE.match(name))


def is_day_folder(name):
    """True if this directory name looks like a 2-digit day segment (01-31)."""
    return bool(DAY_RE.match(name))


def is_log_file(filename):
    """
    True if this filename is a log file (driver-XXXXXXX.log.json) and should
    be skipped by validate.py / route_inbox.py when scanning inbox/ for
    driver submissions.
    """
    return filename.endswith(LOG_SUFFIX)


def is_data_file(filename):
    """
    True if this filename should be treated as a driver data submission --
    any '*.json' file that isn't a log file (see is_log_file()).

    Deliberately NOT restricted to the driver-XXXXXXX.json naming pattern:
    driver_id and date are read from the JSON payload body wherever
    possible (route_inbox.py only falls back to the filename if the
    payload itself can't be parsed), and some mobile browsers rewrite the
    filename on download regardless of what the page requested -- most
    commonly to a UUID. Gating on the naming pattern would silently skip
    those uploads instead of processing them.
    """
    if is_log_file(filename):
        return False
    return filename.endswith(".json")


def driver_id_from_filename(filename):
    """
    Extract driver_id from a data filename, e.g. 'driver-2266642.json' -> '2266642'.
    Returns None if the filename doesn't match the expected pattern.
    NOTE: this is a fallback only -- driver_id from the JSON body should be
    preferred wherever available, since that's the source of truth.
    """
    m = DATA_FILENAME_RE.match(filename)
    return m.group(1) if m else None


def log_filename_for_driver(driver_id):
    """Build the per-driver, ever-accumulating log filename."""
    return "driver-{}{}".format(driver_id, LOG_SUFFIX)


def data_filename_for_driver(driver_id):
    """Build the standard data filename for a driver."""
    return "driver-{}.json".format(driver_id)


def walk_driver_day_tree(root):
    """
    Walk `root` for nested yyyy/mm/dd date folders and return a sorted list
    of (date, driver_id, full_path) tuples for every driver-XXXXXXX.json
    file found -- the same traversal shape needed both by aggregate.py
    (rooted at the repo root, over the organized data tree) and
    visualize.py/trends.py (rooted at rollups/). Centralized here so the
    two don't drift apart on what counts as a valid year/month/day segment
    or how a driver_id gets pulled out of a filename -- the exact drift
    this module already exists to prevent for filename/date recognition
    (see module docstring).

    Non-date folders directly under `root` (inbox/, rollups/, overall/,
    scripts/, etc.) are skipped automatically since they won't match
    is_year_folder(); log files and anything not matching
    'driver-*.json' are skipped the same way at the leaf level.
    """
    results = []
    if not os.path.isdir(root):
        return results

    for year in sorted(os.listdir(root)):
        year_dir = os.path.join(root, year)
        if not os.path.isdir(year_dir) or not is_year_folder(year):
            continue

        for month in sorted(os.listdir(year_dir)):
            month_dir = os.path.join(year_dir, month)
            if not os.path.isdir(month_dir) or not is_month_folder(month):
                continue

            for day in sorted(os.listdir(month_dir)):
                day_dir = os.path.join(month_dir, day)
                if not os.path.isdir(day_dir) or not is_day_folder(day):
                    continue

                date = "{}-{}-{}".format(year, month, day)
                for filename in sorted(os.listdir(day_dir)):
                    if not filename.startswith("driver-") or not filename.endswith(".json"):
                        continue
                    driver_id = filename[len("driver-"):-len(".json")]
                    results.append((date, driver_id, os.path.join(day_dir, filename)))

    return results


def normalize_route_id(route_id):
    """
    Normalize a route_id for grouping purposes (strip + uppercase), so a
    route entered inconsistently anywhere upstream (e.g. '17f' vs '17F')
    can't silently split trend history into two separate buckets.

    index.html already uppercases on save, so this is a no-op for data
    that came through the web tool -- it exists as a second, authoritative
    guarantee for anything that reads route_id for grouping (aggregate.py's
    rollups and trend-by-route), independent of how any given payload was
    produced. Returns None unchanged (a day with no plan has no route_id
    to normalize).
    """
    if not isinstance(route_id, str):
        return route_id
    normalized = route_id.strip().upper()
    return normalized or None
