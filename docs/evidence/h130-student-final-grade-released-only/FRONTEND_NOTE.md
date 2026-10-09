# H-130: note for the frontend

Rule behind the change (2026-10-06): **a student should not know a grade
exists before the teacher releases it.** No request or response field is
added, removed or renamed. Three values a student receives can now differ
from before.

## 1. `GET /student-course/` and `GET /student-course/<id>/`, read by a student
- `final_grade` and `grade_letter` now count RELEASED work only.
- While something is graded and not released, the student's values differ
  from the teacher's for the same enrolment. That is intended. Do not
  show a student a figure taken from a teacher's response.
- With nothing released, both are `null`, also when work has been graded.
- A teacher's response is unchanged.

## 2. A final grade set by hand
- A teacher can still `PATCH` `final_grade` on an enrolment. A student
  never receives that value now; they receive the released-work figure.
- This was fragile already: the next save or delete of one of that
  student's submissions in the course recalculates the column and
  overwrites the hand-set value. Whether a manual final grade should be a
  feature is a separate question, logged by the SM.

## 3. The answer document, read by a student
Fields: `raw_input` on `GET /submissions/<id>/` and
`student_submission_raw_input` on `GET /assignments/<id>/`.
- Until the grade is released, the document's header reads as it does for
  a newly submitted answer: "Graded At: Not graded yet" and an empty
  "Score:" line, whatever has happened to the submission since. After
  release it shows the grading date and the score, as before.
- Until release, the header's assignment title, due date and student name
  are today's values. If a teacher renames the assignment, the student's
  document shows the new title at once.
- A document stored by an older version of the builder is likewise read
  by the student in today's form until release, and in its stored form
  after it.
- A submission with no document yet (still being processed) returns
  `null` or an empty string, as before.
- A teacher's document is unchanged.

## Not in this change
The per-submission fields that change when grading happens (remaining
attempts, grading state, list filters, the "already been graded"
message) are handled in H-127 and a separate row.
