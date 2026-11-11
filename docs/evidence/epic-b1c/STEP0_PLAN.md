# B1c Step 0 — credit requests: plan re-check against staging a0478508e

Branch `task/epic-b1c` from origin/staging a0478508e (B1a + B1b on staging). Source: plan 08 §3.8, §2 step 7, §4, §5, §6 (C-8, C-9), r4 re-check m3-a. No product code in this step. `CREDIT_REQUESTS_ENABLED` and `BILLING_HOLDS_ENABLED` stay off.

## 1. What still matches

- `initiate_overage_purchase` `billing/license_service.py:2975`, `_create_overage_checkout` `:3277`, `Session.create` `:3326`, session id saved `:3362`, `approve_overage_offline_request` `:3447`, `remove_teacher_from_license` `:1949`, `cancel_license_subscription` `:2294`. Only `checkout.session.completed` is wired (`billing/webhooks.py:103`).
- `LicenseOveragePurchaseStatus` still has only PENDING / COMPLETED / FAILED (`billing/models.py:2354`, was `:1981`); the intent's `stripe_checkout_session_id` is now `:2415` (was `:2042`).
- B1a already shipped the FINANCIAL retention class and `FINANCIAL_ACTIONS` (`audit/enums.py:47`, `:140`); B1c only adds `CREDIT_REQUEST_CREATE` / `CREDIT_REQUEST_DECIDE` to it.
- `AIJob` exists with `QUEUED_PENDING_CREDITS` (`billing/models.py:163`, `:1850`), so the request's `ai_job` FK and "credits land → queue promotes" (B1b) are in place.
- Migrations: billing ends at 0084 on staging; B1c adds 0085 (CreditRequest + the three nullable columns + `SUPERSEDED` choice), additive. If H-80 (beta 0074_h80) is merged into staging first, B1c's migration follows that merge migration.

## 2. Drift found (plan text to correct, no design change)

| # | Plan says | Code today | B1c does |
|---|---|---|---|
| D1 | Approve body `{blocks?, payment_method}` | Stripe route raises unless `success_url` and `cancel_url` are passed (`license_service.py` `initiate_overage_purchase`, the checkout branch) | Approve body also takes `success_url` / `cancel_url`, the same as the existing overage purchase view (`license_views.py:303`) |
| D2 | Duplicate intent → `FAILED`; handler refuses `FAILED` | r4 m3-a replaced this with a new `SUPERSEDED` status; H-300 already saves partial-skip purchases `FAILED` *with grants* (`stripe_service.py` handler, H-300 comment) | Use `SUPERSEDED`; refuse + refund only `SUPERSEDED` with no grants; `FAILED` handling untouched |
| D3 | "Evaluate `checkout.session.expired` for `AUTO_REPLAYABLE`" | The license overage flow is not auto-replayable at all; the allowlist holds only `overage_block_purchase_checkout` (`event_replay.py:82`), with a second `VETTED_HANDLERS` gate | Leave it out of `AUTO_REPLAYABLE`; the sweep on `session_expires_at` is the backstop. No vetting proof needed |
| D4 | GRANTED hook after webhook and offline approval | A super admin purchase grants at once via `_grant_overage_offline` (the `is_super` branch) | Third GRANTED hook there (super admin "grant now") |
| D5 | `initiate_overage_purchase` takes a lock | A cache lock refuses a second purchase on the same license (`billing:license_overage:<id>`) | That error is a normal step-2 failure: request back to PENDING with the message |

## 3. Every path that ends a teacher's license access

Access needs an active `SchoolCreditAllocation` **and** an active license (`billing/subscription_resolver.py:68`, `access_control.py:186`). Writers that end either:

| # | Path | Where | Ends |
|---|---|---|---|
| A1 | Admin removes the teacher | `remove_teacher_from_license` `license_service.py:1984` (from `license_views.py:467`) | that teacher |
| A2 | Admin cancels an OFFLINE-billed license | `cancel_license_subscription` → `record_cancellation` `:2367` | whole license, now |
| A3 | Admin cancels a Stripe license | same, Stripe `cancel_at_period_end`; ends later via A6 | whole license, at period end |
| A4 | Renewal with auto-renew off | `process_license_renewal` `:2086`; Beat `process_license_renewals` `billing/tasks.py:212` | whole license |
| A5 | Renewal failed for every teacher | `process_license_renewal` `:2205` | whole license |
| A6 | Stripe says canceled / unpaid | `handle_subscription_updated` `stripe_service.py:4693`; `handle_subscription_deleted` `:4751` | whole license |
| A7 | A new license replaces the school's old one | `create_license_subscription` `license_service.py:1112` | old license |
| A8 | Teacher account deleted; license row deleted | CASCADE | rows deleted with it; nothing to hook |

Not access-ending today, so no hook (owner question Q4): school archived (`classrooms/views.py:316` sets only `School.is_active`), teacher account deactivated or locked (allocation stays active).

**Hook design.** One helper `close_open_requests(license, teacher=None, reason)` (conditional UPDATE PENDING/APPROVED → CLOSED, audit, in the caller's transaction) called at A1, A2, A4–A7 (A3 is covered by A6). Plus a catch-all in the existing Beat sweep: close any open request whose allocation or license is no longer active, so a future eighth path cannot strand a request. Approve also re-checks access and closes instead of buying.

## 4. Build (unchanged from step 7, slimmed per LEAN)

1. Tests first (red): model + partial unique index, API role matrix, ordered approve, the four revert paths, GRANTED by `credit_request_id` (C-8), duplicate intent SUPERSEDED + refund (C-9), each A-path closes.
2. Product: model + 0085, service, API (`/api/v1/billing/credit-requests/`), hooks, `checkout.session.expired` handler, sweeps, emails, audit. All behind `CREDIT_REQUESTS_ENABLED`.
3. Plants: 3 max (approve ordering, GRANTED match by id, refund only on SUPERSEDED-without-grants).
4. One near run on the final tree (billing + users + guards), one staging merge at the end.

Estimate: about 7 days (8.5 less the gate overhead LEAN removes). Ops step before the flag goes on: subscribe the production Stripe endpoint to `checkout.session.expired`.

## 5. New user-facing wording (each needs owner approval)

Exact drafts are in OWNER_SHEET.md §4. Emails: W1 to the admin (new request), W2 to the teacher (credits added), W3 (declined, with reason), W4 (request closed), W5 to the admin (duplicate payment refunded). API messages: W6 (already have an open request), W7 (not on a license), W8 (already answered), W9 (can only withdraw while waiting). Status names: W10. Notes shown to admins: W11 (payment page expired / not finished), W12 (offline request rejected), W13 (left the license / license ended). The inbox page itself is frontend (separate repo) and not in B1c.
