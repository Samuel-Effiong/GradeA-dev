# Verification: Epic A S6a @ 99f219a

**Verifier:** Verification Engineer 2 (v2). **Author:** Hardening (d5). **Date:** 2026-09-30.
**Branch:** task/epic-a-s6a @ 99f219a (off phase2/epic-a 3bbafdd, with d7f2737 merged in). Design: 08a §4–§5, adopted by plan 08 §7.

Every run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`, RACE_COST 600/200) in 0b's slot, from a scratch worktree detached at 99f219a. Rule 15: v2's probes, mutants and the changed modules. d5's 18-module related set (421 OK) and users app run (657 OK) are cited from the committed logs, not repeated.

**Verdict: VERIFIED-WITH-NOTES.** N1 is a merge-order requirement for 0b and ed.

## Evidence
| Check | Result |
|---|---|
| v2 probes + `AutoGrader.tests_reason_codes` + the whole `audit` app + `users.tests_renderers` + `users.tests_exception_handler` | **259 OK** |
| P1: a named event with a code outside the catalogue | the emitter rejects it (returns None, alert). For a signed-in requester, S1's generic event still records the request: `[STATE_CHANGE FAILURE]`, so the write is never untraced |
| P2: user-controlled param `"{0}{file_name}{__class__}.pdf"` | renders literally: "We couldn't read {0}{file_name}{__class__}.pdf. …"; 422; `params` echoed as given; `reason_code` FILE_UNREADABLE |
| v2 mutants (`vf_s6a_mutants.py`, 5) | **5/5 KILLED**: params scalar check off; missing-param check off; renderer hides envelope keys on non-coded dicts; status ignores the spec; `Retry-After` dropped |
| Reason codes in use (AST scan of non-test code at 99f219a) | all 17 literal codes, plus the login serializer's conditional (`WRONG_PASSWORD`/`INVALID_CREDENTIALS`), are in `ReasonCode` |
| d5's gates | 10 mutants (9 plus M8 closed); related 421 OK; users 657 OK (committed) |

**Checked and sound:**
- `coded_body` never carries `detail`. The message comes from the spec plus whitelisted scalar params. `format()` interprets only the template, so param values cannot inject.
- `refusal_response` / `_failure_response` / `custom_exception_handler` route both refusals and any `CodedError` through `coded_response`. The legacy lowercase `code` is kept for the two refusals (F8).
- The renderer hides envelope keys only when a string `reason_code` marks the body. Batch-2a's reset-lock 429 (`code: "RESET_LOCKED"`, no `reason_code`) renders exactly as before.
- `@require_ai_access` maps balance reasons to the credits refusal (402) and everything else to the plan refusal (403), without the internal reason. It is applied nowhere today (latent).
- Rule 14: no mocks. One patch replaces `can_user_access_ai` with a plain function.

## Notes
- **N1 (merge order, REQUIRED).** The emitter now refuses any `reason_code` outside `ReasonCode`. Two codes on ed's unmerged branches are not in the catalogue:
  - `FAILED_AUTH_CAPPED` (S1b, `audit/failed_auth_cap.py:51`), found by an AST scan of ec8cf58 against this enum;
  - `SERVER_ERROR` (the SM's S2 N1 ruling, not yet built).

  Whichever slice lands second must add them. Otherwise every S1b summary is rejected, and because the request is already marked suppressed, a capped failed sign-in leaves **no event at all**. ed's S1b summary tests would fail on rebase, so this should not ship silently, but it needs to be planned.
- **N2.** `reference` is `get_request_id()`. On this tree (without S5) `RequestIDMiddleware` still adopts an inbound `X-Request-ID`, so `reference` can echo a client-chosen id. After S5 merges it is always the server's id.
- **N3.** The batch-2a reset-lock 429 is not yet a coded envelope (it has a string `code`, no `reason_code`/`error_class`/`remediation`). This is consistency work for a later S6 slice.
- **N4.** A code outside the catalogue at an anonymous door is dropped. After S2 merges, the door fallback records INVALID_REQUEST instead, which is the right count but a misleading reason. The completeness scan in d5's tests is the guard.

Logs: `runs/s6a_run1_probes_changed.log`, `runs/s6a_run2_mutants.log`. Probe: `AutoGrader/tests_vf2_s6a_probe.py`.
