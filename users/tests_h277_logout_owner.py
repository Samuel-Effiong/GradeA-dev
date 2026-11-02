"""
H-277 (owner approved 2026-11-02): logout only blacklists the caller's OWN
refresh token. Posting another user's refresh token is treated exactly like
an invalid token: 400 "Invalid or expired token", nothing blacklisted,
nothing revoked, a REFRESH_TOKEN_INVALID failure row. The response never
says whose token it was.

Real JWTs through the real login, refresh and logout endpoints.
"""

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from audit.models import AuditEvent
from users.models import CustomUser, UserTypes
from users.tests_h276_per_device_logout import _Base
from users.tests_token_revocation import PASSWORD


class LogoutOwnerTests(_Base):
    def setUp(self):
        super().setUp()
        self.victim = CustomUser.objects.create_user(
            email="victim.h277@example.com",
            password=PASSWORD,
            user_type=UserTypes.TEACHER,
            is_active=True,
            email_verified_at=timezone.now(),
        )
        r = APIClient().post(
            reverse("login"),
            {"email": self.victim.email, "password": PASSWORD},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.victim_tokens = r.json()["data"]
        self.mine = self.login()

    def _post_victims_refresh(self):
        return self.as_client(self.mine["access"]).post(
            reverse("auth-logout"),
            {"refresh": self.victim_tokens["refresh"]},
            format="json",
        )

    def test_someone_elses_refresh_is_refused_like_an_invalid_token(self):
        r = self._post_victims_refresh()
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("Invalid or expired token", r.content.decode())
        self.assertNotIn(self.victim.email, r.content.decode())

    def test_the_victims_refresh_token_still_works(self):
        self._post_victims_refresh()
        self.assertEqual(self.refresh(self.victim_tokens["refresh"]).status_code, 200)

    def test_the_caller_is_not_signed_out_by_a_refused_logout(self):
        self._post_victims_refresh()
        self.assertEqual(self.call_as(self.mine["access"]).status_code, 200)
        self.assertEqual(self.refresh(self.mine["refresh"]).status_code, 200)

    def test_the_refusal_is_audited_as_an_invalid_token(self):
        self._post_victims_refresh()
        event = AuditEvent.objects.filter(action="AUTH_LOGOUT").latest("occurred_at")
        self.assertEqual(event.outcome, "FAILURE")
        self.assertEqual(event.reason_code, "REFRESH_TOKEN_INVALID")

    def test_own_refresh_still_logs_out(self):
        self.assertIn(self.logout(self.mine).status_code, (200, 204, 205))
        self.assertEqual(self.refresh(self.mine["refresh"]).status_code, 401)
