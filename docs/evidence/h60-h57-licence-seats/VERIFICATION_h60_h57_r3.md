# Verification: H-60 + H-57 round 3 (ed): re-check of v2's notes 1–3

- **Branch:** task/h60-h57-licence-seats at **e59a494**: code 66f92cd, on top of v2's VERIFIED-WITH-NOTES at 9dd291a (6560d8f).
- **Verifier:** v2 (independent), 2026-09-30.
- **Verdict:** **VERIFIED**. Notes 1–3 are closed. Note 4 (dead code) is in the backlog per the SM. Note 5 (contract changes) stands, with one addition below.

## What was run
One targeted run, rules 12 and 13 (6G scope), in the scratch worktree detached at 66f92cd with DB test_vf2_s1. The billing regression was not repeated (rule 15; ed's 1912 OK at 66f92cd stands). No v2 mutation step was needed (see E1).

| Run | Result | Log |
|---|---|---|
| v2 round-3 probe `tests_vf2_h57_r3_probe` + v2 `VfH60Routes` / `VfNotRecorded` re-run at the new tip + ed's `test_h57_licence_patch`, `test_h60_licence_stripe_text`, `test_h28_plan_phases` + beta guards | **113 OK** | runs/h57_h60_r3_66f92cd.log |

**Guards run (addendum 2, beta set):**
- AutoGrader.tests_no_wildcard_invalidation
- AutoGrader.tests_cache_invalidation_coverage
- AutoGrader.tests_migration_rollback_defaults
- AutoGrader.tests_error_messages
- classrooms.tests_teacher_access_sweep
- classrooms.tests_course_roster_scope_sweep

## Note 1 (Stripe text in H-28's reconciliation alert): wording fixed
The module docstring and the NotRecordedRouteTests comment in test_h60_licence_stripe_text now say that the client body and the new route log line carry no Stripe text, and that H-28's reconciliation alert (operator log + super-admin email) keeps it by design. EVIDENCE says the same. The C1/C2 re-run still shows the sentinel only in `billing.license_stripe_mutation` (the alert), never in the body or `billing.license_views`.

## Note 2 (double frame): fixed
- `change_license_plan` now re-raises the inner ValueError. The `try` wraps only `apply_licence_price_at_stripe`, whose ValueErrors are all fixed text (B1).
- **E5** (route level): change_plan with `create_price` refused and the sentinel injected gives a 400 with "Custom price creation failed. Please try again; …", no "Stripe price change failed" prefix, and no sentinel.
- The 9-case B1 sweep re-run at 66f92cd: every case gives a 400 with fixed text, no sentinel in any body or any log record.

## Note 3 (local-only price on a Stripe licence): fixed
**R3-A1, introspection re-run** (11 writable fields, each PATCHed alone with a changed value on the STRIPE fixture):
- **REFUSED:** admin_user, billing_method, contract_months, custom_price_cents (with STRIPE_PRICE_NOT_PATCHABLE), max_seats, plan, school, stripe_subscription_id, teacher_emails.
- **APPLIED:** auto_renew.
- **IGNORED:** carry_forward_teachers.

**Edge probes:**
- **E1:** STRIPE licence, stored price 5000, PATCH 5000 + auto_renew false. 200: auto_renew applied, price still 5000. This is an unchanged non-null echo; ed's echo test uses a stored null.
- **E2:** STRIPE licence, stored 5000, PATCH null. 400 with STRIPE_PRICE_NOT_PATCHABLE, nothing applied. Clearing the price counts as a change.
- **E3:** OFFLINE licence, stored 5000, PATCH null. 200, the price is cleared.
- **E4** (the message's pointer works): change_plan to the same plan with a custom price on a STRIPE licence gives 200, action "charged". The local price is 1500, the Stripe unit amount moves from 12000 to 18000, and the one CHANGE_PLAN intent is COMPLETE.
- **A2–A4 re-run:** PUT is still 405. A price change sent with a refused plan is not applied. A school admin gets 403.

## Notes (none blocks)
1. **Test gap closed by E1.** ed's tests would not kill a mutant that replaces `_stored_differs(...)` in the new check with `attrs["custom_price_cents"] is not None`: the echo test's stored price is null, so both forms accept it. E1 (a stored 5000 echoed as 5000) fails under that mutant. Suggest adding E1 to test_h57_licence_patch. Not required for this verdict.
2. **Contract change (adds to note 5):** a PATCH of a changed `custom_price_cents`, including null, on a STRIPE licence is now a 400 naming the field. The frontend should send price changes to change_plan.

## Worktree
The probes were removed after the run; the worktree is clean at 66f92cd. Probe: tests_vf2_h57_r3_probe.py (it imports tests_vf2_h57_h60_probe.py as billing/tests/test_vf2_h57_h60_probe.py).
