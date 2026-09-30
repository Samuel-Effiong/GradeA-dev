"""The generic audit event for a state-changing request (Epic A completion S1).

The founder's standard is that every action any user can take is traceable.
Named events (login, grading, credit, CRUD, roster, upload, admin ...) cover
the actions FR-A-01 lists; this covers everything else. When an authenticated
request with a state-changing method finishes and no surviving event stored
for it names the requester as actor (`audit.context.RequestAuditState`),
`AuditMiddleware` writes exactly one STATE_CHANGE event naming the route,
method and outcome. Together that is FR-A-01's "exactly one well-formed
event" naming the requester for every write, with no per-view code,
including routes added later. Events naming other people (a teacher's credit
grant during an admin's write) are side effects, recorded in addition.

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
from .enums import AuditAction, AuditOutcome, ErrorClass, ReasonCode

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
    "course-renew-activation-token": (
        "Requests a new student invitation code by email; anonymous and "
        "rate-limited. Using the code is audited through register/student. "
        "Scheduled for removal by retire (B) (register/student and "
        "renew-student-token go after the prod backfill); the route "
        "coverage guard fails when the route is gone, so this entry goes "
        "with it."
    ),
    "stripe-webhook": (
        "Server-to-server from Stripe, signature-verified; no user acts. "
        "The billing effects it causes are recorded by named events "
        "(CREDIT_TRANSACTION); S3 gives them a system actor."
    ),
    "stripe-webhook-thin": (
        "Server-to-server from Stripe (thin events), signature-verified; no "
        "user acts. Its billing effects are recorded by named events; S3 "
        "gives them a system actor."
    ),
}

_TARGET_ID_KWARGS = ("pk", "id", "object_id")

# S2: the anonymous doors that sign in or create an account. Each records its
# own named event on success and on a refused attempt it recognises; when a
# request is refused before that (a malformed body, a missing field), no event
# exists, so `AuditMiddleware` records one FAILURE here instead: actor
# ANONYMOUS, no target, reason INVALID_REQUEST, never the body.
# Route (URL name) -> (action, metadata.auth_method).
ANONYMOUS_AUDITED_ROUTES = {
    "login": (AuditAction.AUTH_LOGIN, "password"),
    "auth-verify": (AuditAction.AUTH_LOGIN, "email_verification"),
    "auth-reset-password": (AuditAction.AUTH_LOGIN, "password_reset"),
    "auth-register-school-admin": (AuditAction.AUTH_LOGIN, "school_admin_invitation"),
    "auth-register-student": (AuditAction.AUTH_LOGIN, "student_invitation"),
    "auth-google-auth": (AuditAction.AUTH_LOGIN, "google"),
    "auth-register": (AuditAction.ACCOUNT_REGISTER, "self_registration"),
}
# Audit-only reason codes, from the FR-A-06 catalogue (S6a): the emitter
# refuses any code outside audit.enums.ReasonCode.
INVALID_REQUEST = ReasonCode.INVALID_REQUEST.value
SERVER_ERROR = ReasonCode.SERVER_ERROR.value


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


def emit_anonymous_refusal(request, response) -> None:
    """Record an anonymous write that left no event (S2). Called by the
    middleware only when no event stored during the request survives.

    - A sign-in door refused with a 4xx (a malformed body, a missing field)
      records one FAILURE, reason INVALID_REQUEST.
    - ANY non-excluded write that crashed (5xx) records one FAILURE,
      error_class SYSTEM, reason SERVER_ERROR (SM ruling on v2's N1): a
      crash must not leave zero trace. A door keeps its own action; any
      other route is a STATE_CHANGE naming the route.

    Actor ANONYMOUS, no body, never a 429 (the throttle refused it before the
    view ran). Never raises."""
    try:
        status_code = response.status_code
        if status_code < 400 or status_code == 429:
            return
        if request.method not in STATE_CHANGING_METHODS:
            return
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            return
        match = getattr(request, "resolver_match", None)
        view_name = getattr(match, "view_name", "") or ""
        if match is None or view_name in EXCLUDED_ROUTES:
            return
        door = ANONYMOUS_AUDITED_ROUTES.get(view_name)
        crashed = status_code >= 500
        if door is None and not crashed:
            return

        if crashed:
            outcome, error_class, reason = (
                AuditOutcome.FAILURE,
                ErrorClass.SYSTEM,
                SERVER_ERROR,
            )
        else:
            outcome, error_class = _outcome_for_status(status_code)
            reason = INVALID_REQUEST
        if door is not None:
            action, auth_method = door
            target_type, target_id = "CustomUser", None
            metadata = {"auth_method": auth_method, "http_status": status_code}
        else:
            action = AuditAction.STATE_CHANGE
            target_type, target_id = _target(match)
            metadata = {
                "route": view_name,
                "method": request.method,
                "http_status": status_code,
            }
        emit(
            action,
            actor=user,
            request=request,
            target_type=target_type,
            target_id=target_id,
            outcome=outcome,
            error_class=error_class,
            reason_code=reason,
            metadata=metadata,
        )
    except Exception as exc:  # noqa: BLE001 - FR-A-11: never fail the response
        logger.error(
            "audit anonymous refusal event failed: %s",
            type(exc).__name__,
            extra={"audit_kind": "generic_failed"},
        )


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
