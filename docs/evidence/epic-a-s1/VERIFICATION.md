# Verification: Epic A S1 @ 419e9f6

**Verifier:** Verification Engineer (1a, grade-automator-plus-09). **Author:** Security (ed).
**Base:** phase2/epic-a cc34081 (Phase 2 only). **Date:** 2026-09-30.

**Verdict: REJECTED.** One defect breaks S1's headline guarantee, "every state-changing request leaves exactly one audit event". The fix is small, and I have already tested it (below).

## R1: a named event that rolls back still suppresses the fallback, leaving ZERO events
`emit()` stores the event in `transaction.atomic()`, which is a **savepoint** when the caller is already inside a transaction. It then calls `mark_named_emitted()` straight away. If the caller's transaction later rolls back, the event row goes with it, but the flag stays set, so `AuditMiddleware` skips the generic STATE_CHANGE. The request ends with **no audit event at all**.

`users/auth_audit.py`'s docstring already warns about this trap for the auth doors, and the registration and Google doors avoid it by emitting after the block. The request-level flag, however, has no protection. The emitter's other callers, notably `CreditLedger.record()` → `_emit_credit_transaction` in `billing/models.py`, which billing services call inside their own atomic blocks, are exposed.

**Reproduced.** A TransactionTestCase with real commits (`audit/tests_vf_s1_probe.py`, attached) runs through the real middleware with a signed-in teacher on `POST session-list`, with the view patched:

| Case | Response | Events stored |
|---|---|---|
| Control: the view raises `ValidationError` | 400 | `[STATE_CHANGE FAILURE]`, 1 |
| The view emits a named event in `atomic()`, then raises `ValidationError` in the same block | 400 | **`[]`, 0** |

I have not traced a specific production route that stores a named event and then rolls back; the mechanism is shown with a patched view. The guarantee has to hold whatever a caller does, and S2's route-coverage guard will rely on it.

**Tested fix** (`vf_s1_rollback_fix.patch`, 20 lines, attached):
- `RequestAuditState` keeps the ids of the events stored during the request.
- `emit()` passes `event.pk` to `mark_named_emitted`.
- `AuditMiddleware` writes the generic event unless one of those ids still exists (`AuditEvent.objects.filter(pk__in=ids).exists()`). That is one query, only on requests that stored an event, inside a try/except that falls back to writing the generic event.
- This works under real commits and inside APITestCase alike.

An `on_commit`-based mark would not work here: the flag would never be set inside test transactions, and every APITestCase that expects exactly one event would record two.

With the patch applied to 419e9f6:
- the probe records 1 event for both cases;
- `audit.tests_state_change`, `audit.tests_admin_action`, `audit.tests_emitter`, `users.tests_auth_audit_doors` and `users.tests_auth_audit_events` plus the probe give **127 OK**.

**Required:**
1. Apply the fix, or an equivalent.
2. Add the probe as a regression test. It has to be a TransactionTestCase, or it must at least use an inner savepoint that rolls back.
3. Add a mutant that restores the immediate flag; it must be killed.

## Checked and sound (no action)
- **The 429 exemption.** Only two views raise `Throttled` themselves: `register/student` and `renew-student-token`. Both are `AllowAny`, so they are never recorded, and both refuse before doing any work. Every other 429 comes from DRF throttles, before the view runs.
- **Middleware bypass.** Every Django route passes through the middleware. DRF's `Request.user` setter writes the authenticated user back to the Django request, and Django admin POSTs by staff are recorded as STATE_CHANGE with an `admin:` route name, which is good coverage. Requests that fail authentication are anonymous, so nothing is recorded, as designed.
- **ContextVar.** The state object is mutated, never re-`set`, so a context copied into `sync_to_async` threads still marks the right request. It is always reset in `finally`, and ed's thread test covers isolation.
- **What is recorded.** Only the route name, method, status and a UUID target from the URL kwargs; never the body, query string or path. The allowlist confirms this.
- **Attribution and scoping.** Failure actors are never the account holder, and `school_id` is the target's. This matches the SM ruling, and ed's mutants A9–A13 cover it.
- **Events from Celery tasks** (for example grading) are not counted for the request, so the request keeps its own STATE_CHANGE. That is a separate action, not a duplicate: acceptable.

## Owed at re-verification
After the fix, I will run:
- the whole regression set (`audit users classrooms students assignments`, 2031);
- `makemigrations --check` for audit 0002 (choices only);
- my own mutants, including the rollback one;
- whole-repo mypy.
