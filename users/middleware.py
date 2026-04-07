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

            try:
                # We try creating the activity. If the user was deleted in the view,
                # this will fail because of the foreign key constraint.
                UserActivity.objects.create(user=user)

                now = int(time.time())

                cache.set(
                    f"active_user:{user.user_type}:{user.id}",
                    now,
                    timeout=ACTIVE_WINDOW_SECONDS,
                )

                cache.sadd("online_users_set", f"{user.user_type}:{user.id}")

            except Exception:
                # User was likely deleted or some other DB error occurred.
                # Since this is non-critical logging, we ignore it.
                pass

        return response
