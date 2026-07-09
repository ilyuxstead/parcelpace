# `route_inbox.py`

Stage 1 of the pipeline. Scans `inbox/`, validates each driver
submission, and either files it into the organized `yyyy/mm/dd/` tree
or leaves it in `inbox/` for a human, depending on whether validation
found any hard errors.

## Entry point

`process_inbox(inbox_dir="inbox", repo_root=".")` → returns
`{"moved": [driver_ids...], "blocked": [driver_ids...]}`.

This is what `run_pipeline.py` calls as stage 1. Run standalone via
`python route_inbox.py [inbox_dir] [repo_root]`.

## Per-file flow

For every file in `inbox_dir`, in sorted filename order:

1. **Skip** log files (`driver-XXXXXXX.log.json`) and anything that
   doesn't match the data-file pattern (those get a printed notice so
   they don't silently sit there unexplained, but are otherwise left
   alone).
2. **Validate** via `validate.py`'s `validate_file()`.
3. **Determine `driver_id`/`date`** — prefer the values from the parsed
   JSON body; fall back to parsing the driver ID out of the filename
   only if the payload itself was too broken to read (e.g. invalid
   JSON). If even that fails, `driver_id` becomes `"UNKNOWN"`.
4. **Append to the driver's log** (`_append_log`) — a no-op if the
   validation result was fully clean. Otherwise appends one entry
   (`date`, `timestamp`, `errors`, `warnings`) to that driver's
   ever-accumulating `driver-XXXXXXX.log.json` in `inbox/`. This log is
   never truncated or rotated automatically — a human clears it
   manually.
5. **If `result.ok` is false** → file stays in `inbox/`, driver ID goes
   into `summary["blocked"]`. Nothing else about this file is touched.
6. **If `result.ok` is true but `date` is missing**, or the date fails
   `split_date()` — these are defensive-only branches (validate.py
   should have already caught both cases), but if they fire, the file
   is treated as blocked rather than crashing.
7. **Otherwise**, build the destination path
   (`{repo_root}/{yyyy}/{mm}/{dd}/driver-XXXXXXX.json`), create the
   folder if needed, and `shutil.move()` the file there — **overwriting**
   any existing file for that same driver+date.

One blocked file never stops the batch — every other file in `inbox/`
still gets processed on its own merits.

## Known accepted risk (by design, not a bug)

The uploaded filename is always `driver-XXXXXXX.json` — it carries no
date. If the same driver uploads two different days' payloads into
`inbox/` before this script has run, **the second upload silently
overwrites the first at the filesystem level**, before validation or
routing ever sees the first one. This is an accepted risk given low
export frequency, not something this script defends against. See the
project README's "Known accepted risk" note if this ever needs
revisiting (e.g. putting the date into the export filename itself).

## Notes

- `_read_log()` treats a corrupt/unparseable existing log file as "no
  log yet" rather than crashing — a damaged log shouldn't block the
  pipeline, though a human should eventually notice it happened.
- The destination move **overwrites** any existing file for the same
  driver+date — this is the resubmission path (a driver correcting an
  earlier upload for the same day) working as intended, not a bug.
