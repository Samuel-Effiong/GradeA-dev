"""A credit request records the blocks the admin approved (beta bug fix,
owner 2026-11-28).

Before: only requested_blocks, so asked 2 and given 5 still read "2, Credits
added". Now approved_blocks is set when the request moves to APPROVED (approve
or purchase-overage), cleared whenever it goes back to waiting, never set by
decline or withdraw, and old answered rows are filled from their linked
purchase where that teacher's allocation is on record.
"""

import importlib
from unittest.mock import patch

import stripe
from django.apps import apps as django_apps
from django.urls import reverse
from rest_framework.test import APIClient

from billing.models import LicenseOveragePurchaseIntent
from billing.tests.test_credit_requests import (
    BASE,
    CUSTOMER,
    ON,
    SEND,
    SESSION_CREATE,
    Fixture,
    cr,
    fake_session,
    model,
)

MIGRATION = "billing.migrations.0093_creditrequest_approved_blocks"
#: W-E, draft for the owner (2026-11-28).
HELP_TEXT = "Blocks the admin approved. Empty until the request is approved."
URLS = {"success_url": "https://app.test/ok", "cancel_url": "https://app.test/cancel"}


class ApprovedBlocksTests(Fixture):
    def setUp(self):
        self.make_school()
        self.request = self.new_request(blocks=2)

    def approved(self):
        return model().objects.get(pk=self.request.pk).approved_blocks

    def card(self, **kwargs):
        with patch(CUSTOMER, return_value="cus_cr"), patch(
            SESSION_CREATE, return_value=fake_session()
        ):
            return cr().approve_request(
                self.request.pk, self.admin, payment_method="stripe", **URLS, **kwargs
            )

    def test_a_new_request_has_none(self):
        self.assertIsNone(self.approved())

    def test_approve_with_5_on_a_2_block_request_stores_5(self):
        self.card(blocks=5)
        row = model().objects.get(pk=self.request.pk)
        self.assertEqual((row.status, row.requested_blocks), ("APPROVED", 2))
        self.assertEqual(row.approved_blocks, 5)

    def test_approve_without_blocks_stores_the_blocks_asked_for(self):
        self.card()
        self.assertEqual(self.approved(), 2)

    @ON
    def test_purchase_overage_with_the_request_and_1_block_stores_1(self):
        api = APIClient()
        api.force_authenticate(self.admin)
        with patch(SEND), patch(CUSTOMER, return_value="cus_cr"), patch(
            SESSION_CREATE, return_value=fake_session()
        ):
            response = api.post(
                reverse(
                    "license-subscription-purchase-overage",
                    args=[self.license_sub.pk],
                ),
                {
                    "total_blocks": 1,
                    "allocations": {str(self.teacher.id): 1},
                    "credit_request_id": str(self.request.pk),
                    **URLS,
                },
                format="json",
            )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.approved(), 1)

    def test_a_super_admin_instant_grant_stores_the_number(self):
        with patch(SEND):
            with self.captureOnCommitCallbacks(execute=True):
                cr().approve_request(self.request.pk, self.super_admin, blocks=3)
        row = model().objects.get(pk=self.request.pk)
        self.assertEqual((row.status, row.approved_blocks), ("GRANTED", 3))
        self.assertIsNone(row.purchase_intent_id)

    def test_a_failed_purchase_clears_it_and_the_request_waits(self):
        with patch(CUSTOMER, return_value="cus_cr"), patch(
            SESSION_CREATE, side_effect=stripe.error.APIConnectionError("down")
        ):
            with self.assertRaises(ValueError):
                cr().approve_request(
                    self.request.pk,
                    self.admin,
                    blocks=5,
                    payment_method="stripe",
                    **URLS,
                )
        row = model().objects.get(pk=self.request.pk)
        self.assertEqual((row.status, row.approved_blocks), ("PENDING", None))

    def test_an_expired_checkout_clears_it_and_the_request_waits(self):
        from billing.webhooks import _EVENT_HANDLERS

        self.card(blocks=4)
        intent = LicenseOveragePurchaseIntent.objects.get()
        _EVENT_HANDLERS["checkout.session.expired"](
            {
                "id": "cs_cr",
                "metadata": {
                    "flow": "license_overage_purchase_checkout",
                    "intent_id": str(intent.id),
                    "credit_request_id": str(self.request.pk),
                },
            }
        )
        row = model().objects.get(pk=self.request.pk)
        self.assertEqual((row.status, row.approved_blocks), ("PENDING", None))

    def test_decline_and_withdraw_leave_it_empty(self):
        with patch(SEND):
            with self.captureOnCommitCallbacks(execute=True):
                cr().decline_request(self.request.pk, self.admin, "Budget is spent")
        self.assertIsNone(self.approved())
        second = self.new_request(blocks=3)
        cr().withdraw_request(second.pk, self.teacher)
        row = model().objects.get(pk=second.pk)
        self.assertEqual((row.status, row.approved_blocks), ("WITHDRAWN", None))


@ON
class ApprovedBlocksInRepliesTests(Fixture):
    def setUp(self):
        self.make_school()
        self.api = APIClient()

    def test_create_and_list_replies_include_it(self):
        self.api.force_authenticate(self.teacher)
        with patch(SEND):
            created = self.api.post(
                BASE, {"requested_blocks": 2, "message": "Exams"}, format="json"
            )
        self.assertEqual(created.status_code, 201, created.content)
        self.assertIn("approved_blocks", created.data)
        self.assertIsNone(created.data["approved_blocks"])
        model().objects.filter(pk=created.data["id"]).update(approved_blocks=5)
        self.api.force_authenticate(self.admin)
        listed = self.api.get(BASE).data
        self.assertEqual(
            [(row["requested_blocks"], row["approved_blocks"]) for row in listed],
            [(2, 5)],
        )

    def test_it_is_read_only(self):
        self.api.force_authenticate(self.teacher)
        with patch(SEND):
            created = self.api.post(
                BASE, {"requested_blocks": 2, "approved_blocks": 9}, format="json"
            )
        self.assertEqual(created.status_code, 201, created.content)
        self.assertIsNone(model().objects.get(pk=created.data["id"]).approved_blocks)


class ApprovedBlocksBackfillTests(Fixture):
    """Old answered rows: filled from the linked purchase's allocation for
    the teacher who asked; left empty when nothing exact is on record."""

    def setUp(self):
        self.make_school()

    def backfill(self):
        importlib.import_module(MIGRATION).backfill(django_apps, None)

    def as_old_row(self, request):
        model().objects.filter(pk=request.pk).update(approved_blocks=None)

    def test_card_offline_and_unlinked_rows(self):
        card = self.new_request(blocks=2)
        with patch(CUSTOMER, return_value="cus_cr"), patch(
            SESSION_CREATE, return_value=fake_session()
        ):
            cr().approve_request(
                card.pk, self.admin, blocks=5, payment_method="stripe", **URLS
            )
        self.complete(LicenseOveragePurchaseIntent.objects.get())
        offline = self.new_request(teacher=self.other, blocks=1)
        with patch(SEND):
            cr().approve_request(
                offline.pk, self.admin, blocks=3, payment_method="offline_request"
            )
        teacher3 = self.make_teacher("3", "Thea", "Third")
        instant = self.new_request(teacher=teacher3, blocks=2)
        with patch(SEND):
            with self.captureOnCommitCallbacks(execute=True):
                cr().approve_request(instant.pk, self.super_admin, blocks=4)
        waiting = self.new_request(blocks=2)
        for row in (card, offline, instant):
            self.as_old_row(row)

        self.backfill()

        def stored(row):
            got = model().objects.get(pk=row.pk)
            return got.status, got.approved_blocks

        self.assertEqual(stored(card), ("GRANTED", 5))
        self.assertEqual(stored(offline), ("APPROVED", 3))
        # A super admin's instant grant links no purchase: nothing exact.
        self.assertEqual(stored(instant), ("GRANTED", None))
        self.assertEqual(stored(waiting), ("PENDING", None))

    def test_a_linked_purchase_without_the_teacher_is_left_empty(self):
        request = self.new_request(blocks=2)
        with patch(CUSTOMER, return_value="cus_cr"), patch(
            SESSION_CREATE, return_value=fake_session()
        ):
            cr().approve_request(
                request.pk, self.admin, payment_method="stripe", **URLS
            )
        LicenseOveragePurchaseIntent.objects.update(allocations={str(self.other.id): 2})
        self.as_old_row(request)
        self.backfill()
        self.assertIsNone(model().objects.get(pk=request.pk).approved_blocks)

    def test_a_value_already_set_is_kept(self):
        request = self.new_request(blocks=2)
        with patch(CUSTOMER, return_value="cus_cr"), patch(
            SESSION_CREATE, return_value=fake_session()
        ):
            cr().approve_request(
                request.pk, self.admin, blocks=5, payment_method="stripe", **URLS
            )
        LicenseOveragePurchaseIntent.objects.update(
            allocations={str(self.teacher.id): 7}
        )
        self.backfill()
        self.assertEqual(model().objects.get(pk=request.pk).approved_blocks, 5)


class ApprovedBlocksDocsTests(Fixture):
    def test_the_docs_describe_approved_blocks(self):
        from billing.tests.test_credit_request_email_hide_approve import (
            generated_schema,
        )

        field = generated_schema()["components"]["schemas"]["CreditRequest"][
            "properties"
        ]["approved_blocks"]
        self.assertEqual(field["description"], HELP_TEXT)
        self.assertTrue(field.get("readOnly"))
        self.assertTrue(field.get("nullable"))
