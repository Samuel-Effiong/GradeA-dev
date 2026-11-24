"""Teacher dashboard overview for a school-managed teacher.

QA: a licensed teacher opening /dashboard got
GET /api/v1/teacher-admin/dashboard/overview/{session_id} -> 404 on every
load. The view looked the session up with `teacher=teacher`, and a SCHOOL
session has no teacher (owner_type=SCHOOL, school=...), so it never matched.

The session is now resolved by the rule the sessions list already uses
(a licensed teacher reads their school's SCHOOL sessions; anyone else their
own INDIVIDUAL sessions), and every count is scoped to the teacher's own
reachable courses, so a shared SCHOOL session never counts a colleague's
students.
"""

from datetime import timedelta

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from billing.models import (
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
)
from classrooms.models import School, Session, SessionOwnerType, StudentCourse
from dashboard.tests_teacher_all_students import Builder
from users.models import UserTypes

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def _url(session):
    return reverse("teacher-admin-overview", kwargs={"session_id": session.id})


@override_settings(CACHES=LOCMEM)
class SchoolManagedTeacherOverviewTest(Builder, APITestCase):
    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="Overview School")
        self.other_school = School.objects.create(name="Overview Other School")
        self.admin = self.user(UserTypes.SCHOOL_ADMIN, school=self.school)
        self.teacher = self.user(UserTypes.TEACHER, first="Ada", school=self.school)
        self.colleague = self.user(UserTypes.TEACHER, first="Bea", school=self.school)
        self.licence = self._license(self.school, self.admin)
        self._seat(self.teacher)
        self._seat(self.colleague)
        assert self.teacher.is_under_license()

        self.term = self._school_session(self.school, "Overview school term")
        self.my_course = self.course(self.teacher, self.term, name="Mine")
        self.their_course = self.course(self.colleague, self.term, name="Theirs")

        self.mine = self.safe_student(self.my_course, first="MyStudent")
        self._final_grade(self.mine, self.my_course, 95)
        # The colleague's student is at risk and failing: if the overview
        # read the whole session, they would show in my count, my F column
        # and my at-risk list.
        self.theirs = self.at_risk_student(self.their_course, first="TheirStudent")
        self._final_grade(self.theirs, self.their_course, 40)

    # -- fixtures --------------------------------------------------------

    def _license(self, school, admin):
        plan = SubscriptionPlan.objects.create(
            name=PlanType.STANDARD,
            display_name=f"Overview plan {self.uid()}",
            category=PlanCategory.LICENSE,
            tier=PlanTier.STANDARD,
            monthly_credits=100_000,
            is_active=True,
        )
        return LicenseSubscription.objects.create(
            school=school,
            admin_user=admin,
            plan=plan,
            contract_months=12,
            max_seats=10,
            is_active=True,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=365),
        )

    def _seat(self, teacher, licence=None):
        return SchoolCreditAllocation.objects.create(
            license_subscription=licence or self.licence,
            user=teacher,
            monthly_allocation=25_000,
            is_active=True,
            next_credit_grant_at=timezone.now() + timedelta(days=30),
        )

    def _school_session(self, school, name):
        return Session.objects.create(
            name=name, owner_type=SessionOwnerType.SCHOOL, school=school
        )

    def _final_grade(self, student, course, grade):
        StudentCourse.objects.filter(student=student, course=course).update(
            final_grade=grade
        )

    def _get(self, user, session):
        cache.clear()
        self.client.force_authenticate(user)
        return self.client.get(_url(session))

    def _ok(self, user, session):
        response = self._get(user, session)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data

    # -- the reported bug ------------------------------------------------

    def test_a_licensed_teacher_gets_their_schools_session(self):
        data = self._ok(self.teacher, self.term)

        self.assertEqual(
            [c["name"] for c in data["course_performance"]], [self.my_course.name]
        )

    # -- data scope in a shared session ----------------------------------

    def test_the_counts_leave_out_a_colleagues_students(self):
        data = self._ok(self.teacher, self.term)

        self.assertEqual(data["total_students"], 1)
        grades = data["grade_distribution"]
        self.assertEqual(grades["A"]["count"], 1)
        self.assertEqual(grades["F"]["count"], 0)
        self.assertEqual(data["at_risk_students"], [])

    def test_the_colleague_sees_their_own_students_in_the_same_session(self):
        # The hidden student is real data, not an empty answer for some
        # other reason.
        data = self._ok(self.colleague, self.term)

        self.assertEqual(data["total_students"], 1)
        self.assertEqual(data["grade_distribution"]["F"]["count"], 1)
        self.assertEqual(
            [str(r["student_id"]) for r in data["at_risk_students"]],
            [str(self.theirs.id)],
        )

    # -- who may not read the session -------------------------------------

    def test_another_schools_session_is_404(self):
        theirs = self._school_session(self.other_school, "Other school term")

        response = self._get(self.teacher, theirs)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_another_teachers_individual_session_is_404(self):
        solo = self.user(UserTypes.TEACHER, first="Cal")
        solo_term = self.session(solo)

        response = self._get(self.teacher, solo_term)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_an_unlicensed_teacher_cannot_read_a_school_session(self):
        # In the school, with a course in the session, but holding no seat.
        unseated = self.user(UserTypes.TEACHER, first="Dee", school=self.school)
        self.course(unseated, self.term, name="Unseated course")
        assert not unseated.is_under_license()

        response = self._get(unseated, self.term)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_cached_overview_is_not_served_after_the_seat_goes(self):
        self.client.force_authenticate(self.teacher)
        cache.clear()
        first = self.client.get(_url(self.term))
        self.assertEqual(first.status_code, status.HTTP_200_OK)

        SchoolCreditAllocation.objects.filter(user=self.teacher).update(is_active=False)
        assert not self.teacher.is_under_license()

        # No cache.clear(): the 15-minute entry from the first call is
        # still there.
        second = self.client.get(_url(self.term))

        self.assertEqual(second.status_code, status.HTTP_404_NOT_FOUND)


@override_settings(CACHES=LOCMEM)
class IndividualTeacherOverviewUnchangedTest(Builder, APITestCase):
    def setUp(self):
        cache.clear()
        self.teacher = self.user(UserTypes.TEACHER, first="Eve")
        self.term = self.session(self.teacher)
        self.my_course = self.course(self.teacher, self.term, name="Solo course")
        self.mine = self.safe_student(self.my_course, first="SoloStudent")
        self.at_risk = self.at_risk_student(self.my_course, first="SoloAtRisk")

    def test_an_individual_teacher_reads_their_own_session(self):
        self.client.force_authenticate(self.teacher)

        response = self.client.get(_url(self.term))

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["total_students"], 2)
        self.assertIn(
            str(self.at_risk.id),
            {str(r["student_id"]) for r in response.data["at_risk_students"]},
        )

    def test_another_teachers_individual_session_is_404(self):
        other = self.user(UserTypes.TEACHER, first="Fay")
        self.client.force_authenticate(other)

        response = self.client.get(_url(self.term))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
