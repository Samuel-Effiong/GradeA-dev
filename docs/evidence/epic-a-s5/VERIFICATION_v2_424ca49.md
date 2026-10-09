# Verification: Epic A S5 @ 424ca49

**Verifier:** Verification Engineer 2 (v2). **Author:** Security (ed). **Date:** 2026-09-30.
**Branch:** task/epic-a-s5 @ 424ca49, merged with phase2/epic-a d7f2737. Delta: `git diff d7f2737 424ca49`.

Every run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`, RACE_COST 600/200) in 0b's slot, from a scratch worktree detached at 424ca49. Rule 15: v2's probes, mutants and ed's changed modules. The ai_processor regression is ed's committed log (816 OK), not repeated.

**Verdict: VERIFIED-WITH-NOTES.**

## Static review
- **X-5.** `RequestIDMiddleware` always mints the id. An inbound `X-Request-ID` is kept only as a canonical UUID `client_request_id`, which goes to the request, a ContextVar, log lines and the audit `client_correlation_id`; anything else is dropped. The response header is the server id, which is the audit trace id. `celery_signals` propagates only the server id.
- **Chokepoint.** `AIProcessor.__ai_model` is the only provider call in product code. `git grep` for `chat.completions.create` / `OpenAI(` outside tests finds only `services.py` and the `ai_processor/benchmark` dev harnesses. `get_ai_model_function` has no callers. The two direct `__ai_model` calls are inside `execute_graded_task` (superadmin and metered branches) and forward `prompt_version` and `task_type`.
  - Each call sends `X-Request-ID` = the server trace id.
  - It logs one line in `finally`, so failures are logged too, and the line never contains prompt or answer text.
- **Prompt versions.** Every `prompt_version=<x>.version` site receives a real `Prompt`: a module constant or `_load_prompt`. None is built by string operations that would drop `.version`.
  - `BLANK_VERIFICATION_INSTRUCTION.format(numbers=…)` is byte-identical to the old f-string, checked for `[3]`, `[1, 2, 10]` and `['4a', None]`.
- **Rule 14.** ed's provider fake returns real scalars (`SimpleNamespace`).

## Evidence
| Check | Result |
|---|---|
| v2 probes + ed's changed modules (149) | **OK** |
| P1: the id the user sees finds the AI call | the response header `4be9a3a8…5d4a` appears in the `verbose`-formatted ai_call line's `[request_id=…]` prefix, so logs join |
| P2: malformed provider `model` | `None` → requested model logged. A dict → logged as its repr (N3). The call still returns `outcome=ok` |
| v2 mutants (`vf_s5_mutants.py`, 7) | 5 KILLED: version not forwarded (superadmin and metered), task_type not forwarded, served model ignored, client id not reset. **2 SURVIVED: V1** (grading call sites pass `None`) and **V2** (grading call sites pass another prompt's version) |
| ed's gates | 15/15 mutants; ai_processor 816 OK; mypy passed; makemigrations clean (committed) |

## Notes
- **N1 (please fix in a follow-up).** V1 and V2 survive, so the value of the prompt version on the three grading calls, the core of NFR-OBS-04, is not pinned. ed's AST test checks only that the keyword is present. Add a value test, e.g. drive each grading/extraction path with the fake provider and assert that the `ai_call` line's `prompt_version` equals the base prompt's `.version`, or extend the AST test to require `prompt_version=<X>.version` where `<X>` is the prompt the call sends.
- **N2.** One id appears in two string forms: hex (`4be9a3a8…`) in the response header and log prefix, and hyphenated in the ai_call `trace_id=` and the provider's `X-Request-ID`. Joining works through the prefix. One form everywhere would make searching the provider dashboard easier.
- **N3.** A non-string `response.model` from the provider is logged as-is. Log `main_model` unless `response.model` is a `str`. This is also the Gate 5 "malformed external response" case.
- **N4.** `docs/backend/BACKEND_REFERENCE.md:157` (and the diagrams at :1500 and :2282) still say the inbound `X-Request-ID` is reused and echoed. The response header is now always the server's id, which is a contract change the frontend should know about.
- **N5.** `client_request_id` is not propagated to Celery tasks, so worker log lines show `client_request_id=-`.
- **N6.** Grading's version is the base `GRADING_ASSIGNMENT_PROMPT`; the teacher's custom-instruction block appended to it is not versioned. That is reasonable, but it should be stated in EVIDENCE.

Logs: `runs/s5_run1_probes_changed_modules.log`, `runs/s5_run2_mutants.log`. Probe: `ai_processor/tests_vf2_s5_probe.py`.
