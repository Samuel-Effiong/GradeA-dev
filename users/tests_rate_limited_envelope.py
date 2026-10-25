"""
Closes a gap in `audit.enums.ErrorClass`'s own stated invariant ("every
non-success outcome falls in exactly one of these"): DRF's own `Throttled`
- raised automatically by every `AnonRateThrottle`/`ScopedRateThrottle`/
`UserRateThrottle` when a rate limit trips - carried none of
reason_code/error_class/retryable/params. Only the three purpose-built
sign-in locks (VERIFY_LOCKED/RESET_LOCKED/ACCOUNT_LOCKED, via
EnvelopedThrottled/EnvelopedAuthenticationFailed) had the envelope.

`custom_exception_handler` (users/exceptions.py) now adds a generic
RATE_LIMITED envelope to any `Throttled` that reaches it WITHOUT an
`.envelope` of its own - closing the gap for every throttle at once
(the global anon default, every named bucket in users/throttling.py,
dashboard/throttling.py's CustomAIPromptThrottle, and any throttle added
later) while leaving the three named locks untouched: they set
`.envelope` themselves, so the generic branch never runs for them.

Covers:
  - A direct unit test of the handler for the generic mechanism itself
    (including DRF's `exc.wait is None` edge case).
  - HTTP-level: every named AnonRateThrottle subclass in
    users/throttling.py, its reuse on classrooms' register_student, and
    dashboard's CustomAIPromptThrottle (UserRateThrottle).
  - Precedence: the existing named-lock envelope tests
    (users/tests_auth_lock_envelope.py) are the regression guard that
    VERIFY_LOCKED/RESET_LOCKED/ACCOUNT_LOCKED are unaffected; this file
    adds one direct check that a lock's `.envelope` wins even though the
    exception IS a `Throttled` instance.

Run with:
    python manage.py test users.tests_rate_limited_envelope
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import Throttled
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, APITestCase
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from audit.enums import ReasonCode
from AutoGrader.reason_codes import REASON_CODES
from dashboard.throttling import CustomAIPromptThrottle
from users.exceptions import EnvelopedThrottled, custom_exception_handler
from users.models import UserTypes

User = get_user_model()

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def tightened_rate(scope, rate):
    """Temporarily lower one named throttle bucket (same trick as
    users/tests_throttling.py: THROTTLE_RATES is bound at class-definition
    time, so override_settings alone has no effect)."""
    return patch.dict(SimpleRateThrottle.THROTTLE_RATES, {scope: rate})


def envelope_fields(response):
    return response.json()["error"]["field_errors"]


def assert_rate_limited_envelope(testcase, response):
    testcase.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
    fields = envelope_fields(response)
    testcase.assertEqual(fields["reason_code"], "RATE_LIMITED")
    testcase.assertEqual(fields["error_class"], "USER")
    testcase.assertIs(fields["retryable"], True)
    testcase.assertEqual(
        fields["remediation"], REASON_CODES[ReasonCode.RATE_LIMITED].remediation
    )
    testcase.assertIn("wait_seconds", fields["params"])
    testcase.assertIsInstance(fields["params"]["wait_seconds"], int)
    testcase.assertGreaterEqual(fields["params"]["wait_seconds"], 0)
    testcase.assertEqual(fields["reference"], response["X-Request-ID"])
    # DRF's own documented contract is untouched: still a bare `detail`,
    # still the header a client's Retry-After handling already reads.
    testcase.assertIn("detail", fields)
    testcase.assertIn("Retry-After", response)
    return fields


class _DummyView(APIView):
    pass


def _handler_context():
    request = Request(APIRequestFactory().get("/dummy"))
    request.user = AnonymousUser()
    return {"view": _DummyView(), "request": request}


class CustomExceptionHandlerRateLimitedTests(SimpleTestCase):
    """Unit-level: the funnel itself, isolated from any real throttle class
    or URL. This is what actually changed, and it is what covers the
    "global defaults" and "any future throttle" parts of the gap - no HTTP
    endpoint is required to raise a bare `Throttled` with no envelope."""

    def test_bare_throttled_gets_the_generic_envelope(self):
        # DRF's own Throttled.__init__ already does `math.ceil(wait)`
        # before this ever reaches the handler, so 42.4 arrives as 43.
        exc = Throttled(wait=42.4)
        response = custom_exception_handler(exc, _handler_context())

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(response.data["reason_code"], "RATE_LIMITED")
        self.assertEqual(response.data["error_class"], "USER")
        self.assertIs(response.data["retryable"], True)
        self.assertEqual(response.data["params"], {"wait_seconds": 43})
        self.assertIn("detail", response.data)

    def test_wait_none_does_not_crash_and_still_gets_an_envelope(self):
        """DRF's own `Throttled.__init__` allows `wait=None` (a throttle
        whose `wait()` can't say how long). The catalogue requires every
        placeholder, so this must not blow up building the body."""
        exc = Throttled(wait=None)
        response = custom_exception_handler(exc, _handler_context())

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(response.data["reason_code"], "RATE_LIMITED")
        self.assertEqual(response.data["params"], {"wait_seconds": 0})

    def test_a_named_lock_keeps_its_own_code_even_though_it_is_throttled(self):
        """The precedence rule, proven directly against the handler rather
        than relying only on the HTTP-level lock tests elsewhere: an
        EnvelopedThrottled (what VERIFY_LOCKED/RESET_LOCKED raise) IS an
        instance of Throttled, so it must take the `.envelope` branch, not
        the new generic one."""
        exc = EnvelopedThrottled(wait=900, detail="locked", reason_code="VERIFY_LOCKED")
        response = custom_exception_handler(exc, _handler_context())

        self.assertEqual(response.data.get("reason_code"), "VERIFY_LOCKED")
        self.assertNotEqual(response.data.get("reason_code"), "RATE_LIMITED")
        # No wait_seconds param was invented for the named lock - it never
        # declared params in its ReasonSpec.
        self.assertEqual(response.data.get("params"), {})


@override_settings(CACHES=LOCMEM_CACHE)
class NamedThrottleEnvelopeTests(APITestCase):
    """HTTP-level: every named AnonRateThrottle subclass in
    users/throttling.py actually answers with the envelope once its
    bucket is exhausted for real, through the real view + URL + settings
    wiring (EXCEPTION_HANDLER is global, AutoGrader/settings.py)."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = User.objects.create_user(
            email="rate.limited.user@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Rate",
            last_name="Limited",
            user_type=UserTypes.TEACHER,
            is_active=True,
            email_verified_at=timezone.now(),
        )

    def _exhaust(self, url, payload, scope, rate="1/min"):
        """Two posts under a tightened bucket: the first is let through (it
        reaches the view), the second is throttled. Both answers, for the
        tests that must look at the first one too (H-226)."""
        with tightened_rate(scope, rate):
            first = self.client.post(url, payload)
            self.assertNotEqual(first.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
            return first, self.client.post(url, payload)

    def _exhaust_and_get_throttled(self, url, payload, scope, rate="1/min"):
        return self._exhaust(url, payload, scope, rate)[1]

    def test_login_throttle(self):
        response = self._exhaust_and_get_throttled(
            reverse("login"),
            {"email": self.user.email, "password": "wrong"},  # pragma: allowlist secret
            "login",
        )
        assert_rate_limited_envelope(self, response)

    def test_verify_email_throttle(self):
        response = self._exhaust_and_get_throttled(
            reverse("auth-verify"),
            {"email": self.user.email, "token": "000000"},
            "verify_email",
        )
        assert_rate_limited_envelope(self, response)

    def test_otp_request_throttle(self):
        response = self._exhaust_and_get_throttled(
            reverse("auth-otp"),
            {"email": self.user.email, "otp_type": "RESET_PASSWORD"},
            "otp_request",
        )
        assert_rate_limited_envelope(self, response)

    def test_password_reset_throttle(self):
        response = self._exhaust_and_get_throttled(
            reverse("auth-reset-password"),
            {
                "email": self.user.email,
                "otp": "000000",
                "new_password": "irrelevant-wrong-code-1",  # pragma: allowlist secret
            },
            "password_reset",
        )
        assert_rate_limited_envelope(self, response)

    def test_register_throttle(self):
        response = self._exhaust_and_get_throttled(
            reverse("auth-register"),
            {
                "email": "rate.limited.register@example.com",
                "password": "some-strong-password-1",  # pragma: allowlist secret
                "first_name": "Reg",
                "last_name": "Throttle",
            },
            "register",
        )
        assert_rate_limited_envelope(self, response)

    def test_renew_student_token_reuses_the_register_scope_and_gets_the_envelope(self):
        """classrooms/views.py:1592 (`handle_expired_token`, DEAD since
        H-152 but still throttle-guarded) attaches RegisterThrottle to a
        DIFFERENT app's view. EXCEPTION_HANDLER is wired globally in
        settings, but that is an assumption worth a direct check rather
        than trusting it by inference - the throttle check in `initial()`
        runs before the (now no-op) view body either way."""
        response = self._exhaust_and_get_throttled(
            reverse("course-renew-activation-token"),
            {"token": "does-not-matter-throttle-fires-first"},
            "register",
        )
        assert_rate_limited_envelope(self, response)

    # H-226. The FIRST post of the pair is let through the throttle and
    # reaches the view, so the code below is handed to the token exchange: it
    # is not true that the throttle fires first. Without a stand-in for
    # Google, that first post made a real call to oauth2.googleapis.com in
    # every full run (blocked by the H-39 network guard online; a real DNS
    # lookup offline), while this test only read the second answer.
    GOOGLE_CODE = {"code": "a-code-no-stand-in-ever-exchanges"}

    def _google_stood_in(self):
        """A stand-in for the token exchange: `raise_for_status` passes and
        the body is an EMPTY dict, both set explicitly so no MagicMock reaches
        the view's `token_data.get("id_token")` or Google's own verifier. The
        view then answers 400 "Google did not return an ID token" before any
        network call."""
        stand_in = patch("requests.post")
        mocked_post = stand_in.start()
        self.addCleanup(stand_in.stop)
        mocked_post.return_value.raise_for_status.return_value = None
        mocked_post.return_value.json.return_value = {}
        return mocked_post

    def test_google_auth_throttle(self):
        mocked_post = self._google_stood_in()
        first, response = self._exhaust(
            reverse("auth-google-auth"), self.GOOGLE_CODE, "google_auth"
        )
        assert_rate_limited_envelope(self, response)
        # The first post reached the view and the stand-in answered it; the
        # throttled second post never got that far.
        mocked_post.assert_called_once()
        # The deciding value for the first answer: the view reached the token
        # exchange and the exchange handed back no id_token, so nothing a real
        # network answer (or its failure) produced shaped this response.
        self.assertEqual(first.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(str(first.data["detail"]), "Google did not return an ID token")

    def test_the_google_throttle_probe_never_calls_google(self):
        """users/views.py logs "Google OAuth token exchange failed" at ERROR
        on the users.views logger whenever the real exchange is attempted and
        fails; a probe that never calls Google never gets there."""
        self._google_stood_in()
        with self.assertNoLogs("users.views", level="ERROR"):
            self._exhaust(reverse("auth-google-auth"), self.GOOGLE_CODE, "google_auth")


@override_settings(CACHES=LOCMEM_CACHE)
class CustomAIPromptThrottleEnvelopeTests(APITestCase):
    """dashboard/throttling.py's CustomAIPromptThrottle is a
    UserRateThrottle (authenticated, not one of users/throttling.py's
    anonymous buckets) shared across 4 endpoints - one is enough to prove
    the generic handler branch also covers this throttle class."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.superadmin = User.objects.create_user(
            email="rate.limited.super@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Super",
            last_name="Admin",
            user_type=UserTypes.SUPER_ADMIN,
            is_active=True,
            is_superuser=True,
            email_verified_at=timezone.now(),
        )

    @patch.object(CustomAIPromptThrottle, "rate", "1/min")
    @patch("dashboard.views.ai_processor.custom_ai_prompt_retry")
    def test_custom_ai_prompt_throttle(self, mock_retry):
        mock_retry.return_value = "ok"
        self.client.force_authenticate(user=self.superadmin)
        url = reverse("dashboard-custom-ai-prompt")

        first = self.client.post(url, {"prompt": "one"})
        self.assertNotEqual(first.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

        response = self.client.post(url, {"prompt": "two"})
        assert_rate_limited_envelope(self, response)
