"""
route_inbox.py

Scans inbox/ for driver submissions (driver-XXXXXXX.json), validates each
one via validate.py, and -- if there are no hard errors -- moves it to its
organized location (nested date path: yyyy/mm/dd/driver-XXXXXXX.json),
overwriting any existing file for that driver+date.

Log files (driver-XXXXXXX.log.json) are skipped when scanning, and are
also where any validation errors/warnings get written -- one accumulating
log per driver, across all dates, that lives in inbox/ until a human
clears it manually. Nothing is written to the log on a fully clean
validation (no errors, no warnings).

Known accepted risk (not handled here): since the uploaded filename is
always driver-XXXXXXX.json with no date in it, if two exports from the
same driver for two different dates land in inbox/ before this script
runs, the second upload overwrites the first at the git level and the
earlier day's data is lost silently. Accepted for now given low export
frequency -- flagged for future reconsideration (e.g. putting the date in
the export filename) rather than solved here.
"""

import json
import os
import shutil
import sys
from datetime import datetime, timezone

from inbox_common import (
    is_data_file,
    is_log_file,
    log_filename_for_driver,
    split_date,
)
from validate import validate_file

INBOX_DIR = "inbox"
REPO_ROOT = "."


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _read_log(log_path):
    if not os.path.exists(log_path):
        return None
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        # Corrupt log shouldn't block the pipeline -- start fresh rather
        # than crash, but this is worth a human noticing eventually.
        return None


def _append_log(inbox_dir, driver_id, date, result):
    """
    Append a validation entry to the per-driver accumulating log.
    Does nothing if the result is fully clean (no errors, no warnings).
    """
    if result.is_clean:
        return

    log_filename = log_filename_for_driver(driver_id)
    log_path = os.path.join(inbox_dir, log_filename)

    existing = _read_log(log_path)
    if existing is None:
        existing = {"driver_id": driver_id, "entries": []}

    existing["entries"].append(
        {
            "date": date,
            "timestamp": _now_iso(),
            "errors": result.errors,
            "warnings": result.warnings,
        }
    )

    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)
        f.write("\n")


def _destination_path(repo_root, date, driver_id):
    """
    Build the nested yyyy/mm/dd destination folder + full path for a
    driver-day file. Returns (None, None) if date isn't in 'YYYY-MM-DD'
    form -- shouldn't happen given validate.py already checked the format,
    but this is the last line of defense before touching the filesystem.
    """
    parts = split_date(date)
    if parts is None:
        return None, None
    yyyy, mm, dd = parts
    folder = os.path.join(repo_root, yyyy, mm, dd)
    filename = "driver-{}.json".format(driver_id)
    return folder, os.path.join(folder, filename)


def process_inbox(inbox_dir=INBOX_DIR, repo_root=REPO_ROOT):
    """
    Process every data file currently in inbox_dir.

    Returns a summary dict: {
        "moved": [...driver_ids...],
        "blocked": [...driver_ids with hard errors, left in inbox...],
    }
    """
    summary = {"moved": [], "blocked": []}

    if not os.path.isdir(inbox_dir):
        print("inbox dir '{}' does not exist -- nothing to do.".format(inbox_dir))
        return summary

    for filename in sorted(os.listdir(inbox_dir)):
        full_path = os.path.join(inbox_dir, filename)

        if not os.path.isfile(full_path):
            continue
        if is_log_file(filename):
            continue
        if not is_data_file(filename):
            # Not a recognized data or log file -- leave it alone, but
            # surface it so it doesn't silently sit there unexplained.
            print("skipping unrecognized file in inbox: {}".format(filename))
            continue

        result, payload = validate_file(full_path)

        # driver_id/date for logging purposes -- prefer payload content,
        # but fall back to filename parsing if the payload itself is too
        # broken to read (e.g. invalid JSON).
        driver_id = None
        date = None
        if payload is not None and isinstance(payload, dict):
            driver_id = payload.get("driver_id")
            date = payload.get("date")

        if not driver_id:
            from inbox_common import driver_id_from_filename

            driver_id = driver_id_from_filename(filename) or "UNKNOWN"

        _append_log(inbox_dir, driver_id, date, result)

        if not result.ok:
            print(
                "BLOCKED '{}': {} error(s) -- left in inbox, see {}.log.json".format(
                    filename, len(result.errors), driver_id
                )
            )
            summary["blocked"].append(driver_id)
            continue

        if not date:
            # Shouldn't happen if validate() passed, but guard anyway --
            # we need a date to compute the destination path.
            print(
                "BLOCKED '{}': validation passed but no usable date found -- "
                "left in inbox.".format(filename)
            )
            summary["blocked"].append(driver_id)
            continue

        folder, dest_path = _destination_path(repo_root, date, driver_id)
        if folder is None:
            # Shouldn't happen -- validate.py already checked date format --
            # but guard rather than crash on a malformed path.
            print(
                "BLOCKED '{}': validation passed but date '{}' isn't in "
                "YYYY-MM-DD form -- left in inbox.".format(filename, date)
            )
            summary["blocked"].append(driver_id)
            continue

        os.makedirs(folder, exist_ok=True)
        shutil.move(full_path, dest_path)  # overwrites if dest_path exists

        if result.warnings:
            print(
                "MOVED '{}' -> '{}' ({} warning(s) logged)".format(
                    filename, dest_path, len(result.warnings)
                )
            )
        else:
            print("MOVED '{}' -> '{}'".format(filename, dest_path))

        summary["moved"].append(driver_id)

    return summary


def _cli():
    inbox_dir = sys.argv[1] if len(sys.argv) > 1 else INBOX_DIR
    repo_root = sys.argv[2] if len(sys.argv) > 2 else REPO_ROOT
    summary = process_inbox(inbox_dir=inbox_dir, repo_root=repo_root)
    print(
        "\nDone. Moved: {}, Blocked: {}".format(
            len(summary["moved"]), len(summary["blocked"])
        )
    )
    sys.exit(1 if summary["blocked"] else 0)


if __name__ == "__main__":
    _cli()
