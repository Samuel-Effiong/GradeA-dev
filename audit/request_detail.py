"""H-273: enrich audit metadata with request detail for every 4xx/5xx row.

Every request-scoped audit row (ADMIN_ACTION, STATE_CHANGE, AUTH_LOGIN,
ACCOUNT_REGISTER) gets method, http_status and route. Failure rows
additionally get a reason_label from a three-tier fallback:

    (a) the row already carries a reason_code → no label needed;
    (b) the exception stashed by custom_exception_handler → isinstance map;
    (c) the HTTP status code → closed status map.

The raw exception class name is never stored. The label vocabulary is a
closed set (REASON_LABELS). Validation errors additionally get the sorted
list of failing field names (never their messages).
"""

import django.core.exceptions
import django.http
from rest_framework import exceptions as drf_exc

REASON_LABELS = frozenset(
    {
        "BAD_REQUEST",
        "NOT_AUTHENTICATED",
        "AUTHENTICATION_FAILED",
        "PERMISSION_DENIED",
        "NOT_FOUND",
        "METHOD_NOT_ALLOWED",
        "NOT_ACCEPTABLE",
        "CONFLICT",
        "TOO_LARGE",
        "UNSUPPORTED_MEDIA_TYPE",
        "PARSE_ERROR",
        "VALIDATION_FAILED",
        "CLIENT_ERROR",
        "SERVER_ERROR",
    }
)

_STATUS_TO_LABEL = {
    400: "BAD_REQUEST",
    401: "NOT_AUTHENTICATED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    406: "NOT_ACCEPTABLE",
    409: "CONFLICT",
    413: "TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
}


def _exc_to_label(exc):
    if isinstance(exc, (drf_exc.NotFound, django.http.Http404)):
        return "NOT_FOUND"
    if isinstance(exc, drf_exc.MethodNotAllowed):
        return "METHOD_NOT_ALLOWED"
    if isinstance(
        exc, (drf_exc.PermissionDenied, django.core.exceptions.PermissionDenied)
    ):
        return "PERMISSION_DENIED"
    if isinstance(exc, drf_exc.NotAuthenticated):
        return "NOT_AUTHENTICATED"
    if isinstance(exc, drf_exc.AuthenticationFailed):
        return "AUTHENTICATION_FAILED"
    if isinstance(
        exc, (drf_exc.ValidationError, django.core.exceptions.ValidationError)
    ):
        return "VALIDATION_FAILED"
    if isinstance(exc, drf_exc.ParseError):
        return "PARSE_ERROR"
    if isinstance(exc, drf_exc.UnsupportedMediaType):
        return "UNSUPPORTED_MEDIA_TYPE"
    if isinstance(exc, drf_exc.NotAcceptable):
        return "NOT_ACCEPTABLE"
    return None


def _status_to_label(status_code):
    label = _STATUS_TO_LABEL.get(status_code)
    if label:
        return label
    if 400 <= status_code < 500:
        return "CLIENT_ERROR"
    if status_code >= 500:
        return "SERVER_ERROR"
    return None


def _resolve_label(response, has_reason_code):
    if has_reason_code:
        return None
    exc = getattr(response, "_audit_exc", None)
    if exc is not None:
        label = _exc_to_label(exc)
        if label:
            return label
    return _status_to_label(response.status_code)


def enrich_request_metadata(request, response, metadata):
    """Add method, http_status, route and (on failure) reason_label + fields.

    Keys already present in ``metadata`` are not overwritten: the caller
    may have set method/http_status/route from its own context before this
    runs (STATE_CHANGE sets route = view_name, for example).
    """
    metadata.setdefault("method", request.method)
    metadata.setdefault("http_status", response.status_code)
    match = getattr(request, "resolver_match", None)
    if match is not None:
        route = getattr(match, "route", None)
        if route is not None:
            metadata.setdefault("route", route)
    has_reason_code = bool(stashed_reason_code(response))
    label = _resolve_label(response, has_reason_code)
    if label:
        metadata["reason_label"] = label
    fields = getattr(response, "_audit_fields", None)
    if fields:
        metadata["fields"] = fields


def enrich_denied_metadata(request, metadata):
    """Enrichment for emit_denied: no response exists yet, but we know 403."""
    metadata["method"] = request.method
    metadata["http_status"] = 403
    match = getattr(request, "resolver_match", None)
    if match is not None:
        route = getattr(match, "route", None)
        if route is not None:
            metadata["route"] = route
    metadata["reason_label"] = "PERMISSION_DENIED"


def stashed_reason_code(response):
    return getattr(response, "_audit_reason_code", None)
