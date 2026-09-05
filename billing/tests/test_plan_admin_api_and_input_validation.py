"""
billing/tests/test_plan_admin_api_and_input_validation.py
=========================================================
Two things the existing suite does not cover.

1. THE PLAN-CREATION HAPPY PATH IS NOT ACTUALLY TESTED
------------------------------------------------------
`tests.py::SubscriptionPlanViewSetTests.test_create_plan_super_admin_success`
builds a payload dict and then, instead of POSTing it, does:

    return data

It never calls the API and asserts nothing. It passes unconditionally, and
would keep passing if plan creation 500'd, silently dropped `monthly_credits`,
or let a teacher through. Its sibling `test_create_plan_teacher_forbidden`
is real, so the endpoint's DENY path is covered while the ALLOW path — the
one that writes a row defining how many credits every subscriber gets — is
not covered at all.

That existing test is left in place (this pass does not rewrite other
people's tests); the coverage it was supposed to provide is added here,
asserting the persisted values rather than just a 201.

2. ADVERSARIAL INPUT AT THE BILLING API BOUNDARY
------------------------------------------------
No billing test posts a wrong content-type, an oversized field, or a
payload missing a required key. These are the shapes a real client sends
when it is broken or hostile, and "500 with a stack trace" is a different
outcome from "400 with a validation error" — only the second is safe.

Every case here asserts the status code AND that no partial row was
written: a rejected create that still persists something is the failure
mode worth catching.
"""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from billing.models import PlanCategory, PlanTier, PlanType, SubscriptionPlan
from users.models import UserTypes

CustomUser = get_user_model()


def valid_plan_payload(**overrides):
    payload = {
        "name": PlanType.PRO,
        "display_name": "Pro Plan",
        "category": PlanCategory.INDIVIDUAL,
        "tier": PlanTier.PRO,
        "monthly_credits": 500,
        "carry_over_percent": 20.00,
        "carry_over_max": 200,
        "carry_over_expiry_months": 2,
        "overage_block_size": 20,
        "overage_block_price": 10.00,
        "max_overage_blocks": 10,
        "is_active": True,
    }
    payload.update(overrides)
    return payload


class _PlanApiBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse("subscription-plan-list")
        self.super_admin = CustomUser.objects.create_user(
            email="plan.admin@gmail.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
            is_active=True,
            is_staff=True,
            is_superuser=True,
        )
        self.teacher = CustomUser.objects.create_user(
            email="plan.teacher@gmail.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )

    def as_admin(self):
        self.client.force_authenticate(user=self.super_admin)


class PlanCreationHappyPathTests(_PlanApiBase):
    """The coverage `test_create_plan_super_admin_success` never provided."""

    def test_a_superadmin_can_create_a_plan_and_the_values_persist(self):
        self.as_admin()
        before = SubscriptionPlan.objects.count()

        response = self.client.post(self.url, valid_plan_payload(), format="json")

        self.assertIn(
            response.status_code,
            (200, 201),
            f"plan creation failed: {getattr(response, 'data', response.content)}",
        )
        self.assertEqual(SubscriptionPlan.objects.count(), before + 1)

        plan = SubscriptionPlan.objects.exclude(
            pk__in=SubscriptionPlan.objects.none()
        ).get(display_name="Pro Plan")
        # Assert the stored values, not merely that a row appeared: a
        # serializer that dropped monthly_credits would still return 201.
        self.assertEqual(plan.name, PlanType.PRO)
        self.assertEqual(plan.monthly_credits, 500)
        self.assertEqual(plan.overage_block_size, 20)
        self.assertEqual(plan.max_overage_blocks, 10)
        self.assertTrue(plan.is_active)

    def test_the_created_plan_is_immediately_listable(self):
        self.as_admin()
        self.client.post(self.url, valid_plan_payload(), format="json")

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        names = {row["display_name"] for row in response.json()["data"]["results"]}
        self.assertIn("Pro Plan", names)

    def test_a_teacher_still_cannot_create_a_plan(self):
        """The deny path, re-asserted alongside the allow path so the pair
        cannot silently collapse into 'everyone allowed'."""
        self.client.force_authenticate(user=self.teacher)
        before = SubscriptionPlan.objects.count()

        response = self.client.post(self.url, valid_plan_payload(), format="json")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(SubscriptionPlan.objects.count(), before)


class PlanCreationAdversarialInputTests(_PlanApiBase):
    def test_a_missing_required_field_is_a_400_not_a_500(self):
        """
        `name` is the required identity field (CharField, choices, unique,
        no default). NOT `display_name` — that one is null=True/blank=True
        and is genuinely optional, which the next test pins so this pair
        documents the real contract rather than an assumed one.
        """
        self.as_admin()
        payload = valid_plan_payload()
        del payload["name"]
        before = SubscriptionPlan.objects.count()

        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            SubscriptionPlan.objects.count(),
            before,
            "a rejected create must not leave a partial row behind",
        )

    def test_display_name_is_genuinely_optional(self):
        """
        Pinned deliberately: `display_name` is null=True/blank=True on the
        model, so omitting it is ACCEPTED. Recorded here so nobody later
        "fixes" a passing 201 into a 400 on the assumption it was a bug.
        """
        self.as_admin()
        payload = valid_plan_payload()
        del payload["display_name"]

        response = self.client.post(self.url, payload, format="json")

        self.assertIn(response.status_code, (200, 201))
        self.assertTrue(SubscriptionPlan.objects.filter(name=PlanType.PRO).exists())

    def test_a_wrong_content_type_is_rejected_cleanly(self):
        """
        A client sending form bytes under a JSON content-type (or the other
        way round) must get a 4xx, never an unhandled parser exception.
        """
        self.as_admin()
        before = SubscriptionPlan.objects.count()

        response = self.client.post(
            self.url,
            data=json.dumps(valid_plan_payload()),
            content_type="text/plain",
        )

        self.assertGreaterEqual(response.status_code, 400)
        self.assertLess(response.status_code, 500)
        self.assertEqual(SubscriptionPlan.objects.count(), before)

    def test_malformed_json_body_is_rejected_cleanly(self):
        self.as_admin()
        before = SubscriptionPlan.objects.count()

        response = self.client.post(
            self.url,
            data=b'{"display_name": "unterminated',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(SubscriptionPlan.objects.count(), before)

    def test_an_oversized_display_name_is_rejected_not_truncated(self):
        """
        Silent truncation would put a different value in the database than
        the caller sent — for a billing catalogue row, that is a defect.
        """
        self.as_admin()
        before = SubscriptionPlan.objects.count()

        response = self.client.post(
            self.url,
            valid_plan_payload(display_name="X" * 10_000),
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(SubscriptionPlan.objects.count(), before)

    def test_a_negative_monthly_credits_is_rejected(self):
        """A negative allocation would mint debt into every new wallet."""
        self.as_admin()
        before = SubscriptionPlan.objects.count()

        response = self.client.post(
            self.url, valid_plan_payload(monthly_credits=-500), format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(SubscriptionPlan.objects.count(), before)

    def test_a_non_numeric_monthly_credits_is_rejected(self):
        self.as_admin()
        before = SubscriptionPlan.objects.count()

        response = self.client.post(
            self.url,
            valid_plan_payload(monthly_credits="not a number"),
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(SubscriptionPlan.objects.count(), before)

    def test_an_unknown_plan_name_choice_is_rejected(self):
        self.as_admin()
        before = SubscriptionPlan.objects.count()

        response = self.client.post(
            self.url, valid_plan_payload(name="NOT_A_REAL_PLAN"), format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(SubscriptionPlan.objects.count(), before)

    def test_an_empty_body_is_rejected(self):
        self.as_admin()
        before = SubscriptionPlan.objects.count()

        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(SubscriptionPlan.objects.count(), before)
