"""
billing/tests/test_custom_ai_prompt_beta.py
==============================================
View-level (HTTP) tests for BetaAnalyticViewSet.custom_ai_prompt
(billing/views.py) - the superadmin-only "beta cohort" variant of the
dashboard "Custom AI Prompt" chat. Shares its serializer (CustomAIPrompt),
kill switch (DASHBOARD_CUSTOM_AI_PROMPT_ENABLED), and throttle scope
("custom_ai_prompt") with the three dashboard/views.py actions covered in
dashboard/tests_custom_ai_prompt.py; the cross-endpoint shared-throttle
test lives there since it needs both URLs.

Run with:
    python manage.py test billing.tests.test_custom_ai_prompt_beta
"""

from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from ai_processor.models import ChatMessage
from dashboard.throttling import CustomAIPromptThrottle
from users.models import CustomUser, UserTypes

LOCMEM_CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
}


def make_user(user_type, email, **extra):
    return CustomUser.objects.create_user(
        email=email,
        password="testpass123",  # pragma: allowlist secret
        user_type=user_type,
        is_active=True,
        **extra,
    )


@override_settings(CACHES=LOCMEM_CACHES)
class BetaAnalyticsCustomAIPromptViewTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.url = reverse("analytics-custom-ai-prompt")
        self.superadmin = make_user(
            UserTypes.SUPER_ADMIN, "beta-super@example.com", is_superuser=True
        )
        self.teacher = make_user(UserTypes.TEACHER, "beta-teacher@example.com")
        self.school_admin = make_user(
            UserTypes.SCHOOL_ADMIN, "beta-schooladmin@example.com"
        )

    def test_non_superadmin_denied(self):
        for user in (self.teacher, self.school_admin):
            self.client.force_authenticate(user=user)
            response = self.client.post(self.url, {"prompt": "how's the cohort?"})
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch("billing.views.ai_processor.custom_ai_prompt_retry")
    def test_superadmin_allowed_and_forwards_raw_prompt(self, mock_retry):
        mock_retry.return_value = "Cohort insight here."
        self.client.force_authenticate(user=self.superadmin)

        response = self.client.post(
            self.url, {"prompt": "Who are our highest-intent leads?"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["response"], "Cohort insight here.")

        args, kwargs = mock_retry.call_args
        self.assertEqual(args[0], self.superadmin)
        self.assertEqual(args[2], "Who are our highest-intent leads?")
        self.assertEqual(kwargs["task_type"], "custom_ai_prompt:superadmin")

        message = ChatMessage.objects.get(content="Who are our highest-intent leads?")
        self.assertEqual(message.role, "user")

    @override_settings(DASHBOARD_CUSTOM_AI_PROMPT_ENABLED=False)
    def test_kill_switch_applies_to_beta_endpoint_too(self):
        self.client.force_authenticate(user=self.superadmin)
        before_count = ChatMessage.objects.count()

        response = self.client.post(self.url, {"prompt": "anything"})

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertEqual(
            response.data["error"],
            "The AI analytics assistant is temporarily unavailable. "
            "Please try again later.",
        )
        self.assertEqual(ChatMessage.objects.count(), before_count)

    def test_blank_prompt_rejected(self):
        self.client.force_authenticate(user=self.superadmin)
        response = self.client.post(self.url, {"prompt": ""})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_prompt_over_max_length_rejected(self):
        self.client.force_authenticate(user=self.superadmin)
        response = self.client.post(self.url, {"prompt": "a" * 2001})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch.object(CustomAIPromptThrottle, "rate", "2/min")
    @patch("billing.views.ai_processor.custom_ai_prompt_retry")
    def test_rate_limit_applies_to_beta_endpoint(self, mock_retry):
        mock_retry.return_value = "ok"
        self.client.force_authenticate(user=self.superadmin)

        first = self.client.post(self.url, {"prompt": "q1"})
        second = self.client.post(self.url, {"prompt": "q2"})
        third = self.client.post(self.url, {"prompt": "q3"})

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(third.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
