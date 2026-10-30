# H-274 Survey: QA server-side messages and checks

Survey only — no code changes. Base: f5d2d577.

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

**Changes:**
- `users/serializers.py` ~line 531: replace `raise` with
  `raise EnvelopedAuthenticationFailed(...)`.
- No changes to audit emit calls — `AUTH_LOGIN` with `WRONG_PASSWORD` or
  `INVALID_CREDENTIALS` reason continues to be emitted before the raise.
- Lockout behaviour is unaffected: `user.register_failed_login()` at line 517
  runs before the raise; the locked check at line 488 runs on the next attempt.

**Tests:**
- Existing: `classrooms/test_views.py` and various auth tests hit this path.
  Any test asserting on the old simplejwt text must be updated.
- New: test that a wrong-password POST to `/api/auth/token/` returns 401 with
  the new text and the coded envelope (`reason_code`, `error_class`, etc.).
- New: test that a non-existent-email POST returns the same message (no
  enumeration).
- Confirm: test that the locked-account path still returns its own message
  (unchanged).

**Touches Release Builder files?** No. `users/exceptions.py` is listed as a
Release Builder file, but the class `EnvelopedAuthenticationFailed` is already
defined there and needs no changes — we only call it from a new site.

**UPDATE — `users/exceptions.py` is in the RB exclusion list.** The fix at
`users/serializers.py:531` only imports and calls the existing
`EnvelopedAuthenticationFailed`; no edits to `users/exceptions.py`.

---

## (b) Checkout "No such customer" (BUG-009)

**Code path:** Every `stripe.checkout.Session.create(customer=...)` call where
the Stripe customer id stored locally is stale or was deleted on the Stripe
side.

**Affected call sites (uncaught `StripeError`):**

| # | File:line | Method | Caller |
|---|-----------|--------|--------|
| 1 | `billing/stripe_service.py:646` | `create_individual_checkout_session` | `IndividualPlanChangeService.select_plan` → `billing/views.py:1299` |
| 2 | `billing/stripe_service.py:774` | `create_license_session` | `billing/license_views.py:313` |
| 3 | `billing/stripe_service.py:840` | `create_license_conversion_session` | `billing/license_views.py:933` |
| 4 | `billing/stripe_service.py:1411` | `create_upgrade_checkout_session` | `IndividualPlanChangeService.select_plan` → `billing/views.py:1299` |

All four pass `customer=<locally stored id>` to `stripe.checkout.Session.create`
with **no** `try/except`. If Stripe returns `InvalidRequestError("No such
customer: cus_...")`, the exception propagates to the view.

**How the error surfaces today:**

- Views catch only `ValueError` (or `CodedError`), not `StripeError`.
- `StripeError` falls to `custom_exception_handler` → unhandled 500 branch
  (`users/exceptions.py:107–113`).
- The renderer (`users/renderers.py:167–176`) calls
  `describe_user_error(exc)`, which does not recognise `StripeError` as
  user-facing → returns `"An unexpected error occurred. Please try again."`.
- **In production** (`DEBUG=False`): the teacher sees a generic 500 with that
  text. No raw Stripe text in the body.
- **On staging/beta** (`DEBUG=True`): the renderer includes `traceback` in the
  response body, which contains the full `InvalidRequestError` message
  including the Stripe customer id. **This is BUG-009.**

**The overage path IS already safe:**
`billing/stripe_service.py:2344–2350` catches `StripeError`, wraps it in
`BillingRequestError` using `describe_stripe_error()` which suppresses
non-CardError messages. Same for `billing/license_service.py:3343`.

**Proposed fix:**
Wrap each of the four `Session.create` calls in `try/except StripeError`,
raising `BillingRequestError` with a user-friendly message, following the
overage precedent. Proposed text:
`"We couldn't start checkout for your account. Please contact support."`

For paths where no charge has been attempted yet (all four — checkout sessions
collect payment, they don't charge immediately), add:
`"You haven't been charged."` — this is true by design (Stripe Checkout is
redirect-then-charge).

Combined message:
`"We couldn't start checkout for your account. You haven't been charged. Please contact support."`

Because `BillingRequestError` is a `ValueError` subclass, the existing
`except ValueError` in each view will catch it and call `describe_user_error`,
which recognises `BillingRequestError` as user-facing and passes its text
through. No view changes needed.

**Tests:**
- Mock `stripe.checkout.Session.create` to raise
  `stripe.error.InvalidRequestError("No such customer: cus_test123", param="customer")`
  for each of the four call sites; assert 400 (not 500), assert response body
  contains the user-friendly text and not "cus_".
- Confirm the overage path still works (already tested).

**Touches Release Builder files?** No.

---

## (c) Invoice descriptions — backfill command (BUG-013)

**Code path:** `billing/management/commands/backfill_billing_transactions.py`

**Current text:** Three description patterns:
1. Line ~(inside `_backfill_checkout_session`):
   `f"Backfilled from checkout.session.completed ({flow})"` — e.g.
   `"Backfilled from checkout.session.completed (overage_block_purchase_checkout)"`.
2. Line ~(inside `_backfill_invoice`):
   `f"Backfilled from invoice.payment_{'succeeded' if succeeded else 'failed'} ({billing_reason})"`.
3. For offline records: `record.notes or record.get_record_type_display()`.

These are stored in `BillingTransaction.description` and are visible to
teachers in their billing history.

**Approach recommendation: display mapping at read time (not a data fix).**

Reasons:
- The backfill command is idempotent and read-only from Stripe. Changing
  existing rows risks breaking that idempotency (re-running would overwrite
  the fixed descriptions with the old patterns).
- A display mapping is reversible and doesn't require a data migration.
- The mapping can also handle future rows from the backfill if it's ever
  re-run.

**Proposed display mapping (in the serializer or a helper):**

| `flow` / source | Proposed display text |
|---|---|
| `individual_checkout` / `individual_subscribe` | `"Subscription – {plan_name}"` |
| `trial_to_paid` | `"Subscription – {plan_name}"` |
| `individual_upgrade_checkout` | `"Upgrade from {old_plan} to {new_plan}"` (needs plan names from context) |
| `overage_block_purchase_checkout` | `"Credit top-up – {quantity} AI credits"` |
| `license_create` | `"Subscription – {plan_name}"` |
| `invoice.payment_succeeded (subscription_cycle)` | `"Subscription renewal – {plan_name}"` |
| `invoice.payment_succeeded (subscription_update)` | `"Plan change – {plan_name}"` |
| `invoice.payment_failed (*)` | `"Payment failed – {plan_name}"` |
| Offline records | keep `record.get_record_type_display()` (already readable) |

The mapping lives in a small function, keyed on the existing description
pattern (regex or startswith). The serializer calls it at read time. The raw
description stays in the database for debugging.

**Row count query (for beta/staging — NOT to run against live):**

```sql
SELECT description, COUNT(*) AS n
FROM billing_billingtransaction
WHERE description LIKE 'Backfilled from %'
GROUP BY description
ORDER BY n DESC;
```

**Tests:**
- Unit test the display mapping function with each pattern.
- Test that the serializer output uses the mapped text.

**Touches Release Builder files?** No.

---

## (d) Creating a session gives 500 when session name is empty

**Code path:** `classrooms/views.py:1976` `SessionViewSet` (ModelViewSet),
serializer: `classrooms/serializers.py:44` `SessionSerializer`.

**Current validation:**

The `Session` model (`classrooms/models.py:29`) has
`name = CharField(max_length=100)`. Django's default is `blank=False`.
DRF's `ModelSerializer` should generate a `CharField` with `allow_blank=False`,
which rejects `""` with a 400.

**However:**
- `name` is NOT in `read_only_fields`, so it IS writable.
- There is NO explicit `validate_name` on `SessionSerializer` (unlike
  `TopicSerializer` at line 112 which has one).
- Possible 500 causes to investigate:
  1. Sending `name: null` — `CharField` default does not set `allow_null=True`,
     so DRF should reject this with 400. But worth testing.
  2. Sending no `name` field at all — `CharField` is `required=True` by
     default. Should be 400.
  3. Sending only whitespace — DRF `CharField` has `trim_whitespace=True` by
     default, so `"   "` becomes `""`, rejected by `allow_blank=False`.
  4. The 500 might be in `perform_create` (line 2048): the `UniqueConstraint`
     on `(name, teacher)` could raise `IntegrityError` if, e.g., two sessions
     with `name=""` are created for the same teacher — but DRF should refuse
     the blank before it reaches the DB.
  5. The 500 might involve the session's UUID `id` field (sent by the client
     as empty string?) colliding with DRF's auto UUID. The `id` is
     `read_only`, so the client's value is ignored.

**Needs reproduction.** I will write a test that POSTs to the session-list
endpoint with `name: ""`, `name: null`, `name: "   "`, and missing `name`,
and assert each returns 400, not 500. If any returns 500, the traceback shows
the cause.

**Proposed fix (depending on root cause):**
- If it's a missing `validate_name`: add one, like TopicSerializer's, that
  strips and rejects blank/whitespace-only.
- If it's an IntegrityError from the UniqueConstraint: add a serializer-level
  unique check, or catch IntegrityError in the view and return a coded 409.

**Tests:**
- Test `POST /api/classrooms/sessions/` with empty/null/whitespace name → 400.
- Test valid creation still works → 201.

**Touches Release Builder files?** No.

---

## (e) Server-side checks: empty uploads and password reuse

### (e.1) Empty PDF upload accepted (BUG-005)

**Code path:** `students/views.py:580–606` (sync upload) and
`students/views.py:685–717` (async upload).

**Current validation:**
1. `isinstance(uploaded_file, UploadedFile)` — rejects non-file payloads.
2. `validate_upload_size(uploaded_file)` (`AutoGrader/uploads.py:56`) — only
   checks `size > max_size_bytes`. **A 0-byte file passes.**
3. No content-type validation at the view level. The check message says
   "Only images (JPEG, PNG, GIF, WebP) and PDFs are allowed" in the
   `isinstance` guard, but this is a type check on the Python object, not on
   the file's content type.

**Does the server already check it on beta?** No. A 0-byte PDF is accepted
and passed to the AI extraction, which will fail downstream.

**Proposed fix:**
Add a minimum-size check in `validate_upload_size`:
```python
if uploaded_file.size == 0:
    raise PayloadTooLarge(  # or a new CodedError
        params={"file_name": file_name_of(uploaded_file), ...},
        ...
    )
```

Better: use a new `ReasonCode`. Looking at existing codes...
`ReasonCode.FILE_TOO_LARGE` exists for the max check. For the empty case,
a new `ReasonCode.FILE_EMPTY` would be cleaner, or reuse `FILE_TOO_LARGE`
with `actual=0`. I recommend reusing `FILE_TOO_LARGE` — the message template
already includes `{actual}` and `{limit}`, and "0 bytes" is self-explanatory.
The `dimension` param would say "bytes" and the display would say
"The file is 0 bytes. The minimum is 1 byte." — or better, a dedicated
message via `alternative_messages` on the spec.

Actually, the simplest correct fix: add to `validate_upload_size`:
```python
if uploaded_file.size == 0:
    raise CodedError(ReasonCode.FILE_EMPTY, params={"file_name": ...})
```
with a new `FILE_EMPTY` ReasonCode. Message: `"The uploaded file is empty. Please select a valid file and try again."`

**Tests:**
- Upload a 0-byte file → assert coded 400 (or 413) with `FILE_EMPTY`.
- Upload a valid file → still works.

### (e.2) Empty profile photo accepted (BUG-028)

**Code path:** `classrooms/serializers.py:655` — `profile_image` field uses
`validators=[validate_image_format]` (`AutoGrader/image_formats.py:21`).

**Current validation:**
`validate_image_format` calls `Image.open(BytesIO(value.read()))` then
`img.verify()`. A 0-byte file would make `BytesIO(b"")`, and
`Image.open(BytesIO(b""))` raises `UnidentifiedImageError` which is caught
by the `except Exception` at line 27 and turned into
`ValidationError("Upload a JPEG, PNG, GIF or WEBP image.")`.

**Does the server already check it on beta?** **Yes** — Pillow rejects a
0-byte image. The `validate_image_format` validator catches it and returns a
400 validation error.

However, the error message could be improved. For an empty file, "Upload a
JPEG, PNG, GIF or WEBP image" doesn't tell the user the file was empty.
But BUG-028 says "empty profile photo accepted" — this contradicts my analysis.
I should reproduce to confirm.

**Test:** Upload a 0-byte image file as profile_image → does it pass or fail?
If it fails (as I expect), BUG-028 is a non-issue on the server side (may be
frontend-only).

### (e.3) New password equal to current not refused (BUG-029)

**Code path:** `users/serializers.py:607` `ChangePasswordSerializer.validate()`

**Current check:** Line 626:
```python
if data["current_password"] == data["new_password"]:
    raise serializers.ValidationError(
        "New password cannot be the same as the old one"
    )
```

**Does the server already check it on beta?** **Yes.** The serializer
validates this before the view even sees the data.

**Current text:** `"New password cannot be the same as the old one"`

**QA writing rules say:** no blame, name the thing, say what to do.
Proposed reword: `"Your new password must be different from your current password. Please choose a different one."`
This is a DRF `ValidationError` — it surfaces as a 400 with the text in
the response body's field errors. Not a coded error (no ReasonCode).

**Should it use a ReasonCode?** The existing check is a serializer validation
error, not a business-logic refusal. Keeping it as a `ValidationError` is
consistent with how other field-level checks work. I'd leave it as-is but
reword the text.

**Touches Release Builder files?** No.

---

## (f) Rewording of server texts

### (f.1) "Student already enrolled" (direct add)

**Code path:** `classrooms/services/enrollment.py:435`
```python
raise EnrollmentError("Student is already enrolled in this course.")
```

**Current text:** `"Student is already enrolled in this course."`

**Proposed text:** `"A student with this email is already enrolled in this course."`

**Lives in reason_codes.py?** No. `EnrollmentError` is a plain exception
raised in the enrollment service. However, there IS a `ROW_ALREADY_ENROLLED`
ReasonCode in `audit/enums.py:179`, used by the bulk/roster import path
(`classrooms/tests_s7d_roster_codes.py`). The direct-add path at line 435
does NOT use it — it raises a plain `EnrollmentError` that DRF answers as a
400 via the custom exception handler's `_passthrough_message` (since
`EnrollmentError` presumably inherits from a user-facing type... let me check).

Actually, `EnrollmentError` is defined in `classrooms/services/enrollment.py:37`.
It's caught by the enrollment view. Let me check if it's in the user-facing
types list.

Looking at `AutoGrader/error_messages.py:42–70`: `EnrollmentError` is NOT in
the list. But the direct-add endpoint catches it locally:

The test at `classrooms/tests_h214_direct_add_answers.py:93` asserts:
`str(response.data[0]), "Student is already enrolled in this course."` — so
it's surfaced as a DRF ValidationError (list format).

**Where is it caught?** In the enrollment service or the view that calls it.
Let me note: the direct-add path is in `classrooms/views.py` — the view
catches `EnrollmentError` and wraps it.

**Change:** Edit the string at `classrooms/services/enrollment.py:435`.
Update any test assertions that check this exact text.

**Touches Release Builder files?** No.

### (f.2) "Not enough AI credits to grade N submissions"

**Code path:** `billing/errors.py:37`:
```python
INSUFFICIENT_CREDITS_MESSAGE = (
    "There aren't enough AI credits available for this. The credit wallet "
    "needs to be topped up before it can run."
)
```
Also: `AutoGrader/reason_codes.py:258–265` — `ReasonCode.INSUFFICIENT_CREDITS`
uses this same message.

And: `AutoGrader/reason_codes.py:236–256` —
`ReasonCode.INSUFFICIENT_CREDITS_MID_BATCH`:
`"Credits ran out after {completed} of {total} items. The finished items are saved."`

**Current text analysis:**
- The `INSUFFICIENT_CREDITS_MESSAGE` does NOT mention how many credits are
  needed or how many are available. The QA catalogue says it "must explain
  the shortfall."
- The mid-batch variant at least says "{completed} of {total}".

**Proposed reword:**
`INSUFFICIENT_CREDITS_MESSAGE`: The fixed text cannot include dynamic values
because `describe_user_error` returns it as a constant for ALL
`InsufficientCreditsError`s. To show the shortfall, we'd need to either:
1. Convert the `InsufficientCreditsError` callers to use
   `CodedError(ReasonCode.INSUFFICIENT_CREDITS, params={...})` with a
   parameterized message template, or
2. Change `INSUFFICIENT_CREDITS_MESSAGE` to be generic but more helpful.

Option 2 is simpler and safer for this batch. Proposed text:
`"There aren't enough AI credits to do this. Top up credits, or ask your school administrator, then try again."`

This follows QA writing rules: says what happened ("not enough credits"),
then what to do ("top up or ask admin"). It doesn't name the shortfall
because the plumbing doesn't carry it to this layer today.

**Lives in reason_codes.py?** Yes — `ReasonCode.INSUFFICIENT_CREDITS` at
line 258.

**Touches Release Builder files?** No.

### (f.3) "Wrong current password"

**Code path:** `users/views.py:1634`
```python
raise ParseError("Incorrect current password. Please try again.")
```

**Current text:** `"Incorrect current password. Please try again."`

**Proposed text:** `"Your current password is incorrect. Please check it and try again."`

This is a DRF `ParseError` (400). Not a coded error. The audit trail records
it via `sign_in_failed` at line 1633 with reason `"WRONG_PASSWORD"`.

**Lives in reason_codes.py?** `ReasonCode.WRONG_PASSWORD` exists in
`audit/enums.py:220` but is used only for the audit trail, not for the
response text.

**Touches Release Builder files?** No.

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

**Confirmed:** these are the owner's approved texts from H-268. **No change
required.** The code at `billing/stripe_service.py:2303–2309` raises
`CodedError(ReasonCode.OVERAGE_CAP_EXCEEDED, params={...})`, and
`variant_from_params=_overage_cap_variant` picks the right variant.

---

## Summary table

| Item | Touches RB files? | Scope |
|------|-------------------|-------|
| (a) sign-in text | No | `users/serializers.py` only |
| (b) checkout Stripe errors | No | `billing/stripe_service.py` (4 sites) |
| (c) invoice descriptions | No | new helper + serializer change in `billing/` |
| (d) session 500 | No | `classrooms/serializers.py` or `classrooms/views.py` |
| (e.1) empty PDF | No | `AutoGrader/uploads.py` + new ReasonCode |
| (e.2) empty photo | Likely already refused — needs reproduction |  |
| (e.3) password reuse | No, **already checked** | reword only: `users/serializers.py` |
| (f.1) already enrolled | No | `classrooms/services/enrollment.py` |
| (f.2) insufficient credits | No | `billing/errors.py` + `AutoGrader/reason_codes.py` |
| (f.3) wrong password | No | `users/views.py` |
| (g) credit limit | No change | confirmed |
