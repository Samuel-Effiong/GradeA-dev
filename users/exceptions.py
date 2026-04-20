import logging

from rest_framework import status
from rest_framework.exceptions import APIException
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


class NotInBetaException(APIException):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "This email is not authorized for our private Beta. You have been added to our Waiting list"
    default_code = "not_in_beta"

    def __init__(self, detail=None, code=None):
        if detail is None:
            detail = self.default_detail
        if code is None:
            code = self.default_code
        super().__init__(detail, code)
