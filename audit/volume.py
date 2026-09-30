"""Epic A S8 (plan 08 §8.1): the rates `audit_volume_report` projects from.

Every number here was MEASURED by the S8 harness (`audit/bench_volume.py`, run
by label on the test database), which drives the real routes and tasks
through a busy school day - see docs/evidence/epic-a-s8/EVIDENCE.md for the
run. Re-run the harness and update this file whenever a slice adds or
removes events; it is a snapshot, not a model.

"A busy school day" is deliberately an upper bound: each active teacher
imports a roster of 30, batch-uploads 30 answer sheets, grades all 30,
publishes all 30 and edits 3 grades; each active student signs in once.
A real day for most teachers is a fraction of this.
"""

from .enums import STUDENT_RECORD_ACTIONS, RetentionClass

# Events one active teacher's busy school day writes, per action.
PER_TEACHER_DAY: dict[str, float] = {}

# Events one active student's school day writes, per action (a sign-in;
# viewing writes nothing).
PER_STUDENT_DAY: dict[str, float] = {}

# Events the system writes per day whatever the school size (Beat).
SYSTEM_PER_DAY: dict[str, float] = {}

# Bytes per row (pg_column_size) and index-to-heap ratio from the harness's
# table, used when the database the report runs against has no rows yet.
HARNESS_BYTES_PER_ROW = 0.0
HARNESS_INDEX_RATIO = 0.0

# How long each retention class is kept (audit.tasks.sweep_audit_retention).
RETENTION_DAYS = {
    RetentionClass.GENERAL.value: 365,
    RetentionClass.STUDENT_RECORD.value: 365 * 3,
}


def retention_class_of(action) -> str:
    """The class an action's events are kept under. An event can be raised
    to STUDENT_RECORD one by one (`touches_student_record`); this is the
    action's floor, so where that happens the projection under-counts
    three-year rows. The harness reports its measured class split beside
    the rates (EVIDENCE), so the gap is visible."""
    if action in STUDENT_RECORD_ACTIONS:
        return RetentionClass.STUDENT_RECORD.value
    return RetentionClass.GENERAL.value
