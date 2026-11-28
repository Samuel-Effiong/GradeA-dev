"""Purchase-overage answers a credit request (beta bug fix, owner 2026-11-28).

The frontend answers a teacher's credit request through
POST license-subscriptions/{id}/purchase-overage/ (approve is hidden from the
docs). With credit_request_id the call takes the same path as approve: the
request is APPROVED, linked to the purchase, GRANTED when the credits land,
and back to waiting if the purchase fails. Without it nothing changes.
"""

from unittest.mock import patch

from django.urls import reverse
from rest_framework.test import APIClient

from billing.models import (
    LicenseOverageOfflineRequest,
    LicenseOveragePurchaseIntent,
    LicenseSubscription,
)
from billing.tests.test_credit_requests import (
    BLOCK,
    CUSTOMER,
    OFF,
    ON,
    SEND,
    SESSION_CREATE,
    Fixture,
    fake_session,
    model,
    subjects,
)
from classrooms.models import School
from users.models import CustomUser, UserTypes

#: Owner-approved wording (2026-11-28).
WRONG_ALLOCATION = (
    "To answer a credit request, give all the blocks to the teacher who asked."
)
REQUESTS_OFF = "Credit requests are turned off, so this purchase can't answer one."
ANSWERED = "This request has already been answered."
HELP_TEXT = (
    "Optional. A teacher's waiting credit request that this purchase answers. "
    "Give all the blocks to that teacher."
)
DESCRIPTION_LINE = (
    "- **Answering a credit request**: send `credit_request_id` with "
    "`allocations` naming only the teacher who asked. The request is approved "
    "now and shows Credits added once the credits are granted (at once for a "
    "super admin, after payment for Stripe, after approval for an offline "
    "request). If the purchase fails, the request goes back to waiting."
)
URLS = {"success_url": "https://app.test/ok", "cancel_url": "https://app.test/cancel"}


class PurchaseOverageAnswersACreditRequestTests(Fixture):
    def setUp(self):
        self.make_school()
        self.request = self.new_request()
        self.api = APIClient()

    def url(self, license_sub=None):
        return reverse(
            "license-subscription-purchase-overage",
            args=[(license_sub or self.license_sub).pk],
        )

    def body(self, blocks=2, teacher=None, with_request=True, **extra):
        body = {
            "total_blocks": blocks,
            "allocations": {str((teacher or self.teacher).id): blocks},
            **URLS,
            **extra,
        }
        if with_request:
            body["credit_request_id"] = str(self.request.pk)
        return body

    def post(self, user, body, license_sub=None):
        self.api.force_authenticate(user)
        with patch(SEND), patch(CUSTOMER, return_value="cus_cr"), patch(
            SESSION_CREATE, return_value=fake_session()
        ):
            with self.captureOnCommitCallbacks(execute=True):
                return self.api.post(self.url(license_sub), body, format="json")

    def row(self):
        return model().objects.get(pk=self.request.pk)

    @ON
    def test_stripe_purchase_approves_links_and_the_webhook_grants(self):
        response = self.post(self.admin, self.body(blocks=3))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["action"], "checkout")
        self.assertEqual(
            response.data["message"],
            "Taking you to secure checkout to buy your extra credits.",
        )
        intent = LicenseOveragePurchaseIntent.objects.get()
        self.assertEqual(intent.credit_request_id, self.request.pk)
        self.assertEqual(intent.allocations, {str(self.teacher.id): 3})
        row = self.row()
        self.assertEqual(
            (row.status, row.purchase_intent_id, row.decided_by),
            ("APPROVED", intent.pk, self.admin),
        )
        send, refund, _ = self.complete(intent)
        self.assertEqual(self.row().status, "GRANTED")
        self.assertEqual(self.overage_credits(self.teacher), 3 * BLOCK)
        self.assertIn("Your extra credits are ready", subjects(send))
        refund.assert_not_called()

    @ON
    def test_offline_purchase_links_the_offline_request(self):
        response = self.post(self.admin, self.body(payment_method="offline_request"))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["action"], "offline_request_pending")
        offline = LicenseOverageOfflineRequest.objects.get()
        self.assertEqual(offline.credit_request_id, self.request.pk)
        row = self.row()
        self.assertEqual((row.status, row.offline_request_id), ("APPROVED", offline.pk))

    @ON
    def test_a_super_admin_purchase_is_granted_at_once(self):
        response = self.post(self.super_admin, self.body())
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["action"], "granted")
        self.assertEqual(self.row().status, "GRANTED")
        self.assertEqual(self.overage_credits(self.teacher), 2 * BLOCK)

    @ON
    def test_a_failed_purchase_puts_the_request_back_to_waiting(self):
        import stripe

        self.api.force_authenticate(self.admin)
        with patch(SEND), patch(CUSTOMER, return_value="cus_cr"), patch(
            SESSION_CREATE, side_effect=stripe.error.APIConnectionError("down")
        ):
            response = self.api.post(self.url(), self.body(), format="json")
        self.assertEqual(response.status_code, 400, response.content)
        row = self.row()
        self.assertEqual(row.status, "PENDING")
        self.assertTrue(row.note)
        self.assertIsNone(row.purchase_intent_id)

    @ON
    def test_a_request_on_another_license_is_not_found(self):
        school = School.objects.create(name="Other CR School")
        admin = CustomUser.objects.create_user(
            email="cr-other-admin@school.edu",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=school,
        )
        other_license = LicenseSubscription.objects.create(
            school=school,
            admin_user=admin,
            plan=self.plan,
            billing_cycle_start=self.license_sub.billing_cycle_start,
            billing_cycle_end=self.license_sub.billing_cycle_end,
            is_active=True,
            max_seats=3,
        )
        response = self.post(self.super_admin, self.body(), license_sub=other_license)
        self.assertEqual(response.status_code, 404, response.content)
        self.assertEqual(response.data["detail"], "Not found.")
        self.assertEqual(self.row().status, "PENDING")
        self.assertEqual(self.overage_credits(self.teacher), 0)

    @ON
    def test_an_answered_request_is_refused(self):
        self.post(self.admin, self.body())
        response = self.post(self.admin, self.body())
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.data["detail"], ANSWERED)
        self.assertEqual(LicenseOveragePurchaseIntent.objects.count(), 1)

    @ON
    def test_the_wrong_teacher_or_more_than_one_allocation_is_refused(self):
        wrong_teacher = self.body(teacher=self.other)
        both = self.body()
        both["total_blocks"] = 3
        both["allocations"] = {str(self.teacher.id): 2, str(self.other.id): 1}
        for body in (wrong_teacher, both):
            response = self.post(self.admin, body)
            self.assertEqual(response.status_code, 400, response.content)
            self.assertEqual(response.data["detail"], WRONG_ALLOCATION)
        self.assertEqual(self.row().status, "PENDING")
        self.assertEqual(LicenseOveragePurchaseIntent.objects.count(), 0)

    @OFF
    def test_a_credit_request_id_while_requests_are_off_is_refused(self):
        response = self.post(self.admin, self.body())
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.data["detail"], REQUESTS_OFF)
        self.assertEqual(self.row().status, "PENDING")
        self.assertEqual(LicenseOveragePurchaseIntent.objects.count(), 0)

    @ON
    def test_without_a_credit_request_id_nothing_changes(self):
        response = self.post(self.super_admin, self.body(with_request=False))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["action"], "granted")
        self.assertEqual(
            response.data["message"], "Granted 2 overage block(s) across 1 teacher(s)."
        )
        self.assertEqual(self.overage_credits(self.teacher), 2 * BLOCK)
        self.assertEqual(self.row().status, "PENDING")
        stripe_reply = self.post(self.admin, self.body(with_request=False))
        self.assertEqual(stripe_reply.data["action"], "checkout")
        self.assertIsNone(LicenseOveragePurchaseIntent.objects.get().credit_request_id)
        self.assertEqual(self.row().status, "PENDING")

    @ON
    def test_a_teacher_still_cannot_call_it(self):
        response = self.post(self.teacher, self.body())
        self.assertIn(response.status_code, (403, 404), response.content)
        self.assertEqual(self.row().status, "PENDING")
        self.assertEqual(LicenseOveragePurchaseIntent.objects.count(), 0)


class PurchaseOverageDocsTests(Fixture):
    def test_the_docs_describe_credit_request_id(self):
        from billing.tests.test_credit_request_email_hide_approve import (
            generated_schema,
        )

        schema = generated_schema()
        field = schema["components"]["schemas"]["PurchaseLicenseOverageRequest"][
            "properties"
        ]["credit_request_id"]
        self.assertEqual(field["description"], HELP_TEXT)
        self.assertEqual(field["format"], "uuid")
        operation = next(
            item["post"]
            for path, item in schema["paths"].items()
            if "license-subscriptions" in path and "purchase-overage" in path
        )
        self.assertIn(DESCRIPTION_LINE, operation["description"])
