"""Grant Credits Step 6 (owner, 2026-11-27, Q1 = A): super admin grant
replies carry AI credits only.

The raw-unit total_credits and used_credits leave every reply that uses
ManualGrantBucketSerializer (POST grant, a user's grant history, all grants,
grant detail); block_size is sent in AI credits (raw / 1,000); the display_*
amounts and the summary's display totals are unchanged. How a grant is made
(blocks, reason, expires_at) does not change.
"""

from django.urls import reverse
from rest_framework.test import APIClient

from billing.models import CONVERSION_FACTOR, CreditBucket, CreditBucketType
from billing.tests.test_admin_credit_grant_api import _AdminCreditBase

#: A real plan's block size (5 AI credits), so the AI-credit value is whole.
RAW_BLOCK = 5000
RAW_FIELDS = ("total_credits", "used_credits")


class GrantRepliesInAiCreditsTests(_AdminCreditBase):
    def setUp(self):
        super().setUp()
        self.plan.overage_block_size = RAW_BLOCK
        self.plan.save(update_fields=["overage_block_size"])
        self.api = APIClient()
        self.api.force_authenticate(self.superadmin)
        response = self.api.post(
            reverse("admin-credits-grant"), self.payload(blocks=3), format="json"
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.created = response.data
        self.bucket = CreditBucket.objects.get(
            bucket_type=CreditBucketType.MANUAL_GRANT, wallet__user=self.teacher
        )
        self.bucket.used_credits = 2 * CONVERSION_FACTOR
        self.bucket.save(update_fields=["used_credits"])

    def first(self, data):
        if isinstance(data, dict) and "results" in data:
            data = data["results"]
        return data[0] if isinstance(data, list) else data

    def rows(self):
        """Every grant reply that returns a bucket, re-read after the use."""
        get = self.api.get
        return {
            "history": self.first(
                get(
                    reverse("admin-credits-user-grant-history", args=[self.teacher.id])
                ).data
            ),
            "all": self.first(get(reverse("admin-credits-all-grants")).data),
            "detail": self.first(
                get(reverse("admin-credits-grant-detail", args=[self.bucket.id])).data
            ),
        }

    def test_the_raw_amounts_are_gone_from_every_grant_reply(self):
        replies = {"grant": self.created, **self.rows()}
        for name, reply in replies.items():
            row = reply
            for field in RAW_FIELDS:
                self.assertNotIn(field, row, name)

    def test_block_size_is_in_ai_credits(self):
        replies = {"grant": self.created, **self.rows()}
        for name, reply in replies.items():
            self.assertEqual(reply["block_size"], RAW_BLOCK // 1000, name)
            self.assertEqual(reply["blocks_granted"], 3, name)

    def test_the_display_amounts_are_unchanged(self):
        self.assertEqual(self.created["display_total"], 15)
        self.assertEqual(self.created["display_used"], 0)
        self.assertEqual(self.created["display_remaining"], 15)
        for name, reply in self.rows().items():
            row = reply
            self.assertEqual(row["display_total"], 15, name)
            self.assertEqual(row["display_used"], 2, name)
            self.assertEqual(row["display_remaining"], 13, name)

    def test_the_summary_has_display_totals_only(self):
        summary = self.api.get(reverse("admin-credits-grants-summary")).data
        self.assertEqual(summary["total_credits_granted_display"], 15)
        self.assertEqual(summary["total_credits_used_display"], 2)
        self.assertEqual(summary["total_credits_remaining_display"], 13)
        self.assertEqual(summary["total_blocks_granted"], 3)
        self.assertEqual(summary["grants_by_admin"][0]["total_credits_display"], 15)
        for field in RAW_FIELDS + ("total_credits_granted", "total_credits_used"):
            self.assertNotIn(field, summary)


class GrantDocsInAiCreditsTests(_AdminCreditBase):
    def test_the_documented_grant_shape_and_example(self):
        from billing.tests.test_credit_request_email_hide_approve import (
            generated_schema,
        )

        schema = generated_schema()
        props = schema["components"]["schemas"]["ManualGrantBucket"]["properties"]
        for field in RAW_FIELDS:
            self.assertNotIn(field, props)
        self.assertIn("AI credits", props["block_size"]["description"])
        self.assertNotIn("Raw", props["block_size"]["description"])
        grant = schema["paths"]["/api/v1/billing/admin/credits/grant"]["post"]
        example = grant["responses"]["201"]["content"]["application/json"]["examples"][
            "SuccessfulGrant"
        ]["value"]
        for field in RAW_FIELDS:
            self.assertNotIn(field, example)
        self.assertEqual(example["block_size"], 5)
        self.assertEqual(
            example["display_total"], example["blocks_granted"] * example["block_size"]
        )
