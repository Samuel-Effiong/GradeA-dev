# Batch 16: the module gate and the one strict full run, green

Recorded by the Release Engineer (0b), 2026-10-09. 0b started both runs and read their results.

**What batch 16 is:** the pushed beta tip `b4fda750` (batch 15a) plus four verified rows, each merged as its final tip: H-211 (`fc35d036`), H-208 (`5b9e9956`), H-203 (`5fe68114`), H-209 (`a0c5af33`, then its own evidence commit `7d328eac`, docs only), then the docs commit `6629f838` (`docs/HARDENING_BACKLOG.md` and `docs/evidence/beta-batch-16/` only). Every merge was conflict-free.

**Module gate (0b, `gate_b16.sh.txt`, on the merge tip `72d6a157`, the code of all four rows):** `pre-commit run --all-files` exit 0 with the hook table in `hook_table_72d6a157.txt` (25 hooks: 24 Passed, 1 Skipped, none Failed); makemigrations "No changes detected"; 14 test modules Ran 253, OK (skipped=3). See `README.md`.

**Command:** the strict full run under rules 12, 13, 16 (revised), 17 and 18, by `strict_bundle.sh 6629f838 b16 Grade-Automator-Plus-beta-batch-16 task/beta-batch-16` (a copy is beside this file).

| Tip | Suite (WAT) | Result |
|---|---|---|
| `6629f838` | 2026-10-09 19:15:13 to 19:24:36 (531 s of tests; 563 s wall) | **Ran 6521 tests, OK (skipped=28)**, exit 0, 0 FAIL, 0 ERROR, 0 blocked outbound, no suspend, watchdog never fired; mypy Passed and makemigrations clean by the script's own pre-checks |

**Tests per app** (they sum to 6521): ai_processor 861, assignments 721, AutoGrader 692, billing 2171, classrooms 496, dashboard 270, students 534, users 776. Against batch 15's 6455: assignments +11, billing +13, classrooms +9, students +14, users +19 (the new tests of the four rows); ai_processor, AutoGrader and dashboard unchanged.

**Load average (1 minute):** 3.25 at the start (under the 4.0 line), 9.31 at the end (four parallel workers, as in the other bundles). No timing test went red.

**The log:** `strict_b16.log.xz` (8,085,413 bytes unpacked, sha256 `4f29b9a7b4758ef67e1a00b08acac2e407cf9d92f58670f4825f01e5d1cc4178`, checked after packing); `strict_b16.summary.txt` is the script's summary.

**Disclosed:** the run started while the machine still carried the load of the module gate's hooks (1-minute load 3.36 just before, under the line). This machine's full runs skip 28 tests; CI's Tests run of batch 15a skipped 75, so the two counts are not comparable.

**Not shown by this run:** anything about live data; the student site's handling of the new refusal answers; the paid AI path (no real-model test runs here).

**After the run tip:** the commit that adds this record changes files under `docs/` only.
