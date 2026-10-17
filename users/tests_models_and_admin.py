"""
Model methods, the user manager, and the Django admin actions.

These are the last unexercised corners of `users/`: the manager's
validation guards, the name/upload-path helpers, the subscription
convenience methods that read through to billing, and the admin bulk
actions (which flip `is_active` on arbitrary rows, so they are worth
pinning even though they are staff-only).
"""

import datetime
from datetime import timedelta

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.utils import timezone

from billing.models import (
    CreditBucket,
    CreditBucketType,
    CreditWallet,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
    UserSubscription,
)
from users.admin import (
    BetaWhitelistAdmin,
    CustomUserAdmin,
    PasswordChangeOTPAdmin,
    PasswordResetOTPAdmin,
    WaitlistAdmin,
)
from users.models import (
    ACTIVATION_TOKEN_VALIDITY,
    BetaWhitelist,
    CustomUser,
    PasswordChangeOTP,
    PasswordResetOTP,
    UserTypes,
    Waitlist,
    get_user_name,
)

User = get_user_model()
PASSWORD = "a-strong-password-42"  # pragma: allowlist secret


def make_subscription(user, plan, *, is_active):
    return UserSubscription.objects.create(
        user=user,
        plan=plan,
        is_active=is_active,
        billing_cycle_start=timezone.now(),
        billing_cycle_end=timezone.now() + timedelta(days=30),
    )


def make_plan(*, monthly_credits):
    return SubscriptionPlan.objects.create(
        name=PlanType.STANDARD,
        display_name="Standard",
        category=PlanCategory.INDIVIDUAL,
        tier=PlanTier.STANDARD,
        monthly_credits=monthly_credits,
        is_active=True,
    )


class CustomUserManagerTests(TestCase):
    def test_an_email_is_required(self):
        with self.assertRaises(ValueError):
            User.objects.create_user(email="", password=PASSWORD)

    def test_the_email_is_normalised(self):
        user = User.objects.create_user(
            email="Person@EXAMPLE.com", password=PASSWORD, first_name="A", last_name="B"
        )

        # BaseUserManager.normalize_email lowercases the domain part.
        self.assertEqual(user.email, "Person@example.com")

    def test_save_false_returns_an_unsaved_instance(self):
        user = User.objects.create_user(
            email="unsaved@example.com",
            password=PASSWORD,
            first_name="Un",
            last_name="Saved",
            save=False,
        )

        self.assertFalse(User.objects.filter(email="unsaved@example.com").exists())
        self.assertTrue(user.check_password(PASSWORD))

    def test_create_superuser_sets_both_flags(self):
        admin = User.objects.create_superuser(
            email="super@example.com", password=PASSWORD, first_name="S", last_name="U"
        )

        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.is_active)

    def test_create_superuser_rejects_is_staff_false(self):
        with self.assertRaises(ValueError):
            User.objects.create_superuser(
                email="notstaff@example.com", password=PASSWORD, is_staff=False
            )

    def test_create_superuser_rejects_is_superuser_false(self):
        with self.assertRaises(ValueError):
            User.objects.create_superuser(
                email="notsuper@example.com", password=PASSWORD, is_superuser=False
            )


class UserHelperTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="helpers@gmail.com",
            password=PASSWORD,
            first_name="Ada",
            last_name="Lovelace",
            user_type=UserTypes.TEACHER,
        )

    def test_full_name_joins_the_present_parts(self):
        self.user.middle_name = "Byron"

        self.assertEqual(self.user.get_full_name(), "Ada Byron Lovelace")

    def test_full_name_skips_blank_and_whitespace_only_parts(self):
        self.user.middle_name = "   "

        self.assertEqual(self.user.get_full_name(), "Ada Lovelace")

    def test_str_is_the_full_name(self):
        self.assertEqual(str(self.user), "Ada Lovelace")

    def test_role_predicates(self):
        self.assertTrue(self.user.is_teacher())
        self.assertFalse(self.user.is_student())
        self.assertTrue(self.user.is_beta_eligible())

        student = User.objects.create_user(
            email="pupil@example.com",
            password=PASSWORD,
            first_name="Pu",
            last_name="Pil",
            user_type=UserTypes.STUDENT,
        )
        self.assertTrue(student.is_student())
        self.assertFalse(student.is_teacher())
        self.assertFalse(student.is_beta_eligible())

    def test_profile_image_upload_path_is_scoped_to_the_user(self):
        path = get_user_name(self.user, "avatar.PNG")

        self.assertTrue(path.startswith(f"profile_pics/{self.user.id}/"))
        self.assertIn("helpers", path)
        self.assertIn(str(datetime.date.today()), path)
        self.assertTrue(path.endswith(".PNG"))


class ActivationTokenRenewalTests(TestCase):
    def test_a_student_token_is_renewed_with_a_fresh_expiry(self):
        student = User.objects.create_user(
            email="renew@student.local",
            password=PASSWORD,
            first_name="Re",
            last_name="New",
            user_type=UserTypes.STUDENT,
        )
        before = timezone.now()

        token = student.renew_activation_token()

        student.refresh_from_db()
        self.assertEqual(student.activation_token, token)
        self.assertEqual(len(token), 6)
        self.assertGreater(student.activation_expires, before)
        self.assertLessEqual(
            student.activation_expires,
            before + ACTIVATION_TOKEN_VALIDITY + timedelta(seconds=5),
        )

    def test_renewal_is_refused_for_non_students(self):
        """Teachers use the OTP endpoint; this path is student-only."""
        teacher = User.objects.create_user(
            email="noreneww@gmail.com",
            password=PASSWORD,
            first_name="No",
            last_name="Renew",
            user_type=UserTypes.TEACHER,
        )

        with self.assertRaises(ValueError):
            teacher.renew_activation_token()


class SubscriptionConvenienceTests(TestCase):
    """`CustomUser`'s read-through helpers into billing."""

    def setUp(self):
        self.teacher = User.objects.create_user(
            email="sub.teacher@gmail.com",
            password=PASSWORD,
            first_name="Sub",
            last_name="Teacher",
            user_type=UserTypes.TEACHER,
        )

    def test_no_subscription_reports_none_and_zero(self):
        self.assertIsNone(self.teacher.get_active_subscription())
        self.assertIsNone(self.teacher.subscription_type)
        self.assertFalse(self.teacher.is_under_license())
        self.assertEqual(self.teacher.get_teacher_monthly_allocation(), 0)

    def test_an_individual_subscription_is_reported(self):
        plan = make_plan(monthly_credits=50_000)
        make_subscription(self.teacher, plan, is_active=True)

        self.assertEqual(self.teacher.subscription_type, "INDIVIDUAL")
        self.assertFalse(self.teacher.is_under_license())
        self.assertEqual(self.teacher.get_teacher_monthly_allocation(), 50_000)

    def test_an_inactive_subscription_is_ignored(self):
        plan = make_plan(monthly_credits=50_000)
        make_subscription(self.teacher, plan, is_active=False)

        self.assertIsNone(self.teacher.get_active_subscription())
        self.assertEqual(self.teacher.get_teacher_monthly_allocation(), 0)

    def test_a_plan_without_monthly_credits_reports_zero(self):
        plan = make_plan(monthly_credits=None)
        make_subscription(self.teacher, plan, is_active=True)

        self.assertEqual(self.teacher.get_teacher_monthly_allocation(), 0)


class LoginLockoutModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="lock.model@gmail.com",
            password=PASSWORD,
            first_name="Lock",
            last_name="Model",
        )

    def test_reset_is_a_noop_when_already_clean(self):
        """Avoids a pointless UPDATE on every successful login."""
        self.user.reset_login_lockout()

        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_login_attempts, 0)
        self.assertIsNone(self.user.locked_until)

    def test_failures_accumulate_then_lock(self):
        for _ in range(CustomUser.MAX_LOGIN_ATTEMPTS):
            self.user.register_failed_login()

        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_login_attempts, CustomUser.MAX_LOGIN_ATTEMPTS)
        self.assertTrue(self.user.is_account_locked())

    def test_reset_clears_an_existing_lockout(self):
        for _ in range(CustomUser.MAX_LOGIN_ATTEMPTS):
            self.user.register_failed_login()

        self.user.reset_login_lockout()

        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_login_attempts, 0)
        self.assertIsNone(self.user.locked_until)
        self.assertFalse(self.user.is_account_locked())


class BetaWhitelistAndWaitlistModelTests(TestCase):
    def test_add_and_remove_a_beta_user(self):
        BetaWhitelist.add_beta_user("beta@example.com")
        self.assertTrue(BetaWhitelist.objects.filter(email="beta@example.com").exists())

        BetaWhitelist.remove_beta_user("beta@example.com")
        self.assertFalse(
            BetaWhitelist.objects.filter(email="beta@example.com").exists()
        )

    def test_add_a_waitlist_user(self):
        Waitlist.add_waitlist_user("waiting@example.com")

        self.assertTrue(Waitlist.objects.filter(email="waiting@example.com").exists())

    def test_transfer_moves_the_row_and_records_the_origin(self):
        entry = Waitlist.objects.create(email="mover@example.com")

        created = entry.transfer_to_whitelist()

        self.assertEqual(created.email, "mover@example.com")
        self.assertEqual(created.mode, "WAITLIST")
        self.assertFalse(Waitlist.objects.filter(email="mover@example.com").exists())

    def test_str_reflects_active_state(self):
        entry = BetaWhitelist.objects.create(email="shown@example.com")
        self.assertIn("Active", str(entry))

        entry.is_active = False
        self.assertIn("Inactive", str(entry))

    def test_waitlist_str_includes_the_email(self):
        entry = Waitlist.objects.create(email="listed@example.com")

        self.assertIn("listed@example.com", str(entry))


class OTPModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="otp.model@gmail.com",
            password=PASSWORD,
            first_name="Otp",
            last_name="Model",
        )

    def test_password_reset_otp_str(self):
        otp = PasswordResetOTP.objects.create(user=self.user)

        self.assertIn(self.user.email, str(otp))

    def test_password_change_otp_expires_after_five_minutes(self):
        otp = PasswordChangeOTP.objects.create(user=self.user)
        otp.generate_code()
        self.assertTrue(otp.is_valid())

        PasswordChangeOTP.objects.filter(pk=otp.pk).update(
            created_at=timezone.now() - timedelta(minutes=6)
        )
        otp.refresh_from_db()

        self.assertFalse(otp.is_valid())


class UserGoogleCredentialsStrTests(TestCase):
    def test_str_names_the_owner(self):
        from users.models import UserGoogleCredentials

        user = User.objects.create_user(
            email="creds@gmail.com",
            password=PASSWORD,
            first_name="Cre",
            last_name="Ds",
        )
        credentials = UserGoogleCredentials.objects.create(
            user=user, access_token="tok", token_expiry=timezone.now()
        )

        self.assertIn("creds@gmail.com", str(credentials))


class UserActivityStrTests(TestCase):
    def test_str_names_the_user(self):
        from users.models import UserActivity

        user = User.objects.create_user(
            email="activity.str@gmail.com",
            password=PASSWORD,
            first_name="Act",
            last_name="Str",
        )
        activity = UserActivity.objects.create(user=user)

        self.assertIn("activity.str@gmail.com", str(activity))


class AdminActionTests(TestCase):
    """
    The admin bulk actions flip `is_active` on arbitrary rows, so their
    behaviour is worth pinning even though the surface is staff-only.
    """

    def setUp(self):
        self.site = AdminSite()
        self.factory = RequestFactory()
        self.staff = User.objects.create_superuser(
            email="admin.actions@example.com",
            password=PASSWORD,
            first_name="Ad",
            last_name="Min",
        )

    def _request(self):
        request = self.factory.get("/admin/")
        request.user = self.staff
        # message_user writes to the messages framework.
        from django.contrib.messages.storage.fallback import FallbackStorage

        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    def test_activate_users_action_activates_the_selection(self):
        target = User.objects.create_user(
            email="to.activate@gmail.com",
            password=PASSWORD,
            first_name="To",
            last_name="Activate",
            is_active=False,
        )
        admin = CustomUserAdmin(CustomUser, self.site)

        admin.activate_users(self._request(), User.objects.filter(pk=target.pk))

        target.refresh_from_db()
        self.assertTrue(target.is_active)

    def test_deactivate_users_action_deactivates_the_selection(self):
        target = User.objects.create_user(
            email="to.deactivate@gmail.com",
            password=PASSWORD,
            first_name="To",
            last_name="Deactivate",
            is_active=True,
        )
        admin = CustomUserAdmin(CustomUser, self.site)

        admin.deactivate_users(self._request(), User.objects.filter(pk=target.pk))

        target.refresh_from_db()
        self.assertFalse(target.is_active)

    def test_readonly_fields_lock_the_id_when_editing(self):
        admin = CustomUserAdmin(CustomUser, self.site)

        self.assertEqual(admin.get_readonly_fields(self._request()), [])
        self.assertIn("id", admin.get_readonly_fields(self._request(), obj=self.staff))

    def test_otp_admins_expose_validity_and_forbid_manual_creation(self):
        otp = PasswordResetOTP.objects.create(user=self.staff)
        otp.generate_code()
        reset_admin = PasswordResetOTPAdmin(PasswordResetOTP, self.site)

        self.assertTrue(reset_admin.is_valid_otp(otp))
        self.assertFalse(reset_admin.has_add_permission(self._request()))

        change_otp = PasswordChangeOTP.objects.create(user=self.staff)
        change_otp.generate_code()
        change_admin = PasswordChangeOTPAdmin(PasswordChangeOTP, self.site)

        self.assertTrue(change_admin.is_valid_otp(change_otp))
        self.assertFalse(change_admin.has_add_permission(self._request()))

    def test_waitlist_admin_transfer_action_moves_every_selected_row(self):
        first = Waitlist.objects.create(email="bulk1@example.com")
        Waitlist.objects.create(email="bulk2@example.com")
        admin = WaitlistAdmin(Waitlist, self.site)

        admin.transfer_to_whitelist(self._request(), Waitlist.objects.all())

        self.assertEqual(BetaWhitelist.objects.count(), 2)
        self.assertFalse(Waitlist.objects.filter(pk=first.pk).exists())

    def test_beta_whitelist_admin_is_registered_with_its_search_fields(self):
        admin = BetaWhitelistAdmin(BetaWhitelist, self.site)

        self.assertIn("email", admin.search_fields)


class CreditWalletBalanceTests(TestCase):
    """Sanity-check the wallet read the permission layer depends on."""

    def test_remaining_credits_exclude_spent_and_expired_buckets(self):
        user = User.objects.create_user(
            email="wallet.math@gmail.com",
            password=PASSWORD,
            first_name="Wal",
            last_name="Let",
        )
        wallet, _ = CreditWallet.objects.get_or_create(user=user)
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=1_000,
            used_credits=400,
            expires_at=timezone.now() + timedelta(days=30),
        )
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=5_000,
            used_credits=0,
            expires_at=timezone.now() - timedelta(days=1),
        )

        self.assertEqual(wallet.total_remaining_credits(), 600)
