import logging
import uuid
from datetime import datetime

import pytz
from rest_framework import status as drf_status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    # Let DRF generate the base response if it knows how
    drf_resp = drf_exception_handler(exc, context)

    # Unique ID for tracing logs
    error_id = uuid.uuid4().hex[:8]
    request = context.get("request")
    title = exc.__class__.__name__

    if drf_resp is not None and isinstance(drf_resp.data, dict):
        # Always use DRF’s integer status code
        http_status = drf_resp.status_code
        # Extract the “detail” (or fall back to the whole data payload)
        detail = drf_resp.data.get("detail", drf_resp.data)
        # If the exception has a default_code, include it as a separate field
        error_code = getattr(exc, "default_code", None)
    else:
        # Unhandled exceptions become HTTP 500
        http_status = drf_status.HTTP_500_INTERNAL_SERVER_ERROR
        view = context.get("view")
        view_name = view.__class__.__name__ if view else "UnknownView"
        action = getattr(view, "action", None)
        detail = f"[{error_id}] Unhandled exception in {view_name} - {exc}" + (
            f" (action: {action})" if action else ""
        )
        error_code = "internal_error"
        logger.exception(f"[{error_id}] {detail}", exc_info=True)

    # Build the problem+json payload
    if request:
        base_url = f"{request.scheme}://{request.get_host()}"
        error_type = f"{base_url}/errors/{title.lower()}"
    else:
        error_type = f"/errors/{title.lower()}"

    payload = {
        "type": error_type,
        "title": title,
        "status": http_status,  # <— integer
        "detail": detail,
        "instance": request.get_full_path() if request else None,
        "timestamp": datetime.now(pytz.UTC).isoformat() + "Z",
        "error_id": error_id,
    }

    # Include your custom string code if present
    if error_code:
        payload["code"] = error_code

    # Return with the proper HTTP status and any DRF headers (e.g. for auth)
    headers = getattr(drf_resp, "headers", None)
    return Response(payload, status=http_status, headers=headers)
