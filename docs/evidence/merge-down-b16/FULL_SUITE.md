# The Phase 2 promotion to beta: the one strict full run, green

Recorded by the Release Engineer (0b), 2026-10-09. 0b started the run and read its result.

**What was run:** the final tip of the merge-down `0ff203c141398a16471957b96790173623874186` (branch `task/epic-a-merge-down-b16`): the Phase 2 line `d2ad0405` plus beta's batches 15, 15a and 16 (up to `42ff4b1e`), the gates 1 to 4 text, the tests-only adaptations, ed's roads-pin fix, H-222, H-192 and H-194. The commits after `918eb2aa` change files under `docs/` only. This one run is the regression of H-222, H-192 and H-194 too (rule 15).

**Gate before it (rule 23):** gate 4 on `918eb2aa`: `pre-commit run --all-files` exit 0, 26 hooks, 25 Passed, 1 Skipped, none Failed (`hook_table_918eb2aa.txt`); makemigrations clean; 1022 module tests OK. See `README.md`.

**Command:** `strict_bundle.sh 0ff203c1 mdb16 Grade-Automator-Plus-epic-a-merge-down-b16 task/epic-a-merge-down-b16` (rules 12, 13, 16 revised, 17, 18; a copy is `strict_bundle.sh.txt`).

| Tip | Suite (WAT) | Result |
|---|---|---|
| `0ff203c1` | 2026-10-09 20:57:59 to 21:10:03 (680 s of tests; 724 s wall) | **Ran 7578 tests, OK (skipped=30)**, exit 0, 0 FAIL, 0 ERROR, 0 blocked outbound, no suspend, watchdog never fired; mypy Passed and makemigrations clean by the script's own pre-checks |

**Tests per app** (they sum to 7578): ai_processor 1126, assignments 756, audit 376, AutoGrader 754, billing 2215, classrooms 529, dashboard 270, students 725, users 827. Against the refresh-13 run of the line (7392): +186, which is the beta batches 15 to 16 and the three verified rows (and the tests-only commits).

**Load average (1 minute):** 2.81 at the start (under the 4.0 line), 6.20 at the end (four parallel workers). No timing test went red. The race test `audit.tests_background_attribution.ClawbackRaceTests`, which deadlocked 3 of 3 alone on the line before H-222, is in this run and passed.

**The log:** `strict_mdb16.log.xz` (9,075,729 bytes unpacked, sha256 `8406382268b4afa897b32254f5bede183a7491ddf4b835315240353851edda39`, checked after packing); `strict_mdb16.summary.txt` is the script's summary.

**Disclosed:** this machine's full runs skip 30 tests; CI's Tests run skips more (75 at batch 16), so the counts are not comparable. Verifier 1's Gate 1 record is `verification_1a_gate1/VERIFICATION.md` (VERIFIED-WITH-NOTES at `0ff203c1`, committed byte for byte).

**Not shown by this run:** anything about live data; the frontend's handling of the coded answers; the paid AI path.

**After the run tip:** the commit that adds this record changes files under `docs/` only.
