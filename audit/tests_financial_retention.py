"""Epic B1a step 1: the FINANCIAL retention class (plan 08 §4, §9 item 11).

Credit events are kept 7 years. New rows are tagged FINANCIAL at emit; old
rows tagged GENERAL (or STUDENT_RECORD) before the class existed are kept
too, because the sweep excludes the financial actions by action.
"""

import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from audit.emitter import emit
from audit.enums import AuditAction, RetentionClass
from audit.models import AuditEvent
from audit.tasks import sweep_audit_retention
from audit.tests_schema import event_fields
from audit.volume import RETENTION_DAYS, retention_class_of

FINANCIAL = "FINANCIAL"


def make_event(**over):
    return AuditEvent.objects.create(**event_fields(**over))


def ago(days, hours=0):
    return timezone.now() - timedelta(days=days, hours=hours)


def sweep_metadata():
    return (
        AuditEvent.objects.filter(action=AuditAction.AUDIT_RETENTION_SWEEP)
        .latest("occurred_at")
        .metadata
    )


def credit_emit(**over):
    args = {
        "actor": None,
        "target_type": "CreditLedger",
        "target_id": uuid.uuid4(),
        "strict": True,
    }
    args.update(over)
    return emit(AuditAction.CREDIT_TRANSACTION, **args)


class FinancialClassTests(TestCase):
    """FR-1: the class, its days, and how a credit event is tagged."""

    def test_financial_is_kept_seven_years(self):
        self.assertIn(FINANCIAL, RetentionClass.values)
        self.assertEqual(RETENTION_DAYS[FINANCIAL], 365 * 7)
        self.assertEqual(retention_class_of("CREDIT_TRANSACTION"), FINANCIAL)

    def test_a_new_credit_event_is_stored_financial(self):
        event = credit_emit()
        self.assertEqual(event.retention_class, FINANCIAL)

    def test_a_student_linked_credit_event_is_stored_financial(self):
        # SM ruling 2026-11-06: FINANCIAL wins over STUDENT_RECORD (7y >= 3y).
        event = credit_emit(touches_student_record=True)
        self.assertEqual(event.retention_class, FINANCIAL)

    def test_an_ordinary_event_is_still_general(self):
        event = emit(
            AuditAction.ASSIGNMENT_COPY,
            actor=None,
            target_type="Assignment",
            target_id=uuid.uuid4(),
        )
        self.assertEqual(event.retention_class, RetentionClass.GENERAL)


class FinancialSweepTests(TestCase):
    """FR-2: the sweep keeps every credit event until 7 years, whatever its
    stored class, and records what it deleted."""

    def test_a_financial_row_survives_until_seven_years(self):
        kept = make_event(
            action="CREDIT_TRANSACTION",
            retention_class=FINANCIAL,
            occurred_at=ago(365 * 7 - 1),
        )
        gone = make_event(
            action="CREDIT_TRANSACTION",
            retention_class=FINANCIAL,
            occurred_at=ago(365 * 7, hours=1),
        )
        summary = sweep_audit_retention()
        self.assertTrue(AuditEvent.objects.filter(pk=kept.pk).exists())
        self.assertFalse(AuditEvent.objects.filter(pk=gone.pk).exists())
        self.assertIn("financial=1", summary)
        self.assertEqual(sweep_metadata()["deleted_financial"], 1)

    def test_a_legacy_general_credit_row_survives_the_general_cutoff(self):
        legacy = make_event(
            action="CREDIT_TRANSACTION",
            retention_class=RetentionClass.GENERAL,
            occurred_at=ago(400),
        )
        ordinary = make_event(
            action="ASSIGNMENT_COPY",
            retention_class=RetentionClass.GENERAL,
            occurred_at=ago(400),
        )
        sweep_audit_retention()
        self.assertTrue(AuditEvent.objects.filter(pk=legacy.pk).exists())
        self.assertFalse(AuditEvent.objects.filter(pk=ordinary.pk).exists())

    def test_a_legacy_general_credit_row_goes_at_seven_years(self):
        legacy = make_event(
            action="CREDIT_TRANSACTION",
            retention_class=RetentionClass.GENERAL,
            occurred_at=ago(365 * 7, hours=1),
        )
        sweep_audit_retention()
        self.assertFalse(AuditEvent.objects.filter(pk=legacy.pk).exists())
        self.assertEqual(sweep_metadata()["deleted_financial"], 1)

    def test_a_student_record_credit_row_survives_the_three_year_cutoff(self):
        kept = make_event(
            action="CREDIT_TRANSACTION",
            retention_class=RetentionClass.STUDENT_RECORD,
            occurred_at=ago(365 * 4),
        )
        sweep_audit_retention()
        self.assertTrue(AuditEvent.objects.filter(pk=kept.pk).exists())
