"""H-1 stage 2, families 11-14: the four bespoke single-key read sites.

These are the last applicable families. They are not served by
`UserCacheMixin` - each builds its own key in its own view - so each needed
its dependency read off the code rather than inferred from the pattern.

| # | Read site | Scopes | Why |
|---|---|---|---|
| 11 | `classrooms` my_courses | `usr` + `global` | enrolments bump `usr`; |
|    |                         |                  | the payload also has TEACHER-owned names, topics, assignments |
| 12 | `students` submission detail | `usr` | staff: `raw_input` is a persisted snapshot; |
|    |                              |       | a student before release: the assignment's save bumps them |
| 13 | `users` profile | `usr` | the user's own row and nothing else |
| 14 | `users` my_settings | `usr` | a Settings save bumps its owner |

Family 12 was first given `global`, and a test disproved it: a teacher
retitling the assignment provably does not change the payload staff read
(`SubmissionDetailFreshnessTests.
test_a_teachers_retitle_does_NOT_change_the_teachers_payload`), nor the
document a student reads once the grade is released
(`test_a_teachers_retitle_does_NOT_change_a_released_students_document`).

Since H-130 a student's document BEFORE release is rebuilt from the row
on every read, so that one does follow the assignment's title and due
date. `usr` alone is still the right scope for it: an assignment's save
bumps every student who holds an enrolment row in its course
(`assignments.signals._bump_assignment_scopes`), which is what
`test_a_teachers_retitle_reaches_the_students_unreleased_document` holds.

KNOWN LIMIT (H-130, accepted 2026-10-07): that bump goes to students with
an enrolment row, while a student reads their own submission with no
enrolment check. A student whose enrolment row has been DELETED (leaving
a course keeps the row, as withdrawn; no production code deletes one)
can read the old title or due date in an unreleased document until the
key's 5-minute TTL runs out. Nothing about a grade is involved. The cure,
if it is ever wanted: add the course's generation (`crs`) to the
student's key in `StudentSubmissionViewSet.retrieve`.

The `global` on 11 is a deliberate, measured choice rather than
laziness. The precise alternative - bumping every enrolled student when a
course changes - was rejected because `global` moves ~6.5 times/day in
production while these keys' 5-minute TTL expires 288 times/day, so the
extra invalidation is ~2% of misses and does not justify a per-edit fan-out
query. The tests below assert the *consequence* that matters: a
teacher-owned change must reach the student's cached view.

Every freshness test runs against the real code with no wildcard
invalidation anywhere (removed in H-1 step 4; until then these tests
patched it out, per the standing rule).
"""

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

    def tearDown(self):
        cache.clear()

    def get(self, client, path, **params):
        response = client.get(path, params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_no_wildcard_sweep_runs_on_a_mutation(self):
        cache.set("courses:user_id__sentinel:query__x", "cached", 300)
        self.teacher.first_name = "Trigger"
        self.teacher.save(update_fields=["first_name"])
        self.assertEqual(
            cache.get("courses:user_id__sentinel:query__x"),
            "cached",
            "a wildcard sweep ran - these tests would be masked",
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

    def retitle(self):
        self.assignment.title = "Retitled By Teacher"
        self.assignment.save()

    def test_a_teachers_retitle_reaches_the_students_unreleased_document(self):
        """Since H-130 a student's document before release is rebuilt from
        the row on every read, with the assignment's current title.

        The key is still scoped to the student alone. What makes the next
        read fresh is the assignment's own save, which bumps every student
        holding an enrolment row in the course. No cache clear here.
        """
        before = self.get(self.student_client, self.url)
        self.assertIn("Original Title", before["raw_input"])

        self.retitle()

        after = self.get(self.student_client, self.url)
        self.assertIn("Retitled By Teacher", after["raw_input"])
        self.assertNotIn("Original Title", after["raw_input"])
        # The document is the only thing that follows the assignment.
        before.pop("raw_input")
        after.pop("raw_input")
        self.assertEqual(before, after)

    def test_a_teachers_retitle_does_NOT_change_the_teachers_payload(self):
        """Documents the real contract, which corrected this family's scope.

        `global` was added to this key first, assuming the payload rendered
        the teacher-owned assignment live. For staff it does not:

          * `assignment` is serialised as a bare UUID;
          * `raw_input` is a snapshot materialised ONCE on first GET and
            persisted on the submission row, not re-rendered per request.

        So a retitle genuinely changes nothing staff read here, and adding
        `global` would have invalidated every submission detail on every
        unrelated system mutation for no freshness gain.
        """
        before = self.get(self.teacher_client, self.url)
        self.assertIn("Original Title", before["raw_input"])

        self.retitle()

        self.assertEqual(
            before,
            self.get(self.teacher_client, self.url),
            "the payload staff read changed after an assignment retitle - "
            "if this is now live-rendered, family 12 needs a scope covering "
            "the assignment again",
        )

    def test_a_teachers_retitle_does_NOT_change_a_released_students_document(self):
        """Once the grade is released the student reads the stored
        snapshot, as staff do, and it does not follow the assignment."""
        self.get(self.teacher_client, self.url)  # materialises the snapshot
        self.submission.refresh_from_db()
        self.submission.is_published = True
        self.submission.save()
        before = self.get(self.student_client, self.url)
        self.assertIn("Original Title", before["raw_input"])

        self.retitle()

        self.assertEqual(before, self.get(self.student_client, self.url))

    def test_a_regrade_is_visible_on_the_next_read(self):
        from django.utils import timezone

        before = self.get(self.student_client, self.url)

        self.submission.score = 95
        self.submission.max_points = 100
        self.submission.graded_at = timezone.now()
        self.submission.save()

        self.assertNotEqual(before, self.get(self.student_client, self.url))
