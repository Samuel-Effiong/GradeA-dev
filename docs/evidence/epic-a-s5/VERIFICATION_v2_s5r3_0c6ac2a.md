# Verification: Epic A S5 R3 @ 0c6ac2a (combined with phase2/epic-a 8de4376)

**Verifier:** Verification Engineer 2 (v2). **Author:** Security (ed). **Date:** 2026-09-30.
**Branch:** task/epic-a-s5 @ **0c6ac2a** (R3 = 1dedac3 plus evidence only, on d189f61). The SM ruled on v2's D1 that X-5 wins.

**Tree verified:** a scratch worktree detached at phase2/epic-a **8de4376** (S1, S2 R1, S1b, S6a) with `git merge --no-commit --no-ff 0c6ac2a`. The merge was clean (emitter.py and metadata.py auto-merged), no branch was moved, and the merge was aborted afterwards. S2/S1b's audit paths therefore meet S5 exactly as 0b's merge will produce them.

Every run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`, RACE_COST 600/200) in 0b's slot. Rule 15: probes, changed modules and mutants only; ed's ai_processor regression (818 OK at 6fef60a) is cited, since R3 is test and doc only.

**Verdict: VERIFIED.** D1 is fixed.

## Evidence
| Check | Result |
|---|---|
| Auto-merged `audit/emitter.py` / `audit/metadata.py` | every slice's pieces are present: S5 (`client_request_id_from_header`, `resolve_trace_id`), S1 (`record_stored_event`), S1b (`_failed_auth_cap_scope`), S6a (the `ReasonCode` check); the allow-lists keep `lock_triggered`, the S1b summary keys and `prompt_version` (grading events); both parse |
| **`AutoGrader.tests_reason_codes`** (the test module D1 found failing) + v2's S1/S1b/S2 R1/S5/S6a probes + the S5, S2 and S1b changed modules + `users.tests_renderers` / `tests_exception_handler` | **307 OK**, including `test_qa_err_04_an_inbound_request_id_is_never_the_reference` |
| The probes on the combined tree | S1 V1 add_teachers names the admin; S2 R1 register-500 → one SERVER_ERROR; S1b lock-then-spray → 4 DENIED + DENIED summaries at 1 and 10 with the school; S5 P1: the response-header id finds the ai_call line |
| Mutants on the combined tree (`vf_s5r3_combined_mutants.py`, restored and sha-checked against the index) | **3/3 KILLED**: V1 (a grading site passes None) and V2 (another prompt's version), both by the value map and the grading-site test; **X5v** (`reference` = the client id when present), by `test_qa_err_04_an_inbound_request_id_is_never_the_reference` and `test_the_error_reference_is_the_server_id` |
| ed's R3 gates | prefix 6F on 75bf91a; 178 OK with tests_reason_codes; 18/18 mutants, X5 included (committed) |

The QA-ERR-04 wording is aligned in the `tests_reason_codes` docstring, the `reason_codes.py` envelope comment and 08a (the SM).

Logs: `runs/s5r3_combined_run1.log`, `runs/s5r3_combined_run2_mutants.log`.
