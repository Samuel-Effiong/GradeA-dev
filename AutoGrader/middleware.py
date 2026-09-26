"""
Correlation-id middleware.

See AutoGrader.request_context for why this exists and how the id
propagates beyond this one request (logs, Sentry, Celery).
"""

from __future__ import annotations

import logging

from .request_context import (
    REQUEST_ID_HEADER,
    generate_request_id,
    is_valid_request_id,
    reset_request_id,
    set_request_id,
)

logger = logging.getLogger(__name__)


class RequestIDMiddleware:
    """Assigns every request a correlation id and threads it through.

    Placed first in MIDDLEWARE (see settings.py) so the id is set before
    any other middleware, view, or signal handler runs, and is only torn
    down after all of them have returned - every log line and Sentry event
    produced anywhere while handling this request can pick it up.

    An inbound `X-Request-ID` header is trusted and reused if present and
    well-formed (e.g. set by a proxy/CDN in front of Railway, or by the
    frontend when retrying a request so a retried attempt shares the
    original id); otherwise one is generated. Either way the id is echoed
    back on the response header, so a client can surface it to a user for a
    support ticket.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        inbound = request.headers.get(REQUEST_ID_HEADER)
        request_id = inbound if is_valid_request_id(inbound) else generate_request_id()

        request.request_id = request_id
        token = set_request_id(request_id)

        # Guarded the same way settings.py guards Sentry init: sentry_sdk
        # may not be installed in every deploy, and even when installed the
        # SDK may not have been initialized (no SENTRY_DSN configured) - in
        # both cases tagging should be a no-op, not a startup/request error.
        try:
            import sentry_sdk

            sentry_sdk.set_tag("request_id", request_id)
        except ImportError:
            pass

        try:
            response = self.get_response(request)
        finally:
            # Reset before touching the response so the header write below
            # can never run twice (middleware __call__ runs once), and so
            # the contextvar is torn down even if get_response raised.
            reset_request_id(token)

        response[REQUEST_ID_HEADER] = request_id
        return response
