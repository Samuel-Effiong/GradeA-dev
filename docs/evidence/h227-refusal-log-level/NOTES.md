# H-227 notes

- **The row** (new in this pack; quoted from batch 17's push package): "the general handler logs every ordinary
  refusal on every route with a full traceback." The 2026-09-28 note in `docs/HARDENING_BACKLOG.md` ("API
  Exception" at ERROR for a bad `school_id`) is the same fault.
- **What the read found** (beta `b4af529f`, `users/exceptions.py:66`):
  - `custom_exception_handler` ran `logger.error("API Exception", exc_info=exc, extra=extra)` before
    `exception_handler(exc, context)`. Every exception the coded branch does not take was logged at ERROR with its
    traceback: the refusals (400, 401, 403, 404, 405, 429) on every route, and the real crashes.
  - The traceback's last line is the exception's own text. A `ValidationError`'s is its whole field dict, which can
    quote what the user typed.
  - `LoggingIntegration(event_level="ERROR")` (`AutoGrader/settings.py:207`) made every refusal a Sentry error event.
  - The renderer does not log, so this line was, and stays, the only record of a real 500.
- **The Senior Manager's rulings (25 October 2026):**
  - one level for every refusal, WARNING (one rule, matching `log_refusal`);
  - a crash keeps ERROR with `exc_info`, with a control test;
  - changing the existing `tests_exception_handler` tests is approved, each named here;
  - R4's route is confirmed by hand trace;
  - answers, audit rows and status codes stay the same.
- **What changes** (`users/exceptions.py`, `custom_exception_handler` only):
  - the log moves below `exception_handler`;
  - DRF answered (a refusal): `logger.warning("API request refused: %s (%s)", type(exc).__name__,
    drf_response.status_code, extra=extra)`, with no `exc_info` and none of the exception's text. This is
    `billing.refusals.log_refusal`'s level and no-traceback rule, without its text;
  - DRF did not answer (a crash): `logger.error("API Exception", exc_info=exc, extra=extra)`, as before;
  - the coded branch above (FR-A-06) is untouched.
- **Visible change, for whoever reads Sentry: refusals are no longer Sentry error events.** WARNING is below the
  integration's event level. With `enable_logs=True` they can still reach Sentry's log stream, scrubbed by
  `before_send_log`. Crashes are still events.
- **Production files:** `users/exceptions.py` only, and only the function's logging. The seam check proves it by the
  syntax tree.
- **Docs:** the new H-227 row (after H-221's), and `docs/backend/BACKEND_REFERENCE.md`'s two passages on the handler.
- **Existing tests changed** (`users/tests_exception_handler.py`, in the tests commit):
  - the helper `handle` is split into `handle_refusal` (captures at WARNING) and `handle_crash` (at ERROR):
    - `test_a_drf_exception_keeps_its_status_and_is_marked_handled` and
      `test_validation_errors_keep_their_field_payload` use `handle_refusal`;
    - `test_an_unhandled_exception_becomes_a_500_carrying_the_original` and
      `test_the_unhandled_response_body_carries_no_exception_text` use `handle_crash`;
  - `test_a_handled_exception_is_logged_too` (it pinned ERROR for a `NotFound`) is reversed, as
    `test_a_handled_exception_is_logged_at_warning_without_a_traceback`;
  - `test_every_exception_is_logged_with_request_context` (a `KeyError`, a crash) is unchanged.
  - How they were found: `git grep` for `assertLogs("users.exceptions"` and "API Exception" outside the handler: one
    file. The three root-logger `assertLogs(level="ERROR")` in `billing/tests/test_h28_*` call tasks directly, not the
    API, so they are unaffected.
- **The wider search (the Senior Manager's second condition).** A test that captures the ROOT logger, or a parent of
  `users.exceptions`, could have caught the handler's old ERROR record whenever a refused API request ran inside it.
  - A syntax-tree scan of every test file found each `assertLogs`/`assertNoLogs` whose logger is none, "root",
    "users", "django" or "django.request", at any level. A grep found every test that attaches its own handler (to root
    or to every logger), and `caplog` (none).
  - Each one was traced. The question for each: is its ERROR (or its asserted text) from its own logger, or could it
    come only from the handler's refusal record?

  | Test (file:line) | What it captures, and how | Does a refused API request run inside? | Verdict |
  |---|---|---|---|
  | `billing/tests/test_h28_alerting.py:186` | `assertLogs(level="ERROR")`, root | No: calls `escalate_stale_licence_stripe_intents.run()` directly | Its ERROR is the task's own (`billing/tasks.py:1713`, `logger.error(summary)` when anything escalated). Unaffected |
  | `billing/tests/test_h28_intent_audit.py:72` | the same | No: the same task, called directly | The same. Unaffected |
  | `billing/tests/test_h28_intent_audit.py:184` | `assertLogs(level="ERROR")`, root; asserts "MANUAL RECONCILIATION NEEDED" | No: calls `license_stripe_mutation.escalate()` directly | Its ERROR is `_reconciliation_needed`'s own (`billing/license_stripe_mutation.py:532`). Unaffected |
  | `billing/tests/test_logs_carry_no_email.py:234, 241, 255, 307` | `assertLogs(level="DEBUG")`, root; assert no address (and 241: a "FAILED" line; 255: two service lines) | No: service and model calls, no request | The asserted lines are the services' own. A refusal line could only lose text. Unaffected |
  | `billing/tests/test_other_school_before_subscription.py:232` | `assertLogs(level="DEBUG")`, root, around `POST add-teachers`; asserts the ids appear, no "@" | A request, but the view answers everything itself (200 with per-email results; its `except` branches return responses, `billing/license_views.py:362-368`), so nothing reaches the handler | The ids come from the service's own refusal and "Skipped enrolling" lines. Unaffected |
  | `billing/tests/test_other_school_before_subscription.py:259` | `assertLogs(level="DEBUG")`, root | No: calls the service directly | Unaffected |
  | `classrooms/tests_s7d_roster_codes.py:232` (helper at 64) | a DEBUG handler on root; asserts the address is NOT in any line | A roster request answered 200 with rows | An absence check: H-227 only removes text. Unaffected |
  | `billing/tests/test_s7d_licence_teacher_codes.py:364` (helper at 78) | the same; asserts three addresses are NOT in any line | `add-teachers`, answered by the view itself | The same. Unaffected |
  | `assignments/tests_task_results_carry_no_text.py` (helper at 64) | a handler on every logger | No: tasks called directly, no client call in the file | Unaffected |
  | `students/tests_broker_text_not_logged.py` (helper at 83) | the same; asserts every record has no `exc_info` | No: no client call in the file | Unaffected |

  - No `assertNoLogs` on root or a parent logger exists. No test relied only on the handler's record, so no further
    test changes.
  - All eight modules are added to the gate's near list (runner sha `c748ba98`), so the gate runs them.
- **R4 traced by hand (the Senior Manager's condition).**
  1. `POST /auth/otp` with `otp_type` `NOT_A_TYPE`: `OTPSerializer.is_valid(raise_exception=False)` is false.
  2. `otp` raises `ParseError("Invalid OTP type. …")` with no try around it (`users/views.py:1197-1200`).
  3. DRF's `handle_exception` calls `custom_exception_handler`, and `exception_handler` answers 400.
  4. `auth-otp` is in `audit.request_audit.EXCLUDED_ROUTES`, so no audit row is written before or after.
- **R5 traced by hand.**
  1. `POST /auth/register/school-admin` with an empty body: the serializer fails, and the view raises
     `ValidationError` inside its `try`.
  2. `except (ParseError, ValidationError, Throttled)` finds no `audit_failure` and re-raises
     (`users/views.py:2079-2088`).
  3. The handler answers 400.
  4. No event survives the request, so `emit_anonymous_refusal` records one AUTH_LOGIN FAILURE, reason
     INVALID_REQUEST (the route is in `ANONYMOUS_AUDITED_ROUTES`).
- **Not shown:** Sentry itself (no Sentry call is made in tests; the level against `event_level` is read from the
  settings); log retention.

## Expected reds against b4af529f (written before any run)

The red run takes `users/exceptions.py` back to the base and runs the two test modules as at the tip.

```
users.tests_h227_refusal_logging (5 red, 2 green)
  R1 test_a_refusal_is_logged_at_warning_without_a_traceback           "'ERROR' != 'WARNING'"
  R2 test_a_refusal_log_carries_none_of_the_refusals_text              "unexpectedly found in"
  R3 test_a_refusal_writes_no_error_record                             "Unexpected logs found"
  R4 test_a_refused_code_request_logs_no_error_and_answers_as_before   "Unexpected logs found"
  R5 test_a_refused_door_logs_no_error_and_keeps_its_audit_row         "Unexpected logs found"
  GREEN (controls): K1 test_a_crash_keeps_its_error_record_with_the_traceback, K2 test_a_throttle_keeps_its_answer
users.tests_exception_handler (1 red, 5 green)
  test_a_handled_exception_is_logged_at_warning_without_a_traceback    "'ERROR' != 'WARNING'"
Total: 6 FAIL blocks, no ERROR; "Ran 13 tests". The script counts them.
```

- At the base a WARNING-level capture also catches the ERROR record, so the two `handle_refusal` tests and K2 are
  green both times by design. Only the level and no-ERROR checks see the change.
- **One red traced by hand: R2 at the base.**
  1. `custom_exception_handler(ValidationError({"email": ["probe-h227@example.com is already taken."]}), …)`: not a
     coded refusal.
  2. Line 66 logs ERROR "API Exception" with `exc_info=exc`.
  3. The capturing handler's formatter appends the exception. It was never raised, so there is no traceback frame,
     and the line is `rest_framework.exceptions.ValidationError: {'email': [ErrorDetail(string='probe-h227@example.com
     is already taken.', code='invalid')]}`.
  4. `assertNotIn("probe-h227", …)` fails: "'probe-h227' unexpectedly found in …".
- **A control traced by hand: K1, after the change.**
  1. `KeyError` is not coded, and `exception_handler` returns None.
  2. One ERROR "API Exception" record with `exc_info` and the `extra` keys `path`, `method` and `user`; the response
     is 500. The same at the base.

## Mutants (expected sets traced against every assertion of the two modules)

| Id | Break | Expected to fail (fragment) |
|---|---|---|
| P1 | the refusal back at ERROR | R1, the reversed handler test (`'ERROR' != 'WARNING'`); R3, R4, R5 (`Unexpected logs found`) |
| P2 | the refusal keeps its traceback | R1, the reversed test (`is not None`); R2 (`unexpectedly found in`) |
| P3 | the refusal's line carries its text | R1 (`!= 'API request refused: NotFound (404)'`); R2 (`unexpectedly found in`) |
| P4 | the crash at WARNING | K1 and the three crash handler tests (`no logs of level ERROR or higher …`) |
| P5 | the crash without its traceback | K1, `test_every_exception_is_logged_with_request_context` (`unexpectedly None`) |
| P6 | the refusal not logged at all | R1, R2, K2, the two `handle_refusal` tests, the reversed test (`no logs of level WARNING or higher …`) |

- **P1:** R2 passes, because the ERROR line has no text or traceback. The WARNING-level captures (the helpers, K2)
  also catch an ERROR record.
- **P3:** R1 passes its level and traceback checks, then fails on the message.

## The seam

`seam_check.py` compares the base and tip syntax trees of `users/exceptions.py`:
- everything outside `custom_exception_handler` must match, and so must its arguments;
- inside it, the statements with every `logger.<level>(…)` call removed must match;
- the tip holds exactly one "API request refused" WARNING without `exc_info`, and one ERROR "API Exception" with
  `exc_info`.

Before the pack (by parsing source only, no test run), it passes on the tip and prints the two calls. It stops on
three bad inputs:
- the base against itself (no refusal WARNING);
- an edit elsewhere in the file;
- an extra statement inside the function.

## Gate

- **Script:** `run_h227_gate.sh.txt`, the H-210 gate's form. The red run expects 6 blocks and "Ran 13 tests". The exit
  code is captured by the start command.
- **Before the test runs:** makemigrations, the changed-file list (only `users/exceptions.py` outside `docs/` and the
  two test files), then the seam check.
- **Own:** the two modules.
- **Near:** `users.tests_renderers`, `users.tests_rate_limited_envelope`, `users.tests_auth_lock_envelope`,
  `users.tests_throttling`, `users.tests_auth_endpoints`, `audit.tests_route_coverage`;
  and the eight modules of the wider search: `billing.tests.test_h28_alerting`, `billing.tests.test_h28_intent_audit`,
  `billing.tests.test_logs_carry_no_email`, `billing.tests.test_other_school_before_subscription`,
  `classrooms.tests_s7d_roster_codes`, `billing.tests.test_s7d_licence_teacher_codes`,
  `assignments.tests_task_results_carry_no_text`, `students.tests_broker_text_not_logged`.
- **Guards:** the 21, `AutoGrader.tests_error_messages`, `tests_no_pii_in_logs`, `tests_log_scrubbing` and
  `tests_sentry_scrubbing` among them.
- **The mutants:** 6, with a 1140 s limit and the 300 s stall watchdog.
- Because the line runs on every route's refusal, the batch's full run is the real regression.
- **Base:** `b4af529f`.
