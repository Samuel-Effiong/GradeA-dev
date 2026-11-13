"""B1a plan step 3b: a teacher who left before the school's overage payment
went through has their share refunded (plan §9 item 3, owner 2026-10-29;
NOTES §18 and its two addenda).

Everything sits behind DEPARTED_SHARE_REFUND_APPROVED, off until the owner
approves the texts. With it off, today's behavior and today's texts stay.
Every Stripe call is mocked: nothing here reaches Stripe.
"""

import importlib
import logging
from datetime import timedelta
from unittest.mock import patch

from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from django.utils.html import escape

from billing.license_service import LicenseSubscriptionService
from billing.models import (
    BillingTransaction,
    BillingTransactionStatus,
    CreditBucket,
    CreditBucketType,
    CreditWallet,
    LicenseBillingRecord,
    LicenseOverageOfflineRequest,
    LicenseOveragePurchaseIntent,
    LicenseOveragePurchaseStatus,
    LicenseSubscription,
    PaymentRefund,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
)
from billing.payment_refunds import RefundService
from billing.stripe_service import StripeWebhookHandler
from classrooms.models import School
from users.models import CustomUser, UserTypes

PRICE = 299
PI = "pi_dr"
SEND = "AutoGrader.tasks.send_email_task.delay"
CREATE = "stripe.Refund.create"
LIST = "stripe.Refund.list"
TODAY_REVIEW = "Needs manual review (partial refund or manual grant)."
TODAY_SUFFIX = " (1 skipped, needs review)"
TODAY_NOT_CREDITED = "Contact support if this needs manual follow-up."
APPROVAL_SUBJECT = "Your overage request has been approved — DR School"
APPROVAL_MESSAGE = "Your offline overage request for 3 block(s) has been approved."
KEYS = {
    "purchase_note",
    "history_suffix_card",
    "history_suffix_offline",
    "email_subject",
    "email_body_card",
    "approval_email_not_credited",
    "offline_record_notes",
}
LABEL = "Overage Refund Owed (Offline)"
#: The owner-approved wording (2026-11-08), verbatim, with the SM's two
#: rulings: the bare amount for the record notes, and W-6 without the
#: "Not credited — " heading the template keeps.
APPROVED = {
    "purchase_note": (
        "Paid for {blocks} block(s). {n} teacher(s) had left before the "
        "purchase went through, so they weren't given credits: {skipped}. "
        "Their share, {amount}, is being refunded automatically to the card "
        "used."
    ),
    "history_suffix_card": "({n} teacher(s) had left; their share is being refunded)",
    "history_suffix_offline": "({n} teacher(s) had left; their share is owed back)",
    "email_subject": "Part of your credit purchase is being refunded",
    "email_body_card": (
        "{n} teacher(s) in your credit purchase had left your school before it "
        "went through, so they weren't given credits. The other teachers "
        "received theirs. We're refunding {amount} for the credits that "
        "weren't given to the card you paid with."
    ),
    "offline_record_notes": "{amount}",
    "approval_email_not_credited": (
        "these teachers had left your school before the purchase went "
        "through, so they weren't given credits. Our team will refund "
        "{amount} to you directly."
    ),
}
#: The literal, not the formatter: a broken formatter must not pass.
SHARE_SHOWN = "$2.99"
# Named, not attribute access: neither exists before the change.
TASK_NAME = "refund_departed_teacher_share"
REFUND_DUE = "OFFLINE_OVERAGE_REFUND_DUE"


def drs():
    return importlib.import_module("billing.departed_share_refund")


def task():
    from billing import tasks

    return getattr(tasks, TASK_NAME)


def switch_on():
    return patch.object(drs(), "DEPARTED_SHARE_REFUND_APPROVED", True)


def switch_off():
    return patch.object(drs(), "DEPARTED_SHARE_REFUND_APPROVED", False)


def refund_list(*refunds):
    return {"data": list(refunds)}


class SchoolFixture:
    def make_school(self):
        self.school = School.objects.create(name="DR School")
        self.admin = CustomUser.objects.create_user(
            email="dr-admin@school.edu",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        self.super_admin = CustomUser.objects.create_superuser(
            email="dr-root@gradea.com",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
        )
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="DR License",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
            overage_block_size=5000,
            overage_block_price=PRICE,
        )
        self.license_sub = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
            max_seats=3,
        )
        self.teachers = []
        for tag in ("a", "b", "c"):
            teacher = CustomUser.objects.create_user(
                email=f"dr-{tag}@school.edu",
                password="test123",  # pragma: allowlist secret
                user_type=UserTypes.TEACHER,
                school=self.school,
            )
            LicenseSubscriptionService.add_teacher_to_license(
                self.license_sub, teacher.email
            )
            self.teachers.append(teacher)
        self.a, self.b, self.c = self.teachers
        self.allocations = {str(t.id): 1 for t in self.teachers}

    def depart(self, teacher):
        """The teacher leaves between checkout and fulfillment."""
        SchoolCreditAllocation.objects.filter(
            license_subscription=self.license_sub, user=teacher
        ).update(is_active=False)

    def overage_credits(self, teacher):
        return sum(
            CreditBucket.objects.filter(
                wallet__user=teacher, bucket_type=CreditBucketType.OVERAGE
            ).values_list("total_credits", flat=True)
        )

    def blocks_used(self, teacher):
        return CreditWallet.objects.get(user=teacher).overage_blocks_used


class CardPathBase(TestCase, SchoolFixture):
    def setUp(self):
        self.make_school()
        self.intent = LicenseOveragePurchaseIntent.objects.create(
            license_subscription=self.license_sub,
            initiated_by=self.admin,
            total_blocks=3,
            allocations=self.allocations,
            block_size_snapshot=self.plan.overage_block_size,
            unit_price_cents_snapshot=PRICE,
            amount_cents=3 * PRICE,
            status=LicenseOveragePurchaseStatus.PENDING,
            stripe_checkout_session_id="cs_dr",
        )
        self.session = {
            "id": "cs_dr",
            "payment_status": "paid",
            "payment_intent": PI,
            "invoice": None,
            "amount_total": 3 * PRICE,
            "currency": "usd",
        }
        self.metadata = {
            "flow": "license_overage_purchase_checkout",
            "license_id": str(self.license_sub.id),
            "intent_id": str(self.intent.id),
        }
        self.depart(self.c)

    def fulfill(self, execute=True):
        """Run the real webhook handler; route the refund task to apply()."""
        with patch.object(
            task(), "delay", side_effect=lambda *a: task().apply(args=a)
        ) as delay:
            with self.captureOnCommitCallbacks(execute=execute) as callbacks:
                StripeWebhookHandler._handle_license_overage_checkout_completed(
                    self.session, self.metadata
                )
        return delay, callbacks

    def ours(self, amount=PRICE):
        return {
            "id": "re_ours",
            "amount": amount,
            "status": "succeeded",
            "metadata": {
                "system_refund_reason": "departed_teacher_share",
                "intent_id": str(self.intent.id),
                "retain_credits": "true",
            },
        }


class SwitchOffTests(CardPathBase):
    def test_dr1_switch_off_keeps_todays_card_behavior_and_texts(self):
        with switch_off(), patch(CREATE) as create, patch(LIST) as listing, patch(
            SEND
        ) as send:
            with self.captureOnCommitCallbacks(execute=True):
                StripeWebhookHandler._handle_license_overage_checkout_completed(
                    self.session, self.metadata
                )
        self.assertEqual(self.overage_credits(self.a), 5000)
        self.assertEqual(self.overage_credits(self.b), 5000)
        self.assertEqual(self.overage_credits(self.c), 0)
        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, LicenseOveragePurchaseStatus.FAILED)
        self.assertTrue((self.intent.failure_reason or "").endswith(TODAY_REVIEW))
        txn = BillingTransaction.objects.get(stripe_payment_intent_id=PI)
        self.assertTrue(txn.description.endswith(TODAY_SUFFIX))
        create.assert_not_called()
        listing.assert_not_called()
        send.assert_not_called()


class SwitchTests(TestCase):
    def test_dr3_the_switch_is_on_and_every_text_is_the_approved_wording(self):
        from billing.models import LicenseBillingRecordType

        self.assertIs(drs().DEPARTED_SHARE_REFUND_APPROVED, True)
        texts = drs().DEPARTED_SHARE_REFUND_TEXTS
        self.assertEqual(set(texts), KEYS)
        self.assertEqual(texts, APPROVED)
        self.assertEqual(getattr(LicenseBillingRecordType, REFUND_DUE).label, LABEL)


class CardRefundTests(CardPathBase):
    def test_dr4_a_departure_refunds_exactly_that_share_once(self):
        with switch_on(), patch(CREATE) as create, patch(
            LIST, return_value=refund_list()
        ) as listing, patch(SEND):
            delay, _ = self.fulfill()
        self.assertEqual(self.overage_credits(self.a), 5000)
        self.assertEqual(self.overage_credits(self.b), 5000)
        self.assertEqual(self.overage_credits(self.c), 0)
        delay.assert_called_once_with(str(self.intent.id), PRICE)
        listing.assert_called_once()
        self.assertEqual(listing.call_args.kwargs["payment_intent"], PI)
        create.assert_called_once()
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["payment_intent"], PI)
        self.assertEqual(kwargs["amount"], PRICE)
        self.assertEqual(
            kwargs["idempotency_key"], f"departed-share-refund-{self.intent.id}"
        )
        self.assertEqual(
            kwargs["metadata"],
            {
                "system_refund_reason": "departed_teacher_share",
                "retain_credits": "true",
                "intent_id": str(self.intent.id),
            },
        )

    def test_dr5_the_refund_runs_only_after_commit(self):
        with switch_on(), patch(CREATE) as create, patch(
            LIST, return_value=refund_list()
        ), patch(SEND):
            _, callbacks = self.fulfill(execute=False)
            create.assert_not_called()
            self.assertGreaterEqual(len(callbacks), 1)

    def test_dr5_a_rolled_back_webhook_refunds_nothing(self):
        class Boom(Exception):
            pass

        with switch_on(), patch(CREATE) as create, patch(
            LIST, return_value=refund_list()
        ), patch(SEND):
            with patch.object(
                task(), "delay", side_effect=lambda *a: task().apply(args=a)
            ):
                with self.captureOnCommitCallbacks(execute=True):
                    with self.assertRaises(Boom):
                        with transaction.atomic():
                            StripeWebhookHandler._handle_license_overage_checkout_completed(
                                self.session, self.metadata
                            )
                            raise Boom
        create.assert_not_called()

    def test_dr6_the_refund_coming_back_leaves_every_granted_credit(self):
        with switch_on(), patch(CREATE) as create, patch(
            LIST, return_value=refund_list()
        ), patch(SEND):
            self.fulfill()
        sent = create.call_args.kwargs
        before = (
            self.overage_credits(self.a),
            self.overage_credits(self.b),
            self.blocks_used(self.a),
            self.blocks_used(self.b),
        )
        transactions = BillingTransaction.objects.count()
        charge = {
            "id": "ch_dr",
            "object": "charge",
            "payment_intent": PI,
            "amount": 3 * PRICE,
            "amount_captured": 3 * PRICE,
            "amount_refunded": sent["amount"],
            "currency": "usd",
            "refunds": {
                "data": [
                    {
                        "id": "re_dr",
                        "amount": sent["amount"],
                        "metadata": sent["metadata"],
                    }
                ]
            },
            "metadata": {},
        }
        RefundService.apply(charge)
        after = (
            self.overage_credits(self.a),
            self.overage_credits(self.b),
            self.blocks_used(self.a),
            self.blocks_used(self.b),
        )
        self.assertEqual(after, before)
        self.assertTrue(
            PaymentRefund.objects.get(stripe_payment_intent_id=PI).retain_credits
        )
        txn = BillingTransaction.objects.get(stripe_payment_intent_id=PI)
        self.assertEqual(txn.status, BillingTransactionStatus.PARTIALLY_REFUNDED)
        self.assertEqual(BillingTransaction.objects.count(), transactions)

    def test_dr7_the_refund_is_capped_at_what_is_still_refundable(self):
        earlier = {"id": "re_old", "amount": 700, "status": "succeeded", "metadata": {}}
        lost = {"id": "re_lost", "amount": 500, "status": "failed", "metadata": {}}
        with switch_on(), patch(CREATE) as create, patch(
            LIST, return_value=refund_list(earlier, lost)
        ), patch(SEND):
            self.fulfill()
        create.assert_called_once()
        self.assertEqual(create.call_args.kwargs["amount"], 3 * PRICE - 700)

    def test_dr7_nothing_left_to_refund_is_reported_not_refunded(self):
        everything = {
            "id": "re_all",
            "amount": 3 * PRICE,
            "status": "succeeded",
            "metadata": {},
        }
        with switch_on(), patch(CREATE) as create, patch(
            LIST, return_value=refund_list(everything)
        ), patch(SEND), self.assertLogs("billing", logging.ERROR) as logs:
            self.fulfill()
        create.assert_not_called()
        self.assertTrue(any("MANUAL RECONCILIATION" in line for line in logs.output))

    def test_dr8_a_rerun_never_refunds_twice(self):
        with switch_on(), patch(CREATE) as create, patch(
            LIST, return_value=refund_list(self.ours())
        ), patch(SEND):
            task().apply(args=(str(self.intent.id), PRICE))
            self.fulfill()
        create.assert_not_called()

    def test_dr9_a_connection_error_is_retried(self):
        from celery.exceptions import Retry

        from billing.imports import stripe

        self.fulfill_off()
        with switch_on(), patch(
            CREATE, side_effect=stripe.error.APIConnectionError("down")
        ) as create, patch(LIST, return_value=refund_list()), patch.object(
            task(), "retry", side_effect=Retry()
        ) as retry:
            with self.assertRaises(Retry):
                task().run(str(self.intent.id), PRICE)
        create.assert_called_once()
        retry.assert_called_once()
        self.assertLessEqual(task().max_retries, 5)

    def test_dr9_a_refused_refund_is_reported_and_grants_stay(self):
        from billing.imports import stripe

        self.fulfill_off()
        before = (self.overage_credits(self.a), self.overage_credits(self.b))
        status = LicenseOveragePurchaseIntent.objects.get(pk=self.intent.pk).status
        with switch_on(), patch(
            CREATE, side_effect=stripe.error.InvalidRequestError("no", None)
        ), patch(LIST, return_value=refund_list()), patch.object(
            task(), "retry"
        ) as retry, self.assertLogs(
            "billing", logging.ERROR
        ) as logs:
            task().run(str(self.intent.id), PRICE)
        retry.assert_not_called()
        self.assertTrue(any("MANUAL RECONCILIATION" in line for line in logs.output))
        self.assertEqual(
            (self.overage_credits(self.a), self.overage_credits(self.b)), before
        )
        self.assertEqual(
            LicenseOveragePurchaseIntent.objects.get(pk=self.intent.pk).status, status
        )

    def fulfill_off(self):
        with patch(CREATE), patch(LIST), patch(SEND):
            with self.captureOnCommitCallbacks(execute=True):
                StripeWebhookHandler._handle_license_overage_checkout_completed(
                    self.session, self.metadata
                )

    def test_dr10_no_payment_intent_is_reported_not_refunded(self):
        self.session["payment_intent"] = None
        with switch_on(), patch(CREATE) as create, patch(LIST) as listing, patch(
            SEND
        ), self.assertLogs("billing", logging.ERROR) as logs:
            self.fulfill()
        create.assert_not_called()
        listing.assert_not_called()
        self.assertTrue(any("MANUAL RECONCILIATION" in line for line in logs.output))

    def test_dr11_the_admin_is_emailed_the_approved_card_text(self):
        texts = drs().DEPARTED_SHARE_REFUND_TEXTS
        with switch_on(), patch(CREATE), patch(LIST, return_value=refund_list()), patch(
            SEND
        ) as send:
            self.fulfill()
        mine = [
            c.kwargs
            for c in send.call_args_list
            if c.kwargs.get("recipient_list") == [self.admin.email]
        ]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["subject"], APPROVED["email_subject"])
        self.assertEqual(
            mine[0]["message"],
            APPROVED["email_body_card"].format(n=1, amount=SHARE_SHOWN),
        )
        self.assertEqual(texts, APPROVED)

    def test_dr12_the_purchase_note_and_history_suffix_are_approved(self):
        with switch_on(), patch(CREATE), patch(LIST, return_value=refund_list()), patch(
            SEND
        ):
            self.fulfill()
        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, LicenseOveragePurchaseStatus.FAILED)
        skipped = [{"teacher_id": str(self.c.id), "blocks": 1}]
        self.assertEqual(
            self.intent.failure_reason,
            APPROVED["purchase_note"].format(
                blocks=3, n=1, skipped=skipped, amount=SHARE_SHOWN
            ),
        )
        self.assertIn(SHARE_SHOWN, self.intent.failure_reason or "")
        self.assertNotIn("Needs manual review", self.intent.failure_reason or "")
        txn = BillingTransaction.objects.get(stripe_payment_intent_id=PI)
        self.assertTrue(
            txn.description.endswith(" " + APPROVED["history_suffix_card"].format(n=1))
        )
        self.assertNotIn("needs review", txn.description)

    def test_dr17_a_non_usd_payment_is_reported_not_refunded(self):
        self.session["currency"] = "eur"
        with switch_on(), patch(CREATE) as create, patch(LIST) as listing, patch(
            SEND
        ), self.assertLogs("billing", logging.ERROR) as logs:
            delay, _ = self.fulfill()
        delay.assert_not_called()
        create.assert_not_called()
        listing.assert_not_called()
        self.assertTrue(any("MANUAL RECONCILIATION" in line for line in logs.output))


class AmountFormatTests(TestCase):
    def test_dr16_amounts_show_as_dollars(self):
        self.assertEqual(drs().format_amount(299), "$2.99")
        self.assertEqual(drs().format_amount(0), "$0.00")
        self.assertEqual(drs().format_amount(123456), "$1,234.56")


class NoDepartureTests(CardPathBase):
    def setUp(self):
        super().setUp()
        SchoolCreditAllocation.objects.filter(
            license_subscription=self.license_sub, user=self.c
        ).update(is_active=True)

    def test_dr14_no_skipped_teacher_means_no_refund(self):
        with switch_on(), patch(CREATE) as create, patch(LIST) as listing, patch(
            SEND
        ) as send:
            delay, _ = self.fulfill()
        create.assert_not_called()
        listing.assert_not_called()
        delay.assert_not_called()
        send.assert_not_called()
        self.assertFalse(
            LicenseBillingRecord.objects.filter(
                record_type="OFFLINE_OVERAGE_REFUND_DUE"
            ).exists()
        )


class OfflinePathTests(TestCase, SchoolFixture):
    def setUp(self):
        self.make_school()
        with patch("billing.license_service.send_email_task"):
            result = LicenseSubscriptionService.request_overage_offline(
                self.license_sub,
                self.admin,
                total_blocks=3,
                allocations=self.allocations,
            )
        self.request = LicenseOverageOfflineRequest.objects.get(id=result["request_id"])
        self.depart(self.c)

    def approve(self):
        with self.captureOnCommitCallbacks(execute=True):
            LicenseSubscriptionService.approve_overage_offline_request(
                self.request,
                performed_by=self.super_admin,
                amount_confirmed_cents=3 * PRICE,
                payment_reference="WIRE-DR",
                payment_method_label="Wire transfer",
            )

    def refund_due(self):
        return LicenseBillingRecord.objects.filter(
            record_type="OFFLINE_OVERAGE_REFUND_DUE"
        )

    def offline_txn(self):
        return BillingTransaction.objects.filter(
            license_subscription=self.license_sub
        ).latest("created_at")

    def test_dr2_switch_off_keeps_todays_offline_behavior(self):
        with switch_off(), patch(CREATE) as create, patch(LIST) as listing, patch(
            SEND
        ) as send:
            self.approve()
        self.request.refresh_from_db()
        self.assertEqual(
            [s["teacher_id"] for s in self.request.skipped_allocations or []],
            [str(self.c.id)],
        )
        self.assertFalse(self.refund_due().exists())
        self.assertTrue(self.offline_txn().description.endswith(TODAY_SUFFIX))
        create.assert_not_called()
        listing.assert_not_called()
        mail = self.approval_email(send)
        self.assertEqual(mail["subject"], APPROVAL_SUBJECT)
        self.assertEqual(mail["message"], APPROVAL_MESSAGE)
        self.assertIn(TODAY_NOT_CREDITED, mail["html_message"])
        self.assertNotIn("PENDING OWNER TEXT", mail["html_message"])

    def test_dr13_switch_on_records_the_share_owed_and_tells_the_admin(self):
        drs()
        with switch_on(), patch(CREATE) as create, patch(LIST) as listing, patch(
            SEND
        ) as send:
            self.approve()
        record = self.refund_due().get()
        self.assertIsNone(record.amount_paid_cents)
        self.assertEqual(record.notes, SHARE_SHOWN)
        self.assertTrue(
            self.offline_txn().description.endswith(
                " " + APPROVED["history_suffix_offline"].format(n=1)
            )
        )
        self.assertNotIn("needs review", self.offline_txn().description)
        mail = self.approval_email(send)
        self.assertEqual(mail["subject"], APPROVAL_SUBJECT)
        html = mail["html_message"]
        self.assertIn(
            "<strong>Not credited</strong> — "
            + escape(
                APPROVED["approval_email_not_credited"].format(amount=SHARE_SHOWN)
            ),
            html,
        )
        self.assertNotIn(TODAY_NOT_CREDITED, html)
        self.assertIn(str(self.c.id), html)
        create.assert_not_called()
        listing.assert_not_called()

    def approval_email(self, send):
        """Exactly one email goes to the admin: the approval email."""
        mine = [
            c.kwargs
            for c in send.call_args_list
            if c.kwargs.get("recipient_list") == [self.admin.email]
        ]
        self.assertEqual(len(mine), 1)
        self.assertEqual(send.call_count, 1)
        return mine[0]


class ChoicesMigrationTests(TransactionTestCase):
    def test_dr15_0078_is_choices_only_and_reverses_cleanly(self):
        target = [("billing", "0078_alter_licensebillingrecord_record_type")]
        previous = [("billing", "0077_billingsettings_hold_sweep_margin")]

        def columns():
            with connection.cursor() as cursor:
                return [
                    (c.name, c.type_code, c.null_ok)
                    for c in connection.introspection.get_table_description(
                        cursor, LicenseBillingRecord._meta.db_table
                    )
                ]

        before = columns()
        executor = MigrationExecutor(connection)
        executor.loader.get_migration(*target[0])
        try:
            executor.migrate(previous)
            self.assertEqual(columns(), before)
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes("billing"))
        self.assertEqual(columns(), before)
