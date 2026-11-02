# H-274 Survey: QA server-side messages and checks

Survey only — no code changes. Base: f5d2d577.
**Amendment 1** (SM review of 92c45e2d): corrected (b) DEBUG premise,
corrected (d) with reproduction results, updated (c) mapping approach,
added (e.1) upload door audit, updated (f.2) INSUFFICIENT_CREDITS analysis.

---

## (a) Sign-in: wrong email or password

**Code path:** `users/serializers.py:465` `CustomTokenObtainPairSerializer.validate()`

**Current behaviour:**

- Line 488–509: if the account is locked, raises `EnvelopedAuthenticationFailed`
  with `"Too many failed login attempts. Please try again later."` (401,
  reason_code `ACCOUNT_LOCKED`). This path is correct.
- Line 511–531: when `super().validate(attrs)` raises `AuthenticationFailed`
  (simplejwt's own error), the serializer catches it, emits an audit event
  (`WRONG_PASSWORD` or `INVALID_CREDENTIALS`), and **re-raises the original
  exception unchanged** (`raise` at line 531).

**Current text reaching the teacher:**
simplejwt's `TokenObtainPairSerializer.validate()` raises
`AuthenticationFailed("No active account found with the given credentials")`
(from `rest_framework_simplejwt.serializers`, via `django.contrib.auth.authenticate`
returning None). That text is what the frontend receives as `detail`.

**Proposed fix:**
Replace the bare `raise` at line 531 with a new `EnvelopedAuthenticationFailed`,
following the precedent at line 504. Proposed text:
`"That email and password don't match. Check them and try again, or reset your password."`
Same 401 status. Use `reason_code="INVALID_CREDENTIALS"` (already in
`audit.enums.ReasonCode`) and `code_value="invalid_credentials"`.

The `EnvelopedAuthenticationFailed` path (line 79–87 in `users/exceptions.py`)
adds the coded envelope to DRF's response without changing status or headers,
so the frontend still gets a 401.

**Invariants preserved:**
- `AUTH_LOGIN` audit events with `WRONG_PASSWORD` or `INVALID_CREDENTIALS`
  reason emitted before the raise — unchanged.
- `user.register_failed_login()` at line 517 runs before the raise — lockout
  behaviour unchanged.
- The response text is the SAME for wrong-password and non-existent-email
  (no account enumeration).

**Changes:**
- `users/serializers.py` ~line 531: replace `raise` with
  `raise EnvelopedAuthenticationFailed(...)`.
- No changes to audit emit calls.
- No edits to `users/exceptions.py` (RB file) — only imports from it.

**Tests:**
- New: wrong-password POST → 401 with the new text and coded envelope.
- New: non-existent-email POST → same text (no enumeration).
- Confirm: locked-account path still returns its own message (unchanged).
- Mutant: replace the message text → test fails (proves the text is asserted).

---

## (b) Checkout "No such customer" (BUG-009)

### Correction: DEBUG is False on beta

`AutoGrader/settings.py:133–144`: `DEBUG=False` for ENVIRONMENT `prod` AND
`dev`. Beta runs `ENVIRONMENT=prod` (per H-246). Only `local` is `True`.
The original survey's theory that the traceback leaks via DEBUG=True is wrong.

### Exhaustive audit of Stripe-text-in-response sites

Grepped `billing/` for every `str(exc)`, `str(e)`, `.user_message`, `.message`
that reaches a `Response` or `ValidationError`. Findings:

**Sites that CAN echo provider text (StripeError → response body):**

None found on the non-staff path. Every `except StripeError` handler in the
billing views uses either `describe_stripe_error(exc, fallback_message=...)`
(which only passes through `user_message` for `CardError`, and returns the
fallback for all others including `InvalidRequestError`), or
`stripe_error_label(exc)` (which only logs the class name and request id,
never sent to the client).

`stripe.error.InvalidRequestError("No such customer: cus_...")` confirmed:

- `str(exc)` → `"No such customer: cus_..."` — provider text.
- `exc.user_message` → `"No such customer: cus_..."` — Stripe fills
  `user_message` for ALL error types, not just CardError (confirmed by test).
- `describe_stripe_error` does NOT pass it through (not a CardError).

**Known leads (not in scope for fix):**

1. `billing/qa_time_travel.py:904–907` — returns raw Stripe text by design
   (`f"Stripe error: {getattr(exc, 'user_message', None) or str(exc)}"`).
   Staff-only endpoint. Leave as-is.
2. `billing/views.py` ValueError `except` handlers at several sites use
   `describe_user_error(exc, fallback_message=...)` — safe, because
   `ValueError` text is always app-authored (from `BillingRequestError` or
   `IndividualPlanChangeService` validation). Not a Stripe text leak path.

**Sites that use `str(exc)` in responses (non-Stripe exceptions):**

| File:line | Exception type | Safe? |
|---|---|---|
| `billing/payment_method_views.py:59` | `ValueError` (from `get_customer_for_request_user`) | Yes — app text |
| `billing/license_views.py:95,97` | `LicenceStripeChangeNotRecorded` (fixed text, docstring says so) | Yes |
| `billing/license_service.py:858,945` | `IndividualSubscriptionConflictError` / `ValueError` | Yes — app text |
| `billing/serializers.py:335` | `PlanAssignmentRefused` | Yes — app text |
| `billing/serializers.py:2239` | `LicenseRequestError` | Yes — app text |

**Conclusion: with `DEBUG=False`, no current code path returns raw Stripe
text to the browser.** The four uncaught `Session.create` sites produce a
generic 500 with `"An unexpected error occurred. Please try again."` The QA
observation of "No such customer" may have been: (1) from a Sentry alert
forwarded to the teacher, (2) from browser dev-tools on a local environment,
or (3) before H-255/H-227 hardened the error handling.

### Root cause: stale customer id

`StripeCustomerService.get_or_create_customer` (`billing/stripe_service.py:333`)
returns `wallet.stripe_customer_id` without checking that the customer still
exists at Stripe. Any stale id makes every later checkout call fail with
`InvalidRequestError("No such customer: cus_...")` until someone edits the
database. Causes:

- A QA test-clock customer was deleted with its clock.
- A database was copied between environments (staging → local, or vice versa).
- A test/live key switch made all stored customer ids point at nothing.

`get_or_create_license_customer` (~line 364) has the same problem: it returns
`license_sub.stripe_customer_id` without verification.

### Fix: self-healing retry on resource_missing

When a `stripe.checkout.Session.create` (or any downstream Stripe call using
the stored customer id) raises `InvalidRequestError` with
`exc.code == "resource_missing"` **AND `exc.param == "customer"`**, the
checkout helper:

1. Saves the stale id, then clears it with a **conditional update**:
   `CreditWallet.objects.filter(pk=…, stripe_customer_id=stale).update(stripe_customer_id="")`.
   Re-reads the row. If another request already stored a new id (update
   returned 0 rows affected), uses that id instead of creating another.
2. If the id was successfully cleared, calls `get_or_create_customer` /
   `get_or_create_license_customer` again to create a fresh Stripe customer
   with a **new idempotency key** that includes the stale id:
   `customer-for-user-{id}-replacing-{stale_id}` (or the licence equivalent).
   The new key is needed because the old key (`customer-for-user-{id}`)
   replays the deleted customer for 24 hours.
3. Retries the `Session.create` once with the new customer id. The
   `Session.create` retry uses its own key scheme (different from the failed
   attempt's key).
4. If the retry also fails, raises `BillingRequestError` with user-friendly
   text (no Stripe text leaked).
5. Logs the stale and new customer ids (ids only, no email/name) and the
   user/licence pk.

**Only heals a missing CUSTOMER.** A `resource_missing` with
`exc.param != "customer"` (e.g. a missing price) goes straight to the
fallback — no customer recreation, no retry.

**Concurrency:** The conditional update (step 1) plus the shared
idempotency key on `Customer.create` (step 2) ensure that two requests
hitting the same stale id don't create two customers. If request A clears
the id and request B's conditional update finds it already cleared, B
re-reads the row and picks up A's new customer id.

Capped at one retry — a second consecutive `resource_missing` means a deeper
issue and falls through to the coded 4xx.

### Four uncaught Session.create calls (still need wrapping)

| # | File:line | Method | Caller |
|---|-----------|--------|--------|
| 1 | `billing/stripe_service.py:646` | `create_individual_checkout_session` | `IndividualPlanChangeService.select_plan` → `billing/views.py:1299` |
| 2 | `billing/stripe_service.py:774` | `create_license_session` | `billing/license_views.py:313` |
| 3 | `billing/stripe_service.py:840` | `create_license_conversion_session` | `billing/license_views.py:933` |
| 4 | `billing/stripe_service.py:1411` | `create_upgrade_checkout_session` | `IndividualPlanChangeService.select_plan` → `billing/views.py:1299` |

Each gets the self-healing retry wrapper. An unwrapped `StripeError` that is
NOT `resource_missing` (or a second consecutive one) raises
`BillingRequestError` with a user-friendly message. `BillingRequestError` is a
`ValueError` subclass, so the existing `except ValueError` in each view
catches it. No view changes needed.

**Proposed text (non-recoverable fallback):**
`"We couldn't start checkout for your account. You haven't been charged. Please contact support."`
("You haven't been charged" is true — checkout sessions collect payment on
redirect, not at creation.)

### Tests

**Self-healing (individual path):**

- Stub `stripe.checkout.Session.create` to raise
  `InvalidRequestError("No such customer: cus_stale123", param="customer", code="resource_missing")`
  on first call, return a mock session on second call.
- Stub `stripe.Customer.create` to return a mock customer with a new id.
- Assert status is 200 (checkout session created).
- Assert `wallet.stripe_customer_id` changed (stale id cleared, new id stored).
- Assert `Customer.create` was called with an idempotency key that includes
  the stale id (e.g. `customer-for-user-{id}-replacing-{stale_id}`).
- Assert the `Session.create` retry used a different idempotency key than
  the failed attempt.

**Self-healing (license path):**

- Same as above, for `create_license_session` and
  `create_license_conversion_session`.
- Assert `license_sub.stripe_customer_id` changed.
- Assert `Customer.create` idempotency key includes the stale id.

**Double failure gives coded 4xx:**

- Stub `Session.create` to raise `resource_missing` (param="customer") on
  BOTH calls.
- Assert status is 400 (not 500).
- Assert response body contains the user-friendly fallback text.
- Assert response body does NOT contain `"cus_"` or `"No such customer"`.

**Non-customer resource_missing gives fallback (no retry):**

- Stub `Session.create` to raise `resource_missing` with
  `param="price"` (not "customer").
- Assert status is 400 with user-friendly fallback text.
- Assert `wallet.stripe_customer_id` is unchanged (stale id still stored).
- Assert `stripe.Customer.create` was NOT called.

**Non-resource-missing StripeError gives coded 4xx:**

- Stub `Session.create` to raise a generic `StripeError`.
- Assert status is 400 with user-friendly text.

**Concurrency (individual + licence):**

- Use `run_concurrently` from `AutoGrader/tests_concurrency_harness`.
- Two parallel requests with the same stale customer id.
- Assert both succeed (or one succeeds and one gets the new customer).
- Assert only ONE `Customer.create` call was made (or both used the same
  idempotency key, so Stripe returns the same customer).
- Assert `wallet.stripe_customer_id` ends at a single new value.
- Same test on the licence row.

**Mutants:**

- M1: Remove the retry → stale-id test fails (gets 400 instead of 200).
- M2: Reuse the original `Customer.create` idempotency key →
  idempotency-key assertion on `Customer.create` kwargs fails.
- M3: Remove the customer id clear → retry also gets `resource_missing`.
- M4: Let Stripe text through in the fallback → "No such customer"
  assertion fails.
- M5: Drop the `exc.param == "customer"` check → non-customer
  resource_missing test fails (customer id changed when it shouldn't have).

### (b) SM rulings B1–B3 (2026-11-02), replacing parts of the plan above

- **B1, site 4 `create_upgrade_checkout_session`: wrap only.** Its customer
  id comes from `stripe.Subscription.retrieve(...)["customer"]`, the owner of
  the live subscription, not from the wallet, so a stale wallet id can't
  reach it. Healing would make the upgrade payer a different customer from
  the subscription owner. Any StripeError becomes `BillingRequestError` with
  the approved text. No clear, no `Customer.create`, no retry.
- **B2, site 2 `create_license_session`: clear that row, retry with
  `customer_email`.** This path has no `Customer.create`: the customer is
  `existing_license.stripe_customer_id` from the school's latest licence row
  with a non-null id. On `resource_missing` with `param == "customer"`:
  - `LicenseSubscription.objects.filter(pk=existing.pk, stripe_customer_id=stale).update(stripe_customer_id=None)`;
  - retry once with `customer_email=admin_user.email` and no `customer`, the
    path a school with no billing history takes.

  **This replaces the test line "Assert `Customer.create` idempotency key
  includes the stale id" for site 2**, because no customer is created here.
  The site 2 test asserts instead that the row's id is cleared and that the
  retry sends `customer_email` and no `customer`.
- **B3 (i): clear to `None`, not `""`.** The fields are `null=True`, and site
  2's query is `stripe_customer_id__isnull=False`, which `""` would still
  match (and then send `customer=""`).
- **B3 (ii): no new idempotency keys on `Session.create`.** None of the 4
  calls has one today. The tests assert the retry carries none, so no key is
  reused.

### (b) Expectations (written before the red run)

Tests: `billing/tests_h274_checkout_stale_customer.py`, 16 tests. Stripe is
always mocked by `FakeStripe`, which patches `stripe.checkout.Session.create`
and `stripe.Customer.create`, and also `Subscription.retrieve` and
`Invoice.create_preview` for site 4. `FakeStripe` derives a customer id from
its idempotency key, as Stripe's replay does.

| Class | Door | Tests |
|---|---|---|
| `IndividualCheckoutTests` | site 1, HTTP `select-plan` | stale replaced → 200 (key `customer-for-user-{id}-replacing-{stale}`, wallet updated, sessions `[stale, new]`, no session key); replacement logged with ids and no email; second missing → fallback 400, no `cus_`/`No such`; missing price → fallback, not healed; other StripeError → fallback |
| `LicenseConversionTests` | site 3, HTTP `convert-to-stripe` | stale replaced → 200 (licence key, row updated); second missing → fallback; missing price → not healed; other error → fallback |
| `LicenseCreateSessionTests` | site 2, service | stale → row cleared to `None`, retry with `customer_email`, no `customer`, no `Customer.create`; missing price → not healed; other error → fallback |
| `UpgradeCheckoutTests` | site 4, service | missing customer → fallback, 1 session, no `Customer.create`, wallet unchanged; other error → fallback |
| `StaleCustomerConcurrencyTests` (TransactionTestCase, `run_concurrently`) | sites 1 and 3 | two requests meet on the stale id: no errors, each makes one failed attempt and one retry, every `Customer.create` uses the one replacing key, and the row ends on that one customer (no lost update) |

Red run, on the tip before the fix (tests only): `Ran 16 tests`,
`FAILED (failures=11, errors=5)`.

- 9 HTTP tests (sites 1 and 3) FAIL with `500 != 200` or `500 != 400`,
  except the logging test, which FAILs on no WARNING from
  `billing.stripe_service`. Today the exception is uncaught and the app's
  handler answers with the generic 500 envelope.
- 5 service tests (sites 2 and 4) ERROR with the raw
  `InvalidRequestError` / `APIConnectionError`.
- 2 concurrency tests FAIL on `errors != []`.

Fix: `billing/stripe_service.py` only. A helper runs `Session.create` and
turns any StripeError into `BillingRequestError(FALLBACK)`, logging only
`stripe_error_label`. An optional `heal(stale_id)` gives the retry kwargs,
and it is only used when `code == "resource_missing"` and
`param == "customer"`.

- Sites 1 and 3 heal through new
  `StripeCustomerService.replace_stale_customer` /
  `replace_stale_license_customer`. Each does a conditional clear to `None`
  and logs the stale id and the pk, then calls `get_or_create_*` (which
  re-reads the row) with `replacing=stale`, giving the key suffix
  `-replacing-{stale}`. The new id is then logged.
- Site 2 heals per B2.
- Site 4 passes no heal.

### (b) Mutants (written before the mutant run, fix at 1ec54b6c)

One line of `billing/stripe_service.py` changes per mutant. Each runs in a
disposable worktree against `billing.tests_h274_checkout_stale_customer`
only, and must be KILLED.

| Id | Line | Mutation | Must fail (at least) |
|---|---|---|---|
| M1 | 346 | retry → `raise BillingRequestError(CHECKOUT_FAILED_MESSAGE)` (no retry) | heal tests, sites 1, 2, 3; both concurrency tests |
| M2 | 316 | `_customer_key` → `return base` (reuse the original key) | site 1 and 3 heal key assertions; both concurrency tests |
| M3 | 455 | wallet clear matches nothing (`stripe_customer_id="__never__"`) | site 1 heal + logging tests; individual concurrency test |
| M3b | 471 | licence clear matches nothing | site 3 heal test; licence concurrency test |
| M3c | 878 | site 2 row clear matches nothing | site 2 heal test (row not `None`) |
| M4 | 329 | fallback carries Stripe's text (`+ " " + str(exc)`) | every fallback test (text and `cus_`/`No such` checks) |
| M5 | 323 | `param == "customer"` check → `True` | the 3 missing-price tests |
| M6 | 756 | site 1 `heal=heal` → `heal=None` | site 1 heal + logging tests; individual concurrency test |
| M7 | 986 | site 3 `heal=heal` → `heal=None` | site 3 heal test; licence concurrency test |
| M8 | 895 | site 2 heal → `None` | site 2 heal test |
| M9 | 889 | site 2 retry `customer_email` → `None` | site 2 heal test |
| M10 | 1545 | site 4 unwrapped (call `Session.create` directly) | both site 4 tests (ERROR, raw Stripe error) |

Site 4 has no heal mutant. Under B1 it has no heal to break: "site 4 never
heals" is the absence of a `heal=` argument, and
`test_a_missing_customer_is_not_healed_here` pins it with 1 session, no
`Customer.create` and the wallet unchanged.

### (b) Results (fixes-worker, 2026-11-02)

- **Red run** at f8d28a64 (tests only): `Ran 16 tests`,
  `FAILED (failures=11, errors=5)`, test by test as expected. Log
  `b/red_run_f8d28a64.txt` (committed in 1ec54b6c), sha256
  `e1183905dc0f7e0af5706042f89fe7719f920f007a2496f21fb5c0024daba293`.
- **Fix** at 1ec54b6c, `billing/stripe_service.py` only. On the committed
  tip, `billing.tests_h274_checkout_stale_customer` plus
  `billing.tests_h265_user_facing_errors`: `Ran 44 tests`, `OK`.
- **Mutants**, run in a disposable worktree at 84a42d52. The committed blob
  of `billing/stripe_service.py` (sha256
  `0929e020d8141443cce1369393b8bc63f68429358f97475389c2a22ef842f453`) was
  restored after each mutant and checked with sha256. The worktree was then
  removed, and no `test_h274*` DB is left. **12/12 KILLED**, each with at
  least the failures expected and no `_FailedTest`. The logs were committed
  in a2ddc95a, without this NOTES text: my NOTES script failed on a path
  error and the commit ran anyway. This commit adds the text.

| Id | Result | Failed | vs expectations | Log sha256 (`b/mutants/`) |
|---|---|---|---|---|
| M1 | KILLED | 8 (7 F, 1 E): heal tests sites 1, 2, 3; logging; both second-missing tests (they count 2 sessions); both concurrency tests | as expected, plus the second-missing and logging tests | `b467b2ee0743810066da8804489d3159ec5952890436e2096746a7664dc4f49a` |
| M2 | KILLED | 5: site 1 and 3 heal; logging (new id is derived from the key); both concurrency tests | as expected, plus logging | `b3752abd114a7dec61c204741befa030ef78ee5a61b6bfc20b96c5291cf6078e` |
| M3 | KILLED | 4: site 1 heal, logging, second-missing (2 `Customer.create` → 0), individual concurrency | as expected, plus second-missing | `ecdff3be64bc6eae96a095469f5f2243c07443fde8bcca6f6faa9a2b08e04658` |
| M3b | KILLED | 2: site 3 heal, licence concurrency | as expected | `43a69ef6536dc0ca5b1abdaa334230354bedbac8667f88e60b3efcb7aa59f55a` |
| M3c | KILLED | 1: site 2 heal | as expected | `298599008ef5027dda4921caf919d4ca17ae7d07683b4c3a88268d32c5860148` |
| M4 | KILLED | 9: all fallback tests except site 2 missing-price | **differs**: site 2's `test_a_missing_price_is_not_healed` survives, because `assertRaisesMessage` checks containment and the appended text still contains the fallback. Killed by the other 9. | `a0fdc22bca79efac40f998280616e027fa285bdd019b3599e236e38fd4c8f580` |
| M5 | KILLED | 3: the 3 missing-price tests | as expected | `72babb73034e544aed0191623523e8feb734061fc4bd6c037244dd803d005bd2` |
| M6 | KILLED | 4: site 1 heal, logging, second-missing, individual concurrency | as expected, plus second-missing | `d3d667e194667cf7d400397c7c06bf6039f517e8f41d4543c6aedb2a0e75c2e5` |
| M7 | KILLED | 3: site 3 heal, second-missing, licence concurrency | as expected, plus second-missing | `05a9817791a91929a590ae600ba9946aad4fb9db64fefd059055e84f7d661b7e` |
| M8 | KILLED | 1 ERROR: site 2 heal (the fallback BillingRequestError is raised) | as expected (ERROR, not FAIL) | `f4a35acb7f90975ea92284366ab13dc3f4fe763bef69b9c2481ec16e9de0fa57` |
| M9 | KILLED | 1: site 2 heal | as expected | `562a7a60b22e2a37d578278550432de17a5eff0f6fe8a37cfaa48efb6e5c2219` |
| M10 | KILLED | 2 ERROR: both site 4 tests (raw Stripe error) | as expected | `bccb72a33f46db1bc14ccf25642123bbc6d691800df53b005a37518d6646c5d2` |

---

## (c) Invoice descriptions — backfill command (BUG-013)

**Code path:** `billing/management/commands/backfill_billing_transactions.py`

**Current text:** Three description patterns:
1. `f"Backfilled from checkout.session.completed ({flow})"` — e.g.
   `"Backfilled from checkout.session.completed (overage_block_purchase_checkout)"`.
2. `f"Backfilled from invoice.payment_{'succeeded' if succeeded else 'failed'} ({billing_reason})"`.
3. For offline records: `record.notes or record.get_record_type_display()`.

**Approach: read-time display mapping from structured fields (approved).**

Map from the transaction's own structured fields (`transaction_type`,
`user_subscription.plan`, `license_subscription.plan`, `amount_cents`) where
they exist. Do NOT parse the description text. Fall back to the stored
description only when it is NOT a `"Backfilled from"` pattern; use a generic
readable label (`"Transaction"`) for unrecognised backfill descriptions.

**Mapping by `BillingTransactionType`:**

| `transaction_type` | Display text |
|---|---|
| `INDIVIDUAL_SUBSCRIPTION_CHARGE` | `"Subscription – {plan_name}"` |
| `INDIVIDUAL_TRIAL_CONVERSION_CHARGE` | `"Subscription – {plan_name}"` |
| `INDIVIDUAL_UPGRADE_CHARGE` | `"Plan upgrade – {plan_name}"` |
| `INDIVIDUAL_OVERAGE_PURCHASE` | `"Credit top-up – {credits} AI credits"` |
| `LICENSE_INITIAL_CHARGE` | `"License – {plan_name}"` |
| `LICENSE_SUBSCRIPTION_CHARGE` | `"License renewal – {plan_name}"` |
| `LICENSE_PLAN_CHANGE_CHARGE` | `"License plan change – {plan_name}"` |
| `LICENSE_OFFLINE_RENEWAL` | stored description (already readable) |
| `LICENSE_OFFLINE_PLAN_CHANGE` | stored description (already readable) |
| `LICENSE_OFFLINE_MANUAL_OVERAGE_GRANT` | stored description |
| (any other) | stored description, unless it starts with `"Backfilled from"` → `"Transaction"` |

The plan name comes from `transaction.user_subscription.plan.display_name` or
`transaction.license_subscription.plan.display_name`. Credits come from the
overage block size on the plan, or from `amount_cents / plan.overage_block_price`
if available, else omitted.

**Row count query (for beta/staging — NOT to run against live):**

```sql
SELECT description, COUNT(*) AS n
FROM billing_billingtransaction
WHERE description LIKE 'Backfilled from %'
GROUP BY description
ORDER BY n DESC;
```

**Tests:**
- Unit test the mapping function with each transaction type.
- Test that a "Backfilled from" description is replaced.
- Test that a non-backfill description passes through unchanged.
- Test that a backfill description with no matching transaction type → `"Transaction"`.

**Touches Release Builder files?** No.

### (c) Owner decision (2026-11-02, via SM): won't change

The owner decided: "ignore backfilled data". **BUG-013 is closed as
won't-change for backfilled rows.** There is no code change for (c): no
mapping and no serializer change. Backfilled rows keep their stored text, and
live rows keep theirs. The mapping above is **superseded** and must not be
built.

Reasons (fixes-worker findings, read-only, at d0f4be8e):

- **F1. Plan drift.** Plans are changed in place on the same subscription row
  (`billing/services.py:641` `user_sub.plan = new_plan`, and `:1899`;
  `billing/license_service.py:2246` and `:2528`). A transaction's
  `user_subscription.plan` / `license_subscription.plan` is therefore the
  CURRENT plan, not the plan at charge time. Under the table above, a January
  "Basic" charge would read "Subscription – Pro" after a March upgrade, which
  is wrong money history. Backfilled rows link the current subscription too
  (`backfill_billing_transactions.py:160`, `:227`), so they drift the same way.
- **F2. Live rows already carry accurate, at-the-time text and operational
  notes.** An AST walk of all 25 `BillingTransactionService.record` call
  sites found, for example, "New subscription — {plan}", "Trial converted to
  {plan}", "…credits NOT granted, needs manual refund."
  (`stripe_service.py:3152`) and "…needs manual review." (`:3501`). Applying
  the type table to every row would replace them with drifting plan names and
  hide those notes.
- **F3. Gaps in the table.** `LICENSE_SEAT_CHANGE_CHARGE`,
  `LICENSE_OVERAGE_PURCHASE`, `LICENSE_OFFLINE_OVERAGE_PURCHASE` and `OTHER`
  are not listed. The "plan missing" and "credits unknown" cases have no
  approved text. A credits count can't be derived honestly, because the
  plan's block size is today's value and individual rows have no snapshot.

**Q4 (with the owner, out of scope here):** live invoice rows still show raw
Stripe terms, e.g. "Subscription invoice paid (subscription_cycle)". Not to be
touched in H-274 unless the owner decides otherwise.

---

## (d) Creating a session gives 500 when session name is empty

### Reproduction results (committed failing test: `classrooms/tests_h274_session_empty.py`)

Ran 7 cases against the live code at f5d2d577:

| Input | Status | Result |
|---|---|---|
| `name: ""` | 400 | DRF validates `allow_blank=False` |
| `name: null` | 400 | DRF validates `allow_null=False` |
| `name: "   "` | 400 | DRF trims → `""` → `allow_blank=False` |
| missing `name` | 400 | DRF validates `required=True` |
| `id: "", name: "Valid"` | 201 | `id` is read_only, ignored by DRF |
| `id: null, name: "Valid"` | 201 | same |
| `name: "Fall 2026"` | 201 | valid creation works |

**All seven pass.** None produce a 500. The `Session` model's
`CharField(max_length=100)` with `blank=False` (default) causes DRF's
`ModelSerializer` to generate `CharField(allow_blank=False, required=True)`,
which rejects empty/null/whitespace at the serializer layer.

**Conclusion:** The 500 reported in the QA catalogue §3 cannot be reproduced
with these inputs. Possible explanations:
1. It was fixed by a prior change (perhaps the session ownership rework).
2. It involved a different endpoint or a different "session" concept.
3. It was a frontend-induced error that looked like a session creation 500.

**No code change needed.** The test file stays as regression coverage. If the
SM has the exact request payload that triggered the 500, I can retry with it.

---

## (e) Server-side checks: empty uploads and password reuse

### (e.1) Empty PDF upload accepted (BUG-005)

**Code path:** `students/views.py:580–606` (sync upload) and
`students/views.py:685–717` (async upload).

**Current validation:**
1. `isinstance(uploaded_file, UploadedFile)` — rejects non-file payloads.
2. `validate_upload_size(uploaded_file)` (`AutoGrader/uploads.py:56`) — only
   checks `size > max_size_bytes`. **A 0-byte file passes.**
3. No content-type validation at the view level.

**Every upload door that calls `validate_upload_size`:**

| # | File:line | Endpoint | Takes files? |
|---|-----------|----------|-------------|
| 1 | `students/views.py:588` | sync submission upload | Yes — single file |
| 2 | `students/views.py:692` | async submission upload | Yes — single file |
| 3 | `students/views.py:1464` | (another upload path) | Yes — single file |

No roster/bulk import path calls `validate_upload_size` — those are JSON
payloads, not file uploads.

**Proposed fix:**
New `ReasonCode.FILE_EMPTY` in `audit/enums.py` and a `ReasonSpec` in
`AutoGrader/reason_codes.py`. Message (owner style):
`"'{file_name}' is empty. Choose another file."`
Status: 400. Error class: USER. Not retryable.

Add to `validate_upload_size` in `AutoGrader/uploads.py`:
```python
if uploaded_file.size == 0:
    raise CodedError(ReasonCode.FILE_EMPTY,
                     params={"file_name": file_name_of(uploaded_file)})
```

This covers all three upload doors in one change.

**Tests:**
- Upload a 0-byte file at each door → coded 400 with `FILE_EMPTY`.
- Upload a valid file → still works.

### (e.2) Empty profile photo accepted (BUG-028)

**Needs reproduction.** `validate_image_format` (`AutoGrader/image_formats.py`)
calls `Image.open(BytesIO(value.read()))` which should raise
`UnidentifiedImageError` for a 0-byte file, caught and turned into
`ValidationError("Upload a JPEG, PNG, GIF or WEBP image.")`.

Will write a test that uploads a 0-byte image as `profile_image` to confirm.
If the server refuses it, BUG-028 is frontend-only — no code change.

#### (e.2) Reproduction result (fixes-worker, 2026-11-02, tip d297ddbf, no code change)

Test: `users/tests_h274_empty_profile_photo.py`, 8 tests. Run on the unchanged
tip: `Ran 8 tests ... OK`. Log: `e2/repro_run_d297ddbf.txt` (336 lines,
sha256 `9d9d8483f3a3b20fd9200f769869496158b46c5ecc44b90be26d3fbf953d36cd`).
Test DB `test_h274_qa_server_messages` destroyed by the runner, 0 connections after.

Every door refuses a 0-byte file before `validate_image_format` runs. The
refusal comes from DRF's `FileField` / Django's `forms.FileField`
(`allow_empty_file=False`, code `empty`), not from app code:

| Door | How driven | Result |
|---|---|---|
| `PATCH /users/<id>` (CustomUserSerializer), 0-byte `face.png` | HTTP, real JWT, multipart | 400, `field_errors.profile_image = ["The submitted file is empty."]`, nothing stored |
| same, 0-byte `face` (`application/octet-stream`) | HTTP | 400, same |
| same, 0-byte upload over an existing photo | HTTP | 400, existing photo name and file unchanged |
| same, 12 non-image bytes as `face.png` | HTTP | 400 (Pillow/DRF "Upload a valid image…") |
| DirectAddStudentSerializer | serializer | invalid, code `empty` |
| SchoolWithAdminSerializer (`admin_profile_image`) | serializer | invalid, code `empty` |
| StudentRegistrationCompletionSerializer | serializer | invalid, code `empty` |
| Admin `_ProfileImageForm` | form | invalid, code `empty` |

**Conclusion:** a 0-byte photo is refused server-side, so BUG-028 as a 0-byte
upload is frontend-only. No server code change. The tests stay as permanent
regression coverage.

**Open for SM (not changed, not tested as a rule):** a multipart PATCH with
`profile_image=""` (an empty form value, not a file) returns **200 and removes
the stored photo** (`profile_image` → null), because the model field is
`blank=True, null=True`. This may be what QA saw as "empty photo accepted" if
the frontend sends an empty value when no file is picked. Whether that is the
intended "remove photo" path is a product decision.

Also not changed: the refusal text is DRF's default, "The submitted file is
empty.", not the owner style used for (e.1) ("'{file_name}' is empty. Choose
another file."). Rewording it would be new user-facing text, so it waits for SM.

#### (e.2) Mutation (SM-requested, 2026-11-02)

Disposable worktree detached at d1221597, own test DB `test_h274_mut_e2`
(destroyed by each run). Mutated file `users/serializers.py`, restored each
time from `git cat-file blob HEAD:users/serializers.py`, sha256
`6952b12923c1375db4584983d90b0819046c128605a5d757ddd4317f1156e4f7` confirmed
after every restore. Worktree removed after. Logs in `e2/mutants/`.

| Mutant | Change (CustomUserSerializer.profile_image) | Expected to catch | Result |
|---|---|---|---|
| M-e2.1 | extra_kwargs `allow_empty_file=True` | the 4 HTTP tests | **Survived** (Ran 8, OK). Log sha256 `6e0d27743eb6293a34ebc1cf92090121f66a3c4c20d8759cfa85d72edb187ae9` |
| M-e2.2 | M-e2.1 plus `validators=[]` (drops validate_image_format) | the 4 HTTP tests | **Survived** (Ran 8, OK). Log sha256 `035e1c288a04161049c06e0b9a484bcce9152e137e6b3c1a5ef38122d45dfb33` |
| M-e2.3 | field replaced by `serializers.FileField(allow_empty_file=True)`, no validators | the 4 HTTP tests | **Killed**: 4 errors, `KeyError: 'field_errors'`; the save hook `to_profile_webp` raises `PIL.UnidentifiedImageError` on the empty file, so the request is no longer a clean 400. Log sha256 `0b01b7ace09f14240f10ce5c2c195ac5c7bf69bb4e5a5a96c844c0bcc87367d1` |

Why M-e2.1 and M-e2.2 survive (equivalent, argued for SM acceptance): DRF's
`ImageField.to_internal_value` hands the file to Django's
`forms.ImageField`, which keeps its **own** `allow_empty_file=False` and its
own Pillow `verify()`. The DRF flag and `validate_image_format` are layers
two and four of four. Under M-e2.1 the response is still
`["The submitted file is empty."]`, code `empty` (checked directly in the
mutant worktree). Removing one layer leaves the refusal and its text
unchanged, so no test that only watches the HTTP outcome can tell them apart.
The guard that matters is "the field is an ImageField", and M-e2.3, which
removes it, is killed.

(e.2b) will add an app-owned refusal with the FILE_EMPTY text. Its mutant
will remove that new guard, and the text assertion will catch it.

#### (e.2b) Expectations (written before the red run, 2026-11-02)

Goal: a 0-byte profile photo is still refused (400) at all 5 doors, but the
text is now (e.1)'s FILE_EMPTY message, "'{file_name}' is empty. Choose
another file." (`AutoGrader/reason_codes.py`), in place of Django's "The
submitted file is empty." The error code stays `empty`. This reuses (e.1)'s
approved text; there is no new wording. It is a field error, not the coded
FILE_EMPTY envelope, because these doors report per-field errors.

Tests: the 8 (e.2) tests in `users/tests_h274_empty_profile_photo.py` now
assert the exact text. The 3 zero-byte HTTP tests check
`field_errors["profile_image"] == [text]`. The 4 serializer/form doors check
code `["empty"]` and messages `[text]`. The non-image test is unchanged.

Red run, on the tip with only the test change: `Ran 8 tests`,
`FAILED (failures=7)`, no errors. Each failure shows
`The submitted file is empty.` against the new text. Only
`test_bytes_that_are_not_an_image_are_refused` passes.

Fix (seam: `AutoGrader/image_formats.py`, `users/serializers.py`,
`classrooms/serializers.py`, `users/admin.py`):

- `ProfileImageField(serializers.ImageField)`: `to_internal_value` refuses
  `size == 0` with the FILE_EMPTY text, code `empty`, before DRF's and
  Django's own checks.
- `ProfileImageFormField(forms.ImageField)`: the same in `to_python`, for
  the admin form.
- Wiring:
  - `CustomUserSerializer.serializer_field_mapping` maps
    `models.ImageField` to `ProfileImageField`.
  - The 3 declared fields in `classrooms/serializers.py` (DirectAdd,
    RegistrationCompletion, SchoolWithAdmin `admin_profile_image`) use
    `ProfileImageField`.
  - `_ProfileImageForm.Meta.field_classes` sets `profile_image` to
    `ProfileImageFormField`.

Green: `Ran 8 tests`, `OK`, and (e.2a) stays `Ran 5`, `OK`.

Mutants (one per guard, disposable worktree, each must be KILLED; "old
text" = `The submitted file is empty.`):

| Id | Mutation | Must fail |
|---|---|---|
| M-e2b.1 | DRF guard `size == 0` → `False` | 3 zero-byte HTTP tests + 3 serializer doors, old text |
| M-e2b.2 | form guard `size == 0` → `False` | `test_the_admin_user_form_refuses_it`, old text |
| M-e2b.3 | drop `serializer_field_mapping` from `CustomUserSerializer` | the 3 zero-byte HTTP tests, old text |
| M-e2b.4 | DirectAdd `ProfileImageField` → `serializers.ImageField` | `test_direct_add_student_refuses_it` |
| M-e2b.5 | RegistrationCompletion → `serializers.ImageField` | `test_student_registration_completion_refuses_it` |
| M-e2b.6 | SchoolWithAdmin → `serializers.ImageField` | `test_school_with_admin_refuses_it` |
| M-e2b.7 | drop `field_classes` from `_ProfileImageForm.Meta` | `test_the_admin_user_form_refuses_it` |

#### (e.2b) Results (fixes-worker, 2026-11-02)

- **Red run** at faac76bb (tests only, no fix): `Ran 8 tests`,
  `FAILED (failures=7)`, no errors. Each failure was
  `['The submitted file is empty.'] != ["'face.png' is empty. Choose another file."]`
  (`'face'` for the octet-stream test). This matches the expectations.
  Log: `e2b/red_run_faac76bb.txt`, sha256
  `44d69e15e5a9f8e1e5bbee2a6bb05320a97e21b070efdf5e7158a26041c24d1b`.
- **Fix** at 0ed07dc8, 4 source files (the seam as written):
  - `AutoGrader/image_formats.py`: +34 lines, the two field classes and
    `_empty_upload_message`, which renders `REASON_CODES[FILE_EMPTY]`.
  - `users/serializers.py`: `serializer_field_mapping`.
  - `classrooms/serializers.py`: 3 fields.
  - `users/admin.py`: `field_classes`.

  Green on the working tree: (e.2) `Ran 8`, `OK`; (e.2a) `Ran 5`, `OK`;
  `makemigrations --check` reports no changes.
- **Mutants**, run in a disposable worktree at 0ed07dc8. Each mutated file
  was restored from its committed blob and checked with sha256:
  - `AutoGrader/image_formats.py`
    `41a14131473d4476e76e97b6dfb10b1fa64abb3048d6332d644762b0f63e6bf7`
  - `users/serializers.py`
    `af36b6200053d7608917a4675055a0486b8c173b5711e123dec375664d8648d1`
  - `classrooms/serializers.py`
    `7a9fb85ae475c0397f2a17b1d8e08051ed48b044a41f923b82e9cebc5cb3a86d`
  - `users/admin.py`
    `ef7e24ec9d859c3c9d129da066c081bff10486c1b5423d2ff095ab4e26cad1e1`

  The worktree was then removed, and no `test_h274*` DB is left. All 7 were
  KILLED, each with the failures that were expected and no others. Every
  failure shows the old text.

| Id | Result | Failures | Log sha256 (`e2b/mutants/`) |
|---|---|---|---|
| M-e2b.1 | KILLED | 6 (3 HTTP + 3 serializer doors) | `43d0bf26f200a1c0da44b3cbbec2e5b1ae6b5d76648de566e837bb5cf3fbd51d` |
| M-e2b.2 | KILLED | 1 (admin form) | `f4c5539794a9a2a4eceec528616c4148f4fb677ca64af8acf293ed088e671639` |
| M-e2b.3 | KILLED | 3 (HTTP) | `9790eced2a843a84170b2a4006c9e3c6e338aab415deb8173bb078f93fa3a939` |
| M-e2b.4 | KILLED | 1 (DirectAdd) | `f4eead649d768e3fb27d4cd224e774a433617471d7f9aad6d320ff84f5a9e39e` |
| M-e2b.5 | KILLED | 1 (RegistrationCompletion) | `ad18b50a9fac5c9c6ae4ea0cbac9eb0ba384e03147222de23595403d88611c5c` |
| M-e2b.6 | KILLED | 1 (SchoolWithAdmin) | `ba67ca0f3d8aca494ec6712caa5551f010292f9a06ca12b9e9cada85bbf4264d` |
| M-e2b.7 | KILLED | 1 (admin form) | `7d73b229301b9e9590ca35eb17695a900b9327f2195654c060b38f45b6dafeec` |

#### (e.2a) Owner decision and remove-photo survey (2026-11-02, read-only)

Owner decision (via SM): an empty form value `profile_image=""` must leave the
stored photo untouched (200, photo kept). SM condition: before shipping, find
whether an explicit remove-photo path exists, and STOP if `""` is the only one.

Survey at d0f4be8e (code search + a throwaway HTTP probe, deleted after):

- No dedicated endpoint or `@action` removes a photo. `users/views.py` has
  none; `CustomUserViewSet.partial_update` (line 356) only adds the
  own-account check. `profile_image_url` (Google picture) is read-only.
- PATCH `/users/<id>` behaviour with a stored photo:

| Request | Status | Photo |
|---|---|---|
| JSON `{"profile_image": null}` | 200 | **removed** (`allow_null`, from the model's `null=True`) |
| multipart `profile_image=""` | 200 | **removed** (DRF maps an empty form value to `None` when `allow_null`) |
| JSON `{"profile_image": ""}` | 400 | kept ("The submitted data was not a file…") |
| multipart without `profile_image` (e.g. only `bio`) | 200 | kept |

**Finding:** `""` is not the only server-side removal path. JSON `null`
removes the photo and will keep doing so. Not knowable from this repo: which
of the two the frontend's remove button sends. If it sends multipart `""`,
(e.2a) will break that button until the frontend switches to JSON `null`.

**SM ruling (2026-11-02):** go. In multipart only, an empty `""`
`profile_image` is ignored (200, photo kept). JSON `{"profile_image": null}`
stays the removal path. **Frontend risk (SM is telling the owner):** if the
remove button sends multipart `profile_image=""`, it stops removing the photo
until the frontend sends JSON `null`.

#### (e.2a) Expectations (written before the red run)

Tests: `users/tests_h274_blank_photo_form_value.py`, class
`BlankPhotoFormValueTests` (5 tests, PATCH `/users/<id>` with a real JWT, a
photo stored in `setUp`).

Red run, on the tip before the fix (no source change):
`Ran 5 tests`, `FAILED (failures=2)`, no errors. The two failures are
`test_an_empty_form_value_keeps_the_photo` and
`test_an_empty_form_value_beside_other_fields_keeps_the_photo`, each with
`'' != 'profile_pics/` (the photo name was cleared). The other three pass:
`test_json_null_still_removes_the_photo`,
`test_a_new_photo_in_a_form_still_replaces_the_old_one`,
`test_a_json_empty_string_is_still_refused`.

Fix: `CustomUserSerializer.to_internal_value` in `users/serializers.py`. When
the input is a form (`rest_framework.utils.html.is_html_input`) and its only
`profile_image` value is `""`, drop `profile_image` from the validated data.
Seam: `users/serializers.py` only.

Green: `Ran 5 tests`, `OK`; the (e.2) module stays `Ran 8 tests`, `OK`.

Mutants (one per guard, disposable worktree, each must be KILLED):

| Id | Mutation | Must fail |
|---|---|---|
| M-e2a.1 | `if blank_photo:` → `if False:` (never drop) | both empty-form-value tests, `'' != 'profile_pics/` |
| M-e2a.2 | `== [""]` → `!= []` (drop any form photo) | `test_a_new_photo_in_a_form_still_replaces_the_old_one` |
| M-e2a.3 | `html.is_html_input(data)` → `True` (JSON too) | `test_json_null_still_removes_the_photo` (ERROR, `AttributeError` on a dict's `getlist`) and `test_a_json_empty_string_is_still_refused` |

#### (e.2a) Results (fixes-worker, 2026-11-02)

- **Red run** at d201c586 (tests only, no fix): `Ran 5 tests`,
  `FAILED (failures=2)`. The two empty-form-value tests failed with
  `'' != 'profile_pics/…'`, and the other three passed. This matches the
  expectations.
  Log: `e2a/red_run_d201c586.txt`, sha256
  `d6fc861507a9f17c3dc28fd179649cd0fb9f83a9d6f823679da235272eaf4ac4`.
- **Fix** at 937c0050: `users/serializers.py` only (+13 lines,
  `CustomUserSerializer.to_internal_value`). Green on the working tree:
  this module `Ran 5 tests`, `OK`; (e.2) module `Ran 8 tests`, `OK`.
- **Mutants**, run in a disposable worktree at 937c0050. The committed blob
  of `users/serializers.py` (sha256
  `b92bdf9c173425b0062a4ae2cf80bbc8a228ae647aea6429c3d6ef2180b2213f`) was
  restored after each mutant and checked with sha256. The worktree was then
  removed, and no `test_h274*` DB is left.

| Id | Result | What failed | Log sha256 (`e2a/mutants/`) |
|---|---|---|---|
| M-e2a.1 | KILLED | both empty-form-value tests, `'' != 'profile_pics/…'`, as expected | `5136a2cb9ec0cc07c7b3a66981644f934a629a6e1653101b712de64b3ed17218` |
| M-e2a.2 | KILLED | all 5. **Differs from expectations:** the mutant also drops the photo that `setUp` uploads, so every test fails at `setUp`'s `assertTrue(self.photo)` (`'' is not true`), the replace test included. The guard is still pinned, because any form upload being dropped fails the suite. | `a17f00ea74e5cf5cf60391c7864a980895ba85c75f4709421a2ef30e30f8d121` |
| M-e2a.3 | KILLED | both JSON tests. **Differs from expectations:** they FAIL with a 500 envelope (`500 != 200`, `500 != 400`), not ERROR. The `AttributeError: 'dict' object has no attribute 'getlist'` is caught by the app's exception handler and logged. | `87d9aee808b6293be4e673f2fc4a315fa212922fb516bd058ce7b5edf0e2f580` |

### (e.3) New password equal to current not refused (BUG-029)

**Already checked** at `users/serializers.py:626`. Reword only.

**Current text:** `"New password cannot be the same as the old one"`

**Proposed text:** `"Your new password must be different from your current password. Please choose a different one."`

---

## (f) Rewording of server texts

### (f.1) "Student already enrolled" (direct add)

**Code path:** `classrooms/services/enrollment.py:435`
```python
raise EnrollmentError("Student is already enrolled in this course.")
```

Caught at `classrooms/views.py:1434`:
```python
except services.EnrollmentError as exc:
    return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
```

**Current text:** `"Student is already enrolled in this course."`
**Proposed text:** `"A student with this email is already enrolled in this course."`
**Lives in reason_codes.py?** No.

Test at `classrooms/tests_h214_direct_add_answers.py:93` asserts the old text.

### (f.2) "Not enough AI credits" (INSUFFICIENT_CREDITS_MESSAGE)

**Code path:** `billing/errors.py:37`:
```python
INSUFFICIENT_CREDITS_MESSAGE = (
    "There aren't enough AI credits available for this. The credit wallet "
    "needs to be topped up before it can run."
)
```

Used by `AutoGrader/reason_codes.py:258–263` for `ReasonCode.INSUFFICIENT_CREDITS`.

**What the message can know:**

The `InsufficientCreditsError` at `billing/models.py:938` carries
`f"Requested: {amount}, Available: {total_available}"` — but
`billing/errors.py:33–36` and `AutoGrader/error_messages.py:76` deliberately
replace this with the fixed message because the exception's text can carry
"balance or deficit detail" that "belongs in server logs, never in an API
error body." Showing needed-vs-available would leak the teacher's credit
balance to anyone who can trigger the error.

**The message CANNOT show the shortfall without violating the privacy rule.**
The reword must stay generic.

**Proposed text (minimal change, B1 will touch this area later):**
`"There aren't enough AI credits to do this. Top up your credits or ask your school administrator, then try again."`

Changes from current: removes "The credit wallet needs to be topped up before
it can run" (technical, names an internal concept "credit wallet") and adds
"or ask your school administrator" (actionable for license teachers). Keeps
"not enough AI credits" (names the thing) and "top up / try again" (what to do).

**Lives in reason_codes.py?** Yes — `ReasonCode.INSUFFICIENT_CREDITS` at
line 258 uses `INSUFFICIENT_CREDITS_MESSAGE`.

### (f.3) "Wrong current password"

**Code path:** `users/views.py:1634`
```python
raise ParseError("Incorrect current password. Please try again.")
```

**Current text:** `"Incorrect current password. Please try again."`
**Proposed text:** `"Your current password is incorrect. Please check it and try again."`

**Lives in reason_codes.py?** `ReasonCode.WRONG_PASSWORD` exists in
`audit/enums.py:220` but is used only for the audit trail, not for the
response text.

---

### (f) Gap in d297ddbf: one test kept an old text (found 2026-11-02)

d297ddbf reworded (e.3), (f.1), (f.2) and (f.3) in source but left
`billing/tests/test_refusal_handling.py:78` `GENERIC_CREDITS_MESSAGE` on the
old (f.2) text, so 26 subtests failed (D10, D11, D2D3, D4) in a billing run.
With SM's approval, a test-only commit sets it to the approved (f.2) text. No
source change. After it, `billing.tests.test_refusal_handling`: `Ran 19`,
`OK`.

A `git grep` of every file for the four old texts found no other test.
The only other hit is `users/views.py:1619`, the OpenAPI description
"Incorrect current password, or invalid/expired OTP". That is schema
documentation, not a response text, so it is left as is.

## (g) Credit-limit text (H-268) — confirm only, NO CHANGE

**Code path:** `AutoGrader/reason_codes.py:280–301`

**Current text (H-268, owner's wording via SM 2026-10-27):**

Default variant (remaining > 0):
`"Purchase limit exceeded — max {max_blocks} blocks. Reduce your quantity to {remaining} or fewer to proceed."`

"cap_reached" variant (remaining == 0):
`"Purchase limit reached — you have used all {max_blocks} blocks for this billing cycle. You can buy more when your next cycle starts."`

Remediation:
`"Request fewer blocks, or wait until your next billing cycle, then try again."`

**Confirmed:** these are the owner's approved texts from H-268. **No change.**

---

## (b) Gate 3 and Gate 5 additions (expectations, written before the run)

Re-reading DOCTRINE H4 and H6 before the evidence turned up two gaps in the
(b) tests. H4 asks for at least 20 simultaneous operations for at least 10
rounds; I had 2 threads for 1 round. H6 asks for a failure injected at each
step of the external calls. The tests change; the fix (1ec54b6c) does not.

- `StaleCustomerConcurrencyTests` replaces the 2-thread tests:
  - sites 1 and 3, **20 threads × 10 rounds** each, real PostgreSQL
    (TransactionTestCase), `run_concurrently` (closes each thread's
    connection and fails on a live thread after the join);
  - each round sets its own stale id, and `FakeStripe(meet=20)` holds all
    20 first attempts until every thread has failed on it;
  - each round asserts: no errors; 20 sessions; 20 failed attempts on the
    stale id plus 20 retries on the one new id; every `Customer.create` uses
    that round's replacing key; the row ends on that one customer.
- Gate 5, on sites 1 and 3, 2 tests each:
  - `test_a_failed_replacement_gives_the_fallback_and_the_next_one_recovers`.
    `Customer.create` fails, giving the fallback 400 and the row id `None`
    (intentional). Then the next click hits the worst case: the plain key
    replays the deleted customer. That attempt fails as missing, the heal
    runs under the replacing key, and the result is 200 with keys
    `[plain, replacing]`.
  - `test_a_retry_timeout_gives_the_fallback_and_keeps_the_new_customer`.
    The retry times out, giving the fallback 400, with the new id stored
    (intentional, because it exists at Stripe). The next click gives 200 in
    one session on that id, with no `Customer.create`.

The module goes from 16 to **20** tests, and the full count from 8633 to
**8637**.

Expected on the fix: `Ran 20 tests`, `OK`. Expected on the pre-fix source
(f8d28a64 tree with this test file, disposable worktree): the 4 Gate 5 tests
FAIL with `500 != 400`, and the 2 concurrency tests FAIL on `errors != []`.

### (b) Gate 3 and Gate 5 additions: results

- **On the fix** (c9f61251): `Ran 20 tests`, `OK` (10.2 s). Log
  `b/gate35_on_fix_c9f61251.txt`, sha256
  `4f123ec02df301d4b434033c87419e8b0bf3d9e486a81b4e248afa545c77468a`.
- **On the pre-fix source** (f8d28a64 tree plus the c9f61251 test file,
  disposable worktree, removed afterwards, DB `test_h274_prefix_b` dropped
  by the runner): `Ran 20 tests`, `FAILED (failures=33, errors=5)`. As
  expected:
  - the 4 Gate 5 tests FAIL with `500 != 400`;
  - both concurrency tests FAIL in all 10 rounds (20 subtests) on
    `errors != []` (`InvalidRequestError: No such customer`);
  - the other 14 fail as in the first red run.

  Log `b/gate35_on_prefix_f8d28a64.txt`, sha256
  `84a928294d285fe5f4b1c52711d8405a3aca2e97e2ca512c6d479daaeadcce00`. That is the hash as committed; the pre-commit
  trailing-whitespace hook stripped line ends from this log (raw before
  the strip: `3616ff92cd1b34bf96f30fe30582006525936ff3d385a27dbd9d125f23634dde`).
- No guard changed after the mutant battery (`billing/stripe_service.py` is
  byte-identical, blob `0929e020…f453`), so per H3.4 the battery is not
  rerun.

## Full regression (expectations, written before the run, 2026-11-02)

Tip: c0bc0864 (branch `task/h274-qa-server-messages`, base beta f5d2d577).

Count without running: the Django runner's `build_suite([])` has 8587 tests
at f5d2d577 (equal to batch 31 run 2's `Ran 8587`) and **8633** at c0bc0864,
with 0 `_FailedTest`. The +46 are all in H-274 test modules:

| Module | base | tip |
|---|---|---|
| `AutoGrader.tests_uploads` | 4 | 8 |
| `billing.tests_h274_checkout_stale_customer` | 0 | 16 |
| `classrooms.tests_h274_session_empty` | 0 | 7 |
| `users.tests_h274_blank_photo_form_value` | 0 | 5 |
| `users.tests_h274_empty_profile_photo` | 0 | 8 |
| `users.tests_h274_sign_in_message` | 0 | 6 |

`AutoGrader.tests_cache_invalidation_coverage` (20 tests) is in the default
discovery, so the full run includes it.

Command: batch 31's, run from the worktree root with the private `.venv`
(requirements-dev now installed, see `venv.md`):
`manage.py test --noinput --settings=settings_worktree --parallel 4`
(DB `test_h274_qa_server_messages`, apart from SM's `test_AutoGrader_h276`),
plus `mypy` and `makemigrations --check --dry-run`.

Expected: **Ran 8633, OK, skipped=30.** (Superseded below: the Gate 3/5
additions make it 8637; the run on 8b70c8b6 stands as a regression signal
for the fix only.) None of the H-274 tests skips. The
30 skip reasons are batch 31's (`beta-batch-31/run2_skips.txt`), all opt-in.
mypy passes and makemigrations reports no changes.

## Full regression on 8b70c8b6: result (2026-11-02)

`Ran 8633 tests in 707.809s`, **FAILED (failures=2, errors=3, skipped=30)**, rc=1.
The 30 skip reasons are batch 31's.

- 3 ERRORs (UnicodeDecodeError), from the environment: two source-scanning tests
  (`ai_processor.tests_grading_run_pipeline.NoGradeShapingSettingIsReadLiveTest`,
  `students.tests_grading_label_written.TheTwoPlacesThatEmitTheEntryTest` x2)
  glob `BASE_DIR/*/**/*.py`, which reached the in-worktree `.venv` and its
  non-UTF-8 `joblib/test/test_func_inspect_special_encoding.py` (joblib 1.5.2,
  requirements.txt). Batch 29/31 venvs live outside the repo. SM ruling: a
  private venv outside the worktree, and no venv inside it for Gate 10.
- 2 FAILs, from the branch: (e.1) 8168a0d3b put the 0-byte check at the top of
  `validate_upload_size`, so the student answer route answered FILE_EMPTY 400
  where S6 wants 415 FILE_TYPE_UNSUPPORTED (empty .txt) and 422
  SUBMISSION_EMPTY (empty .png). Fixed in (e.1a) below.
- The teardown `database "test_h274_qa_server_messages" does not exist`: a
  module run of mine with the same TEST NAME during the full run created and
  dropped it. Gate 10 uses a unique DB name and no concurrent runs.

## (e.1a) The student answer route keeps S6 (SM ruling A, 2026-11-02)

Ruling: on `students/views.py` upload (line 588) the S6 contract holds
exactly; FILE_EMPTY 400 stays at the other doors. Mechanism:
`validate_upload_size(uploaded_file, max_size_bytes=None, *, allow_empty=False)`;
the student route passes `allow_empty=True`; no route names in the function.
No new wording. upload-async (692) and batch-upload (1464) are asked of SM.

### (e.1a) Expectations (written before the red run, on 3757e0aa)

Labels: `assignments.tests_file_reason_codes.StudentUploadAnswersWithItsOwnCode`
and `assignments.tests_h274_empty_upload_doors`.

Before the fix:
- `test_a_zero_byte_file_is_empty` FAIL (400 != 422).
- `test_an_empty_file_of_an_unsupported_type_is_the_type_first` FAIL (400 != 415).
- `test_a_caller_may_leave_the_empty_check_to_its_own_rules` and
  `test_the_byte_cap_still_applies_when_empty_is_allowed` ERROR (TypeError:
  unexpected keyword `allow_empty`).
- `test_by_default_a_zero_byte_file_is_file_empty` and
  `ATeacherDoorStillAnswersFileEmpty` PASS (e.1 is already there; they are the
  guards against regressing it).
- Every other test in the S6 class PASS.

After the fix: all OK.

Mutants (each killed by the named test):
- M-e1a.1 drop `allow_empty=True` at students/views.py:588: the two S6 tests FAIL.
- M-e1a.2 ignore `allow_empty` in uploads.py (`if size == 0:`): the two S6
  tests and `test_a_caller_may_leave...` FAIL.
- M-e1a.3 default `allow_empty=True`: `test_by_default...` and
  `ATeacherDoorStillAnswersFileEmpty` FAIL.

### (e.1a) Scope widened by SM (2026-11-02): all five e.1 sites

SM put in scope, same mechanism: students/views.py:692 (upload-async) and
:1464 (batch-upload) pass `allow_empty=True`; assignments/views.py:942
(assignment-upload) and :1134 (assignment-upload-async) catch
`(PayloadTooLarge, PayloadEmpty)` per item, so FILE_EMPTY stays but one empty
file no longer refuses or half-queues the request. The 1-file guard test in
85b0239c (`ATeacherDoorStillAnswersFileEmpty`, whole request 400) is rewritten
to the per-item form. SKIP=mypy on 85b0239c is for that red commit only; every
later commit and the tip pass the full pre-commit with no SKIP.

### (e.1a) Student doors 692/1464: expectations

Test: `students.tests_h274_empty_student_answer_doors` (4 tests).

**Order slip, recorded:** before this text was committed I ran the 4 tests
once on the pre-e.1 commit 187ed3cd (`Ran 4, OK`: they describe the old
behaviour) and once on e241c9c0 (FAILED, failures=4: 400 != 200 twice on
upload-async, 400 != 202 twice on batch-upload, every body FILE_EMPTY). The
recorded red run below repeats it after this commit.

Before the fix (on this commit): FAILED (failures=4), the same four
assertions. After the fix: OK.

## Summary table

| Item | Touches RB files? | Scope | Status |
|------|-------------------|-------|--------|
| (a) sign-in text | No | `users/serializers.py` only | Ready to code |
| (b) checkout Stripe errors | No | `billing/stripe_service.py` (4 sites) | Sites 1/3 heal, site 2 heals by email (B2), site 4 wrapped (B1); red as written; M1–M10 (12) KILLED |
| (c) invoice descriptions | No | — | **Owner: won't change** (BUG-013 closed for backfilled rows); no code change; Q4 with owner |
| (d) session 500 | No | — | **Cannot reproduce** (all 400); no code change |
| (e.1) empty PDF | No | `AutoGrader/uploads.py` + new ReasonCode | Ready to code |
| (e.2) empty photo | No | test only: `users/tests_h274_empty_profile_photo.py` | **Refused server-side** (0-byte → 400 at all 5 doors); no code change; `""` clears photo — open for SM |
| (e.2a) empty form photo value | No | `users/serializers.py` | Owner: `""` keeps the photo, JSON `null` removes; red as written, M-e2a.1–3 KILLED; frontend risk with owner |
| (e.2b) empty-photo text | No | `AutoGrader/image_formats.py`, `users/serializers.py`, `classrooms/serializers.py`, `users/admin.py` | FILE_EMPTY text at all 5 doors (code `empty`); red as written, M-e2b.1–7 KILLED |
| (e.3) password reuse | No, **already checked** | reword only: `users/serializers.py` | Ready to code |
| (f.1) already enrolled | No | `classrooms/services/enrollment.py` | Ready to code |
| (f.2) insufficient credits | No | `billing/errors.py` | Proposed text above; ready to code |
| (f.3) wrong password | No | `users/views.py` | Ready to code |
| (g) credit limit | No change | confirmed | Done |
