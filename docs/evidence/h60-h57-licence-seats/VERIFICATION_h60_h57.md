# Verification: H-60 + H-57 (ed), including v2's NotRecorded finding

- **Branch:** task/h60-h57-licence-seats at **9dd291a** (code ed89d09), off task/beta-batch-4 8de3078, bundle 5.
- **Verifier:** v2 (independent), 2026-09-30.
- **Verdict:** **VERIFIED-WITH-NOTES**

## What was run
All runs used rule 13 (6G scope) and rule 12 (timeout 1800), in the scratch worktree detached at 9dd291a with DB test_vf2_s1. The billing regression was not repeated (rule 15; ed's 1910 OK at ed89d09 stands).

| Run | Result | Log |
|---|---|---|
| v2 probe + ed's `test_h57_licence_patch` + `test_h60_licence_stripe_text` + guards (below) | 89 run. The only 2 failures were v2 probe fixture mistakes (A1 re-setUp hit a unique school name; A2 assumed PUT was routed). Everything else passed. | runs/h57_h60_9dd291a.log |
| H-57 probe re-run after the fixture fixes | 4/4 OK | runs/h57_h60_9dd291a_probe2.log |
| v2 mutants V1–V3 | 3/3 killed, file restored and hash-checked | runs/h60_nr_mutants_9dd291a.log |

**Guards run (addendum 2, beta set):**
- AutoGrader.tests_no_wildcard_invalidation
- AutoGrader.tests_cache_invalidation_coverage
- AutoGrader.tests_migration_rollback_defaults
- AutoGrader.tests_error_messages
- classrooms.tests_teacher_access_sweep
- classrooms.tests_course_roster_scope_sweep

All passed.

**Probe:** tests_vf2_h57_h60_probe.py. **Mutant runner:** vf_h60_nr_mutants.py.

## H-57 evidence
**A1, introspection.** The writable fields of LicenseSubscriptionSerializer are pinned: 11 fields. Each was PATCHed alone with a changed value, against the same fixture in a rolled-back savepoint:
- **REFUSED** (400 naming the field, nothing stored changed): admin_user, billing_method, contract_months, max_seats, plan, school, stripe_subscription_id, teacher_emails.
- **APPLIED:** auto_renew, custom_price_cents.
- **IGNORED:** carry_forward_teachers (SM ruling).

No silent drop remains. is_active is read-only.

**Other checks:**
- **A2:** PUT is 405 on this viewset, so there is no full-update path. A changed max_seats sent by PUT is also 405 and not applied.
- **A3, atomicity:** custom_price_cents in the same PATCH as a refused plan is not applied (ed's test covers auto_renew only).
- **A4:** a school admin's PATCH of max_seats is 403 and not applied.

## H-60 evidence
**Static sweep beyond the 9 sites** (2d09675, re-checked at 9dd291a):
- Every licence view's `str(exc)` catches ValueError only.
- All remaining `{exc}` in the licence service go to `abandon`/`escalate`/`failure_reason`.
- The only live callers of the four Stripe-mutating services are the four routes. `StripeSubscriptionMutationService.change_license_price` and `StripePriceService.create_custom_price` have no callers.

**B1, route level.** 9 route × site cases, with the sentinel injected:
- seats: retrieve, modify, card
- plan: retrieve, create_price, modify, card
- cancel: modify
- convert-to-offline: delete (PermissionError)

Every case returns a 400 with fixed text and no sentinel in the body. The sentinel appears in **no log record from any logger**, with records formatted including exc_info. (ed's H-60 tests are service level apart from one seats route test.)

## NotRecorded finding: fixed
- **C1:** seats increase, invoice unreadable with a Stripe sentinel. 409, no Retry-After, intent ESCALATED, "flagged for manual reconciliation", no sentinel in the body or in `billing.license_views`' log.
- **C2:** plan upgrade, card declined AND the undo fails with a sentinel. 409, ESCALATED, fixed text, no sentinel in the body or the views' log. Before the fix, both paths were `except Exception`, giving a 500 and a traceback log.

**Mutants:** ed's N1 removes only the seats handler. I removed the handler on the other three routes:

| Mutant | Killed by |
|---|---|
| V1 plan | ed's plan 409/503 tests + v2 C2 |
| V2 convert (no `except Exception` fallback) | ed's convert 409 test |
| V3 cancel | ed's cancel 409/503 tests |

Every NotRecorded construction (6 sites) passes `intent=`. The only compensated site (`finalise`'s undo) passes `escalated=False`; `undo_unpaid_change` failing correctly defaults to escalated.

## Notes (none blocks the verdict)
1. **H-28's reconciliation channel carries Stripe text.** In C1/C2 the sentinel is in `billing.license_stripe_mutation`'s "MANUAL RECONCILIATION NEEDED" ERROR line. `escalate()` logs `why`, which on the invoice-unreadable and undo-failed paths includes `{exc}`. The same `why` goes into the super-admin email. The pre-existing "Stripe response lost (%s)" warning also logs exc.

   This is operator-facing and by H-28's design, so it's not an H-60 defect (QA-ERR-03 is client text). But two statements are too broad:
   - ed's claim "Stripe text only in failure_reason and an ids-only log";
   - NotRecordedRouteTests' comment ("H-28's own reconciliation lines … carry the database error, never Stripe's text").

   Suggest EVIDENCE/comment wording: "the client and the new route log line carry no Stripe text; H-28's reconciliation alert (log + email) keeps Stripe's detail for the human."
2. **Plan route message framing.** It reads "Stripe price change failed: Card declined. …": license_service's wrapper prefixes the fixed inner text. It's our own text and not a leak. Cosmetic (a doubled frame that names the provider).
3. **PATCH custom_price_cents changes only the local price** on a STRIPE licence; the Stripe price is untouched. That is local/Stripe price divergence of the kind H-28 removed for change_plan. It's pre-existing and applied per the H-57 scope. SM to decide whether it needs a backlog item.
4. **Dead code:** `change_license_price` and `create_custom_price` (the latter has `{exc}` in a ValueError). Note only (SM ruling).
5. **Frontend contract changes (already flagged by ed):** the PATCH 400s, and the 409 / 503 + Retry-After on the four routes, in the `{"success": false, "message": …}` envelope.
