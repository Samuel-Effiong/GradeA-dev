# Hotfix: more teachers than seats gives a 400, not a 500

Branch `task/hotfix-license-seat-400` off beta `e7e4bdf`. Founder-requested and urgent (SM, 2026-09-29). It ships **alone** as a hotfix to beta; the founder approves the push, and main is on hold per the founder.

## The bug (reproduced on beta e7e4bdf, `prefix_beta_e7e4bdf_failing.txt`)
Superadmin "School billing", `POST /api/v1/license-subscriptions` (OFFLINE), with 3 `teacher_emails` and `max_seats=2`, answered **500** "An unexpected error occurred".
- `LicenseSubscriptionService.create_license_subscription` refused the request with a **bare `ValueError`** ("Cannot enroll 3 teachers: license max_seats is 2.").
- Nothing between it and the view caught it: `LicenseSubscriptionSerializer.create` and the view's OFFLINE branch inside `transaction.atomic()` both let it through.

**Found while fixing it: the STRIPE branch was worse.** `StripeCheckoutService.create_license_session` never checked seats against teachers, so the checkout went ahead (**200** with a `checkout_url`). The same refusal would then have happened in the **webhook, after the school had paid**, and no licence would have been created.

Reproduce-first on beta's code (a scratch copy of the new tests; the typed error stubbed as `ValueError`):
- The OFFLINE over-cap test and the carry-forward over-cap test each got 500 where 400 was expected.
- The Stripe over-cap test got 200 where 400 was expected.
- The 2-teachers-on-2-seats control and the bare-ValueError guard passed on beta, as they should.

## Every ValueError reachable from `create_license_subscription`
| Raise | Where | Kind | Now |
|---|---|---|---|
| seat cap, plain and carry-forward variants | the old inline check, now `check_seat_capacity` | user input | `LicenseRequestError` with the new message |
| `max_seats must be a positive integer` | `create_license_subscription` | user input; the serializer also validates it | `LicenseRequestError` |
| plan category, missing `monthly_credits`, STANDARD tier | `validate_license_plan` | user input (plan choice) | `LicenseRequestError`. The serializer checked only the category, so the other two could also 500 |
| admin is a student / superadmin / has no school / is from another school; the school has no admin | `validate_admin_user`, `resolve_admin_user` | user input; `validate()` already turns these into a 400 on `admin_user` | `LicenseRequestError` (same messages) |
| email required, not a business email, not a teacher account | `_get_or_invite_teacher` | user input | **Unchanged.** They don't reach the view: `_invite_and_enroll_one_teacher` never raises and reports them per teacher in `teacher_invitations.errors` |

- `LicenseRequestError` **subclasses `ValueError`**, so every existing `except ValueError` caller keeps working. That includes the Stripe view branch, the webhook's admin fallback and the tests.
- Only this type becomes a 400. A bare `ValueError` stays a loud 500, which a test pins.

## Fix
- `billing/license_service.py`:
  - `LicenseRequestError(ValueError)`.
  - `LicenseSubscriptionService.check_seat_capacity(...)`: the carry-forward/new split and the seat check, moved verbatim out of `create_license_subscription` into a shared helper, with the new message.
  - The user-input raises above now use the typed error.
- `billing/serializers.py`: `LicenseSubscriptionSerializer.create` catches **only** `LicenseRequestError` and raises `ValidationError({"non_field_errors": [msg]})`. The renderer shows that as one clean sentence, with no field label and no numbered list. It is raised inside the view's `transaction.atomic()`, so nothing is written.
- `billing/stripe_service.py`: `create_license_session` runs `check_seat_capacity` **before** creating the Stripe checkout. The view already maps `ValueError` to 400 `{"error": msg}`, so the school now gets the same message before paying.

**Message** (singular and plural handled):
- "This licence has 2 seats, but 3 teachers were added. Remove a teacher or increase Max seats."
- Carry-forward: "This licence has 2 seats, but 3 teachers were added (1 carried over from the current licence + 2 new). Remove a teacher or increase Max seats."

## Tests
`billing/tests/test_license_seat_400.py`, 12 tests (7, plus 5 for N1: see VERIFICATION.md "Author's N1 response"), all through the real `POST /license-subscriptions` except the last two:
- OFFLINE, 3 teachers on 2 seats: **400**, the exact message as the envelope's top-level `message`, and **nothing created** (no licence, no billing record, no teacher accounts).
- Carry-forward, with 1 carried over and 2 new on 2 seats: 400 with the carry-forward message. The **old licence stays active**, only one licence exists, and its allocations are byte-for-byte unchanged (atomic).
- Positive control: 2 teachers on 2 seats → **201**, with 2 teacher allocations.
- STRIPE, 3 on 2: 400 with the message, and **`stripe.checkout.Session.create` is never called**.
- A bare `ValueError` from the service is still a **500**, and its text is not leaked.
- Service level: the singular wording ("1 seat", "2 teachers were added"), and `LicenseRequestError` is a `ValueError`.

Also: `billing/tests/test_license_service.py`'s existing carry-forward over-cap test now asserts the new typed error and message; it previously matched "max_seats is 2".

## Also checked (reported, not changed)
- **`add_teachers`** (`license_views.py`): catches every exception and returns 400 with `describe_user_error`. No 500 is possible.
- **`update_seats`** (lowering `max_seats` below the seats in use): the view already maps `ValueError` to 400 with the service's message. No 500.
- **`PATCH /license-subscriptions/<id>` with `max_seats`**: `max_seats` is a writable field, but `update()` saves only `auto_renew` and `custom_price_cents`. So the PATCH answers 200 and **silently ignores `max_seats`**. It isn't a 500; the endpoint for that is `update_seats`. A backlog candidate: reject `max_seats` on PATCH, or document it.
- **The Stripe webhook** still calls `create_license_subscription`, which can refuse if carry-forward membership changed between checkout and payment. That is rare, and pre-existing: the webhook's own error handling and alerting apply. Not in scope.

## Gates
| Gate | Result |
|---|---|
| 1 Reproduce-first | 3 fail on beta (500, 500, 200), and the 2 guards pass. See `prefix_beta_e7e4bdf_failing.txt` |
| 1 Regression | whole `billing` app (the serializer-change rule), `EXEMPT_EMAIL_DOMAINS=`: **1654 OK**. See `regression_billing_app.txt` |
| 2 Mutation | **12 mutants, 12 killed** (after N1; the Verification Engineer's V1–V4 were added and are killed by the N1 tests: Stripe pre-check ignoring carry-forward, zero-credit plan untyped, STANDARD tier untyped, max_seats guard untyped). Originally 8 of 8, every anchor asserted unique (`mutate.py`, `mutation_log.txt`, `mutation_results.json`). H1 catch removed (500); H2 catch widened to bare `ValueError` (the 500 guard); H3 seat refusal raised untyped; H4 Stripe pre-flight removed; H5 cap off by one (kills the 2-on-2 control); H6 carried-over teachers not counted; H7 message loses the remedy; H8 singular wording broken |
| mypy | whole-repo `pre-commit run mypy --all-files` → Passed |
| 4 Adversarial | the bare `ValueError` stays a 500 and its text never reaches the client; the Stripe checkout cannot be paid for an impossible licence |
| 5 Failure/atomicity | the refused request leaves the old licence and its allocations unchanged, and creates nothing |
| Full suite | not run per fix (0b's rule); covered by the hotfix's own landing run on beta |
