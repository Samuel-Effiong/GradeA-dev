"""
H-153: a teacher renames a student.

Founder's rule (2026-10-06): a student does not name themselves; only the
teacher names a student. Until this row nobody could change a student's
name at all: the account edit refuses it whoever asks (H-148 holds that).

`PATCH /users/<student id>/student-name` with `first_name`, `last_name`
and an optional `middle_name`. User's decisions of 2026-10-07:

  * A teacher may rename a student who is CURRENTLY in a course that
    teacher can reach (enrolled or pending; not withdrawn, not completed).
    Either of two teachers who share a student may; the other sees the new
    name.
  * A school admin may NOT rename a student.
  * A student with no current teacher: a super admin only (undecided
    beyond that).
  * A student already without a name is named this way; there is no step
    where a student names themselves.

Rules added with it (Senior Manager, 2026-10-07): one exact name per
course in EVERY course the student is currently in; the refusal that
quotes the name is only ever sent to a teacher of the course where the
clash is, and a clash elsewhere is refused without quoting it; each
rename is recorded as a log line with ids only. "Every course the student
is in" follows the enrolment's own check, which counts every enrolment row
whatever its status: a withdrawn row is still a row.

Real JWTs, real endpoint.

Run with:
    python manage.py test users.tests_teacher_renames_student
"""

import uuid
from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase
from rest_framework.throttling import SimpleRateThrottle

from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from users.models import CustomUser, UserTypes
from users.tests_patch_password import LOCMEM, WIDE, make
from users.tokens import EpochRefreshToken

NEW = {"first_name": "Ada", "middle_name": "King", "last_name": "Lovelace"}
LOGGER = "users.student_names"
OWN_COURSE_CLASH = "already enrolled in this course"
ELSEWHERE_CLASH = "This name cannot be used for this student."


@override_settings(CACHES=LOCMEM)
class RenameBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        rates = patch.dict(SimpleRateThrottle.THROTTLE_RATES, WIDE)
        rates.start()
        self.addCleanup(rates.stop)

        self.teacher = make("teach.er@gmail.com")
        self.other_teacher = make("other.teach@gmail.com")
        self.stranger = make("stranger.teach@gmail.com")
        self.course = self.course_of(self.teacher, "Algebra")
        self.other_course = self.course_of(self.other_teacher, "Biology")
        self.student = self.student_named("stu.dent@gmail.com", "Augusta", "", "Byron")
        self.enrol(self.student, self.course)
        self.superadmin = make(
            "root@example.com",
            UserTypes.SUPER_ADMIN,
            is_superuser=True,
            is_staff=True,
        )

    def course_of(self, teacher, name):
        session = Session.objects.create(name=f"S {name}", teacher=teacher)
        return Course.objects.create(name=name, teacher=teacher, session=session)

    def student_named(self, email, first, middle, last):
        student = make(email, UserTypes.STUDENT)
        CustomUser.objects.filter(pk=student.pk).update(
            first_name=first, middle_name=middle, last_name=last
        )
        return student

    def enrol(self, student, course, status_=EnrollmentStatusType.ENROLLED):
        return StudentCourse.objects.create(
            student=student, course=course, enrollment_status=status_
        )

    def rename(self, actor, target, **body):
        client = APIClient()
        token = EpochRefreshToken.for_user(actor).access_token
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return client.patch(
            reverse("user-student-name", kwargs={"pk": target.pk}),
            body or NEW,
            format="json",
        )

    def stored(self, user):
        row = CustomUser.objects.get(pk=user.pk)
        return (row.first_name, row.middle_name, row.last_name)

    def assert_renamed(self, response, user=None):
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(self.stored(user or self.student), ("Ada", "King", "Lovelace"))

    def assert_refused(self, response, code, unchanged=("Augusta", "", "Byron")):
        self.assertEqual(response.status_code, code, response.content)
        self.assertEqual(self.stored(self.student), unchanged)


class ATeacherRenamesTheirStudentTest(RenameBase):
    def test_the_name_is_changed_and_the_answer_carries_it(self):
        response = self.rename(self.teacher, self.student)

        self.assert_renamed(response)
        self.assertEqual(
            {key: response.json()["data"][key] for key in NEW},
            NEW,
        )

    def test_the_name_is_trimmed_and_the_middle_name_is_optional(self):
        response = self.rename(
            self.teacher, self.student, first_name="  Ada ", last_name=" Lovelace  "
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(self.stored(self.student), ("Ada", "", "Lovelace"))

    def test_a_student_with_no_name_is_named_this_way(self):
        CustomUser.objects.filter(pk=self.student.pk).update(
            first_name="", middle_name="", last_name=""
        )
        self.assert_renamed(self.rename(self.teacher, self.student))

    def test_a_pending_student_can_be_named_before_they_first_sign_in(self):
        pending = self.student_named("pend.ing@gmail.com", "", "", "")
        self.enrol(pending, self.course, EnrollmentStatusType.PENDING)
        self.assert_renamed(self.rename(self.teacher, pending), pending)

    def test_either_of_two_teachers_may_and_the_other_sees_the_new_name(self):
        self.enrol(self.student, self.other_course)

        self.assert_renamed(self.rename(self.other_teacher, self.student))

        client = APIClient()
        token = EpochRefreshToken.for_user(self.teacher).access_token
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        cache.clear()
        seen = client.get(reverse("user-detail", kwargs={"pk": self.student.pk}))
        self.assertEqual(seen.status_code, status.HTTP_200_OK, seen.content)
        self.assertEqual(seen.json()["data"]["first_name"], "Ada")

    def test_each_name_must_have_two_letters_and_cannot_be_blank(self):
        for body in (
            {"first_name": "A", "last_name": "Lovelace"},
            {"first_name": "Ada", "last_name": "L"},
            {"first_name": "   ", "last_name": "Lovelace"},
            {"last_name": "Lovelace"},
            {"first_name": "Ada"},
        ):
            with self.subTest(body=body):
                self.assert_refused(
                    self.rename(self.teacher, self.student, **body),
                    status.HTTP_400_BAD_REQUEST,
                )

    def test_nothing_but_the_name_can_be_changed_here(self):
        response = self.rename(
            self.teacher,
            self.student,
            **NEW,
            email="taken.over@gmail.com",
            user_type="TEACHER",
            is_active=False,
            bio="set by the teacher",
        )
        self.assert_renamed(response)
        row = CustomUser.objects.get(pk=self.student.pk)
        self.assertEqual(row.email, "stu.dent@gmail.com")
        self.assertEqual(row.user_type, UserTypes.STUDENT)
        self.assertTrue(row.is_active)
        self.assertNotEqual(row.bio, "set by the teacher")


class WhoMayNotRenameTest(RenameBase):
    def test_a_school_admin_may_not(self):
        school = School.objects.create(name="Riverside")
        CustomUser.objects.filter(pk=self.teacher.pk).update(school=school)
        admin = make("admin@riverside.edu", UserTypes.SCHOOL_ADMIN, school=school)

        self.assert_refused(self.rename(admin, self.student), status.HTTP_403_FORBIDDEN)

    def test_the_student_may_not(self):
        self.assert_refused(
            self.rename(self.student, self.student), status.HTTP_403_FORBIDDEN
        )

    def test_a_classmate_may_not(self):
        mate = self.student_named("class.mate@gmail.com", "Class", "", "Mate")
        self.enrol(mate, self.course)
        response = self.rename(mate, self.student)
        self.assertIn(
            response.status_code,
            (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND),
            response.content,
        )
        self.assertEqual(self.stored(self.student), ("Augusta", "", "Byron"))

    def test_a_teacher_with_no_course_of_the_student_may_not(self):
        self.assert_refused(
            self.rename(self.stranger, self.student), status.HTTP_404_NOT_FOUND
        )

    def test_an_outsider_cannot_tell_a_student_from_no_account_at_all(self):
        """403 is only ever said about an account the caller can already
        read (their own student, past or present; their school's; their
        own). For everything else, an account that exists and an id that
        does not are answered alike, byte for byte: nobody learns from
        this route that an id is a student of another teacher or school."""
        missing = CustomUser(pk=uuid.uuid4())
        for outsider in (
            self.stranger,
            self.student_named("o@gmail.com", "O", "", "S"),
        ):
            with self.subTest(outsider=outsider.user_type):
                real = self.rename(outsider, self.student)
                none = self.rename(outsider, missing)
                self.assertEqual(real.status_code, status.HTTP_404_NOT_FOUND)
                self.assertEqual(none.status_code, real.status_code)
                self.assertEqual(none.content, real.content)
        self.assertEqual(self.stored(self.student), ("Augusta", "", "Byron"))

    def test_a_teacher_whose_student_has_withdrawn_or_completed_may_not(self):
        for ended in (EnrollmentStatusType.WITHDRAWN, EnrollmentStatusType.COMPLETED):
            with self.subTest(status=ended):
                StudentCourse.objects.filter(student=self.student).update(
                    enrollment_status=ended
                )
                self.assert_refused(
                    self.rename(self.teacher, self.student), status.HTTP_403_FORBIDDEN
                )

    def test_only_a_student_can_be_renamed_here(self):
        response = self.rename(self.superadmin, self.teacher)
        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.content
        )
        self.assertNotEqual(self.stored(self.teacher)[0], "Ada")


class AStudentWithNoCurrentTeacherTest(RenameBase):
    def setUp(self):
        super().setUp()
        StudentCourse.objects.filter(student=self.student).delete()

    def test_their_former_teacher_may_not(self):
        self.assert_refused(
            self.rename(self.teacher, self.student), status.HTTP_404_NOT_FOUND
        )

    def test_a_super_admin_may(self):
        self.assert_renamed(self.rename(self.superadmin, self.student))


class TheNameClashRuleTest(RenameBase):
    def setUp(self):
        super().setUp()
        self.enrol(self.student, self.other_course)

    def test_a_clash_in_the_teachers_own_course_is_refused_and_quoted(self):
        mate = self.student_named("class.mate@gmail.com", "Ada", "King", "Lovelace")
        self.enrol(mate, self.course)

        response = self.rename(self.teacher, self.student)

        self.assert_refused(response, status.HTTP_400_BAD_REQUEST)
        self.assertIn(OWN_COURSE_CLASH, response.content.decode())

    def test_a_clash_in_another_teachers_course_is_refused_without_the_name(self):
        """The student is also in Biology, which this teacher cannot see.
        The rename is refused, but the answer must not confirm that a
        student of that name is in a class the caller does not teach."""
        other = self.student_named("bio.mate@gmail.com", "ada", "king", "LOVELACE")
        self.enrol(other, self.other_course)

        response = self.rename(self.teacher, self.student)

        self.assert_refused(response, status.HTTP_400_BAD_REQUEST)
        text = response.content.decode()
        self.assertIn(ELSEWHERE_CLASH, text)
        self.assertNotIn(OWN_COURSE_CLASH, text)
        self.assertNotIn("exact name", text)
        self.assertNotIn("Biology", text)
        self.assertNotIn("Lovelace", text)

    def test_a_withdrawn_classmate_still_holds_the_name(self):
        """The enrolment's own check (StudentCourse.clean, run on every
        save) counts every row of a course whatever its status. A rename
        that ignored a withdrawn classmate would leave two rows of one
        name in the course, and the next save of either would fail."""
        gone = self.student_named("left.mate@gmail.com", "Ada", "King", "Lovelace")
        self.enrol(gone, self.course, EnrollmentStatusType.WITHDRAWN)

        response = self.rename(self.teacher, self.student)

        self.assert_refused(response, status.HTTP_400_BAD_REQUEST)
        self.assertIn(OWN_COURSE_CLASH, response.content.decode())

    def test_a_course_the_student_has_withdrawn_from_still_counts(self):
        """For the same reason: the student's own withdrawn row is still a
        row of that course. Biology is not the caller's, so no name is
        quoted."""
        StudentCourse.objects.filter(
            student=self.student, course=self.other_course
        ).update(enrollment_status=EnrollmentStatusType.WITHDRAWN)
        other = self.student_named("bio.mate@gmail.com", "Ada", "King", "Lovelace")
        self.enrol(other, self.other_course)

        response = self.rename(self.teacher, self.student)

        self.assert_refused(response, status.HTTP_400_BAD_REQUEST)
        self.assertIn(ELSEWHERE_CLASH, response.content.decode())
        self.assertNotIn("Lovelace", response.content.decode())

    def test_keeping_the_students_own_name_is_no_clash(self):
        response = self.rename(
            self.teacher, self.student, first_name="Augusta", last_name="Byron"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(self.stored(self.student), ("Augusta", "", "Byron"))


class EachRenameIsRecordedTest(RenameBase):
    """On this line there is no audit app: a log line, with ids only."""

    def test_a_rename_writes_one_line_with_ids_and_no_name_or_address(self):
        with self.assertLogs(LOGGER, level="INFO") as logs:
            self.assert_renamed(self.rename(self.teacher, self.student))

        self.assertEqual(len(logs.records), 1)
        line = logs.records[0].getMessage()
        self.assertIn(str(self.teacher.pk), line)
        self.assertIn(str(self.student.pk), line)
        self.assertIn(str(self.course.pk), line)
        for secret in ("Ada", "Lovelace", "Augusta", "Byron", "@"):
            self.assertNotIn(secret, line)

    def test_a_super_admins_rename_is_recorded_with_no_course(self):
        StudentCourse.objects.filter(student=self.student).delete()
        with self.assertLogs(LOGGER, level="INFO") as logs:
            self.assert_renamed(self.rename(self.superadmin, self.student))
        line = logs.records[0].getMessage()
        self.assertIn(str(self.superadmin.pk), line)
        self.assertIn(str(self.student.pk), line)
        self.assertNotIn("@", line)

    def test_a_refused_rename_writes_no_such_line(self):
        with self.assertNoLogs(LOGGER, level="INFO"):
            self.rename(self.student, self.student)
            self.rename(self.stranger, self.student)
