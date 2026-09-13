"""H-1 evidence: what wildcard cache invalidation destroys besides cache.

Runs against REAL Redis, because this is a question about glob matching in a
shared keyspace and LocMem has no `delete_pattern` at all - a LocMem run
would pass while proving nothing.

The project's cache invalidation is a set of wildcard `delete_pattern` calls
fired from model signals. Everything written through the Django cache shares
one keyspace with them, including things that are not caches at all:

  * billing idempotency locks (`billing:planchange:<user id>`,
    `billing:license_overage:<subscription id>`) - deleting one lets a
    second concurrent billing mutation through;
  * the activity heartbeat and presence set (`presence:beat:*`,
    `presence:online`) - deleting them defeats a write throttle and zeroes
    the concurrent-user metric;
  * DRF throttle buckets (`throttle_<scope>_<ident>`) - deleting one resets
    a rate limit.

These already bit once: `users/middleware.py` documents that the heartbeat
and presence keys were RENAMED to avoid the substring "user" after
`delete_pattern("*user*")` was found wiping them on every unrelated user
save. That mitigation is a naming convention with nothing enforcing it, so
these tests enforce it: they fail the moment an invalidation pattern starts
matching a key that is not a cache entry.

Tracked as H-1 in docs/HARDENING_BACKLOG.md. These tests describe CURRENT
behaviour and are expected to keep passing after the H-1 redesign - the
redesign should make them true by construction rather than by naming.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase, override_settings

from classrooms.models import Course, School, Session, StudentCourse, Topic
from users.models import UserTypes

User = get_user_model()

# A dedicated Redis DB so a developer's or another suite's keys are never in
# range of the flush this test performs.
REDIS_CACHE = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://127.0.0.1:6379/12",
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
        "KEY_PREFIX": "gaplus",
    }
}

#: Every non-cache key shape the project writes through the Django cache.
#: Values are irrelevant; only whether the key SURVIVES invalidation matters.
NON_CACHE_KEYS = {
    "billing plan-change lock": "billing:planchange:11111111-2222-3333-4444-555555555555",
    "billing overage lock": "billing:license_overage:66666666-7777-8888-9999-000000000000",
    "activity heartbeat": "presence:beat:TEACHER:11111111-2222-3333-4444-555555555555",
    "presence set": "presence:online",
    "throttle (anon)": "throttle_anon_127.0.0.1",
    "throttle (login)": "throttle_login_127.0.0.1",
    "throttle (register)": "throttle_register_127.0.0.1",
    "healthcheck": "healthcheck",
}

#: DRF's built-in per-user throttle scope. NOT enabled today (settings uses
#: AnonRateThrottle + ScopedRateThrottle), which is the only reason it is
#: safe - the key contains "user" and would be matched by the "*user*"
#: pattern. Asserted separately so enabling UserRateThrottle cannot quietly
#: reintroduce the defect the presence keys were renamed to escape.
USER_SCOPE_THROTTLE_KEY = "throttle_user_11111111-2222-3333-4444-555555555555"


@override_settings(CACHES=REDIS_CACHE)
class CacheInvalidationCollateralDamageTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="Collateral School")
        self.teacher = User.objects.create_user(
            email="collateral@x.test",
            password="password123",  # nosec  # pragma: allowlist secret
        )
        self.teacher.user_type = UserTypes.TEACHER
        self.teacher.is_active = True
        self.teacher.school = self.school
        self.teacher.save()
        self.session = Session.objects.create(name="C", teacher=self.teacher)
        self.course = Course.objects.create(
            name="C", teacher=self.teacher, session=self.session
        )
        self.student = User.objects.create_user(
            email="collateral-s@x.test",
            password="password123",  # nosec  # pragma: allowlist secret
        )
        self.student.user_type = UserTypes.STUDENT
        self.student.is_active = True
        self.student.save()

    def tearDown(self):
        cache.clear()

    def _seed(self, extra=None):
        for key in NON_CACHE_KEYS.values():
            cache.set(key, "sentinel", 300)
        for key in extra or []:
            cache.set(key, "sentinel", 300)
        # A real cache entry, so we can also prove invalidation still WORKS -
        # a test that only checks survival would pass if invalidation were
        # deleted entirely.
        cache.set("courses:user_id__sentinel:query__x", "cached", 300)

    def _survivors(self, extra=None):
        missing = [
            label for label, key in NON_CACHE_KEYS.items() if cache.get(key) is None
        ]
        for key in extra or []:
            if cache.get(key) is None:
                missing.append(key)
        return missing

    def _assert_no_collateral(self, action_description):
        missing = self._survivors()
        self.assertEqual(
            missing,
            [],
            f"{action_description} deleted non-cache Redis keys: {missing}. "
            "Cache invalidation must not be able to reach locks, throttles, "
            "counters or presence data.",
        )

    # ---- one test per model whose save fires an invalidation receiver ----

    def test_saving_a_student_enrollment_spares_non_cache_keys(self):
        self._seed()
        StudentCourse.objects.create(student=self.student, course=self.course)
        self._assert_no_collateral("Creating a StudentCourse")

    def test_saving_a_course_spares_non_cache_keys(self):
        self._seed()
        Course.objects.create(
            name="Another", teacher=self.teacher, session=self.session
        )
        self._assert_no_collateral("Creating a Course")

    def test_saving_a_session_spares_non_cache_keys(self):
        self._seed()
        Session.objects.create(name="Another", teacher=self.teacher)
        self._assert_no_collateral("Creating a Session")

    def test_saving_a_topic_spares_non_cache_keys(self):
        self._seed()
        Topic.objects.create(name="T", course=self.course)
        self._assert_no_collateral("Creating a Topic")

    def test_saving_a_school_spares_non_cache_keys(self):
        self._seed()
        School.objects.create(name="Another School")
        self._assert_no_collateral("Creating a School")

    def test_saving_a_user_spares_non_cache_keys(self):
        """users.signals.clear_user_cache clears "*user*" - the broadest
        pattern in the project, and the one that already wiped the presence
        keys before they were renamed."""
        self._seed()
        self.teacher.first_name = "Renamed"
        self.teacher.save(update_fields=["first_name"])
        self._assert_no_collateral("Saving a CustomUser")

    def test_deleting_an_enrollment_spares_non_cache_keys(self):
        enrollment = StudentCourse.objects.create(
            student=self.student, course=self.course
        )
        self._seed()
        enrollment.delete()
        self._assert_no_collateral("Deleting a StudentCourse")

    # ---- the guard on the guard ----

    def test_invalidation_still_actually_works(self):
        """Without this, every test above would pass if invalidation were
        removed altogether."""
        self._seed()
        self.assertEqual(cache.get("courses:user_id__sentinel:query__x"), "cached")

        StudentCourse.objects.create(student=self.student, course=self.course)

        self.assertIsNone(
            cache.get("courses:user_id__sentinel:query__x"),
            "the cache entry survived a mutation that should have cleared it",
        )

    def test_a_user_scoped_throttle_key_would_be_collateral_damage(self):
        """DOCUMENTS A LATENT DEFECT, and fails if it becomes live.

        DRF's built-in UserRateThrottle keys as `throttle_user_<pk>`, which
        the "*user*" pattern matches. It is not enabled today, so this is
        latent - but enabling it would silently make every user save reset
        every user's rate limit. Asserted rather than commented so the
        trade-off is visible when someone turns it on.
        """
        cache.set(USER_SCOPE_THROTTLE_KEY, "sentinel", 300)
        self.teacher.last_name = "Trigger"
        self.teacher.save(update_fields=["last_name"])

        self.assertIsNone(
            cache.get(USER_SCOPE_THROTTLE_KEY),
            "throttle_user_<pk> survived - if UserRateThrottle has been "
            "enabled, update H-1: this key is no longer latent and the "
            "namespace separation is now load-bearing for rate limiting.",
        )
