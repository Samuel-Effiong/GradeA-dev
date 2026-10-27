# H-259 notes

Security Engineer (db).

- **The row:** new in this pack, severity Medium (information disclosure to operators and Sentry).
- **Source:** H-255's survey; the Senior Manager's follow-up assignment of 27 October 2026, batch 29.
- **Branch:** `task/h259-h260-stripe-logs`, stacked on H-255's gated tip `675b6d4d` (not beta): H-259 edits the same
  `billing/` files as H-255, and the bundle merges H-255 first, then this branch. H-260 follows on the same branch,
  with its own commits, row and evidence, after this row's gate-evidence commit.
- **Rulings (27 October), on the first read:**
  1. The eighteen lines are in scope, same rule; `logger.exception` becomes `logger.error`, so the level and the
     Sentry event stay. The H-255 count (thirteen) is corrected in this row's docs commit, in H-255's NOTES and in
     its backlog row.
  2. The H-28 reconciliation alert is in scope: class and request id for both exceptions, in the log, the Sentry
     event and the super-admin email alike. The email still names the intent and the licence.
  3. Stored `failure_reason` text is out of scope, report-only here, and a new Low row, H-261 (Open, not scheduled).
  4. The 57 broad `except Exception` handlers are not changed; the scrubber is the defence there (one sentence in the
     row).
  5. The scrubber masks `sk_test_`, `sk_live_`, `rk_test_`, `rk_live_` and `whsec_`, keeping the prefix; the tests
     cover a log line, a traceback and a Sentry event.

## What the read found (at `675b6d4d`)

Counted by syntax tree (the survey script is the seam check's `_sites`): every logger call inside an `except` clause
that catches a Stripe error in `billing/` (tests and migrations left out). There are 23. Eighteen use the caught
exception:

| File | Line at the base | What it logged |
|---|---|---|
| `license_stripe_mutation.py` | 740 | the exception in the message (WARNING) |
| `license_views.py` | 787 | `logger.exception` with the exception in the message too |
| `license_views.py` | 1026 | `exc_info=exc` (ERROR) |
| `payment_method_views.py` | 210, 333, 452, 547, 598 | `logger.exception` |
| `receipts.py` | 135 | the exception in the message (WARNING) |
| `stripe_service.py` | 1032, 1599, 1876, 1890, 4819 | `logger.exception` (the handler has no `as exc`) |
| `stripe_service.py` | 2225 | the exception in the message (INFO) |
| `tasks.py` | 148, 203 | the exception in the message (WARNING) |
| `tasks.py` | 1542 | the exception in the message and `exc_info=True` (ERROR) |

The other five do not use it: `stripe_live_qa.py:551`, `stripe_service.py:2080`, `tasks.py:1652` (its class is
`StripeEvent.DoesNotExist`), `webhooks.py:430` and `:451` (a fixed sentence on a bad signature). No change.

- **Why H-255 said thirteen:** its survey looked only at handlers with `as exc` and so missed the five
  `stripe_service.py` lines, which log the traceback without naming the exception. It also gave `stripe_service.py:2225`
  as a base line; that is the line at H-255's tip (2223 at `31a86456`). Both are corrected in H-255's NOTES and row.
- **The H-28 alert** (`license_stripe_mutation.py:727`): `_reconciliation_needed(intent, f"outcome unknown ({exc}) and
  the read-back also failed ({read_exc}); …")`. The reason goes into an ERROR log line (a Sentry event) and into the
  email to every active super admin (`_email_super_admins`).
- **What reaches Sentry:** an ERROR record becomes an event built from the record's template, its arguments and the
  exception object (Sentry's logging integration is not a handler). `AutoGrader/sentry_scrubbing.py` runs
  `log_scrubbing.scrub` over every text part of the event, so one pattern added to `scrub` covers log text,
  tracebacks (`exc_text`, rendered by the record factory) and Sentry events.
- **`tasks.py:1542` re-raises.** Celery's own task-failure log then carries the traceback, Stripe's message
  included. That line is Celery's, not ours; the scrubber masks a key's tail in it.

## What changes

- `AutoGrader/error_messages.py`: `stripe_error_label(error)` returns `"<Class> (request <id>)"`. Every one of the
  eighteen lines, and the alert's reason, uses it in place of the exception.
- The ten traceback lines lose the traceback: `logger.exception` becomes `logger.error` (the level and the Sentry
  event stay) and `exc_info` goes. The five handlers with no name gain `as exc` (`as revert_exc` in the
  reactivation's compensating revert, inside a handler that already has an `exc`).
- The alert says `outcome unknown (AuthenticationError (request req_…)) and the read-back also failed
  (APIConnectionError (request None)); the intent is left PENDING`. It still names the licence, the intent, the
  operation and the Stripe subscription.
- `AutoGrader/log_scrubbing.py`: `_STRIPE_SECRET` masks a Stripe secret key, restricted key or webhook secret from
  the start of a word, keeping the prefix (`sk_live_[secret]`), up to the next space, quote, bracket, comma or
  semicolon. It runs only on text with a "_" in it. A publishable key (`pk_`) is public and is left alone, and so are
  words that merely contain the prefix (`risk_live_report`, `task_test_runner`).

## Not changed here (report only)

- **Stored `failure_reason` (H-261, Low, Open):** `license_stripe_mutation.py:721` ("Stripe refused: {exc}"), `:737`
  ("Outcome unknown ({exc}); read-back shows not applied") and `license_service.py:3342` ("Checkout session creation
  failed: {exc}"). Operator-only: Django admin and `resolve_licence_stripe_intent` show it; no client does. The
  scrubber does not reach the database.
- **The 57 broad handlers** in `billing/` (`except Exception` or bare `except`) that log a traceback, counted by
  syntax tree at `675b6d4d`. Some wrap Stripe calls. The scrubber masks a key there; Stripe's other text (ids,
  parameters) stays.

## Tests

`billing/tests/test_h259_stripe_log_text.py`, 23 tests. The probe is an `AuthenticationError("Expired API Key
provided: sk_test_h259…k7Qp")` with request id `req_h259`; Stripe calls are mocked on the real `stripe` module, so
`stripe.error.*` stay real. The scrubber is off under the test runner, so each site test reads what its line itself
says. `assert_logged_without_stripe_text` renders every captured record with its traceback and checks, in order:
no "sk_test" and no "API Key" anywhere; exactly one record holds the line's fragment; its level; that it names
`AuthenticationError (request req_h259)`; no exception attached.

- 19 site tests, one per line and one for the alert:
  - `receipts`, the two `tasks` lines reached directly, the renewal sweep through `process_license_renewals.apply()`;
  - the five `stripe_service` lines reached directly (the reactivation's revert by a failing local save);
  - the two licence routes and the five payment-method routes over HTTP;
  - the two H-28 lines through `apply_at_stripe` with `read_back_on=(AuthenticationError,)`. The alert test also
    reads every queued email: no Stripe text, both labels, the intent id and the licence id.
- The released-schedule line expects `InvalidRequestError (request req_h259)`: only that class reaches it.
- 3 scrubber tests (`override_settings(LOG_SCRUB_ADDRESSES=True)`), each with all five prefixes: a log line, a
  traceback from `logger.exception`, and `sentry_scrubbing.scrub_event` on an event whose log entry and exception
  values hold the keys. The key bodies are built from parts and short ("H259notarealkey0").
- 1 control: a publishable key and two ordinary words come out unchanged.

## Expected reds against `675b6d4d` (written before the gate)

22 FAIL blocks, each holding "unexpectedly found in", no ERROR, "Ran 23 tests". The control is green.
- The 19 site tests fail on `assertNotIn("sk_test", …)`: every line, rendered with its traceback, holds the probe.
- The 3 scrubber tests fail on `assertNotIn(KEY_BODY, …)`: `scrub` has no Stripe pattern.

A development run on `b19ce0d8` (the tests) with the code at `675b6d4d`: exactly that, "Ran 23 tests",
"FAILED (failures=22)".

## Mutants (expected sets traced against every assertion of the module)

30, in `mutate.py`, each run against the own module with `--keepdb` on `settings_worktree_mut`
(`test_h259_h260_stripe_logs_mut`). "LEAK" is "unexpectedly found in", "MISSING" is "not found in".

| Id | Break | Expected |
|---|---|---|
| H1 | the label carries Stripe's message | all 19 site tests, LEAK |
| H2 | the label drops the request id | all 19 site tests, MISSING |
| P1 | the lost-response warning names the exception | lost response, LEAK |
| P2, P3 | the alert quotes the read-back's / the call's error | alert, LEAK |
| V1 | licence overage names the exception | its test, LEAK |
| V2 | licence setup intent logs the traceback | its test, LEAK |
| W1-W5 | each payment-method line logs the traceback | its test, LEAK |
| W6 | the card-list line drops to WARNING | its test, "'WARNING' != 'ERROR'" |
| R1 | the receipt line names the exception | its test, LEAK |
| T1, T5, T6 | reactivation revert / released schedule / setup_intent.succeeded name the exception | its test, LEAK |
| T2-T4 | duplicate invoice / price revert / unpaid void log the traceback | its test, LEAK |
| K1, K2, K4 | invoice listing / licence cancel / reconciliation name the exception | its test, LEAK |
| K3 | reconciliation logs the traceback (`exc_info=True`) | its test, LEAK |
| S1 | no Stripe pattern | 3 scrubber tests, LEAK |
| S2 | the prefix is dropped | 3 scrubber tests, MISSING |
| S3, S4 | live keys / webhook secrets are missed | 3 scrubber tests, LEAK |
| S5 | the pattern no longer starts at a word | the control, " != " |
| S6 | the pattern's gate never opens | 3 scrubber tests, LEAK |

`--check`: "30 mutants: every anchor found once, all parse".

## The seam

`seam_check.py`, by syntax tree, base `675b6d4d`:
- per file, the changed top-level units are exactly the allowed set (the helper; the scrubber's docstring,
  pattern, `scrub` and `_prefix_kept`; the import lines; the 17 billing functions or methods holding the lines);
- in each changed billing function, with logger and alert calls blanked and `except` names dropped, nothing else
  changed; it has as many logging calls as before, and each one that changed carries `stripe_error_label`;
- `scrub`, apart from its docstring, only gains its first statement;
- at the tip: 23 logger calls in Stripe `except` clauses, none `logger.exception`, none with `exc_info`, none naming
  the caught exception outside `stripe_error_label`, 18 inside one; the alert names `exc` and `read_exc` only inside
  it.

It passes on the tip and stops on these bad inputs (temporary copies; the tree untouched):
- the base against itself;
- a different log line in an allowed function (`process_license_renewals`);
- an extra statement in `release_schedule`;
- a sentence changed in `reactivate_if_cancelling` outside its logging;
- a sentence changed in `create_custom_price`, a function not allowed to change;
- `logger.exception` back on the portal line;
- the alert quoting `read_exc`;
- an extra log line naming the exception in `lookup_receipt_url`;
- `scrub` changed elsewhere.

A changed file outside the eight is the gate script's changed-file check, not this one.

## Gate

- **Script:** `run_h259_gate.sh.txt`, H-255's form. The red run expects 22 blocks and "Ran 23 tests". The exit code is
  captured by the start command.
- **Before the test runs:** makemigrations, the changed-file list (only the eight production files outside `docs/`
  and the one test file), then the seam check.
- **Own:** the one module.
- **Near (49 modules):** every billing test module that reaches a changed function or route, all ten H-28 modules,
  `test_h255_stripe_error_text`, `test_overage_cap`, `test_license_overage_offline`, `test_logs_carry_no_email`
  (root-logger captures) and `AutoGrader.tests_error_messages` (the helper's module).
- **Guards:** 20, those of the H-255 gate, `tests_no_pii_in_logs`, `tests_log_scrubbing` and `tests_sentry_scrubbing`
  among them (H-227's 21 less `tests_error_messages`, which is in Near).
- **The mutants:** 30, with a 3300 s limit and the 300 s stall watchdog.
- Because the lines sit on many billing paths, the batch's full run is the real regression.
- **Base:** `675b6d4d`.
