"""
run_pipeline.py

Single entry point that chains the full Dropstats pipeline in the order
it's meant to run:

    route_inbox.py -> aggregate.py -> visualize.py -> trends.py

Each stage is unchanged and still fully owns its own logic -- this is
just an orchestrator that calls each module's existing run()/
process_inbox() entry point in sequence, exactly as if you'd run all
four scripts by hand back to back. All four keep their existing
"full rebuild every run" behavior; this doesn't add any incremental
logic.

Blocked files intentionally do NOT stop the pipeline:
route_inbox.py leaves hard-error files sitting in inbox/ for a human, but
aggregate.py/visualize.py/trends.py only ever read from the already-
organized yyyy/mm/dd/ tree, rollups/, and overall/ (aggregate.py also
reads the accumulating log files, but only for data_quality counts).
None of those later stages touch the still-pending inbox/ files, so a
blocked driver has no bearing on whether everyone else's stats can be
correctly rebuilt -- halting the whole run over it would just delay
rollups/charts for every other driver. Blocked files are still reported
in the final summary and the process exits non-zero if any exist, so
cron/CI output still surfaces that something needs a human look, without
requiring one on every run.
"""

import sys

import aggregate
import route_inbox
import trends
import visualize

REPO_ROOT = "."


def _stage_header(n, total, name):
    print()
    print("=" * 60)
    print("STAGE {}/{}: {}".format(n, total, name))
    print("=" * 60)


def run(repo_root=REPO_ROOT):
    _stage_header(1, 4, "route_inbox")
    inbox_summary = route_inbox.process_inbox(repo_root=repo_root)

    _stage_header(2, 4, "aggregate")
    aggregate.run(repo_root=repo_root)

    _stage_header(3, 4, "visualize")
    visualize.run(repo_root=repo_root)

    _stage_header(4, 4, "trends")
    trends.run(repo_root=repo_root)

    print()
    print("=" * 60)
    print("PIPELINE SUMMARY")
    print("=" * 60)
    print("inbox: {} moved, {} blocked".format(
        len(inbox_summary["moved"]), len(inbox_summary["blocked"])
    ))
    if inbox_summary["blocked"]:
        print("  blocked driver_id(s): {}".format(", ".join(inbox_summary["blocked"])))
        print("  (left in inbox/ for review -- rollups/charts were still rebuilt "
              "from everything else that filed cleanly)")

    return inbox_summary


def _cli():
    repo_root = sys.argv[1] if len(sys.argv) > 1 else REPO_ROOT
    inbox_summary = run(repo_root=repo_root)
    sys.exit(1 if inbox_summary["blocked"] else 0)


if __name__ == "__main__":
    _cli()
