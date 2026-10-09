# Gate 10 for staging refresh 4 (Epic A S6d)

Recorded by Integration & Release (0b), 2026-09-30. Rule 15: one strict full run per staging refresh.

- **Tree:** phase2/epic-a `c393d17`. That is S6d (`8145e29`, the merge of `433b64a` = `f0068ef` + docs) on top of refresh 3's `95756f0`, plus S6d's record (`1876f87`, `c393d17`).
- **Command:** `systemd-run --user --scope -q -p MemoryMax=12G -p MemorySwapMax=0 nice -n 10 flock ~/.machine-fullsuite.lock timeout -k 60 3600 python manage.py test --settings=settings_worktree --parallel 4 --noinput`, with every `RACE_COST_*` variable and `AUDIT_BENCH` unset.
- **Before:** whole-repo mypy passed and `makemigrations --check` was clean.
- **Result:** 20:06:46–20:15:40 WAT (534 s). **Ran 5623 tests, OK (skipped=30)**, 0 blocked outbound.
- Everything after `c393d17` in this refresh is docs only.
