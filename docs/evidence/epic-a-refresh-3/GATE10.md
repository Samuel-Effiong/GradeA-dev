# Gate 10 for staging refresh 3 (Epic A S8 + CodedError + auth-lock + S7a + the bench move)

Recorded by Integration & Release (0b), 2026-09-30. Rule 15: one strict full run per staging refresh.

## The first run (RED)
At phase2/epic-a `3d6575c` (S8, the CodedError slice and the auth-lock slice), 19:19:45–19:27:26 WAT: **Ran 5586, FAILED (failures=1)**, 0 blocked outbound.
- The one failure was `AutoGrader.tests_no_wildcard_invalidation`: S8's harness `audit/bench_volume.py:70` called `cache.clear()`, and it lived as an app module.
- It was missed because S8's gates ran the audit app while the guard lives in AutoGrader. Hence Rule 15 addendum 2: every slice that adds or moves modules runs all repo-wide guard modules, and bench code lives under test paths.
- **Fix:** the bench-harness move `80b33a8`. Both harnesses moved to `audit/tests/test_bench_*.py`, skipped unless `AUDIT_BENCH=1`. v2 VERIFIED it; it merged at `db27c79`.

## The fresh run (GREEN)
S7a (`039bbc8`, v2 VERIFIED) was merged at `be0744a` beside the move, so the tree differs from `3d6575c` in real code. By SM ruling (option B), one fresh full run then covers the combined tip.
- **Tree:** phase2/epic-a `fc52af3`.
- **Command:** `systemd-run --user --scope -q -p MemoryMax=12G -p MemorySwapMax=0 nice -n 10 flock ~/.machine-fullsuite.lock timeout -k 60 3600 python manage.py test --settings=settings_worktree --parallel 4 --noinput`, with every `RACE_COST_*` variable and `AUDIT_BENCH` unset.
- **Before:** whole-repo mypy passed and `makemigrations --check` was clean.
- **Result:** 19:39:44–19:47:48 WAT (484 s). **Ran 5599 tests, OK (skipped=30)**, 0 blocked outbound. The 30 skips are the 28 standing ones plus the 2 benches.
- Everything after `fc52af3` in this refresh is docs only.
