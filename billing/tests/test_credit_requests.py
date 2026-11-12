"""Epic B1c: credit requests (plan 08 §3.8, STEP0_PLAN, owner go 2026-11-11).

A licensed teacher asks the school admin for more credits; the admin
approves (the normal overage purchase, keyed to the request) or declines.
The owner-approved texts W1-W13 (OWNER_SHEET §4) are checked word for word.

Every Stripe call is mocked; nothing here reaches Stripe.
"""

import importlib
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from audit.enums import FINANCIAL_ACTIONS, AuditAction
from audit.models import AuditEvent
from billing.license_service import LicenseSubscriptionService
from billing.models import (
    CreditBucket,
    CreditBucketType,
    LicenseOverageOfflineRequest,
    LicenseOveragePurchaseIntent,
    LicenseOveragePurchaseStatus,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
)
from billing.stripe_service import StripeWebhookHandler
from classrooms.models import School
from users.models import CustomUser, UserTypes

PRICE = 299
BLOCK = 5000
SEND = "AutoGrader.tasks.send_email_task.delay"
SESSION_CREATE = "stripe.checkout.Session.create"
SESSION_EXPIRE = "stripe.checkout.Session.expire"
CUSTOMER = "billing.stripe_service.StripeCustomerService.get_or_create_license_customer"
REFUND_TASK = "billing.tasks.refund_departed_teacher_share"
BASE = "/api/v1/billing/credit-requests"
ON = override_settings(CREDIT_REQUESTS_ENABLED=True)
OFF = override_settings(CREDIT_REQUESTS_ENABLED=False)
# Named, not attribute access: none of these exists before the change.
CREATE_ACTION = "CREDIT_REQUEST_CREATE"
DECIDE_ACTION = "CREDIT_REQUEST_DECIDE"
SUPERSEDED = "SUPERSEDED"
STATUS_ENUM = "CreditRequestStatus"
REQUEST_ID = "credit_request_id"
EXPIRES_AT = "session_expires_at"

#: OWNER_SHEET §4, approved word for word (SM relay, 2026-11-11).
APPROVED_TEXTS = {
    "admin_new_subject": "{teacher} asked for more credits",
    "admin_new_body": (
        "{teacher} asked for {n} more credit blocks for {school}. Their note: "
        '"{note}". Open your credit requests to approve or decline.'
    ),
    "granted_subject": "Your extra credits are ready",
    "granted_body": (
        "{admin} approved your request. {n} credit blocks have been added to "
        "your account."
    ),
    "declined_subject": "Your credit request was declined",
    "declined_body": (
        "{admin} declined your request for {n} credit blocks. Their reason: "
        '"{reason}".'
    ),
    "closed_subject": "Your credit request was closed",
    "closed_body": (
        "Your request for {n} credit blocks was closed because you are no "
        "longer on {school}'s license."
    ),
    "refund_subject": "We refunded a duplicate payment",
    "refund_body": (
        "Two payments went through for the same credit request. We kept one "
        "and refunded the other ({amount}) to your card."
    ),
    "error_open": (
        "You already have a credit request waiting. Withdraw it first if you "
        "want to change it."
    ),
    "error_no_license": "You need to be on a school license to ask for credits.",
    "error_answered": "This request has already been answered.",
    "error_withdraw": "You can only withdraw a request while it is waiting.",
    "note_checkout_closed": (
        "The payment page closed before payment. The request is waiting again."
    ),
    "note_offline_rejected": (
        "Our team could not confirm the offline payment: {reason}. The "
        "request is waiting again."
    ),
    "note_teacher_left": "Closed: the teacher left the license.",
    "note_license_ended": "Closed: the license ended.",
}
APPROVED_STATUS_LABELS = {
    "PENDING": "Waiting",
    "APPROVED": "Approved",
    "GRANTED": "Credits added",
    "DECLINED": "Declined",
    "WITHDRAWN": "Withdrawn",
    "CLOSED": "Closed",
}


def cr():
    return importlib.import_module("billing.credit_requests")


def model():
    from django.apps import apps

    return apps.get_model("billing", "CreditRequest")


DAY = timedelta(hours=24)


def fake_session(session_id="cs_cr", expires_in=DAY):
    return SimpleNamespace(
        id=session_id,
        url=f"https://checkout.stripe.test/{session_id}",
        expires_at=int((timezone.now() + expires_in).timestamp()),
    )


class Fixture(TestCase):
    def make_school(self):
        self.school = School.objects.create(name="CR School")
        self.admin = CustomUser.objects.create_user(
            email="cr-admin@school.edu",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
            first_name="Ada",
            last_name="Admin",
        )
        self.super_admin = CustomUser.objects.create_superuser(
            email="cr-root@gradea.com",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
            first_name="Root",
            last_name="Team",
        )
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="CR License",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
            overage_block_size=BLOCK,
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
        self.teacher = self.make_teacher("t", "Tess", "Teacher")
        self.other = self.make_teacher("o", "Otto", "Other")

    def make_teacher(self, tag, first, last):
        teacher = CustomUser.objects.create_user(
            email=f"cr-{tag}@school.edu",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            school=self.school,
            first_name=first,
            last_name=last,
        )
        LicenseSubscriptionService.add_teacher_to_license(
            self.license_sub, teacher.email
        )
        return teacher

    def overage_credits(self, teacher):
        return sum(
            CreditBucket.objects.filter(
                wallet__user=teacher, bucket_type=CreditBucketType.OVERAGE
            ).values_list("total_credits", flat=True)
        )

    def new_request(self, teacher=None, blocks=2, message="Grading week"):
        with patch(SEND):
            with self.captureOnCommitCallbacks(execute=True):
                return cr().create_request(teacher or self.teacher, blocks, message)

    def status(self, request):
        return model().objects.get(pk=request.pk).status

    def approve_by_card(self, request, session=None):
        with patch(CUSTOMER, return_value="cus_cr"), patch(
            SESSION_CREATE, return_value=session or fake_session()
        ) as create:
            result = cr().approve_request(
                request.pk,
                self.admin,
                payment_method="stripe",
                success_url="https://app.test/ok",
                cancel_url="https://app.test/cancel",
            )
        return result, create

    def paid_session(self, intent, session_id="cs_cr", pi="pi_cr"):
        session = {
            "id": session_id,
            "payment_status": "paid",
            "payment_intent": pi,
            "invoice": None,
            "amount_total": intent.amount_cents,
            "currency": "usd",
        }
        metadata = {
            "flow": "license_overage_purchase_checkout",
            "license_id": str(self.license_sub.id),
            "intent_id": str(intent.id),
        }
        return session, metadata

    def complete(self, intent, **kwargs):
        session, metadata = self.paid_session(intent, **kwargs)
        with patch(SEND) as send, patch(f"{REFUND_TASK}.delay") as refund, patch(
            SESSION_EXPIRE
        ) as expire:
            with self.captureOnCommitCallbacks(execute=True):
                StripeWebhookHandler.handle_checkout_completed(
                    {**session, "metadata": metadata}
                )
        return send, refund, expire


def subjects(send):
    return [c.kwargs.get("subject") for c in send.call_args_list]


# --------------------------------------------------------------------------
# Wording, statuses and audit
# --------------------------------------------------------------------------


class WordingTests(TestCase):
    def test_every_text_is_the_owner_approved_wording(self):
        self.assertEqual(cr().CREDIT_REQUEST_TEXTS, APPROVED_TEXTS)

    def test_status_names_are_the_approved_ones(self):
        CreditRequestStatus = getattr(
            importlib.import_module("billing.models"), STATUS_ENUM
        )

        self.assertEqual(
            {value: str(label) for value, label in CreditRequestStatus.choices},
            APPROVED_STATUS_LABELS,
        )

    def test_the_two_audit_actions_are_financial_with_the_approved_labels(self):
        self.assertEqual(
            getattr(AuditAction, CREATE_ACTION).label, "Credit request made"
        )
        self.assertEqual(
            getattr(AuditAction, DECIDE_ACTION).label, "Credit request answered"
        )
        self.assertIn(getattr(AuditAction, CREATE_ACTION), FINANCIAL_ACTIONS)
        self.assertIn(getattr(AuditAction, DECIDE_ACTION), FINANCIAL_ACTIONS)

    def test_superseded_is_a_new_purchase_status(self):
        self.assertEqual(
            getattr(LicenseOveragePurchaseStatus, SUPERSEDED), "SUPERSEDED"
        )


# --------------------------------------------------------------------------
# Create, decline and withdraw, through the service
# --------------------------------------------------------------------------


class CreateTests(Fixture):
    def setUp(self):
        self.make_school()

    def test_a_licensed_teacher_makes_a_request_and_the_admin_is_emailed_w1(self):
        with patch(SEND) as send:
            with self.captureOnCommitCallbacks(execute=True):
                request = cr().create_request(self.teacher, 2, "Grading week")
        self.assertEqual(request.status, "PENDING")
        self.assertEqual(request.license_subscription, self.license_sub)
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs["recipient_list"], [self.admin.email])
        self.assertEqual(kwargs["subject"], "Tess Teacher asked for more credits")
        self.assertEqual(
            kwargs["message"],
            "Tess Teacher asked for 2 more credit blocks for CR School. Their "
            'note: "Grading week". Open your credit requests to approve or '
            "decline.",
        )
        event = AuditEvent.objects.get(action=getattr(AuditAction, CREATE_ACTION))
        self.assertEqual(event.target_id, request.pk)
        self.assertEqual(event.retention_class, "FINANCIAL")

    def test_one_open_request_per_teacher_w6(self):
        self.new_request()
        with self.assertRaises(cr().CreditRequestError) as ctx:
            self.new_request()
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(str(ctx.exception), APPROVED_TEXTS["error_open"])

    def test_the_database_allows_only_one_open_request_per_teacher(self):
        from django.db import IntegrityError, transaction

        self.new_request()
        with self.assertRaises(IntegrityError), transaction.atomic():
            model().objects.create(
                teacher=self.teacher,
                license_subscription=self.license_sub,
                requested_blocks=1,
                status="APPROVED",
            )

    def test_a_teacher_with_no_license_cannot_ask_w7(self):
        loner = CustomUser.objects.create_user(
            email="cr-loner@school.edu",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        with self.assertRaises(cr().CreditRequestError) as ctx:
            self.new_request(teacher=loner)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(str(ctx.exception), APPROVED_TEXTS["error_no_license"])

    def test_decline_emails_the_teacher_w3_and_a_second_answer_is_refused_w8(self):
        request = self.new_request()
        with patch(SEND) as send:
            with self.captureOnCommitCallbacks(execute=True):
                cr().decline_request(request.pk, self.admin, "Budget is spent")
        self.assertEqual(self.status(request), "DECLINED")
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs["recipient_list"], [self.teacher.email])
        self.assertEqual(kwargs["subject"], "Your credit request was declined")
        self.assertEqual(
            kwargs["message"],
            "Ada Admin declined your request for 2 credit blocks. Their reason: "
            '"Budget is spent".',
        )
        with self.assertRaises(cr().CreditRequestError) as ctx:
            cr().decline_request(request.pk, self.admin, "again")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(str(ctx.exception), APPROVED_TEXTS["error_answered"])
        decided = AuditEvent.objects.filter(action=getattr(AuditAction, DECIDE_ACTION))
        self.assertEqual(decided.count(), 1)

    def test_withdraw_only_while_waiting_w9(self):
        request = self.new_request()
        cr().withdraw_request(request.pk, self.teacher)
        self.assertEqual(self.status(request), "WITHDRAWN")
        second = self.new_request()
        self.approve_by_card(second)
        with self.assertRaises(cr().CreditRequestError) as ctx:
            cr().withdraw_request(second.pk, self.teacher)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(str(ctx.exception), APPROVED_TEXTS["error_withdraw"])


# --------------------------------------------------------------------------
# Approve (ordered) and the GRANTED hook
# --------------------------------------------------------------------------


class ApproveTests(Fixture):
    def setUp(self):
        self.make_school()
        self.request = self.new_request()

    def test_card_approve_is_approved_before_the_purchase_and_keyed_to_the_request(
        self,
    ):
        seen = {}
        real = LicenseSubscriptionService.initiate_overage_purchase

        def spy(*args, **kwargs):
            seen["status"] = self.status(self.request)
            seen["credit_request_id"] = kwargs.get("credit_request_id")
            return real(*args, **kwargs)

        with patch.object(
            LicenseSubscriptionService, "initiate_overage_purchase", side_effect=spy
        ):
            result, create = self.approve_by_card(self.request)
        self.assertEqual(seen["status"], "APPROVED")
        self.assertEqual(seen["credit_request_id"], self.request.pk)
        self.assertEqual(result["action"], "checkout")
        intent = LicenseOveragePurchaseIntent.objects.get()
        self.assertEqual(getattr(intent, REQUEST_ID), self.request.pk)
        self.assertEqual(intent.allocations, {str(self.teacher.id): 2})
        self.assertIsNotNone(getattr(intent, EXPIRES_AT))
        metadata = create.call_args.kwargs["metadata"]
        self.assertEqual(metadata["credit_request_id"], str(self.request.pk))
        row = model().objects.get(pk=self.request.pk)
        self.assertEqual(row.status, "APPROVED")
        self.assertEqual(row.purchase_intent_id, intent.pk)
        self.assertEqual(row.decided_by, self.admin)

    def test_a_failed_purchase_puts_the_request_back_to_waiting(self):
        import stripe

        with patch(CUSTOMER, return_value="cus_cr"), patch(
            SESSION_CREATE, side_effect=stripe.error.APIConnectionError("down")
        ):
            with self.assertRaises(ValueError):
                cr().approve_request(
                    self.request.pk,
                    self.admin,
                    payment_method="stripe",
                    success_url="https://app.test/ok",
                    cancel_url="https://app.test/cancel",
                )
        row = model().objects.get(pk=self.request.pk)
        self.assertEqual(row.status, "PENDING")
        self.assertTrue(row.note)

    def test_a_second_approve_is_refused_and_buys_nothing(self):
        self.approve_by_card(self.request)
        with self.assertRaises(cr().CreditRequestError) as ctx:
            self.approve_by_card(self.request)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(LicenseOveragePurchaseIntent.objects.count(), 1)

    def test_approve_after_the_teacher_left_closes_instead_of_buying(self):
        SchoolCreditAllocation.objects.filter(user=self.teacher).update(is_active=False)
        with self.assertRaises(cr().CreditRequestError) as ctx:
            self.approve_by_card(self.request)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(self.status(self.request), "CLOSED")
        self.assertEqual(LicenseOveragePurchaseIntent.objects.count(), 0)

    def test_only_this_licenses_admin_or_a_super_admin_can_answer(self):
        stranger = CustomUser.objects.create_user(
            email="cr-stranger@else.edu",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=School.objects.create(name="Else"),
        )
        for actor in (stranger, self.other):
            with self.assertRaises(cr().CreditRequestError) as ctx:
                cr().decline_request(self.request.pk, actor, "no")
            self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(self.status(self.request), "PENDING")

    def test_offline_approve_links_the_offline_request(self):
        with patch(SEND):
            result = cr().approve_request(
                self.request.pk, self.admin, payment_method="offline_request"
            )
        self.assertEqual(result["action"], "offline_request_pending")
        offline = LicenseOverageOfflineRequest.objects.get()
        self.assertEqual(getattr(offline, REQUEST_ID), self.request.pk)
        row = model().objects.get(pk=self.request.pk)
        self.assertEqual((row.status, row.offline_request_id), ("APPROVED", offline.pk))

    def test_super_admin_grant_now_is_granted_at_once_w2(self):
        with patch(SEND) as send:
            with self.captureOnCommitCallbacks(execute=True):
                result = cr().approve_request(self.request.pk, self.super_admin)
        self.assertEqual(result["action"], "granted")
        self.assertEqual(self.status(self.request), "GRANTED")
        self.assertEqual(self.overage_credits(self.teacher), 2 * BLOCK)
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs["recipient_list"], [self.teacher.email])
        self.assertEqual(kwargs["subject"], "Your extra credits are ready")
        self.assertEqual(
            kwargs["message"],
            "Root Team approved your request. 2 credit blocks have been added "
            "to your account.",
        )

    def test_the_paid_webhook_grants_and_emails_the_teacher(self):
        self.approve_by_card(self.request)
        intent = LicenseOveragePurchaseIntent.objects.get()
        send, refund, _ = self.complete(intent)
        self.assertEqual(self.status(self.request), "GRANTED")
        self.assertEqual(self.overage_credits(self.teacher), 2 * BLOCK)
        self.assertIn("Your extra credits are ready", subjects(send))
        refund.assert_not_called()

    def test_c8_a_crash_before_the_link_still_ends_granted(self):
        self.approve_by_card(self.request)
        model().objects.filter(pk=self.request.pk).update(purchase_intent=None)
        intent = LicenseOveragePurchaseIntent.objects.get()
        self.complete(intent)
        row = model().objects.get(pk=self.request.pk)
        self.assertEqual((row.status, row.purchase_intent_id), ("GRANTED", intent.pk))

    def test_the_grant_also_accepts_a_request_an_unlinked_sweep_put_back(self):
        self.approve_by_card(self.request)
        model().objects.filter(pk=self.request.pk).update(
            status="PENDING", purchase_intent=None
        )
        self.complete(LicenseOveragePurchaseIntent.objects.get())
        self.assertEqual(self.status(self.request), "GRANTED")

    def test_offline_approval_grants(self):
        with patch(SEND):
            cr().approve_request(
                self.request.pk, self.admin, payment_method="offline_request"
            )
            with self.captureOnCommitCallbacks(execute=True):
                LicenseSubscriptionService.approve_overage_offline_request(
                    LicenseOverageOfflineRequest.objects.get(),
                    self.super_admin,
                    amount_confirmed_cents=2 * PRICE,
                )
        self.assertEqual(self.status(self.request), "GRANTED")
        self.assertEqual(self.overage_credits(self.teacher), 2 * BLOCK)

    def test_a_teacher_skipped_at_fulfilment_closes_the_request(self):
        self.approve_by_card(self.request)
        SchoolCreditAllocation.objects.filter(user=self.teacher).update(is_active=False)
        self.complete(LicenseOveragePurchaseIntent.objects.get())
        self.assertEqual(self.status(self.request), "CLOSED")


class DuplicatePaymentTests(Fixture):
    """C-9 with r4 m3-a: SUPERSEDED, refused and refunded; FAILED untouched."""

    def setUp(self):
        self.make_school()
        self.request = self.new_request()
        self.approve_by_card(self.request, session=fake_session("cs_one"))
        self.first = LicenseOveragePurchaseIntent.objects.get()
        # A second intent for the same request (the crash-and-retry path).
        model().objects.filter(pk=self.request.pk).update(status="PENDING")
        self.approve_by_card(self.request, session=fake_session("cs_two"))
        self.second = LicenseOveragePurchaseIntent.objects.exclude(
            pk=self.first.pk
        ).get()

    def test_the_first_paid_grants_and_the_other_is_superseded_and_expired(self):
        _, _, expire = self.complete(self.first, session_id="cs_one", pi="pi_one")
        self.assertEqual(self.status(self.request), "GRANTED")
        self.second.refresh_from_db()
        self.assertEqual(
            self.second.status, getattr(LicenseOveragePurchaseStatus, SUPERSEDED)
        )
        expire.assert_called_once_with("cs_two")

    def test_a_superseded_intent_paid_anyway_is_refused_and_refunded_w5(self):
        self.complete(self.first, session_id="cs_one", pi="pi_one")
        before = self.overage_credits(self.teacher)
        send, refund, _ = self.complete(self.second, session_id="cs_two", pi="pi_two")
        self.assertEqual(self.overage_credits(self.teacher), before)
        refund.assert_called_once_with(str(self.second.pk), self.second.amount_cents)
        self.second.refresh_from_db()
        self.assertEqual(
            self.second.status, getattr(LicenseOveragePurchaseStatus, SUPERSEDED)
        )
        self.assertEqual(self.second.stripe_payment_intent_id, "pi_two")
        refund_mail = [
            c.kwargs
            for c in send.call_args_list
            if c.kwargs.get("subject") == "We refunded a duplicate payment"
        ]
        self.assertEqual(len(refund_mail), 1)
        self.assertEqual(refund_mail[0]["recipient_list"], [self.admin.email])
        self.assertEqual(
            refund_mail[0]["message"],
            "Two payments went through for the same credit request. We kept one "
            "and refunded the other ($5.98) to your card.",
        )

    def test_a_rerun_of_a_skip_failed_intent_is_not_treated_as_superseded(self):
        SchoolCreditAllocation.objects.filter(user=self.teacher).update(is_active=False)
        self.complete(self.first, session_id="cs_one", pi="pi_one")
        self.first.refresh_from_db()
        self.assertEqual(self.first.status, LicenseOveragePurchaseStatus.FAILED)
        self.assertEqual(self.status(self.request), "CLOSED")
        send, refund, _ = self.complete(self.first, session_id="cs_one", pi="pi_one")
        refund.assert_not_called()
        self.assertNotIn("We refunded a duplicate payment", subjects(send))


# --------------------------------------------------------------------------
# Back to waiting: expired checkout, sweeps, offline rejection
# --------------------------------------------------------------------------


class RevertTests(Fixture):
    def setUp(self):
        self.make_school()
        self.request = self.new_request()

    def test_checkout_expired_puts_the_request_back_w11(self):
        from billing.webhooks import _EVENT_HANDLERS

        self.approve_by_card(self.request)
        intent = LicenseOveragePurchaseIntent.objects.get()
        session = {
            "id": "cs_cr",
            "metadata": {
                "flow": "license_overage_purchase_checkout",
                "intent_id": str(intent.id),
                "credit_request_id": str(self.request.pk),
            },
        }
        _EVENT_HANDLERS["checkout.session.expired"](session)
        row = model().objects.get(pk=self.request.pk)
        self.assertEqual(row.status, "PENDING")
        self.assertEqual(row.note, APPROVED_TEXTS["note_checkout_closed"])
        intent.refresh_from_db()
        self.assertEqual(
            intent.status, getattr(LicenseOveragePurchaseStatus, SUPERSEDED)
        )

    def test_checkout_expired_is_not_auto_replayable(self):
        from billing.event_replay import AUTO_REPLAYABLE

        self.assertNotIn(
            ("checkout.session.expired", "license_overage_purchase_checkout"),
            AUTO_REPLAYABLE,
        )

    def test_the_sweep_reverts_on_the_stored_expiry(self):
        self.approve_by_card(self.request)
        LicenseOveragePurchaseIntent.objects.update(
            session_expires_at=timezone.now() - timedelta(hours=1)
        )
        cr().sweep()
        row = model().objects.get(pk=self.request.pk)
        self.assertEqual(
            (row.status, row.note), ("PENDING", APPROVED_TEXTS["note_checkout_closed"])
        )

    def test_the_sweep_leaves_a_live_checkout_alone(self):
        self.approve_by_card(self.request)
        cr().sweep()
        self.assertEqual(self.status(self.request), "APPROVED")

    def test_an_unlinked_approved_request_goes_back_after_15_minutes(self):
        model().objects.filter(pk=self.request.pk).update(
            status="APPROVED", decided_at=timezone.now() - timedelta(minutes=16)
        )
        cr().sweep()
        self.assertEqual(self.status(self.request), "PENDING")

    def test_an_unlinked_request_with_a_carrying_intent_is_linked_not_reverted(self):
        self.approve_by_card(self.request)
        intent = LicenseOveragePurchaseIntent.objects.get()
        model().objects.filter(pk=self.request.pk).update(
            purchase_intent=None, decided_at=timezone.now() - timedelta(minutes=16)
        )
        cr().sweep()
        row = model().objects.get(pk=self.request.pk)
        self.assertEqual((row.status, row.purchase_intent_id), ("APPROVED", intent.pk))

    def test_offline_rejection_puts_the_request_back_w12(self):
        with patch(SEND):
            cr().approve_request(
                self.request.pk, self.admin, payment_method="offline_request"
            )
            LicenseSubscriptionService.reject_overage_offline_request(
                LicenseOverageOfflineRequest.objects.get(),
                self.super_admin,
                "No transfer arrived",
            )
        row = model().objects.get(pk=self.request.pk)
        self.assertEqual(row.status, "PENDING")
        self.assertEqual(
            row.note,
            "Our team could not confirm the offline payment: No transfer "
            "arrived. The request is waiting again.",
        )


# --------------------------------------------------------------------------
# Closing on loss of access (A1-A7 and the catch-all)
# --------------------------------------------------------------------------


class CloseTests(Fixture):
    def setUp(self):
        self.make_school()
        self.request = self.new_request()
        self.other_request = self.new_request(teacher=self.other)

    def closed(self, request):
        row = model().objects.get(pk=request.pk)
        return row.status, row.note

    def test_a1_removing_the_teacher_closes_their_request_and_emails_w4(self):
        with patch(SEND) as send:
            with self.captureOnCommitCallbacks(execute=True):
                LicenseSubscriptionService.remove_teacher_from_license(
                    self.license_sub, self.teacher
                )
        self.assertEqual(
            self.closed(self.request), ("CLOSED", APPROVED_TEXTS["note_teacher_left"])
        )
        self.assertEqual(self.status(self.other_request), "PENDING")
        mail = [
            c.kwargs
            for c in send.call_args_list
            if c.kwargs.get("subject") == "Your credit request was closed"
        ]
        self.assertEqual(len(mail), 1)
        self.assertEqual(mail[0]["recipient_list"], [self.teacher.email])
        self.assertEqual(
            mail[0]["message"],
            "Your request for 2 credit blocks was closed because you are no "
            "longer on CR School's license.",
        )

    def assert_all_closed_as_license_ended(self):
        for request in (self.request, self.other_request):
            self.assertEqual(
                self.closed(request), ("CLOSED", APPROVED_TEXTS["note_license_ended"])
            )

    def test_a2_cancelling_an_offline_license_closes_every_open_request(self):
        LicenseSubscription.objects.filter(pk=self.license_sub.pk).update(
            billing_method="OFFLINE"
        )
        with patch(SEND):
            LicenseSubscriptionService.cancel_license_subscription(
                LicenseSubscription.objects.get(pk=self.license_sub.pk)
            )
        self.assert_all_closed_as_license_ended()

    def test_a4_renewal_with_auto_renew_off_closes(self):
        LicenseSubscription.objects.filter(pk=self.license_sub.pk).update(
            auto_renew=False, billing_cycle_end=timezone.now() - timedelta(days=1)
        )
        with patch(SEND):
            LicenseSubscriptionService.process_license_renewal(
                LicenseSubscription.objects.get(pk=self.license_sub.pk)
            )
        self.assert_all_closed_as_license_ended()

    def test_a6_stripe_subscription_deleted_closes(self):
        LicenseSubscription.objects.filter(pk=self.license_sub.pk).update(
            stripe_subscription_id="sub_cr"
        )
        with patch(SEND):
            StripeWebhookHandler.handle_subscription_deleted({"id": "sub_cr"})
        self.assert_all_closed_as_license_ended()

    def test_a6_stripe_subscription_unpaid_closes(self):
        LicenseSubscription.objects.filter(pk=self.license_sub.pk).update(
            stripe_subscription_id="sub_cr"
        )
        with patch(SEND):
            StripeWebhookHandler.handle_subscription_updated(
                {"id": "sub_cr", "status": "unpaid"}
            )
        self.assert_all_closed_as_license_ended()

    def test_the_catch_all_sweep_closes_a_request_whose_access_ended(self):
        SchoolCreditAllocation.objects.filter(user=self.teacher).update(is_active=False)
        with patch(SEND):
            cr().sweep()
        self.assertEqual(
            self.closed(self.request), ("CLOSED", APPROVED_TEXTS["note_teacher_left"])
        )
        self.assertEqual(self.status(self.other_request), "PENDING")


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


class ApiTests(Fixture):
    def setUp(self):
        self.make_school()
        self.api = APIClient()

    def as_user(self, user):
        self.api.force_authenticate(user)
        return self.api

    @OFF
    def test_the_api_is_dark_while_the_flag_is_off(self):
        response = self.as_user(self.teacher).post(BASE, {"requested_blocks": 1})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(model().objects.exists())

    @ON
    def test_a_teacher_creates_lists_and_withdraws(self):
        client = self.as_user(self.teacher)
        with patch(SEND):
            response = client.post(
                BASE, {"requested_blocks": 2, "message": "Exams"}, format="json"
            )
        self.assertEqual(response.status_code, 201, response.content)
        request_id = response.data["id"]
        again = client.post(BASE, {"requested_blocks": 1}, format="json")
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.data["detail"], APPROVED_TEXTS["error_open"])
        listed = client.get(BASE).data
        self.assertEqual([row["id"] for row in listed], [request_id])
        self.assertEqual(listed[0]["status_label"], "Waiting")
        withdrawn = client.post(f"{BASE}/{request_id}/withdraw")
        self.assertEqual(withdrawn.status_code, 200)
        self.assertEqual(withdrawn.data["status"], "WITHDRAWN")

    @ON
    def test_a_teacher_without_a_license_gets_400(self):
        loner = CustomUser.objects.create_user(
            email="cr-loner2@school.edu",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        response = self.as_user(loner).post(
            BASE, {"requested_blocks": 1}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], APPROVED_TEXTS["error_no_license"])

    @ON
    def test_admins_see_their_own_license_and_counts_super_admin_sees_all(self):
        mine = self.new_request()
        theirs = self.new_request(teacher=self.other)
        cr().decline_request(theirs.pk, self.admin, "no")
        stranger = CustomUser.objects.create_user(
            email="cr-stranger2@else.edu",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=School.objects.create(name="Else 2"),
        )
        self.assertEqual(self.as_user(stranger).get(BASE).data, [])
        admin_ids = {row["id"] for row in self.as_user(self.admin).get(BASE).data}
        self.assertEqual(admin_ids, {str(mine.pk), str(theirs.pk)})
        pending = self.as_user(self.admin).get(BASE, {"status": "PENDING"}).data
        self.assertEqual([row["id"] for row in pending], [str(mine.pk)])
        counts = self.as_user(self.admin).get(f"{BASE}/counts").data
        self.assertEqual(
            counts, {"pending": 1, "answered": 1, "granted": 0, "declined": 1}
        )
        root_ids = {row["id"] for row in self.as_user(self.super_admin).get(BASE).data}
        self.assertEqual(root_ids, {str(mine.pk), str(theirs.pk)})
        teacher_ids = {row["id"] for row in self.as_user(self.other).get(BASE).data}
        self.assertEqual(teacher_ids, {str(theirs.pk)})

    @ON
    def test_approve_and_decline_by_the_admin_and_refusals(self):
        request = self.new_request()
        denied = self.as_user(self.teacher).post(
            f"{BASE}/{request.pk}/decline", {"reason": "x"}, format="json"
        )
        self.assertIn(denied.status_code, (403, 404))
        with patch(CUSTOMER, return_value="cus_cr"), patch(
            SESSION_CREATE, return_value=fake_session()
        ):
            approved = self.as_user(self.admin).post(
                f"{BASE}/{request.pk}/approve",
                {
                    "payment_method": "stripe",
                    "success_url": "https://app.test/ok",
                    "cancel_url": "https://app.test/cancel",
                },
                format="json",
            )
        self.assertEqual(approved.status_code, 200, approved.content)
        self.assertEqual(approved.data["purchase"]["action"], "checkout")
        late = self.as_user(self.admin).post(
            f"{BASE}/{request.pk}/decline", {"reason": "x"}, format="json"
        )
        self.assertEqual(late.status_code, 409)
        self.assertEqual(late.data["detail"], APPROVED_TEXTS["error_answered"])
