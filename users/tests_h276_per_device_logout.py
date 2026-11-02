"""
H-276: logging out signs out THIS browser/device only (owner decision
2026-11-02). Logging out of one browser must not affect another browser or
the phone; a password change still signs out everywhere.

Real JWTs through the real login, refresh and logout endpoints and the real
authentication chain (never force_authenticate).
"""

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken

from users.models import CustomUser, UserTypes
from users.tests_token_revocation import PASSWORD, TokenRevocationBase


def claims(token):
    return RefreshToken(token, verify=False).payload


class _Base(TokenRevocationBase):
    def refresh(self, refresh):
        from rest_framework.test import APIClient

        return APIClient().post(reverse("refresh"), {"refresh": refresh}, format="json")

    def logout(self, tokens):
        return self.as_client(tokens["access"]).post(
            reverse("auth-logout"), {"refresh": tokens["refresh"]}, format="json"
        )


class PerDeviceLogoutTests(_Base):
    def test_logging_out_of_one_browser_keeps_the_others_signed_in(self):
        chrome, edge, phone = self.login(), self.login(), self.login()
        self.assertIn(self.logout(chrome).status_code, (200, 204, 205))

        for name, other in (("edge", edge), ("phone", phone)):
            with self.subTest(device=name):
                self.assertEqual(self.call_as(other["access"]).status_code, 200)
                self.assertEqual(self.refresh(other["refresh"]).status_code, 200)

    def test_the_logged_out_browser_is_signed_out_instantly(self):
        chrome = self.login()
        self.login()  # a second browser
        self.logout(chrome)
        self.assertEqual(
            self.call_as(chrome["access"]).status_code, status.HTTP_401_UNAUTHORIZED
        )
        self.assertEqual(
            self.refresh(chrome["refresh"]).status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_logout_does_not_bump_the_epoch(self):
        tokens = self.login()
        self.logout(tokens)
        self.user.refresh_from_db()
        self.assertEqual(self.user.token_epoch, 0)

    def test_a_refreshed_session_is_still_one_session(self):
        chrome, phone = self.login(), self.login()
        r = self.refresh(chrome["refresh"])
        self.assertEqual(r.status_code, 200, r.content)
        rotated = r.json()["data"]
        self.assertEqual(
            claims(rotated["access"])["sid"], claims(chrome["access"])["sid"]
        )
        self.logout(rotated)
        # both the old and the rotated access token of this browser are dead
        self.assertEqual(self.call_as(chrome["access"]).status_code, 401)
        self.assertEqual(self.call_as(rotated["access"]).status_code, 401)
        self.assertEqual(self.call_as(phone["access"]).status_code, 200)

    def test_a_password_change_still_signs_out_everywhere_else(self):
        other, mine = self.login(), self.login()
        r = self.as_client(mine["access"]).post(
            reverse("auth-change-password"),
            {
                "current_password": PASSWORD,
                "new_password": "Another-Passw0rd-9",  # pragma: allowlist secret
            },
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.call_as(other["access"]).status_code, 401)

    def test_a_failed_logout_revokes_nothing(self):
        tokens = self.login()
        self.as_client(tokens["access"]).post(
            reverse("auth-logout"), {"refresh": "junk"}, format="json"
        )
        self.assertEqual(self.call_as(tokens["access"]).status_code, 200)


class SessionIdTests(_Base):
    def test_every_sign_in_gets_its_own_session_id(self):
        a, b = self.login(), self.login()
        self.assertTrue(claims(a["access"]).get("sid"))
        self.assertEqual(claims(a["access"])["sid"], claims(a["refresh"])["sid"])
        self.assertNotEqual(claims(a["access"])["sid"], claims(b["access"])["sid"])

    def test_posting_someone_elses_refresh_never_signs_them_out(self):
        victim = CustomUser.objects.create_user(
            email="victim.h276@example.com",
            password=PASSWORD,
            user_type=UserTypes.TEACHER,
            is_active=True,
            email_verified_at=timezone.now(),
        )
        from rest_framework.test import APIClient

        r = APIClient().post(
            reverse("login"),
            {"email": victim.email, "password": PASSWORD},
            format="json",
        )
        victim_tokens = r.json()["data"]
        mine = self.login()
        self.as_client(mine["access"]).post(
            reverse("auth-logout"), {"refresh": victim_tokens["refresh"]}, format="json"
        )
        c = self.as_client(victim_tokens["access"])
        self.assertEqual(
            c.get(reverse("user-detail", kwargs={"pk": victim.pk})).status_code, 200
        )


class LegacyTokenTests(_Base):
    def test_a_pre_deploy_token_without_a_session_id_signs_out_everywhere(self):
        """A token minted before H-276 has no sid: fall back to the old
        global logout so a logged-out token never stays alive."""
        legacy = RefreshToken.for_user(self.user)  # no sid, no epoch (= 0)
        legacy_tokens = {"access": str(legacy.access_token), "refresh": str(legacy)}
        phone = self.login()
        self.assertEqual(self.call_as(legacy_tokens["access"]).status_code, 200)
        self.logout(legacy_tokens)
        self.assertEqual(self.call_as(legacy_tokens["access"]).status_code, 401)
        self.assertEqual(self.call_as(phone["access"]).status_code, 401)


class PruneTests(_Base):
    def test_old_revocations_are_pruned_on_logout(self):
        from django.apps import apps

        RevokedSession = apps.get_model("users", "RevokedSession")

        old = self.login()
        self.logout(old)
        RevokedSession.objects.update(revoked_at=timezone.now() - timedelta(days=30))
        self.logout(self.login())
        self.assertEqual(RevokedSession.objects.count(), 1)
