"""
audit/tests_query_api.py
=========================
Tests for the audit query API (§8 of docs/phase2/architecture/
04_epic_a_implementation_plan.md).

Mirrors billing/tests/test_credit_endpoint_tenant_isolation.py's pattern:
assertions on row IDENTITY, never on a bare count, and the FR-A-09
adversarial case (a School Admin querying another school's data) is run at
both the queryset level (unit) and the full request/response level
(integration), per §8.3.
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from audit import history
from classrooms.models import School

from .enums import ActorRole, AuditOutcome
from .models import AuditEvent

CustomUser = get_user_model()

SUPER_ADMIN_URL = reverse("super-admin-audit-events")
SCHOOL_ADMIN_URL = reverse("school-admin-audit-events")


def rows_of(response):
    """APIJSONRenderer wraps everything as {"success", "message", "data"};
    pagination puts the page under data["results"]."""
    payload = response.json()["data"]
    return payload["results"] if isinstance(payload, dict) else payload


def _make_event(*, school_id, action="ASSIGNMENT_CREATE", actor_id=None, **extra):
    defaults = {
        "actor_id": actor_id or uuid.uuid4(),
        "actor_role": ActorRole.TEACHER,
        "actor_email": "teacher@example.com",
        "school_id": school_id,
        "action": action,
        "target_type": "Assignment",
        "target_id": uuid.uuid4(),
        "outcome": AuditOutcome.SUCCESS,
        "trace_id": uuid.uuid4(),
        "retention_class": "GENERAL",
    }
    defaults.update(extra)
    return AuditEvent.objects.create(**defaults)


class _TwoSchools(TestCase):
    """Two unrelated schools, each with its own School Admin and events."""

    client: APIClient

    def setUp(self):
        self.client = APIClient()
        self.school_a = School.objects.create(name="School A")
        self.school_b = School.objects.create(name="School B")
        self.school_a_id = self.school_a.id
        self.school_b_id = self.school_b.id

        # Epic A S4: these fixture accounts are privileged, so creating them
        # is itself a PERMISSION_CHANGE; these tests are about something else.
        with history.suppressed():
            self.admin_a = CustomUser.objects.create_user(
                email="admin.a@example.com",
                password="testpass123",  # pragma: allowlist secret
                user_type="SCHOOL_ADMIN",
                school=self.school_a,
                is_active=True,
            )
            self.admin_b = CustomUser.objects.create_user(
                email="admin.b@example.com",
                password="testpass123",  # pragma: allowlist secret
                user_type="SCHOOL_ADMIN",
                school=self.school_b,
                is_active=True,
            )
            self.superadmin = CustomUser.objects.create_user(
                email="root.audit@example.com",
                password="testpass123",  # pragma: allowlist secret
                user_type="SUPER_ADMIN",
                is_active=True,
                is_staff=True,
                is_superuser=True,
            )
            self.teacher = CustomUser.objects.create_user(
                email="teacher.audit@example.com",
                password="testpass123",  # pragma: allowlist secret
                user_type="TEACHER",
                is_active=True,
            )

        self.event_a = _make_event(school_id=self.school_a_id)
        self.event_b = _make_event(school_id=self.school_b_id)

    def as_admin_a(self):
        self.client.force_authenticate(user=self.admin_a)

    def as_admin_b(self):
        self.client.force_authenticate(user=self.admin_b)

    def as_superadmin(self):
        self.client.force_authenticate(user=self.superadmin)

    def as_teacher(self):
        self.client.force_authenticate(user=self.teacher)


class SuperAdminAuditEventPermissionTests(_TwoSchools):
    def test_non_super_admin_gets_403(self):
        self.as_admin_a()
        response = self.client.get(SUPER_ADMIN_URL)
        self.assertEqual(response.status_code, 403)

    def test_teacher_gets_403(self):
        self.as_teacher()
        response = self.client.get(SUPER_ADMIN_URL)
        self.assertEqual(response.status_code, 403)

    def test_super_admin_sees_events_from_both_schools(self):
        self.as_superadmin()
        response = self.client.get(SUPER_ADMIN_URL)
        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in rows_of(response)}
        self.assertIn(str(self.event_a.id), ids)
        self.assertIn(str(self.event_b.id), ids)

    def test_super_admin_can_filter_by_school_id(self):
        self.as_superadmin()
        response = self.client.get(
            SUPER_ADMIN_URL, {"school_id": str(self.school_a_id)}
        )
        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in rows_of(response)}
        self.assertEqual(ids, {str(self.event_a.id)})


class SchoolAdminAuditEventPermissionTests(_TwoSchools):
    def test_teacher_gets_403(self):
        self.as_teacher()
        response = self.client.get(SCHOOL_ADMIN_URL)
        self.assertEqual(response.status_code, 403)

    def test_school_admin_sees_only_own_school(self):
        self.as_admin_a()
        response = self.client.get(SCHOOL_ADMIN_URL)
        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in rows_of(response)}
        self.assertEqual(ids, {str(self.event_a.id)})


class SchoolAdminCrossTenantAdversarialTests(_TwoSchools):
    """FR-A-09's named adversarial case, at both the queryset and the
    request/response level, per §8.3."""

    def test_queryset_level_ignores_other_schools_id_filter(self):
        """Unit level: even though the view itself scopes get_queryset() to
        request.user.school_id, pin the underlying invariant directly - a
        queryset built for admin_b never contains admin_a's school's rows,
        regardless of what filter values might be passed alongside it."""
        from .views import SchoolAdminAuditEventListView

        view = SchoolAdminAuditEventListView()
        request = type("R", (), {"user": self.admin_b})()
        view.request = request
        queryset = view.get_queryset()

        ids = {str(pk) for pk in queryset.values_list("id", flat=True)}
        self.assertEqual(ids, {str(self.event_b.id)})
        self.assertNotIn(str(self.event_a.id), ids)

    def test_request_response_level_school_id_param_is_ignored(self):
        """Integration level: School Admin B queries the endpoint and tries
        to pass School A's school_id as a filter param. Must get back an
        empty-of-A, well-formed result scoped to B's own school - never A's
        rows, never an error revealing A exists."""
        self.as_admin_b()
        response = self.client.get(
            SCHOOL_ADMIN_URL, {"school_id": str(self.school_a_id)}
        )

        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in rows_of(response)}
        self.assertEqual(ids, {str(self.event_b.id)})
        self.assertNotIn(str(self.event_a.id), ids)

    def test_admin_a_never_sees_school_b_by_any_other_filter_either(self):
        """Same adversarial intent via a different lever: filtering by
        School B's own actor/action values still cannot surface School B's
        row through School Admin A's endpoint."""
        self.as_admin_a()
        response = self.client.get(SCHOOL_ADMIN_URL, {"action": self.event_b.action})
        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in rows_of(response)}
        self.assertNotIn(str(self.event_b.id), ids)
