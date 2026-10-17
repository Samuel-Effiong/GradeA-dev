"""H-247: who a student IS when a teacher adds them.

The owner's rules (2026-10-16), held on all three routes a teacher adds a
student by (single add by email, direct add, class-list import):

  * One email is one student, whichever teacher adds it: never a second
    account. That student has one name: a typed name that is not the
    stored one refuses the add (case and spaces aside), and nothing is
    written, enrolled or sent. The refusal never prints the stored name.
  * With no email (a system-made address), a name is unique within ONE
    course only. The same name in another course is a different child and
    becomes a new student; the same name in this course is that student.
  * School boundaries stay: a school's student can't be added by a teacher
    of another school, nor by an individual teacher who has never taught
    them. A refusal is a refusal, never a second account.
  * Rule (b): a teacher who ALREADY teaches the student, in a course they
    can still reach (H-38), keeps access - the tutor who taught a student
    before a school added them can still add them to a new course.
  * An individual teacher's student may be added by a school teacher: the
    same student, from then on associated with that school.
  * Students already joined across courses are left as they are.

Run with:
    python manage.py test classrooms.tests_h247_student_identity
"""

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    SessionOwnerType,
    StudentCourse,
)
from classrooms.services import (
    CROSS_SCHOOL_REJECTION_MESSAGE,
    NAME_MISMATCH_MESSAGE,
    NOT_A_STUDENT_MESSAGE,
    EnrollmentError,
    already_teaches,
    apply_typed_name,
    schools_associated_with,
)
from users.models import UserTypes

User = get_user_model()

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
JANE = {"first_name": "Jane", "last_name": "Smith"}
JANE_EMAIL = "jane@family.test"


def make_user(email, user_type, school=None, **fields):
    user = User.objects.create_user(email=email, password="h247-pass-Word9")  # nosec
    user.user_type = user_type
    user.is_active = True
    user.school = school
    for name, value in fields.items():
        setattr(user, name, value)
    user.save()
    return user


@override_settings(CACHES=LOCMEM)
class IdentityBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        mail = patch("classrooms.services.notifications.safe_delay")
        self.mail = mail.start()
        self.addCleanup(mail.stop)

        self.school_a = School.objects.create(name="H247 School A")
        self.school_b = School.objects.create(name="H247 School B")
        self.teacher_a = make_user("ta@a.h247.test", UserTypes.TEACHER, self.school_a)
        self.teacher_a2 = make_user("ta2@a.h247.test", UserTypes.TEACHER, self.school_a)
        self.teacher_b = make_user("tb@b.h247.test", UserTypes.TEACHER, self.school_b)
        self.tutor = make_user("tutor@x.h247.test", UserTypes.TEACHER)
        self.tutor2 = make_user("tutor2@x.h247.test", UserTypes.TEACHER)

        self.course_a = self.school_course(self.teacher_a, self.school_a, "A1")
        self.course_a2 = self.school_course(self.teacher_a2, self.school_a, "A2")
        self.course_b = self.school_course(self.teacher_b, self.school_b, "B1")
        self.course_tutor = self.own_course(self.tutor, "Tutoring")
        self.course_tutor2 = self.own_course(self.tutor2, "Tutoring 2")

    # -- fixtures ---------------------------------------------------------

    def school_course(self, teacher, school, name):
        session = Session.objects.get_or_create(
            name=f"Term {school.name}",
            owner_type=SessionOwnerType.SCHOOL,
            school=school,
        )[0]
        return Course.objects.create(name=name, teacher=teacher, session=session)

    def own_course(self, teacher, name):
        session = Session.objects.create(name=f"Own {name}", teacher=teacher)
        return Course.objects.create(name=name, teacher=teacher, session=session)

    def enrol(self, student, course):
        StudentCourse.objects.create(
            student=student,
            course=course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )

    def jane(self, *courses, **names):
        """Jane, who has signed in: an add enrols her, it doesn't re-invite."""
        student = make_user(
            JANE_EMAIL, UserTypes.STUDENT, last_login=timezone.now(), **(names or JANE)
        )
        for course in courses:
            self.enrol(student, course)
        return student

    # -- the three routes -------------------------------------------------

    def single_add(self, teacher, course, email=JANE_EMAIL, **names):
        cache.clear()
        self.client.force_authenticate(teacher)
        return self.client.post(
            reverse("course-students", kwargs={"pk": course.id}),
            {"email": email, **(names or JANE)},
            format="json",
        )

    def direct_add(self, teacher, course, email=JANE_EMAIL, **names):
        cache.clear()
        self.client.force_authenticate(teacher)
        payload = {**(names or JANE)}
        if email:
            payload["email"] = email
        return self.client.post(
            reverse("course-direct-add-student", kwargs={"pk": course.id}),
            payload,
            format="json",
        )

    def import_row(self, teacher, course, raw):
        cache.clear()
        self.client.force_authenticate(teacher)
        response = self.client.post(
            reverse("course-bulk-add-students", kwargs={"pk": course.id}),
            {"raw_data": raw},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        (row,) = response.data["results"]
        return row

    # -- what is checked --------------------------------------------------

    def in_course(self, student, course):
        return StudentCourse.objects.filter(student=student, course=course).exists()

    def janes(self):
        return User.objects.filter(email__iexact=JANE_EMAIL).count()

    def assert_refused(self, response, message):
        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.content
        )
        self.assertIn(message, json.dumps(response.data))

    def assert_untouched(self, student, *, enrolments, name=("Jane", "", "Smith")):
        student.refresh_from_db()
        self.assertEqual(
            (student.first_name, student.middle_name, student.last_name), name
        )
        self.assertEqual(
            set(
                StudentCourse.objects.filter(student=student).values_list(
                    "course_id", flat=True
                )
            ),
            {course.id for course in enrolments},
        )
        self.assertEqual(self.janes(), 1)


class OneEmailIsOneStudentTest(IdentityBase):
    """The same email, from any teacher allowed to add it, is the same
    student: never a second account."""

    def test_two_individual_teachers_share_one_student_by_single_add(self):
        first = self.single_add(self.tutor, self.course_tutor)
        second = self.single_add(self.tutor2, self.course_tutor2)

        self.assertEqual(first.status_code, status.HTTP_200_OK, first.content)
        self.assertEqual(second.status_code, status.HTTP_200_OK, second.content)
        self.assertEqual(self.janes(), 1)
        jane = User.objects.get(email=JANE_EMAIL)
        self.assertTrue(self.in_course(jane, self.course_tutor))
        self.assertTrue(self.in_course(jane, self.course_tutor2))

    def test_the_second_teacher_reaches_the_same_student_by_direct_add(self):
        jane = self.jane(self.course_tutor)

        response = self.direct_add(self.tutor2, self.course_tutor2)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(self.janes(), 1)
        self.assertTrue(self.in_course(jane, self.course_tutor2))

    def test_the_second_teacher_reaches_the_same_student_by_import(self):
        jane = self.jane(self.course_tutor)

        row = self.import_row(
            self.tutor2, self.course_tutor2, f"Jane,Smith,{JANE_EMAIL}"
        )

        self.assertIn(row["status"], ("enrolled", "invited"), row)
        self.assertEqual(self.janes(), 1)
        self.assertTrue(self.in_course(jane, self.course_tutor2))

    def test_an_email_in_other_capitals_is_the_same_student(self):
        jane = self.jane(self.course_tutor)

        response = self.single_add(
            self.tutor2, self.course_tutor2, email="  JANE@Family.TEST "
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(self.janes(), 1)
        self.assertTrue(self.in_course(jane, self.course_tutor2))


class OneStudentHasOneNameTest(IdentityBase):
    """A typed name that is not the stored one refuses the add, on every
    route, and the refusal never shows the stored name."""

    TYPED = {"first_name": "Mary", "last_name": "Jones"}

    def test_single_add_refuses_a_different_name(self):
        jane = self.jane(self.course_tutor)

        response = self.single_add(self.tutor2, self.course_tutor2, **self.TYPED)

        self.assert_refused(response, NAME_MISMATCH_MESSAGE)
        self.assertNotIn("Jane", json.dumps(response.data))
        self.assert_untouched(jane, enrolments=[self.course_tutor])
        self.assertEqual(self.mail.call_args_list, [])

    def test_direct_add_refuses_a_different_name(self):
        jane = self.jane(self.course_tutor)

        response = self.direct_add(self.tutor2, self.course_tutor2, **self.TYPED)

        self.assert_refused(response, NAME_MISMATCH_MESSAGE)
        self.assertNotIn("Jane", json.dumps(response.data))
        self.assert_untouched(jane, enrolments=[self.course_tutor])

    def test_import_fails_the_row_with_its_code(self):
        jane = self.jane(self.course_tutor)

        row = self.import_row(
            self.tutor2, self.course_tutor2, f"Mary,Jones,{JANE_EMAIL}"
        )

        self.assertEqual(row["status"], "failed", row)
        self.assertEqual(row["reason_code"], "ROW_NAME_MISMATCH", row)
        self.assertEqual(
            row["message"],
            "Row 1: this email already belongs to a student with a different name.",
        )
        self.assertNotIn("Jane", json.dumps(row))
        self.assert_untouched(jane, enrolments=[self.course_tutor])
        self.assertEqual(self.mail.call_args_list, [])

    def test_a_different_middle_name_is_a_different_name(self):
        jane = self.jane(self.course_tutor)

        response = self.single_add(
            self.tutor2,
            self.course_tutor2,
            first_name="Jane",
            middle_name="Ann",
            last_name="Smith",
        )

        self.assert_refused(response, NAME_MISMATCH_MESSAGE)
        self.assert_untouched(jane, enrolments=[self.course_tutor])

    def test_capitals_and_spaces_are_the_same_name_on_every_route(self):
        jane = self.jane(self.course_tutor)
        typed = {"first_name": " jane ", "last_name": "SMITH"}

        single = self.single_add(self.tutor2, self.course_tutor2, **typed)
        direct = self.direct_add(
            self.tutor, self.own_course(self.tutor, "Second"), **typed
        )
        row = self.import_row(self.teacher_a, self.course_a, f"JANE,smith,{JANE_EMAIL}")

        self.assertEqual(single.status_code, status.HTTP_200_OK, single.content)
        self.assertEqual(direct.status_code, status.HTTP_200_OK, direct.content)
        self.assertEqual(row["status"], "enrolled", row)
        jane.refresh_from_db()
        self.assertEqual((jane.first_name, jane.last_name), ("Jane", "Smith"))
        self.assertEqual(self.janes(), 1)

    def test_direct_add_names_a_nameless_account(self):
        """H-148's rule, now on the direct add too: an account with no name
        takes the typed one instead of being refused."""
        nameless = make_user(JANE_EMAIL, UserTypes.STUDENT)

        response = self.direct_add(self.tutor, self.course_tutor)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        nameless.refresh_from_db()
        self.assertEqual((nameless.first_name, nameless.last_name), ("Jane", "Smith"))
        self.assertTrue(self.in_course(nameless, self.course_tutor))

    def test_the_school_gate_answers_before_the_name(self):
        """A student the teacher may not add is refused for that reason,
        whatever name is typed: the name check can't be used to learn
        anything about another school's student."""
        jane = self.jane(self.course_a)

        single = self.single_add(self.teacher_b, self.course_b, **self.TYPED)
        row = self.import_row(self.teacher_b, self.course_b, f"Mary,Jones,{JANE_EMAIL}")

        self.assert_refused(single, CROSS_SCHOOL_REJECTION_MESSAGE)
        self.assertNotIn(NAME_MISMATCH_MESSAGE, json.dumps(single.data))
        self.assertEqual(row["reason_code"], "ROW_OTHER_SCHOOL", row)
        self.assert_untouched(jane, enrolments=[self.course_a])

    def test_a_staff_address_is_refused_as_staff_whatever_the_name(self):
        make_user("staff@a.h247.test", UserTypes.TEACHER, self.school_a, **JANE)

        response = self.single_add(
            self.tutor, self.course_tutor, email="staff@a.h247.test", **self.TYPED
        )

        self.assert_refused(response, NOT_A_STUDENT_MESSAGE)
        self.assertNotIn(NAME_MISMATCH_MESSAGE, json.dumps(response.data))


class NoEmailIsUniqueWithinOneCourseTest(IdentityBase):
    """With no email a name is unique within one course only."""

    def test_import_into_another_course_of_the_same_teacher_is_a_new_student(self):
        first = self.import_row(self.teacher_a, self.course_a, "Jane,Smith")
        other = self.school_course(self.teacher_a, self.school_a, "A3")

        second = self.import_row(self.teacher_a, other, "Jane,Smith")

        self.assertEqual(first["status"], "enrolled", first)
        self.assertEqual(second["status"], "enrolled", second)
        in_a = User.objects.get(enrollments__course=self.course_a, first_name="Jane")
        in_other = User.objects.get(enrollments__course=other, first_name="Jane")
        self.assertNotEqual(in_a.pk, in_other.pk)
        self.assertFalse(self.in_course(in_a, other))

    def test_direct_add_into_another_course_is_a_new_student(self):
        first = self.direct_add(self.teacher_a, self.course_a, email=None)
        other = self.school_course(self.teacher_a, self.school_a, "A3")

        second = self.direct_add(self.teacher_a, other, email=None)

        self.assertEqual(first.status_code, status.HTTP_200_OK, first.content)
        self.assertEqual(second.status_code, status.HTTP_200_OK, second.content)
        self.assertEqual(
            User.objects.filter(
                first_name="Jane", last_name="Smith", user_type=UserTypes.STUDENT
            ).count(),
            2,
        )

    def test_the_same_name_in_the_same_course_is_that_student(self):
        self.import_row(self.teacher_a, self.course_a, "Jane,Smith")
        students = User.objects.filter(user_type=UserTypes.STUDENT).count()

        again = self.import_row(self.teacher_a, self.course_a, "jane,SMITH")

        self.assertEqual(again["status"], "skipped", again)
        self.assertEqual(again["reason_code"], "ROW_ALREADY_ENROLLED", again)
        self.assertEqual(
            User.objects.filter(user_type=UserTypes.STUDENT).count(), students
        )

    def test_a_student_already_joined_across_courses_is_left_as_is(self):
        """Owner's decision 2: existing joined-up accounts are not split."""
        joined = make_user(
            "jane.smith.0a1b2c3d4e5f6a7b@student.local", UserTypes.STUDENT, **JANE
        )
        self.enrol(joined, self.course_a)
        self.enrol(joined, self.course_a2)

        self.import_row(
            self.teacher_a,
            self.school_course(self.teacher_a, self.school_a, "A3"),
            "Jane,Smith",
        )

        self.assertTrue(self.in_course(joined, self.course_a))
        self.assertTrue(self.in_course(joined, self.course_a2))


class SchoolBoundariesTest(IdentityBase):
    def test_another_schools_teacher_is_refused(self):
        jane = self.jane(self.course_a)

        response = self.single_add(self.teacher_b, self.course_b)

        self.assert_refused(response, CROSS_SCHOOL_REJECTION_MESSAGE)
        self.assert_untouched(jane, enrolments=[self.course_a])

    def test_an_individual_teacher_who_never_taught_them_is_refused_everywhere(self):
        jane = self.jane(self.course_a)

        single = self.single_add(self.tutor, self.course_tutor)
        direct = self.direct_add(self.tutor, self.course_tutor)
        row = self.import_row(self.tutor, self.course_tutor, f"Jane,Smith,{JANE_EMAIL}")

        self.assert_refused(single, CROSS_SCHOOL_REJECTION_MESSAGE)
        self.assert_refused(direct, CROSS_SCHOOL_REJECTION_MESSAGE)
        self.assertEqual(row["reason_code"], "ROW_OTHER_SCHOOL", row)
        self.assert_untouched(jane, enrolments=[self.course_a])

    def test_a_school_teacher_may_add_an_individual_teachers_student(self):
        jane = self.jane(self.course_tutor)

        response = self.single_add(self.teacher_a, self.course_a)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(self.janes(), 1)
        self.assertTrue(self.in_course(jane, self.course_a))
        self.assertEqual(schools_associated_with(jane), {self.school_a.id})

    def test_the_same_school_shares_its_student(self):
        jane = self.jane(self.course_a)

        response = self.single_add(self.teacher_a2, self.course_a2)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertTrue(self.in_course(jane, self.course_a2))
        self.assertEqual(self.janes(), 1)


class ATeacherWhoAlreadyTeachesKeepsAccessTest(IdentityBase):
    """Rule (b): the tutor taught Jane first; School A then added her. The
    tutor can still add her to a NEW course, on every route. A teacher who
    never taught her still can't."""

    def setUp(self):
        super().setUp()
        self.jane_ = self.jane(self.course_tutor)
        response = self.single_add(self.teacher_a, self.course_a)
        assert response.status_code == status.HTTP_200_OK, response.content
        assert schools_associated_with(self.jane_) == {self.school_a.id}
        self.new_term = self.own_course(self.tutor, "Next term")

    def test_by_single_add(self):
        response = self.single_add(self.tutor, self.new_term)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertTrue(self.in_course(self.jane_, self.new_term))
        self.assertEqual(self.janes(), 1)

    def test_by_direct_add(self):
        response = self.direct_add(self.tutor, self.new_term)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertTrue(self.in_course(self.jane_, self.new_term))

    def test_by_import(self):
        row = self.import_row(self.tutor, self.new_term, f"Jane,Smith,{JANE_EMAIL}")

        self.assertEqual(row["status"], "enrolled", row)
        self.assertTrue(self.in_course(self.jane_, self.new_term))

    def test_a_teacher_who_never_taught_her_is_still_refused(self):
        single = self.single_add(self.tutor2, self.course_tutor2)
        other_school = self.single_add(self.teacher_b, self.course_b)

        self.assert_refused(single, CROSS_SCHOOL_REJECTION_MESSAGE)
        self.assert_refused(other_school, CROSS_SCHOOL_REJECTION_MESSAGE)
        self.assertFalse(self.in_course(self.jane_, self.course_tutor2))
        self.assertFalse(self.in_course(self.jane_, self.course_b))

    def test_keeping_access_does_not_let_the_name_change(self):
        response = self.single_add(
            self.tutor, self.new_term, first_name="Janet", last_name="Smyth"
        )

        self.assert_refused(response, NAME_MISMATCH_MESSAGE)
        self.assertFalse(self.in_course(self.jane_, self.new_term))


class AlreadyTeachesUnitTest(IdentityBase):
    """`already_teaches` counts only courses the teacher can still reach
    (H-38): a course in a school's session stops counting once the teacher
    is no longer in that school."""

    def test_a_reachable_course_counts(self):
        jane = self.jane(self.course_tutor)
        self.assertTrue(already_teaches(self.tutor, jane))
        self.assertFalse(already_teaches(self.tutor2, jane))

    def test_a_withdrawn_enrolment_still_counts(self):
        jane = self.jane()
        StudentCourse.objects.create(
            student=jane,
            course=self.course_tutor,
            enrollment_status=EnrollmentStatusType.WITHDRAWN,
        )
        self.assertTrue(already_teaches(self.tutor, jane))

    def test_a_school_course_the_teacher_has_left_does_not_count(self):
        # In a second School A course too, so she stays School A's once
        # teacher_a leaves (ownership is derived from enrolments).
        jane = self.jane(self.course_a, self.course_a2)
        self.assertTrue(already_teaches(self.teacher_a, jane))

        self.teacher_a.school = None
        self.teacher_a.save(update_fields=["school"])

        self.assertFalse(already_teaches(self.teacher_a, jane))
        own = self.own_course(self.teacher_a, "After leaving")
        response = self.single_add(self.teacher_a, own)
        self.assert_refused(response, CROSS_SCHOOL_REJECTION_MESSAGE)


class ApplyTypedNameUnitTest(TestCase):
    def student(self, **names):
        return make_user("unit@h247.test", UserTypes.STUDENT, **names)

    def test_nothing_typed_checks_nothing(self):
        jane = self.student(**JANE)
        self.assertEqual(apply_typed_name(jane, first_name="", last_name=""), [])

    def test_a_nameless_account_takes_the_typed_name(self):
        nameless = self.student()
        fields = apply_typed_name(
            nameless, first_name="Jane", middle_name="A", last_name="Smith"
        )
        self.assertEqual(fields, ["first_name", "middle_name", "last_name"])
        self.assertEqual(
            (nameless.first_name, nameless.middle_name, nameless.last_name),
            ("Jane", "A", "Smith"),
        )

    def test_without_fill_a_nameless_account_is_left_alone(self):
        nameless = self.student()
        self.assertEqual(
            apply_typed_name(
                nameless, first_name="Jane", last_name="Smith", fill_empty=False
            ),
            [],
        )
        self.assertEqual(nameless.first_name, "")

    def test_inner_spaces_capitals_and_ends_do_not_count(self):
        mary = self.student(first_name="Mary Ann", last_name="Smith")
        self.assertEqual(
            apply_typed_name(mary, first_name=" mary   ANN ", last_name="smith "), []
        )

    def test_any_other_difference_refuses(self):
        mary = self.student(first_name="Mary Ann", last_name="Smith")
        for typed in (
            {"first_name": "Mary", "last_name": "Smith"},
            {"first_name": "Mary Ann", "last_name": "Smyth"},
            {"first_name": "Mary Ann", "middle_name": "B", "last_name": "Smith"},
        ):
            with self.subTest(typed=typed), self.assertRaises(
                EnrollmentError
            ) as caught:
                apply_typed_name(mary, **typed)
            self.assertEqual(str(caught.exception), NAME_MISMATCH_MESSAGE)
        self.assertEqual((mary.first_name, mary.last_name), ("Mary Ann", "Smith"))
