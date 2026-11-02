# H-275 + H-277 (SM-built, branch off H-276 tip 194b1a7b)

## H-275: plain invoice wording (owner approved 2026-11-02, American spelling)
Sites: billing/stripe_service.py:3900 (license paid), 3993 (individual paid), 4170 (individual failed), 4218 (license failed).
New pure helper billing/invoice_wording.py `invoice_description(*, billing_reason, paid, license)`; the table is the
owner-approved one verbatim; License = same text with "Subscription" -> "License"; any other/missing reason -> fallback.
New rows only. Out of scope (noted, not changed): "Upgrade payment failed (invoice status: open)" error text at
stripe_service.py:1259/1850 shows a raw Stripe status, not billing_reason; not in the owner's approval.

## H-277: logout only blacklists the caller's own refresh token (owner approved 2026-11-02)
A refresh token whose user_id claim is not request.user is refused exactly like an invalid token: 400
"Invalid or expired token", REFRESH_TOKEN_INVALID failure audit row, nothing blacklisted, nothing revoked. No new wording.

## Red run expectation (committed before the run)
Command: `python manage.py test billing.tests.test_h275_invoice_wording users.tests_h277_logout_owner --settings=settings_worktree --noinput`
Expected: Ran 16, FAILED (failures=10, errors=5).
- errors=5: WordingTableTests (billing.invoice_wording does not exist).
- failures=6: the 4-site end-to-end rows still say "... invoice paid (subscription_cycle)".
- failures=4: H-277 refused / victim refresh works / caller stays signed in / audit row. test_own_refresh_still_logs_out passes.
Anything else = STOP.

Process note: one earlier invocation (before this file) crashed at settings import (KeyError SECRET_KEY, no .env
copied into the new worktree); zero tests were collected or run. .env copied from the H-276 worktree (git-ignored).
