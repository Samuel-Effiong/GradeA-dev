# Epic A staging refresh 6: Gate 10 (S7d + the bundle 4 merge-down)

Recorded by Integration & Release (0b), 2026-10-02.

**Scope:** phase2/epic-a `16377731`: the bundle 4 merge-down (beta `67a0681`, merged at `dc0475aa`), S7d (merged at `16377731`) and the founder's Phase 2 source documents (`ba165b4`). Migrations new to staging: `billing 0071` and `billing 0072`, both from bundle 4.

**Command:** the strict full run under rules 12, 13, 16 and 17: `systemd-inhibit --what=idle:sleep --who=GAP --why="GAP test run" --mode=block systemd-run --user --scope -q -p MemoryMax=12G -p MemorySwapMax=0 nice -n 10 flock ~/.machine-fullsuite.lock timeout -k 60 3600 python manage.py test --settings=settings_worktree --parallel 4 --noinput`, with `PYTHONDONTWRITEBYTECODE=1` and RACE_COST and AUDIT_BENCH unset. Whole-repo mypy (passed) and `makemigrations --check` (no changes) ran first.

| Tip | Start–end (WAT) | Result |
|---|---|---|
| `16377731` | 2026-10-02 10:51:46–11:05:19 (813 s wall, 762 s tests) | **Ran 6023 tests, OK (skipped=30)**, exit 0, 0 blocked outbound, no suspend |

**Earlier attempt:** a first run on the same tip on 2026-10-01 was cut off by a machine restart and left no result. This run is the only one that counts.

**After the run tip:** this record is the only change, so 0 non-docs files differ from `16377731`.
