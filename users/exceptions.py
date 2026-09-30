import logging

from rest_framework.response import Response
from rest_framework.views import exception_handler

from AutoGrader.reason_codes import CodedError, coded_response
from billing.refusals import is_permanent_refusal, log_refusal

logger = logging.getLogger(__name__)


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
        drf_response._drf_handled = True  # marker for renderer
        return drf_response

    # ---- Unhandled 500 ----
    response = Response(status=500)
    response.exception = True
    response._raw_exc = exc  # for traceback in renderer
    return response
