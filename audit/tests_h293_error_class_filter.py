"""H-293: the activity log can be filtered by error class.

Owner ask 2026-11-05: "add a new filter in the audit event, filter by error
class". error_class tells you where a failure came from (USER, VALIDATION,
PROVIDER, MODEL, SYSTEM); SYSTEM is the one that also lands in Sentry, so
`?error_class=SYSTEM` is the "show me the crashes" view. Same rules as the
outcome filter: an unknown value is a 400, never silently ignored, and the
School Admin endpoint stays scoped to the caller's own school.
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from audit import history
from audit.enums import ActorRole, AuditOutcome, ErrorClass
from audit.models import AuditEvent
from classrooms.models import School

CustomUser = get_user_model()

SUPER_ADMIN_URL = reverse("super-admin-audit-events")
SCHOOL_ADMIN_URL = reverse("school-admin-audit-events")


def rows_of(response):
    payload = response.json()["data"]
    return payload["results"] if isinstance(payload, dict) else payload


def _event(school_id, error_class=None, outcome=AuditOutcome.FAILURE):
    return AuditEvent.objects.create(
        actor_id=uuid.uuid4(),
        actor_role=ActorRole.TEACHER,
        actor_email="teacher@example.com",
        school_id=school_id,
        action="STATE_CHANGE",
        target_type="Assignment",
        target_id=uuid.uuid4(),
        outcome=outcome if error_class else AuditOutcome.SUCCESS,
        error_class=error_class,
        trace_id=uuid.uuid4(),
        retention_class="GENERAL",
    )


class ErrorClassFilterTests(TestCase):
    client: APIClient

    def setUp(self):
        self.client = APIClient()
        self.school_a = School.objects.create(name="Alpha H293")
        self.school_b = School.objects.create(name="Bravo H293")
        with history.suppressed():
            self.superadmin = CustomUser.objects.create_user(
                email="root.h293@example.com",
                password="testpass123",  # pragma: allowlist secret
                user_type="SUPER_ADMIN",
                is_active=True,
                is_staff=True,
                is_superuser=True,
            )
            self.admin_a = CustomUser.objects.create_user(
                email="admin.a.h293@example.com",
                password="testpass123",  # pragma: allowlist secret
                user_type="SCHOOL_ADMIN",
                school=self.school_a,
                is_active=True,
            )
        self.system_a = _event(self.school_a.id, ErrorClass.SYSTEM)
        self.user_a = _event(self.school_a.id, ErrorClass.USER)
        self.success_a = _event(self.school_a.id)
        self.system_b = _event(self.school_b.id, ErrorClass.SYSTEM)

    def _ids(self, url, params):
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200, response.content)
        return {r["id"] for r in rows_of(response)}

    def test_super_admin_filters_by_error_class(self):
        self.client.force_authenticate(user=self.superadmin)
        self.assertEqual(
            self._ids(SUPER_ADMIN_URL, {"error_class": "SYSTEM"}),
            {str(self.system_a.id), str(self.system_b.id)},
        )

    def test_another_class_returns_only_its_rows(self):
        self.client.force_authenticate(user=self.superadmin)
        self.assertEqual(
            self._ids(SUPER_ADMIN_URL, {"error_class": "USER"}),
            {str(self.user_a.id)},
        )

    def test_school_admin_filter_stays_inside_their_school(self):
        self.client.force_authenticate(user=self.admin_a)
        self.assertEqual(
            self._ids(SCHOOL_ADMIN_URL, {"error_class": "SYSTEM"}),
            {str(self.system_a.id)},
        )

    def test_combines_with_outcome(self):
        self.client.force_authenticate(user=self.superadmin)
        self.assertEqual(
            self._ids(
                SUPER_ADMIN_URL,
                {
                    "error_class": "SYSTEM",
                    "outcome": "FAILURE",
                    "school_id": str(self.school_b.id),
                },
            ),
            {str(self.system_b.id)},
        )

    def test_unknown_value_is_a_400_not_ignored(self):
        self.client.force_authenticate(user=self.superadmin)
        response = self.client.get(SUPER_ADMIN_URL, {"error_class": "CRASH"})
        self.assertEqual(response.status_code, 400)

    def test_no_filter_still_returns_everything(self):
        self.client.force_authenticate(user=self.superadmin)
        self.assertEqual(len(self._ids(SUPER_ADMIN_URL, {})), 4)

    def test_documented_in_the_openapi_schema(self):
        from drf_spectacular.generators import SchemaGenerator

        schema = SchemaGenerator().get_schema(request=None, public=True)
        for path in (
            "/api/v1/super-admin/audit/events",
            "/api/v1/school-admin/audit/events",
        ):
            op = next(
                v["get"] for k, v in schema["paths"].items() if k.rstrip("/") == path
            )
            names = {p["name"] for p in op.get("parameters", [])}
            self.assertIn("error_class", names, path)
