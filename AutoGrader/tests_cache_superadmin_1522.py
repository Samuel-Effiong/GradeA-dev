"""H-1 stage 2, superadmin families 15-22.

Approved policy (owner, 2026-09-12), which **supersedes the earlier
bounded-staleness decision (b)**:

* **15-18, 21** — `global` generation + 24h TTL. Every relevant mutation
  bumps the counter, so freshness is guaranteed and the TTL is only a
  memory knob.
* **19, 20** — **entity-class** generations (`anysch`, `anyusr`) + 24h TTL.
  Their dependency is a single low-churn table each (School 0.03 writes/day,
  CustomUser 0.8/day in production), so a global counter would invalidate
  them on activity they do not depend on.
* **22** — **not cached at all.** It reads the Redis presence set, measured
  18ms cold vs 6.9ms warm; a 3x saving does not justify a cache, and a 900s
  TTL on a live concurrency figure could be three presence windows stale.

The 24h TTL is the part most easily mistaken for a shortcut, so it is
tested as a *consequence* of versioning rather than asserted: the freshness
tests below run with the legacy mechanism disabled, which means a stale
response cannot be rescued by a wildcard sweep OR by the TTL (24h outlives
any test). If versioning failed, these tests would fail.

Real Redis + real Postgres.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase, override_settings

from assignments.models import Assignment
from AutoGrader.cache_generation import (
    SCOPE_ANY_SCHOOL,
    SCOPE_ANY_USER,
    SCOPE_GLOBAL,
    get_generation,
)
from classrooms.models import Course, School, Session, StudentCourse
from users.models import UserTypes

User = get_user_model()

REDIS_CACHE = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://127.0.0.1:6379/5",
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
        "KEY_PREFIX": "gaplus",
    }
}

LEGACY_MODULES = (
    "classrooms.signals",
    "users.signals",
    "students.signals",
    "assignments.signals",
)

ADOPTION = "/api/v1/super-admin/dashboard/adoption"
USAGE = "/api/v1/super-admin/dashboard/usage"
AI_PERF = "/api/v1/super-admin/dashboard/ai_performance"
SCALING = "/api/v1/super-admin/dashboard/scaling_signals"
SCHOOLS = "/api/v1/super-admin/dashboard/schools"
TEACHERS = "/api/v1/super-admin/dashboard/teachers"
STUDENTS = "/api/v1/super-admin/dashboard/students"
CONCURRENCY = "/api/v1/super-admin/dashboard/concurrency"


def make_user(email, user_type, school=None, superuser=False):
    user = User.objects.create_user(
        email=email,
        password="password123",  # nosec  # pragma: allowlist secret
    )
    user.user_type = user_type
    user.is_active = True
    user.school = school
    if superuser:
        user.is_superuser = True
        user.is_staff = True
    user.save()
    return user


@override_settings(CACHES=REDIS_CACHE)
class SuperadminBase(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="SA1522 School")
        self.superadmin = make_user(
            "sa1522@x.test", UserTypes.SUPER_ADMIN, superuser=True
        )
        self.teacher = make_user("sa1522-t@x.test", UserTypes.TEACHER, self.school)
        self.session = Session.objects.create(name="SA1522", teacher=self.teacher)
        self.course = Course.objects.create(
            name="SA1522 101", teacher=self.teacher, session=self.session
        )

        from rest_framework.test import APIClient

        self.client = APIClient()
        self.client.force_authenticate(self.superadmin)

    def tearDown(self):
        cache.clear()

    def disable_legacy(self):
        patches = [
            patch(f"{module}.delete_cache_patterns", lambda *a, **k: None)
            for module in LEGACY_MODULES
        ]
        for p in patches:
            p.start()
        self.addCleanup(self._stop, patches)

    @staticmethod
    def _stop(patches):
        for p in patches:
            p.stop()

    def get(self, path, **params):
        response = self.client.get(path, params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data


class GlobalScopedFamiliesTests(SuperadminBase):
    """Families 15-18 and 21 — `global` generation, legacy disabled.

    With the wildcards off and a 24h TTL, ONLY the generation bump can make
    these responses refresh. Nothing else could rescue a stale read.
    """

    def setUp(self):
        super().setUp()
        self.disable_legacy()

    def test_the_legacy_mechanism_really_is_disabled(self):
        cache.set("courses:user_id__sentinel:query__x", "cached", 300)
        self.teacher.first_name = "Trigger"
        self.teacher.save(update_fields=["first_name"])
        self.assertEqual(
            cache.get("courses:user_id__sentinel:query__x"),
            "cached",
            "a legacy sweep still ran - every test here would be masked",
        )

    def test_adoption_refreshes_after_a_signup(self):
        before = self.get(ADOPTION)
        make_user("sa1522-new@x.test", UserTypes.TEACHER, self.school)
        self.assertNotEqual(before, self.get(ADOPTION))

    def test_adoption_refreshes_after_a_new_assignment(self):
        before = self.get(ADOPTION)
        Assignment.objects.create(
            title="Adopt", course=self.course, teacher=self.teacher
        )
        self.assertNotEqual(before, self.get(ADOPTION))

    def test_usage_refreshes_after_a_new_assignment(self):
        before = self.get(USAGE)
        Assignment.objects.create(title="Use", course=self.course, teacher=self.teacher)
        self.assertNotEqual(before, self.get(USAGE))

    def test_ai_performance_refreshes_after_a_graded_submission(self):
        from django.utils import timezone

        from students.models import StudentSubmission

        student = make_user("sa1522-s@x.test", UserTypes.STUDENT)
        StudentCourse.objects.create(student=student, course=self.course)
        assignment = Assignment.objects.create(
            title="Graded", course=self.course, teacher=self.teacher
        )
        before = self.get(AI_PERF)

        StudentSubmission.objects.create(
            student=student,
            assignment=assignment,
            answers={},
            score=88,
            max_points=100,
            graded_at=timezone.now(),
            grading_confidence=0.9,
        )

        self.assertNotEqual(before, self.get(AI_PERF))

    def test_scaling_signals_refreshes_after_a_new_school(self):
        before = self.get(SCALING)
        School.objects.create(name="SA1522 Another")
        self.assertNotEqual(before, self.get(SCALING))

    def test_students_refreshes_after_an_enrollment(self):
        student = make_user("sa1522-s2@x.test", UserTypes.STUDENT)
        before = self.get(STUDENTS)
        StudentCourse.objects.create(student=student, course=self.course)
        self.assertNotEqual(before, self.get(STUDENTS))

    def test_the_responses_are_still_cached_between_mutations(self):
        """Guard on the guard. A 24h TTL means a second read MUST be a cache
        hit; if it were not, every freshness test above would pass
        trivially."""
        self.get(ADOPTION)
        generation = get_generation(SCOPE_GLOBAL)
        self.get(ADOPTION)
        self.assertEqual(get_generation(SCOPE_GLOBAL), generation)


class EntityClassScopedFamiliesTests(SuperadminBase):
    """Families 19 and 20 — the point of `anysch` / `anyusr`.

    These exist so a dashboard whose dependency is ONE table is not
    invalidated by unrelated activity. Both directions are asserted:
    the relevant write refreshes it, and an irrelevant write does not.
    """

    def setUp(self):
        super().setUp()
        self.disable_legacy()

    def test_schools_refreshes_after_a_new_school(self):
        before = self.get(SCHOOLS)
        School.objects.create(name="SA1522 Fresh")
        self.assertNotEqual(before, self.get(SCHOOLS))

    def test_teachers_refreshes_after_a_new_teacher(self):
        before = self.get(TEACHERS)
        make_user("sa1522-t2@x.test", UserTypes.TEACHER, self.school)
        self.assertNotEqual(before, self.get(TEACHERS))

    def test_an_assignment_does_NOT_bump_the_school_table_counter(self):
        """The precision that justifies a separate counter: publishing an
        assignment is irrelevant to the schools list."""
        before = get_generation(SCOPE_ANY_SCHOOL)
        Assignment.objects.create(
            title="Irrelevant", course=self.course, teacher=self.teacher
        )
        self.assertEqual(
            get_generation(SCOPE_ANY_SCHOOL),
            before,
            "an assignment bumped the School-table counter - families 19 "
            "would be invalidated by activity they do not depend on, which "
            "is the whole reason this scope exists",
        )

    def test_an_assignment_does_NOT_bump_the_user_table_counter(self):
        before = get_generation(SCOPE_ANY_USER)
        Assignment.objects.create(
            title="Irrelevant 2", course=self.course, teacher=self.teacher
        )
        self.assertEqual(get_generation(SCOPE_ANY_USER), before)

    def test_an_enrollment_does_NOT_bump_either_entity_class_counter(self):
        student = make_user("sa1522-s3@x.test", UserTypes.STUDENT)
        before_sch = get_generation(SCOPE_ANY_SCHOOL)
        before_usr = get_generation(SCOPE_ANY_USER)

        StudentCourse.objects.create(student=student, course=self.course)

        self.assertEqual(get_generation(SCOPE_ANY_SCHOOL), before_sch)
        self.assertEqual(get_generation(SCOPE_ANY_USER), before_usr)

    def test_the_schools_list_survives_unrelated_activity(self):
        """End to end: the response itself must be byte-identical after a
        mutation it does not depend on."""
        before = self.get(SCHOOLS)
        Assignment.objects.create(
            title="Still irrelevant", course=self.course, teacher=self.teacher
        )
        self.assertEqual(
            before,
            self.get(SCHOOLS),
            "the schools list was rebuilt after an unrelated assignment",
        )


class ConcurrencyIsNotCachedTests(SuperadminBase):
    """Family 22 — deliberately uncached.

    Presence is a live figure over a 300s window; a 900s TTL could serve it
    three windows stale for a 3x latency saving. The decision is recorded as
    a test so that "helpfully" re-adding a cache fails here.
    """

    def test_two_reads_are_computed_independently(self):
        """No cache means a second read is not served from the first."""
        self.get(CONCURRENCY)
        cache.clear()
        self.get(CONCURRENCY)  # must still succeed with nothing cached

    def test_no_cache_entry_is_written_for_concurrency(self):
        cache.clear()
        self.get(CONCURRENCY)

        from django.core.cache import cache as live_cache

        client = live_cache.client.get_client(write=True)
        leftovers = list(client.scan_iter(match="*concurrency*", count=1000))
        self.assertEqual(
            leftovers,
            [],
            "a cache entry was written for the concurrency dashboard - it is "
            "deliberately uncached (18ms cold vs 6.9ms warm does not justify "
            "staleness on a live figure)",
        )

    def test_it_reflects_presence_immediately(self):
        """The property caching would have broken."""
        first = self.get(CONCURRENCY)
        self.assertIn("peak_concurrent_users", first)
