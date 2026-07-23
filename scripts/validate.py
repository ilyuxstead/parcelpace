"""
validate.py

Validates a single consolidated driver-day payload against the current
parcelpace schema:

{
  "driver_id": str,
  "date": "YYYY-MM-DD",
  "plan": {
      "route_id": str,
      "planned_stops": int,
      "planned_miles": number,
      "planned_pieces": int,
      "predicted_finish": "HH:MM",
      "entry_time": ISO8601 str
  } | null,
  "hours": {
      "13": {
          "hourly_stops": int,
          "hourly_miles": number,
          "hourly_pieces": int,
          "hourly_pieces_picked_up": int,
          "notes": str,
          "break_flag": bool,
          "entry_time": ISO8601 str
      },
      ...
  }
}

validate() is the importable entry point. A thin CLI wrapper is provided
for standalone/manual checks against a file on disk.

Design notes (see project memory for fuller context):
- Comment-stripping preprocessing is intentionally NOT present here -- the
  web entry tool never introduces comments, so that old template-era
  workaround is dead code and has been dropped.
- Validation runs BEFORE a file is moved out of inbox/, against the loose
  file as submitted. route_inbox.py calls validate() and only moves the
  file if there are no hard errors.
- Hard errors block the move. Warnings do not -- they're logged but the
  file still proceeds, consistent with the project's general
  fault-tolerant-over-blocking philosophy (e.g. negative deltas are
  flagged, not rejected). plan.predicted_finish == null and
  plan.planned_miles == 0 are a deliberate exception to that general
  stance: neither value is ever legitimate, so both are hard errors
  rather than warnings -- the block itself, and the resulting log entry,
  serve as evidence of the unfilled field for a human to review.
"""

import json
import re
import sys

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^\d{2}:\d{2}$")
HOUR_KEY_RE = re.compile(r"^([01]\d|2[0-3])$")  # "00".."23"

REQUIRED_HOUR_FIELDS = {
    "hourly_stops": (int,),
    "hourly_miles": (int, float),
    "hourly_pieces": (int,),
    "hourly_pieces_picked_up": (int,),
    "entry_time": (str,),
}

REQUIRED_PLAN_FIELDS = {
    "route_id": (str,),
    "planned_stops": (int,),
    "planned_miles": (int, float),
    "planned_pieces": (int,),
    "entry_time": (str,),
}


class ValidationResult(object):
    """Container for errors/warnings from a single validate() call."""

    def __init__(self):
        self.errors = []
        self.warnings = []

    def add_error(self, msg):
        self.errors.append(msg)

    def add_warning(self, msg):
        self.warnings.append(msg)

    @property
    def ok(self):
        """True if there are no hard errors (warnings are fine)."""
        return len(self.errors) == 0

    @property
    def is_clean(self):
        """True only if there are neither errors nor warnings at all."""
        return not self.errors and not self.warnings

    def to_dict(self):
        return {"errors": self.errors, "warnings": self.warnings}


def _check_type(value, allowed_types, field_label, result):
    """Helper: record an error if value isn't one of allowed_types."""
    if not isinstance(value, allowed_types) or isinstance(value, bool):
        # isinstance(True, int) is True in Python, so explicitly exclude
        # bool from numeric/int checks to avoid false positives.
        result.add_error(
            "{} has wrong type (got {})".format(field_label, type(value).__name__)
        )
        return False
    return True


def _validate_plan(plan, result):
    if plan is None:
        result.add_warning("plan is null -- driver has not submitted a day plan yet")
        return

    if not isinstance(plan, dict):
        result.add_error("plan must be an object or null")
        return

    for field, types in REQUIRED_PLAN_FIELDS.items():
        if field not in plan:
            result.add_error("plan missing required field '{}'".format(field))
            continue
        _check_type(plan[field], types, "plan.{}".format(field), result)

    # route_id must be non-blank once a plan exists -- needed to group
    # same-route days for the trending average. plan:null itself stays a
    # warning (see above); this only fires when a plan was submitted.
    if "route_id" in plan and isinstance(plan["route_id"], str) and not plan["route_id"].strip():
        result.add_error("plan.route_id is present but blank -- a route code is required")

    # predicted_finish: null used to be accepted (warning-only) but is
    # never actually legitimate -- a driver always has a predicted finish
    # in mind. Tightened to a hard error so it blocks the file and lands
    # in the driver's log as evidence, rather than silently passing through.
    if "predicted_finish" not in plan:
        result.add_error("plan missing required field 'predicted_finish'")
    else:
        pf = plan["predicted_finish"]
        if pf is None:
            result.add_error("plan.predicted_finish is null -- a predicted finish time is required")
        elif not isinstance(pf, str) or not TIME_RE.match(pf):
            result.add_error("plan.predicted_finish must be 'HH:MM'")

    # planned_miles == 0 was previously warning-only but is never a
    # legitimate value -- tightened to a hard error, same reasoning as
    # predicted_finish above.
    if isinstance(plan.get("planned_miles"), (int, float)) and not isinstance(
        plan.get("planned_miles"), bool
    ):
        if plan["planned_miles"] == 0:
            result.add_error("plan.planned_miles is 0 -- planned miles must be greater than 0")


def _validate_hour_entry(hour_key, entry, result):
    label_prefix = "hours[{}]".format(hour_key)

    if not isinstance(entry, dict):
        result.add_error("{} must be an object".format(label_prefix))
        return

    for field, types in REQUIRED_HOUR_FIELDS.items():
        if field not in entry:
            result.add_error("{} missing required field '{}'".format(label_prefix, field))
            continue
        _check_type(entry[field], types, "{}.{}".format(label_prefix, field), result)

    if "notes" in entry and not isinstance(entry["notes"], str):
        result.add_error("{}.notes must be a string".format(label_prefix))

    if "break_flag" in entry and not isinstance(entry["break_flag"], bool):
        result.add_error("{}.break_flag must be a boolean".format(label_prefix))

    if "confirmed_zero" in entry and not isinstance(entry["confirmed_zero"], bool):
        result.add_error("{}.confirmed_zero must be a boolean".format(label_prefix))

    # Negative deltas: flagged, not blocked -- consistent with the HTML
    # tool's own red-highlight-but-don't-block behavior for these fields.
    # hourly_miles is included alongside the others now that it's computed
    # from a cumulative trip-meter reading in the same way as
    # stops/pieces/pieces_picked_up, rather than hand-entered per hour.
    for delta_field in ("hourly_stops", "hourly_pieces", "hourly_pieces_picked_up", "hourly_miles"):
        val = entry.get(delta_field)
        if isinstance(val, (int, float)) and not isinstance(val, bool) and val < 0:
            result.add_warning("{}.{} is negative ({})".format(label_prefix, delta_field, val))

    # All-zero hour with break_flag false: ambiguous on its own -- could be
    # a legitimate zero-activity hour, or a forgotten/unedited entry. The
    # web tool now resolves that ambiguity at save time by requiring the
    # driver to explicitly confirm a genuine all-zero hour (see
    # confirmed_zero handling in index.html's save flow), so only an
    # UNCONFIRMED all-zero hour is actually ambiguous here.
    numeric_fields = ("hourly_stops", "hourly_miles", "hourly_pieces", "hourly_pieces_picked_up")
    all_zero = all(
        isinstance(entry.get(f), (int, float))
        and not isinstance(entry.get(f), bool)
        and entry.get(f) == 0
        for f in numeric_fields
    )
    if all_zero and entry.get("break_flag") is False and entry.get("confirmed_zero") is not True:
        result.add_warning(
            "{} is all-zero with break_flag=false and not confirmed -- could "
            "be legitimate zero activity or an unedited/forgotten entry".format(label_prefix)
        )


def validate(payload):
    """
    Validate a parsed driver-day payload (already loaded from JSON).

    Returns a ValidationResult with .errors and .warnings lists.
    Does not raise on malformed data -- malformed data IS the thing being
    reported, via result.errors.
    """
    result = ValidationResult()

    if not isinstance(payload, dict):
        result.add_error("payload must be a JSON object")
        return result

    driver_id = payload.get("driver_id")
    if not driver_id or not isinstance(driver_id, str):
        result.add_error("driver_id is missing or not a non-empty string")

    date = payload.get("date")
    if not date or not isinstance(date, str) or not DATE_RE.match(date):
        result.add_error("date is missing or not in YYYY-MM-DD format")

    if "plan" not in payload:
        result.add_error("payload missing 'plan' key (use null if no plan submitted)")
    else:
        _validate_plan(payload["plan"], result)

    hours = payload.get("hours")
    if hours is None:
        result.add_error("payload missing 'hours' key")
    elif not isinstance(hours, dict):
        result.add_error("'hours' must be an object/dict keyed by hour string")
    else:
        for hour_key, entry in hours.items():
            if not HOUR_KEY_RE.match(str(hour_key)):
                result.add_error("invalid hour key '{}' (must be '00'-'23')".format(hour_key))
                continue
            _validate_hour_entry(hour_key, entry, result)

    return result


def validate_file(path):
    """
    Load and validate a JSON file from disk. Returns (ValidationResult, payload)
    where payload is None if the file couldn't even be parsed as JSON.
    """
    result = ValidationResult()
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError as e:
        result.add_error("could not read file: {}".format(e))
        return result, None

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        result.add_error("file is not valid JSON: {}".format(e))
        return result, None

    inner_result = validate(payload)
    result.errors.extend(inner_result.errors)
    result.warnings.extend(inner_result.warnings)
    return result, payload


def _cli():
    if len(sys.argv) != 2:
        print("usage: python validate.py <path-to-json-file>")
        sys.exit(2)

    path = sys.argv[1]
    result, _ = validate_file(path)

    if result.errors:
        print("ERRORS:")
        for e in result.errors:
            print("  - {}".format(e))
    if result.warnings:
        print("WARNINGS:")
        for w in result.warnings:
            print("  - {}".format(w))
    if result.is_clean:
        print("OK -- no errors or warnings.")

    sys.exit(0 if result.ok else 1)


if __name__ == "__main__":
    _cli()
