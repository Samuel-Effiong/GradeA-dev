"""
audit/middleware.py
====================
Request-level audit coverage (Epic A; completion slice S1, plan 08 §2).

For every request, `AuditMiddleware`:

1. opens a fresh per-request audit state (`audit.context.request_audit_state`),
   which the emitter marks whenever it stores an event;
2. finishes the automatic ADMIN_ACTION coverage `classrooms.permissions.
   IsSuperAdmin` starts: `IsSuperAdmin.has_permission` tags a GRANTED request
   with the view instance (`admin_action.REQUEST_ATTR`) because the outcome
   isn't known until the view has run, and the event is written here (see
   `audit/admin_action.py`);
3. if no event stored during the request still exists AND names the
   requester as actor (none was stored, the transaction it was stored in
   rolled back, or it names someone else), writes the generic STATE_CHANGE
   event for a state-changing request by an authenticated user
   (`audit/request_audit.py`). So each such request ends with exactly one
   event naming the requester: its named one, or this.

Registered LAST in MIDDLEWARE (AutoGrader/settings.py): it reads
`request.user` after the view has run, and DRF's `Request` writes the
authenticated JWT user back onto the Django request only during the view.
RequestIDMiddleware, first in the list, keeps the correlation id live for
the whole cycle, so the trace id is still available here.
"""

from .admin_action import REQUEST_ATTR, emit_for_response
from .context import (
    a_stored_event_survives,
    a_surviving_event_names,
    request_audit_state,
)
from .request_audit import emit_anonymous_refusal, emit_generic_state_change


class AuditMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        with request_audit_state() as state:
            response = self.get_response(request)
            view = getattr(request, REQUEST_ATTR, None)
            if view is not None:
                emit_for_response(request, view, response)
            # Exactly one event naming the requester: their own named event
            # if one survived, else the generic one. Events naming others
            # (a teacher's credit grant) are side effects, not their trace.
            if not a_surviving_event_names(state, getattr(request, "user", None)):
                emit_generic_state_change(request, response)
            # S2: an anonymous write refused or crashed before any event.
            if not a_stored_event_survives(state):
                emit_anonymous_refusal(request, response)
        return response


# The name this middleware had when it only finished ADMIN_ACTION coverage.
# Kept so existing references (tests, older settings) keep working.
AdminActionAuditMiddleware = AuditMiddleware
