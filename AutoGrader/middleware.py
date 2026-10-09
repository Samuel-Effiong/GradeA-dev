"""
Correlation-id middleware.

See AutoGrader.request_context for why this exists and how the id
propagates beyond this one request (logs, Sentry, Celery).
"""

from __future__ import annotations

import logging

from .request_context import (
    REQUEST_ID_HEADER,
    client_request_id_from_header,
    generate_request_id,
    reset_client_request_id,
    reset_request_id,
    set_client_request_id,
    set_request_id,
)

logger = logging.getLogger(__name__)


class RequestIDMiddleware:
    """Assigns every request a correlation id and threads it through.

    Placed first in MIDDLEWARE (see settings.py) so the id is set before
    any other middleware, view, or signal handler runs, and is only torn
    down after all of them have returned - every log line and Sentry event
    produced anywhere while handling this request can pick it up.

    The id is ALWAYS generated here (X-5): it is also the audit trace id, so
    a client must not be able to choose it and land its actions in another
    action's trail. An inbound `X-Request-ID` that is a UUID (e.g. the
    frontend's own id, shared by its retries) is kept only as
    `client_request_id`, beside the server's id in every log line and on the
    audit event, so frontend and backend logs can still be joined. The
    server's id is echoed back on the response header, so a client can
    surface it to a user for a support ticket.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = generate_request_id()
        client_request_id = client_request_id_from_header(
            request.headers.get(REQUEST_ID_HEADER)
        )

        request.request_id = request_id
        request.client_request_id = client_request_id
        token = set_request_id(request_id)
        client_token = set_client_request_id(client_request_id)

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
            reset_client_request_id(client_token)

        response[REQUEST_ID_HEADER] = request_id
        return response
