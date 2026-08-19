import logging

logger = logging.getLogger("AutoGrader.debug_middleware")


class DebugProxyHeadersMiddleware:
    """Temporary: log what the proxy tells Django about each request, to
    confirm X-Forwarded-Proto is set correctly before flipping on
    SECURE_SSL_REDIRECT. Remove once confirmed - see settings.py:176-202.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        logger.warning(
            "proxy headers: is_secure=%r scheme=%r host=%r "
            "x_forwarded_proto=%r x_forwarded_for=%r",
            request.is_secure(),
            request.scheme,
            request.get_host(),
            request.META.get("HTTP_X_FORWARDED_PROTO"),
            request.META.get("HTTP_X_FORWARDED_FOR"),
        )
        return self.get_response(request)
