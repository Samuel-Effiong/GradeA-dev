"""
Shared plumbing for automatic ADMIN_ACTION audit coverage (§6's "Admin
action" category: ~18 IsSuperAdmin-gated endpoints scattered across
billing/, dashboard/ and users/, with no shared base class - and, per the
plan, more to come with no guarantee any of them remember to instrument
themselves).

The coverage is hooked into `classrooms.permissions.IsSuperAdmin` itself,
not a mixin added to each view: that permission class is the one thing
every one of these endpoints already declares, so wiring it there is the
only way a NEW endpoint gets audited automatically, with zero
endpoint-specific code, the moment it lists `permission_classes =
[IsSuperAdmin]`.

`has_permission` runs before the view body, so a DENIAL is a complete
outcome right there and `IsSuperAdmin` emits it directly (`emit_denied`).
A GRANTED call only tags the request (`REQUEST_ATTR`) - whether the view
then succeeds or fails isn't known until the response exists, which is
`audit.middleware.AdminActionAuditMiddleware`'s job (`emit_for_response`).
"""

from .emitter import emit
from .enums import AuditAction, AuditOutcome, ErrorClass

REQUEST_ATTR = "audit_admin_action_view"


def raw_request(request):
    """The underlying Django HttpRequest.

    `has_permission(self, request, view)` receives DRF's `Request` wrapper,
    but `AdminActionAuditMiddleware` runs at the plain Django middleware
    level and only ever sees the raw `HttpRequest` it wraps. DRF's Request
    doesn't override `__setattr__` to proxy writes through to it, so an
    attribute set on the wrapper is invisible to the middleware unless it
    is set on this underlying object instead.
    """
    return getattr(request, "_request", request)


def resolve_target(view, request):
    """(target_type, target_id) for `view`.

    A view may define `get_audit_target(self, request)` returning that
    pair explicitly - the way to give a specific target (e.g. the id of
    the object a detail endpoint acts on) instead of the generic fallback.
    Without one, this is still well-formed per FR-A-01: the view's own
    class name is always available, and a URL `pk`/`id` kwarg is used as
    the target id when the route has one.
    """
    get_target = getattr(view, "get_audit_target", None)
    if callable(get_target):
        return get_target(request)
    kwargs = getattr(view, "kwargs", None) or {}
    target_id = kwargs.get("pk") or kwargs.get("id")
    return view.__class__.__name__, target_id


def _outcome_for_status(status_code):
    """(AuditOutcome, ErrorClass) for a response's HTTP status, for the
    generic case where a view has no more specific outcome of its own."""
    if 200 <= status_code < 400:
        return AuditOutcome.SUCCESS, None
    if status_code in (401, 403):
        return AuditOutcome.DENIED, ErrorClass.USER
    if 400 <= status_code < 500:
        return AuditOutcome.FAILURE, ErrorClass.USER
    return AuditOutcome.FAILURE, ErrorClass.SYSTEM


def emit_denied(request, view):
    target_type, target_id = resolve_target(view, request)
    emit(
        AuditAction.ADMIN_ACTION,
        actor=request.user,
        request=request,
        target_type=target_type,
        target_id=target_id,
        outcome=AuditOutcome.DENIED,
        error_class=ErrorClass.USER,
        metadata={"source": view.__class__.__name__},
    )


def emit_for_response(request, view, response):
    target_type, target_id = resolve_target(view, request)
    outcome, error_class = _outcome_for_status(response.status_code)
    emit(
        AuditAction.ADMIN_ACTION,
        actor=request.user,
        request=request,
        target_type=target_type,
        target_id=target_id,
        outcome=outcome,
        error_class=error_class,
        metadata={"source": view.__class__.__name__},
    )
