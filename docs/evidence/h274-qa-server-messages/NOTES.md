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

## Summary table

| Item | Touches RB files? | Scope | Status |
|------|-------------------|-------|--------|
| (a) sign-in text | No | `users/serializers.py` only | Ready to code |
| (b) checkout Stripe errors | No | `billing/stripe_service.py` (4 sites) | Corrected; ready to code |
| (c) invoice descriptions | No | — | **Owner: won't change** (BUG-013 closed for backfilled rows); no code change; Q4 with owner |
| (d) session 500 | No | — | **Cannot reproduce** (all 400); no code change |
| (e.1) empty PDF | No | `AutoGrader/uploads.py` + new ReasonCode | Ready to code |
| (e.2) empty photo | No | test only: `users/tests_h274_empty_profile_photo.py` | **Refused server-side** (0-byte → 400 at all 5 doors); no code change; `""` clears photo — open for SM |
| (e.2a) empty form photo value | No | `users/serializers.py` | Owner: `""` keeps the photo, JSON `null` removes; red as written, M-e2a.1–3 KILLED; frontend risk with owner |
| (e.3) password reuse | No, **already checked** | reword only: `users/serializers.py` | Ready to code |
| (f.1) already enrolled | No | `classrooms/services/enrollment.py` | Ready to code |
| (f.2) insufficient credits | No | `billing/errors.py` | Proposed text above; ready to code |
| (f.3) wrong password | No | `users/views.py` | Ready to code |
| (g) credit limit | No change | confirmed | Done |
