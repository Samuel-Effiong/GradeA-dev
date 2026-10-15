# H-238: Teacher Assignment Analytics accuracy

Endpoint: `GET teacher-admin/dashboard/assignments/<assignment_id>`
(`dashboard/views.py:TeacherAdminDashboardView.assignments`). User-reported
via senior-manager investigation; four issues, all four directed for fix.

## What changed

1. **Withdrawn students excluded.** `submissions` is now filtered to
   students whose enrollment in the assignment's course isn't WITHDRAWN,
   via the same `StudentCourse.objects.active()` join pattern already used
   elsewhere in this file (e.g. `dashboard/views.py:4372`). Affects
   `total_submissions`, `average_grade`, `ai_grading_confidence`,
   `ai_extraction_confidence`.
2. **`average_grade` now uses `classrooms.final_grade.final_grade_from`**
   (the single documented points-weighted formula), fed `(score,
   max_points-or-assignment.total_points)` pairs from graded submissions —
   the same fallback `released_final_grade` already uses.
3. **New shared helper `students/confidence.py:average_confidence_from`**
   filters on `graded_at__isnull=False` (the real "was this graded"
   signal) before averaging, replacing both the assignment-level
   endpoint's unfiltered average and the course-level sibling's no-op
   `grading_confidence__isnull=False` filter (that field is never null).
4. **Extraction confidence split.** The old `ai_extraction_confidence`
   (the teacher's question-paper parse quality, stamped once per
   assignment) is relabeled `question_extraction_confidence`.
   `ai_extraction_confidence` is now a real average of each STUDENT
   SUBMISSION's own `extraction_confidence` field via the same shared
   helper — so both AI-trust numbers answer "how well did the AI handle
   these submissions," not two different questions.

Files: `dashboard/views.py`, `dashboard/serializers.py`,
`students/confidence.py` (new), `dashboard/tests_h238_assignment_analytics_accuracy.py` (new).

Scope note: the course-level sibling endpoint (`courses` action,
~line 3375) has the identical missing-enrollment-filter gap on its own
`submissions` queryset. Filed separately as **H-239** per senior-manager
(not fixed here — a mid-build scope increase on a user-directed fix isn't
the moment, and it deserves its own regression/evidence pass).

## Gate 1 — Baseline / Regression

- New test file: `dashboard/tests_h238_assignment_analytics_accuracy.py`,
  10 tests, all green (see Gate 2 mutation run for exact numbers).
- Full regression: `dashboard` + `students` + `classrooms` apps, 1701
  tests, **OK (skipped=3)**, no failures, no tests modified to force a
  pass.
- `AutoGrader.tests_cache_invalidation_coverage`: 20 tests, OK. No new
  cache key introduced — the `teacheradmins:...:view__assignments` family
  entry already existed in `FAMILIES` before this change.
- `pre-commit` on all 4 changed/added files: black, mypy, flake8, isort,
  bandit, detect-secrets — all passed (black/isort auto-fixed formatting
  once, then clean).

## Gate 2 — Mutation

Three mutations, each introduced by editing the working file directly (not
via git checkout — restored from a pre-mutation copy, confirmed by md5),
run against `dashboard.tests_h238_assignment_analytics_accuracy`:

| # | Mutation | Expected to fail | Result | Restored (md5 match) |
|---|---|---|---|---|
| 1 | Dropped the `student__enrollments__...active()` filter from `submissions` in `views.py` (reverted to `StudentSubmission.objects.filter(assignment=assignment)`) | `test_withdrawn_students_submission_excluded_from_totals`, `test_withdrawn_students_submission_would_have_changed_the_old_numbers` | Both failed: `total_submissions` 2 != 1 on both | Yes — `76b975666abebe3695d45dc056dc1782` before and after |
| 2 | `average_confidence_from` in `students/confidence.py` dropped its `graded_at__isnull=False` filter (plain `Avg` over all submissions) | `test_grading_confidence_ignores_the_default_zero_on_an_ungraded_submission`, `test_submission_extraction_confidence_averages_graded_submissions_only` | Both failed with the exact predicted wrong numbers: `47.5 != 95.0`, `50.0 != 100.0` | Yes — `609ab7a0118eed6c0996be8914a8c981` before and after |
| 3 | `average_grade` in `views.py` reverted to `graded.aggregate(avg=Avg("score_percentage"))` instead of `final_grade_from` | `test_average_grade_is_points_weighted_not_a_plain_mean_of_percentages`, `test_average_grade_falls_back_to_assignment_total_points_when_unset` | Both failed: `75.0 != 52.38` (plain mean vs. weighted), `0.0 != 40.0` (no `score_percentage` fallback without `final_grade_from`) | Yes — `76b975666abebe3695d45dc056dc1782` before and after |

All three mutations were caught by exactly the tests written to cover
them; no other test in the 10-test file flipped. Restoration confirmed by
md5 of `dashboard/views.py` / `students/confidence.py` matching
pre-mutation in every case.

## Rebase onto beta a4d089e4

Branch was cut from beta `eca3d034`. H-237 landed on beta (now at
`a4d089e4`) touching the same file (`dashboard/views.py`, different
functions — the at-risk annotation sites vs. this change's
`assignments`/`courses` actions). `git rebase origin/beta` completed with
**no textual conflict** (the two changes sit in disjoint line ranges) —
new tip `6a9ada3d` on top of `a4d089e4`.

Content-preservation check: `git diff eca3d034 33947724` (pre-rebase) vs.
`git diff a4d089e4 6a9ada3d` (post-rebase), both with the `index` lines
stripped (those necessarily change — the base blob shifted under H-237):
**byte-for-byte identical.** Confirms the rebase only replayed the same
patch onto new line numbers; no logic changed.

Re-verified on the rebased tip (`6a9ada3d`):
- `dashboard`+`students`+`classrooms` full regression: **1718 tests, OK
  (skipped=3)** (up from 1701 pre-rebase — the 17 new tests are H-237's).
- `AutoGrader.tests_cache_invalidation_coverage`: 20 tests, OK.
- `pre-commit` on all 5 changed/added files: clean.

## Gates 3-10

Not applicable — this is a read-only reporting endpoint (no writes, no
billing, no new auth surface: the existing `teacher_course_access_q`
ownership check on `Assignment` is untouched). No concurrency, adversarial,
data-integrity, performance, deployment, or rollback risk beyond what the
untouched surrounding view already carries. Gate 8 tier: logic-only
(reporting-accuracy bug fix) per the DOCTRINE's Gate-8 tiering — LOCAL-REAL
is sufficient, no deployed replay required.

## Completion

- Previous behavior: withdrawn students' submissions counted toward
  totals/averages; `average_grade` was an unweighted mean of
  `score_percentage`; both confidence averages included ungraded
  (default-0) submissions; `ai_extraction_confidence` showed the
  assignment's own one-time parse score instead of a per-submission
  average.
- New behavior: as described above, items 1-4.
- Why correct: matches the codebase's own documented single-source-of-truth
  rules (`StudentCourseQuerySet.active()` for teacher-facing reporting,
  `classrooms/final_grade.py`'s weighted formula, `graded_at` as the real
  grading signal) instead of ad hoc per-endpoint logic.
- Tests proving it: `dashboard/tests_h238_assignment_analytics_accuracy.py`,
  all 10, plus the mutation table above showing each is load-bearing.
