# Verification: the Gate 10 fix, bench move @ 80b33a8

**Verifier:** Verification Engineer 2 (v2). **Author:** Security (ed). **Date:** 2026-09-30.
**Branch:** task/epic-a-bench-move @ **80b33a8** (code 47b21e1, off 3d6575c). It fixes Gate 10's red (`tests_no_wildcard_invalidation` on `audit/bench_volume.py:70 cache.clear()`, which v2 found in the S7a re-check and had missed in S8's verification) and closes v2's S4 N1 (bench_history as app code).

The run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, timeout) in 0b's slot. Per rule 15 addendum 2: every repo-wide guard.

**Verdict: VERIFIED.**

| Check | Result |
|---|---|
| Delta (non-docs) | `audit/bench_{volume,history}.py` → `audit/tests/test_bench_{volume,history}.py` (+ `audit/tests/__init__.py`), each class `@skipUnless(AUDIT_BENCH == "1")`. `tests_history_guard`'s stale `SUPPRESSION_ALLOWED` entry is removed (the allow-list is exactly the two production sites). Docstrings only in `audit/volume.py` and `audit_volume_report` |
| Production imports of the benches / `audit.tests` | **none** (grep): only docstring path mentions and a logger *name* `audit.tests.probe` in a test |
| The old files | `audit/bench_*.py` are gone at 80b33a8 |
| All repo-wide guards (`AutoGrader.tests_no_wildcard_invalidation`, `tests_cache_invalidation_coverage`, `tests_reason_codes`, `tests_migration_rollback_defaults`, `audit.tests_route_coverage`, `audit.tests_history_guard`, `classrooms.tests_teacher_access_sweep`, `tests_course_roster_scope_sweep`) + `audit.tests_volume_report` + v2's S8 probe + the `audit.tests` package | **120 OK (skipped=2)**. The 2 skips are the benches (discovered, gated). **The no-wildcard guard is clean** |
| ed's gates | guards + volume 103 OK; audit 334 OK (2 skipped); benches by label with `AUDIT_BENCH=1`: 2 OK (committed) |

Note: this tip is off 3d6575c; epic-a is now 847742b (S7a merged). 0b's Gate 10 on the combined tip runs the whole suite, guards included, and is the integration check.

Log: `runs/benchmove_80b33a8.log`.
