"""H-38: removing a teacher from a school license leaves them attached to it.

`POST /license-subscriptions/{id}/remove_teachers/` deactivates the
teacher's SchoolCreditAllocation and expires their credit buckets, but
never clears `user.school` (set when the school admin added them) and never
touches the courses they built inside the school's sessions.

Everything here runs over HTTP with real JWTs, and every row is written the
way production writes it: the admin adds the teacher by email through
`add_teachers`, creates the school session through `/sessions/`, the teacher
creates the course through `/course/` and enrols the student through
`/course/{id}/students/`, and the admin removes the teacher through
`remove_teachers`. Only the School, the admin and the LicenseSubscription
row are created directly, because production creates those through the
superadmin flow and Stripe checkout, which are not what's under test.

Each test asserts the behaviour a removal should have. On fba1294 they fail,
and the failures are the reproduction.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from billing.models import (
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
)
from classrooms.models import Course, School
from users.models import CustomUser, UserTypes

PASSWORD = "Str0ng-h38-password!"  # pragma: allowlist secret
API = "/api/v1"


def jwt_client(email):
    # A real signed access token, checked by the real JWT authenticator on
    # every request. Minted directly rather than through /auth/login because
    # the login throttle trips after a few logins in one test process.
    token = RefreshToken.for_user(CustomUser.objects.get(email=email)).access_token
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def make_user(email, user_type, school=None):
    return CustomUser.objects.create_user(
        email=email,
        password=PASSWORD,
        first_name=email.split("@")[0].title(),
        last_name="H38",
        user_type=user_type,
        school=school,
        is_active=True,
    )


class TeacherRemovalBase(TestCase):
    def setUp(self):
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="H38 License Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
        )
        self.school = School.objects.create(name="School A H38")
        self.admin = make_user("admin-a@h38.test", UserTypes.SCHOOL_ADMIN, self.school)
        self.license = self._license(self.school, self.admin)

        self.other_school = School.objects.create(name="School B H38")
        self.other_admin = make_user(
            "admin-b@h38.test", UserTypes.SCHOOL_ADMIN, self.other_school
        )
        self.other_license = self._license(self.other_school, self.other_admin)

        # An individual-track teacher who already has an account, joined to
        # School A by its admin - the path that sets user.school.
        self.teacher = make_user("teacher@h38.test", UserTypes.TEACHER)
        self.admin_client = jwt_client(self.admin.email)
        response = self.admin_client.post(
            f"{API}/license-subscriptions/{self.license.id}/add_teachers",
            {"teacher_emails": [self.teacher.email]},
            format="json",
        )
        assert response.status_code == 200, response.content
        assert response.data["successful"] == 1, response.content
        self.teacher.refresh_from_db()
        assert self.teacher.school_id == self.school.id
        assert self.teacher.is_under_license()

        response = self.admin_client.post(
            f"{API}/sessions", {"name": "2026 Term 1"}, format="json"
        )
        assert response.status_code == 201, response.content
        self.session_id = response.data["id"]

        teacher_client = jwt_client(self.teacher.email)
        response = teacher_client.post(
            f"{API}/course",
            {"name": "School A Biology", "session": self.session_id},
            format="json",
        )
        assert response.status_code == 201, response.content
        self.course_id = response.data["id"]

        response = teacher_client.post(
            f"{API}/course/{self.course_id}/students",
            {"email": "pupil@h38.test"},
            format="json",
        )
        assert response.status_code == 200, response.content
        self.student = CustomUser.objects.get(email="pupil@h38.test")

    def _license(self, school, admin):
        return LicenseSubscription.objects.create(
            school=school,
            admin_user=admin,
            plan=self.plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
        )

    def remove_teacher(self):
        response = self.admin_client.post(
            f"{API}/license-subscriptions/{self.license.id}/remove_teachers",
            {"teacher_ids": [str(self.teacher.id)]},
            format="json",
        )
        assert response.status_code == 200, response.content
        assert response.data["successful"] == 1, response.content
        self.teacher.refresh_from_db()
        # What the endpoint does do today.
        assert not SchoolCreditAllocation.objects.filter(
            user=self.teacher, is_active=True
        ).exists()
        assert not self.teacher.is_under_license()


class RemovedTeacherStaysLinkedTests(TeacherRemovalBase):
    """The 'display + re-onboarding' half of H-38."""

    def test_removed_teacher_is_no_longer_linked_to_the_school(self):
        self.remove_teacher()
        self.assertIsNone(self.teacher.school_id)

    def test_removed_teacher_leaves_the_school_admins_user_list(self):
        self.remove_teacher()
        # /users list is superadmin-only; the admin's roster is the dashboard.
        response = self.admin_client.get(
            f"{API}/school-admin/dashboard/teachers", {"page_size": 100}
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn(str(self.teacher.id), response.content.decode())

    def test_another_school_can_onboard_the_removed_teacher(self):
        self.remove_teacher()
        other_admin_client = jwt_client(self.other_admin.email)
        response = other_admin_client.post(
            f"{API}/license-subscriptions/{self.other_license.id}/add_teachers",
            {"teacher_emails": [self.teacher.email]},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["successful"], 1, response.content)


class RemovedTeacherKeepsAccessTests(TeacherRemovalBase):
    """The 'access' half: what the removed teacher can still do to School A's data."""

    def setUp(self):
        super().setUp()
        self.remove_teacher()
        self.teacher_client = jwt_client(self.teacher.email)

    def test_cannot_read_the_school_course(self):
        response = self.teacher_client.get(f"{API}/course/{self.course_id}")
        self.assertEqual(response.status_code, 404, response.content)

    def test_cannot_list_the_school_course_or_its_students(self):
        response = self.teacher_client.get(f"{API}/course", {"page_size": 100})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.content.decode()
        self.assertNotIn(str(self.course_id), body)
        self.assertNotIn(self.student.email, body)

    def test_cannot_read_the_school_student(self):
        response = self.teacher_client.get(f"{API}/users/{self.student.id}")
        self.assertEqual(response.status_code, 404, response.content)

    def test_cannot_enrol_more_students_into_the_school_course(self):
        response = self.teacher_client.post(
            f"{API}/course/{self.course_id}/students",
            {"email": "second-pupil@h38.test"},
            format="json",
        )
        self.assertIn(response.status_code, (403, 404), response.content)
        self.assertFalse(
            CustomUser.objects.filter(email="second-pupil@h38.test").exists()
        )

    def test_cannot_edit_the_school_course(self):
        response = self.teacher_client.patch(
            f"{API}/course/{self.course_id}",
            {"name": "Taken over"},
            format="json",
        )
        self.assertIn(response.status_code, (403, 404), response.content)
        self.assertEqual(Course.objects.get(pk=self.course_id).name, "School A Biology")


class SchoolAdminSeesRemovedTeachersNewDataTests(TeacherRemovalBase):
    """Reverse direction: the school keeps visibility of the teacher after removal.

    Once removed, the teacher is individual-track again and builds a private
    course. School A's admin should not be able to see that course's students.
    """

    def setUp(self):
        super().setUp()
        self.remove_teacher()
        teacher_client = jwt_client(self.teacher.email)
        response = teacher_client.post(
            f"{API}/sessions", {"name": "My private term"}, format="json"
        )
        assert response.status_code == 201, response.content
        response = teacher_client.post(
            f"{API}/course",
            {"name": "Private tutoring", "session": response.data["id"]},
            format="json",
        )
        assert response.status_code == 201, response.content
        response = teacher_client.post(
            f"{API}/course/{response.data['id']}/students",
            {"email": "private-pupil@h38.test"},
            format="json",
        )
        assert response.status_code == 200, response.content
        self.private_student = CustomUser.objects.get(email="private-pupil@h38.test")

    def test_school_admin_cannot_read_the_removed_teachers_private_student(self):
        response = self.admin_client.get(f"{API}/users/{self.private_student.id}")
        self.assertEqual(response.status_code, 404, response.content)

    def test_private_student_is_not_stamped_with_the_school(self):
        # _create_pending_student copies course.teacher.school onto a new
        # student, so the stale link leaks into the teacher's private roster.
        self.assertNotEqual(self.private_student.school_id, self.school.id)

    def test_school_admin_student_dashboard_omits_the_private_student(self):
        response = self.admin_client.get(f"{API}/school-admin/dashboard/students")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn(str(self.private_student.id), response.content.decode())
        self.assertNotIn(self.private_student.email, response.content.decode())


class LicensedTeacherKeepsLegitimateAccessTests(TeacherRemovalBase):
    """Positive controls: the tightened scoping must not lock out anyone it
    should not. Without these, a queryset that returned nothing at all would
    pass every 'cannot' test above."""

    def test_licensed_teacher_can_read_list_and_enrol_into_the_school_course(self):
        client = jwt_client(self.teacher.email)
        response = client.get(f"{API}/course/{self.course_id}")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(str(response.data["id"]), str(self.course_id))

        response = client.get(f"{API}/course", {"page_size": 100})
        self.assertIn(str(self.course_id), response.content.decode())

        response = client.get(f"{API}/users/{self.student.id}")
        self.assertEqual(response.status_code, 200, response.content)

        response = client.post(
            f"{API}/course/{self.course_id}/students",
            {"email": "another-pupil@h38.test"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_another_teacher_in_the_same_school_still_cannot_see_the_course(self):
        other = make_user("colleague@h38.test", UserTypes.TEACHER)
        LicenseSubscription.objects.filter(pk=self.license.pk).update(max_seats=5)
        response = self.admin_client.post(
            f"{API}/license-subscriptions/{self.license.id}/add_teachers",
            {"teacher_emails": [other.email]},
            format="json",
        )
        self.assertEqual(response.data["successful"], 1, response.content)
        response = jwt_client(other.email).get(f"{API}/course/{self.course_id}")
        self.assertEqual(response.status_code, 404, response.content)


class IndividualCoursesSurviveSchoolMembershipTests(TestCase):
    """A course in an INDIVIDUAL session belongs to the teacher whatever
    their school status - joining and leaving a school must not move it."""

    def test_private_course_stays_reachable_through_join_and_removal(self):
        plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="H38 License Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
        )
        school = School.objects.create(name="School Solo H38")
        admin = make_user("admin-solo@h38.test", UserTypes.SCHOOL_ADMIN, school)
        lic = LicenseSubscription.objects.create(
            school=school,
            admin_user=admin,
            plan=plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
        )
        teacher = make_user("solo@h38.test", UserTypes.TEACHER)
        client = jwt_client(teacher.email)
        session = client.post(f"{API}/sessions", {"name": "Mine"}, format="json")
        self.assertEqual(session.status_code, 201, session.content)
        course = client.post(
            f"{API}/course",
            {"name": "Mine 101", "session": session.data["id"]},
            format="json",
        )
        self.assertEqual(course.status_code, 201, course.content)
        course_id = course.data["id"]

        admin_client = jwt_client(admin.email)
        joined = admin_client.post(
            f"{API}/license-subscriptions/{lic.id}/add_teachers",
            {"teacher_emails": [teacher.email]},
            format="json",
        )
        self.assertEqual(joined.data["successful"], 1, joined.content)
        self.assertEqual(
            jwt_client(teacher.email).get(f"{API}/course/{course_id}").status_code, 200
        )

        removed = admin_client.post(
            f"{API}/license-subscriptions/{lic.id}/remove_teachers",
            {"teacher_ids": [str(teacher.id)]},
            format="json",
        )
        self.assertEqual(removed.data["successful"], 1, removed.content)
        self.assertEqual(
            jwt_client(teacher.email).get(f"{API}/course/{course_id}").status_code, 200
        )


class MovedTeacherDoesNotDragOldCoursesToNewSchoolTests(TeacherRemovalBase):
    def test_school_a_course_stays_hidden_after_joining_school_b(self):
        self.remove_teacher()
        response = jwt_client(self.other_admin.email).post(
            f"{API}/license-subscriptions/{self.other_license.id}/add_teachers",
            {"teacher_emails": [self.teacher.email]},
            format="json",
        )
        self.assertEqual(response.data["successful"], 1, response.content)
        client = jwt_client(self.teacher.email)
        self.assertEqual(client.get(f"{API}/course/{self.course_id}").status_code, 404)
        self.assertEqual(client.get(f"{API}/users/{self.student.id}").status_code, 404)


class RemovalDoesNotClobberAnotherSchoolLinkTests(TeacherRemovalBase):
    def test_school_link_to_a_different_school_is_left_alone(self):
        from billing.license_service import LicenseSubscriptionService

        CustomUser.objects.filter(pk=self.teacher.pk).update(school=self.other_school)
        self.teacher.refresh_from_db()
        LicenseSubscriptionService.remove_teacher_from_license(
            self.license, self.teacher
        )
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.school_id, self.other_school.id)
        self.assertFalse(
            SchoolCreditAllocation.objects.filter(
                license_subscription=self.license, user=self.teacher, is_active=True
            ).exists()
        )
