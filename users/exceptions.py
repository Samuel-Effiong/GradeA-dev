import logging

from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):

    logger.error(
        "API Exception",
        exc_info=exc,
        extra={
            "view": context["view"].__class__.__name__,
            "path": context["request"].path,
            "method": context["request"].method,
            "user": getattr(context["request"].user, "pk", None),
        },
    )

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
