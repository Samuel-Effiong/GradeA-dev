# Gate 10 for the staging refresh (Epic A S2 + S1b + S5 R3 + the beta abeda10 merge-down)

Recorded by Integration & Release (0b), 2026-09-30. Rule 15: one strict full run per staging refresh.

- **Tree:** phase2/epic-a `2c950aa` (= `6ae18c4` + step (b)'s gate evidence, docs only). The refresh's final tip differs from it only under `docs/`.
- **Command:** `systemd-run --user --scope -q -p MemoryMax=12G -p MemorySwapMax=0 nice -n 10 flock ~/.machine-fullsuite.lock timeout -k 60 3600 python manage.py test --settings=settings_worktree --parallel 4 --noinput`, with every `RACE_COST_*` variable unset (the capped code defaults).
- **Before:** whole-repo mypy (`pre-commit run mypy --all-files`) passed.
- **Result:** 16:38:57–16:46:10 WAT (433 s wall). **Ran 5450 tests, OK (skipped=28)**, 0 blocked outbound calls.
- It covers the one `/auth/verify` caller outside `users` (`classrooms.test_school_admin_otp_deadend`), which 1a left to Gate 10.
