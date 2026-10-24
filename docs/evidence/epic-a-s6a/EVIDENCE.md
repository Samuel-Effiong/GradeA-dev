# Epic A S6a: the reason-code catalogue and its error envelope (FR-A-06)

Branch `task/epic-a-s6a` off `phase2/epic-a` `3bbafdd`, with `phase2/epic-a` `d7f2737` (the batch-2a merge-down) merged in. Author: Hardening (d5). Design: `docs/phase2/architecture/08a_epic_a_s6_s7_reason_codes_and_batch_design.md` §4–§5, adopted by plan 08 §7. Verifier: v2.

## What S6a delivers (08a §5, first row)

| Piece | Where |
|---|---|
| `ReasonCode`: the 10 FR-A-06 codes (`FR_A_06_CODES`), the two refusals kept alongside (`INSUFFICIENT_CREDITS`, `AI_FEATURE_NOT_AVAILABLE`), `NOT_RETRYABLE`, and the 18 sign-in and session codes already emitted (Epic A S1, plus S2's `INVALID_REQUEST` ahead of S2 landing, per ed) | `audit/enums.py` |
| `REASON_CODES`: one spec per user-facing code (error class, status, message template, remediation, retryable, allowed params, defaults, `Retry-After`). `AUDIT_ONLY_CODES` holds the sign-in and session outcomes, which the auth views answer. `CodedError`: whitelisted scalar params, message rendered from the spec, `detail` kept server-side. `coded_response` / `coded_body` | `AutoGrader/reason_codes.py` |
| `refusal_response` delegates to `coded_response` and keeps its None-for-other contract. The superseded (type, status, code) table is removed | `billing/refusals.py` |
| The global handler answers any coded failure (not only refusals) with its own status, never a 500 | `users/exceptions.py` |
| `_failure_response` tries `coded_response` first | `students/views.py` |
| `flatten_errors` hides the envelope keys when a string `reason_code` marks the dict as a coded body, so `message` stays one sentence. A plain serializer error with a field named like a key is untouched | `users/renderers.py` |
| A `CodedError`'s message passes through as the display text. It counts as user-facing only for the USER and VALIDATION classes (a PROVIDER failure is not a 400) | `AutoGrader/error_messages.py` |
| The emitter accepts only a `ReasonCode` as `reason_code`, so the vocabulary is closed. This replaces the UPPER_SNAKE regex | `audit/emitter.py` |
| `@require_ai_access` answers with the coded body (SM option A). Its two balance reasons become 402 `INSUFFICIENT_CREDITS`, as `execute_graded_task` maps them; anything else becomes 403 `AI_FEATURE_NOT_AVAILABLE` with the spec's fixed message. The internal reason stays in the log | `billing/access_control.py` |

## The envelope (F7: in `error.field_errors`)

```json
{"success": false,
 "message": "<the display sentence>",
 "error": {"field_errors": {
   "error": "<the display sentence>",
   "reason_code": "FILE_TOO_LARGE",
   "error_class": "USER",
   "remediation": "Split the file, ...",
   "retryable": false,
   "params": {"file_name": "p07.pdf", "actual": "312 pages", "limit": "300 pages", "dimension": "pages"},
   "reference": "<the request's X-Request-ID>",
   "code": "insufficient_credits"}}}
```

- `code` appears for the two old refusals only (F8). **Remove it in the release after S6 ships.**
- `reference` equals the response's `X-Request-ID` header (QA-ERR-04). It is still also sent as the header.
- `PROVIDER_FAILURE` adds a `Retry-After` header.

## Frontend impact (F7: the frontend confirms on staging before beta)

1. **The two refusals** (402 credits, 403 plan): the body gains `reason_code`, `error_class`, `remediation`, `retryable`, `params` and `reference` next to the unchanged `error` and `code`. This is additive.
2. **`@require_ai_access`**: the body changes shape. It was `{"detail": "AI access denied: <reason>", "reason_code": "<reason, lowercased>"}` with a 403 for every reason. It is now the envelope above, with 403 `AI_FEATURE_NOT_AVAILABLE`, or 402 `INSUFFICIENT_CREDITS` for the two balance reasons. The decorator is applied to no route today (every use in `assignments/views.py` is commented out), so no live response changes.
3. **Statuses** for the file, identity and grading codes (415 / 422 / 409 / 503) arrive with S6b–S6d, not here.

## Stated gaps (from 08a §6 and plan 08 §7)

- `DUPLICATE_SUBMISSION` is defined but not raised (F3).
- `SUBMISSION_EMPTY` covers empty files only (F6 / §6.1).
- `INSUFFICIENT_CREDITS_MID_BATCH` has a spec but is raised only in S7c. Its 402 applies only if it is ever answered synchronously.

## Tests (`AutoGrader/tests_reason_codes.py`, `audit/tests_emitter.py`)

- **Completeness:**
  - every code is user-facing or audit-only, never both;
  - the ten FR-A-06 ids are present;
  - every value is UPPER_SNAKE;
  - every spec is consistent (its placeholders are allowed params, and its remediation has none);
  - only the two old refusals carry a legacy code;
  - **a scan of every reason code the production code emits** (the `reason_code=` keyword, `sign_in_failed`'s code argument, `*_reason` assignments, `audit_failure` tuples) finds nothing uncatalogued, with a guard-on-guard for each pattern.
- **`CodedError`:** the message comes from the spec, defaults are filled, `detail` stays out of the message, and unknown, non-scalar or missing params are refused. An audit-only code has no body. A subclass fixes its code. The user-facing check follows the error class. `refusal_response`'s contract holds.
- **Through the real API** (a test URLconf whose view raises each failure, with the project's middleware, handler and renderer):
  - every user-facing code answers its own status and envelope;
  - QA-ERR-03: a sentinel in the chained cause and in `detail`, "Traceback" and the class names are absent from every body, including the refusals;
  - QA-ERR-04: `reference` equals `X-Request-ID`, and an inbound id is honoured;
  - the old refusals keep their message and legacy code;
  - a view that answers `coded_response` itself gets the same envelope;
  - a plain serializer error is not mistaken for an envelope;
  - `@require_ai_access` carries no reason text, and each balance reason gives a 402.
- **The emitter:** a well-formed code outside the catalogue is rejected.
- No mocks (rule 14). The one patch in the decorator test replaces `can_user_access_ai` with a plain function.

## Runs (rule 15; every run under `systemd-run` MemoryMax=6G, `nice -n 10`, `timeout`)

| Run | Tree | Result | Log |
|---|---|---|---|
| Changed modules: `AutoGrader.tests_reason_codes` + the whole `audit` app | `b6b5af3` | 235 OK | `changed_new.log` |
| The 18 modules asserting on refusal bodies, `field_errors` or the renderer (users, billing, ai_processor, assignments, dashboard, students) | `b6b5af3` | 421 OK (1 skipped) | `changed_related.log` |
| Owning-app regression (rule 15): `users`, where the renderer and the handler live | `b6b5af3` | 657 OK (4 skipped) | `users_app_regression.log` |
| The M8 gap closed: `AutoGrader.tests_reason_codes` with the new message-layer test, unmutated, then with M8 applied | tip | 28 OK; with M8, 13 failures in the new test | `m8_rerun_*.log` |

An earlier run at `4bb5c9b` (before option A and the epic-a merge) gave the same two targeted sets: 233 OK and 421 OK.

## Mutation battery

Ten mutants in a disposable detached worktree at `b6b5af3`, with its own test DB (`s6a_battery.sh`, `s6a_mutants.py`). Each ran `AutoGrader.tests_reason_codes` + `audit.tests_emitter` (94 tests), and each restore was sha256-checked against the blob (`mutation_battery_b6b5af3.log`).

| Mutant | What it breaks | Result |
|---|---|---|
| M1 | the renderer shows the envelope keys | caught |
| M2 | `reference` dropped | caught |
| M3 | the emitter accepts any UPPER code again | caught |
| M4 | params not whitelisted | caught |
| M5 | the legacy `code` dropped (F8) | caught |
| M6 | `refusal_response` answers non-refusals | caught |
| M7 | `@require_ai_access` loses the credits mapping | caught |
| M8 | a `CodedError`'s server-only `detail` leaks into the message layer (`_passthrough_message`) | **survived the battery**. The API tests reach the message through `coded_response`, which does not use that layer. **Closed:** `test_the_message_layer_shows_the_message_and_never_the_detail` covers `describe_user_error` and `describe_background_task_error` for every user-facing code; with M8 applied it fails (13 failures) |
| M9 | the global handler ignores coded errors (they become 500s) | caught |
| M10 | a PROVIDER failure counts as user-facing | caught |

10 of 10 are caught at the tip.

## Notes

- The merge commit `b6b5af3`'s message lost three words to shell quoting. It should read: "renderers.py shows a structured refusal's `message` when it sits beside a string `code`. A coded body has no `message` key, so that rule and S6a's envelope rule do not interact." The commit is not amended (team rule).
