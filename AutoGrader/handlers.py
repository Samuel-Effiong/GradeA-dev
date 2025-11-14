from django.http import JsonResponse
from rest_framework import status


def _json_error(message, error_detail=None, http_status=None):
    return JsonResponse(
        {"success": False, "message": "Not Found", "error": error_detail or {}},
        status=http_status,
    )


def json_400(request, exception=None):
    return _json_error(
        "Bad Request",
        {"detail": "invalid Request"},
        http_status=status.HTTP_400_BAD_REQUEST,
    )


def json_403(request, exception=None):
    return _json_error(
        "Forbidden", {"detail": "Permission denied"}, status.HTTP_403_FORBIDDEN
    )


def json_404(request, exception=None):
    return _json_error(
        "Not Found", {"detail": "Resource not found"}, status.HTTP_404_NOT_FOUND
    )


def json_500(request, exception=None):
    return _json_error("Server Error", {}, status.HTTP_500_INTERNAL_SERVER_ERROR)
