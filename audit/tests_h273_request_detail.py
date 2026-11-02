"""
audit/tests_h273_request_detail.py
===================================
H-273 step 1: request detail on audit rows (method, http_status, route,
reason_label, fields) and the reason_code passthrough from the exception
handler to the middleware-written row.

Mutant targets covered (NOTES §4):
  M-H273-1  route on ADMIN_ACTION            M-H273-11 http_status on ADMIN_ACTION
  M-H273-2  reason_label derivation           M-H273-12 request.path instead of route
  M-H273-3  fields extraction                 M-H273-13 exc text instead of class
  M-H273-4  exception stash in the handler    M-H273-14 reason_code passthrough
  M-H273-10 method on ADMIN_ACTION            M-H273-15 status fallback (tier c)
  M-H273-17 emit_denied label/status          M-H273-19 refusal reason (no .reason_code)
  M-H273-20 field-name sanitisation
plus M-H273-25/26 (SM addendum 2026-11-02): enveloped DRF refusals (the
named sign-in/verify locks) and the generic Throttled carry their
reason_code to the audit row.
"""

import django.core.exceptions
import django.http
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import resolve, reverse
from rest_framework import exceptions as drf_exc
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate
from rest_framework.views import APIView

from audit import history
from audit.middleware import AdminActionAuditMiddleware
from audit.models import AuditEvent
from audit.request_audit import emit_generic_state_change
from audit.request_detail import (
    REASON_LABELS,
    _exc_to_label,
    _status_to_label,
    enrich_denied_metadata,
    enrich_request_metadata,
)
from AutoGrader.reason_codes import CodedError
from billing.errors import InsufficientCreditsError
from classrooms.permissions import IsSuperAdmin
from users.exceptions import (
    EnvelopedAuthenticationFailed,
    EnvelopedThrottled,
    custom_exception_handler,
)

CustomUser = get_user_model()

HIDDEN_TEXT = "a-message-that-must-never-reach-the-row"


def _handle(exc):
    """Run the project's exception handler exactly as DRF does."""
    request = Request(APIRequestFactory().post("/h273-dummy"))
    return custom_exception_handler(exc, {"request": request, "view": APIView()})


def _response(status_code, **stash):
    response = Response(status=status_code)
    for key, value in stash.items():
        setattr(response, key, value)
    return response


class _FakeRequest:
    method = "PATCH"
    resolver_match = None


# --------------------------------------------------------------------------
# Label derivation (pure)
# --------------------------------------------------------------------------


class ExceptionLabelTests(SimpleTestCase):
    """Tier (b): the isinstance map, NOTES §1d."""

    CASES = [
        (drf_exc.NotFound(), "NOT_FOUND"),
        (django.http.Http404(), "NOT_FOUND"),
        (drf_exc.MethodNotAllowed("PUT"), "METHOD_NOT_ALLOWED"),
        (drf_exc.PermissionDenied(), "PERMISSION_DENIED"),
        (django.core.exceptions.PermissionDenied(), "PERMISSION_DENIED"),
        (drf_exc.NotAuthenticated(), "NOT_AUTHENTICATED"),
        (drf_exc.AuthenticationFailed(), "AUTHENTICATION_FAILED"),
        (drf_exc.ValidationError({"a": ["x"]}), "VALIDATION_FAILED"),
        (django.core.exceptions.ValidationError("x"), "VALIDATION_FAILED"),
        (drf_exc.ParseError(), "PARSE_ERROR"),
        (drf_exc.UnsupportedMediaType("x/y"), "UNSUPPORTED_MEDIA_TYPE"),
        (drf_exc.NotAcceptable(), "NOT_ACCEPTABLE"),
    ]

    def test_each_class_maps_to_its_label(self):
        for exc, label in self.CASES:
            with self.subTest(exc=type(exc).__name__):
                self.assertEqual(_exc_to_label(exc), label)

    def test_unknown_class_falls_through(self):
        self.assertIsNone(_exc_to_label(RuntimeError(HIDDEN_TEXT)))

    def test_every_class_label_is_in_the_closed_set(self):
        for exc, _ in self.CASES:
            self.assertIn(_exc_to_label(exc), REASON_LABELS)


class StatusLabelTests(SimpleTestCase):
    """Tier (c): the closed status map, NOTES §1d."""

    CASES = {
        400: "BAD_REQUEST",
        401: "NOT_AUTHENTICATED",
        403: "PERMISSION_DENIED",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        406: "NOT_ACCEPTABLE",
        409: "CONFLICT",
        413: "TOO_LARGE",
        415: "UNSUPPORTED_MEDIA_TYPE",
        418: "CLIENT_ERROR",
        429: "CLIENT_ERROR",
        500: "SERVER_ERROR",
        503: "SERVER_ERROR",
    }

    def test_each_status_maps_to_its_label(self):
        for code, label in self.CASES.items():
            with self.subTest(status=code):
                self.assertEqual(_status_to_label(code), label)
                self.assertIn(label, REASON_LABELS)

    def test_success_has_no_label(self):
        self.assertIsNone(_status_to_label(200))
        self.assertIsNone(_status_to_label(302))


class EnrichTests(SimpleTestCase):
    def test_success_gets_method_and_status_but_no_label(self):
        metadata = {}
        enrich_request_metadata(_FakeRequest(), _response(200), metadata)
        self.assertEqual(metadata, {"method": "PATCH", "http_status": 200})

    def test_existing_keys_are_not_overwritten(self):
        metadata = {"route": "session-list", "method": "POST", "http_status": 201}
        enrich_request_metadata(_FakeRequest(), _response(400), metadata)
        self.assertEqual(metadata["route"], "session-list")
        self.assertEqual(metadata["method"], "POST")
        self.assertEqual(metadata["http_status"], 201)

    def test_tier_a_stashed_reason_code_means_no_label(self):
        metadata = {}
        response = _response(
            402,
            _audit_reason_code="INSUFFICIENT_CREDITS",
            _audit_exc=drf_exc.ValidationError("x"),
        )
        enrich_request_metadata(_FakeRequest(), response, metadata)
        self.assertNotIn("reason_label", metadata)

    def test_tier_b_exception_beats_status(self):
        # 401 alone would read NOT_AUTHENTICATED; the exception is more exact.
        metadata = {}
        response = _response(401, _audit_exc=drf_exc.AuthenticationFailed())
        enrich_request_metadata(_FakeRequest(), response, metadata)
        self.assertEqual(metadata["reason_label"], "AUTHENTICATION_FAILED")

    def test_tier_c_unknown_exception_falls_back_to_status(self):
        metadata = {}
        response = _response(409, _audit_exc=RuntimeError(HIDDEN_TEXT))
        enrich_request_metadata(_FakeRequest(), response, metadata)
        self.assertEqual(metadata["reason_label"], "CONFLICT")
        self.assertNotIn(HIDDEN_TEXT, repr(metadata))
        self.assertNotIn("RuntimeError", repr(metadata))

    def test_tier_c_without_any_exception(self):
        metadata = {}
        enrich_request_metadata(_FakeRequest(), _response(404), metadata)
        self.assertEqual(metadata["reason_label"], "NOT_FOUND")

    def test_fields_and_truncation_flag_are_copied(self):
        metadata = {}
        response = _response(
            400, _audit_fields=["a", "b"], _audit_fields_truncated=True
        )
        enrich_request_metadata(_FakeRequest(), response, metadata)
        self.assertEqual(metadata["fields"], ["a", "b"])
        self.assertIs(metadata["fields_truncated"], True)

    def test_denied_enrichment(self):
        metadata = {"source": "X"}
        enrich_denied_metadata(_FakeRequest(), metadata)
        self.assertEqual(
            metadata,
            {
                "source": "X",
                "method": "PATCH",
                "http_status": 403,
                "reason_label": "PERMISSION_DENIED",
            },
        )


# --------------------------------------------------------------------------
# The exception handler's stash
# --------------------------------------------------------------------------


class HandlerStashTests(SimpleTestCase):
    def test_validation_dict_stashes_sorted_safe_field_names(self):
        response = _handle(
            drf_exc.ValidationError(
                {"title": [HIDDEN_TEXT], "due_date": ["x"], "pupil@example.com": ["x"]}
            )
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response._audit_fields, ["due_date", "title"])
        self.assertFalse(getattr(response, "_audit_fields_truncated", False))
        self.assertIsInstance(response._audit_exc, drf_exc.ValidationError)

    def test_validation_list_is_non_field_errors(self):
        response = _handle(drf_exc.ValidationError([HIDDEN_TEXT]))
        self.assertEqual(response._audit_fields, ["non_field_errors"])

    def test_more_than_twenty_fields_is_capped_and_flagged(self):
        detail = {f"field_{i:02d}": ["x"] for i in range(25)}
        response = _handle(drf_exc.ValidationError(detail))
        self.assertEqual(len(response._audit_fields), 20)
        self.assertEqual(response._audit_fields[0], "field_00")
        self.assertIs(response._audit_fields_truncated, True)

    def test_coded_error_stashes_its_reason_code(self):
        response = _handle(CodedError("RUBRIC_MISSING"))
        self.assertEqual(response._audit_reason_code, "RUBRIC_MISSING")

    def test_legacy_refusal_without_reason_code_attribute_is_stashed(self):
        # InsufficientCreditsError has no .reason_code: reason_of derives it.
        response = _handle(InsufficientCreditsError())
        self.assertEqual(response._audit_reason_code, "INSUFFICIENT_CREDITS")

    def test_enveloped_throttle_stashes_its_reason_code(self):
        response = _handle(
            EnvelopedThrottled(
                wait=5, reason_code="VERIFY_LOCKED", code_value="VERIFY_LOCKED"
            )
        )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.data["reason_code"], "VERIFY_LOCKED")
        self.assertEqual(response._audit_reason_code, "VERIFY_LOCKED")

    def test_enveloped_auth_failure_stashes_its_reason_code(self):
        response = _handle(
            EnvelopedAuthenticationFailed(
                "locked", "account_locked", reason_code="ACCOUNT_LOCKED"
            )
        )
        self.assertEqual(response._audit_reason_code, "ACCOUNT_LOCKED")

    def test_plain_throttle_stashes_rate_limited(self):
        response = _handle(drf_exc.Throttled(wait=3))
        self.assertEqual(response.data["reason_code"], "RATE_LIMITED")
        self.assertEqual(response._audit_reason_code, "RATE_LIMITED")

    def test_unhandled_crash_stashes_the_exception(self):
        response = _handle(RuntimeError(HIDDEN_TEXT))
        self.assertEqual(response.status_code, 500)
        self.assertIsInstance(response._audit_exc, RuntimeError)
        self.assertFalse(hasattr(response, "_audit_reason_code"))


# --------------------------------------------------------------------------
# End to end: rows written by the middleware
# --------------------------------------------------------------------------


class _RaisingAdminView(APIView):
    permission_classes = [IsSuperAdmin]
    to_raise: BaseException = RuntimeError("unset")

    def get(self, request):
        raise self.to_raise


def _admin_view(exc):
    return type("H273AdminView", (_RaisingAdminView,), {"to_raise": exc})


def _run(view_class, user):
    request = APIRequestFactory().get("/h273-admin")
    force_authenticate(request, user=user)
    return AdminActionAuditMiddleware(view_class.as_view())(request)


class _Users(TestCase):
    def setUp(self):
        with history.suppressed():
            self.superadmin = CustomUser.objects.create_user(
                email="root.h273.detail@example.com",
                password="testpass123",  # pragma: allowlist secret
                user_type="SUPER_ADMIN",
                is_active=True,
                is_staff=True,
                is_superuser=True,
            )
            self.teacher = CustomUser.objects.create_user(
                email="teacher.h273.detail@example.com",
                password="testpass123",  # pragma: allowlist secret
                user_type="TEACHER",
                is_active=True,
            )


class AdminActionRowTests(_Users):
    def test_validation_failure_row(self):
        view = _admin_view(
            drf_exc.ValidationError({"name": [HIDDEN_TEXT], "x@example.com": ["x"]})
        )
        response = _run(view, self.superadmin)
        self.assertEqual(response.status_code, 400)
        event = AuditEvent.objects.get()
        self.assertEqual(event.outcome, "FAILURE")
        self.assertIsNone(event.reason_code)
        self.assertEqual(event.metadata["method"], "GET")
        self.assertEqual(event.metadata["http_status"], 400)
        self.assertEqual(event.metadata["reason_label"], "VALIDATION_FAILED")
        self.assertEqual(event.metadata["fields"], ["name"])
        self.assertNotIn(HIDDEN_TEXT, repr(event.metadata))
        self.assertNotIn("example.com", repr(event.metadata))

    def test_coded_failure_row_carries_reason_code_not_label(self):
        response = _run(_admin_view(CodedError("RUBRIC_MISSING")), self.superadmin)
        self.assertGreaterEqual(response.status_code, 400)
        event = AuditEvent.objects.get()
        self.assertEqual(event.reason_code, "RUBRIC_MISSING")
        self.assertNotIn("reason_label", event.metadata)

    def test_throttled_row_carries_rate_limited(self):
        response = _run(_admin_view(drf_exc.Throttled(wait=2)), self.superadmin)
        self.assertEqual(response.status_code, 429)
        event = AuditEvent.objects.get()
        self.assertEqual(event.reason_code, "RATE_LIMITED")
        self.assertNotIn("reason_label", event.metadata)

    def test_not_found_row(self):
        _run(_admin_view(drf_exc.NotFound(HIDDEN_TEXT)), self.superadmin)
        event = AuditEvent.objects.get()
        self.assertEqual(event.metadata["reason_label"], "NOT_FOUND")
        self.assertEqual(event.metadata["http_status"], 404)
        self.assertNotIn(HIDDEN_TEXT, repr(event.metadata))

    def test_denied_row_is_exactly_one_with_label(self):
        response = _run(_admin_view(drf_exc.NotFound()), self.teacher)
        self.assertEqual(response.status_code, 403)
        event = AuditEvent.objects.get()
        self.assertEqual(event.outcome, "DENIED")
        self.assertEqual(event.metadata["reason_label"], "PERMISSION_DENIED")
        self.assertEqual(event.metadata["http_status"], 403)
        self.assertEqual(event.metadata["method"], "GET")

    def test_real_endpoint_row_records_the_url_pattern_not_the_path(self):
        client = APIClient()
        client.force_authenticate(self.superadmin)
        url = reverse("super-admin-audit-events")
        response = client.get(url, {"trace_id": "not-a-uuid"})
        self.assertEqual(response.status_code, 400)
        event = AuditEvent.objects.filter(action="ADMIN_ACTION").latest("occurred_at")
        self.assertEqual(event.metadata["route"], resolve(url).route)
        self.assertNotEqual(event.metadata["route"], url)
        self.assertEqual(event.metadata["reason_label"], "VALIDATION_FAILED")
        self.assertEqual(event.metadata["fields"], ["trace_id"])


class StateChangeRowTests(_Users):
    def _request(self):
        url = reverse("session-list")
        request = APIRequestFactory().post(url)
        request.user = self.teacher
        request.resolver_match = resolve(url)
        return request

    def test_failure_row_gets_label_and_fields_and_keeps_view_name_route(self):
        response = _response(
            400,
            _audit_exc=drf_exc.ValidationError({"name": ["x"]}),
            _audit_fields=["name"],
        )
        emit_generic_state_change(self._request(), response)
        event = AuditEvent.objects.get(action="STATE_CHANGE")
        self.assertEqual(event.metadata["route"], "session-list")
        self.assertEqual(event.metadata["reason_label"], "VALIDATION_FAILED")
        self.assertEqual(event.metadata["fields"], ["name"])

    def test_coded_failure_row_carries_reason_code(self):
        response = _response(402, _audit_reason_code="INSUFFICIENT_CREDITS")
        emit_generic_state_change(self._request(), response)
        event = AuditEvent.objects.get(action="STATE_CHANGE")
        self.assertEqual(event.reason_code, "INSUFFICIENT_CREDITS")
        self.assertNotIn("reason_label", event.metadata)
