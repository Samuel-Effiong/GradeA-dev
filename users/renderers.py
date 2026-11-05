import logging
import re
import traceback
from collections.abc import Mapping

# utils/response.py
from typing import Any, Dict, Optional

from django.conf import settings
from rest_framework.renderers import JSONRenderer

from AutoGrader.error_messages import describe_user_error
from AutoGrader.reason_codes import ENVELOPE_KEYS


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

# H-296: owner-approved form-error wording. A field error the framework
# raised (it carries a `code`) is rewritten into one plain sentence that
# names the field; anything else keeps the "Label: text" form. Only the
# top-level `message` changes; `error.field_errors` is untouched.
_FIELD_LABELS = {
    "raw_input": "assignment content",
    "schedule_time": "grading date and time",
    "current_password": "your current password",  # pragma: allowlist secret
}


def _field_label(field) -> str:
    return _FIELD_LABELS.get(str(field), str(field).replace("_", " ").lower())


def _reworded_field_error(text, field):
    code = getattr(text, "code", None)
    if code is None:
        return None
    label = _field_label(field)
    if code in ("required", "blank"):
        return f"Enter {label}."
    if code == "min_length":
        number = re.search(r"at least (\d+)", text)
        if number:
            return f"{label[:1].upper()}{label[1:]} needs at least {number.group(1)} characters."
    if code == "max_length":
        number = re.search(r"no more than (\d+)", text)
        if number:
            return f"Use {number.group(1)} characters or fewer for {label}."
    if code == "invalid" and str(text) == "Enter a valid email address.":
        return "Enter a valid email address, like name@school.edu."
    if code == "unique" and str(field) == "email":
        return (
            "An account with this email already exists. Sign in or reset your password."
        )
    return None


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
    - Multiple errors → plain sentences one after another (H-296), e.g.
        "Enter email. Enter password."

    Handles:
      • {"detail": "Not found."}
      • {"email": ["msg1"], "password": ["msg2"]}
      • {"non_field_errors": ["msg"]}
      • Nested dicts (e.g. nested serializer errors)
    """
    messages = []

    def _collect(obj, prefix="", field=None):
        if isinstance(obj, str):
            reworded = _reworded_field_error(obj, field) if field is not None else None
            if reworded is not None:
                messages.append(reworded)
            else:
                messages.append(f"{prefix}{obj}" if prefix else obj)

        elif isinstance(obj, list):
            for item in obj:
                _collect(item, prefix, field)

        elif isinstance(obj, Mapping):
            # A structured refusal (machine `code` + human `message`, plus
            # metadata such as locked_until): the message IS the text to
            # show; the other keys are for clients to branch on.
            if isinstance(obj.get("code"), str) and isinstance(obj.get("message"), str):
                _collect(obj["message"])
                return

            # H-295: a status refusal ({"status": "inactive", "message": ...},
            # billing/views.py) reads as its message, not as two "fields".
            if (
                set(obj) == {"status", "message"}
                and isinstance(obj["status"], str)
                and isinstance(obj["message"], str)
            ):
                _collect(obj["message"])
                return

            # DRF/manual top-level error keys (auth, 404, throttled, etc.)
            top_level_message_keys = ("detail", "error", "message")
            matching_keys = [key for key in top_level_message_keys if key in obj]
            # A machine-readable `code` next to the message (billing/refusals.py)
            # is for clients to branch on, not text to show. So is the rest of
            # a coded error body (FR-A-06, AutoGrader.reason_codes): when a
            # string `reason_code` marks the dict as one, its envelope keys are
            # hidden too, and `message` stays the one display sentence. A plain
            # serializer error dict with a field named e.g. `params` is not
            # affected.
            coded = isinstance(obj.get("reason_code"), str)
            shown_keys = [
                key
                for key in obj
                if not (key == "code" and isinstance(obj[key], str))
                and not (coded and key in ENVELOPE_KEYS)
            ]
            if len(shown_keys) == 1 and matching_keys:
                _collect(obj[matching_keys[0]])
                return

            for field, value in obj.items():
                if field == "non_field_errors":
                    _collect(value)
                else:
                    # Turn snake_case / CamelCase field names into readable labels
                    label = str(field).replace("_", " ").capitalize()
                    _collect(value, prefix=f"{label}: ", field=field)

    _collect(data)

    if not messages:
        return "An error occurred"

    if len(messages) == 1:
        return messages[0]

    # H-296: several errors read as plain sentences, one after another
    # (was a numbered list with line breaks).
    return " ".join(messages)


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
            message = describe_user_error(
                exc, fallback_message="An unexpected error occurred. Please try again."
            )
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
