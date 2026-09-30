# Epic A completion S3: the system actor and background work

Branch `task/epic-a-s3`. Cut from phase2/epic-a `d7f2737`, then merged with `75bf91a` (S6a). Phase 2 only. The verifier is v2. No migration (`AuditEvent.action` has no choices).

Plan: `08_epic_a_completion_plan.md` §4 (gaps G5, G6, G7; decisions D4, D6), plus the SM's S3 notes.

## 1. The actor rule (G5)
**Before:** `billing.models._emit_credit_transaction` named the **wallet owner** as actor for every ledger row. So:
- a Celery grant, renewal or expiry looked like the teacher's own action;
- a school admin's `add_teachers` left an event naming the teacher, not the admin (v2's S1 V1).

**Now:**
- The **actor is the initiator**: the signed-in user of the request being handled, or SYSTEM (`actor=None`) in Celery, Beat, a management command, or an anonymous request.
- The **wallet owner is the target** (`target_type="CustomUser"`), and the event is scoped to the owner's school.
- The ledger row stays named in `metadata.ledger_id`.

**How the chokepoint knows the request:** S1's per-request `RequestAuditState` now carries the Django request (`AuditMiddleware` opens it with `request_audit_state(request)`). `audit.context.current_request_actor()` reads `request.user`, which DRF writes back onto the Django request when it authenticates. Outside a request there is no state, so the actor is SYSTEM.

**The S1 invariant (SM pin):** after S3, a school admin's `add_teachers` credit grant **names the admin**, so it is the admin's trace and the generic STATE_CHANGE is **not** written. `audit/tests_license_admin_attribution.py` is updated on purpose:
- add_teachers leaves exactly one event naming the admin (CREDIT_TRANSACTION GRANT, target the teacher), and no STATE_CHANGE;
- remove_teachers leaves one EXPIRE naming the admin, and no STATE_CHANGE.

**Updated on purpose:** `billing/tests/test_credit_transaction_audit.py` pinned "actor = owner, target = ledger row". Outside a request it now expects actor SYSTEM, target the owner, and `ledger_id` in metadata.

## 2. The sweeps record themselves (G6, D6)
`audit.tasks.sweep_audit_retention` and `sweep_audit_pii_short_retention` each emit one `AUDIT_RETENTION_SWEEP` per run:
- a new action, GENERAL retention, actor SYSTEM, counts only;
- metadata `{deleted_general, deleted_student_record}` or `{scrubbed}`.

**A run that deletes nothing still records itself**, so a stopped sweep shows as a gap.

## 3. The licence clawback goes through the ledger (G7, D4)
**Before:** `remove_teacher_from_license` expired the teacher's buckets with a bare `.update(expires_at=now)`: no ledger row and no event. Its `logger.info` also logged `teacher.email` (a BE-A-04 leak).

**Now:** each still-active bucket gets `expires_at = now`, then goes through `SubscriptionService.expire_bucket(bucket, reference="Removed from licence <id>: ...")`, exactly as the Beat cleanup records a normal expiry:
- one visible **EXPIRE ledger row per bucket with credits left** (D4);
- so one `CREDIT_TRANSACTION` per bucket, naming whoever removed the teacher.

The log line carries ids only. The rollover's `logger.info` in `_rollover_and_grant_monthly_bucket` had the same leak and is fixed too (SM ruling).

**Found while building it: a double-expire race.** `expire_bucket` locked the bucket but never re-checked `is_processed` under the lock, so two expiries of one bucket wrote two EXPIRE rows. It is live on beta through overlapping cleanup runs, and was fixed there separately (`task/expire-bucket-race`, VERIFIED by 1a). S3 carries the same guard, because the clawback adds a second path.

## Tests
`audit/tests_background_attribution.py`:
- **Actor rule:**
  - a Celery-style grant is SYSTEM, with the owner as target, the owner's school and `ledger_id`;
  - the same grant inside a superadmin request names the superadmin;
  - an anonymous request is SYSTEM;
  - the Beat cleanup's expiry is SYSTEM.
- **Sweeps:**
  - a zero-count retention run records `{0, 0}`;
  - a retention run with two old rows records `{2, 0}`;
  - the PII sweep records `{scrubbed: 0}`.
- **Clawback** (MONTHLY 1000/300 used, CARRY_OVER 200/0, and a spent OVERAGE):
  - the ledger nets exactly the expired credits (EXPIRE rows [700, 200], every bucket processed, balance 0);
  - one CREDIT_TRANSACTION per bucket with credits left, naming the remover, with the teacher as target;
  - no email in the log.
- **Rollover:** the suppressed-rollover log line carries no email.
- **Gate 3** (real threads, TransactionTestCase):
  - a clawback racing the Beat cleanup on the same bucket writes one EXPIRE;
  - a clawback racing the monthly grant expires nothing twice.

## Gates
_pending_ (rule 15).
