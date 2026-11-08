"""Epic B1a step 1: the credit-hold schema (plan 08 §3.1, §3.2, §4; 03a §2.2).

Models are looked up through the app registry inside each test, so a
missing model fails that test alone (LookupError) instead of the module.
"""

import importlib
import uuid

from django.apps import apps
from django.db import IntegrityError, connection, models, transaction
from django.test import TestCase, override_settings

from users.models import CustomUser, UserTypes


def model(name, app="billing"):
    return apps.get_model(app, name)


def make_user(email, user_type=UserTypes.TEACHER):
    return CustomUser.objects.create_user(
        email=email,
        password="testpass123",  # pragma: allowlist secret
        user_type=user_type,
    )


def make_wallet(email="b1a.teacher@example.com"):
    wallet_model = model("CreditWallet")
    user = make_user(email)
    wallet, _ = wallet_model.objects.get_or_create(user=user)
    return wallet


def make_job(wallet, **over):
    fields = {
        "job_type": "BATCH_GRADING",
        "wallet": wallet,
        "requested_by": wallet.user,
        "trace_id": uuid.uuid4(),
    }
    fields.update(over)
    return model("AIJob").objects.create(**fields)


def column_default(table, column):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = %s",
            [table, column],
        )
        row = cursor.fetchone()
    return row[0] if row else "NO COLUMN"


def constraint(name):
    """(contype, convalidated) of a named constraint, or None."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT contype, convalidated FROM pg_constraint WHERE conname = %s",
            [name],
        )
        return cursor.fetchone()


def index_def(name):
    with connection.cursor() as cursor:
        cursor.execute("SELECT indexdef FROM pg_indexes WHERE indexname = %s", [name])
        row = cursor.fetchone()
    return row[0] if row else None


class AIJobColumnsTests(TestCase):
    """S-1 / S-2: 03a §2.2 columns plus the plan §3.1 additions."""

    def test_aijob_has_the_03a_columns_and_the_plan_additions(self):
        names = {f.name for f in model("AIJob")._meta.get_fields()}
        expected = {
            # 03a §2.2
            "id",
            "job_type",
            "status",
            "requested_by",
            "wallet",
            "school_id",
            "course",
            "assignment",
            "estimate_p50",
            "estimate_p90",
            "sufficiency_verdict",
            "reserved_credits",
            "reservation_claimed_at",
            "consumed_credits",
            "released_credits",
            "total_items",
            "completed_items",
            "failed_items",
            "stopped_at_item",
            "queued_at",
            "expires_at",
            "trace_id",
            "meta",
            "created_at",
            "updated_at",
            # plan 08 §3.1
            "unbilled_credits",
            "overrun_charged",
            "hold_drawn",
            "warned_expiry_at",
            "held_per_item",
        }
        self.assertEqual(expected - names, set(), "missing AIJob columns")

    def test_aijob_on_delete_rules_follow_03a(self):
        aijob = model("AIJob")
        rules = {
            name: aijob._meta.get_field(name).remote_field.on_delete
            for name in ("wallet", "requested_by", "course", "assignment")
        }
        # NOTES §21n: was PROTECT (03a); a finished job now outlives its wallet.
        self.assertEqual(rules["wallet"], models.SET_NULL)
        # NOTES §21v R2: was CASCADE; a job is a credit record (owner D1/D4).
        self.assertEqual(rules["requested_by"], models.SET_NULL)
        self.assertEqual(rules["course"], models.SET_NULL)
        self.assertEqual(rules["assignment"], models.SET_NULL)
        self.assertIsInstance(aijob._meta.get_field("school_id"), models.UUIDField)

    def test_aijob_job_types_and_statuses(self):
        aijob = model("AIJob")
        job_types = {c[0] for c in aijob._meta.get_field("job_type").choices}
        self.assertEqual(
            job_types,
            {
                "BATCH_GRADING",
                "LESSON_GENERATION",
                "ASSIGNMENT_GENERATION",
                "INSIGHT_GENERATION",
                "SINGLE_CALL",
                "SINGLE_ITEM",
                "UPLOAD",
            },
        )
        statuses = {c[0] for c in aijob._meta.get_field("status").choices}
        self.assertEqual(
            statuses,
            {
                "ESTIMATED",
                "RESERVED",
                "RUNNING",
                "SETTLED",
                "FAILED",
                "QUEUED_PENDING_CREDITS",
                "EXPIRED",
                "CANCELLED",
            },
        )
        self.assertNotIn("PARTIALLY_COMPLETE", statuses)

    def test_a_new_job_starts_estimated_with_every_counter_at_zero(self):
        job = make_job(make_wallet())
        job.refresh_from_db()
        self.assertEqual(job.status, "ESTIMATED")
        for name in (
            "reserved_credits",
            "consumed_credits",
            "released_credits",
            "hold_drawn",
            "unbilled_credits",
            "overrun_charged",
            "held_per_item",
            "total_items",
            "completed_items",
            "failed_items",
        ):
            self.assertEqual(getattr(job, name), 0, name)
        self.assertEqual(job.meta, {})

    def test_aijob_counters_have_database_defaults(self):
        # H-56: rolled-back code that does not know a column still inserts.
        for column in (
            "reserved_credits",
            "consumed_credits",
            "released_credits",
            "hold_drawn",
            "unbilled_credits",
            "overrun_charged",
            "held_per_item",
            "total_items",
            "completed_items",
            "failed_items",
        ):
            self.assertEqual(column_default("billing_aijob", column), "0", column)


class AIJobConstraintTests(TestCase):
    """S-3: 03a §2.2 CHECKs and indexes."""

    def test_negative_job_credits_are_refused(self):
        wallet = make_wallet()
        for name in ("reserved_credits", "consumed_credits", "released_credits"):
            with self.subTest(name=name), self.assertRaises(IntegrityError):
                with transaction.atomic():
                    make_job(wallet, **{name: -1})

    def test_items_finished_beyond_the_total_are_refused(self):
        wallet = make_wallet()
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_job(wallet, total_items=2, completed_items=2, failed_items=1)
        make_job(wallet, total_items=2, completed_items=1, failed_items=1)

    def test_aijob_indexes_match_03a(self):
        open_ix = index_def("aijob_wallet_open_ix") or ""
        self.assertIn("(wallet_id, status)", open_ix)
        self.assertIn("RESERVED", open_ix)
        self.assertIn("RUNNING", open_ix)
        queued_ix = index_def("aijob_queued_expiry_ix") or ""
        self.assertIn("(status, expires_at)", queued_ix)
        self.assertIn("QUEUED_PENDING_CREDITS", queued_ix)
        requester_ix = index_def("aijob_requester_created_ix") or ""
        self.assertIn("(requested_by_id, created_at DESC)", requester_ix)


class WalletHoldColumnsTests(TestCase):
    """S-4: CreditWallet.reserved_credits and its CHECK (plan §3.2, §4)."""

    def test_wallet_starts_with_nothing_reserved(self):
        wallet = make_wallet()
        wallet.refresh_from_db()
        self.assertEqual(wallet.reserved_credits, 0)
        self.assertIsNone(wallet.low_balance_warned_level)
        self.assertIsNone(wallet.low_balance_warned_cycle_start)
        self.assertEqual(
            column_default("billing_creditwallet", "reserved_credits"), "0"
        )

    def test_a_negative_reservation_is_refused_by_the_database(self):
        wallet = make_wallet()
        with self.assertRaises(IntegrityError), transaction.atomic():
            type(wallet).objects.filter(pk=wallet.pk).update(reserved_credits=-1)

    def test_the_reservation_check_is_validated(self):
        self.assertEqual(
            constraint("creditwallet_reserved_credits_nonneg"), ("c", True)
        )


class ReverseGuardTests(TestCase):
    """S-5: plan §4 - reversing the reserved_credits migration is refused
    with the flag on or any hold open."""

    def guard(self):
        module = importlib.import_module(
            "billing.migrations.0074_b1a_credit_hold_schema"
        )
        return module.refuse_reverse_with_open_holds

    def test_reverse_is_refused_while_a_wallet_holds_credits(self):
        guard = self.guard()
        wallet = make_wallet()
        type(wallet).objects.filter(pk=wallet.pk).update(reserved_credits=5)
        with self.assertRaisesMessage(RuntimeError, "reserved_credits"):
            guard(apps, None)

    @override_settings(BILLING_HOLDS_ENABLED=True)
    def test_reverse_is_refused_with_the_flag_on(self):
        guard = self.guard()
        make_wallet()
        with self.assertRaisesMessage(RuntimeError, "BILLING_HOLDS_ENABLED"):
            guard(apps, None)

    def test_reverse_is_allowed_with_the_flag_off_and_no_hold_open(self):
        guard = self.guard()
        make_wallet()
        guard(apps, None)


class SettlementRecordTests(TestCase):
    """S-6: the settlement fence, unique (item_id, retry_count)."""

    def test_one_settlement_per_item_and_retry(self):
        record = model("SettlementRecord")
        job = make_job(make_wallet())
        item = uuid.uuid4()
        record.objects.create(job=job, item_id=item, retry_count=0)
        with self.assertRaises(IntegrityError), transaction.atomic():
            record.objects.create(job=job, item_id=item, retry_count=0)
        record.objects.create(job=job, item_id=item, retry_count=1)
        self.assertEqual(record.objects.filter(item_id=item).count(), 2)

    def test_settlement_has_no_settled_flag_column(self):
        # Plan §3.2 / review r2 M-a: the row is the fence, not a flag.
        names = {f.name for f in model("SettlementRecord")._meta.get_fields()}
        self.assertNotIn("settled", names)


class BackgroundTaskHoldTests(TestCase):
    """S-7: BackgroundProcessingTask.job and the per-item hold columns."""

    def make_task(self, job=None):
        task_model = model("BackgroundProcessingTask", app="students")
        return task_model.objects.create(
            requested_by=job.requested_by if job else make_user("b1a.t@example.com"),
            task_type="answer_extraction",
            job=job,
        )

    def test_a_task_starts_with_no_credit_hold(self):
        task = self.make_task()
        task.refresh_from_db()
        self.assertIsNone(task.job)
        self.assertIsNone(task.hold_state)
        self.assertEqual(
            (task.hold_credits, task.hold_drawn, task.released_credits), (0, 0, 0)
        )
        for column in ("hold_credits", "hold_drawn", "released_credits"):
            self.assertEqual(
                column_default("students_backgroundprocessingtask", column),
                "0",
                column,
            )

    def test_deleting_the_job_keeps_the_task(self):
        job = make_job(make_wallet())
        task = self.make_task(job)
        job.delete()
        task.refresh_from_db()
        self.assertIsNone(task.job_id)

    def test_the_job_foreign_key_is_validated_and_indexed(self):
        self.assertEqual(
            constraint("students_bpt_job_id_fk_billing_aijob"), ("f", True)
        )
        self.assertIn("(job_id)", index_def("students_bpt_job_id_ix") or "")

    def test_hold_state_reads_as_a_credit_hold(self):
        # SM ruling 2026-11-06 (2(a)): never a bare "held"; values unchanged.
        expected = [("HELD", "Credit held"), ("RETURNED", "Credit returned")]
        hold_state = importlib.import_module("billing.models").HoldState
        self.assertEqual([(c[0], str(c[1])) for c in hold_state.choices], expected)
        field = model("BackgroundProcessingTask", app="students")._meta.get_field(
            "hold_state"
        )
        self.assertEqual([(c[0], str(c[1])) for c in field.choices], expected)


class PlanAndSettingsTests(TestCase):
    """S-8: queue lifetime on the plan, BillingSettings, the flag."""

    def test_plan_queue_lifetime_defaults(self):
        plan = model("SubscriptionPlan")
        self.assertEqual(plan._meta.get_field("queue_expiry_days").default, 14)
        self.assertEqual(plan._meta.get_field("queue_expiry_warning_hours").default, 48)
        self.assertEqual(
            column_default("billing_subscriptionplan", "queue_expiry_days"), "14"
        )
        self.assertEqual(
            column_default("billing_subscriptionplan", "queue_expiry_warning_hours"),
            "48",
        )

    def test_billing_settings_is_one_row_with_its_defaults(self):
        settings_model = model("BillingSettings")
        first = settings_model.load()
        second = settings_model.load()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(settings_model.objects.count(), 1)
        self.assertEqual(first.cold_start_cost_factor, 3.0)
        self.assertEqual(first.unstarted_backstop_hours, 36)

    def test_holds_flag_defaults_off(self):
        from django.conf import settings

        self.assertIs(getattr(settings, "BILLING_HOLDS_ENABLED", None), False)
