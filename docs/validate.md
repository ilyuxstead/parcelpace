# `validate.py`

Checks a single consolidated driver-day payload against the current
schema before `route_inbox.py` is allowed to file it. This is the only
place schema rules live — `route_inbox.py` just asks "did this pass?"
and acts on the answer.

## Entry points

- `validate(payload)` — takes an already-parsed dict, returns a
  `ValidationResult`. This is what `route_inbox.py` calls.
- `validate_file(path)` — CLI/manual convenience wrapper: reads a file
  off disk, parses JSON, calls `validate()`. Returns
  `(ValidationResult, payload)`, where `payload` is `None` if the file
  couldn't even be parsed.
- Run directly (`python validate.py path/to/file.json`) for a manual
  spot-check — prints errors/warnings and exits non-zero if there were
  hard errors.

## `ValidationResult`

Two lists, `.errors` and `.warnings`, plus two convenience properties:

- `.ok` — no hard errors (warnings are fine). This is what
  `route_inbox.py` checks before moving a file.
- `.is_clean` — no errors *and* no warnings. Used to decide whether
  anything gets written to the driver's accumulating log at all
  (`route_inbox.py` stays silent on a fully clean submission).

**Errors block. Warnings don't.** This is the one rule that matters
most for understanding the rest of the file — see "Fault-tolerant over
blocking" in the project README for the reasoning.

## What's checked

**Top level:** `driver_id` (non-empty string), `date` (`YYYY-MM-DD`),
presence of `plan` and `hours` keys.

**`plan` block** (`_validate_plan`):
- `plan: null` → warning only ("driver hasn't submitted a day plan
  yet"), not an error.
- If a plan *is* submitted, all of `route_id`, `planned_stops`,
  `planned_miles`, `planned_pieces`, `entry_time` are required and
  type-checked.
- `route_id` present but blank (`""` or whitespace) → **hard error**.
  This is stricter than a missing plan: once a plan exists, a usable
  route code is non-negotiable because `aggregate.py`'s per-route
  trending has nothing to group on otherwise.
- `predicted_finish` — required key. Its value must be a `"HH:MM"`
  string; a `null` value, or anything else that isn't a valid
  `"HH:MM"` string, is a **hard error**. This used to be warning-only
  when `null`, but a predicted finish is never actually optional — the
  block itself, and the resulting log entry, now serve as evidence of
  the unfilled field for a human to review.
- `planned_miles == 0` → **hard error**, same reasoning as
  `predicted_finish`: zero planned miles is never a legitimate value,
  so it's tightened rather than left as a warning.

**`hours` block** (`_validate_hour_entry`, once per hour key):
- Hour keys must match `^([01]\d|2[0-3])$` (i.e. `"00"`–`"23"`).
- Each hour entry requires `hourly_stops`, `hourly_miles`,
  `hourly_pieces`, `hourly_pieces_picked_up`, `entry_time` (typed and
  present), plus `notes` (string), `break_flag` (bool), and
  `confirmed_zero` (bool) if present.
- Negative deltas on any of the four numeric fields (including
  `hourly_miles`, now that it's computed from a cumulative trip-meter
  reading the same way as the others) → warning, not an error,
  matching the web tool's own red-highlight-but-don't-block behavior
  for the same fields.
- All four numeric fields are zero **and** `break_flag` is `false`
  **and** `confirmed_zero` is not `true` → warning. The web tool now
  prompts the driver to explicitly confirm a genuine all-zero hour at
  save time (see `index.html`'s save flow), so this warning only fires
  on an hour that's genuinely unconfirmed — either the driver didn't
  confirm it, or the data predates the `confirmed_zero` field
  entirely, in which case it's treated as unconfirmed by default (same
  backfill-free pattern as the active-hour elapsed-time derivation in
  `aggregate.py`).

## Design notes worth knowing

- Comment-stripping preprocessing was deliberately **removed** — the
  web entry tool never produces comments in its JSON output, so that
  was dead code left over from an earlier, template-based era.
- `bool` is explicitly excluded from `int`/`float` type checks
  (`isinstance(True, int)` is `True` in Python, which would otherwise
  silently accept a boolean where a number was required).
- Validation runs against the file **as submitted, before it moves out
  of `inbox/`**. `route_inbox.py` only performs the move if
  `result.ok` is true; a hard-error file stays in `inbox/` for a human.