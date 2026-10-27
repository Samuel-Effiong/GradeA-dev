"""
H-256: the Google token exchange in `POST /auth/google-auth`
(`users/views.py` `_google_auth`) waits at most 5 s to connect and 10 s for
Google's answer. Before, `requests.post` had no timeout at all, so a Google
that never answered held a web thread until the operating system gave up.

A timeout or a dropped connection answers the refusal the route already
gives for a failed exchange ("Google sign-in failed. Please try again.") and
writes one ERROR line naming the error by class and frames only: no
traceback, no text (requests puts the host, the port and the read timeout in
its messages).

Run with:
    python manage.py test users.tests_h256_google_exchange_timeout
"""

from unittest.mock import patch

import requests
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from users.views import GOOGLE_SIGN_IN_FAILED

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# What requests itself writes into a timeout's message; never logged, never
# answered.
PROVIDER_TEXT = (
    "HTTPSConnectionPool(host='oauth2.googleapis.com', port=443): "
    "Read timed out. (read timeout=10)"
)


@override_settings(CACHES=LOCMEM_CACHE)
class GoogleExchangeTimeoutTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.url = reverse("auth-google-auth")

    def post_with(self, *, side_effect=None):
        """One post to the route, with requests.post stood in. Without a
        side effect the stand-in answers an empty body, so the view stops at
        "Google did not return an ID token" before any network call."""
        cache.clear()  # the google_auth bucket allows one post a minute
        with patch("requests.post") as mocked_post:
            if side_effect is not None:
                mocked_post.side_effect = side_effect
            else:
                mocked_post.return_value.raise_for_status.return_value = None
                mocked_post.return_value.json.return_value = {}
            response = self.client.post(self.url, {"code": "a-code"}, format="json")
        return mocked_post, response

    def test_the_exchange_waits_5_s_to_connect_and_10_s_for_the_answer(self):
        mocked_post, response = self.post_with()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mocked_post.assert_called_once()
        self.assertEqual(mocked_post.call_args.kwargs.get("timeout"), (5, 10))

    def test_a_timeout_or_a_dropped_connection_answers_the_neutral_refusal(self):
        for error in (
            requests.exceptions.ReadTimeout(PROVIDER_TEXT),
            requests.exceptions.ConnectTimeout(PROVIDER_TEXT),
            requests.exceptions.ConnectionError(PROVIDER_TEXT),
        ):
            with self.subTest(error=type(error).__name__):
                _, response = self.post_with(side_effect=error)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(str(response.data["detail"]), GOOGLE_SIGN_IN_FAILED)
                self.assertNotIn(PROVIDER_TEXT, response.content.decode())

    def test_a_timeout_is_logged_by_its_class_only(self):
        for error in (
            requests.exceptions.ReadTimeout(PROVIDER_TEXT),
            requests.exceptions.ConnectionError(PROVIDER_TEXT),
        ):
            name = type(error).__name__
            with self.subTest(error=name):
                with self.assertLogs("users.views", level="ERROR") as logs:
                    self.post_with(side_effect=error)
                self.assertEqual(len(logs.records), 1)
                record = logs.records[0]
                self.assertIsNone(record.exc_info)
                message = record.getMessage()
                self.assertIn("Google OAuth token exchange failed", message)
                self.assertIn(f"error={name}", message)
                self.assertNotIn(PROVIDER_TEXT, message)
