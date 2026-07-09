# `inbox_common.py`

Shared helpers used by every other script in the pipeline. Nothing in
here does I/O beyond string/regex logic — its whole job is to stop
`validate.py`, `route_inbox.py`, `aggregate.py`, `visualize.py`, and
`trends.py` from each independently reinventing (and inevitably
drifting on) the same filename and folder conventions.

## What it defines

**Filename recognition**
- `is_data_file(filename)` — true for `driver-XXXXXXX.json`, false for
  log files or anything else.
- `is_log_file(filename)` — true for `driver-XXXXXXX.log.json`.
- `driver_id_from_filename(filename)` — fallback extraction of the
  driver ID from a filename. Only a fallback: the JSON body's
  `driver_id` field is the source of truth wherever it's available and
  readable.
- `data_filename_for_driver(id)` / `log_filename_for_driver(id)` —
  build the two filename shapes in the other direction.

**Date/folder handling**
- `split_date("2026-06-26")` → `("2026", "06", "26")`, used to build the
  nested `yyyy/mm/dd/` path everywhere data is written or read. Returns
  `None` if the string isn't `YYYY-MM-DD` — callers treat that as "can't
  route this," not a crash.
- `is_year_folder` / `is_month_folder` / `is_day_folder` — used when
  *walking* the tree (in `aggregate.py` and `visualize.py`) to tell a
  genuine date path segment apart from structural folders like `inbox/`,
  `rollups/`, `overall/`, `scripts/`.

## Why this exists as its own file

Two failure modes it's specifically designed to prevent:

1. **Two scripts disagreeing on what counts as a data file.** If
   `route_inbox.py` and `validate.py` each had their own regex for
   "is this a driver submission," a small drift between them (e.g. one
   allowing dots in driver IDs and the other not) would be a subtle,
   hard-to-notice bug. There's exactly one regex, imported everywhere.
2. **Two scripts disagreeing on folder-walking rules.** `aggregate.py`
   and `visualize.py` both need to walk a `yyyy/mm/dd/` tree while
   ignoring non-date folders at the root. Both call the same
   `is_year_folder`/`is_month_folder`/`is_day_folder` helpers rather
   than each hand-rolling their own "is this 4 digits" check.

## Notes

- `date` inside the JSON payload is always `YYYY-MM-DD` (a single
  string, validated by `validate.py`). The **on-disk folder** is the
  thing that's nested (`yyyy/mm/dd/`) — `split_date()` is the only
  place that translates between the two representations.
- Nothing here validates *values* (that's `validate.py`'s job) — this
  file only recognizes shapes (filenames, folder names, date strings).
