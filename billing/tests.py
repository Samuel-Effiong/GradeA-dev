from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from billing.models import PlanType, SubscriptionPlan, UserSubscription
from users.models import CustomUser, UserTypes


class SubscriptionPlanViewSetTests(APITestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.super_admin = CustomUser.objects.create_superuser(
            email="admin@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
        )
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.STANDARD,
            display_name="Standard Plan",
            monthly_credits=100,
            carry_over_percent=10.00,
            carry_over_max=50,
            carry_over_expiry_months=1,
            overage_block_size=10,
            overage_block_price=5.00,
            max_overage_blocks=5,
            is_active=True,
        )
        self.list_url = reverse("subscription-plan-list")
        self.detail_url = reverse(
            "subscription-plan-detail", kwargs={"pk": self.plan.id}
        )

    def test_list_plans_authenticated(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)

    def test_list_plans_unauthenticated(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_retrieve_plan_authenticated(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["display_name"], "Standard Plan")

    def test_create_plan_teacher_forbidden(self):
        self.client.force_authenticate(user=self.user)
        data = {
            "name": PlanType.PRO,
            "display_name": "Pro Plan",
            "monthly_credits": 500,
            "carry_over_percent": 20.00,
            "carry_over_max": 200,
            "carry_over_expiry_months": 2,
            "overage_block_size": 20,
            "overage_block_price": 10.00,
            "max_overage_blocks": 10,
            "is_active": True,
        }
        response = self.client.post(self.list_url, data)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_plan_super_admin_success(self):
        self.client.force_authenticate(user=self.super_admin)
        data = {
            "name": PlanType.PRO,
            "display_name": "Pro Plan",
            "monthly_credits": 500,
            "carry_over_percent": 20.00,
            "carry_over_max": 200,
            "carry_over_expiry_months": 2,
            "overage_block_size": 20,
            "overage_block_price": 10.00,
            "max_overage_blocks": 10,
            "is_active": True,
        }
        return data


class UserSubscriptionViewSetTests(APITestCase):
    def setUp(self):
        self.user_a = CustomUser.objects.create_user(
            email="user_a@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        self.user_b = CustomUser.objects.create_user(
            email="user_b@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        self.super_admin = CustomUser.objects.create_superuser(
            email="admin@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
            is_active=True,
        )
        self.student = CustomUser.objects.create_user(
            email="student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            is_active=True,
        )
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.STANDARD,
            display_name="Standard Plan",
            monthly_credits=100,
            carry_over_percent=10.00,
            carry_over_max=50,
            carry_over_expiry_months=1,
            is_active=True,
        )
        self.sub_a = UserSubscription.objects.create(
            user=self.user_a,
            plan=self.plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timezone.timedelta(days=30),
            is_active=True,
        )
        self.sub_b = UserSubscription.objects.create(
            user=self.user_b,
            plan=self.plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timezone.timedelta(days=30),
            is_active=True,
        )
        self.list_url = reverse("user-subscription-list")

    def test_list_subscriptions_own_only(self):
        self.client.force_authenticate(user=self.user_a)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Should only see 1 subscription (own)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(str(response.data["results"][0]["user"]), str(self.user_a.id))

    def test_list_subscriptions_superadmin_all(self):
        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Should see 2 subscriptions
        self.assertEqual(len(response.data["results"]), 2)

    def test_create_subscription_allowed_for_teacher(self):
        self.client.force_authenticate(user=self.user_a)
        data = {
            "user": self.user_a.id,
            "plan": self.plan.id,
            "billing_cycle_start": timezone.now(),
            "billing_cycle_end": timezone.now() + timezone.timedelta(days=30),
        }
        response = self.client.post(self.list_url, data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_subscription_forbidden_for_student(self):
        self.client.force_authenticate(user=self.student)
        data = {
            "user": self.student.id,
            "plan": self.plan.id,
            "billing_cycle_start": timezone.now(),
            "billing_cycle_end": timezone.now() + timezone.timedelta(days=30),
        }
        response = self.client.post(self.list_url, data)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
