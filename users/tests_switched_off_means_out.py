"""
H-202: switched off means out.

WHAT WAS WRONG (found by reading, 2026-10-08; "by reading, not shown by a run"
until the red tests below ran)
-----------------------------------------------------------------------------
The only code that switches a verified user OFF is the Django admin (the bulk
action "Mark selected users as inactive" and the edit form). Three roads then
let the switched-off person back in with only their own mailbox:

  * `POST /auth/otp` VERIFY_EMAIL refused only "verified AND active", so a
    verified, switched-off account was mailed an activation code, and
    `POST /auth/verify` set `is_active` True and returned tokens.
  * `POST /auth/otp` RESET_PASSWORD and `POST /auth/reset-password` accepted a
    verified, switched-off account: a code was made and mailed, the reset set
    a password the person chose and answered 200 with tokens (the tokens were
    refused later because the row stayed inactive, but the person was told it
    worked).
  * The admin's switch-off did not bump the token epoch (a queryset update,
    and the edit form saved with no bump), so tokens issued BEFORE the
    switch-off were only dormant: a later switch-on, by any road, revived them
    for the rest of their life (access 1 day, refresh 2 days).

Google sign-in already refuses a verified, switched-off account (its written
carve-out); an inactive account's tokens are already refused on every request
and on refresh (simplejwt checks the row each time).

THE RULE (Senior Manager, 2026-10-08 19:20)
-------------------------------------------
A VERIFIED AND INACTIVE account is refused on the VERIFY_EMAIL code request
(the neutral 202 an unknown address gets; nothing sent or stored), on
/auth/verify (the wrong-code refusal; the attempt spent; nothing written), on
the RESET code request (neutral 202; nothing sent or stored) and on the reset
step (the generic refusal; the attempt spent; nothing written; no token).
A NEVER-VERIFIED inactive account passes the verify road exactly as before
(self-registration, an invited school admin, an old-scheme pending student).
And switching a user off REVOKES their sessions (token epoch +1, only when
the flag goes from True to False).

Run with:
    python manage.py test users.tests_switched_off_means_out
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework.throttling import SimpleRateThrottle

from classrooms.models import School
from users.admin import CustomUserAdmin
from users.models import PasswordResetOTP, Settings, UserTypes
from users.throttling import verify_lock_until
from users.tokens import EpochRefreshToken

User = get_user_model()

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
RATES = {
    "otp_request": "1000/hour",
    "login": "1000/min",
    "anon": "1000/min",
    "verify_email": "1000/hour",
}
NEW_PASSWORD = "a-new-strong-passphrase-for-h202"  # pragma: allowlist secret
CODE = "424242"


@override_settings(CACHES=LOCMEM)
class SwitchedOffRoadsTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        throttles = patch.dict(SimpleRateThrottle.THROTTLE_RATES, RATES)
        throttles.start()
        self.addCleanup(throttles.stop)
        mail = patch("users.services.send_email_task")
        self.activation_mail = mail.start()
        self.addCleanup(mail.stop)
        reset_mail = patch("users.views.safe_delay")
        self.reset_mail = reset_mail.start()
        self.addCleanup(reset_mail.stop)
        for target in (
            "users.views.sync_user_to_mailerlite",
            "users.views.AnalyticsService",
        ):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.ip = 0

    # -- fixtures --------------------------------------------------------

    def switched_off(self, email="off.h202@x.example"):
        """A verified user, switched off the way the admin action does it."""
        user = User.objects.create_user(
            email=email,
            password="Old-pass-h202",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            is_active=True,
            email_verified_at=timezone.now(),
        )
        User.objects.filter(pk=user.pk).update(is_active=False)
        user.refresh_from_db()
        return user

    def with_token(self, user):
        User.objects.filter(pk=user.pk).update(
            activation_token=CODE,
            activation_expires=timezone.now() + timedelta(minutes=15),
        )
        user.refresh_from_db()
        return user

    def ask(self, email, otp_type):
        self.ip += 1
        return self.client.post(
            reverse("auth-otp"),
            {"email": email, "otp_type": otp_type},
            format="json",
            REMOTE_ADDR=f"10.203.0.{self.ip}",
        )

    def verify(self, email, token):
        return self.client.post(
            reverse("auth-verify"), {"email": email, "token": token}, format="json"
        )

    def reset(self, email, code):
        return self.client.post(
            reverse("auth-reset-password"),
            {"email": email, "otp": code, "new_password": NEW_PASSWORD},
            format="json",
        )

    def code_for(self, user):
        otp, _ = PasswordResetOTP.objects.get_or_create(user=user)
        return otp.generate_code()

    # -- the verify road ---------------------------------------------------

    def test_the_verify_code_request_for_a_switched_off_account_sends_nothing(self):
        user = self.switched_off()

        self.ask(user.email, "VERIFY_EMAIL")

        user.refresh_from_db()
        self.assertIsNone(user.activation_token)
        self.activation_mail.delay.assert_not_called()

    def test_that_verify_code_request_answers_like_an_unknown_address(self):
        user = self.switched_off()

        unknown = self.ask("nobody.h202@example.com", "VERIFY_EMAIL")
        answer = self.ask(user.email, "VERIFY_EMAIL")

        self.assertEqual(unknown.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(answer.status_code, unknown.status_code)
        self.assertEqual(answer.content, unknown.content)

    def test_verify_does_not_switch_a_switched_off_account_back_on(self):
        user = self.with_token(self.switched_off())

        response = self.verify(user.email, CODE)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn("access", response.data)
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertEqual(user.activation_token, CODE)

    def test_that_verify_refusal_is_the_wrong_code_refusal(self):
        user = self.with_token(self.switched_off())

        refused = self.verify(user.email, CODE)
        wrong = self.verify(user.email, "000000")

        self.assertEqual(refused.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(refused.status_code, wrong.status_code)
        self.assertEqual(refused.content, wrong.content)

    @override_settings(VERIFY_EMAIL_MAX_FAILURES=2)
    def test_a_refused_verify_for_a_switched_off_account_spends_the_budget(self):
        user = self.with_token(self.switched_off())

        self.verify(user.email, CODE)
        self.verify(user.email, CODE)
        third = self.verify(user.email, CODE)

        self.assertEqual(third.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    @override_settings(VERIFY_EMAIL_MAX_FAILURES=2)
    def test_a_refused_verify_for_a_switched_off_account_locks_the_address_like_a_wrong_guess(
        self,
    ):
        """The budget is spent as for a wrong guess only if the address is
        locked when it runs out (the lock also stops a new code being mailed
        to it). Its own test, so the budget test keeps one reason to fail."""
        user = self.with_token(self.switched_off())

        self.verify(user.email, CODE)
        self.assertIsNone(verify_lock_until(user.email))
        self.verify(user.email, CODE)

        self.assertIsNotNone(verify_lock_until(user.email))

    # -- the reset road ----------------------------------------------------

    def test_the_reset_code_request_for_a_switched_off_account_makes_no_code(self):
        user = self.switched_off()

        self.ask(user.email, "RESET_PASSWORD")

        self.assertFalse(PasswordResetOTP.objects.filter(user=user).exists())
        self.reset_mail.assert_not_called()

    def test_that_reset_code_request_answers_like_an_unknown_address(self):
        user = self.switched_off()

        unknown = self.ask("nobody.reset.h202@example.com", "RESET_PASSWORD")
        answer = self.ask(user.email, "RESET_PASSWORD")

        self.assertEqual(unknown.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(answer.status_code, unknown.status_code)
        self.assertEqual(answer.content, unknown.content)

    def test_a_reset_for_a_switched_off_account_changes_nothing_and_signs_nobody_in(
        self,
    ):
        user = self.switched_off()
        code = self.code_for(user)
        before = user.password

        response = self.reset(user.email, code)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn("access", response.data)
        user.refresh_from_db()
        self.assertEqual(user.password, before)
        self.assertFalse(user.is_active)

    def test_that_reset_refusal_is_the_same_as_for_an_address_with_no_account(self):
        user = self.switched_off()
        code = self.code_for(user)

        refused = self.reset(user.email, code)
        unknown = self.reset("nobody.reset2.h202@example.com", code)

        self.assertEqual(refused.status_code, unknown.status_code)
        self.assertEqual(refused.content, unknown.content)

    def test_a_refused_reset_for_a_switched_off_account_spends_an_attempt(self):
        user = self.switched_off()
        code = self.code_for(user)

        self.reset(user.email, code)

        self.assertEqual(PasswordResetOTP.objects.get(user=user).attempts, 1)

    # -- controls: nobody else is affected -----------------------------------

    def test_a_never_verified_inactive_account_still_activates_end_to_end(self):
        """Green on the old code too: self-registration."""
        user = User.objects.create_user(
            email="selfreg.h202@x.example",
            password="Self-pass-h202",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=False,
        )

        self.ask(user.email, "VERIFY_EMAIL")
        user.refresh_from_db()
        self.assertIsNotNone(user.activation_token)
        response = self.verify(user.email, user.activation_token)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertIsNotNone(user.email_verified_at)

    def test_an_invited_school_admin_still_verifies(self):
        """Green on the old code too."""
        school = School.objects.create(name="H202 High")
        admin = User.objects.create_user(
            email="invited.admin.h202@x.example",
            password="Admin-pass-h202",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=school,
            is_active=False,
        )
        self.with_token(admin)

        response = self.verify(admin.email, CODE)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        admin.refresh_from_db()
        self.assertTrue(admin.is_active)

    def test_an_old_scheme_pending_student_still_verifies(self):
        """Green on the old code too: inactive, never verified, a student
        with an activation code from before the new scheme."""
        student = User.objects.create_user(
            email="pending.student.h202@x.example",
            password="Pending-pass-h202",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            is_active=False,
        )
        self.with_token(student)

        response = self.verify(student.email, CODE)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertIn("access", response.data)
        student.refresh_from_db()
        self.assertTrue(student.is_active)

    def test_an_active_verified_account_is_still_told_it_is_verified(self):
        """Green on the old code too."""
        user = User.objects.create_user(
            email="active.h202@x.example",
            password="Active-pass-h202",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
            email_verified_at=timezone.now(),
        )

        response = self.ask(user.email, "VERIFY_EMAIL")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_an_active_verified_account_can_still_reset(self):
        """Green on the old code too."""
        user = User.objects.create_user(
            email="activereset.h202@x.example",
            password="Active-pass-h203",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
            email_verified_at=timezone.now(),
        )

        response = self.reset(user.email, self.code_for(user))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertTrue(user.check_password(NEW_PASSWORD))


@override_settings(CACHES=LOCMEM)
class SwitchingOffRevokesSessionsTests(APITestCase):
    """Switching a user off revokes their sessions: the token epoch goes up by
    one, only when the flag goes from True to False. A later switch-on does not
    revive the tokens issued before."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.operator = User.objects.create_superuser(
            email="operator.h202@x.example",
            password="Operator-pass-h202",  # pragma: allowlist secret
        )
        User.objects.filter(pk=self.operator.pk).update(
            email_verified_at=timezone.now()
        )
        self.person = User.objects.create_user(
            email="person.h202@x.example",
            password="Person-pass-h202",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
            email_verified_at=timezone.now(),
        )
        self.admin_changelist = reverse("admin:users_customuser_changelist")
        # The person's own Settings row (made by a signal; get_or_create so the
        # test does not depend on that): its detail route is open to its owner.
        self.own_settings = Settings.objects.get_or_create(user=self.person)[0]

    # -- fixtures --------------------------------------------------------

    def tokens_of(self, user):
        refresh = EpochRefreshToken.for_user(user)
        return str(refresh.access_token), str(refresh)

    def epoch(self, user):
        return User.objects.values_list("token_epoch", flat=True).get(pk=user.pk)

    def admin_action(self, action, *users):
        self.client.force_login(self.operator)
        response = self.client.post(
            self.admin_changelist,
            {"action": action, "_selected_action": [str(u.pk) for u in users]},
        )
        self.client.logout()
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)

    def settings_status(self, access):
        client = self.client_class()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        url = reverse("settings-detail", kwargs={"pk": self.own_settings.pk})
        return client.get(url).status_code

    def refresh_status(self, refresh):
        return (
            self.client_class()
            .post(reverse("refresh"), {"refresh": refresh}, format="json")
            .status_code
        )

    def admin_edit(self, obj, changed):
        request = RequestFactory().post("/admin/")
        request.user = self.operator
        CustomUserAdmin(User, AdminSite()).save_model(
            request, obj, SimpleNamespace(changed_data=changed), True
        )

    # -- the bulk action -----------------------------------------------------

    def test_the_bulk_switch_off_bumps_the_token_epoch_by_one(self):
        before = self.epoch(self.person)

        self.admin_action("deactivate_users", self.person)

        self.assertEqual(self.epoch(self.person), before + 1)
        self.person.refresh_from_db()
        self.assertFalse(self.person.is_active)

    def test_tokens_issued_before_the_switch_off_stay_dead_after_a_switch_on(self):
        access, refresh = self.tokens_of(self.person)
        self.assertEqual(self.settings_status(access), status.HTTP_200_OK)

        self.admin_action("deactivate_users", self.person)
        self.admin_action("activate_users", self.person)

        self.assertEqual(self.settings_status(access), status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(self.refresh_status(refresh), status.HTTP_401_UNAUTHORIZED)

    def test_a_switch_on_alone_does_not_bump_the_epoch(self):
        self.admin_action("deactivate_users", self.person)
        after_off = self.epoch(self.person)

        self.admin_action("activate_users", self.person)

        self.assertEqual(self.epoch(self.person), after_off)

    def test_tokens_issued_after_the_switch_on_work(self):
        """Green on the old code too."""
        self.admin_action("deactivate_users", self.person)
        self.admin_action("activate_users", self.person)
        access, refresh = self.tokens_of(User.objects.get(pk=self.person.pk))

        self.assertEqual(self.settings_status(access), status.HTTP_200_OK)
        self.assertEqual(self.refresh_status(refresh), status.HTTP_200_OK)

    def test_the_bulk_switch_off_leaves_an_already_inactive_rows_epoch_alone(self):
        other = User.objects.create_user(
            email="other.h202@x.example",
            password="Other-pass-h202",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=False,
        )
        other_before = self.epoch(other)
        person_before = self.epoch(self.person)

        self.admin_action("deactivate_users", self.person, other)

        self.assertEqual(self.epoch(other), other_before)
        self.assertEqual(self.epoch(self.person), person_before + 1)

    # -- the edit form ---------------------------------------------------------

    def test_an_edit_that_switches_a_user_off_bumps_the_epoch(self):
        before = self.epoch(self.person)
        self.person.is_active = False

        self.admin_edit(self.person, ["is_active"])

        self.assertEqual(self.epoch(self.person), before + 1)

    def test_an_edit_that_does_not_touch_is_active_does_not_bump(self):
        before = self.epoch(self.person)
        self.person.first_name = "Renamed"

        self.admin_edit(self.person, ["first_name"])

        self.assertEqual(self.epoch(self.person), before)

    def test_an_edit_that_switches_a_user_on_does_not_bump(self):
        User.objects.filter(pk=self.person.pk).update(is_active=False)
        self.person.refresh_from_db()
        before = self.epoch(self.person)
        self.person.is_active = True

        self.admin_edit(self.person, ["is_active"])

        self.assertEqual(self.epoch(self.person), before)

    def test_an_edit_of_an_already_inactive_user_that_leaves_is_active_alone_does_not_bump(
        self,
    ):
        User.objects.filter(pk=self.person.pk).update(is_active=False)
        self.person.refresh_from_db()
        before = self.epoch(self.person)
        self.person.first_name = "Renamed"

        self.admin_edit(self.person, ["first_name"])

        self.assertEqual(self.epoch(self.person), before)
