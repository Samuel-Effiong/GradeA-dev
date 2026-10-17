import logging

from rest_framework.exceptions import AuthenticationFailed, Throttled
from rest_framework.response import Response
from rest_framework.views import exception_handler

from audit.enums import ReasonCode
from AutoGrader.reason_codes import CodedError, add_coded_envelope, coded_response
from billing.errors import StudentEmptyWalletError
from billing.refusals import is_permanent_refusal, log_refusal
from students.exceptions import STUDENT_UPLOAD_NOT_PROCESSED

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
        if isinstance(exc, StudentEmptyWalletError):
            # H-225: a student refused at the permission (their teacher's
            # wallet is empty) keeps the code and status but reads the one
            # fixed student sentence, with no remediation (as H-216 in the
            # view).
            response.data["error"] = STUDENT_UPLOAD_NOT_PROCESSED
            response.data["remediation"] = None
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
        elif isinstance(exc, Throttled) and isinstance(drf_response.data, dict):
            # The generic catch-all: every OTHER throttle (the global
            # defaults, the named AnonRateThrottle buckets in
            # users/throttling.py, dashboard/throttling.py's
            # CustomAIPromptThrottle, and any future one) raises a plain
            # `Throttled` with no `.envelope`, so it never hit the branch
            # above. `wait` is the same number DRF already used to build
            # `detail` and the `Retry-After` header (None only if a
            # throttle's `wait()` can't say how long - no observed case
            # today, but the catalogue requires every placeholder).
            add_coded_envelope(
                drf_response.data,
                ReasonCode.RATE_LIMITED,
                str(drf_response.data.get("detail", "")),
                params={"wait_seconds": round(exc.wait) if exc.wait is not None else 0},
            )
        drf_response._drf_handled = True  # marker for renderer
        return drf_response

    # ---- Unhandled 500 ----
    response = Response(status=500)
    response.exception = True
    response._raw_exc = exc  # for traceback in renderer
    return response
