import time

from django.core.cache import cache

from .models import UserActivity

ACTIVE_WINDOW_SECONDS = 300


class UserActivityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):

        response = self.get_response(request)

        user = getattr(request, "user", None)

        if user and user.is_authenticated:
            # Log activity.
            # Note: For high traffic, this should be throttled (e.g. once per 1-5 mins)
            # or moved to a background task/cache.
            object = UserActivity.objects.create(user=user)
            print(object)

            now = int(time.time())

            cache.set(
                f"active_user:{user.user_type}:{user.id}",
                now,
                timeout=ACTIVE_WINDOW_SECONDS,
            )

            cache.sadd("online_users_set", f"{user.user_type}:{user.id}")

        return response
