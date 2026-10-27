# H-254 notes

Security Engineer (db).

- **The row:** new in this pack, severity High (information disclosure).
- **Seen on:** the beta test site, 27 October 2026 (the user's screenshot). The subscription page's toast read "Could not
  verify your subscription with our payment provider: Expired API Key provided: sk_test_****…clSJ".
- **Source:** the Senior Manager's assignment of 27 October 2026, the first row of batch 29.

## What the read found (beta `31a86456`)

- `describe_stripe_error` (`AutoGrader/error_messages.py:257`) returned `error.user_message` for any exception that had
  one.
- In stripe 14.4.1, `StripeError.user_message` is a property that returns `self._message`, the error's own message,
  for every class (`stripe/_error.py:63-65`).
  - An AuthenticationError's message is "Expired API Key provided: sk_test_…", with Stripe's masked key tail.
  - An InvalidRequestError's names ids and parameters.
  - Checked in the venv: `AuthenticationError("Expired API Key provided: sk_test_xxx").user_message` is that string.
- The site in the screenshot:
  1. `SubscriptionReactivationService.reactivate_if_cancelling` raises `ValueError("Could not verify your subscription
     with our payment provider: " + describe_stripe_error(exc, ...))` (`billing/stripe_service.py:935-940`).
  2. `SubscriptionManagementViewSet.resume` answers that ValueError with `{"detail": str(exc)}` (`billing/views.py:1178`).
- **`describe_user_error`, the generic classifier, cannot echo a StripeError's text.**
  - It passes through only a `CodedError` (rendered from its spec), `StudentUploadNotProcessedError`, and the
    whitelisted user-written exceptions in `_user_facing_exception_types()`. InsufficientCreditsError gets a fixed
    sentence.
  - No StripeError is among those, so for any StripeError it returns the fallback. No change was needed there.
  - `describe_background_task_error`'s infra classifier returns fixed sentences only.

## What changes

- **`describe_stripe_error`:**
  - Only a `stripe.error.CardError`'s `user_message` is returned. A card error's message is written for the
    cardholder (declined, insufficient funds).
  - Any other StripeError (Authentication, Permission, APIConnection, RateLimit, InvalidRequest, API, Idempotency,
    SignatureVerification, TemporarySessionExpired, or a bare StripeError) falls through to `describe_user_error`, which
    gives the caller's fallback.
  - The server log gets one line, `logger.warning("Stripe call failed: %s (request %s)", type(error).__name__,
    error.request_id)`: no message, no `exc_info` (H-227's rule; the ids-only form of H-60's `log_provider_error`).
  - Logging inside the classifier, not at each caller, means none of its 22 calls (20 at the base, plus the two this row adds) can forget it.
- **`billing/overage_pricing.py`:**
  - `OveragePriceUnavailable` is a ValueError that `purchase_overage` answers with its text (`billing/views.py:1343`).
    Its text was `f"Could not read Stripe price {price_id} for plan {plan.name}: {exc!r}"`: Stripe's repr with the
    message and request id, plus the price id.
  - Its `str()` is now `OVERAGE_PRICE_UNAVAILABLE_MESSAGE`, a fixed sentence.
  - The operator detail is `.detail`: price id, plan, class and request id, with no message.
  - `overage_price_drift` (the reconcile command and the beat task's log) reads `.detail`.
  - The tiered-price raise at :156 keeps its own (our) text as its detail.
- **`billing/qa_time_travel.py:206`:**
  - `new_customer_test_clock_kwargs` runs inside `StripeCustomerService.get_or_create_customer`
    (`billing/stripe_service.py:326`). Its ValueError therefore reaches every checkout route's body on a QA-configured
    site, like the beta test site.
  - It quoted `getattr(exc, 'user_message', None) or exc`, the identical one-liner. It now goes through the classifier.
- **`billing/stripe_service.py`:**
  - `create_custom_price` (:2372) quoted `{exc}`. It now goes through the classifier. It has no callers today; it is
    fixed because it is the same pattern on a public service method.
  - The stale-schedule recovery's two client messages (:2090, :2105) no longer name the stale schedule's id. It is
    still in the WARNING just above them.
- **Visible change: a non-card Stripe failure now ends with the site's fallback ("Please try again." on most) where
  Stripe's sentence was.** Card declines read as before.

## Part 2: every site that puts exception text into a client answer

The method:
- a syntax-tree survey of every `except` in `billing/*.py` whose `raise` or `return` uses the caught exception;
- the 20 calls of `describe_stripe_error` at the base;
- then each view's `except ValueError: {"detail": str(exc)}` traced to the ValueErrors its service can raise.

Line numbers are at the base, `31a86456`.

### Views that answer with `str(exc)` or the classifier

| Site | What it can carry | Verdict |
|---|---|---|
| `views.py:978` cancel, after `release_schedule` | ValueError from `stripe_service.py:2231` (classifier) | Fixed by the classifier |
| `views.py:1008` cancel | the classifier directly over any StripeError, a CardError among them | Fixed; a card decline passes (K1) |
| `views.py:1178` resume (**the screenshot**) | ValueErrors from `reactivate_if_cancelling`: `:937`, `:977` (classifier), and three fixed sentences | Fixed by the classifier (R1, R2) |
| `views.py:1295` select-plan | reactivation (`:937`, `:977`); upgrade (`:1166`, `:1198` CardError, `:1205`, `:1359`, `:1387`, `:1480`); schedule (`:2040`, `:2068`, `:2090`, `:2105`, `:2114`, `:2231`); the Test Clock through `get_or_create_customer`; our own texts | Fixed by the classifier, the id removal and the clock fix |
| `views.py:1343` purchase-overage | `OveragePriceUnavailable` (Stripe's repr), `OveragePriceMismatch` (our text, see open question 1), `:2333` (classifier), the Test Clock | Fixed (R5); the mismatch is left |
| `payment_method_views.py:64` (`_stripe_error_response`, five actions) | the classifier over any StripeError | Fixed by the classifier (R7) |
| `license_views.py:795` overage purchase | the classifier | Fixed by the classifier |
| `license_views.py:1031` setup intent | the classifier | Fixed by the classifier |
| `license_views.py` `except ValueError` (322, 665, 718, 785, 861, 896, 924, 960, 998) and `license_overage_offline_views.py:115, 143` | licence paths raise fixed text since H-60 (`TRY_AGAIN` sentences, `LicenceStripeChangeNotRecorded`); `license_service.py:3346` uses the classifier | Fixed by the classifier where Stripe is quoted; the rest is our text |
| `qa_time_travel.py:1014` (super admin only) | the rewind service's ValueErrors (our text) | Unchanged |

### The raise sites in `billing/stripe_service.py` (the classifier's callers and the others)

| Line(s) | Catches | Verdict |
|---|---|---|
| 937, 977, 1166, 1205, 1359, 1387, 1480, 2040, 2068, 2114, 2231, 2333 | StripeError (2068: InvalidRequestError) | Through the classifier: fixed |
| 1198 | `stripe.error.CardError` only | **Left alone:** a decline's text is the cardholder's to read ("Card declined: Your card was declined."); the classifier passes it by design |
| 2090, 2105 | StripeError on the stale-schedule release and retry | Fixed: classifier, and the id removed (R10, R11) |
| 2372 | StripeError in `create_custom_price` | Fixed (R9) |
| 1683, 1725, 1789, 1817 | StripeError on licence plan changes | Already fixed text (H-60); Stripe's text goes to the intent and `log_provider_error` |
| 1782 | CardError on licence plan changes | `f"card error: {exc}"` goes to the intent's reason only; the client gets "Card declined. The plan has not been changed…" (H-60) |
| 2175 | `_extract_conflicting_schedule_id`'s `user_message or str(exc)` | A parser, not an answer; what it extracts was the id in 2090 and 2105, now removed |
| 3758 | DoesNotExist in a webhook | A server log of ids, not an answer |

- **Elsewhere in `billing/`:**
  - `license_service.py:2889` (CardError → `payment_failed(f"card error: {exc}")`) goes to the intent only, with fixed
    client text (H-60).
  - `license_service.py:3340` writes `failure_reason = f"… {exc}"` to the intent row, for the reconciliation screens,
    not a client.
  - `tasks.py:1221, 1224` (live QA run status, super admin) are not part of this row.

### Open questions for the Senior Manager (no change made)

1. **`OveragePriceMismatch`** (`billing/overage_pricing.py:99`) is our own sentence, but it names the Stripe price id and
   both cent amounts. It reaches the customer through `views.py:1343` and the licence overage views. It carries no
   provider error text, so it is outside this row's rule. Whether a customer should see the price id is a product call.
2. **Thirteen existing server log lines in `billing/` carry Stripe's text or traceback**, so with an expired key the
   masked key tail reaches the logs (and Sentry, for ERROR records). These are server-side, not this row's client
   rule. Counted by syntax tree, logger calls inside a Stripe `except` that use the exception:
   - `license_stripe_mutation.py:740`;
   - `license_views.py:787` (`logger.exception` with the text);
   - `license_views.py:1026` (`exc_info`);
   - `receipts.py:135`;
   - `stripe_service.py:2225`;
   - `tasks.py:148`, `tasks.py:203`, `tasks.py:1542` (`exc_info`);
   - and five more that are `logger.exception` with the traceback: `payment_method_views.py:210, 333, 452, 547, 598`.

   Sentry's `before_send` scrubbing is not shown here. Converting these lines to the class-and-request-id form would
   be a follow-up row if wanted.

## Part 3: the other providers (report only)

- **Google sign-in** (`users/views.py:2199-2212`): the token exchange's `RequestException` goes through
  `describe_user_error`, and RequestException is not whitelisted, so the client gets `GOOGLE_SIGN_IN_FAILED`. A
  ValueError from `id_token.verify_oauth2_token` is uncaught, so it becomes a 500 whose body has no text (H-227's crash
  path). **No leak.**
- **OpenRouter** (`ai_processor/services.py`): the grading calls wrap provider errors in bare
  `Exception(f"Error during AI model: {e}")` (1191, 1248, 2324, 5284, 5675) and `Exception(f"All {n} attempts failed.
  Last error: …")` (1724, 5252, 5713). These reach clients only through `describe_user_error` and
  `describe_background_task_error`, which never pass a bare Exception's text. The infra classifier returns fixed
  sentences. A survey of every `views.py`, `views_*.py`, `serializers.py` and `tasks.py` outside `billing/` found no
  `str(exc)` answer over a provider exception. **No leak found; not the identical one-liner, so no fix.**
- **MailerLite** (`users/mailerlite_service.py`, `users/tasks.py`): no except handler returns or raises the caught
  exception's text toward a client. **No leak.**
- **Seen in passing, not a provider:**
  - `assignments/serializers.py:231` answers any exception during assignment creation with `ValidationError(f"Failed
    to create assignment and rubric: {e}")`. Only database writes run inside, so it can carry database text
    (`IntegrityError`), not a provider's.
  - `ai_processor/services.py:6147, 6165` wrap PyMuPDF and pdf2image errors in `ValueError(f"Something went wrong:
    {e}")`.

  Reported for a later row, not changed.

## Tests

`billing/tests/test_h254_stripe_error_text.py` holds 12 tests. Each probe is `AuthenticationError("Expired API Key
provided: sk_test_h254…clSJ", http_status=401, headers={"request-id": "req_h254"})`.

| Id | Test | Kind |
|---|---|---|
| R1 | `test_an_expired_key_on_resume_answers_without_the_key_text` | route; also the full sentence "…payment provider: Please try again." |
| R2 | `test_the_resume_failure_is_logged_with_class_and_request_id_only` | one WARNING, no text, no `exc_info`, exactly "Stripe call failed: AuthenticationError (request req_h254)" |
| R3 | `test_an_expired_key_on_cancel_answers_without_the_key_text` | route |
| K1 | `test_a_card_errors_text_still_reaches_the_body` | control: "…payment provider: Your card was declined." |
| R4 | `test_no_non_card_stripe_error_passes_its_text` | eight StripeError classes, all get the fallback |
| R5 | `test_an_expired_key_on_overage_purchase_answers_without_the_key_text` | route; no key text and no price id |
| R6 | `test_the_drift_report_names_the_failure_without_its_text` | operator detail keeps price id, class, request id; no text |
| R7 | `test_an_expired_key_on_payment_methods_answers_without_the_key_text` | route |
| R8 | `test_a_test_clock_failure_carries_no_stripe_text` | service |
| R9 | `test_create_custom_price_carries_no_stripe_text` | service |
| R10 | `test_a_stale_schedule_release_failure_names_no_schedule_id` | service; no id, no text |
| R11 | `test_a_stale_schedule_retry_failure_names_no_schedule_id` | service; no id, no text |

**Existing test changed** (`AutoGrader/tests_error_messages.py`):
- `test_uses_stripe_user_message_when_present` gave a plain `Exception` a `user_message` attribute, which the new rule
  rightly no longer honours.
- It is now `test_uses_a_card_errors_user_message`, with a real `stripe.error.CardError`. It is green at the base and
  at the tip.
- How it was found: `git grep user_message` over the test files; no other test fakes one. No `assertNoLogs` on root or
  a parent of `AutoGrader.error_messages` exists.

## Expected reds against 31a86456 (written before the gate)

The red run takes the four production files back to the base and runs the two modules as at the tip.

```
billing.tests.test_h254_stripe_error_text (11 red, 1 green)
  R1, R3, R5, R6, R7, R8, R9, R10, R11                                   "unexpectedly found in"
  R2  test_the_resume_failure_is_logged_with_class_and_request_id_only   "no logs of level WARNING or higher triggered on AutoGrader.error_messages"
  R4  test_no_non_card_stripe_error_passes_its_text                      "Lists differ"
  GREEN (control): K1
AutoGrader.tests_error_messages (0 red, 22 green; the changed card test is green at the base)
Total: 11 FAIL blocks, no ERROR; "Ran 34 tests". The script counts them.
```

- The development red run (before the code commit, on `e35c9681`'s tests against the base code) matched this: 11 FAIL
  blocks, each red for its written reason, "Ran 34 tests", "FAILED (failures=11)".
  - R1's block quotes the screenshot's sentence: `'sk_test' unexpectedly found in 'Could not verify your subscription
    with our payment provider: Expired API Key provided: sk_test_h254…'`.
  - R10 and R11 fail first on the schedule id (`'sub_sched_H254stale' unexpectedly found in …`).
  - R5's and R6's failures quote the repr: `Could not read Stripe price price_overage_h254 for plan PRO:
    AuthenticationError(message='Expired API Key provided: …`.
- **One red traced by hand: R1 at the base.**
  1. `POST /…/subscription/resume`: the subscription is cancelling, and `stripe.Subscription.retrieve` raises the
     probe.
  2. `reactivate_if_cancelling` catches StripeError and calls `describe_stripe_error(exc, "Please try again.")`.
  3. `user_message` is the probe's message, so it is returned.
  4. The ValueError reads "Could not verify your subscription with our payment provider: Expired API Key provided:
     sk_test_h254…".
  5. `resume` answers 400 with it, and `assertNotIn("sk_test", …)` fails.
- **The control traced by hand: K1, after the change.**
  1. `stripe.Subscription.modify` raises a CardError.
  2. `isinstance(error, stripe.error.CardError) and error.user_message` holds, so the classifier returns "Your card was
     declined." and logs nothing.
  3. The view answers "Could not cancel your subscription with our payment provider: Your card was declined.". The
     same holds at the base.

## Mutants (expected sets traced against every assertion of the two modules)

| Id | Break | Expected to fail (fragment) |
|---|---|---|
| S1 | every StripeError's text passes (the old rule) | R1, R3, R7, R8, R9, R10, R11 (`unexpectedly found in`); R2 (no WARNING); R4 (`Lists differ`) |
| S2 | an AuthenticationError's text passes | the same as S1 |
| S3 | the log line carries Stripe's message | R2 (`unexpectedly found in`) |
| S4 | the log line carries the traceback | R2 (`is not None`) |
| S5 | the failure is not logged | R2 (no WARNING) |
| S6 | a card error's text is not shown | K1, `test_uses_a_card_errors_user_message` (`Your card was declined`) |
| S7 | `OveragePriceUnavailable`'s text is its detail again | R5 (`unexpectedly found in`, on the price id) |
| S8 | the drift detail carries Stripe's repr again | R6 (`unexpectedly found in`) |
| S9 | the Test Clock passes `user_message` again | R8 |
| S10 | `create_custom_price` quotes the exception again | R9 |
| S11 | the release-failure message names the stale schedule again | R10 |
| S12 | the retry-failure message names the stale schedule again | R11 |

- **S1 and S2:** the early return skips the log, so R2 sees none. R5 and R6 do not go through the classifier.
- **S3:** the format gains a third `%s` with the argument, so the record formats (no TypeError).
- **S7:** the detail holds no Stripe text, so R5's key checks pass and its price-id check fails.
- `mutate.py --check`: 12 mutants, every anchor found once, all parse.

## The seam

`seam_check.py` compares the syntax trees of the four production files at the base and the tip. Per file, the
added, removed or changed units (functions; a class's methods as `Class.method`; assignments; imports) must be exactly
the allowed set:

| File | Allowed units |
|---|---|
| `error_messages.py` | `import logging`, `logger`, `describe_stripe_error` |
| `overage_pricing.py` | `OVERAGE_PRICE_UNAVAILABLE_MESSAGE`, `OveragePriceUnavailable` and its `__init__`, `stripe_overage_unit_price`, `overage_price_drift` |
| `qa_time_travel.py` | the classifier import, `new_customer_test_clock_kwargs` |
| `stripe_service.py` | `StripeSubscriptionScheduleService.schedule_plan_change_on_stripe`, `StripePriceService.create_custom_price` |

Inside those units:
- `stripe_overage_unit_price`, `new_customer_test_clock_kwargs` and the two `stripe_service` methods dump the same
  with every `raise` blanked: only what is raised changed;
- `overage_price_drift` changes only `str(exc)` → `exc.detail`;
- `describe_stripe_error` holds exactly one `logger.warning` with no keywords, whose arguments are the format,
  `type(error).__name__` and `error.request_id`;
- its one early return sits under a `stripe.error.CardError` check.

Before the pack (by parsing source only), it passes on the tip and prints the log call and the pass-through
condition. It stops on four bad inputs:
- the base against itself;
- an edit elsewhere in `stripe_service.py` (a checkout sentence);
- an extra statement inside `schedule_plan_change_on_stripe`;
- the log line with the error added.

## Gate

- **Script:** `run_h254_gate.sh.txt`, H-227's form. The red run expects 11 blocks and "Ran 34 tests". The exit code is
  captured by the start command.
- **Before the test runs:** makemigrations, the changed-file list (only the four production files outside `docs/` and
  the two test files), then the seam check.
- **Own:** the two modules.
- **Near (22 modules):**
  - every billing test module that reaches the classifier, the overage price, the QA clock, the schedule service or
    the routes the tests use: `test_cancellation_visibility`, `test_converted_trial_is_not_cancelling`,
    `test_date_scheduled_cancellation`, `test_free_plan_activation_security`, `test_license_cancellation`,
    `test_live_qa_invariants`, `test_overage_cap`, `test_overage_price_drift`, `test_payment_methods`,
    `test_qa_test_clock_attachment`, `test_refusal_handling`, `test_subscription_cancel`,
    `test_subscription_cycle_integrity`, `test_subscription_reactivation`, `test_subscription_upgrade`,
    `test_track_separation`, `tests_free_trial`, `test_qa_time_travel_clock`, `test_price_drift_reconciliation`,
    `test_h60_licence_stripe_text`, `test_license_overage_offline`;
  - and `test_logs_carry_no_email` (root-logger DEBUG captures).
  - A development run of the 22 on `b2ec508f`'s code: "Ran 514 tests", "OK".
- **Guards:** the 21 of the H-227 gate, `tests_no_pii_in_logs`, `tests_log_scrubbing` and `tests_sentry_scrubbing`
  among them. `tests_error_messages` is in Own, not repeated.
- **The mutants:** 12, with a 1680 s limit and the 300 s stall watchdog. Own database: `settings_worktree_mut`
  (`test_h254_stripe_error_text_mut`; untracked, excluded by `.git/info/exclude`).
- Because every billing answer goes through the classifier, the batch's full run is the real regression.
- **Base:** `31a86456`.
