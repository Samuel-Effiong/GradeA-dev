"""
CHARACTERIZATION (not a fix) - `POST /auth/verify` and a pre-set password.

`/auth/verify` is the ordinary self-registration flow: register with your own
password, click the emailed link, log in with that password. It takes only
`email` + `token`, so it cannot know whether the password on the row is the
owner's or was chosen by someone who registered the address first.

These tests pin what happens TODAY so a later decision (see the evidence
doc's fix options) changes behaviour on purpose, not by accident. They assert
the current behaviour; if a fix lands, the second test is expected to flip and
should be rewritten for the chosen design.
"""

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from users.models import CustomUser

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM_CACHE)
class VerifyEmailPresetPasswordCharacterizationTests(APITestCase):
    email = "verify.victim@gmail.com"
    chosen_password = "Registrant-Chosen-Pw-1"  # pragma: allowlist secret

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def _pre_register(self):
        response = self.client.post(
            reverse("auth-register"),
            {
                "email": self.email,
                "password": self.chosen_password,
                "first_name": "Registrant",
                "last_name": "Person",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        user = CustomUser.objects.get(email=self.email)
        self.assertFalse(user.is_active)
        self.assertIsNotNone(user.activation_token)
        return user

    def _login(self, password):
        return self.client.post(
            reverse("login"),
            {"email": self.email, "password": password},
            format="json",
        )

    def test_the_dormant_row_cannot_log_in_before_verification(self):
        self._pre_register()

        self.assertNotEqual(self._login(self.chosen_password).status_code, 200)

    def test_current_behaviour_the_registrants_password_works_after_the_token_is_used(
        self,
    ):
        """
        The token is mailed to the address's real owner. Whoever submits it
        activates the row, and the password the REGISTRANT chose is the one
        that then logs in. For a legitimate sign-up that is the point; for a
        registration made by someone else it is the exposure.
        """
        user = self._pre_register()

        verify = self.client.post(
            reverse("auth-verify"),
            {"email": self.email, "token": user.activation_token},
            format="json",
        )

        self.assertEqual(verify.status_code, status.HTTP_202_ACCEPTED, verify.data)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password(self.chosen_password))
        self.assertEqual(
            self._login(self.chosen_password).status_code, status.HTTP_200_OK
        )

    def test_verification_needs_the_emailed_token(self):
        """Without the token the registrant cannot activate their own row."""
        self._pre_register()

        verify = self.client.post(
            reverse("auth-verify"),
            {"email": self.email, "token": "000000"},
            format="json",
        )

        self.assertNotEqual(verify.status_code, status.HTTP_200_OK)
        self.assertFalse(CustomUser.objects.get(email=self.email).is_active)
