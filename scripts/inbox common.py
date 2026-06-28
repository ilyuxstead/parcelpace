"""
Shared helpers for the inbox pipeline (validate.py + route_inbox.py).

Keeping this logic in one place avoids the two scripts drifting apart on
what counts as a "data file" vs a "log file" sitting in inbox/.
"""

import re

LOG_SUFFIX = ".log.json"

# Matches driver-XXXXXXX.json (the data file the driver actually uploads).
# Date is NOT in the filename -- it lives inside the JSON body.
DATA_FILENAME_RE = re.compile(r"^driver-(.+)\.json$")

# Matches a date-first top-level folder, e.g. "2026-06-26".
# Used by aggregate.py to tell organized data folders apart from
# structural folders like inbox/, rollups/, overall/, scripts/.
DATE_FOLDER_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def is_date_folder(name):
    """True if this directory name looks like a YYYY-MM-DD data folder."""
    return bool(DATE_FOLDER_RE.match(name))


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
