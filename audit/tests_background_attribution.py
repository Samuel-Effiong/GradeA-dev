"""Epic A S3 (plan 08 §4): the system actor and background work.

- Actor rule (G5): a credit ledger row names its INITIATOR - the signed-in
  user of the request being handled, or SYSTEM in Celery / Beat - with the
  wallet owner as target.
- Sweeps record themselves (G6): one AUDIT_RETENTION_SWEEP per run, counts
  only, zero-count runs included.
- Licence clawback (G7, D4): through the ledger - one EXPIRE row and one
  CREDIT_TRANSACTION per bucket with credits left - and no email in its log.
- Gate 3: a clawback racing the Beat cleanup, or the monthly grant, expires
  nothing twice.
"""

import logging
import threading
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from audit.context import request_audit_state
from audit.enums import ActorRole, AuditAction, RetentionClass
from audit.models import AuditEvent
from audit.tasks import sweep_audit_pii_short_retention, sweep_audit_retention
from billing.immutable import allow_unsafe_mutation
from billing.license_service import LicenseSubscriptionService
from billing.models import (
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditWallet,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
)
from billing.services import SubscriptionService
from billing.tasks import cleanup_expired_credit_buckets
from classrooms.models import School
from users.models import UserTypes

User = get_user_model()


def make_user(email, user_type=UserTypes.TEACHER, school=None):
    return User.objects.create_user(
        email=email,
        password="S3-test-pw-1",  # pragma: allowlist secret
        first_name="System",
        last_name="Actor",
        user_type=user_type,
        school=school,
        is_active=True,
    )


def superadmin(email):
    admin = User.objects.create_superuser(
        email=email,
        password="S3-super-pw-1",  # pragma: allowlist secret
        first_name="Super",
        last_name="Admin",
    )
    admin.user_type = UserTypes.SUPER_ADMIN
    admin.save()
    return admin


def bucket(wallet, bucket_type=CreditBucketType.MONTHLY, total=1000, used=300, **kw):
    kw.setdefault("expires_at", timezone.now() + timedelta(days=30))
    return CreditBucket.objects.create(
        wallet=wallet,
        bucket_type=bucket_type,
        total_credits=total,
        used_credits=used,
        **kw,
    )


def grant(user, amount=500):
    wallet, _ = CreditWallet.objects.get_or_create(user=user)
    return CreditLedger.record(
        user=user,
        bucket=bucket(wallet),
        ledger_type=CreditLedgerType.GRANT,
        amount=amount,
        reference="S3 test grant",
    )


def credit_events():
    return AuditEvent.objects.filter(action=AuditAction.CREDIT_TRANSACTION)


class ActorRuleTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="S3 School")
        self.teacher = make_user("s3.teacher@example.com", school=self.school)

    def test_background_work_is_the_system_with_the_owner_as_target(self):
        """A Celery grant, renewal or expiry runs outside any request."""
        row = grant(self.teacher)

        event = credit_events().get()
        self.assertEqual(event.actor_role, ActorRole.SYSTEM)
        self.assertIsNone(event.actor_id)
        self.assertEqual(event.target_id, self.teacher.id)
        self.assertEqual(event.school_id, self.school.id)
        self.assertEqual(event.metadata["ledger_id"], str(row.id))

    def test_the_same_grant_in_a_superadmin_request_names_the_superadmin(self):
        admin = superadmin("s3.super@example.com")
        with request_audit_state(SimpleNamespace(user=admin)):
            grant(self.teacher)

        event = credit_events().get()
        self.assertEqual(event.actor_id, admin.id)
        self.assertEqual(event.actor_role, ActorRole.SUPER_ADMIN)
        self.assertEqual(event.target_id, self.teacher.id)

    def test_an_anonymous_request_is_the_system(self):
        """Nobody signed in initiated it: the actor is the system."""
        from django.contrib.auth.models import AnonymousUser

        with request_audit_state(SimpleNamespace(user=AnonymousUser())):
            grant(self.teacher)

        self.assertEqual(credit_events().get().actor_role, ActorRole.SYSTEM)

    def test_a_celery_task_expiry_is_the_system(self):
        wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)
        bucket(wallet, expires_at=timezone.now() - timedelta(minutes=1))

        cleanup_expired_credit_buckets.apply()

        event = credit_events().get(metadata__ledger_type="EXPIRE")
        self.assertEqual(event.actor_role, ActorRole.SYSTEM)
        self.assertEqual(event.target_id, self.teacher.id)


class SweepSelfRecordTests(TestCase):
    def sweep_events(self):
        return AuditEvent.objects.filter(action=AuditAction.AUDIT_RETENTION_SWEEP)

    def test_a_retention_sweep_that_deletes_nothing_still_records_itself(self):
        sweep_audit_retention.apply()

        event = self.sweep_events().get()
        self.assertEqual(event.actor_role, ActorRole.SYSTEM)
        self.assertEqual(
            event.metadata, {"deleted_general": 0, "deleted_student_record": 0}
        )

    def test_a_retention_sweep_records_its_counts(self):
        old = timezone.now() - timedelta(days=400)
        for _ in range(2):
            grant(make_user(f"s3.old.{_}@example.com"))
        with allow_unsafe_mutation():
            AuditEvent.objects.filter(retention_class=RetentionClass.GENERAL).update(
                occurred_at=old
            )

        sweep_audit_retention.apply()

        event = self.sweep_events().get()
        self.assertEqual(
            event.metadata, {"deleted_general": 2, "deleted_student_record": 0}
        )

    def test_the_pii_sweep_records_what_it_scrubbed_including_zero(self):
        sweep_audit_pii_short_retention.apply()

        self.assertEqual(self.sweep_events().get().metadata, {"scrubbed": 0})


class ClawbackThroughTheLedgerTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="S3 Clawback School")
        self.admin = make_user(
            "s3.admin@example.com", UserTypes.SCHOOL_ADMIN, school=self.school
        )
        plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="S3 Licence Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
        )
        self.licence = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
        )
        self.teacher = make_user("s3.clawback.teacher@example.com", school=self.school)
        SchoolCreditAllocation.objects.create(
            license_subscription=self.licence,
            user=self.teacher,
            monthly_allocation=1000,
            is_active=True,
        )
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)
        self.monthly = bucket(self.wallet, total=1000, used=300)
        self.carry = bucket(
            self.wallet, bucket_type=CreditBucketType.CARRY_OVER, total=200, used=0
        )
        self.spent = bucket(
            self.wallet, bucket_type=CreditBucketType.OVERAGE, total=50, used=50
        )

    def remove(self):
        with request_audit_state(SimpleNamespace(user=self.admin)):
            LicenseSubscriptionService.remove_teacher_from_license(
                self.licence, self.teacher
            )

    def test_the_ledger_nets_exactly_the_expired_credits(self):
        self.remove()

        rows = CreditLedger.objects.filter(
            user_id=self.teacher.id, ledger_type=CreditLedgerType.EXPIRE
        )
        self.assertEqual(sorted(rows.values_list("amount", flat=True)), [200, 700])
        for b in (self.monthly, self.carry, self.spent):
            b.refresh_from_db()
            self.assertTrue(b.is_processed)
            self.assertLessEqual(b.expires_at, timezone.now())
        self.assertEqual(self.wallet.total_remaining_credits(), 0)

    def test_one_credit_transaction_per_bucket_naming_the_remover(self):
        self.remove()

        events = credit_events().filter(metadata__ledger_type="EXPIRE")
        self.assertEqual(events.count(), 2)  # the spent bucket has nothing left
        for event in events:
            self.assertEqual(event.actor_id, self.admin.id)
            self.assertEqual(event.target_id, self.teacher.id)

    def test_no_email_reaches_the_log(self):
        with self.assertLogs("billing.license_service", logging.INFO) as logs:
            self.remove()

        text = "\n".join(logs.output)
        self.assertNotIn(self.teacher.email, text)
        self.assertIn(str(self.teacher.id), text)


class RolloverLogTests(TestCase):
    def test_the_suppressed_rollover_log_carries_no_email(self):
        """SM ruling: the same BE-A-04 leak, in the rollover's log line."""
        teacher = make_user("s3.rollover@example.com")
        wallet, _ = CreditWallet.objects.get_or_create(user=teacher)
        bucket(wallet, total=1000, used=0)
        plan = cast(Any, SimpleNamespace(carry_over_expiry_months=1))
        now = timezone.now()
        with patch.object(
            CreditWallet,
            "compute_capped_rollover",
            return_value=(0, {"requested_rollover": 1000}),
        ):
            with self.assertLogs("billing.license_service", logging.INFO) as logs:
                LicenseSubscriptionService._rollover_and_grant_monthly_bucket(
                    teacher,
                    wallet,
                    plan,
                    grant_amount=500,
                    new_expiry=now + timedelta(days=30),
                    now=now,
                    reference="S3 rollover",
                    metadata={},
                )

        text = "\n".join(logs.output)
        self.assertIn("rollover fully suppressed", text)
        self.assertNotIn(teacher.email, text)


class ClawbackRaceTests(TransactionTestCase):
    """Gate 3, real threads and commits: nothing is expired twice."""

    def setUp(self):
        self.school = School.objects.create(name="S3 Race School")
        self.admin = make_user(
            "s3.race.admin@example.com", UserTypes.SCHOOL_ADMIN, school=self.school
        )
        plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="S3 Race Licence Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
        )
        self.licence = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
        )
        self.teacher = make_user("s3.race.teacher@example.com", school=self.school)
        SchoolCreditAllocation.objects.create(
            license_subscription=self.licence,
            user=self.teacher,
            monthly_allocation=1000,
            is_active=True,
        )
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)

    def clawback(self):
        """The REAL licence clawback (H-222: these tests used to carry a
        hand-written copy of its lock order, which would keep the old order
        whatever the production code did)."""
        LicenseSubscriptionService.remove_teacher_from_license(
            self.licence, self.teacher
        )

    def race(self, *callables):
        barrier = threading.Barrier(len(callables), timeout=30)
        errors = []

        def run(call):
            try:
                barrier.wait()
                call()
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=(c,)) for c in callables]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual(errors, [])

    def expire_rows_per_bucket(self):
        rows = CreditLedger.objects.filter(
            user_id=self.teacher.id, ledger_type=CreditLedgerType.EXPIRE
        ).values_list("bucket_id", flat=True)
        return [list(rows).count(b) for b in set(rows)]

    def test_a_clawback_racing_the_cleanup_expires_each_bucket_once(self):
        b = bucket(self.wallet, total=1000, used=300)
        stale = CreditBucket.objects.get(pk=b.pk)

        self.race(self.clawback, lambda: SubscriptionService.expire_bucket(stale))

        self.assertEqual(self.expire_rows_per_bucket(), [1])

    def test_a_clawback_racing_the_monthly_grant_expires_nothing_twice(self):
        bucket(self.wallet, total=1000, used=300)
        now = timezone.now()
        plan = cast(Any, SimpleNamespace(carry_over_expiry_months=1))

        def monthly_grant():
            from django.db import transaction

            with patch.object(
                CreditWallet,
                "compute_capped_rollover",
                return_value=(0, {"requested_rollover": 0}),
            ), transaction.atomic():
                LicenseSubscriptionService._rollover_and_grant_monthly_bucket(
                    self.teacher,
                    self.wallet,
                    plan,
                    grant_amount=500,
                    new_expiry=now + timedelta(days=30),
                    now=now,
                    reference="S3 race grant",
                    metadata={},
                )

        self.race(self.clawback, monthly_grant)

        self.assertTrue(all(n == 1 for n in self.expire_rows_per_bucket()))
