"""FR-A-01 for the auth call sites: exactly one well-formed AuditEvent per
branch of login (CustomTokenObtainPairSerializer.validate) and logout
(AuthViewSet.logout) - no branch silently emits zero or more than one, and
each emits the outcome/error_class/reason_code the plan specifies.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from audit.enums import ActorRole, AuditAction, AuditOutcome, ErrorClass
from audit.models import AuditEvent
from users.models import UserTypes

User = get_user_model()

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
PASSWORD = "a-strong-password-42"  # pragma: allowlist secret
WRONG_PASSWORD = "definitely-the-wrong-password"  # pragma: allowlist secret


def make_user(email="audit.auth@example.com", **overrides):
    defaults = {
        "email": email,
        "password": PASSWORD,
        "first_name": "Audit",
        "last_name": "Auth",
        "user_type": UserTypes.TEACHER,
        "is_active": True,
        "email_verified_at": timezone.now(),
    }
    defaults.update(overrides)
    return User.objects.create_user(**defaults)


@override_settings(CACHES=LOCMEM_CACHE)
class LoginAuditEventTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user()
        self.url = reverse("login")

    def login(self, password):
        return self.client.post(
            self.url, {"email": self.user.email, "password": password}
        )

    def test_a_successful_login_emits_exactly_one_success_event(self):
        response = self.login(PASSWORD)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        events = AuditEvent.objects.filter(action=AuditAction.AUTH_LOGIN)
        self.assertEqual(events.count(), 1)
        event = events.get()
        self.assertEqual(event.outcome, AuditOutcome.SUCCESS)
        self.assertIsNone(event.error_class)
        self.assertIsNone(event.reason_code)
        self.assertEqual(event.actor_id, self.user.id)
        self.assertEqual(event.actor_role, ActorRole.TEACHER)
        self.assertEqual(event.target_type, "CustomUser")
        self.assertEqual(event.target_id, self.user.id)

    def test_a_wrong_password_emits_exactly_one_failure_event(self):
        response = self.login(WRONG_PASSWORD)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        events = AuditEvent.objects.filter(action=AuditAction.AUTH_LOGIN)
        self.assertEqual(events.count(), 1)
        event = events.get()
        self.assertEqual(event.outcome, AuditOutcome.FAILURE)
        self.assertEqual(event.error_class, ErrorClass.USER)
        self.assertEqual(event.reason_code, "WRONG_PASSWORD")
        self.assertEqual(event.target_id, self.user.id)

    def test_an_unknown_email_emits_exactly_one_failure_event_with_no_target(self):
        response = self.client.post(
            self.url, {"email": "nobody-at-all@example.com", "password": PASSWORD}
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        events = AuditEvent.objects.filter(action=AuditAction.AUTH_LOGIN)
        self.assertEqual(events.count(), 1)
        event = events.get()
        self.assertEqual(event.outcome, AuditOutcome.FAILURE)
        self.assertEqual(event.error_class, ErrorClass.USER)
        self.assertEqual(event.reason_code, "INVALID_CREDENTIALS")
        self.assertIsNone(event.target_id)
        self.assertIsNone(event.actor_id)

    def test_a_locked_account_emits_exactly_one_denied_event_and_never_checks_the_password(
        self,
    ):
        self.user.failed_login_attempts = User.MAX_LOGIN_ATTEMPTS
        self.user.locked_until = timezone.now() + timedelta(minutes=30)
        self.user.save()

        response = self.login(PASSWORD)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        events = AuditEvent.objects.filter(action=AuditAction.AUTH_LOGIN)
        self.assertEqual(events.count(), 1)
        event = events.get()
        self.assertEqual(event.outcome, AuditOutcome.DENIED)
        self.assertEqual(event.error_class, ErrorClass.USER)
        self.assertEqual(event.reason_code, "ACCOUNT_LOCKED")
        self.assertEqual(event.target_id, self.user.id)


@override_settings(CACHES=LOCMEM_CACHE)
class LogoutAuditEventTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user("logout.audit@example.com")
        self.client.force_authenticate(user=self.user)
        self.url = reverse("auth-logout")

    def test_a_successful_logout_emits_exactly_one_success_event(self):
        refresh = RefreshToken.for_user(self.user)

        response = self.client.post(self.url, {"refresh": str(refresh)}, format="json")

        self.assertEqual(response.status_code, status.HTTP_205_RESET_CONTENT)
        events = AuditEvent.objects.filter(action=AuditAction.AUTH_LOGOUT)
        self.assertEqual(events.count(), 1)
        event = events.get()
        self.assertEqual(event.outcome, AuditOutcome.SUCCESS)
        self.assertIsNone(event.error_class)
        self.assertIsNone(event.reason_code)
        self.assertEqual(event.actor_id, self.user.id)
        self.assertEqual(event.target_id, self.user.id)

    def test_a_missing_refresh_token_emits_exactly_one_failure_event(self):
        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        events = AuditEvent.objects.filter(action=AuditAction.AUTH_LOGOUT)
        self.assertEqual(events.count(), 1)
        event = events.get()
        self.assertEqual(event.outcome, AuditOutcome.FAILURE)
        self.assertEqual(event.error_class, ErrorClass.VALIDATION)
        self.assertEqual(event.reason_code, "REFRESH_TOKEN_MISSING")

    def test_an_invalid_refresh_token_emits_exactly_one_failure_event(self):
        response = self.client.post(
            self.url, {"refresh": "not-a-real-token"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        events = AuditEvent.objects.filter(action=AuditAction.AUTH_LOGOUT)
        self.assertEqual(events.count(), 1)
        event = events.get()
        self.assertEqual(event.outcome, AuditOutcome.FAILURE)
        self.assertEqual(event.error_class, ErrorClass.USER)
        self.assertEqual(event.reason_code, "REFRESH_TOKEN_INVALID")
