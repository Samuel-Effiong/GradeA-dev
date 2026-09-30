# Student dashboard tiles partition the total

Branch `task/student-tiles-partition`, commit `2eb7c3e` on beta `463e222`. Founder request, 2026-09-30.

## BREAKING RESPONSE-VALUE CHANGE (frontend)

The **value** of the Submitted count changes; field names and response shape do not.

| Endpoint | Field | Before | After |
|---|---|---|---|
| student overview | `assignments_submitted` | every assignment with a submission, graded or not | submissions whose grade is **not released** to the student |
| student status-summary (all courses, or `?course=`) | `assignments_submitted` | same as above | same as above |
| student course summary | `assignment_submitted` | same as above | same as above |

For a student, Submitted drops by exactly the Graded count. The founder's example, 20 assignments:

| Tile | Before | After |
|---|---|---|
| Submitted | 17 | 11 |
| Graded | 6 | 6 |
| Not Submitted | 1 | 1 |
| Overdue | 2 | 2 |
| Sum | 26 (Graded counted twice) | 20 |

A frontend that adds Graded into Submitted, or shows Graded as a share of Submitted, must change. A frontend that shows the four tiles side by side needs no change.

## Definitions (`dashboard/views.py` `_assignment_status_counts`)

- **Submitted**: has a submission whose grade the student cannot see yet (not graded, or graded but unpublished).
- **Graded**: grade released (`is_published` with a `score_percentage`), unchanged.
- **Overdue**: no submission, due date passed, unchanged.
- **Not Submitted**: no submission, not overdue, unchanged.

These four are mutually exclusive and sum to the number of assignments shown. The query count is unchanged. Submitted is computed as the submissions total minus Graded; a unique constraint guarantees one submission per student per assignment.

**`completion_rate`** (course summary) used Submitted. It now counts Submitted + Graded, so it keeps its meaning (the share of assignments with a submission) and a grade release does not change it.

## Tests

`dashboard.tests`, 88 tests, all OK (`dashboard_tests_2eb7c3e.log`, one outcome line per test). The run was under rule 13 (`systemd-run` MemoryMax=6G, `nice -n 10`, `timeout`), with `EXEMPT_EMAIL_DOMAINS=` set empty. The same run also named `dashboard.tests_cache_matrix_status_summary`, which exists only on the H-1 stage 3/step 4 branches, not on beta. It reported an import error for that one label; no test failed.

The new and changed tests:
- `StudentTilesPartitionFounderExampleTest`: the founder's 20 assignments (3 of the 11 Submitted are graded but unpublished). It checks 11/6/1/2 through overview, status-summary (all and one course) and course summary, with completion 85%.
- Both "tiles sum to total" tests now sum all four tiles, before and after a release.
- `test_completion_rate_counts_graded_and_ungraded_submissions`: 50% before and after a release.
- Pinned values that change: after a release, a submission moves from Submitted to Graded.

## Reported, not changed

The per-row status (`assignments/services.py` `get_student_assignment_status`) defines GRADED as `graded_at` + published. The Graded tile uses published + `score_percentage`. Today's write paths can't make these disagree (see H-63 in `docs/HARDENING_BACKLOG.md`). One shared definition is due when either is next touched.
