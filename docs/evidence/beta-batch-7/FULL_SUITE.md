# Bundle 7: the strict full run

Recorded by Integration & Release (0b), 2026-10-05.

**Scope:** `task/beta-batch-7` at `01e87ed1`: beta `141c8031` plus bundle 7's seven items: H-97, H-108 (`085adecd`), H-112 (`cb0927f1`), H-91 (`87389416`), H-89 (`94f08711`), H-109 (`c823cdca`) and H-107 (`7ac6fb48`). The last code merge is `7ac6fb48`; `01e87ed1` adds only the backlog rows in `docs/HARDENING_BACKLOG.md` and four document files under `docs/`. No migration against beta. `AutoGrader/settings.py` changes for H-89 only; no environment setting is added.

**Command:** ONE strict full run under rules 12, 13, 16, 17 and 18: `systemd-inhibit --what=idle:sleep:handle-lid-switch --who=GAP --why="GAP test run" --mode=block systemd-run --user --scope -q -p MemoryMax=12G -p MemorySwapMax=0 nice -n 10 flock ~/.machine-fullsuite.lock timeout -k 60 3600 python manage.py test --settings=settings_worktree --parallel 4 --noinput --verbosity 2`, with `PYTHONDONTWRITEBYTECODE=1`, `PYTHONFAULTHANDLER=1`, and RACE_COST, AUDIT_BENCH and ENABLE_GRADING_BENCHMARK unset. Output went straight to a file, with stdin from `/dev/null` (rule 18); no pipe. A watchdog at the side would have recorded the process tree and stopped the run after 300 s without a new log line. Whole-repo mypy (passed) and `makemigrations --check` (no changes) ran first.

| Tip | Start–end (WAT) | Result |
|---|---|---|
| `01e87ed1` | 2026-10-05 15:04:34–15:17:04 (750 s wall, 695 s tests) | **Ran 5686 tests, OK (skipped=28)**, exit 0, 0 FAIL, 0 ERROR, 0 blocked outbound, no suspend, watchdog never fired |

**Tests per app** (counted from the run's per-test lines; they sum to 5686):

| App | Tests | Change from bundle 6's strict run (5601) |
|---|---|---|
| ai_processor | 817 | 0 |
| assignments | 628 | 0 |
| AutoGrader | 551 | +82 |
| billing | 2097 | +3 |
| classrooms | 401 | 0 |
| dashboard | 270 | 0 |
| students | 268 | 0 |
| users | 654 | 0 |

The per-app changes are counted, not attributed test by test: the bundle's new tests are in AutoGrader (H-89, H-91, H-97, H-107, H-109) and billing.

**What this run is the first of:** the first full run with H-107's two fixes in (workers take the default SIGTERM; the runner's stream waits and retries on a write that would block). It ran at `--parallel 4` with the PDF tests' real browser driver and ended with its result line. Because the output went to a file, this run does not exercise the stream fix against a full pipe; that was shown separately in H-107's own evidence. It is also the first run of the whole of assignments, students, ai_processor, classrooms and dashboard on the combined bundle.

**The machine:** shared with another project; its manager was told before the start and at the end. 0b granted no other GAP run during it. **This was the only strict run of bundle 7.** Log: 7.5 MB, sha256 prefix `9f961d4b22420a8b`, kept outside the repo at `~/Documents/Projects/GAP-0b-runs/strict_b7.log`.

**After the run tip:** this record is the only change, so 0 non-docs files differ from `01e87ed1`.
