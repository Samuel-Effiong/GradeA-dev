# Verification: Epic A S2 R1 @ e101bfa, S1b @ 65aab2e, S5 R2 @ d189f61

**Verifier:** Verification Engineer 2 (v2). **Author:** Security (ed). **Date:** 2026-09-30.
Every run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`, RACE_COST 600/200) in 0b's slot, from a scratch worktree detached at each sha. Rule 15: v2's probes, mutants and changed modules; the author's committed regressions are cited, not repeated.

---

## S2 R1 @ e101bfa: **VERIFIED**
Both of the REJECTED 883ee93 items and the SM's N1 ruling are done.

| Check | Result |
|---|---|
| v2 probes (`tests_vf2_s2r1_probe.py`) + `audit.tests_route_coverage`, `users.tests_auth_audit_doors`, `audit.tests_state_change`, the S1 probes, `AutoGrader.tests_reason_codes` | **118 OK** |
| G1: own URLconf with an unnamed `AllowAny` POST route | `unnamed_write_routes()` → `['vf2-r1/unnamed/']`, which the guard's real-URLconf test requires to be `[]` |
| N1: `POST /auth/register` with the serializer raising (500) | exactly `[ACCOUNT_REGISTER, FAILURE, SYSTEM, SERVER_ERROR, ANONYMOUS]` |
| A crashed anonymous non-door write (500) | exactly `[STATE_CHANGE, FAILURE, SYSTEM, SERVER_ERROR, ANONYMOUS]`, metadata `{route, method, http_status}`, body sentinel absent |
| A refused excluded route (stripe-webhook, 400) | `[]` |
| Catalogue | `INVALID_REQUEST` and `SERVER_ERROR` are in `ReasonCode` at e101bfa |
| v2 mutants (`vf_s2r1_mutants.py`) | **2/2 KILLED**: the crash/refusal branch applied to signed-in requesters (a double event beside S1's); safe methods recorded |
| ed's gates | 11/11 mutants (G1, N1, N2 included); audit 230 OK; mypy passed (committed) |

---

## S1b @ 65aab2e: **VERIFIED-WITH-NOTES**
Built to the SM's re-ruling, including v2's H1/H2/N1–N3 and the DENIED flag.

| Check | Result |
|---|---|
| v2 probes (`tests_vf2_s1b_probe.py`, through the real routes, caps floor 2 / target 4 / global 6) + `audit.tests_failed_auth_cap`, route coverage, doors, state_change, emitter, the S2 R1 probes, `AutoGrader.tests_reason_codes` | **198 OK** |
| P1: lock-then-spray (a locked account, 25 logins from 25 IPs) | **4** individual DENIED rows (= the target limit). Summaries are DENIED at `suppressed_so_far` 1 and 10, `cap=target`, each carrying the account as target and **its school** |
| P2 (H1): 20 junk-email failures past the global cap, then the real account | the real account's **2** floor failures are all written |
| P3 (H2): a signed-in change-password with the wrong current password ×12 | 12 `AUTH_LOGIN FAILURE WRONG_PASSWORD`; no summary, no STATE_CHANGE |
| P4: `cache.incr` raising (Redis down) ×10 | 10 individual events written (fails open) |
| Catalogue | `FAILED_AUTH_CAPPED`, `SERVER_ERROR` and `INVALID_REQUEST` are in `ReasonCode` at 65aab2e. `STATE_CHANGE`'s allow-list carries the summary keys, so a capped crash summary keeps its counts |
| v2 mutants (`vf_s1b_mutants.py`) | **2/2 KILLED**: summary `school_id` dropped (**killed only by v2's P1**); a DENIED summary recorded as FAILURE |
| ed's gates | 15/15 mutants; audit 246 OK; mypy passed (committed) |

**Notes:**
- **N1.** Mutant B1 (the summary's `school_id` set to None) survives ed's labels and is killed only by v2's P1. The summary's school scoping is what lets a school admin see that its account is under attack, so pin it: adopt P1's school assertion.
- **N2.** `audit/failed_auth_cap.py`'s module docstring still says "Never capped: … a DENIED event". DENIED events are now floor + per-target capped. Update the docstring.
- **N3.** FAILURE and DENIED events share one per-target counter, so once the floor is spent a lock-then-spray is bounded at the target limit (P1). That is intended; state it in EVIDENCE.

---

## S5 R2 @ d189f61: **REJECTED** (integration)
The V1/V2 value tests work: re-anchored V1 (a grading site passes None) and V2 (another prompt's version) are **KILLED** by `test_each_caller_passes_its_own_prompts_version` and `test_the_grading_sites_send_the_grading_prompts_version`. All 7 v2 S5 mutants are killed. `BACKEND_REFERENCE.md:159/162/2268` now describe the server-owned id and the frontend contract change.

**D1 (defect): S5's merged tree fails S6a's QA-ERR-04 test.**

| Labels at d189f61 | Result |
|---|---|
| v2 S5 probe + ed's S5 changed modules + **`AutoGrader.tests_reason_codes`** (S6a's, now on this tree) | 181 run, **1 FAIL** |

`AutoGrader.tests_reason_codes.CodedEnvelopeThroughTheAPITests.test_qa_err_04_an_inbound_request_id_is_the_reference` sends `X-Request-ID: 3f2b9c1e-…` and asserts `envelope["reference"] == inbound`. On S5 it gets the server id `96b8b822…`. X-5 (the SM's order) makes the reference server-owned, and ed's `CodedErrorReferenceTests` asserts exactly the opposite of S6a's test. ed's changed-module set did not include `AutoGrader.tests_reason_codes`, so the merge was never run against it.

**Required:**
1. An SM ruling on QA-ERR-04 against X-5. The recommendation is X-5 wins: the `reference` is the server id, which is also the response `X-Request-ID`, and a client's own UUID stays `client_request_id`.
2. S6a's test updated to that ruling, e.g. assert `reference == response["X-Request-ID"]` and `!= inbound`, with the QA-ERR-04 wording in 08a and EVIDENCE aligned.
3. A re-run of `AutoGrader.tests_reason_codes` on the S5 tip.

Logs: `runs/s2r1_*`, `runs/s1b_*`, `runs/s5r_*`.
