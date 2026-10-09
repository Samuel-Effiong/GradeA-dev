"""H-1: cache invalidation cannot destroy anything besides cache.

Runs against REAL Redis, because this is a question about what a shared
keyspace loses when a model is saved.

Until H-1 step 4 the project's cache invalidation was a set of wildcard
`delete_pattern` calls fired from model signals, and everything written
through the Django cache shared one keyspace with them, including things
that are not caches at all:

  * billing idempotency locks (`billing:planchange:<user id>`,
    `billing:license_overage:<subscription id>`) - deleting one lets a
    second concurrent billing mutation through;
  * the activity heartbeat and presence set (`presence:beat:*`,
    `presence:online`) - deleting them defeats a write throttle and zeroes
    the concurrent-user metric;
  * DRF throttle buckets (`throttle_<scope>_<ident>`) - deleting one resets
    a rate limit.

These bit once: `users/middleware.py` records that the heartbeat and
presence keys were RENAMED to avoid the substring "user" after
`delete_pattern("*user*")` was found wiping them on every unrelated user
save. That was safety by naming convention.

Step 4 removed every wildcard, so collateral damage is now structurally
impossible rather than avoided by naming: invalidation only increments
generation counters under `cachegen:`. These tests assert exactly that, on
every model save that invalidates - no DEL/UNLINK/SCAN/KEYS/FLUSH is sent,
and every key written is a counter - plus the end result (every non-cache
key survives) and that invalidation still WORKS (the versioned entry
becomes unreachable), so none of it can pass by invalidating nothing.
"""

from collections import Counter
from contextlib import contextmanager
from unittest.mock import patch

import redis
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase, override_settings

from AutoGrader.cache_generation import SCOPE_USER, versioned_key
from AutoGrader.test_cache import real_redis_caches
from classrooms.models import Course, School, Session, StudentCourse, Topic
from users.models import UserTypes

User = get_user_model()

#: Redis commands that remove keys. Invalidation must send none of them.
DESTRUCTIVE = {"DEL", "UNLINK", "SCAN", "KEYS", "FLUSHDB", "FLUSHALL", "EXPIRE"}
#: Commands that write a key. Invalidation may only write counters.
WRITES = {
    "SET",
    "SETEX",
    "PSETEX",
    "SETNX",
    "INCR",
    "INCRBY",
    "DECR",
    "DECRBY",
    "GETSET",
}


def _text(value):
    return value.decode() if isinstance(value, bytes) else str(value)


@contextmanager
def redis_commands_with_keys():
    """Every (command, first key) this process sends to Redis, both the
    single-command and the pipelined path (see
    AutoGrader/tests_cache_generation.redis_commands_sent_by_this_process)."""
    sent = []
    connection_class = redis.connection.AbstractConnection
    send_one = connection_class.send_command
    pack_many = connection_class.pack_commands

    def record(args):
        name = _text(args[0]).upper()
        key = _text(args[1]) if len(args) > 1 else ""
        sent.append((name, key))

    def recording_send_command(self, *args, **kwargs):
        record(args)
        return send_one(self, *args, **kwargs)

    def recording_pack_commands(self, commands):
        for command in commands:
            record(command)
        return pack_many(self, commands)

    with patch.object(connection_class, "send_command", recording_send_command):
        with patch.object(connection_class, "pack_commands", recording_pack_commands):
            yield sent


# A dedicated Redis DB so a developer's or another suite's keys are never in
# range of the flush this test performs.
REDIS_CACHE = real_redis_caches("redis://127.0.0.1:6379/12")

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
#: AnonRateThrottle + ScopedRateThrottle). Until H-1 step 4 that was the only
#: thing keeping it safe - the key contains "user" and the "*user*" sweep
#: matched it. Now it is safe by construction; asserted below.
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

    def _cached_entry_key(self):
        """The student's live versioned course-list key (UserCacheMixin
        shape), recomputed at call time."""
        return versioned_key(
            f"courses:user_id__{self.student.id}:query__x",
            [(SCOPE_USER, self.student.id)],
        )

    def _seed(self, extra=None):
        for key in NON_CACHE_KEYS.values():
            cache.set(key, "sentinel", 300)
        for key in extra or []:
            cache.set(key, "sentinel", 300)
        # A real (versioned) cache entry, so we can also prove invalidation
        # still WORKS - a test that only checks survival would pass if
        # invalidation were deleted entirely.
        self.seeded_entry_key = self._cached_entry_key()
        cache.set(self.seeded_entry_key, "cached", 300)

    def _survivors(self, extra=None):
        missing = [
            label for label, key in NON_CACHE_KEYS.items() if cache.get(key) is None
        ]
        for key in extra or []:
            if cache.get(key) is None:
                missing.append(key)
        return missing

    @contextmanager
    def _structurally_harmless(self, action_description):
        """The mutation may only INCREMENT COUNTERS: it sends no command
        that removes a key, and every key it writes is under `cachegen:`.
        That is what makes collateral damage impossible rather than merely
        absent from the keys this file happens to seed."""
        with redis_commands_with_keys() as sent:
            yield
        commands = Counter(name for name, _ in sent)
        destructive = {name: n for name, n in commands.items() if name in DESTRUCTIVE}
        self.assertEqual(
            destructive,
            {},
            f"{action_description} sent key-removing Redis commands: {destructive}",
        )
        non_counter_writes = sorted(
            {key for name, key in sent if name in WRITES and ":cachegen:" not in key}
        )
        self.assertEqual(
            non_counter_writes,
            [],
            f"{action_description} wrote non-counter keys while invalidating",
        )
        self.assertTrue(
            any(name in WRITES for name, _ in sent),
            f"{action_description} bumped no generation at all",
        )

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
        with self._structurally_harmless("Creating a StudentCourse"):
            StudentCourse.objects.create(student=self.student, course=self.course)
        self._assert_no_collateral("Creating a StudentCourse")

    def test_saving_a_course_spares_non_cache_keys(self):
        self._seed()
        with self._structurally_harmless("Creating a Course"):
            Course.objects.create(
                name="Another", teacher=self.teacher, session=self.session
            )
        self._assert_no_collateral("Creating a Course")

    def test_saving_a_session_spares_non_cache_keys(self):
        self._seed()
        with self._structurally_harmless("Creating a Session"):
            Session.objects.create(name="Another", teacher=self.teacher)
        self._assert_no_collateral("Creating a Session")

    def test_saving_a_topic_spares_non_cache_keys(self):
        self._seed()
        with self._structurally_harmless("Creating a Topic"):
            Topic.objects.create(name="T", course=self.course)
        self._assert_no_collateral("Creating a Topic")

    def test_saving_a_school_spares_non_cache_keys(self):
        self._seed()
        with self._structurally_harmless("Creating a School"):
            School.objects.create(name="Another School")
        self._assert_no_collateral("Creating a School")

    def test_saving_a_user_spares_non_cache_keys(self):
        """users.signals.clear_user_cache used to clear "*user*" - the
        broadest pattern in the project, and the one that already wiped the
        presence keys before they were renamed."""
        self._seed()
        with self._structurally_harmless("Saving a CustomUser"):
            self.teacher.first_name = "Renamed"
            self.teacher.save(update_fields=["first_name"])
        self._assert_no_collateral("Saving a CustomUser")

    def test_deleting_an_enrollment_spares_non_cache_keys(self):
        enrollment = StudentCourse.objects.create(
            student=self.student, course=self.course
        )
        self._seed()
        with self._structurally_harmless("Deleting a StudentCourse"):
            enrollment.delete()
        self._assert_no_collateral("Deleting a StudentCourse")

    # ---- the guard on the guard ----

    def test_invalidation_still_actually_works(self):
        """Without this, every test above would pass if invalidation were
        removed altogether. The entry is not deleted (nothing is); it
        becomes unreachable because the key the view would read moved."""
        self._seed()
        self.assertEqual(cache.get(self._cached_entry_key()), "cached")

        StudentCourse.objects.create(student=self.student, course=self.course)

        self.assertNotEqual(
            self._cached_entry_key(),
            self.seeded_entry_key,
            "the student's generation did not move on their own enrolment",
        )
        self.assertIsNone(
            cache.get(self._cached_entry_key()),
            "the cached entry is still reachable after a mutation that "
            "should have invalidated it",
        )
        self.assertEqual(
            cache.get(self.seeded_entry_key),
            "cached",
            "the superseded entry was deleted - invalidation must not delete",
        )

    def test_a_user_scoped_throttle_key_survives_a_user_save(self):
        """The latent defect this used to DOCUMENT is closed.

        DRF's built-in UserRateThrottle keys as `throttle_user_<pk>`, which
        the "*user*" pattern matched: enabling it would have made every user
        save reset every user's rate limit, and this test asserted the key
        was destroyed so that trade-off stayed visible. With the wildcards
        removed (H-1 step 4) the key survives, so UserRateThrottle can be
        enabled without a cache-namespace constraint.
        """
        cache.set(USER_SCOPE_THROTTLE_KEY, "sentinel", 300)
        with self._structurally_harmless("Saving a CustomUser"):
            self.teacher.last_name = "Trigger"
            self.teacher.save(update_fields=["last_name"])

        self.assertEqual(cache.get(USER_SCOPE_THROTTLE_KEY), "sentinel")
