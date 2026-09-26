"""
Rate limit for the "Custom AI Prompt" dashboard chat.

One bucket, shared across all four surfaces that expose this feature
(SuperAdminDashboardView, SchoolAdminDashboardView, TeacherAdminDashboardView
in dashboard/views.py, and BetaAnalyticViewSet in billing/views.py) via
`throttle_classes=[CustomAIPromptThrottle]` on each action. Deliberately a
plain UserRateThrottle keyed on the authenticated user (not
ScopedRateThrottle): these are multi-action ViewSets, and ScopedRateThrottle
requires `throttle_scope` to already be a recognized attribute on the view
class for DRF's router to accept it as an @action kwarg - a fixed `.scope`
on the throttle class itself sidesteps that with no per-view wiring needed.

All four endpoints share this single scope on purpose, so a user with
access to more than one of them (e.g. a super-admin hitting both the
dashboard and billing beta-analytics variants) can't multiply their budget
by switching endpoints. Rate is defined in
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["custom_ai_prompt"].
"""

from rest_framework.throttling import UserRateThrottle


class CustomAIPromptThrottle(UserRateThrottle):
    scope = "custom_ai_prompt"
    # Declared explicitly (SimpleRateThrottle doesn't define a class-level
    # `rate`) so tests can `patch.object(CustomAIPromptThrottle, "rate", ...)`
    # to exercise a specific limit - see dashboard/tests_custom_ai_prompt.py
    # for why DEFAULT_THROTTLE_RATES can't be overridden per-test instead.
    rate = None
