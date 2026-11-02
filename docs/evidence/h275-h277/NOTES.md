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

## Red run result (efa2fe46)
Ran 16, FAILED (failures=10, errors=5): matches the expectation exactly. Log ~/Documents/Projects/GAP-a0-runs/h275_red_efa2fe46.log.

## Fix (this commit)
- billing/invoice_wording.py (new), 4 sites in billing/stripe_service.py call it.
- users/views.py logout: refresh token whose user_id != request.user raises TokenError before blacklist (same 400 path).
  H-276's later `same_user` condition removed: always true after the H-277 check (would be a dead branch / equivalent mutant).
- Process miss (recorded, not hidden): one targeted check (the 16 new + 11 H-276 tests, Ran 27 OK) was run on the
  uncommitted fix before this expectation existed. The runs below are the gated ones.

## Expectations (committed before the runs)
1. Mutation: `python docs/evidence/h275-h277/mutants.py`. Expected: M-1..M-8 all KILLED, every restore sha256 OK.
   M-1 license swap off, M-2 paid/failed swapped, M-3 fallback leaks raw reason, M-4..7 each site's license flag
   flipped, M-8 H-277 ownership check off.
2. Near run: `python manage.py test billing users --settings=settings_worktree --noinput`.
   Expected: Ran 3223, OK (skipped=4). (Count from DiscoverRunner.build_suite without running; skips = the 3
   CI_REQUIRE_NETWORK google tests + 1 CI_REQUIRE_REDIS, as seen at -v 2 on H-276.)
3. Full suite count (for the batch): 8614 = 8598 (H-276) + 16.
Anything else = STOP.
