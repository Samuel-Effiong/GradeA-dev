import time

from django.core.cache import cache

from .models import UserActivity

ACTIVE_WINDOW_SECONDS = 300


class UserActivityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)

        if user and user.is_authenticated:
            # Log activity.
            # Note: For high traffic, this should be throttled (e.g. once per 1-5 mins)
            # or moved to a background task/cache.
            UserActivity.objects.create(user=user)
            now = int(time.time())

            cache.set(
                f"active_user:{user.user_type}:{user.id}",
                now,
                timeout=ACTIVE_WINDOW_SECONDS,
            )

        response = self.get_response(request)
        return response
