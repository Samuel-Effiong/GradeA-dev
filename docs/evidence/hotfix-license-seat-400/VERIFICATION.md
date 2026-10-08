# Verification: hotfix-license-seat-400 @ 324164f

**Verifier:** Verification Engineer (1a, session grade-automator-plus-09). **Author:** Security (ed).
**Base:** beta e7e4bdf. **Date:** 2026-09-29.

**Verdict: VERIFIED-WITH-NOTES.** The code is correct. One item, N1, is required before the push: it adds tests only.

## What I checked
The checks were run in my own detached checkouts: the hotfix commit twice (one untouched, one for mutation) and beta once. Each used its own test database.

| Check | Result |
|---|---|
| Reproduce on beta, independently | OFFLINE: 3 teachers on 2 seats → **500** "An unexpected error occurred", no licence created. STRIPE: 3 on 2 → **200**, and `Session.create` is called once, so the school could pay for a licence the webhook then refuses. Both confirmed on e7e4bdf. |
| Only the typed error becomes a 400 | `LicenseSubscriptionSerializer.create` catches only `LicenseRequestError`. Ed's test pins a bare `ValueError` as a 500 with no text leak. Widening the catch to `Exception` (my V7) is caught by it. |
| Other callers | `LicenseRequestError` subclasses `ValueError`. The webhook's `except ValueError` admin fallback (`stripe_service.py:3537`) and every other `except ValueError` behave exactly as before. No code checks the exact type. |
| Atomicity, with real commits | TransactionTestCase, via the real POST: refused OFFLINE and refused carry-forward requests leave **every** billing, users and classrooms table count unchanged. The only exception is `users.UserActivity`, the per-request heartbeat middleware, which writes regardless of outcome. No `on_commit` callbacks are queued and no email is sent. The seat check runs before any write, because `existing_license` is a plain read. |
| Message rendering | The envelope `message` is one plain sentence with no field label. Plural, singular and carry-forward variants all render correctly. Also checked: `max_seats` omitted → 400 "max_seats must be a positive integer" (was 500), zero-credit plan → 400, STANDARD tier → 400. |
| Stripe pre-check | Carry-forward on the Stripe path, 1 carried + 2 new on 2 seats → 400 with the variant message, `Session.create` not called. With `carry_forward_teachers=false` → 200. |
| Whole billing app @324164f (committed tree) | `EXEMPT_EMAIL_DOMAINS=`, **Ran 1654, OK**, 539 s. |
| Commit scope and hygiene | 3 source files, 2 test files and evidence. `test_zz_prefix_tmp.py` is not in the tree. `black --check` passes. |
| Ed's mutants | 8/8 killed (from the author's log; not re-run). |

## My mutants (7), against ed's committed tests / my probes
| Mutant | Ed's tests | My probes |
|---|---|---|
| V1 Stripe pre-check ignores carry-forward (`existing_license=None`) | **SURVIVED** | killed |
| V2 zero-credit plan refusal raised as a bare `ValueError` | **SURVIVED** | killed |
| V3 STANDARD-tier refusal raised as a bare `ValueError` | **SURVIVED** | killed |
| V4 `max_seats <= 0` guard raised as a bare `ValueError` | SURVIVED | SURVIVED (my probe printed, did not assert) |
| V5 serializer raises `ValidationError(str(exc))` instead of the `non_field_errors` dict | SURVIVED | SURVIVED. Equivalent for the top-level `message`; only `error.field_errors` changes shape. |
| V6 `max_seats > 0` ("0 = unlimited") dropped from the check | SURVIVED | SURVIVED. Reachable only on STRIPE with `max_seats` omitted. |
| V7 serializer catches `Exception` | killed | n/a |

All restores were sha256-checked against 324164f.

## Notes
**N1 (REQUIRED before push: tests only).** V1–V3 survive the committed tests.
- V1 is a headline claim of this hotfix: "a school can no longer pay for a licence the webhook would refuse". If the Stripe pre-check stopped counting carried-over teachers, a school could again pay for a licence that is then refused, and nothing would fail.
- V2, V3 and V4 are the "could also 500" conversions the EVIDENCE table claims.

Please add, through the real POST:
1. STRIPE carry-forward over the cap → 400 with the variant message and `Session.create` not called. Also `carry_forward_teachers=false` → 200.
2. Zero-credit plan → 400 with its message.
3. STANDARD tier → 400.
4. `max_seats` omitted → 400.

My probe versions are in `billing/tests/test_vf_seat400_probe.py` in my scratch checkout, and I'll send them on request. I'll re-verify the test-only delta by re-running V1–V4.

**N2 (pre-existing; not introduced, not widened).** The view's STRIPE branch still maps **every** `ValueError` to a 400 with its text (`license_views.py:291`). A bare `ValueError("a real bug")` from inside `create_license_session` returns 400 `"a real bug"`, not a 500. The hotfix adds only a typed raise under that catch, so nothing gets worse. Narrowing it to `LicenseRequestError` needs the contact-sales raise typed first; the missing `stripe_price_id` raise is a configuration bug and should be a 500. Backlog.

**N3 (pre-existing; untouched endpoint).** `update_seats` wraps Stripe failures as `ValueError(f"Stripe error while updating seats: {exc}")` (`license_service.py:2341`). Its view maps `ValueError` to a 400, so a Stripe outage reaches the caller as a 400 carrying Stripe's text. Backlog: type the user errors there and let Stripe failures be a 500 or 502.

**N4 (pre-existing, moved verbatim).** Emails are lower-cased but not de-duplicated, so the same teacher listed twice ("dup@", "DUP@") on 1 seat is refused as "2 teachers were added".

**N5 (pre-existing).** The model default for `max_seats` is 1, but the serializer and view fall back to 0 when it is omitted. On STRIPE that means `quantity: 0` and an unlimited seat check, and the webhook's `max_seats must be positive` guard would then refuse **after payment**. Backlog: make `max_seats` required on create, or fall back to the model default.

**N6 (process).** This hotfix ships alone to beta, so "full suite: covered by the batch-2 run" does not apply. It needs its own strict full run on the landing tip before the push. The EVIDENCE says this is "covered by the hotfix's own landing run on beta"; I'm flagging it so it isn't skipped.

## Author's N1 response (ed)
Added to `billing/tests/test_license_seat_400.py`, all through the real `POST /license-subscriptions`:
- `test_stripe_counts_carried_over_teachers_before_checkout`: 1 carried + 2 new on 2 seats → 400 with the variant message; `Session.create` not called (V1).
- `test_stripe_checkout_goes_ahead_when_nobody_is_carried_over`: the same request with `carry_forward_teachers=false` → 200, `Session.create` called once (V1 control).
- `test_a_plan_without_monthly_credits_is_a_400` (V2) and `test_a_standard_tier_plan_is_a_400` (V3): 400 with the service's message, nothing created.
- `test_omitted_max_seats_is_a_400` (V4): 400 "max_seats must be a positive integer", nothing created.
N4 and N5 are backlog H-58 and H-59 (SM, 2026-09-29), batch-3, owner ed; the hotfix is not widened for them. N6: the strict full run on the landing tip before the push is 0b's.

## Re-verification: N1 (665b026) and the widening (2e25139). Verification Engineer, 2026-09-29

**Combined verdict for the tip 2e25139: VERIFIED-WITH-NOTES.** Nothing is required before the push other than N6, the strict full run on the landing tip.

### N1 @665b026: closed
- The delta is tests-only: under `billing/` it touches only `billing/tests/test_license_seat_400.py`. It is a descendant of 324164f.
- This file's first section is committed verbatim; I diffed it against my copy.
- My V1, V2, V3 and V4 are each killed by exactly the test added for it. V7 is still killed. All restores were sha-checked.
- Baseline: the seat tests plus `test_license_service`, **46 OK**.

### Widening @2e25139
| Check | Result |
|---|---|
| Scope | `AutoGrader/error_messages.py` (allowlist +1), `billing/license_service.py` (`add_teachers_batch` and `remove_teacher_from_license` refusals typed, plus `_no_seats_message`), a new test file, and evidence. No view code changes. A descendant of 665b026. |
| Seat message correctness | Built from the **locked** row (`select_for_update`) after skipping teachers who are already active. `seats_remaining` is floored at 0, so it never says "-1 seats"; a licence already over its cap reads "no seats left (3 of 2 in use)". The plural path only runs with 2 or more teachers, because `adding > remaining >= 1`. |
| Real-commit atomicity (TransactionTestCase, the school admin through the real `add_teachers` route) | A full licence with 2 new teachers → 400 with the exact message. Every billing, users and classrooms table count is unchanged, apart from the `UserActivity` heartbeat. **0** `on_commit` callbacks, **0** emails. |
| Global allowlist reach | I enumerated every non-test caller of `describe_user_error` and `is_user_facing_error`, and of each `LicenseRequestError` raiser. My result matches ed's reach table. One addition: the **renderer's unhandled-500 branch** (`users/renderers.py:152`) also uses `describe_user_error`, so a `LicenseRequestError` escaping any view uncaught would show its text in a 500. That is **latent**: every web path to a raiser catches it (create serializer, `validate()`, the Stripe branch, `add_teachers`, `remove_teachers`), and the webhook answers with a bare `HttpResponse`, never the renderer. See N7. |
| Admin texts with emails and school names | They still reach only the superadmin create and checkout paths, which already showed them. A school admin can't reach `validate_admin_user`. |
| N3 (`update_seats`, Stripe text) | Unchanged: its Stripe raise stays a bare `ValueError`, which is not on the allowlist. |
| A bug in `add_teachers` | Still a 400 with the generic fallback; its text is not shown (pinned). |
| Whole billing app + `AutoGrader.tests_error_messages`, `users.tests_renderers`, `users.tests_exception_handler`, `students.tests_task_tracking` @2e25139 | **Ran 1725, OK** (`EXEMPT_EMAIL_DOMAINS=`, 441 s, committed tree) |

**My mutants on the widening:**
- **X1: allowlist widened to `ValueError`.** Killed by 3 of ed's tests: `test_a_bare_value_error_is_still_a_500` (through the renderer's 500 branch), `test_any_other_error_keeps_the_generic_message`, and `test_unknown_exception_falls_back_to_default_when_no_fallback_given`.
- **X2: the add-teachers seat refusal removed.** Killed by `test_a_full_licence_says_so` and `test_fewer_seats_than_teachers_says_how_many`, and by my real-commit probe.
- Restores were sha-checked against 2e25139.

### New notes (not blocking)
**N7 (latent).** Listing `LicenseRequestError` as user-facing also affects the renderer's unhandled-500 message. Today no web route lets one escape uncaught. If a future view does, the status stays 500, but the body shows the typed message instead of "An unexpected error occurred". That is acceptable, because the typed messages are written to be shown. Please add one row for it to the reach table.

**N8 (informational).** `remove_teachers` now tells a school admin two cases apart. An **existing** user who isn't on their licence gets "This teacher isn't an active teacher on this licence." An id that doesn't exist gets the generic fallback, because it's a 404 inside the loop. That is a user-existence oracle, but user ids are random UUIDs (`users/models.py` `UUIDField(default=uuid4)`), so it can't be enumerated. No action; recorded for the anti-enumeration register.
