# Batch 17: the one strict full run, green

Recorded by the Release Engineer (a6), 2026-10-14. a6 merged the batch, started the run and read its result.

**What batch 17 is:** beta `913f248f` (the user's 13 October commits) plus three verified tips, each merged at the tip the Verifier named, with no conflicts:

- H-220 `0105987f`, which carries H-213 `2df68a52` (merge `c62a2472`);
- H-214 `a125a8cb` (merge `c42b70d7`);
- H-212 `a829bee6` (merge `64164157`).

On the merge tip, every file each branch changed is identical to that branch's tip. No branch adds a migration.

**Hooks on the merge tip `64164157`:** `pre-commit run --all-files` exit 0. The table is in `hook_table_64164157.txt`: 26 hooks, 25 Passed, 1 Skipped (broken symlinks, no files), none Failed. The hooks changed no files.

**Command:** `strict_bundle.sh 64164157 b17 Grade-Automator-Plus-beta-batch-17 task/beta-batch-17`; a copy is beside this file. It is the previous Release Engineer's script with two changes:

- the output folder is a6's (`GAP-a6-runs`);
- `MemoryMax` is 9G instead of 12G, because only 10.7 GB was free on the laptop at the start. The cap was not reached.

The run kept rules 12, 13, 16 (one inhibitor around the whole script), 17 and 18, plus the shared lock, the 3600 s timeout and the 300 s stall watchdog.

| Tip | Suite (WAT) | Result |
|---|---|---|
| `64164157` | 2026-10-14 11:57:04 to 12:06:46 (549 s of tests; 582 s wall) | **Ran 7647 tests, OK (skipped=30)**, exit 0. FAIL/ERROR headers: 0 across the whole log. No suspend; the watchdog never fired. The script's own pre-checks found mypy Passed and makemigrations "No changes detected". |

**Tests per app** (they sum to 7647): ai_processor 1126, assignments 767, audit 376, AutoGrader 754, billing 2216, classrooms 552, dashboard 290, students 725, users 841. Batch 16's 6521 came before the Phase 2 line was promoted (`fafed57f`, CI Ran 7578), so the two counts are not comparable.

**Rule 20:** `AutoGrader.tests_cache_bespoke_1114` ran, and every test in it is "... ok".

**Load average (1 minute):** 2.86 at the start, 5.08 at the end (four parallel workers).

**The log:** `strict_b17.log.xz`, 9,157,922 bytes unpacked, sha256 `d861c81617c05948793924011778a69b9c147f3c63be2674be14fe04ac0ae70e`. Unpacking it was checked to give the identical bytes. `strict_b17.summary.txt` is the script's summary.

**Disclosed: blocked outbound = 8.** The 8 log lines come from two attempts to reach `oauth2.googleapis.com/token`, made inside the `users.tests_google_auth` tests. The test network guard (H-39) blocked both, and every test in that module passed. No batch 17 file is on that path. Batch 16's run had 0 such lines, so this came in with the Phase 2 line or the user's 13 October commits. It is worth a backlog row to mock that client.

**Not shown by this run:** anything about live data; the paid AI path (no real-model test ran).

**After the run tip:** the commit that adds this record, and H-220's chain evidence, changes files under `docs/` only.
