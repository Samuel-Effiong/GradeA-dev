import logging

from rest_framework.response import Response
from rest_framework.views import exception_handler

from billing.refusals import is_permanent_refusal, log_refusal, refusal_response

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    request = context["request"]
    extra = {
        "view": context["view"].__class__.__name__,
        "path": request.path,
        "method": request.method,
        "user": getattr(request.user, "pk", None),
    }

    # ---- A refusal no view caught: 402/403 with a code, never a 500 ----
    if is_permanent_refusal(exc):
        log_refusal(logger, "API request", exc, **extra)
        response = refusal_response(exc)
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
