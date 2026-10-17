# Score printing, the guard and the per-shape tests: every assertion read (2026-10-08, BEFORE any run)

Ruling (Senior Manager, 2026-10-08): the manual-grade route keeps a small
guard as a SECOND line behind H-165's builder; each defence is shown by its
own tests.

| Test (students/tests_manual_grade_unreadable_answers.py) | Defence it proves | Inspects (form) | Red under |
|---|---|---|---|
| TheBuilderMakesTheGradeSaveForEveryShape: dict, list of strings, string (3 tests) | **the builder (H-165)**, REAL, through the route. They do NOT depend on the guard. | the shape is non-empty first; status 200; `row.score` Decimal 9; `row.raw_input` non-empty and different from the old document and containing "could not be displayed"; the answer's `raw_input` equals the stored one | on the OLD code (330f3efd, no H-165): all three (the builder raises, 500). Not red under M9 (guard removed): the real builder does not raise. |
| TheGuardKeeps...: the grade is saved and the old document left as it was | **the guard**, builder REPLACED by one that raises RuntimeError | 200; score 9; `was_regraded` true; `raw_input` equals the old marker string | M9 |
| ...the answer does not claim a refresh | the guard | the answer's `raw_input` equals the old marker | M9 |
| ...the fault is logged with the id and the type and nothing else | the guard | `assertLogs` output joined (non-empty first); the submission id and "RuntimeError" in it; the fault's own message text, the student's full name and e-mail NOT in it (each decided: the message is in the raised error, the name and e-mail are on the row) | M9 |
| ...a fault in the save is not swallowed | the guard is around the BUILD only | status >= 500 and the stored score still 7 | M10 (a guard that also covers the save) |

Said plainly: the document is LEFT AS IT WAS on a builder fault, not cleared.
Why: it is the paper the student reads; a stale printed score is a lesser harm
than a paper that vanishes, and the fault is seen in the log. H-142 is then
not cured for a paper whose document cannot be rebuilt, which H-165's builder
makes unreachable for any shape of `answers` today.

The "no mutant decides" list: the three-shape tests cannot tell a guarded
route from an unguarded one (they are meant not to). The old test
`students.tests.StudentSubmissionGradeUpdateTest.test_teacher_can_update_grade`
(answers stored as a dict) stays as it is, in the chain's modules step.
