"""
aggregate.py

Reads organized driver-day data (<date>/driver-XXXXXXX.json) and produces
two kinds of derived rollups, written to disk:

  rollups/<date>/driver-XXXXXXX.json   -- one per driver per day
  overall/driver-XXXXXXX.json          -- one per driver, across all days

Both are fully rebuilt from scratch on every run (no incremental/merge
logic) -- simplest and safest starting point given the data volume.

Key assumption (per project decision): drivers do not submit partial
shifts. A file landing in <date>/ represents a complete day. This means:
  - plan_vs_actual deltas are always meaningful (no "day still in
    progress" ambiguity to worry about)
  - "finish time" for a day = the entry_time of whichever hour key is
    numerically highest in that day's `hours` dict (NOT the latest
    entry_time across all hours -- the highest hour-number key wins,
    even if entries were logged slightly out of order)

data_quality numbers come from each driver's accumulating inbox log
(inbox/driver-XXXXXXX.log.json), filtered down to entries matching the
specific date being rolled up. The log file is read once per driver and
reused across all of that driver's dates, rather than re-read per date.
"""

import json
import os
import statistics
import sys
from datetime import datetime

from inbox_common import data_filename_for_driver, is_date_folder, log_filename_for_driver

REPO_ROOT = "."
INBOX_DIR = "inbox"
ROLLUPS_DIR = "rollups"
OVERALL_DIR = "overall"

TREND_WINDOW_DAYS = 7


# ---------- loading source data ----------

def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def find_driver_day_files(repo_root):
    """
    Walk repo_root for date-folders and return a list of
    (date, driver_id, full_path) tuples for every driver-day data file.
    """
    results = []
    for entry in sorted(os.listdir(repo_root)):
        full_dir = os.path.join(repo_root, entry)
        if not os.path.isdir(full_dir) or not is_date_folder(entry):
            continue
        for filename in sorted(os.listdir(full_dir)):
            if not filename.startswith("driver-") or not filename.endswith(".json"):
                continue
            driver_id = filename[len("driver-"):-len(".json")]
            results.append((entry, driver_id, os.path.join(full_dir, filename)))
    return results


_log_cache = {}


def _load_driver_log(inbox_dir, driver_id):
    if driver_id in _log_cache:
        return _log_cache[driver_id]
    path = os.path.join(inbox_dir, log_filename_for_driver(driver_id))
    data = _load_json(path) or {"driver_id": driver_id, "entries": []}
    _log_cache[driver_id] = data
    return data


def _log_counts_for_date(inbox_dir, driver_id, date):
    """Sum warning/error counts from a driver's accumulating log, filtered to one date."""
    log = _load_driver_log(inbox_dir, driver_id)
    warning_count = 0
    error_count = 0
    for entry in log.get("entries", []):
        if entry.get("date") != date:
            continue
        warning_count += len(entry.get("warnings", []))
        error_count += len(entry.get("errors", []))
    return warning_count, error_count


# ---------- daily rollup computation ----------

def _hour_keys_sorted(hours):
    """Sort hour keys numerically (string keys like '09', '17')."""
    return sorted(hours.keys(), key=lambda h: int(h))


def _has_gaps(hour_keys):
    if len(hour_keys) < 2:
        return False
    nums = [int(h) for h in hour_keys]
    span = max(nums) - min(nums) + 1
    return span != len(nums)


def _finish_time_delta_minutes(predicted_finish, finish_time_iso):
    """
    Diff predicted_finish (HH:MM, same-day assumed) against the actual
    finish timestamp's time-of-day. Returns None if either side is missing.
    Known limitation: HH:MM has no date, so this breaks for shifts that
    cross midnight -- not handled, flagged as a known edge case.
    """
    if not predicted_finish or not finish_time_iso:
        return None
    try:
        finish_dt = datetime.fromisoformat(finish_time_iso)
    except ValueError:
        return None
    try:
        pred_h, pred_m = [int(x) for x in predicted_finish.split(":")]
    except ValueError:
        return None
    predicted_minutes = pred_h * 60 + pred_m
    actual_minutes = finish_dt.hour * 60 + finish_dt.minute
    return actual_minutes - predicted_minutes


def build_daily_rollup(date, driver_id, payload, inbox_dir):
    plan = payload.get("plan")
    hours = payload.get("hours") or {}
    hour_keys = _hour_keys_sorted(hours)

    total_stops = 0
    total_miles = 0.0
    total_pieces = 0
    total_pieces_picked_up = 0
    active_hours = 0
    break_hours = 0
    all_zero_hours = []

    for hk in hour_keys:
        entry = hours[hk]
        stops = entry.get("hourly_stops", 0) or 0
        miles = entry.get("hourly_miles", 0) or 0
        pieces = entry.get("hourly_pieces", 0) or 0
        pickup = entry.get("hourly_pieces_picked_up", 0) or 0

        total_stops += stops
        total_miles += miles
        total_pieces += pieces
        total_pieces_picked_up += pickup

        if entry.get("break_flag"):
            break_hours += 1
        else:
            active_hours += 1

        if stops == 0 and miles == 0 and pieces == 0 and pickup == 0:
            all_zero_hours.append(hk)

    last_hour_key = hour_keys[-1] if hour_keys else None
    finish_time = hours[last_hour_key].get("entry_time") if last_hour_key else None

    predicted_finish = plan.get("predicted_finish") if plan else None
    route_id = plan.get("route_id") if plan else None
    planned_stops = plan.get("planned_stops") if plan else None
    planned_miles = plan.get("planned_miles") if plan else None
    planned_pieces = plan.get("planned_pieces") if plan else None

    stops_delta = (total_stops - planned_stops) if isinstance(planned_stops, (int, float)) else None
    miles_delta = (round(total_miles - planned_miles, 2)
                   if isinstance(planned_miles, (int, float)) else None)
    pieces_delta = (total_pieces - planned_pieces) if isinstance(planned_pieces, (int, float)) else None
    finish_delta = _finish_time_delta_minutes(predicted_finish, finish_time)

    stops_per_mile = round(total_stops / total_miles, 2) if total_miles > 0 else None
    pieces_per_mile = round(total_pieces / total_miles, 2) if total_miles > 0 else None
    stops_per_active_hour = round(total_stops / active_hours, 2) if active_hours > 0 else None
    pieces_per_active_hour = round(total_pieces / active_hours, 2) if active_hours > 0 else None

    warning_count, error_count = _log_counts_for_date(inbox_dir, driver_id, date)

    return {
        "driver_id": driver_id,
        "date": date,
        "plan": {
            "route_id": route_id,
            "planned_stops": planned_stops,
            "planned_miles": planned_miles,
            "planned_pieces": planned_pieces,
            "predicted_finish": predicted_finish,
        } if plan else None,
        "actual": {
            "total_stops": total_stops,
            "total_miles": round(total_miles, 2),
            "total_pieces": total_pieces,
            "total_pieces_picked_up": total_pieces_picked_up,
            "active_hours": active_hours,
            "break_hours": break_hours,
            "hours_submitted": hour_keys,
            "last_hour_key": last_hour_key,
            "finish_time": finish_time,
        },
        "plan_vs_actual": {
            "stops_delta": stops_delta,
            "miles_delta": miles_delta,
            "pieces_delta": pieces_delta,
            "finish_time_delta_minutes": finish_delta,
        },
        "pace": {
            "stops_per_mile": stops_per_mile,
            "pieces_per_mile": pieces_per_mile,
            "stops_per_active_hour": stops_per_active_hour,
            "pieces_per_active_hour": pieces_per_active_hour,
        },
        "data_quality": {
            "warning_count": warning_count,
            "error_count": error_count,
            "has_gaps": _has_gaps(hour_keys),
            "all_zero_hours": all_zero_hours,
        },
    }


# ---------- driver-overall rollup computation ----------

def _safe_avg(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 2) if values else None


def _safe_stddev(values):
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return None
    return round(statistics.stdev(values), 2)


def _build_trend_by_route(daily_rollups):
    """
    Group daily rollups by route_id (days with no plan / no route_id are
    excluded -- there's nothing to group them by) and compute a recent-vs-
    prior stops_per_mile trend within each route's own date-ordered series.

    Windows are based on "last N days this route was driven", not calendar
    days -- same-route days are rarely contiguous on the calendar, so a
    calendar-day window would mix in unrelated routes or skip route days
    that are further apart than TREND_WINDOW_DAYS.
    """
    by_route = {}
    for r in daily_rollups:
        route_id = r["plan"]["route_id"] if r["plan"] else None
        if not route_id:
            continue
        by_route.setdefault(route_id, []).append(r)

    trend = {}
    for route_id, route_rollups in by_route.items():
        route_rollups = sorted(route_rollups, key=lambda r: r["date"])
        series = [r["pace"]["stops_per_mile"] for r in route_rollups]
        recent = route_rollups[-TREND_WINDOW_DAYS:]
        prior = route_rollups[:-TREND_WINDOW_DAYS]

        trend[route_id] = {
            "days_tracked": len(route_rollups),
            "stops_per_mile_recent_avg": _safe_avg([r["pace"]["stops_per_mile"] for r in recent]),
            # None (not 0) when there isn't enough same-route history yet.
            "stops_per_mile_prior_avg": _safe_avg([r["pace"]["stops_per_mile"] for r in prior]) if prior else None,
            "window_days": TREND_WINDOW_DAYS,
        }
    return trend


def build_overall_rollup(driver_id, daily_rollups):
    """daily_rollups: list of daily rollup dicts for one driver, any order."""
    daily_rollups = sorted(daily_rollups, key=lambda r: r["date"])
    dates = [r["date"] for r in daily_rollups]

    total_stops = sum(r["actual"]["total_stops"] for r in daily_rollups)
    total_miles = round(sum(r["actual"]["total_miles"] for r in daily_rollups), 2)
    total_pieces = sum(r["actual"]["total_pieces"] for r in daily_rollups)
    total_pieces_picked_up = sum(r["actual"]["total_pieces_picked_up"] for r in daily_rollups)

    days_tracked = len(daily_rollups)

    stops_per_mile_series = [r["pace"]["stops_per_mile"] for r in daily_rollups]
    pieces_per_active_hour_series = [r["pace"]["pieces_per_active_hour"] for r in daily_rollups]
    finish_delta_series = [r["plan_vs_actual"]["finish_time_delta_minutes"] for r in daily_rollups]

    total_warnings = sum(r["data_quality"]["warning_count"] for r in daily_rollups)
    total_errors = sum(r["data_quality"]["error_count"] for r in daily_rollups)
    days_with_gaps = sum(1 for r in daily_rollups if r["data_quality"]["has_gaps"])

    return {
        "driver_id": driver_id,
        "days_tracked": days_tracked,
        "date_range": {"first": dates[0], "last": dates[-1]} if dates else None,
        "totals": {
            "total_stops": total_stops,
            "total_miles": total_miles,
            "total_pieces": total_pieces,
            "total_pieces_picked_up": total_pieces_picked_up,
        },
        "averages": {
            "stops_per_day": round(total_stops / days_tracked, 2) if days_tracked else None,
            "pieces_per_day": round(total_pieces / days_tracked, 2) if days_tracked else None,
            "stops_per_mile": _safe_avg(stops_per_mile_series),
            "pieces_per_active_hour": _safe_avg(pieces_per_active_hour_series),
            "finish_time_delta_minutes": _safe_avg(finish_delta_series),
        },
        "consistency": {
            "stops_per_mile_stddev": _safe_stddev(stops_per_mile_series),
            "pieces_per_active_hour_stddev": _safe_stddev(pieces_per_active_hour_series),
        },
        "trend": {
            "by_route": _build_trend_by_route(daily_rollups),
            "window_days": TREND_WINDOW_DAYS,
        },
        "data_quality": {
            "total_warnings": total_warnings,
            "total_errors": total_errors,
            "days_with_gaps": days_with_gaps,
        },
    }


# ---------- orchestration ----------

def run(repo_root=REPO_ROOT, inbox_dir=None, rollups_dir=None, overall_dir=None):
    inbox_dir = inbox_dir or os.path.join(repo_root, INBOX_DIR)
    rollups_dir = rollups_dir or os.path.join(repo_root, ROLLUPS_DIR)
    overall_dir = overall_dir or os.path.join(repo_root, OVERALL_DIR)

    _log_cache.clear()

    driver_day_files = find_driver_day_files(repo_root)
    print("Found {} driver-day file(s) across {} date folder(s).".format(
        len(driver_day_files), len(set(d for d, _, _ in driver_day_files))
    ))

    daily_rollups_by_driver = {}

    for date, driver_id, path in driver_day_files:
        payload = _load_json(path)
        if payload is None:
            print("  skipping unreadable file: {}".format(path))
            continue

        rollup = build_daily_rollup(date, driver_id, payload, inbox_dir)

        out_dir = os.path.join(rollups_dir, date)
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, data_filename_for_driver(driver_id))
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(rollup, f, indent=2)
            f.write("\n")

        daily_rollups_by_driver.setdefault(driver_id, []).append(rollup)

    os.makedirs(overall_dir, exist_ok=True)
    for driver_id, rollups in daily_rollups_by_driver.items():
        overall = build_overall_rollup(driver_id, rollups)
        out_path = os.path.join(overall_dir, data_filename_for_driver(driver_id))
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(overall, f, indent=2)
            f.write("\n")

    print("Wrote daily rollups for {} driver(s) to '{}'.".format(
        len(daily_rollups_by_driver), rollups_dir
    ))
    print("Wrote overall rollups for {} driver(s) to '{}'.".format(
        len(daily_rollups_by_driver), overall_dir
    ))


def _cli():
    repo_root = sys.argv[1] if len(sys.argv) > 1 else REPO_ROOT
    run(repo_root=repo_root)


if __name__ == "__main__":
    _cli()
