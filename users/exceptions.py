import logging

from rest_framework.exceptions import AuthenticationFailed, Throttled
from rest_framework.response import Response
from rest_framework.views import exception_handler

from AutoGrader.reason_codes import CodedError, add_coded_envelope, coded_response
from billing.refusals import is_permanent_refusal, log_refusal

logger = logging.getLogger(__name__)


# The sign-in locks (v2's S6a N3, SM ruling): DRF's own answer - status,
# `detail`, `Retry-After`, `WWW-Authenticate` - exactly as documented, with
# the coded envelope ADDED to its body by the handler below (the F8
# pattern). `envelope` is (reason code, the `code` value to add if absent).


class EnvelopedAuthenticationFailed(AuthenticationFailed):
    def __init__(self, detail, code, *, reason_code, code_value=None):
        super().__init__(detail, code)
        self.envelope = (reason_code, code_value)


class EnvelopedThrottled(Throttled):
    def __init__(self, wait=None, detail=None, *, reason_code, code_value=None):
        super().__init__(wait=wait, detail=detail)
        self.envelope = (reason_code, code_value)


def custom_exception_handler(exc, context):
    request = context["request"]
    extra = {
        "view": context["view"].__class__.__name__,
        "path": request.path,
        "method": request.method,
        "user": getattr(request.user, "pk", None),
    }

    # ---- A coded failure no view caught (FR-A-06): its own status and the
    # coded body, never a 500. A refusal (credits, plan) is one of these. ----
    if is_permanent_refusal(exc) or isinstance(exc, CodedError):
        if is_permanent_refusal(exc):
            log_refusal(logger, "API request", exc, **extra)
        else:
            logger.warning(
                "API request failed with %s: %s",
                exc.reason_code,
                exc.detail or exc,
                extra=extra,
            )
        response = coded_response(exc)
        response._drf_handled = True  # marker for renderer
        return response

    logger.error("API Exception", exc_info=exc, extra=extra)

    # ---- Let DRF try to handle it (validation, 404, etc.) ----
    drf_response = exception_handler(exc, context)

    if drf_response is not None:
        envelope = getattr(exc, "envelope", None)
        if envelope is not None and isinstance(drf_response.data, dict):
            reason_code, code_value = envelope
            add_coded_envelope(
                drf_response.data,
                reason_code,
                str(drf_response.data.get("detail", "")),
                code_value=code_value,
            )
        drf_response._drf_handled = True  # marker for renderer
        return drf_response

    # ---- Unhandled 500 ----
    response = Response(status=500)
    response.exception = True
    response._raw_exc = exc  # for traceback in renderer
    return response
