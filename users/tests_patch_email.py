"""
AUTHZ-PATCHPW (email): PATCH /users/<id> never changes an email address.

The email is the recovery identity: with only a stolen access token, PATCHing
it to an attacker's address and then requesting a reset code for that address
was a complete account takeover. Founder decision 2026-09-28: refuse any
email change on this route, for every caller (super admins included); an
unchanged address is still accepted because frontends send the whole object.
Real JWTs, real endpoints.
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
REFUSAL = "Email address can't be changed."


class EmailChangeTests(PatchPasswordTests):
    def patch(self, actor, target, **body):
        return self.client_for(actor).patch(self.url(target), body, format="json")

    def email_of(self, user):
        return CustomUser.objects.get(pk=user.pk).email

    def assertRefused(self, response):
        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.content
        )
        body = response.json()
        self.assertIs(body["success"], False)
        self.assertEqual(body["error"]["field_errors"]["email"], [REFUSAL])

    # --- the refusal -------------------------------------------------------

    def test_the_stolen_token_takeover_chain_is_closed(self):
        self.assertRefused(self.patch(self.teacher, self.teacher, email=ATTACKER_EMAIL))
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

    def test_a_changed_email_is_refused_even_alongside_valid_edits(self):
        """Refused whole: the other fields in the same request are not saved."""
        self.assertRefused(
            self.patch(
                self.teacher, self.teacher, email=ATTACKER_EMAIL, first_name="Changed"
            )
        )
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.email, "teach.er@gmail.com")
        self.assertNotEqual(self.teacher.first_name, "Changed")

    def test_super_admin_on_another_account_is_refused_too(self):
        """Founder decision (confirmed 2026-09-28): super admins can't change
        anyone's email through this route either."""
        self.assertRefused(
            self.patch(self.superadmin, self.teacher, email="moved@gmail.com")
        )
        self.assertEqual(self.email_of(self.teacher), "teach.er@gmail.com")

    def test_super_admin_on_their_own_account_is_refused(self):
        self.assertRefused(
            self.patch(self.superadmin, self.superadmin, email="root2@example.com")
        )

    def test_the_old_current_password_mechanism_no_longer_unlocks_a_change(self):
        self.assertRefused(
            self.patch(
                self.teacher,
                self.teacher,
                email="new.address@gmail.com",
                current_password=PASSWORD,
            )
        )
        self.assertEqual(self.email_of(self.teacher), "teach.er@gmail.com")

    def test_refusing_does_not_touch_the_login_lockout_counter(self):
        for _ in range(CustomUser.MAX_LOGIN_ATTEMPTS + 1):
            self.patch(self.teacher, self.teacher, email=ATTACKER_EMAIL)
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.failed_login_attempts, 0)
        self.assertFalse(self.teacher.is_account_locked())

    def test_an_account_without_a_usable_password_is_refused_the_same_way(self):
        google = make("g.user@gmail.com")
        google.set_unusable_password()
        google.save()
        self.assertRefused(self.patch(google, google, email=ATTACKER_EMAIL))
        self.assertEqual(self.email_of(google), "g.user@gmail.com")

    def test_nobody_can_change_someone_elses_email(self):
        for actor, target in (
            (self.other_teacher, self.teacher),
            (self.teacher, self.student),
        ):
            r = self.patch(actor, target, email=ATTACKER_EMAIL)
            self.assertIn(
                r.status_code,
                (
                    status.HTTP_400_BAD_REQUEST,
                    status.HTTP_403_FORBIDDEN,
                    status.HTTP_404_NOT_FOUND,
                ),
            )
            self.assertNotEqual(self.email_of(target), ATTACKER_EMAIL)

    def test_password_and_email_together_is_still_refused(self):
        r = self.patch(
            self.teacher, self.teacher, email=ATTACKER_EMAIL, password=ATTACKER_PASSWORD
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.email, "teach.er@gmail.com")
        self.assertTrue(self.teacher.check_password(PASSWORD))

    # --- positive controls: what must keep working --------------------------

    def test_an_unchanged_email_is_accepted_with_case_and_whitespace_variants(self):
        for i, variant in enumerate(
            ("teach.er@gmail.com", "  TEACH.ER@gmail.com ", "Teach.Er@Gmail.com")
        ):
            r = self.patch(
                self.teacher, self.teacher, email=variant, first_name=f"Ok{i}"
            )
            self.assertEqual(r.status_code, status.HTTP_200_OK, (variant, r.content))
            self.teacher.refresh_from_db()
            self.assertEqual(self.teacher.first_name, f"Ok{i}")
        self.assertEqual(self.email_of(self.teacher), "teach.er@gmail.com")

    def test_a_legacy_mixed_case_stored_email_sent_back_unchanged_is_accepted(self):
        """Rows stored before emails were lower-cased on write: the incoming
        value is normalised by validate_email, so the STORED side has to be
        normalised too, or an unchanged full-profile PATCH gets a false 400."""
        CustomUser.objects.filter(pk=self.teacher.pk).update(
            email="Teach.Er@Gmail.com "
        )

        r = self.patch(
            self.teacher, self.teacher, email="Teach.Er@Gmail.com", first_name="Legacy"
        )

        self.assertEqual(r.status_code, status.HTTP_200_OK, r.content)
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.first_name, "Legacy")

    def test_other_field_edits_without_an_email_still_succeed(self):
        r = self.patch(self.teacher, self.teacher, first_name="New", last_name="Name")
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.content)
        self.teacher.refresh_from_db()
        self.assertEqual(
            (self.teacher.first_name, self.teacher.last_name), ("New", "Name")
        )

    def test_super_admin_editing_another_users_other_fields_still_succeeds(self):
        r = self.patch(
            self.superadmin,
            self.teacher,
            email="teach.er@gmail.com",
            first_name="ByAdmin",
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.content)
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.first_name, "ByAdmin")

    def test_account_creation_is_unaffected(self):
        r = self.client_for(self.superadmin).post(
            reverse("user-list"),
            {
                "email": "created.by.admin@gmail.com",
                "first_name": "C",
                "last_name": "A",
                "password": ATTACKER_PASSWORD,
            },
            format="json",
        )
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.content)
        self.assertTrue(
            CustomUser.objects.filter(email="created.by.admin@gmail.com").exists()
        )
