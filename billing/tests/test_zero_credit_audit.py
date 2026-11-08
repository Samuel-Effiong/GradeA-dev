"""H-299: an audit event written inside a zero-credit scope is stored.

AuditEvent shares billing's append-only guards with the credit tables, and
the guard's zero-credit check treated every unlisted model as a ledger
insert. Inside a scope the audit write was refused, the emitter dropped it
(FR-A-11), and the refusal was reported as a credit-ledger anomaly
(docs/evidence/h299/NOTES.md).

Z-1..Z-3 and Z-5 pin the fix. Z-4 pins what must not move: credit writes are
still refused inside a scope, an unlisted append-only model is still treated
as a credit table, and audit rows are still append-only.
"""

import logging
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.db import transaction
from django.test import TestCase

from audit.emitter import emit
from audit.enums import AuditAction
from audit.models import AuditEvent
from billing import immutable, zero_credit
from billing.immutable import ImmutableRecordError
from billing.models import CreditLedger, CreditLedgerType, CreditUsageLog
from billing.tests.test_execute_graded_task import ExecuteGradedTaskTestBase
from billing.zero_credit import ZeroCreditViolation, zero_credit_scope

OP = "_selftest"
ALARMS = {"credit_ledger_anomaly", "audit_emit_failures_total"}


class Rollback(Exception):
    pass


def emit_one():
    return emit(
        AuditAction.ASSIGNMENT_COPY,
        actor=None,
        target_type="Assignment",
        target_id=uuid.uuid4(),
    )


def copy_of(event):
    """An unsaved AuditEvent with `event`'s values and a new id."""
    values = {
        f.attname: getattr(event, f.attname)
        for f in AuditEvent._meta.concrete_fields
        if not f.primary_key
    }
    return AuditEvent(id=uuid.uuid4(), **values)


class AuditInsideAScopeTests(TestCase):
    def test_z1_emit_inside_a_scope_stores_the_event(self):
        before = AuditEvent.objects.count()
        with zero_credit_scope(OP):
            event = emit_one()
        self.assertIsNotNone(event)
        self.assertEqual(AuditEvent.objects.count(), before + 1)
        self.assertTrue(AuditEvent.objects.filter(pk=event.pk).exists())

    def test_z2a_emit_inside_a_scope_logs_no_zero_credit_violation(self):
        with self.assertNoLogs("billing.zero_credit", level=logging.ERROR):
            with zero_credit_scope(OP):
                emit_one()

    def test_z2b_emit_inside_a_scope_counts_no_anomaly_or_emit_failure(self):
        with patch("audit.metrics.count") as count:
            with zero_credit_scope(OP):
                emit_one()
        names = {c.args[0] for c in count.call_args_list if c.args}
        self.assertEqual(names & ALARMS, set())

    def test_z3_audit_bulk_create_inside_a_scope_stores_the_row(self):
        row = copy_of(emit_one())
        with zero_credit_scope(OP):
            AuditEvent.objects.bulk_create([row])
        self.assertTrue(AuditEvent.objects.filter(pk=row.pk).exists())

    def test_z5_the_event_shares_the_transaction_of_the_work(self):
        with self.assertRaises(Rollback):
            with transaction.atomic():
                with zero_credit_scope(OP):
                    event = emit_one()
                self.assertIsNotNone(event)
                self.assertTrue(AuditEvent.objects.filter(pk=event.pk).exists())
                pk = event.pk
                raise Rollback
        self.assertFalse(AuditEvent.objects.filter(pk=pk).exists())


class CreditWritesStillRefusedTests(ExecuteGradedTaskTestBase):
    def setUp(self):
        super().setUp()
        self.teacher = self._make_teacher_with_credits(credits=1000)
        self.wallet = self.teacher.credit_wallet
        self.bucket = self.wallet.buckets.first()

    def assert_refused(self, attempted, write):
        before = (CreditLedger.objects.count(), CreditUsageLog.objects.count())
        with zero_credit_scope(OP):
            with self.assertRaises(ZeroCreditViolation) as cm:
                write()
        self.assertEqual(cm.exception.attempted, attempted)
        self.assertEqual(
            (CreditLedger.objects.count(), CreditUsageLog.objects.count()), before
        )

    def ledger_row(self):
        return CreditLedger.build(
            user=self.teacher,
            bucket=self.bucket,
            ledger_type=CreditLedgerType.GRANT,
            amount=1,
            reference="h299",
        )

    def test_z4a_ledger_insert_is_still_refused(self):
        self.assert_refused(
            zero_credit.LEDGER_INSERT,
            lambda: CreditLedger.objects.create(
                user_id=self.teacher.id,
                user_email=self.teacher.email,
                bucket=self.bucket,
                ledger_type=CreditLedgerType.GRANT,
                amount=1,
                reference="h299",
            ),
        )

    def test_z4b_usage_log_insert_is_still_refused(self):
        self.assert_refused(
            zero_credit.USAGE_LOG_INSERT,
            lambda: CreditUsageLog.record(
                wallet=self.wallet,
                bucket=self.bucket,
                amount=1,
                feature="Grading Assignment",
            ),
        )

    def test_z4c_ledger_bulk_create_is_still_refused(self):
        row = self.ledger_row()
        self.assert_refused(
            zero_credit.BULK_INSERT, lambda: CreditLedger.objects.bulk_create([row])
        )


class GuardShapeTests(TestCase):
    def test_z4d_an_unlisted_append_only_model_is_still_a_credit_write(self):
        sender = SimpleNamespace(
            _meta=SimpleNamespace(model_name="futurecredittable"),
            mutable_fields=frozenset(),
            __name__="FutureCreditTable",
        )
        instance = SimpleNamespace(_state=SimpleNamespace(adding=True), pk=None)
        with zero_credit_scope(OP):
            with self.assertRaises(ZeroCreditViolation) as cm:
                immutable._guard_save(sender, instance, raw=False, update_fields=None)
        self.assertEqual(cm.exception.attempted, zero_credit.LEDGER_INSERT)

    def test_z4e_audit_rows_are_still_append_only_inside_a_scope(self):
        event = emit_one()
        with zero_credit_scope(OP):
            with self.assertRaises(ImmutableRecordError):
                AuditEvent.objects.filter(pk=event.pk).update(action="changed")
            event.action = "changed"
            with self.assertRaises(ImmutableRecordError):
                event.save()
            with self.assertRaises(ImmutableRecordError):
                event.delete()
            with self.assertRaises(ImmutableRecordError):
                AuditEvent.objects.filter(pk=event.pk).delete()
        self.assertTrue(AuditEvent.objects.filter(pk=event.pk).exists())
