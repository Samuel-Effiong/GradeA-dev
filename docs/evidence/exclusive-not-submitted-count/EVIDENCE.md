# Evidence — mutually exclusive dashboard assignment-status tiles

Commit: `7246b65` on `task/exclusive-not-submitted-count` (base: `beta` at
`969dc89`), worktree `GAP-exclusive-not-submitted`. Not landed, not pushed.

## What was wrong

The user noticed the dashboard tiles (Submitted / Graded / Not Submitted /
Overdue) don't sum to the total assignment count. Root cause: `"Not
Submitted"` was defined as "everything with no submission" — which
**includes** overdue assignments — while `"Overdue"` was a separate,
overlapping view over that same "no submission" set, not a partition of it.
This was confirmed to the user as an unintended overlap, not an
intentional design choice, since the per-row status
(`assignments/services.py::get_student_assignment_status`, from the
previous unification task) already treats `OVERDUE` and `NOT_SUBMITTED` as
mutually exclusive alternatives for a single assignment — a reader
naturally expects the aggregate tiles to agree with that.

## Scope decision made mid-task, with the Senior Manager (recorded here for
the record, since it changes what the sum-equality proof below actually
asserts)

The task as scoped asked for a cross-check that `Not Submitted + Overdue +
Submitted + Graded == total assignment count`. Before implementing that,
found that `assignments_graded` is a **subset** of `assignments_submitted`,
not exclusive from it — a graded-and-released submission is counted in
BOTH. This is proven by an existing pinned regression test
(`dashboard/tests.py::test_assignments_graded_only_counts_released_scored_submissions`,
comment: *"Submitted/not-submitted/overdue counts are unaffected by
release"*), which asserts `assignments_submitted` does NOT drop when a
submission is released and counted as `assignments_graded`.

Flagged this to the Senior Manager before proceeding rather than guessing:
redefining `assignments_submitted` to exclude graded ones would be a
second, bigger, unrequested API contract change, and would require
rewriting that pinned test's intentional assertion. Decision: leave
`assignments_submitted` exactly as it is (Graded is an informational
subset/annotation on Submitted, not a competing partition member), and
scale the cross-check back to:

```
Not Submitted + Overdue + Submitted == total assignment count
```

with Graded reported separately, excluded from the sum. Verified against
the user's own screenshot numbers: 1 (not submitted) + 2 (overdue) + 17
(submitted, unchanged) = 20 = total; Graded = 6 stays a separate figure.

## Fix (this commit)

- `dashboard/views.py::_assignment_status_counts` — shared by
  `StudentAdminDashboardView.overview` (main dashboard tiles) and
  `.status_summary` (the standalone four-count endpoint), so this one
  change automatically covers both call sites:

  ```python
  assignments_due_no_submission = pending_assignments.filter(due_date__lt=now).count()
  assignments_not_submitted = pending_assignments.exclude(due_date__lt=now).count()
  ```

  (previously `assignments_not_submitted = pending_assignments.count()`,
  which included the overdue ones).

- `StudentAdminDashboardView.summary` (the per-course page) had a **third**,
  independent copy of this same logic (`not_submitted_count` from
  `missing_assignments.count()`, `overdue_count` as a separate overlapping
  filter on the same queryset) — not going through `_assignment_status_counts`
  at all. This is the exact "same logic computed independently and
  drifting" pattern the per-assignment status unification task just
  eliminated for the per-row case, so per the Senior Manager's instruction
  this endpoint was also refactored to call
  `_assignment_status_counts(student, assignments, now)` instead of
  hand-rolling its own version. Field names in the response are unchanged
  (`assignment_submitted`/`assignment_not_submitted`/`assignment_graded`/
  `missing_or_overdue`) — only how three of the four values are computed
  changed; they now read from the shared helper's dict, mapped to this
  endpoint's differently-named fields. `assignment_graded` now also reads
  from the shared helper instead of `released.count()` (same value, one
  source of truth). The `released` queryset itself (average grade, trend,
  best/worst assignments) is untouched — those computations don't go
  through the shared helper, only the four status counts do.

## BREAKING RESPONSE-VALUE CHANGE (intentional, per direct user instruction
via the Senior Manager)

`assignments_not_submitted` (on `GET /student-admin/dashboard/overview/`
and `GET /student-admin/dashboard/status-summary/`) and
`assignment_not_submitted` (on `GET /student-admin/dashboard/summary/<course_id>/`)
now return a **smaller number** wherever an overdue assignment previously
inflated the not-submitted count — that assignment moves to being counted
only under Overdue/`missing_or_overdue`. Any frontend code that summed
`Not Submitted + Overdue` expecting double-counting, or that displayed
`Not Submitted` alone as "everything still owed regardless of due date,"
will see a different (smaller, more correct) number. `assignments_submitted`/
`assignment_submitted` and `assignments_graded`/`assignment_graded` are
**unchanged** in both value and definition — see the scope decision above.

## Tests

Five existing pinned tests in `dashboard/tests.py` updated to the new
exclusive counts (`test_student_dashboard_overview_analytics`,
`test_assignments_graded_only_counts_released_scored_submissions`,
`test_status_summary_without_course_matches_overview`,
`test_status_summary_scoped_to_one_course`,
`test_course_summary_breaks_out_submitted_not_submitted_graded_overdue`) —
each fixture's assignment set and total count is unchanged; only the
expected `not_submitted`/`assignment_not_submitted` value changed, since it
no longer includes the assignment(s) already counted as overdue.

Two new tests prove the scaled-back sum-equality, end-to-end through the
real API (not just against `_assignment_status_counts` directly):

- `StudentDashboardOverviewAPITest.test_tiles_sum_to_total_assignment_count`
  — checks `GET .../overview/` and `GET .../status-summary/` both satisfy
  `Submitted + Not Submitted + Overdue == 5` (the fixture's 5 active-course
  assignments), then releases one submission and re-checks the same
  equality holds with `Graded == 1` reported separately (proving a graded
  submission does not throw the sum off, since it stays counted under
  Submitted rather than moving out of it).
- `StudentCourseSummaryAPITest.test_tiles_sum_to_total_assignment_count` —
  the same check against `GET .../summary/<course_id>/`'s differently-named
  fields (`assignment_submitted` + `assignment_not_submitted` +
  `missing_or_overdue` == `assignment_assigned`).

Per-row status (`assignments/serializers.py`, `classrooms/serializers.py`,
`dashboard/views.py`'s per-assignment list, all built on the shared
`get_student_assignment_status` from the prior task) is unaffected by this
change and was not touched — confirmed by re-running
`assignments.tests_student_assignment_status` (19 tests) unmodified against
this tree: still `OK`. That module already asserts a per-row `OVERDUE`
result never shows as `NOT SUBMITTED`, which is the per-row version of the
same exclusivity this task adds at the aggregate level.

`python manage.py test dashboard.tests dashboard.tests_dashboard_audit_fixes --settings=settings_worktree --noinput -v1`
- Ran 109 tests
- **OK**

## Regression — touched apps

`python manage.py test dashboard assignments classrooms students --settings=settings_worktree --noinput -v1`
- Ran 1438 tests
- **OK (skipped=16)**

## Regression — full suite

Run via `scripts/isolated-test-env.sh` (private Postgres 16 + Redis), on
this worktree, branch `task/exclusive-not-submitted-count` off `beta` at
`969dc89`.

`python manage.py test --settings=settings_worktree --parallel 4 --noinput`
- Ran 4623 tests in 512.791s (~8.5 min)
- **OK (skipped=28)**
- 0 `FAIL`/`ERROR` lines anywhere in the log (grepped, not just the final
  summary line)

Tail of the full run: `full_regression_tail.txt`.

## Conclusion

`Not Submitted` and `Overdue` are now mutually exclusive across all three
call sites (`.overview`, `.status_summary`, `.summary`), which now share
one implementation instead of three independently-drifting copies. The
`Submitted`/`Graded` relationship was deliberately left as-is after
surfacing the tradeoff to the Senior Manager rather than expanding scope
unilaterally — documented above so the decision and its reasoning are on
the record, not just the resulting diff. No regressions anywhere in the
full suite. Not yet landed — reported to the Senior Manager for independent
re-verification before merging to beta.

## File hashes (post-commit, from the committed blob)

```
$ git show 7246b65:<path> | sha256sum
dashboard/views.py  fc73565d352277078ae0e2f85b38dafb385976819ffcf2a0c08e6516b10cbf48
dashboard/tests.py  e66c3a6a07b35c51aeb5404d4141f3de237581b3beb3c4022afdff881a38df72
```
