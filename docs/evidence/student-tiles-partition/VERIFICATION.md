# Verification: student tiles partition @ f908aaf

**Verifier:** Verification Engineer (1a, grade-automator-plus-c2). **Author:** Hardening (d5).
**Base:** beta 463e222 (next bundle; 2a is frozen). **Date:** 2026-09-30.

**Verdict: VERIFIED-WITH-NOTES.** The change is correct and implements the founder's decision: the four student tiles now partition the total. Nothing is required before merge. It is a **breaking response-value change for the frontend**, as the EVIDENCE says.

## What I checked
My own detached checkout with its own test DB, under `systemd-run` MemoryMax=6G, nice and timeout.

| Check | Result |
|---|---|
| Scope | `dashboard/views.py` (`_assignment_status_counts` plus `summary`'s completion rate), `dashboard/tests.py` and evidence. No migration. |
| Submitted = submissions − Graded | This is valid only if there is one submission per student per assignment. `StudentSubmission.Meta` has an **unconditional** `UniqueConstraint(fields=["student", "assignment"])`, so the subtraction is exact. Graded's definition (`is_published=True, score_percentage__isnull=False`) is unchanged. |
| Consumers | `_assignment_status_counts` is used by `summary` (completion rate updated), by the course-summary view (which returns the counts and derives no rate from Submitted), and by `status_summary` (the serializer returns the counts). The other `submitted_count` values in `dashboard/services.py` and `risk.py` come from separate teacher-side code that doesn't use this function, so they are unaffected. |
| Completion rate | (Submitted + Graded) / total, so releasing a grade no longer lowers it. d5's test pins 50% before and after a release. |
| Query count | Unchanged: the new value is arithmetic on two counts already computed. |
| `dashboard.tests` | **Ran 88, OK**, 207 MB peak |

## My mutants (d5's three suggestions)
| Mutant | Result |
|---|---|
| T1: Submitted back to `submissions.count()` (includes Graded) | killed by 6 tests, including `test_the_four_tiles_partition_the_total` and `test_tiles_sum_to_total_assignment_count` |
| T2: completion rate from Submitted only | killed: `test_completion_rate_counts_graded_and_ungraded_submissions` and the partition test |
| T3: Graded without the score condition (`is_published` only) | **SURVIVED.** See N1. |

All restores were sha-checked.

## Notes (not blocking)
**N1.** Nothing pins that Graded needs a score. A submission that is published but has no `score_percentage` would move from Submitted to Graded under T3, and the tiles would still sum to the total. This definition predates the change (unchanged here), but a test with one published-unscored submission (expected: Submitted) would pin it.

**N2 (frontend).** `assignments_submitted` and `assignment_submitted` now exclude graded work. The field names and shape are unchanged; the values drop by the Graded count. Any client that computed "graded out of submitted" or its own completion figure from these fields needs updating. The EVIDENCE flags this.

## Re-verification: N1 @ f2510cc. Verification Engineer 1a, 2026-09-30
**Verdict for the tip f2510cc: VERIFIED.** f908aaf..f2510cc is this record (d6bb320, committed verbatim) plus one test, `test_a_published_but_unscored_submission_stays_submitted` (dashboard/tests.py +26, test-only). `dashboard.tests`: **Ran 89, OK**. My T3 (Graded = `is_published` only) is now **killed** by the new test; T1 and T2 are still killed. N1 is closed. N2 (the frontend value change) stands as information.
