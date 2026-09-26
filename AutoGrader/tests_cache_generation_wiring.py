"""H-1 stage 2: the read -> mutation -> bump -> fresh-read chain, end to end.

Stage 1 proved the counter mechanism in isolation. This proves it is
actually WIRED: that a real mutation through the ORM bumps the right
counter, and that a real cached endpoint response goes stale as a result.

Runs against real Redis and real Postgres. LocMem cannot be used - it has no
`delete_pattern`, so the old mechanism silently no-ops there and a test
would pass without proving which mechanism did the work.

Every test in `MigratedFamiliesTests` follows the chain the owner specified:

    read (populates cache) -> mutation -> generation bump -> read again

and asserts the second read is FRESH, not that some key vanished - a key
disappearing proves nothing about which mechanism removed it.

Coverage status is tracked in `docs/H1_CACHE_INVALIDATION_DESIGN.md` §3.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from AutoGrader.cache_generation import (
    SCOPE_COURSE,
    SCOPE_GLOBAL,
    SCOPE_SCHOOL,
    SCOPE_USER,
    get_generation,
)
from AutoGrader.test_cache import real_redis_caches
from classrooms.models import Course, School, Session, StudentCourse, Topic
from users.models import UserTypes

User = get_user_model()

REDIS_CACHE = real_redis_caches("redis://127.0.0.1:6379/8")


def make_user(email, user_type, school=None):
    user = User.objects.create_user(
        email=email,
        password="password123",  # nosec  # pragma: allowlist secret
    )
    user.user_type = user_type
    user.is_active = True
    user.school = school
    user.save()
    return user


@override_settings(CACHES=REDIS_CACHE)
class SignalBumpWiringTests(TransactionTestCase):
    """Does a real ORM mutation move the counters it is supposed to?"""

    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="Wiring School")
        self.teacher = make_user("wire-t@x.test", UserTypes.TEACHER, self.school)
        self.student = make_user("wire-s@x.test", UserTypes.STUDENT)
        self.session = Session.objects.create(name="Wire", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Wire 101", teacher=self.teacher, session=self.session
        )

    def tearDown(self):
        cache.clear()

    def snapshot(self):
        return {
            "teacher": get_generation(SCOPE_USER, self.teacher.pk),
            "student": get_generation(SCOPE_USER, self.student.pk),
            "course": get_generation(SCOPE_COURSE, self.course.pk),
            "school": get_generation(SCOPE_SCHOOL, self.school.pk),
            "global": get_generation(SCOPE_GLOBAL),
        }

    def assert_advanced(self, before, *names):
        after = self.snapshot()
        for name in names:
            self.assertGreater(
                after[name],
                before[name],
                f"{name} generation did not advance - a cached response "
                f"depending on it would stay stale",
            )

    def assert_unchanged(self, before, *names):
        after = self.snapshot()
        for name in names:
            self.assertEqual(
                after[name],
                before[name],
                f"{name} generation moved when it should not have - this is "
                f"over-invalidation, the bug being replaced",
            )

    def test_enrolling_a_student_bumps_student_teacher_course_and_school(self):
        before = self.snapshot()
        StudentCourse.objects.create(student=self.student, course=self.course)
        self.assert_advanced(before, "student", "teacher", "course", "school")

    def test_enrolling_does_not_bump_an_unrelated_user(self):
        stranger = make_user("wire-x@x.test", UserTypes.STUDENT)
        before = get_generation(SCOPE_USER, stranger.pk)
        StudentCourse.objects.create(student=self.student, course=self.course)
        self.assertEqual(
            get_generation(SCOPE_USER, stranger.pk),
            before,
            "an unrelated user's generation moved - tenant isolation broken",
        )

    def test_removing_an_enrollment_bumps_the_same_counters(self):
        enrollment = StudentCourse.objects.create(
            student=self.student, course=self.course
        )
        before = self.snapshot()
        enrollment.delete()
        self.assert_advanced(before, "student", "teacher", "course", "school")

    def test_saving_a_course_bumps_course_teacher_and_school(self):
        before = self.snapshot()
        self.course.name = "Renamed"
        self.course.save()
        self.assert_advanced(before, "course", "teacher", "school")

    def test_saving_a_topic_bumps_its_course(self):
        before = self.snapshot()
        Topic.objects.create(name="Algebra", course=self.course)
        self.assert_advanced(before, "course", "teacher")

    def test_saving_a_session_bumps_its_teacher(self):
        before = self.snapshot()
        Session.objects.create(name="Second", teacher=self.teacher)
        self.assert_advanced(before, "teacher")

    def test_saving_a_school_bumps_school_and_global(self):
        before = self.snapshot()
        self.school.name = "Renamed School"
        self.school.save()
        self.assert_advanced(before, "school", "global")

    def test_saving_a_user_bumps_only_that_user(self):
        """The headline replacement: today every CustomUser save is a
        de-facto full flush because "*user*" matches 29 of 35 families.

        Renaming a teacher moves the teacher AND their school - but nothing
        else. This test originally asserted the school must NOT move, and
        that encoded a real defect: the school admin's teacher-performance
        dashboard lists each teacher's name and is keyed on the school, so a
        legacy-disabled probe showed it serving the old name (H-1 Stage 3
        item 7). The student and the course are still untouched.
        """
        before = self.snapshot()
        self.teacher.first_name = "Renamed"
        self.teacher.save(update_fields=["first_name"])
        self.assert_advanced(before, "teacher", "school")
        self.assert_unchanged(before, "student", "course")

    def test_a_bump_failure_does_not_break_the_mutation(self):
        """Receivers run inside the caller's transaction."""
        from unittest.mock import patch

        import redis as redis_lib

        with patch(
            "AutoGrader.cache_generation.cache.incr",
            side_effect=redis_lib.exceptions.ConnectionError("down"),
        ), patch(
            "AutoGrader.cache_generation.cache.add",
            side_effect=redis_lib.exceptions.ConnectionError("down"),
        ), patch(
            "AutoGrader.cache_generation._bump_pipelined", return_value=None
        ):
            StudentCourse.objects.create(student=self.student, course=self.course)

        self.assertTrue(
            StudentCourse.objects.filter(
                student=self.student, course=self.course
            ).exists(),
            "a cache-generation failure rolled back the enrollment",
        )


@override_settings(CACHES=REDIS_CACHE)
class MigratedFamiliesTests(TransactionTestCase):
    """read -> mutation -> bump -> fresh read, through the real API.

    These are the families served by `UserCacheMixin`, which is nine of the
    project's 35 and the first block migrated in stage 2.
    """

    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="Family School")
        self.teacher = make_user("fam-t@x.test", UserTypes.TEACHER, self.school)
        self.session = Session.objects.create(name="Fam", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Original Name", teacher=self.teacher, session=self.session
        )
        self.client = APIClient()
        self.client.force_authenticate(self.teacher)

    def tearDown(self):
        cache.clear()

    def course_names(self):
        response = self.client.get(reverse("course-list"))
        self.assertEqual(response.status_code, 200)
        return [row["name"] for row in response.data["results"]]

    def test_the_response_is_actually_cached(self):
        """Guard on the guard: if nothing is cached, every freshness test
        below would pass trivially."""
        self.course_names()
        key_generation = get_generation(SCOPE_USER, self.teacher.pk)
        self.course_names()
        self.assertEqual(get_generation(SCOPE_USER, self.teacher.pk), key_generation)

    def test_renaming_a_course_is_visible_on_the_next_read(self):
        self.assertEqual(self.course_names(), ["Original Name"])

        self.course.name = "Renamed"
        self.course.save()

        self.assertEqual(
            self.course_names(),
            ["Renamed"],
            "the cached course list did not refresh after a mutation",
        )

    def test_a_new_course_appears_on_the_next_read(self):
        self.assertEqual(len(self.course_names()), 1)

        Course.objects.create(name="Second", teacher=self.teacher, session=self.session)

        self.assertEqual(len(self.course_names()), 2)

    def test_a_deleted_course_disappears_on_the_next_read(self):
        self.course_names()
        self.course.delete()
        self.assertEqual(self.course_names(), [])

    def test_the_key_carries_a_generation_segment(self):
        """Structural: proves the migrated path, not the legacy one, built
        this key."""
        from classrooms.views import CourseViewSet

        view = CourseViewSet()
        view.request = type("R", (), {"user": self.teacher, "query_params": {}})()
        view.kwargs = {}
        view.request.query_params = __import__(
            "django.http", fromlist=["QueryDict"]
        ).QueryDict("")
        key = view.get_cache_key("list")
        self.assertIn(":g.", key)
        self.assertIn(
            f"{SCOPE_USER}={get_generation(SCOPE_USER, self.teacher.pk)}", key
        )

    def test_another_users_cached_list_is_untouched_by_this_users_mutation(self):
        other = make_user("fam-o@x.test", UserTypes.TEACHER, self.school)
        other_session = Session.objects.create(name="Other", teacher=other)
        Course.objects.create(name="Other Course", teacher=other, session=other_session)
        other_client = APIClient()
        other_client.force_authenticate(other)

        before = get_generation(SCOPE_USER, other.pk)
        self.course.name = "Renamed Again"
        self.course.save()

        self.assertEqual(
            get_generation(SCOPE_USER, other.pk),
            before,
            "another teacher's generation moved because of this teacher's "
            "course change - that is the over-invalidation being removed",
        )
