"""
audit/middleware.py
====================
Finishes the automatic ADMIN_ACTION coverage `classrooms.permissions.
IsSuperAdmin` starts (see `audit/admin_action.py`'s module docstring for
the full design rationale).

`IsSuperAdmin.has_permission` tags a GRANTED request with the view
instance (`admin_action.REQUEST_ATTR`) because the actual outcome -
success or failure - isn't known until the view has run. This middleware
runs after the full response is ready and does that emission. It is a
plain function-of-the-response check (`getattr(request, REQUEST_ATTR,
None)`), so it costs nothing on the overwhelming majority of requests
that never touch a superadmin endpoint.

Must be registered in MIDDLEWARE (see AutoGrader/settings.py) - anywhere
after RequestIDMiddleware works, since RequestIDMiddleware wraps the
entire request/response cycle and its correlation id is still live in
context when this middleware's post-response code runs, regardless of
where in the list this one sits.
"""

from .admin_action import REQUEST_ATTR, emit_for_response


class AdminActionAuditMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        view = getattr(request, REQUEST_ATTR, None)
        if view is not None:
            emit_for_response(request, view, response)
        return response
