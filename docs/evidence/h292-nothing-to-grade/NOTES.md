# H-292: "Grade all" with nothing to grade is a coded refusal

Owner go 2026-11-05 ("yes" to: clear reason code + typo correction). Found
through the activity log: a teacher's POST assignment-grade-all logged as
STATE_CHANGE / USER / 400 / reason_label PARSE_ERROR. The view raised DRF
ParseError("No ungraded submissons to process") (assignments/views.py:1764)
for "nothing left to grade". SM does it. Branch `task/h292-nothing-to-grade`
from beta a3ca097d8. NO full suite; near runs only.

## Design
- audit.enums.ReasonCode.NOTHING_TO_GRADE (reason_code column is a plain
  CharField: no migration).
- REASON_CODES spec: USER, 400 (status unchanged), message "No ungraded
  submissions to process." (owner-approved typo correction; period per the
  catalogue's style), remediation "" (none, reaches the body as null),
  retryable False.
- The view raises CodedError(ReasonCode.NOTHING_TO_GRADE); the existing
  handler answers the coded body and H-273 records reason_code on the row.
- APPROVED_ADDITIONS in AutoGrader/tests_reason_codes.py gets the entry.

## Expectations (before the red run)
New module `assignments.tests_h292_nothing_to_grade` (5 tests), red on beta:
4 FAIL/ERROR (no-submissions, all-graded, audit row reason_code, catalogue
entry), 1 PASS (an ungraded submission still starts grading, 202).
After the fix: Ran 5 OK; `AutoGrader.tests_reason_codes` OK;
`students.tests_s6d_rubric_gate` OK.
Mutants: (1) view back to ParseError -> 3 tests fail; (2) catalogue message
typo restored -> catalogue + envelope tests fail.
Near run: `assignments audit AutoGrader.tests_reason_codes students.tests_s6d_rubric_gate`.
