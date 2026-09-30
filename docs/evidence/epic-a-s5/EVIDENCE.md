# Epic A completion S5: the trace id reaches every AI call; model and prompt version on every call

Branch `task/epic-a-s5`. Cut from phase2/epic-a `cc34081` (plan 08 says S5 has no dependency on S1), then merged with phase2/epic-a `d7f2737` (S1 + the batch-2a merge-down) as `cb41156`, so the gates run on the combined tree. Phase 2 only. The verifier is v2. No migration.

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
- This tree has no error body carrying a `reference`, so the header is the only place the id goes back to the client.

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
- prompt versions: format, and a text edit changes the version.

`assignments.tests_grading_audit_events` pins `prompt_version` on both grading events. The 23 existing direct calls in tests pass a test version.

## Gates
_pending_ (runs through 0b, rules 12–14).
