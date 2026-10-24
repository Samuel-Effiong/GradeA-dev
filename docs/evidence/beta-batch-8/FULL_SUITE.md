# Batch 8: the strict full run

Recorded by Integration & Release (0b), 2026-10-06. 0b did not start this run and did not grant it; 0b checked its evidence afterwards. The Senior Manager (SM) ruled on 2026-10-06 that it stands as batch 8's one full run, on the condition that this record states the departures below plainly.

**Scope:** `task/beta-batch-8` at `e578e3db`: beta `63c3da22` plus batch 8's three items: H-119 (`4df1b61a`), H-98 (`5236b7dd`) and H-118 (`e578e3db`). Six files outside `docs/` differ from beta: `billing/license_service.py`, `billing/refresh_timing.py`, `billing/tests/test_allocation_anchor.py` (H-98); `assignments/tests_pdf_renderer.py`, `AutoGrader/tests_no_playwright_at_import.py` (H-118, test only); `students/migrations/0026_alter_backgroundprocessingtask_task_type.py` (H-119, four comment lines, no operation). No new migration. H-110 is not in this batch.

**Command:** `scripts/strict_gate.py` (the tracked file at `e578e3db`, sha256 prefix `245629cc0acf31ac`), run name `beta-batch-8-strict`, two consecutive runs in its own worktree. Each run: `pre-commit run --all-files` (all hooks passed, mypy among them), `scripts/check_migration_safety.py --base 63c3da22` ("No new migration files in this diff."), `manage.py check` (no issues), `makemigrations --check --dry-run` ("No changes detected"), then `python manage.py test --settings=settings_worktree --noinput --parallel 1 -v 2`. Each run used its own test database. The script wrote every log straight to a file.

| Run | Start–end of the suite (WAT) | Result |
|---|---|---|
| 1 | 2026-10-06 00:48:35–01:23:16 | **Ran 5709 tests in 2043.246s, OK (skipped=26)**, exit 0 |
| 2 | 2026-10-06 01:25:47–01:59:10 | **Ran 5709 tests in 1965.852s, OK (skipped=26)**, exit 0 |

The whole gate ran from 00:42:49 to 01:59:15. The script's verdict is PASS. Its tree fingerprint is the same before and after each run: head `e578e3db`, tree `7d916790`, 3171 tracked files, no uncommitted change. It found no suspend in the system journal.

**What 0b checked, 2026-10-06 about 10:29 WAT:**
- All 24 raw logs in `~/Documents/Projects/Grade-Automator-Plus-gate-logs/beta-batch-8-strict/` match `RAW_LOG_SHA256SUMS.txt` (`sha256sum -c`, no mismatch).
- Each of the two suite logs holds exactly one "Ran" line, ends with its "Ran 5709 tests", "OK (skipped=26)" and "Destroying test database" lines, and holds no `FAIL:` or `ERROR:` header.
- `summary.json` names commit `e578e3dba0e124468825ef1eae2086da1264a3d5`.

**Tests per app** (counted by 0b from run 1's test lines in the raw log; they sum to 5709):

| App | Tests | Change from bundle 7's strict run (5686) |
|---|---|---|
| ai_processor | 817 | 0 |
| assignments | 628 | 0 |
| AutoGrader | 562 | +11 |
| billing | 2109 | +12 |
| classrooms | 401 | 0 |
| dashboard | 270 | 0 |
| students | 268 | 0 |
| users | 654 | 0 |

The changes are counted, not attributed test by test; the batch's new tests are in AutoGrader (H-118) and billing (H-98).

**Skipped: 26, against 28 in bundle 7's run.** The 26 are the same opt-in tests, by the reasons printed in the log: 12 real AI calls, 9 load tests, 4 live network, 1 `CI_REQUIRE_REDIS`. The other two in bundle 7's run were tests that cannot fork inside a parallel worker; this run was serial, so they ran.

## Departures from the form of earlier bundle runs

None of these bears on whether `e578e3db` passed. They are stated because the team's rules were not followed, or because the evidence is weaker than it looks.

1. **Serial, and twice.** Bundle 7's run was one run at `--parallel 4` with the log-silence watchdog. This was two runs at `--parallel 1`. Rule 15 asks for one run per bundle; the second was not needed. Because the run was serial, it does not exercise the parallel test runner (H-107's ground).
2. **No memory cap and no machine lock.** `strict_gate.py` applies neither rule 13's `MemoryMax` nor the lock on `~/.machine-fullsuite.lock`, and the evidence does not show that the caller wrapped it. The script did hold the sleep, idle and lid-switch inhibitor (rule 16; `inhibitor.txt`).
3. **No load average recorded** at the start or the end. This batch holds H-118's real-browser tests, which have wall-clock limits. They passed in both runs.
4. **No grant and no notice to the other project on record; the runner is not identified.** 0b's notes end at 00:21 with nothing of ours holding the machine; the run started at 00:42. Neither 0b nor the SM started it or has a note of it. The SM is asking the user.
5. **The per-test table is not evidence.** The script's own cross-check reads "Per-test parse matches summary: False" for both runs, and the verdict does not use it. `r1-5-full-suite.tests.tsv` and `r2-…tsv` hold 5706 rows against 5709 tests run, and three of the rows are not test ids ("1a", "a", "b": the parser read log lines that were mixed into test lines). The raw logs' own "Ran" and "OK" lines are the evidence (rule 18). Backlog row H-125.
6. **`beta-batch-8-strict/EVIDENCE.md` is the script's template, committed as generated.** Its "NOT RUN", "NOT SUPPLIED" and "PARTIAL" entries are the script's placeholders for a single change's ten gates; they are not findings about this batch. The items' own gates are in each item's evidence folder.

## Where the evidence is

`docs/evidence/beta-batch-8-strict/`: the script's `EVIDENCE.md`, `summary.json`, `RAW_LOG_SHA256SUMS.txt` and the 24 logs, compressed. The checksums in the list are of the uncompressed logs. The two suite logs are stored as `.xz`, not the script's `.gz`, because each `.gz` is over the repository's 500 KB limit for an added file; 0b compressed them from the raw logs, and each decompresses to the listed checksum (`706ced20…` and `2fffc3b2…`). The other 22 `.gz` files are the script's own.

**Credential pattern check (0b, on the files added here, compressed files opened, values masked):** no address with a password part. Two lines in each suite log match the assignment pattern on the word "secret" (lines 75084 and 75091 of run 1); the matched text is a test's host name in a "blocked unsafe fetch" warning, not a credential. Its limit: the two patterns only.

**After the run tip:** the commit that adds this record changes files under `docs/` only.
