# H-220: a withdrawn student is listed on the assignment page only if they submitted for it

The user's decision (2026-10-09 19:21 WAT): "Show withdrawn only if they submitted for that assignment". Built by d5 on top of H-213 (the defined list order); base-updated by a6 onto beta 913f248f (H-213 2df68a52, H-220 0105987f; the row's files byte-identical to the pre-update 05200724). The chain was run by Builder 58 on 2026-10-14 under a6's GRANT (11:33 WAT), after the Verifier's re-read of 0105987f. Under rule 24 the row has no own regression: the batch 17 full run covers it.

## The chain (logs gzipped in chain_0105987f/)

Scripts as run: `chain.sh.txt`, `run_chain.sh.txt`, `expected_kills.py.txt` (the written expectations and rule 22 fragments, written by d5 on 2026-10-09 before any run), and the mutation runner `run_mutants.py` (in this folder). Two comments were corrected in these copies only, after the run (a6's notes): chain.sh said "the battery of 6 mutants" (there are 8, R1-R8, and EXPECTED covers all 8); expected_kills.py named the base as 8170f7ec (the run used 2df68a52, whose H-213 files are byte-identical). No logic was changed. One systemd-inhibit around the whole run_chain.sh (a6's start condition), memory-capped scope and timeout per step.

Copied from chain.status and the logs:

| Stage | Result |
|---|---|
| (r) the 3 row modules from the tip, on 2df68a52 | 11:33:19-11:33:41; "Ran 11 tests", FAILED (failures=5), exit=1, wall_s=22; "the red set is the expected one, each for its written reason" ({W2, W3, C2, L1, F1}) |
| (a) 44 labels incl. AutoGrader.tests_cache_bespoke_1114 and the guard list | "Ran 506 tests in 216.965s", OK (skipped=9), exit=0, wall_s=238 |
| (b) baseline + R1-R8 | baseline "Ran 11 tests", OK; R1-R8 KILLED with FAILED (failures=5, 2, 1, 3, 11, 1, 1, 1); "every mutant's failing set is the expected one"; "8/8 killed with verified restore", wall_s=75 |

"H-220 CHAIN END: (c) needs its own GRANT" at 11:38:54; (c) was not asked for (rule 24). Afterwards the H-220 worktree was clean at 0105987f and the throwaway trees h220-repro and h220-mut were gone.
