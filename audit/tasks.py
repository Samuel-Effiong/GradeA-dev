"""
Retention sweeps for the audit trail (A6, X-4, §7 of the Epic A plan).

Two separate tasks, not one: the row-delete sweep and the IP/UA-null sweep
run on different clocks (365d/1095d vs. PII_SHORT_RETENTION_DAYS) and should
be independently observable/retryable in Beat - bundling them loses that.
"""

from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from AutoGrader import beat_locks
from AutoGrader.beat_locks import single_instance
from billing.immutable import allow_unsafe_mutation

from .emitter import emit
from .enums import FINANCIAL_ACTIONS, AuditAction, RetentionClass
from .models import AuditEvent

# X-4: source_ip/user_agent are blanked after this many days regardless of
# the row's own retention_class - the row survives for its full class, the
# IP/UA do not.
PII_SHORT_RETENTION_DAYS = 90


@shared_task(bind=True, max_retries=0)
@single_instance(max_hold=beat_locks.DAILY)
def sweep_audit_retention(self):
    """
    Deletes AuditEvent rows past their retention_class's cutoff (A6: 12
    months general, 3 years student-record, 7 years financial).

    FINANCIAL actions (CREDIT_TRANSACTION and B1's credit/purchase actions)
    are excluded from both the GENERAL and STUDENT_RECORD deletes, so old
    rows tagged GENERAL before the FINANCIAL class existed are kept until
    the 7-year cutoff.

    Deletion is the one case `AuditEvent.mutable_fields` cannot cover (it
    only exempts source_ip/user_agent from the append-only guard), so this
    is the retention sweep's one legitimate, unsupervised use of
    `allow_unsafe_mutation()` - see that function's docstring.

    Returns a summary string consumed by Celery Beat's result backend.
    """
    now = timezone.now()
    general_cutoff = now - timedelta(days=365)
    student_cutoff = now - timedelta(days=365 * 3)
    financial_cutoff = now - timedelta(days=365 * 7)

    with allow_unsafe_mutation():
        deleted_general, _ = (
            AuditEvent.objects.filter(
                retention_class=RetentionClass.GENERAL,
                occurred_at__lt=general_cutoff,
            )
            .exclude(action__in=FINANCIAL_ACTIONS)
            .delete()
        )

        deleted_student, _ = (
            AuditEvent.objects.filter(
                retention_class=RetentionClass.STUDENT_RECORD,
                occurred_at__lt=student_cutoff,
            )
            .exclude(action__in=FINANCIAL_ACTIONS)
            .delete()
        )

        deleted_financial, _ = AuditEvent.objects.filter(
            occurred_at__lt=financial_cutoff,
            action__in=FINANCIAL_ACTIONS,
        ).delete()

    # S3 (G6): the sweep records its own run - counts only, actor SYSTEM -
    # including a run that deleted nothing, so a stopped sweep is a gap.
    emit(
        AuditAction.AUDIT_RETENTION_SWEEP,
        target_type="AuditEvent",
        metadata={
            "deleted_general": deleted_general,
            "deleted_student_record": deleted_student,
            "deleted_financial": deleted_financial,
        },
    )

    summary = (
        f"Audit retention sweep: deleted general={deleted_general} "
        f"student_record={deleted_student} financial={deleted_financial}"
    )
    return summary


@shared_task(bind=True, max_retries=0)
@single_instance(max_hold=beat_locks.DAILY)
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

    # S3 (G6): as above - one self-record per run, zero included.
    emit(
        AuditAction.AUDIT_RETENTION_SWEEP,
        target_type="AuditEvent",
        metadata={"scrubbed": updated},
    )

    summary = f"Audit PII short-retention sweep: nulled {updated} rows"
    return summary
