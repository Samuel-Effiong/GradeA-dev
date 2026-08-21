"""
dashboard/tests_custom_ai_prompt.py
=====================================
View-level (HTTP) tests for the "Custom AI Prompt" dashboard chat actions:
SuperAdminDashboardView.custom_ai_prompt, SchoolAdminDashboardView.custom_ai_prompt,
and TeacherAdminDashboardView.custom_ai_prompt (dashboard/views.py). The
billing/views.py BetaAnalyticViewSet variant, which shares the same
throttle scope and serializer, is covered separately in
billing/tests/test_custom_ai_prompt_beta.py.

Covers:
  - Permission boundaries (only the intended role reaches each endpoint).
  - Serializer validation: blank/missing prompt, max_length=2000 cap.
  - DASHBOARD_CUSTOM_AI_PROMPT_ENABLED kill switch, end-to-end through the
    real view (not just the service layer), including that a killed
    request does not persist a chat message (the append + AI call share
    one atomic block).
  - Rate limiting: the shared "custom_ai_prompt" throttle scope actually
    blocks a user who exceeds the configured rate, and does NOT block a
    different user.
  - Cross-tenant boundaries: a teacher's context never contains another
    teacher's course name; a school admin's context never contains
    another school's name.
  - The view forwards the RAW (unwrapped) prompt/context to the service
    layer and stores the RAW prompt in chat history - wrapping happens
    once, in AIProcessor.custom_ai_prompt, not per-view.

Run with:
    python manage.py test dashboard.tests_custom_ai_prompt
"""

from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from ai_processor.models import ChatMessage
from assignments.models import Assignment
from classrooms.models import Course, School
from dashboard.throttling import CustomAIPromptThrottle
from users.models import CustomUser, UserTypes

LOCMEM_CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
}

# DRF's SimpleRateThrottle reads DEFAULT_THROTTLE_RATES into a class
# attribute ONCE, when rest_framework.throttling is first imported - NOT
# on every request. @override_settings(REST_FRAMEWORK=...) updates
# django.conf.settings (and fires DRF's api_settings.reload()) but that
# frozen class attribute is never re-read, so overriding the rate via
# settings has no effect once the process has started. Patching `.rate`
# directly on our throttle class is what actually changes the rate a
# request is checked against - see SimpleRateThrottle.__init__, which
# skips calling get_rate() entirely when `.rate` is already set.


def make_user(user_type, email, **extra):
    return CustomUser.objects.create_user(
        email=email,
        password="testpass123",  # pragma: allowlist secret
        user_type=user_type,
        first_name="Test",
        last_name="User",
        is_active=True,
        **extra,
    )


@override_settings(CACHES=LOCMEM_CACHES)
class SuperAdminCustomAIPromptViewTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.url = reverse("dashboard-custom-ai-prompt")
        self.superadmin = make_user(
            UserTypes.SUPER_ADMIN, "view-super@example.com", is_superuser=True
        )
        self.teacher = make_user(UserTypes.TEACHER, "view-super-teacher@example.com")

    def test_non_superadmin_denied(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(self.url, {"prompt": "how are we doing?"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_superadmin_type_without_is_superuser_flag_denied(self):
        """IsSuperAdmin requires BOTH user_type==SUPER_ADMIN AND
        is_superuser=True - a user_type alone must not be enough."""
        weak_admin = make_user(
            UserTypes.SUPER_ADMIN, "weak-super@example.com", is_superuser=False
        )
        self.client.force_authenticate(user=weak_admin)
        response = self.client.post(self.url, {"prompt": "how are we doing?"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_denied(self):
        response = self.client.post(self.url, {"prompt": "how are we doing?"})
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    @patch("dashboard.views.ai_processor.custom_ai_prompt_retry")
    def test_superadmin_allowed_and_forwards_raw_prompt_and_context(self, mock_retry):
        mock_retry.return_value = "Here's the platform overview."
        self.client.force_authenticate(user=self.superadmin)

        response = self.client.post(self.url, {"prompt": "How is the platform doing?"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["response"], "Here's the platform overview.")

        mock_retry.assert_called_once()
        args, kwargs = mock_retry.call_args
        self.assertEqual(args[0], self.superadmin)
        # context is args[1], question is args[2] - the RAW question, not
        # wrapped in <untrusted_user_question> tags (that happens once,
        # inside AIProcessor.custom_ai_prompt).
        self.assertEqual(args[2], "How is the platform doing?")
        self.assertEqual(kwargs["task_type"], "custom_ai_prompt:superadmin")

        # The stored chat message is the raw question too.
        message = ChatMessage.objects.get(content="How is the platform doing?")
        self.assertEqual(message.role, "user")

    @override_settings(DASHBOARD_CUSTOM_AI_PROMPT_ENABLED=False)
    def test_kill_switch_returns_friendly_error_and_persists_nothing(self):
        self.client.force_authenticate(user=self.superadmin)
        before_count = ChatMessage.objects.count()

        response = self.client.post(self.url, {"prompt": "anything"})

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertEqual(
            response.data["error"],
            "The AI analytics assistant is temporarily unavailable. "
            "Please try again later.",
        )
        # append_dashboard_chat_message() and the AI call are in the same
        # atomic block - a kill-switch denial must not leave an orphaned
        # "user" turn with no reply.
        self.assertEqual(ChatMessage.objects.count(), before_count)

    def test_blank_prompt_rejected(self):
        self.client.force_authenticate(user=self.superadmin)
        response = self.client.post(self.url, {"prompt": ""})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_prompt_rejected(self):
        self.client.force_authenticate(user=self.superadmin)
        response = self.client.post(self.url, {})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_prompt_over_max_length_rejected(self):
        self.client.force_authenticate(user=self.superadmin)
        response = self.client.post(self.url, {"prompt": "a" * 2001})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("dashboard.views.ai_processor.custom_ai_prompt_retry")
    def test_prompt_at_max_length_accepted(self, mock_retry):
        mock_retry.return_value = "ok"
        self.client.force_authenticate(user=self.superadmin)
        response = self.client.post(self.url, {"prompt": "a" * 2000})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @patch.object(CustomAIPromptThrottle, "rate", "2/min")
    @patch("dashboard.views.ai_processor.custom_ai_prompt_retry")
    def test_rate_limit_blocks_after_configured_number_of_requests(self, mock_retry):
        mock_retry.return_value = "ok"
        self.client.force_authenticate(user=self.superadmin)

        first = self.client.post(self.url, {"prompt": "question one"})
        second = self.client.post(self.url, {"prompt": "question two"})
        third = self.client.post(self.url, {"prompt": "question three"})

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(third.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(mock_retry.call_count, 2)

    @patch.object(CustomAIPromptThrottle, "rate", "1/min")
    @patch("dashboard.views.ai_processor.custom_ai_prompt_retry")
    def test_rate_limit_is_per_user_not_global(self, mock_retry):
        mock_retry.return_value = "ok"
        other_superadmin = make_user(
            UserTypes.SUPER_ADMIN, "other-super@example.com", is_superuser=True
        )

        self.client.force_authenticate(user=self.superadmin)
        first = self.client.post(self.url, {"prompt": "hello"})
        self.assertEqual(first.status_code, status.HTTP_200_OK)

        self.client.force_authenticate(user=other_superadmin)
        second = self.client.post(self.url, {"prompt": "hello"})
        self.assertEqual(second.status_code, status.HTTP_200_OK)

    @patch.object(CustomAIPromptThrottle, "rate", "1/min")
    @patch("billing.views.ai_processor.custom_ai_prompt_retry")
    @patch("dashboard.views.ai_processor.custom_ai_prompt_retry")
    def test_rate_limit_is_shared_across_dashboard_and_billing_beta_endpoints(
        self, mock_dashboard_retry, mock_billing_retry
    ):
        """
        The throttle scope is deliberately the SAME for all four
        custom_ai_prompt actions (see dashboard/throttling.py) so a
        superadmin can't multiply their budget by switching between the
        dashboard and billing beta-analytics variants.
        """
        mock_dashboard_retry.return_value = "ok"
        mock_billing_retry.return_value = "ok"
        self.client.force_authenticate(user=self.superadmin)

        first = self.client.post(self.url, {"prompt": "hello"})
        self.assertEqual(first.status_code, status.HTTP_200_OK)

        beta_url = reverse("analytics-custom-ai-prompt")
        second = self.client.post(beta_url, {"prompt": "hello"})
        self.assertEqual(second.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


@override_settings(CACHES=LOCMEM_CACHES)
class SchoolAdminCustomAIPromptViewTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.url = reverse("school-admin-custom-ai-prompt")
        self.school_a = School.objects.create(name="Alpha School")
        self.school_b = School.objects.create(name="Beta School")
        self.admin_a = make_user(
            UserTypes.SCHOOL_ADMIN, "admin-a@example.com", school=self.school_a
        )
        self.admin_b = make_user(
            UserTypes.SCHOOL_ADMIN, "admin-b@example.com", school=self.school_b
        )
        self.teacher = make_user(UserTypes.TEACHER, "sa-view-teacher@example.com")

    def test_non_school_admin_denied(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(self.url, {"prompt": "how's my school doing?"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch("dashboard.views.ai_processor.custom_ai_prompt_retry")
    def test_school_admin_context_never_contains_other_schools_name(self, mock_retry):
        mock_retry.return_value = "ok"
        self.client.force_authenticate(user=self.admin_a)

        response = self.client.post(self.url, {"prompt": "how are we doing?"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        context = mock_retry.call_args[0][1]
        self.assertIn("Alpha School", context)
        self.assertNotIn("Beta School", context)

    @override_settings(DASHBOARD_CUSTOM_AI_PROMPT_ENABLED=False)
    def test_kill_switch_applies_to_school_admin_too(self):
        self.client.force_authenticate(user=self.admin_a)
        response = self.client.post(self.url, {"prompt": "anything"})
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)

    def test_prompt_over_max_length_rejected(self):
        self.client.force_authenticate(user=self.admin_a)
        response = self.client.post(self.url, {"prompt": "a" * 2001})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


@override_settings(CACHES=LOCMEM_CACHES)
class TeacherCustomAIPromptViewTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.url = reverse("teacher-admin-custom-ai-prompt")
        self.teacher_a = make_user(UserTypes.TEACHER, "teacher-a@example.com")
        self.teacher_b = make_user(UserTypes.TEACHER, "teacher-b@example.com")
        self.course_a = Course.objects.create(
            name="Alpha Course", teacher=self.teacher_a
        )
        self.course_b = Course.objects.create(
            name="Beta Course", teacher=self.teacher_b
        )
        Assignment.objects.create(course=self.course_a, title="Alpha Assignment")
        Assignment.objects.create(course=self.course_b, title="Beta Assignment")
        self.school_admin = make_user(
            UserTypes.SCHOOL_ADMIN, "ta-view-admin@example.com"
        )

    def test_non_teacher_denied(self):
        self.client.force_authenticate(user=self.school_admin)
        response = self.client.post(self.url, {"prompt": "how's my class doing?"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch("dashboard.views.ai_processor.custom_ai_prompt_retry")
    def test_teacher_context_never_contains_other_teachers_course(self, mock_retry):
        mock_retry.return_value = "ok"
        self.client.force_authenticate(user=self.teacher_a)

        response = self.client.post(self.url, {"prompt": "how's my class doing?"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        context = mock_retry.call_args[0][1]
        self.assertIn("Alpha Course", context)
        self.assertNotIn("Beta Course", context)
        self.assertIn("Alpha Assignment", context)
        self.assertNotIn("Beta Assignment", context)

    @patch("dashboard.views.ai_processor.custom_ai_prompt_retry")
    def test_teacher_prompt_uses_teacher_role_and_task_type(self, mock_retry):
        mock_retry.return_value = "ok"
        self.client.force_authenticate(user=self.teacher_a)

        self.client.post(self.url, {"prompt": "how's my class doing?"})

        _, kwargs = mock_retry.call_args
        self.assertEqual(kwargs["task_type"], "custom_ai_prompt:teacher")

    def test_blank_prompt_rejected(self):
        self.client.force_authenticate(user=self.teacher_a)
        response = self.client.post(self.url, {"prompt": ""})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(DASHBOARD_CUSTOM_AI_PROMPT_ENABLED=False)
    def test_kill_switch_applies_to_teacher_too(self):
        self.client.force_authenticate(user=self.teacher_a)
        response = self.client.post(self.url, {"prompt": "anything"})
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)


@override_settings(CACHES=LOCMEM_CACHES)
class SuperAdminUnmeteredEndToEndTest(APITestCase):
    """
    Confirms requirement #3 end-to-end through the real HTTP view and the
    real (unmocked) AIProcessor.custom_ai_prompt_retry -> execute_graded_task
    chain, mocking only the outbound LLM call itself
    (AIProcessor._AIProcessor__ai_model, name-mangled). A superadmin with
    zero credits and no wallet activity must still get a successful
    response, and their wallet (if one exists at all) must be untouched -
    proving the throttle/kill-switch/wrapping changes in this module did
    not disturb execute_graded_task's unmetered superadmin bypass.
    """

    def setUp(self):
        cache.clear()
        self.url = reverse("dashboard-custom-ai-prompt")
        self.superadmin = make_user(
            UserTypes.SUPER_ADMIN, "unmetered-super@example.com", is_superuser=True
        )

    @patch("ai_processor.services.AIProcessor._AIProcessor__ai_model")
    def test_superadmin_call_succeeds_fully_unmetered(self, mock_ai_model):
        from unittest.mock import MagicMock

        from billing.models import CreditWallet

        response_obj = MagicMock()
        response_obj.choices = [MagicMock()]
        response_obj.choices[0].message.content = "Here's the answer."
        mock_ai_model.return_value = response_obj

        self.client.force_authenticate(user=self.superadmin)
        response = self.client.post(self.url, {"prompt": "how are we doing?"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["response"], "Here's the answer.")

        wallet = CreditWallet.objects.filter(user=self.superadmin).first()
        if wallet is not None:
            self.assertEqual(wallet.total_remaining_credits(), 0)
