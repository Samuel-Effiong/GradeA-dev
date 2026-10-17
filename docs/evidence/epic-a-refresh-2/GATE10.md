# Gate 10 for staging refresh 2 (Epic A S3 + audit.E001 + S6b + S6c + S4)

Recorded by Integration & Release (0b), 2026-09-30. Rule 15: one strict full run per staging refresh.

- **Tree:** phase2/epic-a `ffa1dba`, which carries:
  - S3 (`064a278`);
  - the audit.E001 floor/limit check (`c9ad98a`);
  - S6b (`3b80977`);
  - S6c (`9df055a`) and its N1 fix (`58244bc`);
  - S4 (`ce49cec`);
  - v2's S4 + S6c integration check at `4124d73`.
- **Command:** `systemd-run --user --scope -q -p MemoryMax=12G -p MemorySwapMax=0 nice -n 10 flock ~/.machine-fullsuite.lock timeout -k 60 3600 python manage.py test --settings=settings_worktree --parallel 4 --noinput`, with every `RACE_COST_*` variable unset.
- **Before:** whole-repo mypy (`pre-commit run mypy --all-files`) passed; `makemigrations --check --dry-run` reported no changes.
- **Result:** 18:18:49–18:26:12 WAT (443 s wall). **Ran 5562 tests, OK (skipped=28)**, 0 blocked outbound calls.
- Everything after `ffa1dba` in this refresh is docs only.
