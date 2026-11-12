# B1c: credit requests (build)

**Scope and approvals.** Plan 08 §3.8, with STEP0_PLAN.md and OWNER_SHEET.md beside this file. Owner go was relayed by the SM on 2026-11-11:
- Q1-Q5 answered as suggested.
- W1-W13 approved word for word.
- The audit labels "Credit request made" and "Credit request answered" approved.

**Branch.** `task/epic-b1c`, rebased onto origin/staging ef1080075, which includes H-80's 0085_merge_h80 and the door-close fix.

**Run conditions.** Venv `~/Documents/Virtualenvs/epic_b1c_env`. Runs used flock and `--noinput`, with BILLING_HOLDS_ENABLED, CREDIT_REQUESTS_ENABLED, RUN_LOAD_TESTS, CI_REQUIRE_REDIS and CI_REQUIRE_NETWORK unset. No log files are committed (owner rule).

## What was built

- **Model and migration.** `CreditRequest` and billing `0086_b1c_credit_requests`, which is additive and passes `check_migration_safety --base origin/staging` with "additive only". Contents:
  - the `CreditRequest` table, with the inbox index and one open request per teacher (a partial unique index on PENDING/APPROVED);
  - `LicenseOveragePurchaseStatus.SUPERSEDED`;
  - `LicenseOveragePurchaseIntent.session_expires_at` and `.credit_request_id`;
  - `LicenseOverageOfflineRequest.credit_request_id`.
  - The new id columns have no index, so the migration stays additive only. The tables are small, and only the sweep and the hooks read these columns.
- **`billing/credit_requests.py`.**
  - Create, decline and withdraw.
  - The ordered approve: (1) PENDING to APPROVED, committed; (2) `initiate_overage_purchase(..., credit_request_id=)`, run outside any transaction; (3) back to PENDING with the error on any failure; (4) link the purchase.
  - The hooks.
  - The sweep.
  - The W texts, in one dict.
- **Drift fixes D1-D5 (STEP0_PLAN §2).**
  - D1: approve takes `success_url` and `cancel_url`.
  - D2: SUPERSEDED, not FAILED. The completed handler refuses and refunds only SUPERSEDED intents with no grants; refunds use the departed-share refund task for the whole amount. FAILED handling is unchanged.
  - D3: `checkout.session.expired` is wired in `_EVENT_HANDLERS` and left out of AUTO_REPLAYABLE.
  - D4: a third GRANTED hook in `_grant_overage_offline`, the super admin's grant-now.
  - D5: a purchase-lock refusal is an ordinary step-3 revert.
- **GRANTED hook.** It runs in the fulfilment's transaction, in three places: the Stripe completed handler, `approve_overage_offline_request` and `_grant_overage_offline`.
  - It matches on `credit_request_id` from the intent or the session metadata, and accepts PENDING or APPROVED.
  - A teacher skipped at fulfilment moves the request to CLOSED, never back to PENDING.
  - It moves the request's other open intents to SUPERSEDED and expires their checkout sessions after commit.
- **Back to waiting.**
  - `checkout.session.expired`.
  - The stale-APPROVED sweep, on the stored `session_expires_at` plus 10 minutes.
  - The unlinked-APPROVED sweep after 15 minutes, which links the purchase instead when one carries the request's id.
  - `reject_overage_offline_request` (W12).
  - The offline approval's own automatic rejection when the license has become inactive closes the request instead ("license ended").
- **Closing on loss of access.**
  - One helper, `close_open_requests`, is called at each path that ends access:
    - A1 `remove_teacher_from_license`;
    - A2 the offline cancel;
    - A4 renewal with auto-renew off (both the service and the Beat task);
    - A5 renewal failed for every teacher;
    - A6 Stripe subscription updated (canceled or unpaid) and subscription deleted;
    - A7 the replacement license.
    - A3 is covered by A6.
  - The sweep also closes any open request whose access has ended (the catch-all).
  - Approve re-checks access, and closes the request instead of buying.
  - The teacher gets W4 (owner Q1).
- **API.** `/api/v1/billing/credit-requests` offers POST, GET (`?status=`), `counts`, and `{id}/approve`, `decline`, `withdraw`.
  - It returns 404 while `CREDIT_REQUESTS_ENABLED` is off.
  - Who sees what: the teacher sees their own requests, a license's admin sees that license's, and super admins see and answer all (owner Q3).
- **Beat.** `sweep-credit-requests` runs every 5 minutes, is single-instance, and does nothing while the flag is off.
- **Choice made.** The purchase hooks (GRANTED, SUPERSEDED refusal, close) run whatever the flag says. They act only on request rows the API created, and a SUPERSEDED intent must never be granted even if the flag is switched off later.

## Evidence

- R1 red, tests first, `billing.tests.test_credit_requests`: Ran 43 FAILED (failures=2, errors=41). The only test that passed is the flag-off 404 guard.
  - After that run, the test file was changed only so the commit hooks would pass: mypy-safe `getattr` for names that did not exist yet, and `response.data` in place of `.json()`, because the API renderer wraps responses.
- R2 green: Ran 43 OK.
- Plants on `test_credit_requests` (all killed, files restored):
  - P1, a superseded intent paid anyway is granted: 1 FAIL.
  - P2, the GRANTED hook matches only by the link (M-B24): 5 FAIL.
  - P3, removing a teacher leaves the request open: 1 FAIL.
- N0 near run before the guard fixes (on ef1080075): Ran 5046 FAILED (failures=4, skipped=16). The four standing guards that failed, all fixed in the guard-registration commit:
  - route coverage: the flag-off 404 ran before sign-in, so an anonymous caller got 404; it now runs after sign-in (anonymous gets 401);
  - audit target label: `CreditRequest` had none; added "Credit request" (new admin-facing label, needs owner approval);
  - history guard: approve's dynamic link update is now static, and `_move` (conditional status update) is listed;
  - student data map: `CreditRequest.teacher` and `decided_by` added as staff.
  - Guards + credit tests after the fixes: Ran 134 OK.
- N1 near run on the final tree (rebased on staging b3c900aaf), billing + users + guards67, holds and credit requests unset. Expected Ran 5052, 10 static skips (billing 2994, users 1137, guards67 1151, overlap removed). Result: Ran 5052 OK (skipped=16).

## Before the flag goes on (ops)

- Subscribe the production Stripe webhook endpoint to `checkout.session.expired`.
- The frontend inbox page and the request button are not part of B1c.
