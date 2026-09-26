"""
audit/tests_admin_action.py
=============================
Tests for the automatic ADMIN_ACTION audit coverage (§6's "Admin action"
category - see audit/admin_action.py's module docstring for the design).

Two test classes:

* `AutomaticCoverageTests` - the plan's own emphasis: a BRAND NEW view
  that only declares `permission_classes = [IsSuperAdmin]`, with no
  get_audit_target, no emit() call, nothing else - and proves it still
  gets exactly one well-formed ADMIN_ACTION event (FR-A-01's literal
  "exactly one", not ">= 1"). Runs the view through
  AdminActionAuditMiddleware directly (an APIRequestFactory unit), not
  through a registered URL, precisely so nothing endpoint-specific is
  wired up on its behalf.

* `RealEndpointIntegrationTests` - the full request/response path,
  through the actual Django MIDDLEWARE stack and a real, already-existing
  IsSuperAdmin endpoint (the audit query API's own super-admin list view),
  proving the wiring is really registered in settings.py and not just
  unit-testable in isolation.
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.response import Response
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate
from rest_framework.views import APIView

from audit.middleware import AdminActionAuditMiddleware
from audit.models import AuditEvent
from classrooms.permissions import IsSuperAdmin

CustomUser = get_user_model()


class DummySuccessView(APIView):
    """A brand-new endpoint. No get_audit_target, no emit() call, no
    mixin - the only thing it declares is the permission class every
    IsSuperAdmin-gated endpoint already declares."""

    permission_classes = [IsSuperAdmin]

    def get(self, request):
        return Response({"ok": True}, status=200)


class DummyFailingView(APIView):
    """Same, but the view itself fails once it's running - proves the
    FAILURE branch (known only after the view runs, not at the permission
    check) is covered too."""

    permission_classes = [IsSuperAdmin]

    def get(self, request):
        return Response({"error": "boom"}, status=500)


def _run(view_class, user):
    """Exercise `view_class` through AdminActionAuditMiddleware exactly as
    MIDDLEWARE would, without registering any URL for it."""
    factory = APIRequestFactory()
    request = factory.get("/dummy-admin-endpoint")
    force_authenticate(request, user=user)
    middleware = AdminActionAuditMiddleware(view_class.as_view())
    return middleware(request)


class _Users(TestCase):
    def setUp(self):
        self.superadmin = CustomUser.objects.create_user(
            email="root.admin-action@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type="SUPER_ADMIN",
            is_active=True,
            is_staff=True,
            is_superuser=True,
        )
        self.teacher = CustomUser.objects.create_user(
            email="teacher.admin-action@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type="TEACHER",
            is_active=True,
        )


class AutomaticCoverageTests(_Users):
    def test_new_view_granted_produces_exactly_one_success_event(self):
        response = _run(DummySuccessView, self.superadmin)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AuditEvent.objects.count(), 1)
        event = AuditEvent.objects.get()
        self.assertEqual(event.action, "ADMIN_ACTION")
        self.assertEqual(event.outcome, "SUCCESS")
        self.assertIsNone(event.error_class)
        self.assertEqual(event.actor_id, self.superadmin.id)
        self.assertEqual(event.target_type, "DummySuccessView")
        self.assertEqual(event.metadata, {"source": "DummySuccessView"})

    def test_new_view_denied_produces_exactly_one_denied_event(self):
        response = _run(DummySuccessView, self.teacher)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(AuditEvent.objects.count(), 1)
        event = AuditEvent.objects.get()
        self.assertEqual(event.action, "ADMIN_ACTION")
        self.assertEqual(event.outcome, "DENIED")
        self.assertEqual(event.error_class, "USER")
        self.assertEqual(event.actor_id, self.teacher.id)

    def test_new_view_that_fails_at_runtime_produces_exactly_one_failure_event(self):
        """The outcome here is only knowable AFTER the view runs - proves
        the split between the permission-class DENIED path and the
        middleware's post-response SUCCESS/FAILURE path is load-bearing,
        not just a SUCCESS-only demo."""
        response = _run(DummyFailingView, self.superadmin)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(AuditEvent.objects.count(), 1)
        event = AuditEvent.objects.get()
        self.assertEqual(event.outcome, "FAILURE")
        self.assertEqual(event.error_class, "SYSTEM")
        self.assertEqual(event.target_type, "DummyFailingView")

    def test_unauthenticated_request_is_also_recorded_as_denied(self):
        response = _run(DummySuccessView, user=None)

        # DRF's own permission_denied() picks 401 over 403 whenever there
        # is no successful_authenticator at all, regardless of what
        # has_permission() returned - IsSuperAdmin still ran and recorded
        # the denial either way.
        self.assertEqual(response.status_code, 401)
        self.assertEqual(AuditEvent.objects.count(), 1)
        event = AuditEvent.objects.get()
        self.assertEqual(event.outcome, "DENIED")
        self.assertEqual(event.actor_role, "SYSTEM")

    def test_a_view_defining_get_audit_target_is_honoured(self):
        target_id = uuid.uuid4()

        class DummyViewWithTarget(APIView):
            permission_classes = [IsSuperAdmin]

            def get_audit_target(self, request):
                return "SpecificResource", target_id

            def get(self, request):
                return Response(status=200)

        _run(DummyViewWithTarget, self.superadmin)

        event = AuditEvent.objects.get()
        self.assertEqual(event.target_type, "SpecificResource")
        self.assertEqual(event.target_id, target_id)


class RealEndpointIntegrationTests(_Users):
    """The full stack, through Django's real MIDDLEWARE setting and a real
    endpoint that predates this change (the audit query API's own
    super-admin list view) - not a hand-wired unit."""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.url = reverse("super-admin-audit-events")

    def test_real_endpoint_success_is_audited_end_to_end(self):
        self.client.force_authenticate(user=self.superadmin)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        admin_action_events = AuditEvent.objects.filter(action="ADMIN_ACTION")
        self.assertEqual(admin_action_events.count(), 1)
        event = admin_action_events.get()
        self.assertEqual(event.outcome, "SUCCESS")
        self.assertEqual(event.target_type, "SuperAdminAuditEventListView")

    def test_real_endpoint_denial_is_audited_end_to_end(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)
        admin_action_events = AuditEvent.objects.filter(action="ADMIN_ACTION")
        self.assertEqual(admin_action_events.count(), 1)
        self.assertEqual(admin_action_events.get().outcome, "DENIED")
