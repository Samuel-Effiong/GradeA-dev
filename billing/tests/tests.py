import threading
import unittest
from datetime import timedelta

from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from billing.context import clear_license_invitation_context
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


class ConcurrentRegistrationTest(TransactionTestCase):
    # Real OS threads (test_concurrent_registration below) each open their
    # own DB connection. Under plain TestCase, setUp()'s writes live in
    # the outer per-test transaction and are invisible to those other
    # connections until rollback — the TRIAL plan created in setUp() would
    # never actually be visible to either thread, so automatic trial
    # activation would silently no-op for both. TransactionTestCase commits
    # setUp() for real, so both threads see it.
    def setUp(self):
        # activate_automatic_free_trial() (fired on every CustomUser
        # creation via users/signals.py) requires a TRIAL-tier INDIVIDUAL
        # plan to exist — without it, trial signup silently no-ops (caught
        # and logged, not raised) and every test below sees zero
        # subscriptions instead of the trial they expect.
        SubscriptionPlan.objects.create(
            name=PlanType.TRIAL,
            display_name="Free Trial",
            category="INDIVIDUAL",
            tier="TRIAL",
            monthly_credits=0,
            is_active=True,
        )

    def test_concurrent_registration(self):
        def create_user():
            CustomUser.objects.create_user(
                email="concurrent@test.com",
                password="test",  # pragma: allowlist secret
                user_type="TEACHER",
            )

        t1 = threading.Thread(target=create_user)
        t2 = threading.Thread(target=create_user)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        user = CustomUser.objects.get(email="concurrent@test.com")
        trials = user.subscriptions.filter(is_trial=True)
        assert trials.count() == 1, (
            f"Should have exactly one trial, got {trials.count()}: "
            f"{list(user.subscriptions.values('id', 'is_trial', 'is_active', 'plan_id'))}"
        )

    def test_trial_cannot_be_activated_twice(self):
        user = CustomUser.objects.create_user(
            email="double@test.com",
            password="test",  # pragma: allowlist secret
            user_type="TEACHER",
        )
        # User now has a trial (created by signal)

        # Attempt to activate again
        from billing.services import SubscriptionService

        with self.assertRaises(ValueError) as cm:
            SubscriptionService.activate_automatic_free_trial(user)

        assert "already used" in str(cm.exception).lower()
        # Verify still only one trial
        assert user.subscriptions.filter(is_trial=True).count() == 1

    def test_trial_activation_with_existing_wallet(self):
        user = CustomUser.objects.create_user(
            email="wallet@test.com",
            password="test",  # pragma: allowlist secret
            user_type="TEACHER",
        )

        # After signal fires, user should have wallet
        assert hasattr(user, "credit_wallet")
        original_wallet_id = user.credit_wallet.id

        # Manually trigger signal again (simulate retry)
        from users.signals import create_default_settings_and_wallet

        create_default_settings_and_wallet(
            sender=CustomUser, instance=user, created=True
        )

        # Same wallet should be reused
        user.refresh_from_db()
        assert user.credit_wallet.id == original_wallet_id

    def test_trial_bucket_creation_failure_rolls_back(self):
        from unittest.mock import patch

        from billing.models import CreditBucket

        with patch.object(
            CreditBucket.objects, "create", side_effect=Exception("DB error")
        ):
            user = CustomUser.objects.create_user(
                email="fail@test.com",
                password="test",  # pragma: allowlist secret
                user_type="TEACHER",
            )

            # Signal caught the exception, user exists but no trial
            assert user.subscriptions.count() == 0
            # Wallet exists (created before error)
            assert user.credit_wallet is not None

    def test_student_does_not_get_trial(self):
        student = CustomUser.objects.create_user(
            email="student@test.com",
            password="test",  # pragma: allowlist secret
            user_type="STUDENT",
        )

        assert student.subscriptions.count() == 0
        assert not student.is_beta_eligible()

    def test_teacher_gets_trial(self):
        teacher = CustomUser.objects.create_user(
            email="teacher@test.com",
            password="test",  # pragma: allowlist secret
            user_type="TEACHER",
        )

        assert teacher.subscriptions.count() == 1
        assert teacher.subscriptions.first().is_trial

    def test_license_invited_user_does_not_get_trial(self):
        from billing.context import set_license_invitation_context

        set_license_invitation_context(True)
        try:
            user = CustomUser.objects.create_user(
                email="invited@test.com",
                password="test",  # pragma: allowlist secret
                user_type="TEACHER",
            )

            # Should have no personal subscription (uses license)
            # But should have wallet
            assert user.subscriptions.count() == 0
            assert user.credit_wallet is not None
        finally:
            clear_license_invitation_context()

    def test_trial_expires_after_14_days(self):
        from freezegun import freeze_time

        user = CustomUser.objects.create_user(
            email="expire@test.com",
            password="test",  # pragma: allowlist secret
            user_type="TEACHER",
        )

        trial = user.subscriptions.first()
        assert trial.is_active

        # Fast-forward 15 days
        with freeze_time(trial.trial_end + timedelta(days=1)):
            from billing.tasks import expire_active_trials

            expire_active_trials()

        # Refresh from DB
        trial.refresh_from_db()
        assert not trial.is_active

        # Check ledger has EXPIRE entry
        from billing.models import CreditLedgerType

        ledger = trial.user.credit_ledgers.filter(
            ledger_type=CreditLedgerType.EXPIRE
        ).first()
        assert ledger is not None

    def test_trial_expires_when_credits_exhausted(self):
        user = CustomUser.objects.create_user(
            email="exhaust@test.com",
            password="test",  # pragma: allowlist secret
            user_type="TEACHER",
        )

        trial = user.subscriptions.first()
        wallet = user.credit_wallet

        # Consume all credits
        from billing.errors import InsufficientCreditsError

        try:
            wallet.consume_credits(wallet.total_remaining_credits())
        except InsufficientCreditsError:
            pass  # Expected if there's a cap

        # Exhaust remaining
        bucket = wallet.buckets.filter(bucket_type="TRIAL").first()
        bucket.used_credits = bucket.total_credits
        bucket.save()

        # Run expiry task
        from billing.tasks import expire_active_trials

        expire_active_trials()

        # Trial should be inactive
        trial.refresh_from_db()
        assert not trial.is_active

    def test_expired_trial_task_handles_missing_wallet(self):
        user = CustomUser.objects.create_user(
            email="nowallet@test.com",
            password="test",  # pragma: allowlist secret
            user_type="TEACHER",
        )

        # Delete wallet (simulate corruption)
        user.credit_wallet.delete()

        # Task should not crash
        from billing.tasks import expire_active_trials

        result = expire_active_trials()

        # Should mention failed count
        assert "failed" in result.lower()

    @unittest.skip(
        "SubscriptionService.convert_trial_to_paid no longer exists (trial "
        "conversion moved to a Stripe-checkout-based flow) - this test needs "
        "to be rewritten against the current conversion path before "
        "re-enabling."
    )
    def test_concurrent_trial_expiration_and_conversion(self):
        import threading

        user = CustomUser.objects.create_user(
            email="concurrent_expire@test.com",
            password="test",  # pragma: allowlist secret
            user_type="TEACHER",
        )

        paid_plan = SubscriptionPlan.objects.first()
        errors = []

        def expire():
            try:
                from billing.tasks import expire_active_trials

                expire_active_trials()
            except Exception as e:
                errors.append(("expire", e))

        def convert():
            try:
                from billing.services import SubscriptionService

                SubscriptionService.convert_trial_to_paid(  # type: ignore[attr-defined]
                    user, paid_plan
                )
            except Exception as e:
                errors.append(("convert", e))

        t1 = threading.Thread(target=expire)
        t2 = threading.Thread(target=convert)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # At most one operation should complete
        # (or both complete, but subscription ends up in valid state)
        user.refresh_from_db()
        active_subs = user.subscriptions.filter(is_active=True).count()
        assert active_subs in [0, 1]  # Either expired or converted

    def test_settings_creation_failure_doesnt_block_trial(self):
        from unittest.mock import patch

        from users.models import Settings

        with patch.object(
            Settings.objects, "get_or_create", side_effect=Exception("DB error")
        ):
            user = CustomUser.objects.create_user(
                email="nosettings@test.com",
                password="test",  # pragma: allowlist secret
                user_type="TEACHER",
            )

            # Trial should still be created
            assert user.subscriptions.filter(is_trial=True).exists()

    def test_deleted_user_has_no_trial_access(self):
        user = CustomUser.objects.create_user(
            email="delete@test.com",
            password="test",  # pragma: allowlist secret
            user_type="TEACHER",
        )

        # Deactivate user
        user.is_active = False
        user.save()

        # Check access
        from billing.access_control import can_user_access_ai

        can_access, reason = can_user_access_ai(user)
        assert not can_access
        assert reason is not None and "inactive" in reason.lower()

    # def test_celery_beat_task_configured(self):
    #     from io import StringIO

    #     from django.core.management import call_command

    #     out = StringIO()
    #     call_command("shell", stdout=out)
    #     # Or: check celery_app.conf.beat_schedule dict directly

    #     # Verify expire_active_trials is in schedule
    #     from billing.tasks import expire_active_trials

    #     # (This is mostly an ops/deployment verification)
