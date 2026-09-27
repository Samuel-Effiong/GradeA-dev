"""
AUTHZ-PATCHPW (email): changing your OWN email needs your current password.

The email is the recovery identity: with only a stolen access token, PATCHing
it to an attacker's address and then requesting a reset code for that address
was a complete account takeover. Real JWTs, real endpoints.
"""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from users.models import CustomUser, PasswordResetOTP
from users.tests_patch_password import (
    ATTACKER_PASSWORD,
    PASSWORD,
    PatchPasswordTests,
    make,
)

ATTACKER_EMAIL = "attacker@gmail.com"
WRONG_PASSWORD = "definitely-wrong-1"  # pragma: allowlist secret


class EmailChangeTests(PatchPasswordTests):
    def patch_email(self, actor, target, email, **extra):
        return self.client_for(actor).patch(
            self.url(target), {"email": email, **extra}, format="json"
        )

    def email_of(self, user):
        return CustomUser.objects.get(pk=user.pk).email

    def test_the_stolen_token_takeover_chain_is_closed(self):
        r = self.patch_email(self.teacher, self.teacher, ATTACKER_EMAIL)
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST, r.content)
        self.assertEqual(self.email_of(self.teacher), "teach.er@gmail.com")
        # the rest of the chain has nothing to work with
        anon = APIClient()
        anon.post(
            reverse("auth-otp"),
            {"email": ATTACKER_EMAIL, "otp_type": "RESET_PASSWORD"},
            format="json",
        )
        self.assertFalse(PasswordResetOTP.objects.filter(user=self.teacher).exists())
        r = anon.post(
            reverse("auth-reset-password"),
            {
                "email": ATTACKER_EMAIL,
                "otp": "123456",
                "new_password": ATTACKER_PASSWORD,
            },
            format="json",
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_or_blank_current_password_is_a_400_on_that_field(self):
        for extra in ({}, {"current_password": ""}):
            r = self.patch_email(self.teacher, self.teacher, ATTACKER_EMAIL, **extra)
            self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST, extra)
            self.assertIn("current_password", r.content.decode())
        self.assertEqual(self.email_of(self.teacher), "teach.er@gmail.com")

    def test_wrong_current_password_is_refused_and_counted(self):
        r = self.patch_email(
            self.teacher, self.teacher, ATTACKER_EMAIL, current_password=WRONG_PASSWORD
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.email_of(self.teacher), "teach.er@gmail.com")
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.failed_login_attempts, 1)

    def test_it_is_not_a_password_guessing_oracle_the_lockout_applies(self):
        for _ in range(CustomUser.MAX_LOGIN_ATTEMPTS):
            self.patch_email(
                self.teacher,
                self.teacher,
                ATTACKER_EMAIL,
                current_password=WRONG_PASSWORD,
            )
        self.teacher.refresh_from_db()
        self.assertTrue(self.teacher.is_account_locked())
        # locked: even the CORRECT password is refused, so guessing stops
        r = self.patch_email(
            self.teacher, self.teacher, ATTACKER_EMAIL, current_password=PASSWORD
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.email_of(self.teacher), "teach.er@gmail.com")

    def test_correct_current_password_changes_the_email_and_clears_the_counter(self):
        CustomUser.objects.filter(pk=self.teacher.pk).update(failed_login_attempts=2)
        r = self.patch_email(
            self.teacher,
            self.teacher,
            "New.Address@Gmail.com",
            current_password=PASSWORD,
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.content)
        self.assertEqual(self.email_of(self.teacher), "new.address@gmail.com")
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.failed_login_attempts, 0)
        self.assertNotIn("current_password", r.content.decode())
        self.assertNotIn(PASSWORD, r.content.decode())

    def test_same_value_email_needs_no_password_even_with_case_and_whitespace(self):
        for variant in (
            "teach.er@gmail.com",
            "  TEACH.ER@gmail.com ",
            "Teach.Er@Gmail.com",
        ):
            r = self.patch_email(self.teacher, self.teacher, variant, first_name="Ok")
            self.assertEqual(r.status_code, status.HTTP_200_OK, (variant, r.content))
        self.assertEqual(self.email_of(self.teacher), "teach.er@gmail.com")

    def test_domain_rules_still_apply_when_the_password_is_right(self):
        r = self.patch_email(
            self.teacher,
            self.teacher,
            "someone@bigcorp-business.com",
            current_password=PASSWORD,
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.email_of(self.teacher), "teach.er@gmail.com")

    def test_an_account_without_a_usable_password_gets_a_clear_400(self):
        google = make("g.user@gmail.com")
        google.set_unusable_password()
        google.save()
        r = self.patch_email(google, google, ATTACKER_EMAIL, current_password="x")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Google", r.content.decode())
        self.assertEqual(self.email_of(google), "g.user@gmail.com")
        google.refresh_from_db()
        self.assertEqual(google.failed_login_attempts, 0)

    def test_superadmin_changing_another_users_email_is_unchanged(self):
        r = self.patch_email(self.superadmin, self.teacher, "moved@gmail.com")
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.content)
        self.assertEqual(self.email_of(self.teacher), "moved@gmail.com")

    def test_superadmin_changing_their_own_email_does_need_the_password(self):
        r = self.patch_email(self.superadmin, self.superadmin, "root2@example.com")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        r = self.patch_email(
            self.superadmin,
            self.superadmin,
            "root2@example.com",
            current_password=PASSWORD,
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.content)

    def test_nobody_can_change_someone_elses_email(self):
        for actor, target in (
            (self.other_teacher, self.teacher),
            (self.teacher, self.student),
        ):
            r = self.patch_email(
                actor, target, ATTACKER_EMAIL, current_password=PASSWORD
            )
            self.assertIn(
                r.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND)
            )
            self.assertNotEqual(self.email_of(target), ATTACKER_EMAIL)

    def test_password_and_email_together_is_still_refused_on_the_password(self):
        r = self.patch_email(
            self.teacher,
            self.teacher,
            ATTACKER_EMAIL,
            current_password=PASSWORD,
            password=ATTACKER_PASSWORD,
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("/auth/change-password", r.content.decode())
        self.assertEqual(self.email_of(self.teacher), "teach.er@gmail.com")

    def test_superadmin_create_ignores_a_stray_current_password(self):
        r = self.client_for(self.superadmin).post(
            reverse("user-list"),
            {
                "email": "created.by.admin@gmail.com",
                "first_name": "C",
                "last_name": "A",
                "password": ATTACKER_PASSWORD,
                "current_password": WRONG_PASSWORD,
            },
            format="json",
        )
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.content)
