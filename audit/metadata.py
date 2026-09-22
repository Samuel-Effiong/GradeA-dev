"""What may be written into an audit event's `metadata`, `before` and `after`.

FR-A-04 / NFR-CMP-02: no student name, email or answer text in any log. The
data model requires that be "enforced by the emitter's allow-list, not by
convention", so this module is that allow-list.

Only these keys, only identifiers, counters, codes and short labels as values.
No nesting, no long strings, nothing shaped like an email address. To record a
new kind of fact, add its key HERE, in a reviewed change: the allow-list is the
review point. `tests_metadata.py` refuses any key that names a person or their
work (name, email, answer, text, title ...).
"""

import json
import math
import re
from collections.abc import Mapping

ALLOWED_KEYS = frozenset(
    {
        # how many
        "item_count",
        "succeeded_count",
        "failed_count",
        "skipped_count",
        "file_count",
        "page_count",
        "attempt",
        "count",
        # how big / how long
        "file_size_bytes",
        "duration_ms",
        # credits
        "credits",
        "credits_estimated",
        "credits_actual",
        "ledger_type",
        # identifiers of the things involved (ids, never names)
        "batch_id",
        "job_id",
        "task_id",
        "assignment_id",
        "course_id",
        "lesson_id",
        "tag_id",
        "session_id",
        "submission_id",
        "student_id",
        # short labels and versions
        "http_status",
        "auth_method",
        "model",
        "prompt_version",
        "grading_config_version",
        "strictness",
        "feature",
        "task_type",
        "file_type",
        "source",
        "changed_fields",
    }
)

MAX_KEYS = 20
MAX_STRING = 128
MAX_LIST_ITEMS = 50
MAX_ITEM_STRING = 64
MAX_BYTES = 2048

_SAFE_KEY = re.compile(r"[a-z][a-z0-9_]{0,39}")
_EMAIL_SHAPED = re.compile(r"\S+@\S+")


def _report_key(key) -> str:
    """A key name that is safe to write to an operational log. A key is chosen
    by the calling code, so a careless caller can make it a person's name."""
    if isinstance(key, str) and _SAFE_KEY.fullmatch(key):
        return key
    return "<invalid-key>"


def _scalar_problem(value, limit):
    """Why `value` may not be stored as a scalar, or None if it may."""
    if value is None or isinstance(value, (bool, int)):
        return None
    if isinstance(value, float):
        return None if math.isfinite(value) else "not a finite number"
    if isinstance(value, str):
        if len(value) > limit:
            return "string too long"
        if "\x00" in value:
            return "string contains a NUL byte"
        if _EMAIL_SHAPED.search(value):
            return "looks like an email address"
        return None
    return "value is not a scalar or short list"


def _value_problem(value):
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_LIST_ITEMS:
            return "list too long"
        for item in value:
            problem = _scalar_problem(item, MAX_ITEM_STRING)
            if problem:
                return problem
        return None
    return _scalar_problem(value, MAX_STRING)


def sanitise(value):
    """Return `(clean, problems)`.

    `clean` holds only what is allowed; `problems` is a list of
    `(key, reason)` for everything that was dropped, with key names made safe
    to log. The input is never modified. Anything wrong with the whole object
    empties it: a partial write of an over-size or malformed object would
    record something nobody reviewed.
    """
    if value is None:
        return {}, []
    if not isinstance(value, Mapping):
        return {}, [("*", "not an object")]
    if len(value) > MAX_KEYS:
        return {}, [("*", "too many keys")]

    clean, problems = {}, []
    for key, item in value.items():
        if not isinstance(key, str) or key not in ALLOWED_KEYS:
            problems.append((_report_key(key), "not on the allow-list"))
            continue
        problem = _value_problem(item)
        if problem:
            problems.append((key, problem))
            continue
        clean[key] = list(item) if isinstance(item, tuple) else item

    if len(json.dumps(clean, separators=(",", ":")).encode("utf-8")) > MAX_BYTES:
        return {}, [("*", "too large")]
    return clean, problems
