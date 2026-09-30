"""S1 (plan 08 §2): every state-changing request by an authenticated user
leaves exactly one audit event - its named event, or the generic STATE_CHANGE.

Real routes through the real middleware stack, so the actor, the route name and
the outcome are what production would record.
"""

import threading
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from audit import context as audit_context
from audit.enums import AuditAction, AuditOutcome, ErrorClass, RetentionClass
from audit.models import AuditEvent
from audit.request_audit import EXCLUDED_ROUTES, should_record
from users.models import UserTypes

User = get_user_model()

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
PASSWORD = "a-strong-password-42"  # pragma: allowlist secret
SENTINEL = "zq-sentinel-7f3a"


def make_user(email, user_type=UserTypes.TEACHER, **extra):
    user = User.objects.create_user(
        email=email,
        password=PASSWORD,
        first_name="State",
        last_name="Change",
        user_type=user_type,
        is_active=True,
        email_verified_at=timezone.now(),
        **extra,
    )
    return user


def make_superadmin():
    admin = User.objects.create_superuser(
        email="state.super@example.com",
        password=PASSWORD,
        first_name="Super",
        last_name="Admin",
    )
    admin.user_type = UserTypes.SUPER_ADMIN
    admin.is_active = True
    admin.save()
    return admin


@override_settings(CACHES=LOCMEM_CACHE)
class ExactlyOneEventTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.teacher = make_user("state.teacher@example.com")

    def post_as(self, user, url, data=None, method="post"):
        self.client.force_authenticate(user=user)
        return getattr(self.client, method)(url, data or {}, format="json")

    def test_a_plain_write_with_no_named_event_records_one_state_change(self):
        response = self.post_as(self.teacher, reverse("session-list"), {"name": "T1"})

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditAction.STATE_CHANGE)
        self.assertEqual(event.outcome, AuditOutcome.SUCCESS)
        self.assertIsNone(event.error_class)
        self.assertEqual(event.actor_id, self.teacher.id)
        self.assertEqual(event.actor_role, UserTypes.TEACHER)
        self.assertEqual(event.target_type, "SessionViewSet")
        self.assertEqual(
            event.metadata,
            {"route": "session-list", "method": "POST", "http_status": 201},
        )
        self.assertIsNotNone(event.trace_id)

    def test_a_failed_write_records_one_failure(self):
        response = self.post_as(self.teacher, reverse("session-list"), {})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditAction.STATE_CHANGE)
        self.assertEqual(
            (event.outcome, event.error_class),
            (AuditOutcome.FAILURE, ErrorClass.USER),
        )

    def test_a_refused_write_records_one_denied(self):
        student = make_user("state.student@example.com", UserTypes.STUDENT)

        response = self.post_as(student, reverse("session-list"), {"name": "T1"})

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditAction.STATE_CHANGE)
        self.assertEqual(event.outcome, AuditOutcome.DENIED)

    def test_a_missing_target_records_its_id_from_the_url(self):
        missing = uuid.uuid4()

        response = self.post_as(
            self.teacher,
            reverse("session-detail", kwargs={"pk": missing}),
            method="delete",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        event = AuditEvent.objects.get()
        self.assertEqual(event.outcome, AuditOutcome.FAILURE)
        self.assertEqual(event.target_id, missing)
        self.assertEqual(event.metadata["route"], "session-detail")
        self.assertEqual(event.metadata["method"], "DELETE")

    def test_a_route_with_a_named_event_records_only_that_event(self):
        """Logout emits AUTH_LOGOUT; no STATE_CHANGE is added."""
        from users.tokens import EpochRefreshToken

        refresh = EpochRefreshToken.for_user(self.teacher)

        response = self.post_as(
            self.teacher, reverse("auth-logout"), {"refresh": str(refresh)}
        )

        self.assertEqual(response.status_code, status.HTTP_205_RESET_CONTENT)
        self.assertEqual(
            list(AuditEvent.objects.values_list("action", flat=True)),
            [AuditAction.AUTH_LOGOUT],
        )

    def test_a_superadmin_write_records_only_its_admin_action(self):
        response = self.post_as(make_superadmin(), reverse("school-list"), {})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            list(AuditEvent.objects.values_list("action", "outcome")),
            [(AuditAction.ADMIN_ACTION, AuditOutcome.FAILURE)],
        )

    def test_a_denied_superadmin_route_records_only_its_admin_action(self):
        response = self.post_as(self.teacher, reverse("school-list"), {})

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            list(AuditEvent.objects.values_list("action", "outcome")),
            [(AuditAction.ADMIN_ACTION, AuditOutcome.DENIED)],
        )

    def test_an_excluded_route_records_nothing(self):
        with patch("users.views.send_mail"):
            response = self.post_as(
                self.teacher, reverse("auth-request-change-password")
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(AuditEvent.objects.exists())

    def test_a_read_records_nothing(self):
        self.client.force_authenticate(user=self.teacher)

        response = self.client.get(reverse("session-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(AuditEvent.objects.exists())

    def test_an_anonymous_write_records_nothing(self):
        response = self.client.post(
            reverse("session-list"), {"name": "T1"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(AuditEvent.objects.exists())

    def test_a_students_write_is_a_student_record_without_address_or_email(self):
        student = make_user("state.student2@example.com", UserTypes.STUDENT)

        self.post_as(student, reverse("session-list"), {"name": "T1"})

        event = AuditEvent.objects.get()
        self.assertEqual(event.retention_class, RetentionClass.STUDENT_RECORD)
        self.assertIsNone(event.actor_email)
        self.assertIsNone(event.source_ip)
        self.assertIsNone(event.user_agent)

    def test_the_request_body_is_never_recorded(self):
        self.post_as(
            self.teacher,
            reverse("session-list"),
            {"name": f"Term {SENTINEL}", "notes": SENTINEL},
        )

        event = AuditEvent.objects.get()
        stored = " ".join(
            str(value) for value in AuditEvent.objects.values().get().values()
        )
        self.assertNotIn(SENTINEL, stored)
        self.assertEqual(event.action, AuditAction.STATE_CHANGE)

    def test_the_route_is_searchable_through_the_query_api(self):
        self.post_as(self.teacher, reverse("session-list"), {"name": "T1"})
        self.post_as(self.teacher, reverse("session-list"), {})
        # A different route, which the filter must leave out.
        self.post_as(
            self.teacher,
            reverse("session-detail", kwargs={"pk": uuid.uuid4()}),
            method="delete",
        )
        self.assertEqual(AuditEvent.objects.count(), 3)

        response = self.post_as(
            make_superadmin(),
            reverse("super-admin-audit-events") + "?route=session-list",
            method="get",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        rows = body.get("data", body)
        rows = rows.get("results", rows)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["action"] == AuditAction.STATE_CHANGE for r in rows))


@override_settings(CACHES=LOCMEM_CACHE)
class NeverFailsTheUserTests(APITestCase):
    """FR-A-11: a broken audit store costs the event, never the response."""

    def test_a_write_still_succeeds_when_the_audit_store_is_down(self):
        cache.clear()
        teacher = make_user("state.down@example.com")
        self.client.force_authenticate(user=teacher)

        with patch.object(
            AuditEvent.objects, "create", side_effect=RuntimeError("store down")
        ):
            response = self.client.post(
                reverse("session-list"), {"name": "T1"}, format="json"
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertFalse(AuditEvent.objects.exists())

    def test_a_failed_named_event_falls_back_to_the_generic_one(self):
        """Only a STORED event suppresses the fallback, so a rejected named
        write still leaves the request traceable."""
        cache.clear()
        teacher = make_user("state.fallback@example.com")
        self.client.force_authenticate(user=teacher)
        from users.tokens import EpochRefreshToken

        real_create = AuditEvent.objects.create

        def reject_logout(**fields):
            if fields.get("action") == AuditAction.AUTH_LOGOUT:
                raise RuntimeError("logout event lost")
            return real_create(**fields)

        with patch.object(AuditEvent.objects, "create", side_effect=reject_logout):
            self.client.post(
                reverse("auth-logout"),
                {"refresh": str(EpochRefreshToken.for_user(teacher))},
                format="json",
            )

        self.assertEqual(
            list(AuditEvent.objects.values_list("action", flat=True)),
            [AuditAction.STATE_CHANGE],
        )


class RequestStateIsolationTests(SimpleTestCase):
    def test_state_is_per_request_and_restored_on_exception(self):
        with self.assertRaises(ValueError):
            with audit_context.request_audit_state():
                audit_context.mark_named_emitted()
                raise ValueError
        self.assertIsNone(audit_context._request_state_var.get())

    def test_concurrent_requests_never_share_state(self):
        seen = {}
        barrier = threading.Barrier(2)

        def run(name, mark):
            with audit_context.request_audit_state() as state:
                barrier.wait()
                if mark:
                    audit_context.mark_named_emitted()
                barrier.wait()
                seen[name] = state.named_emitted

        threads = [
            threading.Thread(target=run, args=("a", True)),
            threading.Thread(target=run, args=("b", False)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(seen, {"a": True, "b": False})

    def test_marking_outside_a_request_is_a_no_op(self):
        audit_context.mark_named_emitted()
        self.assertIsNone(audit_context._request_state_var.get())

    def test_a_throttled_request_is_not_recorded(self):
        """Only the 429 stops it: the same authenticated write on a real,
        non-excluded route with a 201 IS recorded (the control)."""
        request = SimpleNamespace(
            method="POST",
            user=SimpleNamespace(is_authenticated=True),
            resolver_match=SimpleNamespace(view_name="session-list"),
        )

        self.assertTrue(should_record(request, SimpleNamespace(status_code=201)))
        self.assertFalse(should_record(request, SimpleNamespace(status_code=429)))

    def test_every_exclusion_has_a_reason(self):
        for route, reason in EXCLUDED_ROUTES.items():
            with self.subTest(route=route):
                self.assertTrue(reason.strip())


@override_settings(CACHES=LOCMEM_CACHE)
class AnonymousFailureScopingTests(APITestCase):
    """Gate 9 for ANONYMOUS sign-in failures (SM ruling): scoped to the
    TARGETED account's school. A school admin sees attempts on its own
    school's accounts and never another school's."""

    def setUp(self):
        from classrooms.models import School

        cache.clear()
        self.addCleanup(cache.clear)
        self.school_a = School.objects.create(name="Scope A")
        self.school_b = School.objects.create(name="Scope B")
        self.admin_a = make_user(
            "scope.admin.a@example.com", UserTypes.SCHOOL_ADMIN, school=self.school_a
        )
        self.teacher_a = make_user("scope.t.a@example.com", school=self.school_a)
        self.teacher_b = make_user("scope.t.b@example.com", school=self.school_b)

    def failed_login(self, user):
        self.client.force_authenticate(user=None)
        response = self.client.post(
            reverse("login"),
            {
                "email": user.email,
                "password": "not-the-password",  # pragma: allowlist secret
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def rows_for(self, admin, url_name, query=""):
        self.client.force_authenticate(user=admin)
        response = self.client.get(reverse(url_name) + query)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()["data"]
        return payload["results"] if isinstance(payload, dict) else payload

    def test_a_failure_is_anonymous_and_carries_the_targets_school(self):
        self.failed_login(self.teacher_a)

        event = AuditEvent.objects.get(action=AuditAction.AUTH_LOGIN)
        self.assertEqual(event.actor_role, "ANONYMOUS")
        self.assertIsNone(event.actor_id)
        self.assertEqual(event.target_id, self.teacher_a.id)
        self.assertEqual(event.school_id, self.school_a.id)

    def test_school_admin_sees_attempts_on_its_own_accounts(self):
        self.failed_login(self.teacher_a)

        rows = self.rows_for(self.admin_a, "school-admin-audit-events")

        self.assertEqual([r["target_id"] for r in rows], [str(self.teacher_a.id)])

    def test_school_admin_never_sees_attempts_on_another_schools_accounts(self):
        self.failed_login(self.teacher_b)

        rows = self.rows_for(self.admin_a, "school-admin-audit-events")

        self.assertEqual(rows, [])

    def test_superadmin_can_filter_anonymous_events(self):
        self.failed_login(self.teacher_a)
        self.failed_login(self.teacher_b)

        rows = self.rows_for(
            make_superadmin(), "super-admin-audit-events", "?actor_role=ANONYMOUS"
        )

        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["actor_role"] == "ANONYMOUS" for r in rows))
