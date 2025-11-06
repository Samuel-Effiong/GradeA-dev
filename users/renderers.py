import logging
import traceback

# utils/response.py
from typing import Any, Dict, Optional

from django.conf import settings
from rest_framework.renderers import JSONRenderer


def api_response(
    *,
    success: bool,
    message: str = "",
    data: Optional[Dict[str, Any]] = None,
    error: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Single source of truth for the API payload."""
    payload = {
        "success": success,
        "message": message,
    }
    if success:
        payload["data"] = data or {}
    else:
        payload["error"] = error or {}
    return payload


logger = logging.getLogger(__name__)


class APIJSONRenderer(JSONRenderer):
    def render(self, data, accepted_media_type=None, renderer_context=None):
        response = renderer_context["response"]

        # ---------- SUCCESS ----------
        if not getattr(response, "exception", False):
            return super().render(
                api_response(
                    success=True,
                    message=getattr(response, "message", "Request Successful"),
                    data=data,
                ),
                accepted_media_type,
                renderer_context,
            )

        # ---------- ERROR ----------
        # DRF already gave us a response (validation, 404, etc.)

        message = "An error occurred"
        error_dict: Dict[str, Any] = {}
        if hasattr(response, "_drf_handled"):
            error_dict = {"field_errors": data} if isinstance(data, dict) else {}

            message = (
                data.get("detail", "Validation failed")
                if isinstance(data, dict) and "detail" in data
                else "Validation Failed"
            )

        else:
            # Unhandled 500
            exc = getattr(response, "_raw_exc", None)
            message = str(exc) or "Internal server error"
            if exc and settings.DEBUG:
                tb = "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                )
                error_dict["traceback"] = tb

        payload = api_response(
            success=False,
            message=message,
            error=error_dict,
        )
        return super().render(payload, accepted_media_type, renderer_context)
