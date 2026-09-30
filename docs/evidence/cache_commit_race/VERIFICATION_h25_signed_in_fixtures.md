# Verification: H-25 signed-in fixtures @ 877c900

**Verifier:** Verification Engineer (1a, grade-automator-plus-09). **Author:** Hardening (d5).
**Base:** batch-2a bda5927. **Date:** 2026-09-30.

**Verdict: VERIFIED-WITH-NOTES.** The change is tests only and correct. It restores H-25's tests to the path they claim to measure (a signed-in student → ENROLLED) and pins the PENDING path that retire (A) now sends never-signed-in students down. Nothing is required before merge.

## What I checked
The checks ran in my own detached checkout at 877c900 with its own test database. Every run used `nice -n 10 timeout -k 60 1800` and `EXEMPT_EMAIL_DOMAINS=`.

| Check | Result |
|---|---|
| Scope | One commit on bda5927. It touches only `AutoGrader/tests_cache_commit_race.py` (+9) and `AutoGrader/tests_cache_commit_race_cost.py` (+51/−2), with no source changes. `black --check` passes. |
| Cause | `classrooms.services.enrollment.has_signed_in` is `last_login is not None` or a `UserActivity` row. Fixtures without `last_login` therefore went down the PENDING branch: temporary password, account `save()`, enrollment PENDING, credentials email. This matches d5's reproduction (0 != 20; 40 != 20). |
| Where the 2 PENDING bumps come from | 1. The account `save(update_fields=[password, is_active, …])`: `users.signals.clear_user_cache` bumps on every CustomUser save.<br>2. The StudentCourse create: `classrooms.signals.clear_student_course_cache`.<br>The ENROLLED path has only (2). The measured PENDING round trips are 4.00 per row: 2 while the transaction is open plus 2 at commit. That is flat at 2/20 rows (reduced sizes) and 20/200 rows (defaults). |
| Path (1) proves its path | `import_rows` now asserts every row's `enrollment_status` (ENROLLED by default), and the race test already asserts 20×round ENROLLED. So a fixture that silently changes path fails on status, not only on cost. |
| Path (2) proves its path | It asserts `status=PENDING` for every row. PENDING is set only on the no-account and never-signed-in branches, so status is enough to identify the branch. It also asserts exactly 2×size bumps both open and at commit, and the commit round trips. |
| Other users of the fixtures | `make_user` isn't imported anywhere else. The only cross-module import is `CommitRaceBase` in the cost module. |
| Baseline at reduced sizes (`RACE_COST_ROWS=200 RACE_COST_ENROLLMENTS=600`), the race class + the per-row class | **3 OK**, 160 s |
| Both whole modules at the **default** 2000/6000 | **Ran 17, OK** (1467 s test time, 1515 s wall). See N1. |

## My mutants (5, at reduced sizes, against both classes)
| Mutant | Result |
|---|---|
| F1: the race fixture's `last_login` removed | **killed**: `test_twenty_concurrent_enrollments_racing_reads_settle_fresh_every_round` |
| F2: the cost fixture never signed in (`last_login = None`) | **killed**: `test_import_roster_at_200_and_2000_rows` |
| P1: the PENDING account save done with `QuerySet.update()` (no signal, so 1 bump per row) | **killed**: `test_first_import_of_never_signed_in_students_is_two_bumps_per_row` |
| P2: the ENROLLED path gains an extra account save (2 bumps per row) | **killed**: `test_import_roster_at_200_and_2000_rows`, so "1 per row" is pinned exactly, not as a floor |
| P3: `has_signed_in` ignores `last_login` | **killed** by both path (1) tests |

Each mutant is killed by the test it targets. All restores were sha256-checked against 877c900.

## Notes (not blocking)
**N1 (runtime; pre-existing, not introduced).** Both modules at the default sizes took 1515 s of the 1800 s rule-12 timeout. The machine was shared with one other targeted run at the time.
- Almost all of it is `SingleTransactionRosterCostTests` at 6000: 36,000 students, four timed passes of about 100–160 s each, plus two instrumented passes.
- That class creates `StudentCourse` rows directly and never calls `enroll_student_by_email`, so this commit's fixture change cannot alter its path or its time.
- d5's own run at the defaults covered only the two changed classes (217 s), which is why the whole-module time wasn't visible there.
- Risks:
  - a targeted run of these two whole modules at the defaults can hit the rule-12 timeout on a busier machine;
  - in a `--parallel 4` full suite this one class takes about 20 minutes of a worker.
- Suggestion, not required: gate the 6000 scale behind an opt-in env var, or lower its default. That is for 0b and d5 to decide.

**N2 (informational).** Path (2) identifies the branch by PENDING status and pins the bump count. It doesn't assert the temporary password or the credentials email; those belong to retire (A)'s own tests, and status is enough to prove the path here.
