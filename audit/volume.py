"""Epic A S8 (plan 08 §8.1): the rates `audit_volume_report` projects from.

Every number here was MEASURED by the S8 harness (`audit/tests/test_bench_volume.py`, run
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

# Events one active teacher's busy school day writes, per action. Measured
# by the harness on 2026-09-30 at task/epic-a-s8 (phase2/epic-a fda47d7):
#   roster import of 30      -> 31 ROSTER_CHANGE (30 enrolments + 1 aggregate)
#   batch upload of 30       ->  1 SUBMISSION_UPLOAD (one per batch)
#   grade-all of 30          -> 30 GRADING_REQUESTED
#   the 30 grading runs      -> 30 GRADING_COMPLETED
#   publish-all of 30        -> 30 GRADE_CHANGE
#   3 grade edits            ->  3 GRADE_CHANGE
# DERIVED, not measured: CREDIT_TRANSACTION. The harness patches the AI call
# (`execute_graded_task`), which is also where a grading consumes credits,
# so it records none; in production each grading writes one CONSUME per
# bucket it draws from (usually one).
PER_TEACHER_DAY: dict[str, float] = {
    "ROSTER_CHANGE": 31,
    "SUBMISSION_UPLOAD": 1,
    "GRADING_REQUESTED": 30,
    "GRADING_COMPLETED": 30,
    "GRADE_CHANGE": 33,
    "CREDIT_TRANSACTION": 30,  # derived: one CONSUME per grading
}

# Events one active student's school day writes, per action: one sign-in
# (measured); viewing writes nothing.
PER_STUDENT_DAY: dict[str, float] = {"AUTH_LOGIN": 1}

# Events the system writes per day whatever the school size: the two audit
# sweeps' self-records (measured).
SYSTEM_PER_DAY: dict[str, float] = {"AUDIT_RETENTION_SWEEP": 2}

# Bytes per row (pg_column_size, the harness's 129 rows) and the index-to-heap
# ratio, used when the database the report runs against has no rows of its
# own. The ratio is measured on a tiny table, where fixed per-index pages
# dominate, so it overstates a large table's; the report uses the real
# table's own ratio whenever it has rows.
HARNESS_BYTES_PER_ROW = 485.0
HARNESS_INDEX_RATIO = 1.75

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
