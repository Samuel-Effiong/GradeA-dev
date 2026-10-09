# Batch 14: the module gate and the one strict full run, green

Recorded by the Release Engineer (0b), 2026-10-09. 0b started both runs and read their results.

**What batch 14 is:** the pushed beta tip `5e37ac2b` (batch 13h) plus five rows, each merged as its verified record commit, then the docs commit `38195d20` (docs/HARDENING_BACKLOG.md only). Rows: H-181, the wallet is locked before any bucket (`cacc2141`, of `6fe3a08e`, Verifier 1 VERIFIED-WITH-NOTES at `5b0ddded`); H-178 Part A, a cancellation Stripe schedules for a date is mirrored (`12290a29`, of `f4d77b84`, Verifier 2 VERIFIED-WITH-NOTES at `26e16742`); H-164, the reset and email-code roads (`f898bfe4`, of `e11a0083`, Verifier 1 VERIFIED-WITH-NOTES at `b6282c7f`, `42742c40` and `f12f12d0`); H-182, the licence roll-up after the commit (`ed213782`, of `44da6f58`, Verifier 1 VERIFIED-WITH-NOTES at `046a90f6`); H-202, switched off means out (`9201dbc0` of `eba65a42`, then ed's docs-only final tip `13e79e12` as `403980bc`; Verifier 1 VERIFIED-WITH-NOTES at `5ad21e96`). The branch was first brought up to beta `5e37ac2b` by a clean base update (`94c3b3b2`; it had stood on `3f2ad13e` plus H-181). All merges conflict-free (`git merge-tree` first). The score-printing stack, H-196, H-180, H-179, H-201, H-203 and H-206 are NOT in this batch (Senior Manager's cut rule: the batch closes when H-202 is verified and its users-app regression is green).

**Module gate (0b, `md_b14_modules.sh 403980bc`, a copy beside this file):** makemigrations "No changes detected", then the lock-order modules (`billing.tests.test_wallet_lock_first`, `billing.tests.test_licence_rollup_after_commit`), the modules of H-178 Part A, the two billing consumption modules H-182 touches, and the sign-in modules of H-164 and H-202 together (`users.tests_reset_for_an_invited_student`, `users.tests_switched_off_means_out`, which touch the same views): 2026-10-09 10:08:23 to 10:09:04, **Ran 115 tests, OK**, 0 FAIL, 0 ERROR (`md_b14_modules.log`).

**Command:** the strict full run under rules 12, 13, 16 (revised), 17 and 18, by `strict_bundle.sh 38195d20 b14 Grade-Automator-Plus-beta-batch-14 task/beta-batch-14` (a copy is beside this file).

| Tip | Suite (WAT) | Result |
|---|---|---|
| `38195d20` | 2026-10-09 10:11:05 to 10:20:33 (531 s of tests; 568 s wall) | **Ran 6356 tests, OK (skipped=28)**, exit 0, 0 FAIL, 0 ERROR, 0 blocked outbound, no suspend, watchdog never fired; mypy Passed and makemigrations "No changes detected" by the script's own pre-checks |

**Tests per app** (they sum to 6356): ai_processor 852, assignments 710, AutoGrader 692, billing 2158, classrooms 479, dashboard 270, students 438, users 757. Against batch 13h's 6252: billing +28 (H-181's, H-178 Part A's and H-182's new tests), users +76 (H-164's and H-202's); every other app is unchanged. Skipped 28, as in every run since batch 11.

**Load average (1 minute):** 1.93 at the start, 7.44 at the end (a browser and other work beside it after the run).

**The log:** `strict_b14.log.xz` (8,083,070 bytes unpacked, sha256 `dcad3a9ae5f6d1811725ff81789783ef2a0afe040ac7a6cb555b64d9ebc5739b`); `strict_b14.summary.txt` is the script's summary.

**Credential pattern check (0b, `scripts/credscan.py`'s scan function on each file added with this record, values masked):** the record, the two script copies and the summary: 0 hits. The packed log and the module-gate log: no address with a password part; the literal rows are the two known warning lines from the AI module's test and names holding only the word KEY (cache keys: 5 in the packed log, 1 in the module log), plus code expressions, placeholder words and variables or masks, the same kinds as batch 13h.

**After the run tip:** the commit that adds this record changes files under `docs/` only.
