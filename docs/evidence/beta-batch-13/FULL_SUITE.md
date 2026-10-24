# Batch 13: the one strict full run, green

Recorded by the Release Engineer (0b), 2026-10-08. 0b started the run and read its result.

**What batch 13 is:** the pushed beta tip `0eabb0bf` (batch 12s) plus three rows, each merged as its verified record commit: H-158, the second form (`fff27a49`, of `6aa13949`, Verifier 2 VERIFIED-WITH-NOTES at `850cecff`), H-152, the old activation door closed (`0dbae4e2`, of `7a7b8bd2`, Verifier 2 VERIFIED-WITH-NOTES at `8b23502b`) and H-153, a teacher renames a student (`52b9a5fa`, of `01f95d9f`, Verifier 1 VERIFIED-WITH-NOTES at `05c83b0d`; H-153's branch carries H-152's whole final tip, so the two ride together); then the docs commit `b7f38629` (docs/HARDENING_BACKLOG.md only). The user's word of 2026-10-08 ("there are no leftover account", his statement and not a count the team made) lifted the hold on H-152 and H-153. All three merges were conflict-free (`git merge-tree` first). By the Senior Manager's order the score-printing stack, H-180, H-179, H-181, H-182 and H-178 are batch 14.

**H-148 is not reverted:** every non-docs line that H-148's branch added (26 files, `787a81fb..19f5c872`) is still present in the batch's files (0 missing, checked by a line count against the merged tree).

**Command:** the strict full run under rules 12, 13, 16 (revised), 17 and 18, by `strict_bundle.sh b7f38629 b13 Grade-Automator-Plus-beta-batch-13 task/beta-batch-13` (a copy is beside this file). The script's guard against a second test run now counts only runs whose working directory is a Grade-Automator-Plus worktree, so another project's test run on the laptop no longer stops it (the load average is watched instead, as the Senior Manager ruled).

| Tip | Suite (WAT) | Result |
|---|---|---|
| `b7f38629` | 2026-10-08 13:36:06 to 13:43:43 (423 s of tests; 457 s wall) | **Ran 6247 tests, OK (skipped=28)**, exit 0, 0 FAIL, 0 ERROR, 0 blocked outbound, no suspend, watchdog never fired; mypy Passed and makemigrations "No changes detected" by the script's own pre-checks |

**Tests per app** (counted from the run's per-test lines; they sum to 6247): ai_processor 852, assignments 705, AutoGrader 692, billing 2130, classrooms 479, dashboard 270, students 438, users 681. Against batch 12s's 6172: assignments +42 (H-158), classrooms +18 and users +15 (H-152's and H-153's new tests, less the 27 old tests of the removed door that H-152 removes, each named in its evidence). Skipped 28, the same as every run since batch 11.

**Load average (1 minute):** 3.69 at the start, 7.74 at the end (another project's tests ran beside it).

**The log:** `strict_b13.log.xz` (8,185,822 bytes unpacked, sha256 `ad891ded56831d8557e0663ed27ee4b338102a997c6e148fea5f0f342d502d40`); `strict_b13.summary.txt` is the script's summary.

**Credential pattern check (0b, `scripts/credscan.py`'s scan function on each file added with this record, values masked):** the record, the script copy and the summary: 0 hits. The packed log: no address with a password part; 6 literal rows (the two known warning lines from the AI module's test, and 4 under names holding only the word KEY), 5 code expressions, 5 placeholder words, 9 variables or masks.

**After the run tip:** the commit that adds this record changes files under `docs/` only.
