"""`my-students` must only describe the requesting teacher's own courses.

A student can be enrolled with several teachers at once. In production this
is the normal case, not an edge case: student accounts have
`school_id = NULL`, and a school-less account may join any individual
teacher's course (`check_existing_account_may_join`). The endpoint picks
students by "has an active enrollment with ME", but the serializer then
walks `obj.enrollments.all()` - and when that prefetch was unfiltered it held
EVERY enrollment the student had, so:

  * `enrolled_courses` listed other teachers' course names;
  * `?enrollments__course=<another teacher's course>` kept the shared student
    in the result (the Exists() check still matched on the teacher's own
    enrollment) and made that foreign course the row's "relevant course",
    exposing its description, its teacher's name and the student's grade in
    it.

Enrollments here are made through `enroll_student_by_email`, the service
every real roster path uses, so the cross-teacher state is one production
actually reaches.
"""

import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIRequestFactory, APITestCase

from assignments.models import Assignment, AssignmentStatus
from AutoGrader.test_cache import real_redis_caches
from classrooms.models import Course, School, Session
from classrooms.services import enroll_student_by_email
from classrooms.views import StudentCourseViewSet
from students.models import StudentSubmission
from students.serializers import StudentListSerializer
from users.models import UserTypes

User = get_user_model()

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

URL = reverse("student-course-my-students")


def make_user(email, user_type, school=None, first_name="", last_name=""):
    user = User.objects.create_user(email=email, password="password123")  # nosec
    user.user_type = user_type
    user.is_active = True
    user.school = school
    user.first_name, user.last_name = first_name, last_name
    user.save()
    return user


@override_settings(CACHES=LOCMEM)
class MyStudentsCourseScopeBase(APITestCase):
    def setUp(self):
        cache.clear()

    def make_course(self, teacher, name, description):
        session = Session.objects.create(name=f"{name} session", teacher=teacher)
        return Course.objects.create(
            name=name, description=description, teacher=teacher, session=session
        )

    def fetch(self, teacher, params=None):
        self.client.force_authenticate(teacher)
        response = self.client.get(URL, params or {})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response

    def row_for(self, response, student):
        rows = [r for r in response.data["results"] if str(r["id"]) == str(student.id)]
        self.assertEqual(len(rows), 1, "the shared student should be listed once")
        return rows[0]

    def assert_nothing_about(self, response, *secrets):
        """Search the WHOLE serialized payload, not just the fields a test
        happens to name, so a leak through any field fails."""
        body = json.dumps(response.data, default=str)
        for secret in secrets:
            self.assertNotIn(secret, body)

    def assert_same_as_unknown_id(self, response, param):
        """A foreign id must be indistinguishable from one that doesn't
        exist, or the filter is an oracle for other tenants' ids."""
        unknown = self.client.get(URL, {param: "00000000-0000-4000-8000-000000000000"})
        self.assertEqual(unknown.status_code, response.status_code)
        self.assertEqual(unknown.data, response.data)


class IndividualTeachersSharingAStudent(MyStudentsCourseScopeBase):
    """The production shape: school-less student, two unrelated teachers."""

    def setUp(self):
        super().setUp()
        self.teacher_a = make_user(
            "ta@indiv.test", UserTypes.TEACHER, None, "Ada", "Alpha"
        )
        self.teacher_b = make_user(
            "tb@indiv.test", UserTypes.TEACHER, None, "Bea", "Bravo"
        )
        self.course_a = self.make_course(self.teacher_a, "Algebra A", "A notes")
        self.course_a2 = self.make_course(self.teacher_a, "Geometry A", "A2 notes")
        self.course_b = self.make_course(
            self.teacher_b, "Private Tutoring B", "B confidential notes"
        )

        self.student = make_user(
            "shared@indiv.test", UserTypes.STUDENT, None, "Sam", "Shared"
        )
        # B enrolls first, so an unscoped enrollments cache puts B's course
        # at index 0 - the serializer's last-resort fallback.
        for course in (self.course_b, self.course_a, self.course_a2):
            enroll_student_by_email(course=course, email=self.student.email)

        self.student.refresh_from_db()
        self.assertIsNone(self.student.school_id)

        self.assignment_b = Assignment.objects.create(
            title="B quiz",
            course=self.course_b,
            teacher=self.teacher_b,
            status=AssignmentStatus.PUBLISHED,
        )
        # Graded the way the grading pipeline saves it; the post_save signal
        # derives the enrollment's final_grade (37.00) from this row.
        StudentSubmission.objects.create(
            student=self.student,
            assignment=self.assignment_b,
            answers={},
            score=37,
            max_points=100,
            graded_at=timezone.now(),
        )

    def test_teacher_a_sees_only_their_own_course_names(self):
        row = self.row_for(self.fetch(self.teacher_a), self.student)
        self.assertCountEqual(row["enrolled_courses"], ["Algebra A", "Geometry A"])

    def test_teacher_b_sees_only_their_own_course_names(self):
        row = self.row_for(self.fetch(self.teacher_b), self.student)
        self.assertEqual(row["enrolled_courses"], ["Private Tutoring B"])

    def test_teacher_a_payload_carries_nothing_of_teacher_b(self):
        response = self.fetch(self.teacher_a)
        row = self.row_for(response, self.student)
        self.assertEqual(row["teacher"], "Ada Alpha")
        self.assertIsNone(row["grade"])
        self.assertEqual(row["total_assignments_submitted"], 0)
        self.assert_nothing_about(
            response, "Private Tutoring B", "B confidential notes", "Bea", "Bravo"
        )

    def test_course_filter_naming_another_teachers_course_returns_no_rows(self):
        """Not even the shared student: a row would confirm the student is
        also in course B, and would make B the row's "relevant course"."""
        response = self.fetch(
            self.teacher_a, {"enrollments__course": str(self.course_b.id)}
        )
        self.assertEqual(response.data["count"], 0)
        self.assert_same_as_unknown_id(response, "enrollments__course")
        self.assert_nothing_about(
            response, "Private Tutoring B", "B confidential notes", "Bea", "Bravo"
        )

    def test_session_filter_naming_another_teachers_session_returns_no_rows(self):
        response = self.fetch(
            self.teacher_a,
            {"enrollments__course__session": str(self.course_b.session_id)},
        )
        self.assertEqual(response.data["count"], 0)
        self.assert_same_as_unknown_id(response, "enrollments__course__session")

    def test_prefetch_caches_hold_only_the_teachers_own_rows(self):
        """Defence in depth below the serializer.

        The serializer happens to discard a foreign submission today (it
        only counts submissions for the row's relevant course), so the
        payload alone cannot prove the submissions prefetch is scoped. Read
        the caches the serializer is handed instead: nothing of teacher B's
        may be loaded for teacher A at all.
        """
        request = APIRequestFactory().get(URL)
        request.user = self.teacher_a
        view = StudentCourseViewSet(action="my_students", request=request)
        student = next(s for s in view.get_queryset() if s.pk == self.student.pk)

        self.assertCountEqual(
            [e.course_id for e in student.enrollments.all()],
            [self.course_a.id, self.course_a2.id],
        )
        self.assertEqual(list(student.submissions.all()), [])

    def test_own_course_filter_still_selects_that_course(self):
        row = self.row_for(
            self.fetch(self.teacher_a, {"enrollments__course": str(self.course_a2.id)}),
            self.student,
        )
        self.assertEqual(row["course_description"], "A2 notes")
        self.assertCountEqual(row["enrolled_courses"], ["Algebra A", "Geometry A"])

    def test_own_session_filter_still_selects_the_student(self):
        response = self.fetch(
            self.teacher_a,
            {"enrollments__course__session": str(self.course_a.session_id)},
        )
        self.row_for(response, self.student)

    def test_malformed_course_id_is_still_rejected(self):
        self.client.force_authenticate(self.teacher_a)
        response = self.client.get(URL, {"enrollments__course": "not-a-uuid"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_student_only_in_a_foreign_course_is_not_listed(self):
        loner = make_user("loner@indiv.test", UserTypes.STUDENT, None, "Lo", "Ner")
        enroll_student_by_email(course=self.course_b, email=loner.email)
        response = self.fetch(self.teacher_a)
        self.assertNotIn(
            str(loner.id), {str(r["id"]) for r in response.data["results"]}
        )

    def test_teacher_b_payload_carries_nothing_of_teacher_a(self):
        response = self.fetch(self.teacher_b)
        row = self.row_for(response, self.student)
        self.assertEqual(row["teacher"], "Bea Bravo")
        self.assertEqual(row["grade"]["percentage"], Decimal("37.00"))
        self.assertEqual(row["total_assignments_submitted"], 1)
        self.assert_nothing_about(
            response, "Algebra A", "Geometry A", "A notes", "A2 notes", "Ada", "Alpha"
        )


class SchoolTeachersSharingAStudent(MyStudentsCourseScopeBase):
    """Two teachers of the same school, one student in both classes."""

    def setUp(self):
        super().setUp()
        self.school = School.objects.create(name="Shared School")
        self.teacher_a = make_user(
            "ta@school.test", UserTypes.TEACHER, self.school, "Cal", "Chem"
        )
        self.teacher_b = make_user(
            "tb@school.test", UserTypes.TEACHER, self.school, "Dee", "Drama"
        )
        self.course_a = self.make_course(self.teacher_a, "Chemistry 101", "chem notes")
        self.course_b = self.make_course(self.teacher_b, "Drama Club", "drama notes")

        # A brand-new address, so the service creates the account itself
        # (with school set from the teacher, as production does), then the
        # second teacher adds the same address.
        self.email = "pupil@school.test"
        self.student, created = enroll_student_by_email(
            course=self.course_b, email=self.email
        )
        self.assertTrue(created)
        enroll_student_by_email(course=self.course_a, email=self.email)
        self.student.refresh_from_db()
        self.assertEqual(self.student.school_id, self.school.id)

    def test_each_school_teacher_sees_only_their_own_course(self):
        row_a = self.row_for(self.fetch(self.teacher_a), self.student)
        row_b = self.row_for(self.fetch(self.teacher_b), self.student)
        self.assertEqual(row_a["enrolled_courses"], ["Chemistry 101"])
        self.assertEqual(row_b["enrolled_courses"], ["Drama Club"])
        self.assertEqual(row_a["teacher"], "Cal Chem")
        self.assertEqual(row_b["teacher"], "Dee Drama")

    def test_each_school_teacher_payload_carries_nothing_of_the_colleague(self):
        self.assert_nothing_about(
            self.fetch(self.teacher_a), "Drama Club", "drama notes", "Dee", 'Drama"'
        )
        self.assert_nothing_about(
            self.fetch(self.teacher_b), "Chemistry 101", "chem notes", "Cal", 'Chem"'
        )

    def test_course_filter_naming_a_colleagues_course_returns_no_rows(self):
        response = self.fetch(
            self.teacher_a, {"enrollments__course": str(self.course_b.id)}
        )
        self.assertEqual(response.data["count"], 0)
        self.assert_same_as_unknown_id(response, "enrollments__course")
        self.assert_nothing_about(response, "Drama Club", "drama notes", "Dee")

    def test_session_filter_naming_a_colleagues_session_returns_no_rows(self):
        response = self.fetch(
            self.teacher_a,
            {"enrollments__course__session": str(self.course_b.session_id)},
        )
        self.assertEqual(response.data["count"], 0)
        self.assert_same_as_unknown_id(response, "enrollments__course__session")


class OtherRolesAndTheCache(MyStudentsCourseScopeBase):
    """Every other caller of the endpoint, and the cache layer around it."""

    def setUp(self):
        super().setUp()
        self.school = School.objects.create(name="Role School")
        self.teacher_a = make_user(
            "ra@role.test", UserTypes.TEACHER, self.school, "Ada", "Alpha"
        )
        self.teacher_b = make_user(
            "rb@role.test", UserTypes.TEACHER, None, "Bea", "Bravo"
        )
        self.course_a = self.make_course(self.teacher_a, "Algebra A", "A notes")
        self.course_b = self.make_course(
            self.teacher_b, "Private Tutoring B", "B confidential notes"
        )
        self.student = make_user("rs@role.test", UserTypes.STUDENT, None, "Sam", "S")
        enroll_student_by_email(course=self.course_b, email=self.student.email)
        enroll_student_by_email(course=self.course_a, email=self.student.email)
        self.secrets = ("Algebra A", "A notes", "Private Tutoring B", "B confidential")

    def assert_refused(self, user):
        self.client.force_authenticate(user)
        response = self.client.get(URL)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assert_nothing_about(response, *self.secrets)

    def test_the_shared_student_is_refused(self):
        self.assert_refused(self.student)

    def test_a_school_admin_is_refused(self):
        self.assert_refused(
            make_user("adm@role.test", UserTypes.SCHOOL_ADMIN, self.school)
        )

    def test_a_superadmin_with_both_flags_is_refused(self):
        admin = make_user("sa@role.test", UserTypes.SUPER_ADMIN)
        admin.is_superuser = admin.is_staff = True
        admin.save()
        self.assert_refused(admin)

    def test_a_single_flag_superuser_is_only_a_teacher_with_no_students(self):
        """create_superuser() leaves user_type=TEACHER: such an account gets
        the teacher view of its OWN (empty) roster, never anyone else's."""
        rogue = make_user("rogue@role.test", UserTypes.TEACHER)
        rogue.is_superuser = rogue.is_staff = True
        rogue.save()
        response = self.fetch(rogue)
        self.assertEqual(response.data["count"], 0)
        self.assert_nothing_about(response, *self.secrets)

    def test_unauthenticated_is_refused(self):
        self.client.force_authenticate(None)
        response = self.client.get(URL)
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )
        self.assert_nothing_about(response, *self.secrets)

    def test_my_students_never_reads_or_writes_the_cache(self):
        """Nothing cached means no cached payload can cross teachers.

        `UserCacheMixin` caches `list`/`retrieve` only; `my_students` is a
        separate action. Pin that, so adding caching later is a deliberate
        change that must bring its own per-teacher isolation proof.
        """
        with patch("users.mixins.cache") as mixin_cache, patch(
            "classrooms.views.cache"
        ) as view_cache:
            self.fetch(self.teacher_a)
            self.fetch(self.teacher_b)
        for mocked in (mixin_cache, view_cache):
            self.assertEqual(mocked.method_calls, [])

    @override_settings(CACHES=real_redis_caches("redis://127.0.0.1:6379/14"))
    def test_alternating_teachers_on_real_redis_each_get_their_own_payload(self):
        for _ in range(2):
            row_a = self.row_for(self.fetch(self.teacher_a), self.student)
            row_b = self.row_for(self.fetch(self.teacher_b), self.student)
            self.assertEqual(row_a["enrolled_courses"], ["Algebra A"])
            self.assertEqual(row_b["enrolled_courses"], ["Private Tutoring B"])

    @override_settings(CACHES=real_redis_caches("redis://127.0.0.1:1/0"))
    def test_redis_unreachable_still_serves_only_the_teachers_own_data(self):
        response = self.fetch(self.teacher_a)
        row = self.row_for(response, self.student)
        self.assertEqual(row["enrolled_courses"], ["Algebra A"])
        self.assert_nothing_about(response, "Private Tutoring B", "B confidential")


class PremisesTheEquivalenceArgumentsRestOn(MyStudentsCourseScopeBase):
    """Pins for the two mutants that cannot be killed (M7 in the battery).

    M7 removes `_resolve_relevant_course`'s "prefer a course I teach"
    fallback. That mutant is equivalent ONLY because every enrollment the
    serializer can see already belongs to the requester, which rests on two
    facts that a future change could quietly break:

      1. `StudentListSerializer` is reachable from the `my_students` action
         and nowhere else, so it never sees an unscoped cache;
      2. the cache that action builds holds the requester's enrollments only.

    If either stops holding, the equivalence argument in
    `docs/evidence/MY_STUDENTS_TENANCY_EVIDENCE.md` is void and the leak can
    come back without any mutant noticing. These tests fail loudly in that
    case. Fact 2 is also asserted from the response side by
    `test_prefetch_caches_hold_only_the_teachers_own_rows`.
    """

    def setUp(self):
        super().setUp()
        self.teacher_a = make_user("pa@indiv.test", UserTypes.TEACHER, None, "Ada", "A")
        self.teacher_b = make_user("pb@indiv.test", UserTypes.TEACHER, None, "Bea", "B")
        self.course_a = self.make_course(self.teacher_a, "Algebra A", "A notes")
        self.course_b = self.make_course(self.teacher_b, "Tutoring B", "B notes")
        self.student = make_user("ps@indiv.test", UserTypes.STUDENT, None, "Sam", "S")
        for course in (self.course_b, self.course_a):
            enroll_student_by_email(course=course, email=self.student.email)

    def test_student_list_serializer_is_used_by_my_students_only(self):
        request = APIRequestFactory().get(URL)
        request.user = self.teacher_a
        for action in ("list", "retrieve", "partial_update", "destroy", "my_students"):
            view = StudentCourseViewSet(action=action, request=request)
            used = view.get_serializer_class()
            if action == "my_students":
                self.assertIs(used, StudentListSerializer)
            else:
                self.assertIsNot(
                    used,
                    StudentListSerializer,
                    f"{action} now serves StudentListSerializer; it must then "
                    "build the same teacher-scoped enrollment cache",
                )

    def test_every_cached_enrollment_belongs_to_the_requesting_teacher(self):
        request = APIRequestFactory().get(URL)
        request.user = self.teacher_a
        view = StudentCourseViewSet(action="my_students", request=request)
        rows = list(view.get_queryset())
        self.assertTrue(rows, "the premise test would be vacuous with no rows")
        for row in rows:
            cached = list(row.enrollments.all())
            self.assertTrue(cached)
            for enrollment in cached:
                self.assertEqual(enrollment.course.teacher_id, self.teacher_a.id)
