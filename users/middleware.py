import logging
import time

from django.core.cache import cache

from billing.models import CreditWallet

from .models import UserActivity

logger = logging.getLogger(__name__)

ACTIVE_WINDOW_SECONDS = 300

# Both keys below are DELIBERATELY named without the substring "user".
#
# users/signals.py clear_user_cache fires on every CustomUser and Settings
# save/delete and calls cache.delete_pattern("*user*"). That glob matches
# any key containing "user" - which the old names ("active_user:<type>:<id>"
# and "online_users_set") both did. Verified against real Redis: one
# unrelated user saving their settings wiped BOTH, every time.
#
# The consequences were live, not theoretical:
#
#   * The heartbeat is a SET NX throttle whose whole job is to stop this
#     middleware writing a UserActivity row and running a
#     CreditWallet.get_or_create on every authenticated request. Clearing
#     the key releases the window early, so the next request writes again -
#     the throttle was defeated by any unrelated user save, which is one of
#     the commonest writes in the system (registration, profile edit,
#     settings change, and the create_default_settings_and_wallet signal
#     chain all trigger it).
#
#   * The presence set is the concurrent-users figure. Wiping it silently
#     resets that number to zero, which reads as a traffic dip rather than
#     as a bug.
#
# users/tests_activity_middleware.py already documented this glob collision
# as a test-isolation nuisance; it was the same defect seen from the other
# side. Renaming the keys out of the swept namespace is the fix, rather
# than narrowing the sweep, because that sweep legitimately has to clear
# the per-user list/detail JSON it was written for.
#
# Anything added here must stay clear of every pattern swept in
# users/signals.py, classrooms/signals.py, assignments/signals.py and
# students/signals.py - see
# users/tests_activity_middleware.py::HeartbeatKeyNamespaceTests, which
# asserts exactly that.
HEARTBEAT_KEY_PREFIX = "presence:beat"
ONLINE_SET_KEY = "presence:online"


def heartbeat_key_for(user_type, user_id) -> str:
    """The throttle key for one user. Single definition, two readers."""
    return f"{HEARTBEAT_KEY_PREFIX}:{user_type}:{user_id}"


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
                heartbeat_key = heartbeat_key_for(user.user_type, user.id)

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
                    cache.sadd(ONLINE_SET_KEY, f"{user.user_type}:{user.id}")

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
