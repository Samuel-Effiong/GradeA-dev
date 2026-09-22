import uuid

from django.db import models
from django.db.models import Q
from django.utils import timezone

from billing.immutable import AppendOnlyModel

from .enums import ActorRole, AuditOutcome, ErrorClass, RetentionClass


class AuditEvent(AppendOnlyModel):
    """One row per meaningful user or system action (FR-A-01, BE-A-02).

    NO foreign keys, on purpose. Who acted, under which licence and on what is
    captured as a VALUE at write time, so deleting a user, school or object can
    never erase or cascade into the record of what happened - an audit log a
    deletion can erase is not an audit log. `CreditLedger` set the precedent.

    Append-only: a persisted row cannot be edited or deleted through the ORM.
    The single exception is `source_ip` and `user_agent`, which the retention
    sweep blanks after `PII_SHORT_RETENTION_DAYS` while the event itself lives
    on for its full retention class (X-4). Rows past their class are removed
    by that sweep, which is the only caller allowed to lift the guard.

    Rows are written through `audit.emitter.emit()`, never by constructing
    this model at a call site: the emitter enforces the fields, the metadata
    allow-list and the student-data rules that this table alone cannot.
    """

    mutable_fields = frozenset({"source_ip", "user_agent"})

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    occurred_at = models.DateTimeField(default=timezone.now)

    actor_id = models.UUIDField(null=True, blank=True)
    actor_role = models.CharField(max_length=20, choices=ActorRole.choices)
    actor_email = models.CharField(max_length=254, null=True, blank=True)

    license_id = models.UUIDField(null=True, blank=True)
    department_id = models.UUIDField(null=True, blank=True)

    action = models.CharField(max_length=64)
    target_type = models.CharField(max_length=64)
    target_id = models.UUIDField(null=True, blank=True)

    outcome = models.CharField(max_length=16, choices=AuditOutcome.choices)
    error_class = models.CharField(
        max_length=16, choices=ErrorClass.choices, null=True, blank=True
    )
    reason_code = models.CharField(max_length=64, null=True, blank=True)

    # Server-generated and authoritative. The client's own X-Request-ID is kept
    # beside it as untrusted context only (X-5).
    trace_id = models.UUIDField()
    client_correlation_id = models.CharField(max_length=64, null=True, blank=True)

    source_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, null=True, blank=True)

    retention_class = models.CharField(max_length=16, choices=RetentionClass.choices)

    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    metadata = models.JSONField(default=dict)

    class Meta:
        indexes = [
            models.Index(
                fields=["license_id", "-occurred_at"], name="audit_lic_time_ix"
            ),
            models.Index(
                fields=["department_id", "-occurred_at"],
                name="audit_dept_time_ix",
                condition=Q(department_id__isnull=False),
            ),
            models.Index(
                fields=["actor_id", "-occurred_at"], name="audit_actor_time_ix"
            ),
            models.Index(
                fields=["action", "-occurred_at"], name="audit_action_time_ix"
            ),
            models.Index(
                fields=["reason_code", "-occurred_at"],
                name="audit_reason_time_ix",
                condition=Q(reason_code__isnull=False),
            ),
            models.Index(
                fields=["retention_class", "occurred_at"], name="audit_retention_ix"
            ),
            models.Index(fields=["trace_id"], name="audit_trace_ix"),
        ]
        constraints = [
            # A student is an actor whose identity, address and browser are
            # never recorded (data minimisation, X-4). The emitter already
            # leaves them out; this makes the database refuse them too.
            models.CheckConstraint(
                condition=~Q(actor_role="STUDENT")
                | (
                    Q(actor_email__isnull=True)
                    & Q(source_ip__isnull=True)
                    & Q(user_agent__isnull=True)
                ),
                name="audit_student_no_pii_ck",
            ),
        ]

    def __str__(self):
        return f"{self.action} {self.outcome} {self.occurred_at:%Y-%m-%d %H:%M:%S}"
