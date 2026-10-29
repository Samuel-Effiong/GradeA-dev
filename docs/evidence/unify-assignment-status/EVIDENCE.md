# Evidence — unify per-assignment student status logic

Commit: `2d009ff` on `task/unify-assignment-status` (base: `beta` at
`174be2d`), worktree `GAP-unify-assignment-status`. Not landed, not pushed.

## What was wrong

A student's assignment status (SUBMITTED / GRADED / OVERDUE / NOT SUBMITTED)
was computed independently in three places, and had drifted:

1. `assignments/serializers.py::AssignmentListStudentSerializer.get_status`
   used the label `"PENDING"` and had a real bug: it checked
   `submission and not submission.graded_at` for the SUBMITTED case, so a
   submission that was **graded but not yet released**
   (`is_published=False`) fell through to the OVERDUE/PENDING branch instead
   of SUBMITTED.
2. `classrooms/serializers.py::StudentCourseDetailSerializer.get_assignments`
   also used `"PENDING"`, but its condition order was already correct
   (`if not submission` first) — it did NOT have the bug.
3. `dashboard/views.py::StudentAdminDashboardView.assignments` already had
   both the correct order and the newer `"NOT SUBMITTED"` label — someone
   had already fixed this one site in isolation, which is exactly the kind
   of drift this unification exists to stop.

## Fix (this commit)

- New shared function `assignments/services.py::get_student_assignment_status(assignment, submission)`,
  returning exactly one of `"SUBMITTED"`, `"GRADED"`, `"OVERDUE"`,
  `"NOT SUBMITTED"`:
  - submission exists, `graded_at` set AND `is_published` → `GRADED`
  - submission exists, anything else (not graded yet, or graded but not
    released) → `SUBMITTED`
  - no submission, `due_date` in the past → `OVERDUE`
  - no submission, otherwise → `NOT SUBMITTED`

  Whether a submission **exists** is checked first, independent of publish
  state — that ordering is what fixes site 1's bug.
- All three call sites now call this one function for the status value.
  Each site's own score/grade-letter computation is untouched — only the
  status piece was unified:
  - `assignments/serializers.py::get_status` — the buggy branch is gone
    entirely; the method is now three lines.
  - `classrooms/serializers.py::get_assignments` — status comes from the
    shared function; the local `score` computation (which used
    `submission.score`, not `score_percentage`) is kept as its own small
    `if`/`else`.
  - `dashboard/views.py::assignments` — status comes from the shared
    function; the `released` variable (gating `score`/`score_percentage`/
    `feedback`) is untouched.

## BREAKING RESPONSE-VALUE CHANGE (intentional, per direct user instruction)

`AssignmentListStudentSerializer.get_status` (site 1) and
`StudentCourseDetailSerializer.get_assignments` (site 2) **used to return
`"PENDING"`** for a not-yet-submitted, not-yet-overdue assignment. They now
return **`"NOT SUBMITTED"`** (with the space), matching the string
`dashboard/views.py` (site 3) already shipped. This is a real, observable
change to those two endpoints' JSON response values, not an internal
refactor:

- `GET /assignments/` (student, list) — `results[].status`
- `GET /student-course/<pk>/` (student) — `assignments[].status`

Any frontend code that compares this field against the literal string
`"PENDING"` will stop matching and needs to be updated to `"NOT SUBMITTED"`.
`dashboard/views.py`'s endpoint (`GET /student-admin/dashboard/assignments/`)
is unaffected — it already returned `"NOT SUBMITTED"`.

Site 1 also now returns `"SUBMITTED"` instead of `"OVERDUE"`/`"PENDING"` for
the previously-buggy graded-but-unpublished case — a correctness fix, not
just a label change, and also a response-value change for anyone who was
relying on (or working around) the old buggy behavior.

## Tests

New module `assignments/tests_student_assignment_status.py` (19 tests):

- `TestSharedFunction` (7 tests): the four states directly against the
  shared function, including `test_graded_but_not_released_is_submitted_not_graded`
  (the case that used to be buggy) and
  `test_a_submission_always_beats_an_overdue_due_date` (due_date is only
  consulted in the no-submission branch).
- `TestAssignmentsSerializerCallSite` (4 tests): through the real
  `GET /assignments/` endpoint as a student, including
  `test_graded_but_not_released_is_submitted_not_overdue` — the previously
  buggy site, proven fixed end-to-end, not just at the unit level — and
  `test_no_submission_not_due_is_not_submitted` proving the new label.
- `TestClassroomsSerializerCallSite` (4 tests): through
  `GET /student-course/<pk>/` as a student, same four states plus the new
  label.
- `TestDashboardViewCallSite` (4 tests): through
  `GET /student-admin/dashboard/assignments/`, confirming this site's
  already-correct behavior is unchanged now that it goes through the shared
  function instead of its own inline copy.

`python manage.py test assignments.tests_student_assignment_status --settings=settings_worktree --noinput -v2`
- Ran 19 tests in 1.822s
- **OK**

## Mutation testing

Ran against `assignments/services.py::get_student_assignment_status`
directly (script + full log in this directory:
`mutation_results.json`/`mutation_log.jsonl`). 9 mutants, one per
branch/condition/return literal in the function: flipping the
submission-exists check, `and`→`or` and dropping a clause in the GRADED
condition, swapping each of the four return literals for a wrong one, and
flipping/dropping the due-date comparison and its None-guard.

Restored from a collision-safe backup file after every mutant, verified by
md5 each time (never `git checkout`) — confirmed clean via `git diff --stat`
showing only the intended 21-line addition after the run finished.

**KILLED 9 / 9.** `TestSharedFunction`'s own 7 tests, run alone
(`--keepdb`, isolated Postgres/Redis), caught every mutant — no mutant
needed a call-site test to be killed.

## Regression — touched apps

`python manage.py test assignments classrooms dashboard students --settings=settings_worktree --noinput -v1`
- Ran 1432 tests
- **OK (skipped=16)**

## Regression — full suite

Run via `scripts/isolated-test-env.sh` (private Postgres 16 + Redis), on
this worktree, branch `task/unify-assignment-status` off `beta` at
`174be2d`.

`python manage.py test --settings=settings_worktree --parallel 4 --noinput`
- Ran 4617 tests in 273.011s (~4.6 min)
- **OK (skipped=28)**
- 0 `FAIL`/`ERROR` lines anywhere in the log (grepped, not just the final
  summary line)

Tail of the full run: `full_regression_tail.txt`.

## Conclusion

The shared function replaces three drifted, independently-maintained copies
of the same logic with one, fixes the graded-but-unpublished bug in
`assignments/serializers.py`, and is covered directly (7 unit tests, 9/9
mutants killed) and at every call site (4 tests each, 12 total, including
the previously-buggy case proven fixed end-to-end). The `"PENDING"` →
`"NOT SUBMITTED"` string change is a deliberate, documented breaking
response-value change for two endpoints' frontend consumers — see the
section above, not an incidental side effect. No regressions anywhere in
the full suite. Not yet landed — reported to the Senior Manager for
independent re-verification before merging to beta.

## File hashes (post-commit, from the committed blob)

```
$ git show 2d009ff:<path> | sha256sum
assignments/services.py                        7516439365b06cdc6d6f6001564d35d72ca71b1ed8ebbe6fa7b69700a454a0ff
assignments/serializers.py                     e4717b0867a14f0718741fe4f601e933f912edc6013742b6295ae5f4e7357634
classrooms/serializers.py                      097895fc1e262ede47c8ea34d0e614c98afc25ea78bcbf98c61f6b33c5740337
dashboard/views.py                             6fc952670aaa38c1fdbb6bb7500ab00e9c66f76aa29d7a9ff34de2e2d2ac8e1b
assignments/tests_student_assignment_status.py 396ffa7d5b4fc8c02cc9f0bc53044b97b0344f009b5b6309d80e364fb3e5f991
```
