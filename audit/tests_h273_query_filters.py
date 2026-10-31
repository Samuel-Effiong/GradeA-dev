"""
audit/tests_h273_query_filters.py
===================================
H-273 step 3: tests for the query API filters (trace_id, target_id),
target_label, target_name, dashed reference format, and the school-admin
scope restriction on target_name.

Mutant targets covered:
  M-H273-21: generate_request_id back to .hex
  M-H273-22: item_results reference back to .hex
  M-H273-23: drop dash-tolerant normalisation in trace_id filter
  M-H273-24: drop school-admin scope check on target_name
  M-H273-17: target_label mapping
  M-H273-18: target_name query count
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from audit import history
from audit.enums import ActorRole, AuditOutcome
from audit.filters import _normalise_uuid
from audit.models import AuditEvent
from audit.serializers import _TARGET_LABEL
from classrooms.models import School

CustomUser = get_user_model()

SUPER_ADMIN_URL = reverse("super-admin-audit-events")
SCHOOL_ADMIN_URL = reverse("school-admin-audit-events")

_UUID_DASHED_RE = (
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def rows_of(response):
    payload = response.json()["data"]
    return payload["results"] if isinstance(payload, dict) else payload


def _make_event(*, school_id=None, trace_id=None, target_type="Assignment", **extra):
    defaults = {
        "actor_id": uuid.uuid4(),
        "actor_role": ActorRole.TEACHER,
        "actor_email": "teacher@example.com",
        "school_id": school_id,
        "action": "ASSIGNMENT_CREATE",
        "target_type": target_type,
        "target_id": uuid.uuid4(),
        "outcome": AuditOutcome.SUCCESS,
        "trace_id": trace_id or uuid.uuid4(),
        "retention_class": "GENERAL",
    }
    defaults.update(extra)
    return AuditEvent.objects.create(**defaults)


class _Fixtures(TestCase):
    client: APIClient

    def setUp(self):
        self.client = APIClient()
        self.school_a = School.objects.create(name="Alpha School H273")
        self.school_b = School.objects.create(name="Bravo School H273")
        with history.suppressed():
            self.superadmin = CustomUser.objects.create_user(
                email="root.h273.filters@example.com",
                password="testpass123",  # pragma: allowlist secret
                user_type="SUPER_ADMIN",
                is_active=True,
                is_staff=True,
                is_superuser=True,
            )
            self.admin_a = CustomUser.objects.create_user(
                email="admin.a.h273@example.com",
                password="testpass123",  # pragma: allowlist secret
                user_type="SCHOOL_ADMIN",
                school=self.school_a,
                is_active=True,
            )

    def as_superadmin(self):
        self.client.force_authenticate(user=self.superadmin)

    def as_admin_a(self):
        self.client.force_authenticate(user=self.admin_a)


# ── trace_id filter ──────────────────────────────────────────────────────


class TraceIdFilterTests(_Fixtures):
    """M-H273-23: drop dash-tolerant normalisation → undashed test fails."""

    def setUp(self):
        super().setUp()
        self.trace = uuid.uuid4()
        self.event = _make_event(school_id=self.school_a.id, trace_id=self.trace)
        _make_event(school_id=self.school_a.id)
        self.as_superadmin()

    def test_dashed_uuid_finds_the_row(self):
        response = self.client.get(SUPER_ADMIN_URL, {"trace_id": str(self.trace)})
        ids = {r["id"] for r in rows_of(response)}
        self.assertEqual(ids, {str(self.event.id)})

    def test_undashed_uuid_finds_the_row(self):
        response = self.client.get(SUPER_ADMIN_URL, {"trace_id": self.trace.hex})
        ids = {r["id"] for r in rows_of(response)}
        self.assertEqual(ids, {str(self.event.id)})

    def test_upper_case_uuid_finds_the_row(self):
        response = self.client.get(
            SUPER_ADMIN_URL, {"trace_id": str(self.trace).upper()}
        )
        ids = {r["id"] for r in rows_of(response)}
        self.assertEqual(ids, {str(self.event.id)})

    def test_whitespace_padded_uuid_finds_the_row(self):
        response = self.client.get(SUPER_ADMIN_URL, {"trace_id": f"  {self.trace}  "})
        ids = {r["id"] for r in rows_of(response)}
        self.assertEqual(ids, {str(self.event.id)})

    def test_garbage_returns_400(self):
        response = self.client.get(SUPER_ADMIN_URL, {"trace_id": "not-a-uuid"})
        self.assertEqual(response.status_code, 400)

    def test_unknown_valid_uuid_returns_empty_list(self):
        response = self.client.get(SUPER_ADMIN_URL, {"trace_id": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(rows_of(response), [])


class NormaliseUuidUnitTests(TestCase):
    def test_dashed(self):
        u = uuid.uuid4()
        self.assertEqual(_normalise_uuid(str(u)), u)

    def test_undashed(self):
        u = uuid.uuid4()
        self.assertEqual(_normalise_uuid(u.hex), u)

    def test_upper(self):
        u = uuid.uuid4()
        self.assertEqual(_normalise_uuid(str(u).upper()), u)

    def test_whitespace(self):
        u = uuid.uuid4()
        self.assertEqual(_normalise_uuid(f"  {u}  "), u)

    def test_garbage_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            _normalise_uuid("garbage")

    def test_none_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            _normalise_uuid(None)


# ── target_id filter ─────────────────────────────────────────────────────


class TargetIdFilterTests(_Fixtures):
    def setUp(self):
        super().setUp()
        self.target = uuid.uuid4()
        self.event = _make_event(school_id=self.school_a.id, target_id=self.target)
        _make_event(school_id=self.school_a.id)
        self.as_superadmin()

    def test_target_id_filter_finds_the_row(self):
        response = self.client.get(SUPER_ADMIN_URL, {"target_id": str(self.target)})
        ids = {r["id"] for r in rows_of(response)}
        self.assertEqual(ids, {str(self.event.id)})

    def test_unknown_target_id_returns_empty(self):
        response = self.client.get(SUPER_ADMIN_URL, {"target_id": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(rows_of(response), [])


# ── target_label ─────────────────────────────────────────────────────────


class TargetLabelTests(_Fixtures):
    """M-H273-17: target_label mapping present on every known target_type."""

    def setUp(self):
        super().setUp()
        self.event = _make_event(school_id=self.school_a.id, target_type="CustomUser")
        self.as_superadmin()

    def test_known_target_type_gets_a_label(self):
        response = self.client.get(
            SUPER_ADMIN_URL, {"trace_id": str(self.event.trace_id)}
        )
        row = rows_of(response)[0]
        self.assertEqual(row["target_label"], "User account")

    def test_unknown_target_type_gets_null_label(self):
        event = _make_event(school_id=self.school_a.id, target_type="UnknownWidget")
        response = self.client.get(SUPER_ADMIN_URL, {"trace_id": str(event.trace_id)})
        row = rows_of(response)[0]
        self.assertIsNone(row["target_label"])

    def test_every_known_target_type_has_an_entry(self):
        for target_type, label in _TARGET_LABEL.items():
            with self.subTest(target_type=target_type):
                self.assertIsInstance(label, str)
                self.assertTrue(len(label) > 0)


# ── target_name (School-only, scoped) ────────────────────────────────────


class TargetNameSuperAdminTests(_Fixtures):
    """Super admin sees all school names."""

    def setUp(self):
        super().setUp()
        self.event = _make_event(
            school_id=self.school_a.id,
            target_type="School",
            target_id=self.school_a.id,
        )
        self.as_superadmin()

    def test_school_target_gets_name(self):
        response = self.client.get(
            SUPER_ADMIN_URL, {"trace_id": str(self.event.trace_id)}
        )
        row = rows_of(response)[0]
        self.assertEqual(row["target_name"], "Alpha School H273")

    def test_non_school_target_gets_null_name(self):
        event = _make_event(school_id=self.school_a.id, target_type="CustomUser")
        response = self.client.get(SUPER_ADMIN_URL, {"trace_id": str(event.trace_id)})
        row = rows_of(response)[0]
        self.assertIsNone(row["target_name"])

    def test_school_target_with_null_target_id_gets_null_name(self):
        event = _make_event(
            school_id=self.school_a.id,
            target_type="School",
            target_id=None,
        )
        response = self.client.get(SUPER_ADMIN_URL, {"trace_id": str(event.trace_id)})
        row = rows_of(response)[0]
        self.assertIsNone(row["target_name"])


class TargetNameSchoolAdminScopeTests(_Fixtures):
    """M-H273-24: drop the scope check → another school's name appears."""

    def setUp(self):
        super().setUp()
        self.own_event = _make_event(
            school_id=self.school_a.id,
            target_type="School",
            target_id=self.school_a.id,
        )
        self.other_event = _make_event(
            school_id=self.school_a.id,
            target_type="School",
            target_id=self.school_b.id,
        )

    def test_school_admin_sees_own_school_name(self):
        self.as_admin_a()
        response = self.client.get(
            SCHOOL_ADMIN_URL, {"trace_id": str(self.own_event.trace_id)}
        )
        row = rows_of(response)[0]
        self.assertEqual(row["target_name"], "Alpha School H273")

    def test_school_admin_does_not_see_other_schools_name(self):
        self.as_admin_a()
        response = self.client.get(
            SCHOOL_ADMIN_URL, {"trace_id": str(self.other_event.trace_id)}
        )
        row = rows_of(response)[0]
        self.assertIsNone(row["target_name"])


class TargetNameQueryCountTests(_Fixtures):
    """M-H273-18: per-row query instead of batch → assertNumQueries kills it.

    The endpoint's middleware (audit, user activity, billing wallet) adds
    queries beyond the view's own. We count School-lookup queries
    specifically: there must be exactly ONE `classrooms_school` SELECT
    regardless of how many School-target events are on the page.
    """

    def setUp(self):
        super().setUp()
        self.as_superadmin()

    def _school_query_count(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            self.client.get(SUPER_ADMIN_URL)
        return sum(
            1
            for q in ctx.captured_queries
            if "classrooms_school" in q["sql"] and "SELECT" in q["sql"]
        )

    def test_one_school_event_one_school_query(self):
        _make_event(
            school_id=self.school_a.id,
            target_type="School",
            target_id=self.school_a.id,
        )
        self.assertEqual(self._school_query_count(), 1)

    def test_five_school_events_still_one_school_query(self):
        for _ in range(5):
            _make_event(
                school_id=self.school_a.id,
                target_type="School",
                target_id=self.school_a.id,
            )
        self.assertEqual(self._school_query_count(), 1)


# ── dashed generate_request_id format ────────────────────────────────────


class DashedRequestIdFormatTests(TestCase):
    """M-H273-21: generate_request_id back to .hex → format test fails."""

    def test_generate_request_id_returns_dashed_uuid4(self):
        from AutoGrader.request_context import generate_request_id

        value = generate_request_id()
        self.assertEqual(len(value), 36)
        self.assertRegex(value, _UUID_DASHED_RE)

    def test_generated_id_round_trips_through_uuid(self):
        from AutoGrader.request_context import generate_request_id

        value = generate_request_id()
        self.assertEqual(str(uuid.UUID(value)), value)

    def test_generated_id_is_valid_for_inbound(self):
        from AutoGrader.request_context import generate_request_id, is_valid_request_id

        for _ in range(50):
            self.assertTrue(is_valid_request_id(generate_request_id()))


# ── dashed item_results reference ────────────────────────────────────────


class DashedItemResultsReferenceTests(TestCase):
    """M-H273-22: item_results reference back to .hex → dashed test fails."""

    def test_item_result_reference_is_dashed(self):
        from unittest.mock import MagicMock

        from students.item_results import item_result

        task = MagicMock()
        task.trace_id = uuid.uuid4()
        task.item_index = 0
        task.retry_count = 0
        task.error = None
        task.status = "SUCCESS"
        task.reason_code = None
        task.meta = {}
        task.file_name = "test.png"
        task.celery_task_id = "celery-fake"
        task.id = uuid.uuid4()
        task.submission_id = None

        result = item_result(task, context={})
        self.assertEqual(result["reference"], str(task.trace_id))
        self.assertRegex(result["reference"], _UUID_DASHED_RE)

    def test_null_trace_id_gives_null_reference(self):
        from unittest.mock import MagicMock

        from students.item_results import item_result

        task = MagicMock()
        task.trace_id = None
        task.item_index = 0
        task.retry_count = 0
        task.error = None
        task.status = "SUCCESS"
        task.reason_code = None
        task.meta = {}
        task.file_name = "test.png"
        task.celery_task_id = "celery-fake"
        task.id = uuid.uuid4()
        task.submission_id = None

        result = item_result(task, context={})
        self.assertIsNone(result["reference"])


# ── end-to-end: body reference = X-Request-ID = audit trace_id ───────────


class ReferenceTraceIdByteIdenticalTests(_Fixtures):
    """The refusal body's `reference`, the X-Request-ID response header, and
    the audit event's trace_id are the same string, character for character,
    all in dashed 8-4-4-4-12 form."""

    def setUp(self):
        super().setUp()
        with history.suppressed():
            self.teacher = CustomUser.objects.create_user(
                email="teacher.h273.e2e@example.com",
                password="testpass123",  # pragma: allowlist secret
                user_type="TEACHER",
                is_active=True,
            )

    def test_denied_admin_action_reference_matches_trace_id(self):
        """A teacher hitting a super-admin endpoint gets denied; the
        response header and audit trace_id must match, both dashed."""
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(SUPER_ADMIN_URL)
        self.assertEqual(response.status_code, 403)

        event = AuditEvent.objects.filter(action="ADMIN_ACTION").latest("occurred_at")
        header_id = response["X-Request-ID"]
        audit_trace = str(event.trace_id)

        self.assertEqual(header_id, audit_trace)
        self.assertRegex(header_id, _UUID_DASHED_RE)
