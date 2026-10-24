# Batch 12s: the one strict full run, green

Recorded by the Release Engineer (0b), 2026-10-08. 0b started the run and read its result.

**What batch 12s is:** the pushed beta tip `035e0a07` (batch 12b) plus ONE row, H-148 (the teacher names a student when adding by email), merged as its verified record commit `b8098a65` (merge `03f3f1b7`), then the docs commit `95c51b61` (docs/HARDENING_BACKLOG.md only: H-148 closed). The user's order of 2026-10-08 13:02 WAT lifted the hold on H-148. H-152 and H-153 are NOT in it: H-153's branch contains H-152's whole final tip (`8b23502b`) and H-152's contains H-148's, so the three are stacked and the rename row cannot go in alone; both stay held for the user's decision on the old activation door.

**Command:** the strict full run under rules 12, 13, 16 (revised: the whole script in one inhibit), 17 and 18, by `strict_bundle.sh 95c51b61 b12s Grade-Automator-Plus-beta-batch-12s task/beta-batch-12s` (a copy is beside this file; it stops on a missing settings file, a shared test-database name, a failing mypy or a dirty makemigrations).

| Tip | Suite (WAT) | Result |
|---|---|---|
| `95c51b61` | 2026-10-08 13:15:05 to 13:22:37 (425 s of tests; 452 s wall) | **Ran 6172 tests, OK (skipped=28)**, exit 0, 0 FAIL, 0 ERROR, 0 blocked outbound, no suspend, watchdog never fired; mypy Passed and makemigrations "No changes detected" by the script's own pre-checks |

**Tests per app** (counted from the run's per-test lines; they sum to 6172): ai_processor 852, assignments 663, AutoGrader 692, billing 2130, classrooms 461, dashboard 270, students 438, users 666. Against batch 12b's 6133: classrooms +27 and users +12, the 39 tests of H-148 (as in batch 12a's record: 6047 + 39 = the old six-row batch's 6086). Skipped 28, the same as every run since batch 11.

**Load average (1 minute):** 2.25 at the start, 7.29 at the end (another project's tests ran beside it).

**The log:** `strict_b12s.log.xz` (8,179,244 bytes unpacked, sha256 `9f62515fdadff419c1c272980e285354f062572603d8923edd1376521bc38d63`); `strict_b12s.summary.txt` is the script's summary.

**After the run tip:** the commit that adds this record changes files under `docs/` only.

**Credential pattern check (0b, `scripts/credscan.py`'s scan function called on each file added with this record, values masked):** the record, the script copy and the summary: 0 hits. The packed log (unpacked by the tool): no address with a password part; 6 literal rows (the two known warning lines from the AI module's test, a 13-character test host name, and 4 under names that hold only the word KEY, which the tool counts and does not list), 12 code expressions, 5 placeholder words, 9 variables or masks: the same counts as for batch 12's and 12a's logs.
