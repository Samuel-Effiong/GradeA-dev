import logging
import time

from django.core.cache import cache

from billing.models import CreditWallet

from .models import UserActivity

logger = logging.getLogger(__name__)

ACTIVE_WINDOW_SECONDS = 300


class UserActivityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):

        response = self.get_response(request)

        user = getattr(request, "user", None)

        if user and user.is_authenticated:
            # Everything below is inside the try, the cache read included.
            # This middleware runs on every authenticated request and its
            # work is pure bookkeeping, so NOTHING here may be allowed to
            # fail the response the view already produced - a Redis outage
            # must cost an activity row, not the request.
            try:
                heartbeat_key = f"active_user:{user.user_type}:{user.id}"

                # Throttled to once per ACTIVE_WINDOW_SECONDS: this used to
                # run unconditionally on every authenticated request,
                # writing a UserActivity row and doing a
                # CreditWallet.get_or_create() on every single one - two DB
                # round trips per request, per user, with no upper bound on
                # table growth. Nothing downstream reads UserActivity at a
                # finer grain than "most recent row" (dashboard.tasks reads
                # it via aggregate(Max("timestamp"))), so a row per
                # heartbeat window loses no signal that matters.
                #
                # `add` (SET NX on Redis), not get-then-set: it claims the
                # window atomically and returns False if someone already
                # holds it. A read followed by a write is a check-then-act
                # race, and the requests most likely to lose it are
                # simultaneous ones - exactly the burst the throttle exists
                # to absorb - so under load it degraded to no throttle at
                # all and wrote a row per request anyway.
                #
                # Claiming BEFORE the DB writes is deliberate. If the writes
                # then fail, this window records nothing (logged below)
                # rather than letting every racing request retry them.
                if not cache.add(
                    heartbeat_key, int(time.time()), ACTIVE_WINDOW_SECONDS
                ):
                    return response

                # We try creating the activity. If the user was deleted in the view,
                # this will fail because of the foreign key constraint.
                UserActivity.objects.create(user=user)
                CreditWallet.objects.get_or_create(user=user)

                # Guarded like AutoGrader.cache_utils does for
                # delete_pattern: `sadd` is a django-redis extension, so a
                # non-Redis backend (LocMem in tests) simply has no presence
                # index. That is a missing feature, not a failure worth
                # logging on every single request.
                if hasattr(cache, "sadd"):
                    cache.sadd("online_users_set", f"{user.user_type}:{user.id}")

            except Exception:
                # User was likely deleted or some other DB error occurred.
                # Non-critical (activity logging), so the request must not
                # fail over it - but still logged, since a silently-broken
                # heartbeat (e.g. cache.sadd failing) would otherwise be
                # invisible until someone notices the concurrent-users
                # dashboard is wrong.
                logger.warning(
                    "Failed to record user activity heartbeat for user %s",
                    getattr(user, "id", None),
                    exc_info=True,
                )

        return response
