# H-223: a teacher's batch starts, runs the files that fit, and parks the rest

The user's decision (2026-10-09 20:26 WAT): "Starts, run the files that fits, parks the rest" on a teacher's BATCH upload; a student's single upload keeps H-180's door and its sentence. The proposal (SM-approved 2026-10-14) and rulings: no file fits -> 402 with no session, as before; "parked" = a FAILURE item coded INSUFFICIENT_CREDITS that the teacher uploads again after a top-up (automatic restart comes with Epic B's waiting files); plain INSUFFICIENT_CREDITS, no new code; no credits_exhausted_at stamp at the door; no migration.

## The change
- b993c13a tests only: students.tests_h223_batch_runs_what_fits, the same six tests on the answer batch (students .../batch-upload) and the assignment upload (assignments upload-async): t1 a dear file in the middle is parked and the others run; t2 the balance runs down; t3 a cheap file after a dear one still runs; t4 no file fits -> 402, no session (green before too); t5 no stamp; t6 one dear file alone -> 402 (green before too). The estimate (the one method the door asks) and the dispatch are replaced; the wallet is 50,000.
- Restored to the real door path (they patched H-180's door out; the SM's order): students.tests_batch_item_results BatchFixture (the door_open parameter and both door patches removed; the teacher's wallet, created EMPTY by users.signals, is funded to 1,000,000 instead) and students.tests_credits_mid_batch AnUploadBatchThatRunsOutOfCredits (its door patch removed; the same funding).
- 3cb3846c fix: assignments.upload_door.parked_files (running balance in upload order; a dear file -> {index: InsufficientCreditsError}; no wallet, a super admin, an unreadable or too-large file count as fitting, as before) and batch_refusal_response (the old teacher 402). Both teacher routes use them; the student route keeps upload_refusal_if_unaffordable.
- **Verifier's F1 on 3cb3846c (found by reading):** each route recorded a park inside its loop, after earlier files were dispatched; launch_processing_task dispatches at once, so a fast worker could mark an earlier item STARTED first, and batch_credit_refusal then read the park as a mid-batch stop (recoded INSUFFICIENT_CREDITS_MID_BATCH, the session stamped, the later fitting files stopped). The tests did not see it: their dispatch never started an item. SM ruling: fix (a), batch_credit_refusal unchanged.
- 9dc121e7 tests: the test dispatch marks each item STARTED (a fast worker).
- f26e9022 fix: both routes record every park right after the session is made, before the loop launches anything.

## Gate list
(a) = every test module that reads the two routes or the door (a read-only `git grep` over 3cb3846c on a6's slot, `chain_f26e9022/test_readers_grep_3cb3846c.txt.gz`, 23 modules) and the guard list: 34 labels. The Verifier named five more readers (a static action map, a paid test skipped without RUN_REAL_AI, three student single-upload modules whose door is unchanged), left to the batch's full run.

## The chain (a6's GRANT 12:32 WAT; logs gzipped in chain_f26e9022/)
Runner row_chain.py sha256 f3fe0bdde92d9065aeaf99aecfc3a2a9c69dc67a9598e561da2faa108d805b8e; config (`config.py.gz`) sha256 e2ba334b77646f2bda3f175faf5a3398e94d2731936a10f58f857a36e3b8e44e, written before the run, pre-read by the Verifier (clear). One systemd-inhibit around the whole chain. Copied from chain.status and the logs:

| Stage | Result |
|---|---|
| (r) the 3 test files from the tip, on 913f248f | "Ran 31 tests in 4.560s", FAILED (failures=8); "8 failing, as expected" (t1, t3, t5 "402 != 202"; t2 "['s0.png', 's1.png'] != ['s0.png']"; both routes); "each for its written reason" |
| (a) 34 labels | "Ran 573 tests in 107.703s", OK (skipped=1) |
| (b) baseline | OK |
| U1 no running subtraction | 2 failing, as expected |
| U2 everything after the first dear file parked | 4 |
| U3a / U3b the door's session stamped | 1 / 1 |
| U4a / U4b whole refusal again | 4 / 4 |
| U5 parked item without a credit code | 6 |
| U6a / U6b no-fit 402 dropped | 3 / 3 |
| U7 the student's upload switched to the per-file door (SM's mutant) | 2 |
| U8a / U8b one route's parks recorded after the launches again (the route file byte-identical to 3cb3846c: the written red reason against 3cb3846c the SM asked for, and his "park after the launches" mutant) | 3 / 3 (t1, t2 "'INSUFFICIENT_CREDITS_MID_BATCH' != 'INSUFFICIENT_CREDITS'", t5 "is not None") |

"(b) every mutant killed, each failing set and reason as written"; "h223 CHAIN END: green as written" at 12:38:00. Rule 24: the row's regression is its batch's full run.
