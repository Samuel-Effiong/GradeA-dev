"""H-298: deleting a teacher whose courses hold assignments must succeed.

Deleting a teacher cascades to their sessions and courses, then to each
course's assignments. Each assignment's `post_delete` runs
`_bump_assignment_scopes`, which used to read `course.teacher`. By then the
teacher row is gone, so the lookup raised `CustomUser.DoesNotExist` (a plain
DoesNotExist, which `getattr(..., None)` does not catch). The whole delete
rolled back.

The fix resolves related rows defensively, as the function's docstring
already promised, and still bumps the teacher's, course's and enrolled
students' caches. The same lookup was in the course cache bump
(classrooms/signals.py); both now use
`AutoGrader.cache_generation.related_or_none`. The submission bump
(students/signals.py) is left as is: submissions are removed while their
course and teacher still exist, so its lookup never fails here (plant P3
survived, which is why it isn't changed).
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from assignments.models import Assignment, AssignmentStatus
from AutoGrader.cache_generation import SCOPE_COURSE, SCOPE_USER
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import StudentSubmission
from users.models import UserTypes

User = get_user_model()


def _user(email, user_type):
    return User.objects.create_user(
        email=email,
        password="password123",  # nosec  # pragma: allowlist secret
        user_type=user_type,
        is_active=True,
        first_name="H298",
        last_name=user_type.title(),
    )


class DeletingATeacherWithAssignmentsTests(TestCase):
    def setUp(self):
        self.teacher = _user("h298-teacher@x.test", UserTypes.TEACHER)
        self.student = _user("h298-student@x.test", UserTypes.STUDENT)
        session = Session.objects.create(name="H298 term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="H298 course", teacher=self.teacher, session=session
        )
        StudentCourse.objects.create(
            student=self.student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        self.assignments = [
            Assignment.objects.create(
                title=f"H298 assignment {n}",
                course=self.course,
                status=status,
                questions=[],
            )
            for n, status in enumerate(
                (AssignmentStatus.DRAFT, AssignmentStatus.PUBLISHED)
            )
        ]
        # A submission too, so the submission cache bump runs in the same
        # cascade.
        self.submission = StudentSubmission.objects.create(
            assignment=self.assignments[1], student=self.student, answers={}
        )

    def test_the_teacher_and_their_courses_and_assignments_are_deleted(self):
        teacher_id = self.teacher.id
        self.teacher.delete()

        self.assertFalse(User.objects.filter(id=teacher_id).exists())
        self.assertFalse(Course.objects.filter(id=self.course.id).exists())
        self.assertFalse(
            Assignment.objects.filter(id__in=[a.id for a in self.assignments]).exists()
        )
        self.assertFalse(
            StudentSubmission.objects.filter(id=self.submission.id).exists()
        )
        self.assertTrue(User.objects.filter(id=self.student.id).exists())

    def test_the_delete_still_refreshes_the_course_and_teacher_caches(self):
        teacher_id = self.teacher.id
        course_id = self.course.id
        bumped = set()

        def record(scopes):
            bumped.update(scopes)

        # Every receiver imports bump_many by name; watch all of them.
        with mock.patch("assignments.signals.bump_many", record), mock.patch(
            "classrooms.signals.bump_many", record
        ), mock.patch("students.signals.bump_many", record):
            self.teacher.delete()

        self.assertIn((SCOPE_COURSE, course_id), bumped)
        self.assertIn((SCOPE_USER, teacher_id), bumped)
        self.assertIn((SCOPE_USER, self.student.id), bumped)
