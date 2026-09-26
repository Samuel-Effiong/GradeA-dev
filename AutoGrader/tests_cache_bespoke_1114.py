"""H-1 stage 2, families 11-14: the four bespoke single-key read sites.

These are the last applicable families. They are not served by
`UserCacheMixin` - each builds its own key in its own view - so each needed
its dependency read off the code rather than inferred from the pattern.

| # | Read site | Scopes | Why |
|---|---|---|---|
| 11 | `classrooms` my_courses | `usr` + `global` | enrolments bump `usr`; |
|    |                         |                  | the payload also has TEACHER-owned names, topics, assignments |
| 12 | `students` submission detail | `usr` | `raw_input` is a persisted snapshot |
| 13 | `users` profile | `usr` | the user's own row and nothing else |
| 14 | `users` my_settings | `usr` | a Settings save bumps its owner |

Family 12 was first given `global`, and a test disproved it: a teacher
retitling the assignment provably does not change the submission payload
(`SubmissionDetailFreshnessTests.
test_a_teachers_assignment_edit_does_NOT_change_this_payload`).

The `global` on 11 is a deliberate, measured choice rather than
laziness. The precise alternative - bumping every enrolled student when a
course changes - was rejected because `global` moves ~6.5 times/day in
production while these keys' 5-minute TTL expires 288 times/day, so the
extra invalidation is ~2% of misses and does not justify a per-edit fan-out
query. The tests below assert the *consequence* that matters: a
teacher-owned change must reach the student's cached view.

Every freshness test runs with the legacy mechanism disabled, per the
standing rule.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from assignments.models import Assignment, AssignmentStatus
from AutoGrader.cache_generation import SCOPE_USER, get_generation
from AutoGrader.test_cache import real_redis_caches
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from users.models import UserTypes

User = get_user_model()

REDIS_CACHE = real_redis_caches("redis://127.0.0.1:6379/4")

LEGACY_MODULES = (
    "classrooms.signals",
    "users.signals",
    "students.signals",
    "assignments.signals",
)


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
class BespokeBase(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="B1114 School")
        self.teacher = make_user("b1114-t@x.test", UserTypes.TEACHER, self.school)
        self.student = make_user("b1114-s@x.test", UserTypes.STUDENT)
        self.session = Session.objects.create(name="B1114", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Original Course", teacher=self.teacher, session=self.session
        )
        StudentCourse.objects.create(
            student=self.student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        self.student_client = APIClient()
        self.student_client.force_authenticate(self.student)
        self.teacher_client = APIClient()
        self.teacher_client.force_authenticate(self.teacher)
        self.disable_legacy()

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

    def get(self, client, path, **params):
        response = client.get(path, params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_the_legacy_mechanism_really_is_disabled(self):
        cache.set("courses:user_id__sentinel:query__x", "cached", 300)
        self.teacher.first_name = "Trigger"
        self.teacher.save(update_fields=["first_name"])
        self.assertEqual(
            cache.get("courses:user_id__sentinel:query__x"),
            "cached",
            "a legacy sweep still ran - these tests would be masked",
        )


class MyCoursesFreshnessTests(BespokeBase):
    """Family 11 — a student's own course list."""

    @property
    def url(self):
        return reverse("course-my-courses")

    def course_names(self):
        return [row["name"] for row in self.get(self.student_client, self.url)]

    def test_a_new_enrollment_appears(self):
        """The `usr` half: the student's own enrolment."""
        other = Course.objects.create(
            name="Second Course", teacher=self.teacher, session=self.session
        )
        before = self.course_names()

        StudentCourse.objects.create(
            student=self.student,
            course=other,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )

        self.assertNotEqual(before, self.course_names())

    def test_a_TEACHERS_course_rename_reaches_the_student(self):
        """The `global` half, and the reason it is there.

        A teacher's course edit bumps the TEACHER's generation, not the
        student's. Without `global` in this key the student would keep
        reading the old course name for the full TTL.
        """
        self.assertEqual(self.course_names(), ["Original Course"])

        self.course.name = "Renamed By Teacher"
        self.course.save()

        self.assertEqual(
            self.course_names(),
            ["Renamed By Teacher"],
            "a teacher-owned course rename did not reach the student's "
            "cached course list",
        )

    def test_withdrawing_the_student_empties_the_list(self):
        self.assertEqual(len(self.course_names()), 1)

        enrollment = StudentCourse.objects.get(student=self.student, course=self.course)
        enrollment.withdrawn()

        self.assertEqual(self.course_names(), [])

    def test_the_list_is_actually_cached(self):
        """Guard on the guard."""
        self.course_names()
        generation = get_generation(SCOPE_USER, self.student.id)
        self.course_names()
        self.assertEqual(get_generation(SCOPE_USER, self.student.id), generation)

    def test_another_students_list_is_unaffected_by_this_ones_enrollment(self):
        other_student = make_user("b1114-s2@x.test", UserTypes.STUDENT)
        before = get_generation(SCOPE_USER, other_student.id)

        new_course = Course.objects.create(
            name="Third", teacher=self.teacher, session=self.session
        )
        StudentCourse.objects.create(
            student=self.student,
            course=new_course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )

        self.assertEqual(
            get_generation(SCOPE_USER, other_student.id),
            before,
            "an unrelated student's generation moved",
        )


class ProfileAndSettingsFreshnessTests(BespokeBase):
    """Families 13 and 14 — `usr` alone, the cleanest case in the project."""

    PROFILE = "/api/v1/users/me"
    SETTINGS = "/api/v1/users/settings/my_settings"

    def test_a_profile_edit_is_visible_on_the_next_read(self):
        before = self.get(self.student_client, self.PROFILE)

        self.student.first_name = "Renamed"
        self.student.save(update_fields=["first_name"])

        after = self.get(self.student_client, self.PROFILE)
        self.assertNotEqual(before, after)
        self.assertEqual(after["first_name"], "Renamed")

    def test_the_profile_is_actually_cached(self):
        self.get(self.student_client, self.PROFILE)
        generation = get_generation(SCOPE_USER, self.student.id)
        self.get(self.student_client, self.PROFILE)
        self.assertEqual(get_generation(SCOPE_USER, self.student.id), generation)

    def test_another_users_edit_does_not_invalidate_this_profile(self):
        """`usr` alone means precisely this: unrelated activity leaves it
        alone. This is the precision `global` would have destroyed."""
        before = get_generation(SCOPE_USER, self.student.id)

        self.teacher.first_name = "Someone Else"
        self.teacher.save(update_fields=["first_name"])

        self.assertEqual(
            get_generation(SCOPE_USER, self.student.id),
            before,
            "another user's edit bumped this user's profile generation",
        )

    def test_a_settings_change_is_visible_on_the_next_read(self):
        from users.models import Settings

        before = self.get(self.student_client, self.SETTINGS)

        settings_obj, _ = Settings.objects.get_or_create(user=self.student)
        boolean_fields = [
            f.name
            for f in Settings._meta.fields
            if f.get_internal_type() == "BooleanField"
        ]
        self.assertTrue(boolean_fields, "Settings has no boolean field to toggle")
        field = boolean_fields[0]
        setattr(settings_obj, field, not getattr(settings_obj, field))
        settings_obj.save(update_fields=[field])

        self.assertNotEqual(
            before,
            self.get(self.student_client, self.SETTINGS),
            "a settings change did not reach the cached my-settings payload",
        )


class SubmissionDetailFreshnessTests(BespokeBase):
    """Family 12 — one submission's detail view."""

    def setUp(self):
        super().setUp()
        from students.models import StudentSubmission

        # PUBLISHED is required: the student queryset excludes DRAFT and
        # UNPUBLISHED assignments, so an unset status 404s the detail view.
        self.assignment = Assignment.objects.create(
            title="Original Title",
            course=self.course,
            teacher=self.teacher,
            status=AssignmentStatus.PUBLISHED,
        )
        self.submission = StudentSubmission.objects.create(
            student=self.student, assignment=self.assignment, answers={}
        )

    @property
    def url(self):
        return f"/api/v1/submissions/{self.submission.id}"

    def test_a_teachers_assignment_edit_does_NOT_change_this_payload(self):
        """Documents the real contract, which corrected this family's scope.

        `global` was added to this key first, assuming the payload rendered
        the teacher-owned assignment live. This test disproved that and is
        kept as the reason the scope is now `usr` alone:

          * `assignment` is serialised as a bare UUID;
          * `raw_input` is a snapshot materialised ONCE on first GET and
            persisted on the submission row, not re-rendered per request.

        So a retitle genuinely changes nothing here, and adding `global`
        would have invalidated every student's submission detail on every
        unrelated system mutation for no freshness gain.
        """
        before = self.get(self.student_client, self.url)

        self.assignment.title = "Retitled By Teacher"
        self.assignment.save()

        self.assertEqual(
            before,
            self.get(self.student_client, self.url),
            "the payload changed after an assignment retitle - if this is "
            "now live-rendered, family 12 needs a scope covering the "
            "assignment again",
        )

    def test_a_regrade_is_visible_on_the_next_read(self):
        from django.utils import timezone

        before = self.get(self.student_client, self.url)

        self.submission.score = 95
        self.submission.max_points = 100
        self.submission.graded_at = timezone.now()
        self.submission.save()

        self.assertNotEqual(before, self.get(self.student_client, self.url))
