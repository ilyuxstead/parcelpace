# `run_pipeline.py`

The single entry point that chains the full pipeline in the order it's
meant to run:

```
route_inbox.py  ->  aggregate.py  ->  visualize.py  ->  trends.py
```

## Entry point

`run(repo_root=".")` — calls each stage's own existing
`run()`/`process_inbox()` function in sequence, exactly as if all four
scripts had been run by hand back to back. Nothing here duplicates
their logic; this is purely an orchestrator. Run standalone via
`python run_pipeline.py [repo_root]`.

Returns the stage-1 (`route_inbox`) summary dict
(`{"moved": [...], "blocked": [...]}`) so the CLI wrapper can decide the
process exit code.

## Why a blocked file doesn't stop the whole run

`route_inbox.py` leaves any driver-day file with hard validation errors
sitting in `inbox/` for a human to look at — it does not raise, and it
does not stop processing the rest of `inbox/`. `run_pipeline.py`
extends that same philosophy across the whole chain:

- `aggregate.py` only ever reads from the already-organized
  `yyyy/mm/dd/` tree, `rollups/`, and the accumulating per-driver log
  files (and only for `data_quality` counts, not routing decisions).
- `visualize.py` and `trends.py` only read `rollups/` and `overall/`.

None of stages 2–4 touch files still pending in `inbox/`. So a blocked
driver has zero bearing on whether everyone else's rollups and charts
can be correctly rebuilt — halting the entire run over one bad
submission would just delay every other driver's output for no
benefit.

## What still surfaces the problem

The process **does** exit non-zero (`sys.exit(1)`) if `inbox_summary["blocked"]`
is non-empty, and the blocked driver IDs are printed in the final
summary along with a note that they're waiting in `inbox/` for review.
This is what makes a blocked file visible to cron/CI output without
requiring a human to babysit every single run — a green exit code means
"nothing needs attention," a non-zero one means "check `inbox/`," but
either way rollups/charts for everyone else are already up to date.

## Stage-by-stage summary printed to stdout

Each stage prints a `STAGE n/4: name` header as it starts (via
`_stage_header`), and the final block prints how many files were moved
vs. blocked, plus the blocked driver IDs if any exist.

## Notes

- All four stages retain their own "full rebuild every run" behavior —
  this orchestrator adds no incremental logic of its own; it's purely
  sequencing.
- If Woodpecker CI is wired up later (see the project README's
  "Automation" section), the CI job would call this same `run()`
  entry point rather than reimplementing the chain — that's the whole
  reason this file exists as a single callable function rather than
  just four `python x.py` lines in a shell script.
