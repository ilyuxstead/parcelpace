# `aggregate.py`

Stage 2 of the pipeline. Walks the organized `yyyy/mm/dd/` data tree
and rebuilds two kinds of derived output, **from scratch, every run**:

- `rollups/yyyy/mm/dd/driver-XXXXXXX.json` — one file per driver per
  day.
- `overall/driver-XXXXXXX.json` — one file per driver, across every
  day they've ever logged.

## Entry point

`run(repo_root=".", inbox_dir=None, rollups_dir=None, overall_dir=None)`.
Run standalone via `python aggregate.py [repo_root]`, or as stage 2 of
`run_pipeline.py`.

## Assumption this whole file leans on

**Drivers don't submit partial shifts.** A file landing in
`yyyy/mm/dd/` represents a *complete* day. That assumption is why:
- plan-vs-actual deltas are always treated as meaningful (there's no
  "day still in progress" ambiguity to special-case),
- a day's "finish time" is defined as the `entry_time` of whichever
  hour key is numerically **highest** in that day's `hours` dict — not
  the latest `entry_time` across all hours. If entries were logged out
  of order, the highest hour number still wins.

---

## The elapsed-minutes / active-hours computation

This is the part worth reading slowly, since it's the least obvious
piece of math in the whole pipeline.

### The problem it's solving

`stops_per_active_hour` and `pieces_per_active_hour` need a
denominator: how many hours did the driver actually spend working
(excluding breaks)? The naive answer is "count the non-break hour
buckets" — but a bucket isn't a fixed 60 minutes in reality. If a
driver logs in 20–25 minutes late for one check-in, that overrun
doesn't disappear — it just gets silently absorbed into whatever the
*next* bucket looks like, making that hour look artificially more (or
less) productive per unit time than it really was.

So instead of counting buckets, `_compute_active_minutes()` derives
**real elapsed wall-clock time** between consecutive `entry_time`
timestamps, and sums that.

### The mechanism, step by step

```python
def _compute_active_minutes(hour_keys, hours, plan):
    active_minutes = 0.0
    irregular_gaps = []

    prev_time = _parse_iso(plan.get("entry_time")) if plan else None

    for hk in hour_keys:                      # hour keys, sorted numerically
        entry = hours[hk]
        this_time = _parse_iso(entry.get("entry_time"))

        elapsed = None
        if prev_time is not None and this_time is not None:
            candidate = (this_time - prev_time).total_seconds() / 60.0
            if candidate > 0:
                elapsed = candidate

        if elapsed is None:
            elapsed = ASSUMED_HOUR_MINUTES          # 60.0, the fallback

        if not entry.get("break_flag"):
            active_minutes += elapsed

        if abs(elapsed - ASSUMED_HOUR_MINUTES) > GAP_THRESHOLD_MINUTES:  # 15.0
            irregular_gaps.append({"hour": hk, "elapsed_minutes": round(elapsed, 1)})

        if this_time is not None:
            prev_time = this_time                   # advance regardless

    return active_minutes, irregular_gaps
```

Walking through what happens for **one hour entry** `hk`:

1. **`this_time`** = that hour's own `entry_time`, parsed. (`None` if
   missing/malformed.)
2. **`elapsed`** = minutes between `this_time` and whatever the
   *previous* timestamp was (`prev_time`) — **not** a fixed 60. For the
   very first hour of the day, `prev_time` starts out as the **plan's**
   `entry_time` (when the driver submitted their day plan), not the
   shift's actual start time.
3. If that diff can't be computed (missing timestamp on either side),
   **or comes out ≤ 0** (e.g. a driver re-saving an earlier hour out of
   order, so the "next" entry's timestamp is actually earlier) — the
   diff is discarded and `elapsed` falls back to the flat assumption of
   `ASSUMED_HOUR_MINUTES = 60`.
4. That `elapsed` value (real or fallback) is added to
   `active_minutes`, **but only if `break_flag` is falsy** for this
   hour. Break hours contribute their elapsed time to nothing here —
   see "break_flag asymmetry" below.
5. If `elapsed` differs from the assumed 60 by more than
   `GAP_THRESHOLD_MINUTES` (15), it's recorded in `irregular_gaps` —
   purely a data-quality flag for a human to notice later. It does
   **not** change the math; a huge gap still gets added to
   `active_minutes` at its real value (or the 60-minute fallback, if
   that's what was used).
6. `prev_time` is advanced to `this_time` **whenever `this_time` exists
   at all** — even if this particular hour's own `elapsed` was thrown
   out for being ≤ 0. This matters: it means a single bad/out-of-order
   timestamp doesn't propagate its error forward into every subsequent
   hour's diff. The next hour is still diffed against a real
   timestamp, not a stale one.

Finally, in `build_daily_rollup()`:

```python
active_minutes, irregular_gaps = _compute_active_minutes(hour_keys, hours, plan)
active_hours_equiv = active_minutes / 60.0
...
stops_per_active_hour = round(total_stops / active_hours_equiv, 2) if active_hours_equiv > 0 else None
```

`active_minutes` is converted back into "hour units" (`/ 60.0`) purely
so the resulting pace number reads naturally as "stops per hour" — the
denominator itself is still built from real elapsed minutes, not a
bucket count.

### A worked example

Say a driver's day looks like this:

| Event | `entry_time` |
|---|---|
| Plan submitted | `09:00:00` |
| Hour `09` (break_flag=false) | `10:05:00` |
| Hour `10` (break_flag=false) | `10:58:00` |
| Hour `11` (break_flag=true, lunch) | `12:10:00` |
| Hour `12` (break_flag=false) | `13:40:00` |

Computing elapsed time per hour (each diffed against the *previous
row's* timestamp):

- Hour `09`: `10:05 - 09:00` = 65 min → not break → **+65** to
  `active_minutes`. `|65-60|=5`, under threshold, no flag.
- Hour `10`: `10:58 - 10:05` = 53 min → not break → **+53**.
  `|53-60|=7`, no flag.
- Hour `11`: `12:10 - 10:58` = 72 min → **is** break → contributes
  **0** to `active_minutes` (but the 72 minutes themselves are simply
  not counted anywhere — see below). `|72-60|=12`, still under the
  15-minute threshold, no flag.
- Hour `12`: `13:40 - 12:10` = 90 min → not break → **+90**.
  `|90-60|=30` → **flagged** as an irregular gap (a long lunch that ran
  over, worth a human's attention).

`active_minutes = 65 + 53 + 0 + 90 = 208`, so
`active_hours_equiv = 208 / 60 = 3.4667`. If `total_stops = 34` for the
day, `stops_per_active_hour = 34 / 3.4667 ≈ 9.81`.

Note the break hour's 72 minutes aren't "missing" from the day — they
still show up in `data_quality.irregular_hour_gaps` if they cross the
threshold, and the raw totals (`total_stops`, `total_miles`, etc.)
still include whatever activity was logged in that hour. They're just
excluded from *this one specific denominator*.

### The `break_flag` asymmetry (important, easy to miss)

`break_flag` gates **only** the `active_minutes` denominator used by
`stops_per_active_hour` / `pieces_per_active_hour`. It does **not**
filter:
- `total_stops`, `total_miles`, `total_pieces`,
  `total_pieces_picked_up` (raw totals — a break hour's numbers, if any
  were logged, still count),
- `stops_per_mile` / `pieces_per_mile` (denominator is miles, entirely
  unrelated to break status),
- the raw `active_hours` bucket count kept in `actual` for
  display/context (that's just `len(non-break hour keys)`, informational
  only — it is *not* what pace is divided by).

So a "break hour" is really "an hour excluded from the active-time
denominator," not "an hour whose data disappears." This is intentional
but non-obvious from a first read of the JSON output.

### A sharp edge worth knowing about

The **first** hour of the day is diffed against the **plan's**
`entry_time`, not the actual shift start. If a driver submits their
plan at 6 AM (from home, before driving to the depot) but doesn't log
their first hourly check-in until 9 AM, that entire 3-hour gap gets
counted as elapsed time for hour one (assuming it isn't flagged as
break) — and will almost certainly trip `irregular_hour_gaps` for
being way over the 15-minute threshold, which is exactly the point:
it's surfaced for a human to notice, not silently absorbed.

### Constants that control this

- `ASSUMED_HOUR_MINUTES = 60.0` — fallback when elapsed time can't be
  derived at all (no previous timestamp, unparseable/out-of-order
  timestamp).
- `GAP_THRESHOLD_MINUTES = 15.0` — how far elapsed time can drift from
  60 before it's surfaced in `data_quality.irregular_hour_gaps`. Purely
  a visibility flag; never changes the math.

### Why this is retroactive with no backfill needed

`entry_time` has been present in every historical hour entry and plan
since the schema's inception — this computation is derived entirely
from data that already exists. Re-running `aggregate.py` against old
data picks this up automatically; no client-side change or backfill
script was needed.

---

## Everything else in the daily rollup

`build_daily_rollup(date, driver_id, payload, inbox_dir)` produces one
JSON object per driver-day, with these top-level blocks:

- **`plan`** — `null` if no plan was submitted, otherwise
  `route_id`/`planned_stops`/`planned_miles`/`planned_pieces`/
  `predicted_finish` copied straight from the source payload.
- **`actual`** — totals for the day (stops/miles/pieces/pieces picked
  up), plus `active_hours`/`break_hours` (raw bucket counts, for
  display — not the pace denominator), `hours_submitted` (sorted hour
  keys), `last_hour_key`, `finish_time`.
- **`plan_vs_actual`** — `stops_delta`/`miles_delta`/`pieces_delta`
  (actual minus planned; `None` if there was no plan), plus
  `finish_time_delta_minutes` via `_finish_time_delta_minutes()`.
  **Known limitation:** that finish-time diff assumes same-day
  `HH:MM` vs. the actual finish timestamp's time-of-day — it will
  produce a nonsense result for any shift that crosses midnight. Not
  handled; flagged as a known edge case.
- **`pace`** — the four `TREND_METRICS`: `stops_per_mile`,
  `pieces_per_mile` (denominator: total miles), `stops_per_active_hour`,
  `pieces_per_active_hour` (denominator: `active_hours_equiv`, see
  above). All `None` if their denominator is zero/unavailable rather
  than a divide-by-zero.
- **`data_quality`** — `warning_count`/`error_count` (summed from that
  driver's accumulating inbox log, filtered to this date via
  `_log_counts_for_date`), `has_gaps` (non-contiguous hour keys, via
  `_has_gaps`), `all_zero_hours` (list of hour keys where every numeric
  field was 0), `irregular_hour_gaps` (from the elapsed-minutes
  computation above).

## The overall (all-time) rollup

`build_overall_rollup(driver_id, daily_rollups)` — one per driver,
combining every daily rollup on disk for them:

- **`totals`** — plain sums across all days.
- **`averages`** — per-day and per-pace-metric averages
  (`_safe_avg`, which quietly drops `None`s rather than treating them
  as zero).
- **`consistency`** — stddev of each pace metric across all days
  (`_safe_stddev`; requires ≥2 non-`None` values or returns `None`).
- **`trend.by_route`** — see below.
- **`data_quality`** — summed warnings/errors, count of days with gaps,
  count of days with any irregular hour gap.

### Per-route trend windows

`_build_trend_by_route()` groups daily rollups by `route_id` (days
with no plan/no route are excluded — nothing to group them by), sorts
each route's days chronologically, then splits into a `recent` window
(last `TREND_WINDOW_DAYS = 7` entries in that list) and everything
before it (`prior`). **This window is "last 7 days this specific route
was driven," not 7 calendar days** — same-route days are rarely
contiguous on a driver's calendar, so a calendar-day window would
either mix in unrelated routes or skip route days that are further
apart than a week. Each of the four `TREND_METRICS` gets a
`_recent_avg`/`_prior_avg`/`_recent_stddev`/`_prior_stddev` in this
block — the stddevs let a downstream chart (`trends.py`) draw a
variance band around each segment, not just a single point estimate.

## Log caching

`_load_driver_log()` memoizes each driver's parsed
`driver-XXXXXXX.log.json` in a module-level `_log_cache` dict, so a
driver with many days on record only triggers one file read for their
log, not one per day rolled up. `run()` clears this cache at the start
of every invocation.

## Orchestration

`run()` walks the whole tree via `find_driver_day_files()`, builds and
writes one daily rollup per file found (grouped in-memory by
`driver_id` as it goes), then — once every day is processed — builds
and writes one overall rollup per driver from that in-memory
collection. Both `rollups/` and `overall/` are fully rewritten every
run; nothing here reads its own previous output.
