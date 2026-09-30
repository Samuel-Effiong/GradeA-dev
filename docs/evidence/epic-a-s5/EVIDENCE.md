# Epic A completion S5: the trace id reaches every AI call; model and prompt version on every call

Branch `task/epic-a-s5`. Cut from phase2/epic-a `cc34081` (plan 08 says S5 has no dependency on S1), then merged with phase2/epic-a `d7f2737` (S1 + the batch-2a merge-down) as `cb41156`, so the gates run on the combined tree. After v2's VERIFIED-WITH-NOTES at `424ca49` (`VERIFICATION_v2_424ca49.md`): `44dac06` closes N1, then phase2/epic-a `75bf91a` (S6a) is merged in and `6fef60a` pins the error reference. The gates below are re-run on `6fef60a`. Phase 2 only. The verifier is v2. No migration.

Plan: `08_epic_a_completion_plan.md` §6 (FR-A-03, NFR-OBS-04), plus **part 0 (X-5)**, ordered by the SM because S5 is the trace-id slice.

## Part 0: the trace id is always the server's (X-5)
**Defect.**
- `RequestIDMiddleware` adopted any inbound `X-Request-ID` matching `[A-Za-z0-9._-]{1,128}` as the request id.
- `audit.emitter._resolve_trace_id` uses that id as the audit trace id whenever it parses as a UUID.

So a client that sent another action's trace id as its `X-Request-ID` placed its own events in that action's trail, which is exactly what X-5 exists to prevent. The existing unit test (`audit.tests_emitter.TraceIdTest`) called the emitter directly, skipping the middleware, so it never saw this.

**Fix (SM rulings).**
- `RequestIDMiddleware` always mints the id.
- An inbound `X-Request-ID` that is a UUID is kept **only** as `client_request_id`, in canonical form:
  - `request.client_request_id`;
  - a `client_request_id` ContextVar;
  - every log line, via `RequestIDLogFilter` and the log formats;
  - the audit row's `client_correlation_id`.

  Anything else is dropped, so no free text reaches the logs or the audit.
- The response `X-Request-ID` header is the server id, which is the audit trace id (the QA-ERR-04 join).
- **With S6a merged:** a coded error body's `reference` (QA-ERR-04) is read from the request id, so before S5 it echoed the client's inbound id. On S5 it is the server id, the same as the response header and the audit trace (`CodedErrorReferenceTests`).
- `docs/backend/BACKEND_REFERENCE.md`: the inbound `X-Request-ID` is no longer echoed. This is a Phase 2 contract change for the frontend.

**Beta.** `RequestIDMiddleware` is the same on beta. Beta has no audit trace, so there the inbound id only reaches log correlation (and Sentry's tag). That goes to the backlog, not a hotfix, per the SM.

**Tests.**
- `audit/tests_trace_server_owned.py` goes through the real middleware stack:
  - a client can't join another action's trail;
  - the response header is the server trace id;
  - a non-UUID inbound id is dropped;
  - log lines carry the server id and the client id apart.
- Updated on purpose:
  - `AutoGrader.tests_middleware`: "reuses a valid inbound header" becomes "never adopts an inbound id", plus the non-UUID case;
  - `audit.tests_emitter`: the client id must be a UUID.

## Part 1: the AI provider call (plan §6)
- **One chokepoint.** `AIProcessor.__ai_model` is the only place a provider call leaves the app; its two near-identical `create` branches are folded into one. Every call:
  - sends `X-Request-ID: <server trace id>` beside `HTTP-Referer` and `X-Title`;
  - writes **one** log line: `ai_call trace_id=… model=<the model that served> prompt_version=… task_type=… attempt=… latency_ms=… outcome=ok|<ExceptionClass>`, and **never** prompt or answer text.
  - `attempt` is Celery's retry count + 1 inside a task, else 1.
- **Prompt versions.** `_load_prompt` returns a `Prompt(str)` carrying `.version = "<file stem>:<first 8 hex of sha256(text)>"`, so an edit without a rename still changes the version. The separator is ":", not "@": the audit metadata sanitiser drops anything shaped like an email (`\S+@\S+`), which the grading-event tests caught.
  - The inline blank-answer verification instruction is now a module-level `Prompt` template, versioned over its text.
- **Required everywhere.** `execute_graded_task(..., *, prompt_version)` is keyword-only and required; an empty value raises `ValueError`. All 16 callers pass their prompt's `.version`. Grading passes `GRADING_ASSIGNMENT_PROMPT.version`, because the custom-instructions block appended per assignment is data, not the prompt.
- **Grading events.** `GRADING_COMPLETED` (which already carried `model`) and `GRADING_FAILED` carry `prompt_version`. `GRADING_FAILED`'s allow-list gains it; plan 08 said it was already there, but only COMPLETED had it.
- **Not in the app path:** `ai_processor/benchmark/isolation_run8/isolation_harness.py` builds its own OpenAI client. It's a dev benchmark tool, left as is.

**Tests.** `ai_processor/tests_ai_call_trace.py`:
- the provider receives `X-Request-ID`;
- one log line with every field and no prompt or answer text;
- a failed call is logged with its error class and still raises;
- a retried task logs attempt 3;
- the AI call carries the server id, not an inbound one (X-5 through the real middleware);
- **the Celery hop**: a real in-memory worker, the pattern of `AutoGrader.tests_celery_signals`. The log call's own arguments are recorded, because starting a worker reconfigures logging;
- an AST check that every `execute_graded_task` call passes `prompt_version`, and that omitting it is refused;
- **v2's N1:** an AST value map (`EXPECTED`) pins **which** prompt's `.version` each of the 16 callers passes, and a grading-site test pins the grading prompt's version on the three grading calls;
- prompt versions: format, and a text edit changes the version.

`assignments.tests_grading_audit_events` pins `prompt_version` on both grading events. The 23 existing direct calls in tests pass a test version.

## Gates (rule 15: changed modules + mutation + ONE owning-app regression; logs committed)
Re-run on `6fef60a` (S5 + S6a `75bf91a`). Every run was wrapped in `systemd-run MemoryMax=6G`, `nice -n 10`, a timeout, RACE_COST 600/200, `EXEMPT_EMAIL_DOMAINS=` and `--noinput`, one at a time.

| Gate | Result |
|---|---|
| Reproduce-first | `75bf91a`'s source for the 7 changed files against the new tests (`prefix_75bf91a_failing.txt`): **5 failures, 1 error**. The 4 X-5 tests fail, and so does the error-reference test (S6a's `reference` is the client's id there). `tests_ai_call_trace` fails to import (`Prompt` doesn't exist there). The first prefix, on `d7f2737`, is kept (`prefix_d7f2737_failing.txt`). |
| Changed modules | `tests_ai_call_trace`, `tests_trace_server_owned`, `AutoGrader.tests_middleware`, `AutoGrader.tests_request_context`, `audit.tests_emitter`, `assignments.tests_grading_audit_events`, `billing.tests.test_execute_graded_task`: **150 OK** (`changed_modules.txt`) |
| 2 Mutation | `mutate.py`, **17 mutants, 17 killed**, anchors asserted unique (`mutation_log.txt`, `mutation_results.json`). X1–X4 cover part 0; T1–T6 the AI call; P1–P5 prompt versions and the grading events; V1–V2 are v2's two survivors (a grading site passing `None`, or another prompt's version). |
| 1 Regression (owning app) | `ai_processor`: **818 OK** (skipped=6). The repo copy is trimmed to its last 200 lines; full log in `~/Documents/Projects/GAP-evidence-logs/epic-a-s5_regression_ai_processor_6fef60a_full.txt` |
| mypy | whole-repo `pre-commit run mypy --all-files`: **Passed** (on `6fef60a`) |
| Migrations | `makemigrations --check --dry-run`: **No changes detected** |
| 3 Concurrency | the Celery hop, with a real in-memory worker in its own thread |
| 7 Real infra | Celery: the real signal handlers and a real (in-memory broker) worker. Provider: faked by design; the H-39 guard blocks real calls. |

**Found and fixed while running the gates:**
- The version separator "@" was dropped as email-shaped by the audit metadata sanitiser; it is now ":".
- The Celery-hop test first used `assertLogs`, which never saw the worker thread's record because starting a worker reconfigures logging; it now records `_log_ai_call`'s arguments.
