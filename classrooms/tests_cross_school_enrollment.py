"""Adversarial tests for the cross-school enrollment rule.

An account that already belongs to one school must not be enrolled into
another school's course by ANY route, and a rejected attempt must leave
every tenant row exactly as it was - the account's school above all.

Ownership here is derived (student -> enrollments -> course -> teacher ->
school), not read from `CustomUser.school`: measured against production,
every student account has `school_id = NULL`, so a rule keyed on that
column would silently never fire. Several tests below therefore assert on
accounts whose own `school` FK is null while their enrollments place them
firmly in a school - the realistic case.
"""

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
from classrooms.services import (
    CROSS_SCHOOL_REJECTION_MESSAGE,
    EnrollmentError,
    check_existing_account_may_join,
    enroll_student_by_email,
    schools_associated_with,
)
from users.models import UserTypes

User = get_user_model()

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def make_user(email, user_type, school=None, **fields):
    user = User.objects.create_user(email=email, password="password123")  # nosec
    user.user_type = user_type
    user.is_active = True
    user.school = school
    for name, value in fields.items():
        setattr(user, name, value)
    user.save()
    return user


@override_settings(CACHES=LOCMEM)
class CrossSchoolBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.school_a = School.objects.create(name="School A")
        self.school_b = School.objects.create(name="School B")

        # Two teachers in school A, one in school B, one with no school at
        # all (the individual track, which is the majority in production).
        self.teacher_a = make_user("ta@a.test", UserTypes.TEACHER, self.school_a)
        self.teacher_a2 = make_user("ta2@a.test", UserTypes.TEACHER, self.school_a)
        self.teacher_b = make_user("tb@b.test", UserTypes.TEACHER, self.school_b)
        self.teacher_solo = make_user("solo@x.test", UserTypes.TEACHER, None)
        self.admin_b = make_user("adminb@b.test", UserTypes.SCHOOL_ADMIN, self.school_b)

        self.course_a = self._course(self.teacher_a, "A1")
        self.course_a2 = self._course(self.teacher_a2, "A2")
        self.course_b = self._course(self.teacher_b, "B1")
        self.course_solo = self._course(self.teacher_solo, "Solo")

    def _course(self, teacher, name):
        session = Session.objects.create(name=f"S-{name}", teacher=teacher)
        return Course.objects.create(name=name, teacher=teacher, session=session)

    def student_of(self, email, course, school_fk=None):
        """A student whose ownership comes from an enrollment, as in prod."""
        student = make_user(email, UserTypes.STUDENT, school_fk)
        StudentCourse.objects.create(
            student=student,
            course=course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        return student

    def snapshot(self, user):
        user.refresh_from_db()
        return {
            "school_id": user.school_id,
            "user_type": user.user_type,
            "is_active": user.is_active,
            "enrollments": sorted(
                str(pk)
                for pk in StudentCourse.objects.filter(student=user).values_list(
                    "course_id", flat=True
                )
            ),
        }

    def add_by_email(self, teacher, course, email):
        cache.clear()
        self.client.force_authenticate(teacher)
        return self.client.post(
            reverse("course-students", kwargs={"pk": course.id}), {"email": email}
        )

    def bulk_add(self, teacher, course, raw):
        cache.clear()
        self.client.force_authenticate(teacher)
        return self.client.post(
            reverse("course-bulk-add-students", kwargs={"pk": course.id}),
            {"raw_data": raw},
        )

    def direct_add(self, teacher, course, payload):
        cache.clear()
        self.client.force_authenticate(teacher)
        return self.client.post(
            reverse("course-direct-add-student", kwargs={"pk": course.id}), payload
        )


class OwnershipDerivation(CrossSchoolBase):
    """`schools_associated_with` is the whole rule's foundation."""

    def test_an_enrollment_establishes_school_ownership(self):
        student = self.student_of("s1@x.test", self.course_b)
        self.assertIsNone(student.school_id, "precondition: prod shape")
        self.assertEqual(schools_associated_with(student), {self.school_b.id})

    def test_an_unaffiliated_student_is_associated_with_no_school(self):
        student = self.student_of("s2@x.test", self.course_solo)
        self.assertEqual(schools_associated_with(student), set())

    def test_the_school_fk_is_honoured_when_it_is_set(self):
        student = make_user("s3@x.test", UserTypes.STUDENT, self.school_b)
        self.assertEqual(schools_associated_with(student), {self.school_b.id})

    def test_two_teachers_in_one_school_are_one_association(self):
        student = self.student_of("s4@x.test", self.course_a)
        StudentCourse.objects.create(
            student=student,
            course=self.course_a2,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        self.assertEqual(schools_associated_with(student), {self.school_a.id})

    def test_a_withdrawn_enrollment_still_counts_as_ownership(self):
        """Removing a student from a course does not release the school's
        claim on the account - otherwise withdrawing them would be a way to
        make them poachable."""
        student = self.student_of("s5@x.test", self.course_b)
        StudentCourse.objects.filter(student=student).update(
            enrollment_status=EnrollmentStatusType.WITHDRAWN
        )
        self.assertEqual(schools_associated_with(student), {self.school_b.id})


class SameSchoolIsAllowed(CrossSchoolBase):
    """Scenario 1: the normal flow must keep working."""

    def test_another_teacher_in_the_same_school_may_enrol_the_student(self):
        student = self.student_of("same@a.test", self.course_a)

        response = self.add_by_email(self.teacher_a2, self.course_a2, student.email)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            StudentCourse.objects.filter(
                student=student, course=self.course_a2
            ).exists()
        )

    def test_the_service_permits_it_directly(self):
        student = self.student_of("same2@a.test", self.course_a)
        check_existing_account_may_join(student, self.course_a2)  # must not raise

    def test_bulk_import_permits_a_same_school_account(self):
        student = self.student_of("same3@a.test", self.course_a)

        response = self.bulk_add(
            self.teacher_a2, self.course_a2, f"Same,School,{student.email}"
        )

        self.assertEqual(response.data["success_count"], 1)
        self.assertTrue(
            StudentCourse.objects.filter(
                student=student, course=self.course_a2
            ).exists()
        )

    def test_an_unaffiliated_student_may_still_be_enrolled(self):
        """No school association means no ownership to violate - this is the
        ordinary individual-teacher case and must not be collateral."""
        student = self.student_of("free@x.test", self.course_solo)

        response = self.add_by_email(self.teacher_a, self.course_a, student.email)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            StudentCourse.objects.filter(student=student, course=self.course_a).exists()
        )


class CrossSchoolIsRejected(CrossSchoolBase):
    """Scenario 2 and 7: rejected, and nothing mutated."""

    def setUp(self):
        super().setUp()
        self.victim = self.student_of("victim@b.test", self.course_b)
        self.before = self.snapshot(self.victim)

    def _assert_nothing_changed(self):
        self.assertEqual(
            self.snapshot(self.victim),
            self.before,
            "a rejected cross-school attempt mutated tenant data",
        )

    def test_single_add_is_rejected(self):
        response = self.add_by_email(self.teacher_a, self.course_a, self.victim.email)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self._assert_nothing_changed()

    def test_single_add_does_not_create_the_enrollment(self):
        self.add_by_email(self.teacher_a, self.course_a, self.victim.email)
        self.assertFalse(
            StudentCourse.objects.filter(
                student=self.victim, course=self.course_a
            ).exists()
        )

    def test_the_victims_school_is_not_reassigned(self):
        self.add_by_email(self.teacher_a, self.course_a, self.victim.email)
        self.victim.refresh_from_db()
        self.assertEqual(schools_associated_with(self.victim), {self.school_b.id})

    def test_bulk_import_is_rejected(self):
        """Scenario 5."""
        response = self.bulk_add(
            self.teacher_a, self.course_a, f"Cross,School,{self.victim.email}"
        )
        self.assertEqual(response.data["success_count"], 0)
        self.assertEqual(response.data["failure_count"], 1)
        self._assert_nothing_changed()

    def test_direct_add_is_rejected(self):
        response = self.direct_add(
            self.teacher_a,
            self.course_a,
            {
                "first_name": "Cross",
                "last_name": "School",
                "email": self.victim.email,
            },
        )
        self.assertIn(
            response.status_code,
            (status.HTTP_400_BAD_REQUEST, status.HTTP_500_INTERNAL_SERVER_ERROR),
        )
        self._assert_nothing_changed()

    def test_an_individual_teacher_cannot_poach_a_school_student(self):
        """The target having no school is not a loophole - the account still
        belongs to school B."""
        response = self.add_by_email(
            self.teacher_solo, self.course_solo, self.victim.email
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self._assert_nothing_changed()

    def test_the_service_raises_for_a_cross_school_account(self):
        with self.assertRaises(EnrollmentError):
            check_existing_account_may_join(self.victim, self.course_a)

    def test_a_withdrawn_student_still_cannot_be_poached(self):
        StudentCourse.objects.filter(student=self.victim).update(
            enrollment_status=EnrollmentStatusType.WITHDRAWN
        )
        response = self.add_by_email(self.teacher_a, self.course_a, self.victim.email)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class RejectionDisclosesLittle(CrossSchoolBase):
    """The message must not turn the form into an account-enumeration oracle."""

    def test_the_message_is_generic(self):
        victim = self.student_of("quiet@b.test", self.course_b)

        response = self.add_by_email(self.teacher_a, self.course_a, victim.email)

        body = str(response.data)
        self.assertIn("cannot be added to this school", body)
        self.assertNotIn(self.school_b.name, body)
        self.assertNotIn(victim.email, body)

    def test_the_message_does_not_name_the_other_school_in_bulk_either(self):
        victim = self.student_of("quiet2@b.test", self.course_b)

        response = self.bulk_add(self.teacher_a, self.course_a, f"Q,Two,{victim.email}")

        error = response.data["results"][0]["error"]
        self.assertEqual(error, CROSS_SCHOOL_REJECTION_MESSAGE)
        self.assertNotIn(self.school_b.name, error)

    def test_the_reason_is_recorded_server_side_for_administrators(self):
        victim = self.student_of("logged@b.test", self.course_b)

        with self.assertLogs("classrooms.services.enrollment", level="WARNING") as cap:
            self.add_by_email(self.teacher_a, self.course_a, victim.email)

        joined = " ".join(cap.output)
        self.assertIn(str(victim.pk), joined)
        self.assertIn(str(self.school_b.id), joined)


class StaffAccountsAreRejected(CrossSchoolBase):
    """Scenario 3, on every route."""

    def test_single_add_refuses_a_teacher(self):
        response = self.add_by_email(
            self.teacher_a, self.course_a, self.teacher_b.email
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.teacher_b.refresh_from_db()
        self.assertEqual(self.teacher_b.user_type, UserTypes.TEACHER)
        self.assertFalse(StudentCourse.objects.filter(student=self.teacher_b).exists())

    def test_bulk_import_refuses_a_teacher(self):
        response = self.bulk_add(
            self.teacher_a, self.course_a, f"Mal,Actor,{self.teacher_b.email}"
        )
        self.assertEqual(response.data["failure_count"], 1)
        self.assertFalse(StudentCourse.objects.filter(student=self.teacher_b).exists())

    def test_bulk_import_refuses_a_school_admin(self):
        self.bulk_add(self.teacher_a, self.course_a, f"Mal,Actor,{self.admin_b.email}")
        self.admin_b.refresh_from_db()
        self.assertEqual(self.admin_b.user_type, UserTypes.SCHOOL_ADMIN)
        self.assertFalse(StudentCourse.objects.filter(student=self.admin_b).exists())

    def test_direct_add_refuses_a_teacher(self):
        self.direct_add(
            self.teacher_a,
            self.course_a,
            {"first_name": "M", "last_name": "A", "email": self.teacher_b.email},
        )
        self.assertFalse(StudentCourse.objects.filter(student=self.teacher_b).exists())

    def test_a_staff_account_keeps_its_own_school(self):
        before = self.teacher_b.school_id
        self.add_by_email(self.teacher_a, self.course_a, self.teacher_b.email)
        self.teacher_b.refresh_from_db()
        self.assertEqual(self.teacher_b.school_id, before)


class NewAccountsStillWork(CrossSchoolBase):
    """Scenario 4: the rule must not block genuine onboarding."""

    def test_an_unknown_email_creates_a_pending_student(self):
        # Single-add-by-email creates the account active immediately with a
        # system-generated temporary password (see
        # classrooms/services/enrollment.py::_create_new_student) rather
        # than is_active=False + an activation token - the enrollment
        # itself still starts PENDING until the student's first login.
        response = self.add_by_email(
            self.teacher_a, self.course_a, "brand.new@nowhere.test"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        student = User.objects.get(email="brand.new@nowhere.test")
        self.assertEqual(student.user_type, UserTypes.STUDENT)
        self.assertTrue(student.is_active)
        self.assertTrue(student.must_change_password)
        self.assertEqual(
            StudentCourse.objects.get(student=student).enrollment_status,
            EnrollmentStatusType.PENDING,
        )

    def test_a_new_student_inherits_the_creating_teachers_school(self):
        self.add_by_email(self.teacher_a, self.course_a, "inherit@nowhere.test")
        student = User.objects.get(email="inherit@nowhere.test")
        self.assertEqual(student.school_id, self.school_a.id)

    def test_bulk_import_still_creates_new_students(self):
        response = self.bulk_add(
            self.teacher_a, self.course_a, "Fresh,Face,fresh@nowhere.test"
        )
        self.assertEqual(response.data["success_count"], 1)
        self.assertTrue(User.objects.filter(email="fresh@nowhere.test").exists())

    def test_direct_add_still_creates_a_nameless_email_student(self):
        response = self.direct_add(
            self.teacher_a, self.course_a, {"first_name": "No", "last_name": "Email"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            User.objects.filter(first_name="No", last_name="Email").exists()
        )


class IdManipulationCannotBypassTheRule(CrossSchoolBase):
    """Scenario 6: the rule is derived server-side, so client-supplied ids
    for school/student/course must not be able to steer it."""

    def setUp(self):
        super().setUp()
        self.victim = self.student_of("target@b.test", self.course_b)
        self.before = self.snapshot(self.victim)

    def test_a_forged_school_field_in_the_payload_is_ignored(self):
        cache.clear()
        self.client.force_authenticate(self.teacher_a)
        response = self.client.post(
            reverse("course-students", kwargs={"pk": self.course_a.id}),
            {
                "email": self.victim.email,
                "school": str(self.school_a.id),
                "school_id": str(self.school_a.id),
                "user_type": UserTypes.STUDENT,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.snapshot(self.victim), self.before)

    def test_a_forged_student_id_in_the_bulk_payload_is_ignored(self):
        response = self.bulk_add(
            self.teacher_a,
            self.course_a,
            f"student_id,first_name,last_name,email\n"
            f"{self.victim.id},T,Arget,{self.victim.email}",
        )
        self.assertEqual(response.data["success_count"], 0)
        self.assertEqual(self.snapshot(self.victim), self.before)

    def test_targeting_another_schools_course_id_is_a_404_not_a_bypass(self):
        response = self.add_by_email(
            self.teacher_a, self.course_b, "someone@nowhere.test"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(User.objects.filter(email="someone@nowhere.test").exists())

    def test_patching_an_enrollment_cannot_move_a_student_between_schools(self):
        mine = self.student_of("mine@a.test", self.course_a)
        enrollment = StudentCourse.objects.get(student=mine, course=self.course_a)

        cache.clear()
        self.client.force_authenticate(self.teacher_a)
        self.client.patch(
            reverse("student-course-detail", kwargs={"pk": enrollment.id}),
            {"student": str(self.victim.id), "course": str(self.course_b.id)},
            format="json",
        )

        enrollment.refresh_from_db()
        self.assertEqual(enrollment.student_id, mine.id)
        self.assertEqual(enrollment.course_id, self.course_a.id)
        self.assertEqual(self.snapshot(self.victim), self.before)

    def test_case_and_whitespace_variants_of_the_email_do_not_slip_through(self):
        """Found a REAL bypass: Postgres compares the unique `email` column
        case-sensitively, so "TARGET@B.TEST" did not match the existing
        account and was onboarded as a brand-new student - which both
        evaded this rule and created a second account for a real person's
        mailbox. Every enrollment path now normalises and matches
        case-insensitively."""
        users_before = User.objects.count()

        for variant in [
            self.victim.email.upper(),
            self.victim.email.title(),
            f"  {self.victim.email}  ",
        ]:
            response = self.add_by_email(self.teacher_a, self.course_a, variant)
            self.assertNotEqual(
                response.status_code,
                status.HTTP_200_OK,
                f"variant {variant!r} bypassed the cross-school rule",
            )
            self.assertEqual(self.snapshot(self.victim), self.before)

        self.assertEqual(
            User.objects.count(),
            users_before,
            "a case variant created a duplicate shadow account",
        )

    def test_a_case_variant_never_creates_a_duplicate_account(self):
        """Independent of the cross-school rule: an uppercase address for an
        UNAFFILIATED existing student must reuse that account, not mint a
        second one for the same mailbox."""
        student = self.student_of("dupe@x.test", self.course_solo)
        users_before = User.objects.count()

        response = self.add_by_email(self.teacher_a, self.course_a, "DUPE@X.TEST")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(User.objects.count(), users_before)
        self.assertTrue(
            StudentCourse.objects.filter(student=student, course=self.course_a).exists()
        )

    def test_bulk_import_also_folds_case_variants(self):
        victim_upper = self.victim.email.upper()
        users_before = User.objects.count()

        response = self.bulk_add(
            self.teacher_a, self.course_a, f"Case,Variant,{victim_upper}"
        )

        self.assertEqual(response.data["success_count"], 0)
        self.assertEqual(User.objects.count(), users_before)
        self.assertEqual(self.snapshot(self.victim), self.before)

    def test_a_legacy_mixed_case_account_is_still_matched(self):
        """Normalising the INPUT is not enough on its own.

        Rows created before normalisation are stored mixed-case - there are
        2 such accounts in production. Lowercasing the incoming address
        would then fail to match them and mint a duplicate, so the lookup
        itself has to be case-insensitive. This is the test that fails if
        `find_account_by_email` drops its `iexact`.
        """
        legacy = self.student_of("Legacy@B.Test", self.course_b)
        User.objects.filter(pk=legacy.pk).update(email="Legacy@B.Test")
        legacy.refresh_from_db()
        self.assertEqual(
            legacy.email, "Legacy@B.Test", "precondition: stored mixed-case"
        )
        users_before = User.objects.count()

        response = self.add_by_email(self.teacher_a, self.course_a, "legacy@b.test")

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
            "a legacy mixed-case account was not matched, so the cross-school "
            "rule did not fire",
        )
        self.assertEqual(
            User.objects.count(),
            users_before,
            "a legacy mixed-case account was shadowed by a new duplicate",
        )

    def test_a_legacy_mixed_case_account_is_reused_not_duplicated(self):
        legacy = self.student_of("Solo@Legacy.Test", self.course_solo)
        User.objects.filter(pk=legacy.pk).update(email="Solo@Legacy.Test")
        users_before = User.objects.count()

        response = self.add_by_email(self.teacher_a, self.course_a, "solo@legacy.test")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(User.objects.count(), users_before)
        self.assertTrue(
            StudentCourse.objects.filter(student=legacy, course=self.course_a).exists()
        )

    def test_a_new_student_is_stored_with_a_normalised_address(self):
        self.add_by_email(self.teacher_a, self.course_a, "  MiXeD@New.Test  ")
        self.assertTrue(User.objects.filter(email="mixed@new.test").exists())
        self.assertFalse(User.objects.filter(email="  MiXeD@New.Test  ").exists())


class ServiceLevelInvariants(CrossSchoolBase):
    """Calling the service directly must not be a way around the rule -
    other apps import it, so the guard has to live below the view."""

    def test_enroll_student_by_email_enforces_it(self):
        victim = self.student_of("svc@b.test", self.course_b)
        with self.assertRaises(EnrollmentError):
            enroll_student_by_email(course=self.course_a, email=victim.email)
        self.assertFalse(
            StudentCourse.objects.filter(student=victim, course=self.course_a).exists()
        )

    def test_a_rejected_service_call_writes_nothing_at_all(self):
        victim = self.student_of("svc2@b.test", self.course_b)
        users_before = User.objects.count()
        enrollments_before = StudentCourse.objects.count()

        with self.assertRaises(EnrollmentError):
            enroll_student_by_email(course=self.course_a, email=victim.email)

        self.assertEqual(User.objects.count(), users_before)
        self.assertEqual(StudentCourse.objects.count(), enrollments_before)
