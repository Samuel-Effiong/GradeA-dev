"""Regression tests for the section-3 (classrooms) audit.

Each test here pins a defect that was found and fixed during the audit, and
each was confirmed to fail before its fix: reverting the fix turns the test
red. They cover the tenancy boundary between schools/teachers/students and
the bulk roster paths, which the existing suite exercised only for the
single-record happy path.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
    Topic,
)
from students.models import StudentSubmission
from users.models import UserTypes

User = get_user_model()


def make_user(email, user_type, school=None, **fields):
    user = User.objects.create_user(email=email, password="password123")  # nosec
    user.user_type = user_type
    user.is_active = True
    user.school = school
    for name, value in fields.items():
        setattr(user, name, value)
    user.save()
    return user


class ClassroomTenancyBase(APITestCase):
    def setUp(self):
        self.school_a = School.objects.create(name="School A")
        self.school_b = School.objects.create(name="School B")

        self.teacher_a = make_user("a@school-a.test", UserTypes.TEACHER, self.school_a)
        self.teacher_b = make_user("b@school-b.test", UserTypes.TEACHER, self.school_b)

        self.session_a = Session.objects.create(name="Fall A", teacher=self.teacher_a)
        self.session_b = Session.objects.create(name="Fall B", teacher=self.teacher_b)

        self.course_a = Course.objects.create(
            name="Course A", teacher=self.teacher_a, session=self.session_a
        )
        self.course_b = Course.objects.create(
            name="Course B", teacher=self.teacher_b, session=self.session_b
        )

    def make_student(self, email, **fields):
        return make_user(email, UserTypes.STUDENT, **fields)


class RemoveStudentTest(ClassroomTenancyBase):
    """`remove_student` removes the enrollment - and nothing else.

    It used to call student.delete() as well, so one teacher unenrolling a
    student destroyed that student's account and, by CASCADE, every record
    they had under every other teacher and school.
    """

    def setUp(self):
        super().setUp()
        self.student = self.make_student("shared@student.test")
        StudentCourse.objects.create(student=self.student, course=self.course_a)
        self.other_enrollment = StudentCourse.objects.create(
            student=self.student, course=self.course_b
        )
        self.url = reverse(
            "course-remove-student",
            kwargs={"pk": self.course_a.id, "student_id": self.student.id},
        )

    def test_removes_only_the_enrollment_in_that_course(self):
        self.client.force_authenticate(self.teacher_a)

        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            StudentCourse.objects.filter(
                student=self.student, course=self.course_a
            ).exists()
        )

    def test_the_student_account_survives(self):
        self.client.force_authenticate(self.teacher_a)

        self.client.delete(self.url)

        self.assertTrue(User.objects.filter(pk=self.student.pk).exists())

    def test_another_schools_enrollment_is_untouched(self):
        self.client.force_authenticate(self.teacher_a)

        self.client.delete(self.url)

        self.assertTrue(
            StudentCourse.objects.filter(pk=self.other_enrollment.pk).exists()
        )

    def test_another_teachers_grades_survive(self):
        assignment = Assignment.objects.create(
            title="B1", course=self.course_b, teacher=self.teacher_b
        )
        submission = StudentSubmission.objects.create(
            student=self.student, assignment=assignment, answers={}
        )
        self.client.force_authenticate(self.teacher_a)

        self.client.delete(self.url)

        self.assertTrue(StudentSubmission.objects.filter(pk=submission.pk).exists())


class TopicPermissionTest(ClassroomTenancyBase):
    """TopicViewSet's permission classes and course scoping.

    The viewset spelled the attribute `permission_class`, which DRF ignores,
    so it silently fell back to the project default of IsAuthenticated alone.
    `course` is also a plain writable PK field, so even a real teacher could
    point a new topic at somebody else's course by UUID.
    """

    def test_a_student_cannot_create_a_topic(self):
        student = self.make_student("s@student.test")
        StudentCourse.objects.create(student=student, course=self.course_a)
        self.client.force_authenticate(student)

        response = self.client.post(
            reverse("topic-list"), {"name": "T", "course": str(self.course_a.id)}
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Topic.objects.exists())

    def test_a_teacher_cannot_create_a_topic_on_another_teachers_course(self):
        self.client.force_authenticate(self.teacher_b)

        response = self.client.post(
            reverse("topic-list"), {"name": "T", "course": str(self.course_a.id)}
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Topic.objects.exists())

    def test_the_owning_teacher_can_still_create_a_topic(self):
        self.client.force_authenticate(self.teacher_a)

        response = self.client.post(
            reverse("topic-list"), {"name": "Algebra", "course": str(self.course_a.id)}
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Topic.objects.get().course_id, self.course_a.id)

    def test_a_duplicate_topic_is_a_400_not_a_500(self):
        Topic.objects.create(name="Algebra", course=self.course_a)
        self.client.force_authenticate(self.teacher_a)

        response = self.client.post(
            reverse("topic-list"), {"name": "Algebra", "course": str(self.course_a.id)}
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_blank_topic_name_is_rejected(self):
        self.client.force_authenticate(self.teacher_a)

        response = self.client.post(
            reverse("topic-list"), {"name": "   ", "course": str(self.course_a.id)}
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_options_preflight_is_allowed(self):
        """http_method_names said "option", so DRF 405'd every OPTIONS
        request and CORS preflight to this endpoint failed."""
        self.client.force_authenticate(self.teacher_a)

        response = self.client.options(reverse("topic-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)


class EnrollmentReassignmentTest(ClassroomTenancyBase):
    """A PATCH may not re-point an enrollment at another course or student."""

    def setUp(self):
        super().setUp()
        self.student = self.make_student("e@student.test")
        self.enrollment = StudentCourse.objects.create(
            student=self.student, course=self.course_a
        )
        self.url = reverse("student-course-detail", kwargs={"pk": self.enrollment.id})

    def test_course_cannot_be_reassigned(self):
        self.client.force_authenticate(self.teacher_a)

        self.client.patch(self.url, {"course": str(self.course_b.id)}, format="json")

        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.course_id, self.course_a.id)

    def test_student_cannot_be_reassigned(self):
        victim = self.make_student("victim@student.test")
        self.client.force_authenticate(self.teacher_a)

        self.client.patch(self.url, {"student": str(victim.id)}, format="json")

        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.student_id, self.student.id)

    def test_enrollment_status_is_still_editable(self):
        self.client.force_authenticate(self.teacher_a)

        response = self.client.patch(
            self.url,
            {"enrollment_status": EnrollmentStatusType.COMPLETED},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.enrollment.refresh_from_db()
        self.assertEqual(
            self.enrollment.enrollment_status, EnrollmentStatusType.COMPLETED
        )


class BulkEnrollmentTenancyTest(ClassroomTenancyBase):
    """The no-email bulk path matches an existing student by name.

    That lookup used to run against every user in the system, so a roster row
    reading "John Smith" pulled a different school's John Smith into this
    teacher's course.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse("course-bulk-add-students", kwargs={"pk": self.course_a.pk})
        self.client.force_authenticate(self.teacher_a)

    def test_a_name_match_in_another_school_is_not_reused(self):
        stranger = self.make_student(
            "john@school-b.test",
            school=self.school_b,
            first_name="John",
            last_name="Smith",
        )
        StudentCourse.objects.create(student=stranger, course=self.course_b)

        self.client.post(self.url, {"raw_data": "John,Smith"})

        self.assertFalse(
            StudentCourse.objects.filter(
                student=stranger, course=self.course_a
            ).exists()
        )

    def test_a_name_match_creates_a_fresh_local_student_instead(self):
        stranger = self.make_student(
            "john2@school-b.test",
            school=self.school_b,
            first_name="John",
            last_name="Smith",
        )
        StudentCourse.objects.create(student=stranger, course=self.course_b)

        response = self.client.post(self.url, {"raw_data": "John,Smith"})

        self.assertEqual(response.data["success_count"], 1)
        self.assertEqual(
            User.objects.filter(first_name="John", last_name="Smith").count(), 2
        )

    def test_the_teachers_own_existing_student_is_still_reused(self):
        mine = self.make_student(
            "mine@school-a.test",
            school=self.school_a,
            first_name="Jane",
            last_name="Doe",
        )
        other_course = Course.objects.create(
            name="Course A2", teacher=self.teacher_a, session=self.session_a
        )
        StudentCourse.objects.create(student=mine, course=other_course)

        self.client.post(self.url, {"raw_data": "Jane,Doe"})

        self.assertTrue(
            StudentCourse.objects.filter(student=mine, course=self.course_a).exists()
        )
        self.assertEqual(
            User.objects.filter(first_name="Jane", last_name="Doe").count(), 1
        )


class BulkEnrollmentInputLimitsTest(ClassroomTenancyBase):
    """Bulk enrolment runs synchronously, so its input needs a ceiling."""

    def setUp(self):
        super().setUp()
        self.url = reverse("course-bulk-add-students", kwargs={"pk": self.course_a.pk})
        self.client.force_authenticate(self.teacher_a)

    def test_an_oversized_row_count_is_rejected(self):
        from classrooms.services import MAX_ROWS

        rows = "\n".join(f"First{i},Last{i}" for i in range(MAX_ROWS + 1))

        response = self.client.post(self.url, {"raw_data": rows})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(StudentCourse.objects.filter(course=self.course_a).exists())

    def test_an_oversized_file_is_rejected_without_being_read(self):
        import io

        from classrooms.services import MAX_FILE_BYTES

        payload = io.BytesIO(b"a,b\n" * (MAX_FILE_BYTES // 4 + 1))
        payload.name = "roster.csv"

        response = self.client.post(self.url, {"file": payload}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_non_utf8_file_is_a_400_not_a_500(self):
        import io

        payload = io.BytesIO("first,last\nJosé,Núñez\n".encode("latin-1"))
        payload.name = "roster.csv"

        response = self.client.post(self.url, {"file": payload}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class DirectAddStudentPasswordTest(ClassroomTenancyBase):
    """Directly-added roster entries get no password at all.

    They used to all share the literal "student123!" on an active account
    with a guessable address, so knowing the literal was enough to sign in
    as any of them.
    """

    def test_the_account_has_no_usable_password(self):
        self.client.force_authenticate(self.teacher_a)
        url = reverse("course-direct-add-student", kwargs={"pk": self.course_a.pk})

        response = self.client.post(url, {"first_name": "Amy", "last_name": "Pond"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        student = User.objects.get(first_name="Amy")
        self.assertFalse(student.has_usable_password())
        self.assertFalse(student.check_password("student123!"))  # nosec

    def test_the_student_is_still_enrolled_and_active(self):
        self.client.force_authenticate(self.teacher_a)
        url = reverse("course-direct-add-student", kwargs={"pk": self.course_a.pk})

        self.client.post(url, {"first_name": "Rory", "last_name": "Williams"})

        student = User.objects.get(first_name="Rory")
        self.assertTrue(student.is_active)
        self.assertEqual(
            StudentCourse.objects.get(student=student).enrollment_status,
            EnrollmentStatusType.ENROLLED,
        )


class WithdrawnStudentVisibilityTest(ClassroomTenancyBase):
    def test_a_withdrawn_student_no_longer_sees_the_course(self):
        student = self.make_student("w@student.test")
        enrollment = StudentCourse.objects.create(student=student, course=self.course_a)
        enrollment.withdrawn()
        self.client.force_authenticate(student)

        response = self.client.get(reverse("course-list"))

        self.assertEqual(response.data["count"], 0)

    def test_an_enrolled_student_still_sees_the_course(self):
        student = self.make_student("k@student.test")
        StudentCourse.objects.create(
            student=student,
            course=self.course_a,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        self.client.force_authenticate(student)

        response = self.client.get(reverse("course-list"))

        self.assertEqual(response.data["count"], 1)


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class StudentCourseListQueryBudgetTest(ClassroomTenancyBase):
    """The enrollment list must not scale its query count with page size.

    StudentCourseSerializer's three assignment counters each called .count()
    or .filter(), both of which bypass the view's prefetch cache, so every
    extra row on the page cost four more round trips.
    """

    def _seed(self, student_count):
        for index in range(3):
            Assignment.objects.create(
                title=f"A{index}", course=self.course_a, teacher=self.teacher_a
            )
        for index in range(student_count):
            student = self.make_student(
                f"q{index}@student.test", first_name=f"S{index}", last_name="T"
            )
            StudentCourse.objects.create(student=student, course=self.course_a)

    def _measure(self, student_count):
        self._seed(student_count)
        # UserCacheMixin caches the list per (user, query params), so a
        # second measurement would otherwise replay the first response.
        cache.clear()
        self.client.force_authenticate(self.teacher_a)
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(reverse("student-course-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), student_count)
        return len(captured)

    def test_query_count_is_flat_across_page_sizes(self):
        one_row = self._measure(1)

        StudentCourse.objects.all().delete()
        User.objects.filter(user_type=UserTypes.STUDENT).delete()
        Assignment.objects.all().delete()

        ten_rows = self._measure(10)

        self.assertEqual(
            ten_rows,
            one_row,
            f"query count grew from {one_row} to {ten_rows} between a 1-row "
            "and a 10-row page - the serializer is issuing per-row queries",
        )

    def test_the_counts_are_still_correct(self):
        self._seed(1)
        student = User.objects.get(first_name="S0")
        assignment = Assignment.objects.filter(course=self.course_a).first()
        StudentSubmission.objects.create(
            student=student, assignment=assignment, answers={}
        )

        self.client.force_authenticate(self.teacher_a)
        row = self.client.get(reverse("student-course-list")).data["results"][0]

        self.assertEqual(row["total_no_of_assignment"], 3)
        self.assertEqual(row["total_assignment_submitted"], 1)
        self.assertAlmostEqual(row["submitted_assignment_percentage"], 100 / 3)


class CreateTopicsActionTest(ClassroomTenancyBase):
    """The /course/<id>/topics bulk action still works after TopicSerializer
    gained a course check - it now passes the view's serializer context so
    validate_course can see who is asking."""

    def test_the_owning_teacher_can_create_topics_in_bulk(self):
        self.client.force_authenticate(self.teacher_a)
        url = reverse("course-create-topics", kwargs={"pk": self.course_a.pk})

        response = self.client.post(url, ["Algebra", "Geometry"], format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            sorted(Topic.objects.values_list("name", flat=True)),
            ["Algebra", "Geometry"],
        )

    def test_another_teachers_course_is_a_404(self):
        self.client.force_authenticate(self.teacher_b)
        url = reverse("course-create-topics", kwargs={"pk": self.course_a.pk})

        response = self.client.post(url, ["Algebra"], format="json")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(Topic.objects.exists())
