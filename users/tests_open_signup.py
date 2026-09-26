"""
Signup is open: no beta whitelist gate stands between a new user and an
account.

Until this suite, `CustomUserSerializer.validate_email` rejected any address
missing from an *active* `BetaWhitelist` row: it parked the address on
`Waitlist` and raised `NotInBetaException` (403). Because every account
creation path funnels through that serializer -- `/auth/register`,
`/auth/google-auth` (GoogleUserSerializer subclasses it) and superadmin user
creation -- a new teacher could not register, and therefore could not log in,
until a superadmin whitelisted them by hand.

The gate is gone. `BetaWhitelist` and `Waitlist` survive as superadmin-only
records, but nothing reads them to allow or deny access. These tests pin
both halves of that: signup works for addresses nowhere near the whitelist,
and the guards that are NOT the beta gate (email verification before login,
personal-vs-business email rules, duplicate emails) still hold.

Two mechanics worth knowing when editing this file:

- The default cache is pinned to LocMem and cleared, so the per-IP register
  throttle (`10/hour`, keyed in the cache) cannot leak counts between tests
  or from another suite sharing Redis.
- Registration fires Celery tasks (activation email, MailerLite sync) via
  `.delay`. They are patched out: the assertions are about the account, not
  about the broker.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from users.models import BetaWhitelist, UserTypes, Waitlist
from users.serializers import CustomUserSerializer

User = get_user_model()

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

PASSWORD = "strongpass123"  # pragma: allowlist secret


class SignupTestCase(APITestCase):
    """Shared plumbing: clean throttle cache, silenced outbound side effects."""

    def setUp(self):
        cache.clear()

        activation = patch("users.serializers.send_user_activation_email")
        self.send_activation = activation.start()
        self.addCleanup(activation.stop)

        mailerlite = patch("users.views.sync_user_to_mailerlite")
        self.sync_mailerlite = mailerlite.start()
        self.addCleanup(mailerlite.stop)

        self.addCleanup(cache.clear)

    def register(self, **overrides):
        payload = {
            "email": "brand.new.teacher@gmail.com",
            "password": PASSWORD,
            "first_name": "Brand",
            "last_name": "New",
        }
        payload.update(overrides)
        # JSON because that is what the frontend sends. The other encodings
        # are covered in users/tests.py (TeacherRegistrationEncodingTests).
        return self.client.post(reverse("auth-register"), payload, format="json")


@override_settings(CACHES=LOCMEM_CACHE)
class OpenSignupTests(SignupTestCase):
    def test_register_succeeds_for_email_absent_from_whitelist(self):
        """The former gate's default case: an address nobody whitelisted."""
        self.assertFalse(BetaWhitelist.objects.exists())

        response = self.register()

        self.assertIn(
            response.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED)
        )
        self.assertTrue(
            User.objects.filter(email="brand.new.teacher@gmail.com").exists()
        )

    def test_register_does_not_park_the_email_on_the_waitlist(self):
        """
        The gate's side effect. A waitlist row now implies a superadmin put
        it there, so signup must not keep writing them behind the user's
        back.
        """
        self.register()

        self.assertFalse(
            Waitlist.objects.filter(email="brand.new.teacher@gmail.com").exists()
        )

    def test_register_succeeds_when_the_whitelist_entry_is_inactive(self):
        """
        The old check required `is_active=True`, so a deactivated row read
        as "not in the beta" and blocked signup just like a missing one.
        """
        BetaWhitelist.objects.create(
            email="brand.new.teacher@gmail.com", is_active=False
        )

        response = self.register()

        self.assertIn(
            response.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED)
        )
        self.assertTrue(
            User.objects.filter(email="brand.new.teacher@gmail.com").exists()
        )

    def test_register_never_answers_403_not_in_beta(self):
        """
        The exact failure users hit: 403 with code `not_in_beta`. Asserting
        on the shape as well as the status catches a re-introduction that
        raises it from somewhere new.
        """
        response = self.register(email="another.newcomer@gmail.com")

        self.assertNotEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertNotIn("not_in_beta", str(response.data))
        self.assertNotIn("Waiting list", str(response.data))

    def test_registered_user_can_verify_then_log_in(self):
        """
        End to end, because "can register" was never the point -- "can log
        in" was. Verification is still required first; that guard is not
        the beta gate.
        """
        self.register(email="login.newcomer@gmail.com")
        user = User.objects.get(email="login.newcomer@gmail.com")

        login_payload = {"email": "login.newcomer@gmail.com", "password": PASSWORD}
        blocked = self.client.post(reverse("login"), login_payload)
        self.assertEqual(blocked.status_code, status.HTTP_401_UNAUTHORIZED)

        # send_user_activation_email is patched out, so stand in for the
        # token it would have issued and walk the real verify endpoint.
        user.activation_token = "activation-token"
        user.activation_expires = timezone.now() + timezone.timedelta(minutes=15)
        user.save(update_fields=["activation_token", "activation_expires"])

        verified = self.client.post(
            reverse("auth-verify"),
            {"email": user.email, "token": "activation-token"},
        )
        self.assertEqual(verified.status_code, status.HTTP_202_ACCEPTED)

        logged_in = self.client.post(reverse("login"), login_payload)
        self.assertEqual(logged_in.status_code, status.HTTP_200_OK)
        self.assertIn("access", logged_in.data)

    def test_register_still_normalises_the_email(self):
        """
        Case-folding lived inside the gated validator and has to outlive it:
        login, uniqueness and invitation lookups all assume stored emails
        are lowercase.
        """
        response = self.register(email="  MiXeD.Case@Gmail.COM  ")

        self.assertIn(
            response.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED)
        )
        self.assertTrue(User.objects.filter(email="mixed.case@gmail.com").exists())

    def test_google_signup_creates_an_account_for_an_unwhitelisted_email(self):
        """
        The second creation path. GoogleUserSerializer subclasses
        CustomUserSerializer, so it inherited the gate and has to inherit
        its removal.
        """
        with patch("requests.post") as mocked_post, patch(
            "users.views.id_token.verify_oauth2_token"
        ) as mocked_verify:
            mocked_post.return_value.raise_for_status.return_value = None
            mocked_post.return_value.json.return_value = {
                "id_token": "fake-id-token",
                "access_token": "fake-access-token",
                "refresh_token": "fake-refresh-token",
                "expires_in": 3600,
            }
            mocked_verify.return_value = {
                "email": "google.newcomer@gmail.com",
                "email_verified": True,
                "given_name": "Google",
                "family_name": "Newcomer",
            }

            response = self.client.post(
                reverse("auth-google-auth"), {"code": "oauth-code"}
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)

        user = User.objects.get(email="google.newcomer@gmail.com")
        self.assertTrue(user.is_active)
        self.assertFalse(Waitlist.objects.filter(email=user.email).exists())


@override_settings(CACHES=LOCMEM_CACHE)
class SignupGuardsSurviveTests(SignupTestCase):
    """
    Everything that rejected a signup for a reason OTHER than the beta gate
    must still reject it. Deleting a validator is the easy way to make
    "anyone can sign up" true by accident.
    """

    def test_teacher_with_a_business_email_is_still_rejected(self):
        response = self.register(email="principal@acme-school.org")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Business emails are not allowed", str(response.data))
        self.assertFalse(
            User.objects.filter(email="principal@acme-school.org").exists()
        )

    def test_school_admin_moving_to_a_personal_email_is_still_rejected(self):
        """
        The mirror-image rule. `user_type` is read-only on this serializer,
        so the SCHOOL_ADMIN branch is only reachable on an existing account
        changing its email -- which is also where the old gate used to fire
        a second time.
        """
        admin = User.objects.create_user(
            email="admin@acme-school.org",
            password=PASSWORD,
            first_name="School",
            last_name="Admin",
            user_type=UserTypes.SCHOOL_ADMIN,
            is_active=True,
        )

        serializer = CustomUserSerializer(
            admin, data={"email": "admin@gmail.com"}, partial=True
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("Personal emails are not allowed", str(serializer.errors))

    def test_duplicate_email_is_still_rejected(self):
        User.objects.create_user(
            email="taken@gmail.com",
            password=PASSWORD,
            first_name="Already",
            last_name="Here",
            user_type=UserTypes.TEACHER,
            is_active=True,
        )

        response = self.register(email="taken@gmail.com")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(User.objects.filter(email="taken@gmail.com").count(), 1)

    def test_new_account_is_inactive_until_verified(self):
        self.register(email="unverified.newcomer@gmail.com")

        user = User.objects.get(email="unverified.newcomer@gmail.com")
        self.assertFalse(user.is_active)
        self.assertIsNone(user.email_verified_at)


@override_settings(CACHES=LOCMEM_CACHE)
class WhitelistRecordsSurviveTests(APITestCase):
    """
    The gate is gone; the bookkeeping is not. Superadmins keep their
    whitelist/waitlist surface (and its admin-only permissions) for the
    records already in it.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

        self.super_admin = User.objects.create_user(
            email="records.super@example.com",
            password=PASSWORD,
            first_name="Records",
            last_name="Super",
            user_type=UserTypes.SUPER_ADMIN,
            is_active=True,
            is_superuser=True,
        )

    def test_superadmin_can_still_add_a_whitelist_entry(self):
        self.client.force_authenticate(user=self.super_admin)

        response = self.client.post(
            reverse("whitelist-list"), {"email": "kept@example.com"}
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(BetaWhitelist.objects.filter(email="kept@example.com").exists())

    def test_superadmin_can_still_transfer_a_waitlist_entry(self):
        entry = Waitlist.objects.create(email="legacy@example.com")
        self.client.force_authenticate(user=self.super_admin)

        response = self.client.post(
            reverse("waitlist-transfer", kwargs={"pk": entry.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            BetaWhitelist.objects.filter(email="legacy@example.com").exists()
        )
        self.assertFalse(Waitlist.objects.filter(pk=entry.pk).exists())
