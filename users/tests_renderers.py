import json
from unittest import TestCase

from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=False,
        DEFAULT_CHARSET="utf-8",
        INSTALLED_APPS=[],
        REST_FRAMEWORK={},
        SECRET_KEY="renderer-tests",  # pragma: allowlist secret
    )

from rest_framework import status
from rest_framework.response import Response

from users.renderers import APIJSONRenderer


class APIJSONRendererTests(TestCase):
    def render_payload(self, response):
        rendered = APIJSONRenderer().render(
            response.data,
            accepted_media_type="application/json",
            renderer_context={"response": response},
        )
        return json.loads(rendered.decode("utf-8"))

    def test_success_response_promotes_detail_to_message(self):
        response = Response(
            {"detail": "An OTP code has been sent to your email"},
            status=status.HTTP_202_ACCEPTED,
        )

        payload = self.render_payload(response)

        self.assertTrue(payload["success"])
        self.assertEqual(payload["message"], "An OTP code has been sent to your email")
        self.assertEqual(payload["data"], response.data)

    def test_success_response_promotes_message_to_message(self):
        response = Response(
            {"message": "Background task cancellation requested successfully."},
            status=status.HTTP_200_OK,
        )

        payload = self.render_payload(response)

        self.assertTrue(payload["success"])
        self.assertEqual(
            payload["message"],
            "Background task cancellation requested successfully.",
        )

    def test_manual_error_status_is_rendered_as_error(self):
        response = Response(
            {"detail": "The 'plan' field is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

        payload = self.render_payload(response)

        self.assertFalse(payload["success"])
        self.assertEqual(payload["message"], "The 'plan' field is required.")
        self.assertEqual(payload["error"]["field_errors"], response.data)
        self.assertNotIn("data", payload)

    def test_manual_error_key_becomes_plain_error_message(self):
        response = Response(
            {"error": "Could not generate assignment"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

        payload = self.render_payload(response)

        self.assertFalse(payload["success"])
        self.assertEqual(payload["message"], "Could not generate assignment")

    def test_validation_errors_keep_numbered_message_and_field_errors(self):
        response = Response(
            {"email": ["This field is required."], "password": ["Too short."]},
            status=status.HTTP_400_BAD_REQUEST,
        )
        response._drf_handled = True

        payload = self.render_payload(response)

        self.assertFalse(payload["success"])
        self.assertIn("1. Email: This field is required.", payload["message"])
        self.assertIn("2. Password: Too short.", payload["message"])
        self.assertEqual(payload["error"]["field_errors"], response.data)
