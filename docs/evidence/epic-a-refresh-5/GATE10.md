# Epic A staging refresh 5: Gate 10 (S7b + S7c)

Recorded by Integration & Release (0b), 2026-10-01.

**Scope:** phase2/epic-a `830bf8d`: S7b (per-item retry, already on the epic at `ca35971`) and S7c (credit exhaustion mid-batch, merged at `830bf8d`, with `students 0030`).

**Command:** the strict full run under rules 12 and 13: `systemd-run --user --scope -q -p MemoryMax=12G -p MemorySwapMax=0 nice -n 10 flock ~/.machine-fullsuite.lock timeout -k 60 3600 python manage.py test --settings=settings_worktree --parallel 4 --noinput`, with RACE_COST and AUDIT_BENCH unset. Whole-repo mypy (passed) and `makemigrations --check` (no changes) ran first.

| Tip | Start–end (WAT) | Result |
|---|---|---|
| `830bf8d` | 00:12:02–00:20:39 (517 s wall, 488 s tests) | **Ran 5654 tests, OK (skipped=30)**, 0 blocked outbound, no suspend |

**After the run tip:** only docs changed, so 0 non-docs files differ from `830bf8d`. That covers the architecture-docs merge of `eec13d1` and this record.
