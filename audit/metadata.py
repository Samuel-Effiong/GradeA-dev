"""What may be written into an audit event's `metadata`, `before` and `after`.

FR-A-04 / NFR-CMP-02: no student name, email or answer text in any log. The
data model requires that be "enforced by the emitter's allow-list, not by
convention", so this module is that allow-list.

Only these keys, only identifiers, counters, codes and short labels as values.
No nesting, no long strings, nothing shaped like an email address. To record a
new kind of fact, add its key HERE, in a reviewed change: the allow-list is the
review point. `tests_metadata.py` refuses any key that names a person or their
work (name, email, answer, text, title ...).

`ALLOWED_KEYS` is the full pool a key must belong to before it is even
considered - `sanitise()` checks a value against it and against the generic
shape rules (size, nesting, email-shaped strings) below, independent of which
action produced it. `METADATA_ALLOWLIST` (bottom of this file) is a second,
narrower gate: per FR-A-02 / 03a_data_model.md 2.1, `metadata` is scoped to
"the emitter's allow-list" PER ACTION, not just any key from the shared pool -
a key that is fine for one action (e.g. `credits` on a credit transaction) is
not automatically fine on an unrelated one. `emitter.py` applies both: a key
must be in `ALLOWED_KEYS` (this file's shape/PII rules) AND in the calling
action's own `METADATA_ALLOWLIST` entry.
"""

import json
import math
import re
from collections.abc import Mapping

from .enums import AuditAction

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
        "lock_triggered",
        "model",
        "prompt_version",
        "grading_config_version",
        "strictness",
        "feature",
        "task_type",
        "file_type",
        "source",
        "changed_fields",
        # S1 generic event: the URL name and HTTP method, never the path or body
        "route",
        "method",
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


# ---------------------------------------------------------------------------
# Per-action metadata allow-list (03a_data_model.md 2.1: "bounded, PII-free
# context, enforced by the emitter's allow-list"). Every key here must also be
# in ALLOWED_KEYS above - `tests_metadata.py` checks that, so a key cannot be
# opened up for one action without first passing the generic shape/PII rules.
#
# An action absent from this dict, or mapped to an empty set, accepts no
# metadata at all - the safe default for an action with no call site yet
# (Lesson/Tag/Department/Library, Epics C/D/F). Widen an entry only when the
# call site that needs the extra key is actually being built, in the same
# reviewed change - not ahead of time "in case it's useful".
METADATA_ALLOWLIST = {
    # lock_triggered: the password-reset guess that spent the budget and set
    # the lock (L2), recorded as the failure it was plus this flag.
    AuditAction.AUTH_LOGIN: frozenset({"auth_method", "http_status", "lock_triggered"}),
    AuditAction.AUTH_LOGOUT: frozenset(),
    AuditAction.GRADING_REQUESTED: frozenset(
        {"assignment_id", "submission_id", "task_id", "task_type"}
    ),
    AuditAction.GRADING_COMPLETED: frozenset(
        {
            "assignment_id",
            "submission_id",
            "task_id",
            "duration_ms",
            "model",
            "prompt_version",
            "grading_config_version",
            "strictness",
        }
    ),
    AuditAction.GRADING_FAILED: frozenset(
        {
            "assignment_id",
            "submission_id",
            "task_id",
            "attempt",
            "model",
            "feature",
            "http_status",
        }
    ),
    AuditAction.ASSIGNMENT_CREATE: frozenset({"course_id", "session_id"}),
    AuditAction.ASSIGNMENT_UPDATE: frozenset({"course_id", "changed_fields"}),
    AuditAction.ASSIGNMENT_DELETE: frozenset({"course_id"}),
    AuditAction.ASSIGNMENT_COPY: frozenset(
        {"course_id", "session_id", "source", "item_count"}
    ),
    AuditAction.LESSON_CREATE: frozenset(),
    AuditAction.LESSON_UPDATE: frozenset(),
    AuditAction.LESSON_DELETE: frozenset(),
    AuditAction.TAG_CREATE: frozenset(),
    AuditAction.TAG_RENAME: frozenset(),
    AuditAction.TAG_DELETE: frozenset(),
    AuditAction.ROSTER_CHANGE: frozenset(
        {"course_id", "item_count", "succeeded_count", "failed_count", "student_id"}
    ),
    AuditAction.SUBMISSION_UPLOAD: frozenset(
        {"assignment_id", "file_type", "file_size_bytes", "file_count"}
    ),
    AuditAction.CREDIT_TRANSACTION: frozenset(
        {"credits", "credits_estimated", "credits_actual", "ledger_type"}
    ),
    AuditAction.DEPARTMENT_CREATE: frozenset(),
    AuditAction.DEPARTMENT_UPDATE: frozenset(),
    AuditAction.DEPARTMENT_DELETE: frozenset(),
    AuditAction.DEPARTMENT_MEMBER_ADD: frozenset(),
    AuditAction.DEPARTMENT_MEMBER_REMOVE: frozenset(),
    AuditAction.LIBRARY_ADD: frozenset(),
    AuditAction.LIBRARY_EDIT: frozenset(),
    AuditAction.LIBRARY_COPY: frozenset(),
    AuditAction.ADMIN_ACTION: frozenset({"source"}),
    AuditAction.DATA_EXPORT: frozenset({"file_count", "file_size_bytes"}),
    AuditAction.PERMISSION_CHANGE: frozenset({"changed_fields"}),
    AuditAction.STATE_CHANGE: frozenset({"route", "method", "http_status"}),
}


def metadata_allowlist_for(action) -> frozenset:
    """The metadata keys `action` may carry. Unknown/unmapped actions get
    none - see the module note above."""
    return METADATA_ALLOWLIST.get(action, frozenset())


def sanitise_metadata_for_action(action, value):
    """Like `sanitise`, plus the per-action narrowing this module exists to
    enforce. A key that passes the generic `ALLOWED_KEYS` shape/PII rules but
    is not in this action's own allow-list is still dropped and reported -
    the per-action gate is a restriction on top of `sanitise`, never a way
    to admit a key `sanitise` would otherwise refuse.
    """
    clean, problems = sanitise(value)
    if not clean:
        return clean, problems
    permitted = metadata_allowlist_for(action)
    narrowed = {}
    for key, item in clean.items():
        if key in permitted:
            narrowed[key] = item
        else:
            problems.append((key, "not allowed for this action"))
    return narrowed, problems
