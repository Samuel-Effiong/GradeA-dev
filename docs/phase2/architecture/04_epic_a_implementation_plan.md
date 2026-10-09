# 04 — Epic A Implementation Plan (Action logging, error taxonomy, observability)

**Status:** plan for review. Nothing here is implemented yet.
**Companion to:** [01a_requirements_specification.md](01a_requirements_specification.md) (FR-A-01…11),
[03_architecture.md](03_architecture.md) (§2.1, §3.1 Stage 1, X-4, X-5),
[03a_data_model.md](03a_data_model.md) (§2.1 `AuditEvent`).

This document turns the spec + architecture + data model into an ordered,
concrete build plan: what gets built, in what order, against which existing
code, with a test mapped to every acceptance criterion. Where the prior
documents left something unverified or, on inspection of the actual
codebase, wrong, that is called out here rather than silently assumed.

---

## 0. Corrections to the prior documents

Verified directly against the codebase while drafting this plan. These
either close an open question in `03_architecture.md` Part VIII or flag a
new problem.

### 0.1 Open Question #1 is answered: correlation ID DOES cross the Celery boundary — confirmed, not assumed

`03_architecture.md` §2.1 and Part VIII #1 list this as "must verify."
Read directly:

- [`AutoGrader/request_context.py`](../../../AutoGrader/request_context.py) —
  a `contextvars.ContextVar` holds the current request/task id, with a
  logging filter (`RequestIDLogFilter`) that injects it into every log
  record.
- [`AutoGrader/celery_signals.py`](../../../AutoGrader/celery_signals.py) —
  `before_task_publish` stamps the current id onto every outgoing task's
  message headers (covers `.delay()`, `.apply_async()`, the `safe_delay()`
  wrapper, and task-to-task chaining uniformly); `task_prerun` reads it back
  and restores the contextvar in the worker process; `task_postrun` clears
  it, so prefork process reuse can't leak one task's id into the next.
  Handles both real-broker dispatch and `CELERY_TASK_ALWAYS_EAGER` (test
  mode) by checking both `task.request.<key>` and
  `task.request.headers[<key>]`.

**Conclusion: BE-A-03's queue hop is already built and already correct.**
FR-A-03's remaining gap is narrower than the architecture doc assumed — it
is *not* "propagate across Celery" (done), it is:

1. Stamp the same id onto the model-provider call itself (currently nothing
   attaches `request_id` to the outbound call in `ai_processor/services.py`,
   so "any model call" in FR-A-03's wording is the one real gap).
2. Persist the id onto `AuditEvent.trace_id` at write time (the audit table
   doesn't exist yet — that's most of this plan).
3. Resolve X-5 below (the *audit* trail needs a server-authoritative id,
   distinct from the client-trustable one this infrastructure already
   provides for logging).

### 0.2 Open Question #6 is answered: the duplicate-Beat idempotency pattern already exists — reuse it directly

`billing.tasks.cleanup_expired_credit_buckets` is the exact shape FR-A-08
needs: a sweep filtered on a boundary condition (`expires_at__lte=now,
is_processed=False`) where a second run finds nothing left to touch. No
distributed lock, no `PeriodicTask` de-duplication exists anywhere in this
codebase (`AutoGrader/beat_health.py` is a *watchdog for missed runs*, not
a mutex against concurrent runs) — duplicate-Beat safety here is achieved
by the sweep query itself being idempotent, not by locking. The retention
sweep (§6 below) follows the same shape: `DELETE ... WHERE occurred_at <
cutoff AND retention_class = X` — a second concurrent run deletes zero rows.

### 0.3 New finding: `license_id` should be `School.id`, not `LicenseSubscription.id` — flagging and resolving

`03a_data_model.md` §2.1 defines `AuditEvent.license_id` as "The School
License the action occurred under." Taken literally, "License" is
`billing.models.LicenseSubscription` — but that table's `school` FK is
`related_name="license_subscriptions"` (**plural**): a school can and does
accumulate more than one `LicenseSubscription` row over its lifetime
(renewal, replan, re-signing after a lapse). If `license_id` stored the
`LicenseSubscription` PK, a School Admin's audit history would fragment
across every renewal — FR-A-09's "School Admin view scoped strictly to
their own License" would silently stop showing pre-renewal history the
moment the school renews, with no error and no indication anything was
dropped.

Every existing tenancy boundary in this codebase — permission classes,
`get_queryset()` filters, the dashboard endpoints landed earlier today —
scopes a School Admin by `school_id`, not by the current
`LicenseSubscription.id`. That is also the stable, renewal-proof identifier,
and it is `NULL` for an Individual Teacher exactly as the spec requires
(they have no `school`).

**Resolution:** `AuditEvent` stores `school_id` (captured as a value, no
FK, following the same identity-as-value rule as every other column on this
table) and the column is named `school_id` in code, with the data-model
doc's "License" framing kept as the *product* description of what that
scope means, not as a literal FK target. Flagging this now, before the
migration is written, because renaming a column after events exist means a
backfill; renaming it in a spec that hasn't been built yet costs nothing.

### 0.3a Build-time correction (2026-09-22, during implementation): `trace_id` must resolve from `request_context.get_request_id()`, not a standalone ContextVar

Found by audit-lead while implementing §4: the reused branch's
`audit/context.py` defines its own separate `audit_trace_id` ContextVar,
disconnected from `AutoGrader/request_context.py`'s already-confirmed-working
propagation (§0.1). Left as-is, every real call site would stamp a random
`uuid.uuid4()` on `trace_id` — not the id that actually ties a request to
its Celery tasks and log lines — silently defeating FR-A-03/X-5 despite
passing tests, because the existing tests exercise the scaffold directly
rather than the real pipeline.

**Fix, approved and folded into the §4 emitter deliverable (not deferred to
§5):** `_build()` resolves `trace_id` via
`AutoGrader.request_context.get_request_id()`, parsed as a UUID (it's a
`uuid4().hex`, which `uuid.UUID()` accepts); falls back to a freshly
generated UUID when there is none (background tasks with no originating
request) or the inbound value isn't UUID-shaped (an untrusted/malformed
client-influenced value, consistent with X-5's "server-authoritative, not
client-trustable" requirement). This is exactly what §4.1 step 3 already
specified — the gap was the reused branch's standalone scaffold not being
wired to it, not a gap in this plan.

### 0.3b Build-time improvement (accepted): DB-level `CheckConstraint` for X-4 + narrowed `mutable_fields`

The reused branch's model carries a `CheckConstraint` refusing
`source_ip`/`user_agent` on `STUDENT`-actor rows at the database level
(`audit_student_no_pii_ck`), and declares `mutable_fields =
{"source_ip", "user_agent"}` so the retention sweep's PII-short-retention
null-out (§7.1) can run without `allow_unsafe_mutation()` for exactly
those two columns. This is **strictly stronger** than §2.2's literal code
sample (`mutable_fields = frozenset()`, X-4 enforced only at the emitter
layer) — a constraint the database itself refuses is not bypassable by a
future emitter bug the way an application-layer check is. **Adopted as the
correct design; §2.2's code sample is superseded by this.** The sweep in
§7.1 should therefore call plain `.update(source_ip=None, user_agent=None)`
for the two mutable fields without needing `allow_unsafe_mutation()` at
all — only the row-delete sweep still needs that context manager.

### 0.4 Clarification (not a contradiction): FR-A-02 schema validation vs. FR-A-11 never-fail-the-caller

FR-A-02 requires "a write missing a required field is rejected"; FR-A-11
requires that logging failure must never fail the user's action. These
resolve to two different failure surfaces, not one:

- **Schema validation** (required-field check, allow-listed metadata keys)
  is a programming-error guard. It raises **inside the emitter**,
  immediately, so a call site that forgets `target_id` fails loudly in
  tests/CI/dev — the same posture the codebase already takes with
  `ImmutableRecordError` on the append-only tables.
- **Everything else** (the audit store being down, a DB write timing out,
  an unexpected exception in the emit path) is caught at the **outermost**
  layer of the emitter and never propagates to the caller. See §3.3.

These are the same rule the existing `AppendOnlyModel` machinery already
applies to `CreditLedger`: application-level guards raise on a mistake in
committed code, and are never a reason to hand a user a 500 for their own
unrelated request.

**Build-time correction to this section's own prescription (2026-09-22):**
the implementation swallows schema-validation failures too, by default —
`_build()` raises `AuditValidationError` internally, but `emit()`'s outer
catch treats that the same as a store failure (logs at ERROR, increments
the failure metric, returns `None`) unless the caller passes `strict=True`.
This reads as a *stronger* posture than this section's original wording
("the ONLY part of the emitter allowed to raise") — a call site with a
genuine schema bug still never risks a user-facing 500, and it is still
operationally visible (the ERROR log + `audit_emit_failures_total`, §9).
**Adopted as the correct design.** The consequence: every call-site
instrumentation test (§6's "exactly one event" tests) catches a schema
mistake anyway, because a swallowed validation failure means no row was
created and `AuditEvent.objects.count() == 1` fails — `strict=True` is
only needed in a test that wants to assert the *specific* validation
exception, not for the basic per-site regression tests §6 already
requires. §4.1 step 2's wording is superseded by this: schema validation
raises `AuditValidationError` from `_build()`, and is then subject to the
same catch as everything else in step 7, not exempted from it.

### 0.5a Open Question #4 is answered: BE-A-04 is NOT a clean slate — pre-existing leaks confirmed, bundle the fix into Epic A

Worker 88 ran a full enumeration (not a sample) of all 625 `logger.*`/
`print`/Sentry calls outside tests/migrations. Verdict: **11 confirmed
student/user PII leaks**, plus a systemic pattern and a Sentry
configuration gap that matters more than any single leak.

**Confirmed leaks (fix as part of this epic, not a follow-up):**

| # | Location | Leak |
|---|---|---|
| 1 | `assignments/tasks.py:1061` | `print(f"...{submission.student.get_full_name}")` — student name, bare `print` (bypasses the logging filters entirely), also missing `()` |
| 2 | `classrooms/services/roster_import.py:369-373` | `logger.error(..., row.first_name, row.last_name, ...)` — student name on every bulk-add failure |
| 3-5 | `AutoGrader/tasks.py:48,56-60,63-66` | shared email-send helper logs the raw recipient list at info/error/warning — used for student, teacher and admin sends alike |
| 6 | `users/serializers.py:338-341` | `logger.exception(..., user.email)` on registration email failure |
| 7 | `users/mailerlite_service.py:100` | `logger.error(..., user.email, exc_info=True)` |
| 8 | `users/signals.py:242,255,267` | `logger.debug(...email)` ×3 — DEBUG level, lower prod risk but still a leak if debug logging is ever enabled in prod |
| 9 | `classrooms/serializers.py:758-762` | admin invite email in `.exception()` — admin-side, lower severity |
| 10 | `billing/license_service.py:1339` | `logger.info(..., teacher.email)` |
| 11 | `billing/management/commands/backfill.py:117` | `print(f"...{user_sub.user.email}...")` in a management command |

**Needs a closer look, not yet confirmed:** `assignments/services.py:640,772`
(`%r` on untyped rubric entries, which can carry evidence-quote text);
broad `exc_info=e` on unresolved-origin exceptions in `students/views.py`,
`assignments/views.py`.

**The systemic finding (bigger than the 11 lines):** a dominant
`except Exception as e: logger.error(..., exc_info=e)` shape across
`students/`, `assignments/`, `classrooms/`, `users/` views. Nothing
confirmed leaking today through it, but it is exactly the shape that
starts leaking the moment someone adds `ValidationError(str(request.data))`
inside one of these blocks — a landmine, not a leak. `ai_processor/
services.py` has ~15+ sites (2743, 2754, 2765, 2771, 2916, 2922, 3510…)
logging `str(e)`/`exc_info=e` around AI-provider calls and JSON parsing of
a grading response that is itself built from submission content — not
proven to leak, but a provider/SDK exception whose `__str__` echoes the
response body would.

**The highest-leverage single finding: `send_default_pii=False`
(`AutoGrader/settings.py:152-169`) does not do what its own comment
implies.** It suppresses Sentry's *automatic* user/request context, not
the string content of a log message. `LoggingIntegration(event_level=
"ERROR")` turns every `logger.error`/`.exception` call into a Sentry
event — so leaks #2, #3-4, #6, #7 above ship to Sentry as event message
text regardless of that flag. This means the actual PII exposure surface
today is **Sentry's dashboard**, not just server log files, and no amount
of fixing individual call sites closes that class of risk on its own — the
`before_send` scrubber below is required, not optional hardening.

**Consequence for this plan.** This resolves Open Question #4 with an
answer the architecture doc did not have: BE-A-04 is not "write clean
going forward" but "clean 11 known leaks, close a Sentry gap, and add a
regression guard" as explicit, scoped line items inside Stage 1 — small
in code volume, but they belong in Epic A's own PR sequence (§1), before
or alongside the emitter, not deferred to a "someday" cleanup:

1. Fix the 11 confirmed leaks — log `.id`, never `.get_full_name()`/
   `.first_name`/`.last_name`/`.email`.
2. A CI lint rule (grep-based, matching this codebase's existing flake8/
   pre-commit posture) banning `get_full_name`/`.first_name`/`.last_name`/
   `.email` as a direct argument to `logger.*`/`print` — makes leak #1's
   class of mistake fail CI instead of waiting for the next audit.
3. A **Sentry `before_send` hook** that scrubs known PII patterns
   (email-shaped strings at minimum) from `event["logentry"]["message"]`/
   `event["exception"]` before transmission — defense-in-depth for the gap
   above, since a lint rule only catches what it's told to look for and a
   raw `f"{exc}"` on a future exception is not a static-analysis-catchable
   pattern.

   > **As built (note added 2026-10-05, H-122).** Items 2 and 3 are not
   > what the code has today.
   > - Item 2 is an AST check, not a grep: `scripts/check_no_pii_in_logs.py`
   >   as a pre-commit hook, and the same rule as a test in
   >   `AutoGrader/tests_no_pii_in_logs.py` (H-91). The script's baseline
   >   file `scripts/pii_log_baseline.txt` has had no entry since the
   >   bundle 7 merge-down.
   > - Item 3 was first built as one `before_send` function in
   >   `AutoGrader/sentry_scrubbing.py`. The bundle 7 merge-down
   >   (phase2/epic-a 2919e5aa) replaced it with beta's H-89 module of the
   >   same file name: three hooks, `scrub_event` (passed as `before_send`
   >   and `before_send_transaction`), `scrub_breadcrumb`
   >   (`before_breadcrumb`) and `scrub_log` (`before_send_log`), wired in
   >   `AutoGrader/settings.py`. They remove email addresses and URL
   >   passwords from text and withhold a part they cannot scrub.
   > - H-89 also added a log record factory, `AutoGrader/log_scrubbing.py`,
   >   which does the same for what log handlers print. It is off under
   >   the test runner.
   >
   > The text above and the FR-A-04 row in the test mapping are left as
   > planned. Evidence: `docs/evidence/h89-log-address-scrubber/`,
   > `docs/evidence/h91-ids-only-logs-everywhere/`,
   > `docs/evidence/epic-a-merge-down-b7/`.
4. A policy decision, not just a fix, for `exc_info=e`/`str(e)` on broad
   excepts in the grading pipeline specifically (`ai_processor/services.py`)
   — this is where the systemic pattern is most likely to reintroduce a
   leak after #1-3 land, and it needs an explicit rule (e.g. "never log a
   raw provider exception body; log its type and a truncated, PII-swept
   summary") rather than case-by-case judgment at each of the 15+ sites.
   **Recorded 2026-09-22 as `docs/decisions/AI_PROCESSOR_EXCEPTION_LOGGING_POLICY.md`**
   (privacy-guard) — a separate doc rather than an edit here, deliberately,
   since this plan file is untracked/main-checkout-only and multiple
   sessions treat it as shared reference material.

**Also found during the build (2026-09-22, privacy-guard): ~95 more
pre-existing instances of the same leaky-logging pattern**, all school-side
actors (teacher/admin/subscription-owner, never students), across
`billing/access_control.py`, `license_service.py`, `services.py`,
`stripe_service.py`, `tasks.py`, `views.py`, `qa_time_travel.py`,
`management/commands/backfill.py`, `users/signals.py`. Verified as true
positives. Out of this cleanup's scope (not student PII, so not a BE-A-04
violation) but real follow-up work — tracked as
[H-23 in the hardening backlog](../../HARDENING_BACKLOG.md), grandfathered
into `scripts/pii_log_baseline.txt` so the new CI lint rule (item 2 above)
catches only *new* instances going forward rather than failing on all 95
existing ones at once.

### 0.6 Build-time correction (2026-09-23, during implementation): `record()` alone is an incomplete chokepoint for credit transactions — `bulk_create()` must be instrumented too

Found by privacy-guard while implementing §6's credit-transaction
instrumentation, confirmed independently by senior-manager before
proceeding. §6's table above states `CreditLedger.record()` /
`CreditUsageLog.record()` are "true chokepoints... called from ~18 sites
across `billing/services.py`" — true for those 18 sites (all
GRANT/EXPIRE/REFUND/PURCHASE/PLAN_CHANGE/DISPUTE_REVERSAL, confirmed by
grepping `ledger_type=`), but two real paths bypass `record()` entirely:

1. `CreditWallet.consume_credits()` (`billing/models.py:864-1047`) —
   the **only** place `CreditLedgerType.CONSUME` is ever written, via
   `CreditLedger.build()` + `QuerySet.bulk_create()`. This is very
   likely the highest-volume ledger type (every grading/AI-feature
   credit spend), and instrumenting only `record()` would have silently
   produced zero `CREDIT_TRANSACTION` events for it — a coverage gap
   that would not show up in any test written only against `record()`.
2. The batch-refund path in `billing/services.py`'s `refund_credits`
   (~line 1389) — builds a list of REFUND ledger rows and
   `bulk_create()`s them, same bypass.

Django's `bulk_create()` never routes through `Model.save()` or emits
`post_save`/`pre_save` signals — this is already documented in
`billing/models.py`'s own comment on the consume path ("bulk_create
never emits post_save") and is why `_record_license_consumption()` is
called explicitly there rather than via a signal. The same fact applies
to any audit instrumentation placed only inside `record()`.

**Resolution, adopted as the correct design:** instrument **two** places,
not one — `CreditLedger.record()` (covers the 18 `services.py` sites)
and `AppendOnlyQuerySet.bulk_create()` in `billing/immutable.py` (an
already-existing override, shared by both `CreditLedger` and
`CreditUsageLog`, that both bypass paths above already go through for
their append-only enforcement) — emitting one `CREDIT_TRANSACTION` event
per `CreditLedger` row actually written in either case. The paired
`CreditUsageLog` row written alongside a `CONSUME` ledger row in
`consume_credits()` deliberately does **not** get its own emission: it
is the same economic event as its ledger row, not a second transaction,
and the `CREDIT_TRANSACTION` metadata allow-list (`audit/metadata.py`)
is already ledger-shaped (`credits`, `ledger_type`, ...) with no
usage-log-specific keys. §6's table above and its design-refinement
paragraph are superseded by this for the credit-transaction row only —
every other category's chokepoint claim in that table is unaffected.

### 0.5 X-4 and X-5 (architecture doc's own flagged contradictions): adopted as written

Both proposed resolutions in `03_architecture.md` are sound and are adopted
without change:

- **X-4** — `source_ip`/`user_agent` are never populated for `STUDENT`
  actors; for every other actor they're populated and then nulled by the
  retention sweep after `PII_SHORT_RETENTION_DAYS` (90), while the event
  row itself survives its full retention class.
- **X-5** — `trace_id` is server-generated at audit-write time and never
  taken from the client-supplied `X-Request-ID`. The inbound value is
  stored separately as `client_correlation_id`, explicitly untrusted.

---

## 1. Build order inside Epic A

Matches `03_architecture.md`'s own stated order (event schema frozen first,
because every later call site depends on it), expanded into concrete steps:

1. New `audit` app + `AuditEvent` model + migration (§2)
2. Error taxonomy + reason-code catalogue as code constants (§3)
3. The emitter service (§4)
4. Correlation propagation into the model-call layer (§5)
5. Call-site instrumentation, one action category at a time (§6)
6. Retention sweep (§7)
7. Query API — Super Admin + School Admin (§8)
8. Metrics and alerting (§9)
9. Full acceptance-criteria test pass (§10)

Steps 1–4 are a hard sequence (each depends on the last). Steps 5's
sub-items (the individual action categories) can be built and reviewed as
independent, parallelizable PRs once step 4 lands, since they only ever
*call* the frozen emitter API — this is the natural split point for a
second engineer/session to pick up call sites while the foundation is being
reviewed.

---

## 2. The `AuditEvent` model

### 2.1 New Django app

`python manage.py startapp audit`, added to `INSTALLED_APPS`. A dedicated
app rather than bolting onto `dashboard` or `billing`: every other epic
depends on this one (per `03_architecture.md`'s layer-0 diagram), so it
must have no reverse dependency on any feature app. It may import from
`users` (for `UserTypes`) but nothing may import audit-specific models into
`users`.

### 2.2 Model — exact fields, per `03a_data_model.md` §2.1, with §0.3's `school_id` correction

```python
# audit/models.py
import uuid

from django.db import models

from billing.immutable import AppendOnlyModel


class ActorRole(models.TextChoices):
    STUDENT = "STUDENT", "Student"
    TEACHER = "TEACHER", "Teacher"
    SCHOOL_ADMIN = "SCHOOL_ADMIN", "School Admin"
    SUPER_ADMIN = "SUPER_ADMIN", "Super Admin"
    SYSTEM = "SYSTEM", "System"


class AuditOutcome(models.TextChoices):
    SUCCESS = "SUCCESS", "Success"
    FAILURE = "FAILURE", "Failure"
    DENIED = "DENIED", "Denied"


class AuditErrorClass(models.TextChoices):
    """FR-A-05's fixed taxonomy. Five classes, no sixth without a spec change."""
    USER = "USER", "User error"
    VALIDATION = "VALIDATION", "Validation error"
    PROVIDER = "PROVIDER", "Provider error"
    MODEL = "MODEL", "Model error"
    SYSTEM = "SYSTEM", "System error"


class AuditRetentionClass(models.TextChoices):
    GENERAL = "GENERAL", "General (12 months)"
    STUDENT_RECORD = "STUDENT_RECORD", "Student record (3 years)"


class AuditEvent(AppendOnlyModel):
    """
    One row per meaningful action (FR-A-01). Append-only — see
    billing.immutable, whose AppendOnlyModel/AppendOnlyQuerySet this reuses
    verbatim rather than re-implementing.

    NO FOREIGN KEYS. Every identifying column is a plain value captured at
    write time, following the CreditLedger precedent (billing/models.py) —
    an audit trail a deletion can erase is not an audit trail. This is
    enforced by convention here (there is nothing to constrain at the DB
    level about NOT having an FK); code review is the guard.
    """

    mutable_fields = frozenset()  # every field is frozen after creation

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)

    actor_id = models.UUIDField(null=True, blank=True, db_index=True)
    actor_role = models.CharField(max_length=20, choices=ActorRole.choices, db_index=True)
    actor_email = models.CharField(max_length=254, null=True, blank=True)

    school_id = models.UUIDField(null=True, blank=True, db_index=True)  # §0.3
    department_id = models.UUIDField(null=True, blank=True, db_index=True)

    action = models.CharField(max_length=64, db_index=True)  # AuditAction enum value
    target_type = models.CharField(max_length=64, db_index=True)
    target_id = models.UUIDField(null=True, blank=True, db_index=True)

    outcome = models.CharField(max_length=16, choices=AuditOutcome.choices, db_index=True)
    error_class = models.CharField(
        max_length=16, choices=AuditErrorClass.choices, null=True, blank=True
    )
    reason_code = models.CharField(max_length=64, null=True, blank=True, db_index=True)

    trace_id = models.UUIDField(db_index=True)  # server-generated; see X-5
    client_correlation_id = models.CharField(max_length=64, null=True, blank=True)

    source_ip = models.GenericIPAddressField(null=True, blank=True)  # X-4
    user_agent = models.CharField(max_length=512, null=True, blank=True)  # X-4

    retention_class = models.CharField(
        max_length=16, choices=AuditRetentionClass.choices, db_index=True
    )

    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    metadata = models.JSONField(default=dict)

    class Meta:
        indexes = [
            models.Index(fields=["school_id", "-occurred_at"], name="audit_school_time_idx"),
            models.Index(fields=["actor_id", "-occurred_at"], name="audit_actor_time_idx"),
            models.Index(fields=["action", "-occurred_at"], name="audit_action_time_idx"),
            models.Index(
                fields=["reason_code", "-occurred_at"],
                name="audit_reason_time_idx",
                condition=models.Q(reason_code__isnull=False),
            ),
            models.Index(fields=["retention_class", "occurred_at"], name="audit_retention_idx"),
        ]

    def __str__(self):
        return f"{self.action} · {self.outcome} · {self.occurred_at:%Y-%m-%d %H:%M}"
```

`register_append_only_guards(AuditEvent)` is called from `audit/apps.py`'s
`ready()`, matching how `billing/apps.py` registers `CreditLedger` and
`CreditUsageLog` today (confirm the exact call site there and mirror it
exactly, rather than re-deriving the wiring).

### 2.3 Migration

One additive migration, no data to backfill (this table is genuinely
greenfield — confirmed, §0.1's confirmation extends the same "no legacy
rows" finding). No expand-contract steps needed for the initial create.

### 2.4 Test — model layer

- Append-only enforcement: attempting `.save()` on an existing row, or
  `.delete()`, raises `ImmutableRecordError` (mirrors the existing
  `billing/tests/test_immutable.py` pattern — reuse its structure against
  `AuditEvent`).
- Every index exists (a `connection.introspection` check, matching however
  `CreditLedger`'s indexes are already verified, if they are).
- No `ForeignKey` field exists on the model (a reflection-based test that
  fails the build if anyone adds one later — the "no FK" rule is a design
  invariant, so it gets a test, not just a docstring).

---

## 3. Error taxonomy and reason codes — code constants

Per `03_architecture.md` §9.10: reason codes are a shared frontend contract
(FE-GL-08) reviewed like code, not a database table someone could edit
without review. Same treatment for the five-class taxonomy.

### 3.1 `audit/taxonomy.py`

```python
class ErrorClass(models.TextChoices):  # mirrors AuditErrorClass; single source
    ...

class ReasonCode(models.TextChoices):
    # FR-A-06's minimum set, verbatim:
    MISSING_STUDENT_NAME = "MISSING_STUDENT_NAME", "Missing or unmatched student name"
    STUDENT_NOT_ON_ROSTER = "STUDENT_NOT_ON_ROSTER", "Student not on roster"
    FILE_UNREADABLE = "FILE_UNREADABLE", "Unreadable or corrupt file"
    FILE_TYPE_UNSUPPORTED = "FILE_TYPE_UNSUPPORTED", "Unsupported file type"
    FILE_TOO_LARGE = "FILE_TOO_LARGE", "File too large / page limit exceeded"
    SUBMISSION_EMPTY = "SUBMISSION_EMPTY", "Empty submission"
    RUBRIC_MISSING = "RUBRIC_MISSING", "Missing rubric"
    DUPLICATE_SUBMISSION = "DUPLICATE_SUBMISSION", "Duplicate submission for one student"
    PROVIDER_FAILURE = "PROVIDER_FAILURE", "Model or provider failure"
    INSUFFICIENT_CREDITS_MID_BATCH = "INSUFFICIENT_CREDITS_MID_BATCH", "Insufficient credits mid-batch"

REASON_CODE_MESSAGE = {...}     # display message per code
REASON_CODE_REMEDIATION = {...} # remediation hint per code
```

Each entry carries: stable identifier (the enum value), display message,
remediation hint — exactly FR-A-06's three-part requirement. `03_architecture.md`
Part VIII #7 flags an open question: does the QA document's reason-code
catalogue match this minimum set exactly, or does QA have more? **Action
item, not blocking**: diff this list against the QA document's catalogue
(QA-ERR-01) before Epic A's call-site work reaches the categories that use
these codes (grading, uploads) — QA owns the catalogue per the architecture
doc, so any addition needs to come from there, not be invented here.

### 3.2 `audit/actions.py` — the fixed action vocabulary

```python
class AuditAction(models.TextChoices):
    # Authentication
    LOGIN_SUCCESS = "LOGIN_SUCCESS", ...
    LOGIN_FAILURE = "LOGIN_FAILURE", ...
    LOGOUT = "LOGOUT", ...
    # Grading
    GRADING_REQUESTED = "GRADING_REQUESTED", ...
    GRADING_COMPLETED = "GRADING_COMPLETED", ...
    GRADING_FAILED = "GRADING_FAILED", ...
    # Assignment
    ASSIGNMENT_CREATE = "ASSIGNMENT_CREATE", ...
    ASSIGNMENT_UPDATE = "ASSIGNMENT_UPDATE", ...
    ASSIGNMENT_DELETE = "ASSIGNMENT_DELETE", ...
    ASSIGNMENT_COPY = "ASSIGNMENT_COPY", ...
    # Roster / submission / credit (exist today)
    ROSTER_CHANGE = "ROSTER_CHANGE", ...
    SUBMISSION_UPLOAD = "SUBMISSION_UPLOAD", ...
    CREDIT_TRANSACTION = "CREDIT_TRANSACTION", ...
    # Admin / export / permission (exist today)
    ADMIN_ACTION = "ADMIN_ACTION", ...
    DATA_EXPORT = "DATA_EXPORT", ...
    PERMISSION_CHANGE = "PERMISSION_CHANGE", ...
    # Future-epic actions, defined now so the enum doesn't grow ad hoc later,
    # but with NO call sites until the owning epic exists:
    LESSON_CREATE = "LESSON_CREATE", ...          # Epic D
    LESSON_UPDATE = "LESSON_UPDATE", ...           # Epic D
    LESSON_DELETE = "LESSON_DELETE", ...           # Epic D
    TAG_CREATE = "TAG_CREATE", ...                 # Epic C
    TAG_RENAME = "TAG_RENAME", ...                 # Epic C
    TAG_DELETE = "TAG_DELETE", ...                 # Epic C
    DEPARTMENT_CREATE = "DEPARTMENT_CREATE", ...   # Epic F
    DEPARTMENT_UPDATE = "DEPARTMENT_UPDATE", ...   # Epic F
    DEPARTMENT_MEMBER_ADD = "DEPARTMENT_MEMBER_ADD", ...     # Epic F
    DEPARTMENT_MEMBER_REMOVE = "DEPARTMENT_MEMBER_REMOVE", ...  # Epic F
    LIBRARY_ADD = "LIBRARY_ADD", ...               # Epic F
    LIBRARY_EDIT = "LIBRARY_EDIT", ...             # Epic F
    LIBRARY_COPY = "LIBRARY_COPY", ...             # Epic F
```

Each action is also mapped, in the same module, to its `retention_class`
(§0.3's resolved classification rule, made explicit rather than left to
each call site to decide):

```python
STUDENT_RECORD_ACTIONS = frozenset({
    AuditAction.GRADING_REQUESTED, AuditAction.GRADING_COMPLETED, AuditAction.GRADING_FAILED,
    AuditAction.SUBMISSION_UPLOAD, AuditAction.ROSTER_CHANGE,
    # + any action where actor_role == STUDENT, regardless of the action itself
})

def retention_class_for(action: AuditAction, actor_role: ActorRole) -> AuditRetentionClass:
    if actor_role == ActorRole.STUDENT or action in STUDENT_RECORD_ACTIONS:
        return AuditRetentionClass.STUDENT_RECORD
    return AuditRetentionClass.GENERAL
```

This resolves an ambiguity neither `01a_requirements_specification.md` nor
`03a_data_model.md` states explicitly: "events touching student records"
(FR-A-08) is a per-action classification, computed once here, not decided
ad hoc at each of the ~15 call sites — the same reasoning the architecture
doc itself applies to reason codes (one authoritative list, not
per-call-site judgment calls).

### 3.3 Test

- Every `AuditAction` value maps to a `retention_class` (no action falls
  through to a default silently).
- Every `ReasonCode` has both a message and a remediation hint (a
  completeness test over the dict, so adding a code without both fails
  CI).

---

## 4. The emitter service

### 4.1 `audit/emit.py`

```python
def emit_audit_event(
    *,
    actor,                    # CustomUser | None (None => actor_role must be SYSTEM)
    action: AuditAction,
    target_type: str,
    target_id=None,
    outcome: AuditOutcome,
    error_class: AuditErrorClass | None = None,
    reason_code: ReasonCode | None = None,
    school_id=None,           # explicit override; else derived from actor
    department_id=None,
    before=None,
    after=None,
    metadata: dict | None = None,
    request=None,             # optional: source of source_ip/user_agent/client id
) -> None:
    ...
```

**Design, in the order FR-A-11 demands:**

1. Resolve `actor_role`, `actor_id`, `actor_email`, `school_id` from
   `actor` (or `SYSTEM` if `actor is None`) — **before** anything that can
   fail, so the retention/PII rules below always have the values they
   need.
2. Validate the call (§0.4's "schema validation" surface): `action`,
   `target_type`, `outcome` are required; `metadata` keys are checked
   against a per-action allow-list (§4.2). **This raises** — a
   `TypeError`/`ValueError` a test or CI run catches, not something
   swallowed. This is the ONLY part of the emitter allowed to raise.
3. Resolve `trace_id` from the current `request_context.get_request_id()`
   parsed as a UUID if valid, else a freshly generated one (X-5: this is
   the server-authoritative id; the caller's `request_id` value is never
   assumed to be a well-formed UUID just because it passed
   `is_valid_request_id()`, which allows a broader charset).
4. Populate `client_correlation_id` from the inbound `X-Request-ID` header
   if `request` is provided.
5. Populate `source_ip`/`user_agent` **only if `actor_role != STUDENT`**
   (X-4) and `request` is provided.
6. Resolve `retention_class` via `retention_class_for(action, actor_role)`.
7. **Everything from here down is wrapped in a bare `try/except Exception`.**
   On any failure (DB unreachable, unexpected error): log at `ERROR` with
   the full context (action, actor_id, trace_id — never metadata, which
   may carry the same PII rules its caller is trying to avoid logging
   raw), increment the `audit_emit_failures_total` metric (§9), and
   **return** — never raise past this point. This is the literal
   implementation of FR-A-11's "queued or dropped with an operational
   alert, never a 500 to the user."

**Explicitly NOT step 7: queueing.** The architecture doc's phrasing
("queued or dropped") leaves both options open. Given the write is a
single-row insert with no external I/O beyond Postgres, and the same
Postgres instance already backs the action the event describes, a queued
retry adds a Celery task, another failure mode, and another correlation-id
hop for marginal benefit over "log the drop loudly and alert." Recommend
**drop with alert**, not queue-and-retry, unless the metric shows this
firing often enough in practice to justify the extra moving part — cheaper
to add a retry queue later than to remove one that turned out to be dead
weight.

### 4.2 Metadata allow-list

Per action, a frozenset of permitted `metadata` keys, checked at step 2.
This is what makes "bounded, PII-free context ... enforced by the
emitter's allow-list, not by convention" (data model §2.1) actually true
rather than aspirational — an unlisted key is a `ValueError` at the call
site, caught by whichever test exercises that call site, not a silent
extra field that might be a student's name six months from now when
someone adds a debugging field carelessly.

```python
METADATA_ALLOWLIST = {
    AuditAction.GRADING_FAILED: frozenset({"assignment_id", "attempt_number", "provider"}),
    AuditAction.SUBMISSION_UPLOAD: frozenset({"assignment_id", "file_type", "file_size_bytes"}),
    ...
}
```

### 4.3 Test — emitter layer

- A required-field omission raises (not swallowed) — covers §0.4's split.
- An out-of-allow-list metadata key raises.
- With `AuditEvent.objects.create` patched to raise (simulating the store
  being down), `emit_audit_event()` returns normally and the caller's own
  operation (a plain view call wrapping it) still returns 200 — this is
  FR-A-11's acceptance criterion, built as a real test rather than
  asserted by inspection. Mirrors the existing pattern used elsewhere in
  this codebase for failure-injection tests (see `django-cache-object-is-
  per-thread` class of test in `billing/tests/`, which patches the CLASS
  not an instance, for the same reason — this must reach whatever thread
  the emit call runs on).
- A `STUDENT` actor never gets `source_ip`/`user_agent` populated, over a
  table of every action, not just one.
- The metric increments on a forced failure (§9 depends on this).

---

## 5. Correlation into the model-call layer

The one real gap left by §0.1's finding. `ai_processor/services.py`'s
`__ai_model` (the name-mangled, single chokepoint confirmed by
`03_architecture.md` §2.2) gets the current `request_id` attached to the
outbound call — as a header/metadata field on the provider request where
the provider SDK supports it (verify per-provider; at minimum, log it
alongside the call so a `trace_id` search finds the provider round-trip in
the logs even where the provider itself can't be tagged). This closes
FR-A-03's "any model call" clause without touching the chokepoint's privacy
boundary (§5.3/BE-B-01) — it's an additional header/log field, not a new
call path.

**Test:** a grading call made under a known `request_id` produces a log
line for the model call carrying that same id (string match, not a mock
assertion — this is exactly the kind of claim `verify-before-relaying-a-
gate-closed` says needs the real check, not a relayed assertion).

---

## 6. Call-site instrumentation

**Confirmed by worker 88's read-only survey (beta @ b441a29).** Every
category FR-A-01 lists, against the actual codebase, with exact call
sites:

| Category | Current call site(s) | Notes |
|---|---|---|
| Auth success | `users/serializers.py:417` (`CustomTokenObtainPairSerializer.validate`, success path), `:424` (`reset_login_lockout()`) | Single chokepoint — every login goes through this serializer |
| Auth failure | `users/serializers.py:415` (locked-account branch), `:420-423` (`register_failed_login()` + re-raise) | Two distinct failure branches (locked vs. wrong password) — need different reason codes |
| Sign-out | `users/views.py:1059-1080` (`AuthViewSet.logout`) | Single endpoint |
| Grading requested | `students/views.py:801-817` (`grade_async`, via `create_processing_task` + `launch_processing_task`); `assignments/views.py:~1579-1600` (batch grading) | `create_processing_task` (`students/task_tracking.py:39`) is a shared factory called from ~11 view sites for *other* task types too — filter by `task_type`, don't instrument the factory itself |
| Grading completed | `assignments/tasks.py:489` (`mark_processing_task_success`, inside `grade_engine_async`) | Same chokepoint function (`students/task_tracking.py:215`) also used at `:556`, `:979` for extraction/formatting — action must derive from `task.task_type`, not call site |
| Grading failed | `assignments/tasks.py:577` (`mark_processing_task_failure`, except block of `grade_engine_async`) | Chokepoint `students/task_tracking.py:225`, ~15 call sites total across non-grading task types |
| Assignment create | `assignments/views.py:362` (`create`), `:432` (`create_async`) | Two paths — sync form and async upload-derived |
| Assignment update | `assignments/views.py:518` (`partial_update`), `:602` (`update_async`) | — |
| Assignment delete | **No override exists.** `AssignmentViewSet` uses DRF's default `destroy()`/`perform_destroy()` | Needs a `perform_destroy` override added as part of this instrumentation — doesn't exist today |
| Assignment copy | **Does not exist anywhere in the codebase** (confirmed by grep) | FR-E-01/Epic E builds this from scratch — design the emitter contract now so Epic E's copy endpoint calls it on day one, no retrofit needed |
| Lesson create/update/delete | **Epic D doesn't exist** | Future — action enum stays dormant (§3.2) |
| Tag create/rename/delete | **Epic C's target shape doesn't exist.** Today's `Topic` (`classrooms/models.py:154-176`, course-scoped, single FK) is a different, narrower model being migrated away (§0 of `03_architecture.md` §2.3) | Future. Not worth an interim emit on `Topic` given it's being replaced, not extended |
| Roster change | `classrooms/views.py:1388` (`bulk_add_students`), `:1441` (`remove_student`) | No single-student-add endpoint exists, only bulk-add + remove |
| Submission upload | `students/views.py:483` (`upload`), `:567` (`upload-async`), `:1189` (`batch-upload`) | Three paths |
| Credit transaction | **`billing/models.py:1417` `CreditLedger.record()` and `:1576` `CreditUsageLog.record()`** — true chokepoints, called from ~18 sites across `billing/services.py` | Instrument inside `record()` itself, once, not at 18 call sites — see the design note below |
| Department create/update/membership change | **Epic F doesn't exist** (confirmed "100% unimplemented" independently by both the architecture doc and this survey) | Future |
| Library add/edit/copy | **Epic F/G scoped, doesn't exist** | Future |
| Admin action | Broad, no shared base — ~18 `IsSuperAdmin`-gated endpoints across `billing/views.py`, `billing/views_admin_credits.py:122`, `billing/license_overage_offline_views.py:75`, `billing/license_views.py:197`, `billing/qa_time_travel.py:963`, `dashboard/views.py:230`, `users/views.py:247/530/2222/2273` | Per-endpoint instrumentation doesn't scale at this count — see design note below |
| Data export | **No existing endpoint** (grepped `def export`, `url_path.*export` repo-wide — nothing) | Future. NFR-CMP-04 (export/deletion) isn't built either — same gap |
| Permission change | **No existing mutation.** `user_type=` is only ever set at account creation, never changed on an existing row | Future — matters once FR-F-07's per-member flag or a role-change tool ships |

**Design refinement from the survey (adopted): emit from chokepoints, not
scattered call sites, wherever a chokepoint already exists.** Grading
outcomes and every credit transaction already funnel through exactly one
function each (`mark_processing_task_success/failure`, `CreditLedger.record`/
`CreditUsageLog.record`) instead of their many callers. The emitter call
for these categories belongs **inside those shared functions**, with the
specific `AuditAction` resolved from `task.task_type` / the ledger's
`reason` field — one instrumented site instead of 15–18. This changes §4's
emitter signature slightly: it must accept an already-resolved
`AuditAction` (never infer it from the call stack), and the chokepoint
functions need a small internal mapping (`task_type` → `AuditAction`,
`CreditLedgerType`/usage reason → `AuditAction`) rather than each of their
many callers passing one in.

**Admin actions need the same treatment, differently:** ~18 scattered
`IsSuperAdmin` endpoints with no shared base class is the wrong shape for
per-endpoint instrumentation (it will be forgotten on the 19th endpoint).
Recommend a **DRF view-mixin or a decorator applied at the `IsSuperAdmin`
permission-class level** that emits `ADMIN_ACTION` automatically around
any view carrying that permission class, with the view supplying only
`target_type`/`target_id` via a small convention (e.g. a
`get_audit_target()` method), rather than a hand-written emit call added to
each of the 18 (and future) admin endpoints individually.

**Instrumentation rule for the remaining, genuinely per-view categories**
(auth, sign-out, assignment CRUD, roster change, submission upload): the
emitter call sits at the point that already knows the outcome — after
`serializer.save()` succeeds, or in the `except` block that already
catches the specific failure. Each becomes its own small, independently
reviewable PR once the emitter (§4) is merged — the parallelizable half of
the epic mentioned in §1.

**Test per site:** the acceptance criterion for FR-A-01 is literal —
"produces exactly one well-formed event." Each instrumented call site gets
a test that performs the action once and asserts `AuditEvent.objects.count()
== 1` immediately before/after (not `>= 1`), catching both a missing call
and an accidental double-emit (e.g. a retry path calling it twice).

---

## 7. Retention sweep

### 7.1 `audit/tasks.py`

```python
@shared_task
def sweep_audit_retention():
    now = timezone.now()
    general_cutoff = now - timedelta(days=365)
    student_cutoff = now - timedelta(days=365 * 3)

    with allow_unsafe_mutation():  # billing.immutable — the sanctioned escape hatch
        deleted_general, _ = AuditEvent.objects.filter(
            retention_class=AuditRetentionClass.GENERAL, occurred_at__lt=general_cutoff
        ).delete()
        deleted_student, _ = AuditEvent.objects.filter(
            retention_class=AuditRetentionClass.STUDENT_RECORD, occurred_at__lt=student_cutoff
        ).delete()

    return f"deleted general={deleted_general} student_record={deleted_student}"


@shared_task
def sweep_audit_pii_short_retention():
    """X-4: null source_ip/user_agent after PII_SHORT_RETENTION_DAYS (90),
    independent of the row's own retention_class — the row survives, the
    IP/UA do not."""
    cutoff = timezone.now() - timedelta(days=90)
    with allow_unsafe_mutation():
        AuditEvent.objects.filter(
            occurred_at__lt=cutoff
        ).exclude(
            source_ip__isnull=True, user_agent__isnull=True
        ).update(source_ip=None, user_agent=None)
```

Two separate tasks, not one: the row-delete sweep and the IP/UA-null sweep
run on different clocks (365d/1095d vs. 90d) and should be independently
observable/retryable in Beat — bundling them loses that.

### 7.2 `CELERY_BEAT_SCHEDULE` entries

```python
"sweep-audit-retention-daily": {
    "task": "audit.tasks.sweep_audit_retention",
    "schedule": crontab(minute=0, hour=6),  # after the existing 05:00 credit-bucket sweep
},
"sweep-audit-pii-short-retention-daily": {
    "task": "audit.tasks.sweep_audit_pii_short_retention",
    "schedule": crontab(minute=30, hour=6),
},
```

Add both to `BEAT_HEALTH_EXPECTATIONS` (`AutoGrader/beat_health.py`) with
the existing docstring convention — this table already exists and taking
part in it is one line, not new infrastructure.

### 7.3 `allow_unsafe_mutation()` scope note

`billing/immutable.py`'s docstring currently says this context manager is
intended for "test setup" and "a supervised data-repair session." The
retention sweep is a **third, legitimate, unsupervised** caller. Update
that docstring when this lands — not a functional change, but leaving the
comment saying "two callers" when there are three is exactly the kind of
drift this codebase's own conventions (see the `git commit` style
throughout) treat as worth fixing in the same PR, not left for someone
later to notice.

### 7.4 Test

- Seed rows at `cutoff - 1 day` and `cutoff + 1 day` for each retention
  class (via `allow_unsafe_mutation()` to back-date `occurred_at`, same
  pattern `CreditLedger` tests already use); run the sweep; assert exactly
  the expected side is gone.
- Run the sweep twice in a row; assert the second run's return value
  reports zero deletions (idempotent-under-duplicate-Beat, made concrete).
- Run both sweeps concurrently in two threads against overlapping rows
  (matches this codebase's established pattern for "prove it under real
  concurrency, not simulated" — see `postgres-connection-cap-limits-
  parallel-tests` and the barrier-synchronised tests already in
  `billing/tests/`); assert no row is double-processed and no exception
  escapes either thread.

---

## 8. Query API

### 8.1 Endpoints

- `GET /api/v1/super-admin/audit/events` — unrestricted, filterable by
  `actor_id`, `actor_role`, `action`, `school_id`, `department_id`,
  `time_from`/`time_to`, `outcome`, `reason_code`. `IsSuperAdmin` only.
- `GET /api/v1/school-admin/audit/events` — same filter surface, but the
  queryset is **hard-scoped** to `request.user.school_id` at the queryset
  level (not just the serializer) — this is the FR-A-09 acceptance
  criterion verbatim: "a School Admin query for another License returns an
  empty, well-formed result — not the other License's rows, not an error
  revealing they exist." A `school_id` filter param on this endpoint, if
  present at all, must be **ignored** in favour of the authenticated
  admin's own `school_id`, never merely validated — accepting and
  trusting a client-supplied `school_id` here would be the exact same
  class of mistake X-5 already flagged for `trace_id`.

### 8.2 Pagination and cost

Standard cursor/page pagination (reuse `StandardPageNumberPagination`,
already used throughout `dashboard/views.py`). No aggregate/dashboard-style
endpoint here — this is a raw event log, and every index in §2.2 is chosen
to make the filter combinations above a single index scan, not a full-
table sort.

### 8.3 Test

- `IsSuperAdmin` gate: a non-super-admin gets 403.
- The adversarial case FR-A-09 names explicitly: School Admin A queries
  School Admin B's `school_id` via whatever means the endpoint exposes (a
  param, if kept for symmetry with the super-admin endpoint) — assert the
  result set is empty and scoped to A's own school, not an error and not
  B's rows. This is the same class of test this codebase already runs for
  the H18/H19 tenancy findings referenced in `billing/tests/` — follow
  that pattern, including running it at both the queryset level (unit) and
  the full request/response level (integration), per FR-F-10's own
  "queryset AND serializer level" standard, which applies here too.

---

## 9. Metrics and alerting (BE-A-09/FR-A-10)

Minimum set, each with a documented threshold (the acceptance criterion
requires a *documented* threshold, not just an emitted metric):

| Metric | Emission point | Threshold (proposed, for founder/eng sign-off) |
|---|---|---|
| `grading_failure_rate` | `GRADING_FAILED` emission, rate over `GRADING_COMPLETED + GRADING_FAILED` | > 5% over a rolling 15 min window |
| `model_fallback_rate` | wherever the provider fallback path already lives in `ai_processor/services.py` (locate during call-site work) | > 10% over 15 min |
| `credit_ledger_anomaly` | any ledger row whose running balance goes negative, or whose `EXPIRE`/`REFUND` amount doesn't reconcile — reuse whatever check `cleanup_expired_credit_buckets` or the existing reconciliation tasks already compute | any occurrence — page immediately, this is a P0-class signal per this codebase's existing billing hardening posture |
| `reason_code_rate{code=...}` | every `emit_audit_event()` call carrying a `reason_code` | code-specific; flag any single code exceeding 3x its own 7-day trailing average |
| `audit_emit_failures_total` | §4.1 step 7's except block | > 0 sustained for 5 min — FR-A-11's failure path should be rare, so any sustained rate is itself an incident |

**Implementation:** this codebase doesn't appear to have an existing
metrics/StatsD/Prometheus emission layer visible in the files read so
far — **must verify** before committing to an emission mechanism (a
`statsd.incr()` call, a Sentry custom metric, or a periodic aggregation
query feeding an existing alerting channel). Flagging as an open item
rather than guessing a library that isn't there.

---

## 10. Test plan — mapped 1:1 to acceptance criteria

| Requirement | Test (§ in this doc) |
|---|---|
| FR-A-01 | §6, per-site "exactly one event" test |
| FR-A-02 | §4.3, required-field + allow-list rejection tests |
| FR-A-03 | §5 test (log line carries `trace_id` through to the model call); §0.1's Celery-boundary claim already covered by existing infra tests (verify they exist; write one if not) |
| FR-A-04 | §0.5a's 11 fixes + lint rule + Sentry `before_send` scrubber, each with its own test (log-output assertion per fixed site; a lint-rule test using a deliberately-bad fixture line; a `before_send` unit test with a synthetic PII-bearing event); new call sites covered by the metadata allow-list (§4.2) plus a grading-run log inspection test mirroring the acceptance criterion's own wording |
| FR-A-05 | taxonomy completeness test (§3.3) + one induced failure per class (provider outage via mock, validation failure via bad input, model failure via mocked model error, system fault via forced exception) each asserted to land the correct `error_class` |
| FR-A-06 | §3.3 completeness test + one reproduction test per reason code, each asserting its own code (not a shared generic) |
| FR-A-07 | out of Epic A's direct scope per the architecture doc (`GradingBatchItem`, §9 of the data model doc) — Epic A supplies the taxonomy/reason codes this consumes; the batch-level partial-success mechanism itself is Epic B/E territory. Cross-referenced, not owned, here |
| FR-A-08 | §7.4 |
| FR-A-09 | §8.3 |
| FR-A-10 | §9 — pending the metrics-layer "must verify" above |
| FR-A-11 | §4.3's failure-injection test |

---

## 11. Open items carried forward

1. ~~**A6 (retention: 12mo general / 3yr student-record) needs explicit
   founder confirmation before §7 is built.**~~ **CONFIRMED 2026-09-22 by
   the user** — 12 months general / 3 years student-record retention is
   approved as written. §7 is unblocked. The master requirements PDF (`docs/backend/phase 2/
   Phase 2 Part 1 Backend Requirements.docx.pdf`, §6 "Assumptions requiring
   confirmation") lists A6 as an unconfirmed assumption ("Medium — storage
   design" impact if wrong) and BE-A-07 says outright: "Implement and
   enforce retention. See assumption A6; **confirm before building**." The
   distilled `01a_requirements_specification.md` states the 12/3-year
   figures as if settled (FR-A-08's wording has no hedge) — that is the
   distillation dropping a caveat the source document is explicit about.
   Model layer (§2), taxonomy (§3) and emitter (§4) do not depend on the
   exact retention numbers and can proceed; the retention sweep (§7) must
   not be built against 12/3 as fixed until this is confirmed, since a
   changed number after rows exist is a migration with a compliance
   narrative attached — the same cost X-4 already warned about for
   `source_ip`/`user_agent`.
2. **Metrics emission mechanism** (§9) — must verify before Stage 1 is
   called done.
2. **QA reason-code catalogue diff** (§3.1) — before grading/upload call
   sites land, not before the emitter itself.
3. **`Topic`/`Assignment.topic` row counts** and **every `get_queryset()`
   audit** (Part VIII #3, #5 in `03_architecture.md`) — out of Epic A's
   scope, but #5 (the authorisation-matrix baseline) directly benefits from
   §8's School Admin scoping test being written first, since it's the same
   kind of adversarial test the wider audit will need to replicate per
   endpoint.

---

## 12. Coordination note

Worker `grade-automator-plus-88` is running the call-site inventory (§6)
and the PII audit (FR-A-04, item 1 above) in parallel with this document.
Their report fills in §6's table with exact file:line references and
either closes or reshapes item 1 of this open-items list. Do not start
call-site instrumentation PRs before that report lands — the point of
commissioning it was to avoid guessing at call sites that turn out to be
wrong once actually located.
