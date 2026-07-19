"""
aggregate.py

Reads organized driver-day data (yyyy/mm/dd/driver-XXXXXXX.json) and
produces two kinds of derived rollups, written to disk:

  rollups/yyyy/mm/dd/driver-XXXXXXX.json   -- one per driver per day
  overall/driver-XXXXXXX.json              -- one per driver, across all days

Both are fully rebuilt from scratch on every run (no incremental/merge
logic) -- simplest and safest starting point given the data volume.

Key assumption (per project decision): drivers do not submit partial
shifts. A file landing in yyyy/mm/dd/ represents a complete day. This means:
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

Active-hour pace normalization (stops_per_active_hour, pieces_per_active_hour)
is based on ELAPSED WALL-CLOCK TIME between consecutive entry_time
timestamps, not a raw count of submitted hour buckets. A driver who logs
20-25 minutes late doesn't reset the clock -- their next entry's bucket
would otherwise silently absorb that overrun and look like one inflated
"hour" of activity. See _compute_active_minutes() for the derivation.
This is computed retroactively from entry_time (already present in every
historical file since the schema's inception) rather than requiring a new
client-supplied field, so it applies uniformly to old and new data alike
with no backfill gap.
"""

import json
import os
import statistics
import sys
from datetime import datetime

from inbox_common import (
    data_filename_for_driver,
    is_day_folder,
    is_month_folder,
    is_year_folder,
    log_filename_for_driver,
    normalize_route_id,
    split_date,
)

REPO_ROOT = "."
INBOX_DIR = "inbox"
ROLLUPS_DIR = "rollups"
OVERALL_DIR = "overall"

TREND_WINDOW_DAYS = 7

# Fallback assumed duration (minutes) for an hour entry when elapsed time
# can't be derived -- no previous timestamp to diff against (first hour of
# the day with no plan entry_time either), or an unparseable/out-of-order
# timestamp. Keeps behavior sane/backward-compatible rather than blowing
# up the pace math on missing data.
ASSUMED_HOUR_MINUTES = 60.0

# How far an hour's derived elapsed time can drift from ASSUMED_HOUR_MINUTES
# before it's surfaced in data_quality.irregular_hour_gaps for a human to
# glance at. Not used to alter the math itself -- just a visibility flag.
GAP_THRESHOLD_MINUTES = 15.0

# The four pace metrics tracked both in overall averages/consistency and
# per-route trend. Centralized here so adding a future pace metric (e.g.
# something picked-up-per-hour based) only means adding a string to this
# tuple rather than touching build_overall_rollup and _build_trend_by_route
# separately.
TREND_METRICS = ("stops_per_mile", "pieces_per_mile", "stops_per_active_hour", "pieces_per_active_hour")


# ---------- loading source data ----------

def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def find_driver_day_files(repo_root):
    """
    Walk repo_root for nested yyyy/mm/dd date folders and return a list of
    (date, driver_id, full_path) tuples for every driver-day data file.

    Structural folders at the root (inbox/, rollups/, overall/, scripts/)
    are skipped automatically since they won't match is_year_folder().
    """
    results = []
    for year in sorted(os.listdir(repo_root)):
        year_dir = os.path.join(repo_root, year)
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


def _parse_iso(timestamp_str):
    """
    Parse an ISO 8601 timestamp string as written by isoTimestampNow() in
    index.html. Returns None on missing/malformed input rather than
    raising -- callers treat that as "can't derive elapsed time for this
    entry" and fall back to ASSUMED_HOUR_MINUTES.
    """
    if not timestamp_str or not isinstance(timestamp_str, str):
        return None
    try:
        return datetime.fromisoformat(timestamp_str)
    except ValueError:
        return None


def _compute_active_minutes(hour_keys, hours, plan):
    """
    Derive elapsed wall-clock minutes for each submitted hour entry by
    diffing its entry_time against the entry_time of the previous
    submission (or the plan's entry_time, for the first hour of the day),
    then sum the minutes belonging to non-break hours.

    Returns (active_minutes, irregular_gaps):
      - active_minutes: float, total elapsed minutes across non-break hours,
        the new denominator for stops_per_active_hour / pieces_per_active_hour
        (divide by 60 to get "active hours" in the old count-based sense).
      - irregular_gaps: list of {"hour": hk, "elapsed_minutes": n} for any
        hour whose derived elapsed time drifted from the assumed 60-minute
        window by more than GAP_THRESHOLD_MINUTES -- surfaced in
        data_quality for a human to glance at, not used to alter the math.

    A derived elapsed time <= 0 (e.g. a driver going back and re-saving an
    earlier hour out of order) is discarded and treated the same as a
    missing timestamp, since it isn't a trustworthy measure of real elapsed
    time.
    """
    active_minutes = 0.0
    irregular_gaps = []

    prev_time = _parse_iso(plan.get("entry_time")) if plan else None

    for hk in hour_keys:
        entry = hours[hk]
        this_time = _parse_iso(entry.get("entry_time"))

        elapsed = None
        if prev_time is not None and this_time is not None:
            candidate = (this_time - prev_time).total_seconds() / 60.0
            if candidate > 0:
                elapsed = candidate

        if elapsed is None:
            elapsed = ASSUMED_HOUR_MINUTES

        if not entry.get("break_flag"):
            active_minutes += elapsed

        if abs(elapsed - ASSUMED_HOUR_MINUTES) > GAP_THRESHOLD_MINUTES:
            irregular_gaps.append({"hour": hk, "elapsed_minutes": round(elapsed, 1)})

        # Advance the reference point even if this entry's own elapsed time
        # was thrown out, so the *next* hour is still diffed against a real
        # timestamp rather than propagating the gap forward.
        if this_time is not None:
            prev_time = this_time

    return active_minutes, irregular_gaps


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
    # Subset of all_zero_hours where the driver did NOT explicitly confirm
    # the zero via confirmed_zero (see index.html's save-flow confirm step
    # and validate.py's matching warning). This is the field a human
    # actually wants to glance at -- all_zero_hours is kept as-is,
    # unfiltered, for anyone who still wants the full raw list. Old data
    # predating confirmed_zero has no such field on any hour entry, so
    # entry.get("confirmed_zero") is falsy and every legacy all-zero hour
    # lands in this list too -- same "applies retroactively, no backfill
    # gap" pattern as _compute_active_minutes().
    unconfirmed_zero_hours = []

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
            if not entry.get("confirmed_zero"):
                unconfirmed_zero_hours.append(hk)

    # active_minutes is the time-normalized replacement denominator for the
    # two active-hour pace metrics below; active_hours (the raw bucket
    # count, computed above) is kept as-is in `actual` for display/context,
    # it's just no longer what pace is divided by.
    active_minutes, irregular_gaps = _compute_active_minutes(hour_keys, hours, plan)
    active_hours_equiv = active_minutes / 60.0

    last_hour_key = hour_keys[-1] if hour_keys else None
    finish_time = hours[last_hour_key].get("entry_time") if last_hour_key else None

    predicted_finish = plan.get("predicted_finish") if plan else None
    # Normalized here (strip + uppercase) rather than trusting upstream
    # casing -- index.html already uppercases on save, but this is the
    # single point every downstream consumer of route_id for grouping
    # (this file's own _build_trend_by_route, and trends.py's per-route
    # series) reads through, so it's now a data guarantee rather than a
    # process one. Raw yyyy/mm/dd source files are left untouched -- this
    # only affects the rollup.
    route_id = normalize_route_id(plan.get("route_id")) if plan else None
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
    stops_per_active_hour = round(total_stops / active_hours_equiv, 2) if active_hours_equiv > 0 else None
    pieces_per_active_hour = round(total_pieces / active_hours_equiv, 2) if active_hours_equiv > 0 else None

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
            "unconfirmed_zero_hours": unconfirmed_zero_hours,
            "irregular_hour_gaps": irregular_gaps,
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
    prior trend, per pace metric (see TREND_METRICS), within each route's
    own date-ordered series. Each metric gets both an avg and a stddev for
    the recent and prior windows, so a consumer can draw a variance band
    around each segment rather than just two point estimates.

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
        recent = route_rollups[-TREND_WINDOW_DAYS:]
        prior = route_rollups[:-TREND_WINDOW_DAYS]

        route_trend = {
            "days_tracked": len(route_rollups),
            "window_days": TREND_WINDOW_DAYS,
        }
        for metric in TREND_METRICS:
            recent_series = [r["pace"][metric] for r in recent]
            prior_series = [r["pace"][metric] for r in prior]
            route_trend["{}_recent_avg".format(metric)] = _safe_avg(recent_series)
            # None (not 0) when there isn't enough same-route history yet.
            route_trend["{}_prior_avg".format(metric)] = _safe_avg(prior_series) if prior else None
            # Per-route stddev, same windows as the averages above -- lets a
            # consumer draw a variance band around each segment of the trend
            # line rather than only having the all-days-combined figure in
            # the driver's overall consistency block. _safe_stddev already
            # returns None under 2 points, so a route with only 1 day (or an
            # empty prior window) naturally yields None here too.
            route_trend["{}_recent_stddev".format(metric)] = _safe_stddev(recent_series)
            route_trend["{}_prior_stddev".format(metric)] = _safe_stddev(prior_series) if prior else None

        trend[route_id] = route_trend
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
    pieces_per_mile_series = [r["pace"]["pieces_per_mile"] for r in daily_rollups]
    stops_per_active_hour_series = [r["pace"]["stops_per_active_hour"] for r in daily_rollups]
    pieces_per_active_hour_series = [r["pace"]["pieces_per_active_hour"] for r in daily_rollups]
    finish_delta_series = [r["plan_vs_actual"]["finish_time_delta_minutes"] for r in daily_rollups]

    total_warnings = sum(r["data_quality"]["warning_count"] for r in daily_rollups)
    total_errors = sum(r["data_quality"]["error_count"] for r in daily_rollups)
    days_with_gaps = sum(1 for r in daily_rollups if r["data_quality"]["has_gaps"])
    days_with_irregular_hours = sum(
        1 for r in daily_rollups if r["data_quality"]["irregular_hour_gaps"]
    )
    days_with_unconfirmed_zero_hours = sum(
        1 for r in daily_rollups if r["data_quality"]["unconfirmed_zero_hours"]
    )

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
            "pieces_per_mile": _safe_avg(pieces_per_mile_series),
            "stops_per_active_hour": _safe_avg(stops_per_active_hour_series),
            "pieces_per_active_hour": _safe_avg(pieces_per_active_hour_series),
            "finish_time_delta_minutes": _safe_avg(finish_delta_series),
        },
        "consistency": {
            "stops_per_mile_stddev": _safe_stddev(stops_per_mile_series),
            "pieces_per_mile_stddev": _safe_stddev(pieces_per_mile_series),
            "stops_per_active_hour_stddev": _safe_stddev(stops_per_active_hour_series),
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
            "days_with_irregular_hours": days_with_irregular_hours,
            "days_with_unconfirmed_zero_hours": days_with_unconfirmed_zero_hours,
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

        yyyy, mm, dd = split_date(date)
        out_dir = os.path.join(rollups_dir, yyyy, mm, dd)
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
