# Batch 15: the module gate and the one strict full run, green

Recorded by the Release Engineer (0b), 2026-10-09. 0b started both runs and read their results.

**What batch 15 is:** the pushed beta tip `8567a7a0` (batch 14) plus four verified rows, each merged as its final tip, then the docs commit `8d09c754` (docs/HARDENING_BACKLOG.md only). Rows: the score-printing stack (H-139, H-140, H-142, H-144, H-145, H-146), merge `d89bbf2d` of `099870f8` (a clean base update onto beta of the stack that Verifier 2 verified at `2ee3cbee`, code under test `c8ec622d`); H-196, one defined order for enrolment lists, merge `f0b98ab7` of `3f53bced` (Verifier 2 VERIFIED-WITH-NOTES at `2f9dfc1d`, a clean base update of the gated `c388dfd9`); H-180, a student's refused upload gets a fixed sentence, merge `ebb243d0` of `22090331` (Verifier 1 VERIFIED-WITH-NOTES at `4216575f`, a clean base update of the gated `90b5c763`); H-179, a cut-off AI reply is logged once per metered call, merge `bff4a844` of `c0294cc9` (Verifier 2 VERIFIED-WITH-NOTES at `e61cbe5a`, a clean base update of the gated `021c8f7e`). Each verifier's record is committed byte for byte (Verifier 2 compared the stack 35 of 35, H-196 21 of 21 and H-179 16 of 16; Verifier 1 compared H-180's 14 blobs after the mechanical changes d5 named). The trial `git merge-tree` of the four branches into beta before any merge was clean; the merges were conflict-free. H-211 (stacked on H-180) is built and gated (c_h211 Ran 5884 OK) but not verified, so it is NOT in this batch (the Senior Manager's closing rule); H-203, H-201 and H-206 are not either.

**Module gate (0b, `md_b15_modules.sh bff4a844`, a copy beside this file):** makemigrations "No changes detected", then the test modules of the four rows and the cache race test that H-196 is about (`ai_processor.tests_finish_reason`, `classrooms.tests_enrollment_list_order`, the five score-printing modules of `students`, `students.tests_upload_credit_door`, `billing.tests.test_refusal_handling`, `AutoGrader.tests_cache_commit_race`): 2026-10-09 13:11:44 to 13:12:39, **Ran 156 tests, OK**, 0 FAIL, 0 ERROR (`md_b15_modules.log`).

**Command:** the strict full run under rules 12, 13, 16 (revised), 17 and 18, by `strict_bundle.sh 8d09c754 b15 Grade-Automator-Plus-beta-batch-15 task/beta-batch-15` (a copy is beside this file).

| Tip | Suite (WAT) | Result |
|---|---|---|
| `8d09c754` | 2026-10-09 13:14:33 to 13:22:20 (429 s of tests; 467 s wall) | **Ran 6455 tests, OK (skipped=28)**, exit 0, 0 FAIL, 0 ERROR, 0 blocked outbound, no suspend, watchdog never fired; mypy Passed and makemigrations "No changes detected" by the script's own pre-checks |

**Tests per app** (they sum to 6455): ai_processor 861, assignments 710, AutoGrader 692, billing 2158, classrooms 487, dashboard 270, students 520, users 757. Against batch 14's 6356: ai_processor +9, classrooms +8, students +82; every other app is unchanged. Skipped 28, as in every run since batch 11.

**Load average (1 minute):** 2.05 at the start (under the 4.0 line), 4.68 at the end. No timing test went red.

**The log:** `strict_b15.log.xz` (8,175,617 bytes unpacked, sha256 `f93947789c65dd0f56239de01271340dd07cac9bb9a1e75fbddf46b7463d041e`); `strict_b15.summary.txt` is the script's summary.

**Credential pattern check (0b, `scripts/credscan.py`'s scan function on each file added with this record, values masked):** no address with a password part in any file; the packed log's literal rows are names holding only the word KEY (cache keys: 5) and the two known warning lines from the AI module's test; the rest are code expressions, placeholder words and variables or masks, the same kinds as batches 13h and 14. The record, the script copies, the summary and the module-gate log: 0 hits.

**Not shown by this run:** the student site's handling of the 402 body; a batch whose first file succeeds and second is refused; whether production logs let anyone count the cut-off lines of H-179; format_grade is queued nowhere; the lock against a concurrent grade is untested with two connections (all named in the rows' evidence).

**After the run tip:** the commit that adds this record changes files under `docs/` only.
