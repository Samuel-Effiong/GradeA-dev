"""The generic audit event for a state-changing request (Epic A completion S1).

The founder's standard is that every action any user can take is traceable.
Named events (login, grading, credit, CRUD, roster, upload, admin ...) cover
the actions FR-A-01 lists; this covers everything else. When an authenticated
request with a state-changing method finishes and NO event was stored for it
(`audit.context.RequestAuditState`), `AuditMiddleware` writes exactly one
STATE_CHANGE event naming the route, method and outcome. Together that is
FR-A-01's "exactly one well-formed event" for every write, with no per-view
code, including routes added later.

Never recorded: the request body, the query string or the path itself. The
route's URL name and the method are enough to say what was done, and the
target id comes from the URL kwargs only when it is a UUID.

Not recorded at all:
* anonymous requests - there is no actor, and the anonymous routes that
  matter (sign-in, registration, code checks) have named auth events;
* safe methods (GET/HEAD/OPTIONS) - superadmin reads stay audited as
  ADMIN_ACTION (plan 08 D2);
* a 429: the request was refused by a throttle before the view ran, so it
  changed nothing, and recording it would let a throttled client write an
  unbounded number of audit rows;
* the routes in `EXCLUDED_ROUTES`, each with its reason.
"""

import logging
import uuid

from .admin_action import _outcome_for_status
from .emitter import emit
from .enums import AuditAction

logger = logging.getLogger(__name__)

STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Route (URL name, as `resolver_match.view_name` gives it) -> why it is not
# audited. Reviewed like code; `audit/tests_route_coverage.py` (S2) fails if an
# entry stops resolving or lacks a reason. Keep it short: every entry is a
# deliberate hole in "every action is traceable".
EXCLUDED_ROUTES = {
    "refresh": (
        "Token rotation, not a user action. High volume; session revocation "
        "is audited through AUTH_LOGOUT and credential changes."
    ),
    "auth-otp": (
        "Requests a verification or reset code; anonymous and rate-limited. "
        "Using the code is audited (AUTH_LOGIN on verify / reset-password)."
    ),
    "auth-request-change-password": (
        "Requests a change-password code by email; changes no state a user "
        "can see. Using it is audited through change-password."
    ),
}

_TARGET_ID_KWARGS = ("pk", "id", "object_id")


def _target(match):
    """(target_type, target_id) from the resolved route, never the body."""
    func = match.func
    cls = getattr(func, "cls", None) or getattr(func, "view_class", None)
    name = cls.__name__ if cls is not None else getattr(func, "__name__", "")
    target_type = name if name and name[0].isalpha() else "View"
    target_id = None
    for key in _TARGET_ID_KWARGS:
        value = match.kwargs.get(key)
        if value is None:
            continue
        try:
            target_id = uuid.UUID(str(value))
        except ValueError:
            pass
        break
    return target_type[:64], target_id


def should_record(request, response) -> bool:
    if request.method not in STATE_CHANGING_METHODS:
        return False
    if response.status_code == 429:
        return False
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return False
    match = getattr(request, "resolver_match", None)
    if match is None:
        return False
    return match.view_name not in EXCLUDED_ROUTES


def emit_generic_state_change(request, response) -> None:
    """Write the STATE_CHANGE event for `request`, if it needs one. Never
    raises: this runs after the user's response already exists."""
    try:
        if not should_record(request, response):
            return
        match = request.resolver_match
        target_type, target_id = _target(match)
        outcome, error_class = _outcome_for_status(response.status_code)
        user = request.user
        emit(
            AuditAction.STATE_CHANGE,
            actor=user,
            request=request,
            target_type=target_type,
            target_id=target_id,
            outcome=outcome,
            error_class=error_class,
            metadata={
                "route": match.view_name,
                "method": request.method,
                "http_status": response.status_code,
            },
            # A student's own actions are part of their record (A6, 3 years).
            touches_student_record=getattr(user, "user_type", None) == "STUDENT",
        )
    except Exception as exc:  # noqa: BLE001 - FR-A-11: never fail the response
        logger.error(
            "audit generic state-change event failed: %s",
            type(exc).__name__,
            extra={"audit_kind": "generic_failed"},
        )
