"""
Shared helpers for the inbox pipeline (validate.py + route_inbox.py).

Keeping this logic in one place avoids the two scripts drifting apart on
what counts as a "data file" vs a "log file" sitting in inbox/, or on how
a date string maps onto the on-disk folder layout.
"""

import re

LOG_SUFFIX = ".log.json"

# Matches driver-XXXXXXX.json (the data file the driver actually uploads).
# Date is NOT in the filename -- it lives inside the JSON body.
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
    True if this filename looks like a driver data submission
    (driver-XXXXXXX.json) and is NOT a log file.
    """
    if is_log_file(filename):
        return False
    return bool(DATA_FILENAME_RE.match(filename))


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
