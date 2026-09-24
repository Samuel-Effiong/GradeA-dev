"""
Retention sweeps for the audit trail (A6, X-4, §7 of the Epic A plan).

Two separate tasks, not one: the row-delete sweep and the IP/UA-null sweep
run on different clocks (365d/1095d vs. PII_SHORT_RETENTION_DAYS) and should
be independently observable/retryable in Beat - bundling them loses that.
"""

from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from billing.immutable import allow_unsafe_mutation

from .enums import RetentionClass
from .models import AuditEvent

# X-4: source_ip/user_agent are blanked after this many days regardless of
# the row's own retention_class - the row survives for its full class, the
# IP/UA do not.
PII_SHORT_RETENTION_DAYS = 90


@shared_task(bind=True, max_retries=0)
def sweep_audit_retention(self):
    """
    Deletes AuditEvent rows past their retention_class's cutoff (A6: 12
    months general, 3 years student-record).

    Deletion is the one case `AuditEvent.mutable_fields` cannot cover (it
    only exempts source_ip/user_agent from the append-only guard), so this
    is the retention sweep's one legitimate, unsupervised use of
    `allow_unsafe_mutation()` - see that function's docstring.

    Returns a summary string consumed by Celery Beat's result backend.
    """
    now = timezone.now()
    general_cutoff = now - timedelta(days=365)
    student_cutoff = now - timedelta(days=365 * 3)

    with allow_unsafe_mutation():
        deleted_general, _ = AuditEvent.objects.filter(
            retention_class=RetentionClass.GENERAL, occurred_at__lt=general_cutoff
        ).delete()
        deleted_student, _ = AuditEvent.objects.filter(
            retention_class=RetentionClass.STUDENT_RECORD,
            occurred_at__lt=student_cutoff,
        ).delete()

    summary = (
        f"Audit retention sweep: deleted general={deleted_general} "
        f"student_record={deleted_student}"
    )
    return summary


@shared_task(bind=True, max_retries=0)
def sweep_audit_pii_short_retention(self):
    """
    X-4: nulls source_ip/user_agent after PII_SHORT_RETENTION_DAYS,
    independent of the row's own retention_class - the row survives, the
    IP/UA do not.

    `source_ip` and `user_agent` are declared in `AuditEvent.mutable_fields`
    precisely so this sweep can run as a plain `.update()` without needing
    `allow_unsafe_mutation()` at all (§0.3b of the Epic A plan) - the
    database-level `audit_student_no_pii_ck` constraint plus the emitter
    already guarantee no STUDENT-actor row ever has these fields populated
    in the first place, so this only ever touches non-student rows.

    Returns a summary string consumed by Celery Beat's result backend.
    """
    cutoff = timezone.now() - timedelta(days=PII_SHORT_RETENTION_DAYS)
    updated = (
        AuditEvent.objects.filter(occurred_at__lt=cutoff)
        .exclude(source_ip__isnull=True, user_agent__isnull=True)
        .update(source_ip=None, user_agent=None)
    )

    summary = f"Audit PII short-retention sweep: nulled {updated} rows"
    return summary
