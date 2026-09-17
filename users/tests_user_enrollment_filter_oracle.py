"""`/users/<id>` enrollment filters must not reveal other tenants' enrollments.

DRF's `get_object()` runs `filter_queryset()`, so the `enrollments__*`
filters on `CustomUserViewSet` apply to retrieve, PATCH and DELETE - not just
the superadmin-only list. Declared as plain `filterset_fields`, each filter
joined EVERY enrollment the target student had, so a teacher (or a school
admin) who can see a shared student could ask yes/no questions about that
student's enrollments with OTHER teachers:

  * `GET /users/<S>?enrollments__course=<B's course>` -> 200 if S is in it,
    while an unknown course id gave 400 and a course S is not in gave 404;
  * `?enrollments__course__session=<B's session>` -> the same, per session;
  * `?enrollments__enrollment_status=WITHDRAWN` (or `__in=`) -> 200 if S
    withdrew from ANY course anywhere - no foreign id needed;
  * `PATCH /users/<S>?enrollments__course=<B's course>` -> 403 vs 404, which
    also sidesteps the retrieve cache.

The filters now only look through the requester's own enrollments, so a
foreign value is indistinguishable from a value that matches nothing.

Fixtures go through the production paths: `enroll_student_by_email` for
enrollment, and teacher B's own PATCH on the enrollment for withdrawal.
"""

import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from classrooms.services import enroll_student_by_email
from users.models import UserTypes

User = get_user_model()

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
UNKNOWN = "00000000-0000-4000-8000-000000000000"


def make_user(email, user_type, school=None, first_name="", last_name=""):
    user = User.objects.create_user(email=email, password="password123")  # nosec
    user.user_type = user_type
    user.is_active = True
    user.school = school
    user.first_name, user.last_name = first_name, last_name
    user.save()
    return user


def detail(user):
    return reverse("user-detail", kwargs={"pk": user.pk})


@override_settings(CACHES=LOCMEM)
class UserEnrollmentFilterOracleBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="Oracle School")
        # A and C teach at the same school; B is an unrelated individual.
        self.teacher_a = make_user(
            "oa@school.test", UserTypes.TEACHER, self.school, "Ada", "Alpha"
        )
        self.teacher_c = make_user(
            "oc@school.test", UserTypes.TEACHER, self.school, "Cal", "Chem"
        )
        self.teacher_b = make_user(
            "ob@indiv.test", UserTypes.TEACHER, None, "Bea", "Bravo"
        )
        self.admin = make_user("admin@school.test", UserTypes.SCHOOL_ADMIN, self.school)
        self.superadmin = make_user("root@x.test", UserTypes.SUPER_ADMIN)
        self.superadmin.is_superuser = self.superadmin.is_staff = True
        self.superadmin.save()

        self.course_a = self.make_course(self.teacher_a, "Algebra A")
        self.course_b = self.make_course(self.teacher_b, "Private Tutoring B")
        self.course_c = self.make_course(self.teacher_c, "Chemistry C")

        # School-less student shared by B (individual) and A (school). The
        # order is the one production allows: once a school course holds the
        # account, the cross-school rule refuses an individual teacher's add.
        self.student = make_user("shared@x.test", UserTypes.STUDENT, None, "Sam", "S")
        enroll_student_by_email(course=self.course_b, email=self.student.email)
        enroll_student_by_email(course=self.course_a, email=self.student.email)
        # Same-school pair: the service creates this account for C, then A
        # adds the same address.
        self.pupil, _ = enroll_student_by_email(
            course=self.course_c, email="pupil@school.test"
        )
        enroll_student_by_email(course=self.course_a, email="pupil@school.test")

    def make_course(self, teacher, name):
        session = Session.objects.create(name=f"{name} term", teacher=teacher)
        return Course.objects.create(
            name=name, description=f"{name} notes", teacher=teacher, session=session
        )

    def withdraw_as_owner(self, student, course):
        """Withdraw the way production does: the course's teacher PATCHes
        the enrollment."""
        enrollment = StudentCourse.objects.get(student=student, course=course)
        self.client.force_authenticate(course.teacher)
        response = self.client.patch(
            reverse("student-course-detail", kwargs={"pk": enrollment.pk}),
            {"enrollment_status": EnrollmentStatusType.WITHDRAWN},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.enrollment_status, EnrollmentStatusType.WITHDRAWN)

    def call(self, actor, method, target, params):
        cache.clear()  # cold cache: the uncached path is the one that answers
        self.client.force_authenticate(actor)
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{detail(target)}?{query}"
        if method == "get":
            return self.client.get(url)
        return self.client.patch(url, {"first_name": "Hacked"}, format="json")

    def assert_indistinguishable(self, actor, method, target, param, foreign, blank):
        """The foreign value must answer exactly like a value that matches
        nothing at all."""
        leaked = self.call(actor, method, target, {param: foreign})
        control = self.call(actor, method, target, {param: blank})
        self.assertEqual(
            (leaked.status_code, leaked.data),
            (control.status_code, control.data),
            f"{method.upper()} {param}={foreign} is distinguishable from {blank}",
        )
        return leaked


class TeacherCannotProbeAnotherTeachersEnrollments(UserEnrollmentFilterOracleBase):
    def test_course_filter_on_retrieve(self):
        response = self.assert_indistinguishable(
            self.teacher_a,
            "get",
            self.student,
            "enrollments__course",
            self.course_b.id,
            UNKNOWN,
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_session_filter_on_retrieve(self):
        self.assert_indistinguishable(
            self.teacher_a,
            "get",
            self.student,
            "enrollments__course__session",
            self.course_b.session_id,
            UNKNOWN,
        )

    def test_course_filter_on_patch(self):
        self.assert_indistinguishable(
            self.teacher_a,
            "patch",
            self.student,
            "enrollments__course",
            self.course_b.id,
            UNKNOWN,
        )
        self.student.refresh_from_db()
        self.assertEqual(self.student.first_name, "Sam")

    def test_withdrawn_status_elsewhere_is_invisible(self):
        self.withdraw_as_owner(self.student, self.course_b)
        # COMPLETED matches nothing anywhere: the control.
        for param, foreign, blank in (
            ("enrollments__enrollment_status", "WITHDRAWN", "COMPLETED"),
            ("enrollments__enrollment_status__in", "WITHDRAWN", "COMPLETED"),
        ):
            for method in ("get", "patch"):
                self.assert_indistinguishable(
                    self.teacher_a, method, self.student, param, foreign, blank
                )

    def test_same_school_colleague_course_is_invisible(self):
        self.assert_indistinguishable(
            self.teacher_a,
            "get",
            self.pupil,
            "enrollments__course",
            self.course_c.id,
            UNKNOWN,
        )

    def test_payload_carries_nothing_of_teacher_b(self):
        response = self.call(self.teacher_a, "get", self.student, {})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = json.dumps(response.data, default=str)
        for secret in ("Private Tutoring B", str(self.course_b.id), "Bea", "Bravo"):
            self.assertNotIn(secret, body)


class TeacherOwnFiltersStillWork(UserEnrollmentFilterOracleBase):
    def test_own_course_filter_finds_the_student(self):
        response = self.call(
            self.teacher_a,
            "get",
            self.student,
            {"enrollments__course": self.course_a.id},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_own_session_filter_finds_the_student(self):
        response = self.call(
            self.teacher_a,
            "get",
            self.student,
            {"enrollments__course__session": self.course_a.session_id},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_own_status_filter_finds_the_student(self):
        for param in (
            "enrollments__enrollment_status",
            "enrollments__enrollment_status__in",
        ):
            response = self.call(
                self.teacher_a, "get", self.student, {param: "ENROLLED"}
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, param)

    def test_own_withdrawal_is_visible_to_its_teacher(self):
        self.withdraw_as_owner(self.student, self.course_b)
        response = self.call(
            self.teacher_b,
            "get",
            self.student,
            {"enrollments__enrollment_status": "WITHDRAWN"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_isnull_false_matches_through_own_enrollment(self):
        response = self.call(
            self.teacher_a,
            "get",
            self.student,
            {"enrollments__course__isnull": "false"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_malformed_values_are_still_rejected(self):
        for param, value in (
            ("enrollments__course", "not-a-uuid"),
            ("enrollments__enrollment_status", "NOT_A_STATUS"),
        ):
            response = self.call(self.teacher_a, "get", self.student, {param: value})
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, param)


class SchoolAdminCannotProbeOutsideTheSchool(UserEnrollmentFilterOracleBase):
    def test_outside_teachers_course_is_invisible(self):
        self.assert_indistinguishable(
            self.admin,
            "get",
            self.student,
            "enrollments__course",
            self.course_b.id,
            UNKNOWN,
        )

    def test_outside_withdrawal_is_invisible(self):
        self.withdraw_as_owner(self.student, self.course_b)
        self.assert_indistinguishable(
            self.admin,
            "get",
            self.student,
            "enrollments__enrollment_status",
            "WITHDRAWN",
            "COMPLETED",
        )

    def test_in_school_course_filter_still_works(self):
        response = self.call(
            self.admin, "get", self.pupil, {"enrollments__course": self.course_c.id}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class SuperAdminAndSelf(UserEnrollmentFilterOracleBase):
    def test_superadmin_filters_remain_platform_wide(self):
        response = self.call(
            self.superadmin,
            "get",
            self.student,
            {"enrollments__course": self.course_b.id},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.client.force_authenticate(self.superadmin)
        listed = self.client.get(
            reverse("user-list"), {"enrollments__course": str(self.course_b.id)}
        )
        self.assertEqual(
            {str(row["id"]) for row in listed.data["results"]}, {str(self.student.id)}
        )

    def test_single_flag_superuser_is_scoped_like_a_teacher(self):
        rogue = make_user("rogue@x.test", UserTypes.TEACHER)
        rogue.is_superuser = rogue.is_staff = True
        rogue.save()
        response = self.call(
            rogue, "get", self.student, {"enrollments__course": self.course_b.id}
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_student_filtering_their_own_record_sees_their_own_enrollments(self):
        response = self.call(
            self.student, "get", self.student, {"enrollments__course": self.course_b.id}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
