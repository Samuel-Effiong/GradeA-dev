import logging
import traceback
from collections.abc import Mapping

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


SUCCESS_MESSAGE_KEYS = ("message", "detail")


def get_response_message(data, default: str) -> str:
    if not isinstance(data, Mapping):
        return default

    for key in SUCCESS_MESSAGE_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value

    return default


def is_error_response(response) -> bool:
    return bool(
        getattr(response, "exception", False)
        or getattr(response, "status_code", 200) >= 400
    )


def flatten_errors(data) -> str:
    """
    Recursively collects all error messages from a DRF error payload and
    formats them into a single string.

    - Single error  → plain string,  e.g. "This field is required."
    - Multiple errors → numbered list string, e.g.
        "1. Email: This field is required. 2. Password: This field may not be blank."

    Handles:
      • {"detail": "Not found."}
      • {"email": ["msg1"], "password": ["msg2"]}
      • {"non_field_errors": ["msg"]}
      • Nested dicts (e.g. nested serializer errors)
    """
    messages = []

    def _collect(obj, prefix=""):
        if isinstance(obj, str):
            messages.append(f"{prefix}{obj}" if prefix else obj)

        elif isinstance(obj, list):
            for item in obj:
                _collect(item, prefix)

        elif isinstance(obj, Mapping):
            # DRF/manual top-level error keys (auth, 404, throttled, etc.)
            top_level_message_keys = ("detail", "error", "message")
            matching_keys = [key for key in top_level_message_keys if key in obj]
            if len(obj) == 1 and matching_keys:
                _collect(obj[matching_keys[0]])
                return

            for field, value in obj.items():
                if field == "non_field_errors":
                    _collect(value)
                else:
                    # Turn snake_case / CamelCase field names into readable labels
                    label = str(field).replace("_", " ").capitalize()
                    _collect(value, prefix=f"{label}: ")

    _collect(data)

    if not messages:
        return "An error occurred"

    if len(messages) == 1:
        return messages[0]

    # Multiple errors → numbered list embedded in a single string
    numbered = " ".join(f"{i}. {msg}\n" for i, msg in enumerate(messages, start=1))
    return numbered


class APIJSONRenderer(JSONRenderer):
    def render(self, data, accepted_media_type=None, renderer_context=None):
        response = renderer_context.get("response") if renderer_context else None

        if response is None:
            return super().render(data, accepted_media_type, renderer_context)

        # ---------- SUCCESS ----------
        if not is_error_response(response):
            message = getattr(response, "message", None) or get_response_message(
                data, "Request Successful"
            )
            return super().render(
                api_response(
                    success=True,
                    message=message,
                    data=data,
                ),
                accepted_media_type,
                renderer_context,
            )

        # ---------- ERROR ----------
        # DRF already gave us a response (validation, 404, etc.)

        message = "An error occurred"
        error_dict: Dict[str, Any] = {}
        if hasattr(response, "_drf_handled") or data is not None:
            # Build the human-readable message from the DRF error payload
            message = flatten_errors(data) if data else "Validation failed"
            error_dict = {"field_errors": data} if isinstance(data, Mapping) else {}

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
