# import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment
from billing.models import (
    BillingInterval,
    CreditBucket,
    CreditBucketType,
    CreditUsageLog,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    SchoolCreditAllocation,
    SubscriptionPlan,
)
from classrooms.models import (  # EnrollmentStatusType,
    Course,
    School,
    Session,
    StudentCourse,
)
from users.models import UserTypes

User = get_user_model()


class ClassroomBaseAPITest(APITestCase):
    def setUp(self):
        # Clear cache before each test
        cache.clear()

        # Mock delete_pattern since it's a django-redis specific method
        # and might not be available in the test cache backend.
        if not hasattr(cache, "delete_pattern"):
            cache.delete_pattern = lambda x: None

        # Create a superuser
        self.superadmin = User.objects.create_superuser(
            email="superadmin@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Super",
            last_name="Admin",
        )
        self.superadmin.user_type = UserTypes.SUPER_ADMIN
        self.superadmin.is_active = True
        self.superadmin.save()

        # Create teachers
        self.teacher1 = User.objects.create_user(
            email="teacher1@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="One",
        )
        self.teacher1.user_type = UserTypes.TEACHER
        self.teacher1.is_active = True
        self.teacher1.save()

        self.teacher2 = User.objects.create_user(
            email="teacher2@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="Two",
        )
        self.teacher2.user_type = UserTypes.TEACHER
        self.teacher2.is_active = True
        self.teacher2.save()

        # Create a school
        self.school = School.objects.create(name="Central High")

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def _log_tokens(self, teacher, amount, course=None, school=None):
        """Directly insert a CreditUsageLog row, bypassing consume_credits()
        - lets tests set an explicit `school` snapshot independently of the
        teacher's current `.school`, to simulate usage recorded before a
        since-happened school transfer."""
        bucket = CreditBucket.objects.create(
            wallet=teacher.credit_wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=amount,
            used_credits=amount,
        )
        return CreditUsageLog.record(
            wallet=teacher.credit_wallet,
            bucket=bucket,
            amount=amount,
            course=course,
            school=school,
        )


class SchoolViewSetTest(ClassroomBaseAPITest):
    def test_list_schools_superadmin_caching_and_invalidation(self):
        self.authenticate(self.superadmin)
        url = reverse("school-list")

        # Mock cache.delete_pattern to simulate invalidation
        with patch.object(cache, "delete_pattern") as mock_delete:
            # First call caches the response
            response1 = self.client.get(url)
            self.assertEqual(len(response1.data["results"]), 1)

            # Create a new school via API
            self.client.post(url, {"name": "New API School"})

            # Check if delete_pattern was called
            mock_delete.assert_called()

            # Manually clear cache to simulate invalidation effect
            cache.clear()

            # Second call should reflect the new school
            response2 = self.client.get(url)
            self.assertEqual(len(response2.data["results"]), 2)

    def test_list_schools_teacher_denied(self):
        # SchoolViewSet is superadmin-only: teachers (and school admins)
        # must not be able to browse every school's data.
        self.authenticate(self.teacher1)
        url = reverse("school-list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_list_schools_student_denied(self):
        student = User.objects.create_user(
            email="student1@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Student",
            last_name="One",
        )
        student.user_type = UserTypes.STUDENT
        student.is_active = True
        student.save()

        self.authenticate(student)
        url = reverse("school-list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_school_detail_session_breakdown_groups_by_session_not_teacher(self):
        self.teacher1.school = self.school
        self.teacher1.save()
        self.teacher2.school = self.school
        self.teacher2.save()

        # A SCHOOL-owned session shared by both teachers, each with their
        # own course under it.
        school_session = Session.objects.create(
            name="2024/2025", owner_type="SCHOOL", school=self.school
        )
        Course.objects.create(
            name="Math", teacher=self.teacher1, session=school_session
        )
        Course.objects.create(
            name="Science", teacher=self.teacher2, session=school_session
        )

        # A SCHOOL session nobody has attached a course to yet.
        empty_school_session = Session.objects.create(
            name="2025/2026", owner_type="SCHOOL", school=self.school
        )

        # teacher1 also has two of their own INDIVIDUAL sessions.
        fall_session = Session.objects.create(
            name="Fall 2024", owner_type="INDIVIDUAL", teacher=self.teacher1
        )
        Course.objects.create(
            name="History", teacher=self.teacher1, session=fall_session
        )
        spring_session = Session.objects.create(
            name="Spring 2025", owner_type="INDIVIDUAL", teacher=self.teacher1
        )

        self.authenticate(self.superadmin)
        url = reverse("school-detail", kwargs={"pk": self.school.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        breakdown = {
            row["session_id"]: row for row in response.data["session_breakdown"]
        }
        self.assertEqual(
            set(breakdown.keys()),
            {
                str(school_session.id),
                str(empty_school_session.id),
                str(fall_session.id),
                str(spring_session.id),
            },
        )

        # SCHOOL session with two courses: both teachers show up nested,
        # neither the session row itself nor either teacher entry repeats.
        school_row = breakdown[str(school_session.id)]
        self.assertEqual(school_row["owner_type"], "SCHOOL")
        self.assertEqual(
            {t["teacher_id"] for t in school_row["teachers"]},
            {str(self.teacher1.id), str(self.teacher2.id)},
        )

        # SCHOOL session with no courses yet: still visible, empty teachers.
        self.assertEqual(breakdown[str(empty_school_session.id)]["teachers"], [])

        # INDIVIDUAL sessions: one row each, one nested teacher each — this
        # is the accepted repetition (teacher1 appears in both rows), but
        # each row is still a single, non-duplicated session with a single
        # teacher entry rather than the old flat single-teacher-string shape.
        for session_id in (fall_session.id, spring_session.id):
            row = breakdown[str(session_id)]
            self.assertEqual(row["owner_type"], "INDIVIDUAL")
            self.assertEqual(len(row["teachers"]), 1)
            self.assertEqual(row["teachers"][0]["teacher_id"], str(self.teacher1.id))

        # Session-level totals equal the sum of the nested teacher entries.
        for row in breakdown.values():
            self.assertEqual(
                row["assignments"], sum(t["assignments"] for t in row["teachers"])
            )
            self.assertEqual(
                row["students"], sum(t["students"] for t in row["teachers"])
            )
            self.assertEqual(row["tokens"], sum(t["tokens"] for t in row["teachers"]))

    def test_tokens_used_stays_with_school_after_teacher_transfers(self):
        """
        The core regression test for the CreditUsageLog.school snapshot:
        a teacher's historical token usage must stay attributed to the
        school they belonged to when it happened, not follow them to a
        new school, and vice versa - a school's reported tokens_used must
        never silently include usage from before a teacher even joined.
        """
        school_a = self.school
        school_b = School.objects.create(name="School B")

        self.teacher1.school = school_a
        self.teacher1.save()

        old_session = Session.objects.create(
            name="Old Session", owner_type="INDIVIDUAL", teacher=self.teacher1
        )
        old_course = Course.objects.create(
            name="Old Course", teacher=self.teacher1, session=old_session
        )
        # Usage recorded while teacher1 was genuinely at school_a.
        self._log_tokens(self.teacher1, 1000, course=old_course, school=school_a)

        # Teacher transfers to school_b.
        self.teacher1.school = school_b
        self.teacher1.save()

        new_session = Session.objects.create(
            name="New Session", owner_type="INDIVIDUAL", teacher=self.teacher1
        )
        new_course = Course.objects.create(
            name="New Course", teacher=self.teacher1, session=new_session
        )
        # New usage recorded now that teacher1 is at school_b.
        self._log_tokens(self.teacher1, 400, course=new_course, school=school_b)

        self.authenticate(self.superadmin)

        # --- School A: retains its historical 1000, even though teacher1
        # (and their old session) now belongs to School B by current
        # roster. The old session is invisible in School A's breakdown
        # (it's scoped by CURRENT teacher.school), so the 1000 shows up as
        # unattributed rather than under any session row - and critically,
        # it must NOT just vanish or get double-counted.
        resp_a = self.client.get(reverse("school-detail", kwargs={"pk": school_a.id}))
        self.assertEqual(resp_a.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_a.data["tokens_used"], 1000)
        self.assertEqual(resp_a.data["session_breakdown"], [])
        self.assertEqual(resp_a.data["tokens_unattributed"], 1000)

        # --- School B: only the NEW 400 counts as tokens_used (the old
        # 1000 must not retroactively follow the teacher here). The old
        # session DOES now appear in School B's breakdown (current roster
        # says teacher1 belongs to B), but with 0 tokens, since that usage
        # was never actually spent under School B.
        resp_b = self.client.get(reverse("school-detail", kwargs={"pk": school_b.id}))
        self.assertEqual(resp_b.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_b.data["tokens_used"], 400)
        breakdown_b = {
            row["session_id"]: row for row in resp_b.data["session_breakdown"]
        }
        self.assertEqual(
            set(breakdown_b.keys()), {str(old_session.id), str(new_session.id)}
        )
        self.assertEqual(breakdown_b[str(old_session.id)]["tokens"], 0)
        self.assertEqual(breakdown_b[str(new_session.id)]["tokens"], 400)
        self.assertEqual(resp_b.data["tokens_unattributed"], 0)

        # --- list() and admin_summary() must agree with retrieve() on the
        # per-school totals.
        list_resp = self.client.get(reverse("school-list"))
        list_by_id = {row["id"]: row for row in list_resp.data["results"]}
        self.assertEqual(list_by_id[str(school_a.id)]["tokens_used"], 1000)
        self.assertEqual(list_by_id[str(school_b.id)]["tokens_used"], 400)

        admin = User.objects.create_user(
            email="school-b-admin@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="B",
            last_name="Admin",
        )
        admin.user_type = UserTypes.SCHOOL_ADMIN
        admin.school = school_b
        admin.is_active = True
        admin.save()
        admin_summary_resp = self.client.get(reverse("school-admins-summary"))
        admin_row = next(
            row
            for row in admin_summary_resp.data["results"]
            if row["id"] == str(admin.id)
        )
        self.assertEqual(admin_row["tokens_used"], 400)


class TeacherSummaryEndpointTest(ClassroomBaseAPITest):
    def setUp(self):
        super().setUp()
        self.teacher1.school = self.school
        self.teacher1.save()

        self.session_a = Session.objects.create(
            name="Session A", owner_type="INDIVIDUAL", teacher=self.teacher1
        )
        self.course_a = Course.objects.create(
            name="Course A", teacher=self.teacher1, session=self.session_a
        )
        self.session_b = Session.objects.create(
            name="Session B", owner_type="INDIVIDUAL", teacher=self.teacher1
        )
        self.course_b = Course.objects.create(
            name="Course B", teacher=self.teacher1, session=self.session_b
        )

        Assignment.objects.create(course=self.course_a)

        student = User.objects.create_user(
            email="student.teachersummary@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Stu",
            last_name="Dent",
        )
        student.user_type = UserTypes.STUDENT
        student.is_active = True
        student.save()
        StudentCourse.objects.create(student=student, course=self.course_a)

        # 1000 tokens inside session_a, 500 inside session_b (a *different*
        # session), 300 with no course context at all (e.g. custom AI
        # chat) — full personal total is 1800.
        self._log_tokens(self.teacher1, 1000, course=self.course_a)
        self._log_tokens(self.teacher1, 500, course=self.course_b)
        self._log_tokens(self.teacher1, 300, course=None)

        self.url = reverse("school-teacher-summary")

    def _row_for(self, response, teacher):
        return next(
            row for row in response.data["results"] if row["id"] == str(teacher.id)
        )

    def test_tokens_used_is_full_personal_total_without_session_filter(self):
        self.authenticate(self.superadmin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        row = self._row_for(response, self.teacher1)
        self.assertEqual(row["tokens_used"], 1800)
        self.assertEqual(row["tokens_used_outside_session"], 0)
        self.assertEqual(row["assignments"], 1)
        self.assertEqual(row["students"], 1)

    def test_tokens_used_scoped_to_session_when_filtered(self):
        self.authenticate(self.superadmin)
        response = self.client.get(self.url, {"session_id": str(self.session_a.id)})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        row = self._row_for(response, self.teacher1)
        # Only course_a's 1000 tokens are inside session_a.
        self.assertEqual(row["tokens_used"], 1000)
        # The other 800 (session_b's 500 + the 300 with no course at all)
        # is reported, not silently dropped.
        self.assertEqual(row["tokens_used_outside_session"], 800)
        # tokens_used + tokens_used_outside_session always reconstructs the
        # teacher's full personal total.
        self.assertEqual(row["tokens_used"] + row["tokens_used_outside_session"], 1800)
        # assignments/students were already session-scoped before this fix
        # and must remain so.
        self.assertEqual(row["assignments"], 1)
        self.assertEqual(row["students"], 1)

    def test_tokens_used_scoped_to_other_session_excludes_everything(self):
        self.authenticate(self.superadmin)
        response = self.client.get(self.url, {"session_id": str(self.session_b.id)})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        row = self._row_for(response, self.teacher1)
        self.assertEqual(row["tokens_used"], 500)
        self.assertEqual(row["tokens_used_outside_session"], 1300)
        self.assertEqual(row["assignments"], 0)
        self.assertEqual(row["students"], 0)

    def test_ordering_by_students_does_not_500(self):
        # Regression test: order_map previously pointed "students" at a
        # nonexistent annotation name ("students_count" instead of
        # "student_count"), which raised FieldError -> 500.
        self.authenticate(self.superadmin)
        response = self.client.get(self.url, {"ordering": "students"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(self.url, {"ordering": "-students"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_requires_superadmin(self):
        self.authenticate(self.teacher1)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_malformed_school_id_returns_400_not_500(self):
        # Regression test: filtering directly on a malformed UUID used to
        # raise Django's ValidationError deep in the ORM, which DRF's
        # exception handler doesn't translate to 400 - it surfaced as an
        # unhandled 500.
        self.authenticate(self.superadmin)
        response = self.client.get(self.url, {"school_id": "not-a-uuid"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("school_id", response.data)

    def test_malformed_session_id_returns_400_not_500(self):
        self.authenticate(self.superadmin)
        response = self.client.get(self.url, {"session_id": "still-not-a-uuid"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("session_id", response.data)

    def test_well_formed_but_nonexistent_school_id_returns_empty_page(self):
        # A syntactically valid UUID that doesn't match any School is a
        # normal empty filter result, not an error - 404 is for detail
        # lookups, not list filters.
        self.authenticate(self.superadmin)
        response = self.client.get(
            self.url, {"school_id": "00000000-0000-0000-0000-000000000000"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"], [])

    def test_well_formed_but_nonexistent_session_id_returns_empty_stats(self):
        self.authenticate(self.superadmin)
        response = self.client.get(
            self.url, {"session_id": "00000000-0000-0000-0000-000000000000"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        row = self._row_for(response, self.teacher1)
        self.assertEqual(row["assignments"], 0)
        self.assertEqual(row["students"], 0)
        self.assertEqual(row["tokens_used"], 0)
        self.assertEqual(row["tokens_used_outside_session"], 1800)


class MonthlyTokenUsageEndpointTest(ClassroomBaseAPITest):
    def setUp(self):
        super().setUp()
        self.teacher1.school = self.school
        self.teacher1.save()
        self.url = reverse("school-monthly-token-usage")

    def test_malformed_school_id_returns_400_not_500(self):
        self.authenticate(self.superadmin)
        response = self.client.get(self.url, {"school_id": "not-a-uuid"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("school_id", response.data)

    def test_nonexistent_school_id_returns_404(self):
        self.authenticate(self.superadmin)
        response = self.client.get(
            self.url, {"school_id": "00000000-0000-0000-0000-000000000000"}
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_school_with_departed_teacher_still_shows_historical_usage(self):
        """
        Regression test: this endpoint used to resolve "which teachers
        currently belong to this school" first and 404 ("No teachers
        found") if that set was empty - so a school whose only
        usage-generating teacher had since left (or transferred) would
        404 even though its token history is completely real. Scoping
        directly by CreditUsageLog.school (the snapshot) fixes this.
        """
        self._log_tokens(self.teacher1, 750, school=self.school)

        # Teacher leaves the school entirely.
        self.teacher1.school = None
        self.teacher1.save()

        self.authenticate(self.superadmin)
        response = self.client.get(self.url, {"school_id": str(self.school.id)})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        total_tokens = sum(row["tokens"] for row in response.data)
        self.assertEqual(total_tokens, 750)

    def test_usage_after_transfer_does_not_count_toward_old_school(self):
        other_school = School.objects.create(name="Other School")
        self._log_tokens(self.teacher1, 750, school=self.school)

        self.teacher1.school = other_school
        self.teacher1.save()
        self._log_tokens(self.teacher1, 250, school=other_school)

        self.authenticate(self.superadmin)

        response_original = self.client.get(
            self.url, {"school_id": str(self.school.id)}
        )
        self.assertEqual(sum(row["tokens"] for row in response_original.data), 750)

        response_new = self.client.get(self.url, {"school_id": str(other_school.id)})
        self.assertEqual(sum(row["tokens"] for row in response_new.data), 250)

    def test_school_admin_sees_own_school_without_school_id_param(self):
        admin = User.objects.create_user(
            email="monthly-admin@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="M",
            last_name="Admin",
        )
        admin.user_type = UserTypes.SCHOOL_ADMIN
        admin.school = self.school
        admin.is_active = True
        admin.save()
        self._log_tokens(self.teacher1, 300, school=self.school)

        self.authenticate(admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(sum(row["tokens"] for row in response.data), 300)


class SessionViewSetTest(ClassroomBaseAPITest):
    def test_session_cache_invalidation_on_creation(self):
        self.authenticate(self.teacher1)
        url = reverse("session-list")

        # Mock cache.delete_pattern to simulate invalidation
        with patch.object(cache, "delete_pattern") as mock_delete:
            # First GET call
            response1 = self.client.get(url)
            self.assertEqual(len(response1.data["results"]), 0)

            # Create a new session via API
            new_session_data = {"name": "Spring 2025"}
            create_response = self.client.post(url, new_session_data)
            self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)

            # Check if delete_pattern was called
            mock_delete.assert_called()

            # Manually clear cache to simulate invalidation effect
            cache.clear()

            # Second GET call should reflect new session
            response2 = self.client.get(url)
            self.assertEqual(len(response2.data["results"]), 1)

    def test_session_isolation(self):
        # Teacher 1 creates a session
        Session.objects.create(name="T1 Session", teacher=self.teacher1)

        # Teacher 2 should not see Teacher 1's session
        self.authenticate(self.teacher2)
        url = reverse("session-list")
        response = self.client.get(url)
        self.assertEqual(len(response.data["results"]), 0)

    def test_hacker_access_others_session(self):
        t1_session = Session.objects.create(name="T1 Session", teacher=self.teacher1)

        self.authenticate(self.teacher2)
        url = reverse("session-detail", kwargs={"pk": t1_session.id})

        # Try to retrieve T1's session
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class CourseViewSetTest(ClassroomBaseAPITest):
    def setUp(self):
        super().setUp()
        self.session = Session.objects.create(name="Session 1", teacher=self.teacher1)

    def test_course_cache_invalidation_on_creation(self):
        self.authenticate(self.teacher1)
        url = reverse("course-list")

        # Mock cache.delete_pattern to simulate invalidation
        with patch.object(cache, "delete_pattern") as mock_delete:
            # First GET call
            response1 = self.client.get(url)
            self.assertEqual(len(response1.data["results"]), 0)

            # Create a new course via API
            new_course_data = {"name": "Science", "session": str(self.session.id)}
            create_response = self.client.post(url, new_course_data)
            self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)

            # Check if delete_pattern was called
            mock_delete.assert_called()

            # Manually clear cache to simulate invalidation
            cache.clear()

            # Second GET call should reflect the new course
            response2 = self.client.get(url)
            self.assertEqual(len(response2.data["results"]), 1)

    def test_hacker_enroll_same_student_twice(self):
        course = Course.objects.create(
            name="Math", teacher=self.teacher1, session=self.session
        )
        self.authenticate(self.teacher1)
        url = reverse("course-students", kwargs={"pk": course.id})

        email = "student@example.com"
        # First enrollment
        self.client.post(url, {"email": email})

        # Second enrollment attempt
        response = self.client.post(url, {"email": email})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already enrolled", str(response.data.get("detail", "")))

    @patch("classrooms.views.send_email_task.delay")
    def test_teacher_can_invite_multiple_pending_students_by_email(
        self, mock_send_email
    ):
        course = Course.objects.create(
            name="Physics", teacher=self.teacher1, session=self.session
        )
        self.authenticate(self.teacher1)
        url = reverse("course-students", kwargs={"pk": course.id})

        first_response = self.client.post(url, {"email": "pending1@example.com"})
        second_response = self.client.post(url, {"email": "pending2@example.com"})

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(mock_send_email.call_count, 2)

    @override_settings(
        FRONTEND_DOMAIN="teacher.example.test",
        STUDENT_FRONTEND_DOMAIN="student.example.test",
    )
    @patch("classrooms.views.send_email_task.delay")
    def test_new_student_invite_link_uses_student_frontend_domain(
        self, mock_send_email
    ):
        """A brand-new student (no CustomUser row yet) gets a registration
        link pointed at the student app, not the teacher app."""
        course = Course.objects.create(
            name="Chemistry", teacher=self.teacher1, session=self.session
        )
        self.authenticate(self.teacher1)
        url = reverse("course-students", kwargs={"pk": course.id})

        response = self.client.post(url, {"email": "brandnew@example.com"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        merge_data = mock_send_email.call_args.kwargs["merge_data"]
        self.assertIn("student.example.test", merge_data["activation_url"])
        self.assertNotIn("teacher.example.test", merge_data["activation_url"])

    @override_settings(
        FRONTEND_DOMAIN="teacher.example.test",
        STUDENT_FRONTEND_DOMAIN="student.example.test",
    )
    @patch("classrooms.views.send_email_task.delay")
    def test_existing_inactive_student_invite_link_uses_student_frontend_domain(
        self, mock_send_email
    ):
        """An existing-but-not-yet-activated student invited into a second
        course also gets a student-app link, not a teacher-app one."""
        course_a = Course.objects.create(
            name="Biology", teacher=self.teacher1, session=self.session
        )
        course_b = Course.objects.create(
            name="Geography", teacher=self.teacher1, session=self.session
        )
        self.authenticate(self.teacher1)
        url_a = reverse("course-students", kwargs={"pk": course_a.id})
        url_b = reverse("course-students", kwargs={"pk": course_b.id})

        email = "still.pending@example.com"
        first = self.client.post(url_a, {"email": email})
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        mock_send_email.reset_mock()

        second = self.client.post(url_b, {"email": email})
        self.assertEqual(second.status_code, status.HTTP_200_OK)

        merge_data = mock_send_email.call_args.kwargs["merge_data"]
        self.assertIn("student.example.test", merge_data["activation_url"])
        self.assertNotIn("teacher.example.test", merge_data["activation_url"])

    @override_settings(
        FRONTEND_DOMAIN="teacher.example.test",
        STUDENT_FRONTEND_DOMAIN="student.example.test",
    )
    @patch("classrooms.views.send_email_task.delay")
    def test_existing_active_student_added_login_link_uses_student_frontend_domain(
        self, mock_send_email
    ):
        """A student who is already active gets a login link pointed at the
        student app, not the teacher app."""
        student = User.objects.create_user(
            email="already.active@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Already",
            last_name="Active",
        )
        student.user_type = UserTypes.STUDENT
        student.is_active = True
        student.save()

        course = Course.objects.create(
            name="History", teacher=self.teacher1, session=self.session
        )
        self.authenticate(self.teacher1)
        url = reverse("course-students", kwargs={"pk": course.id})

        response = self.client.post(url, {"email": student.email})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        merge_data = mock_send_email.call_args.kwargs["merge_data"]
        self.assertIn("student.example.test", merge_data["content"])
        self.assertNotIn("teacher.example.test", merge_data["content"])

    @patch("classrooms.views.send_email_task.delay")
    def test_teacher_cannot_remove_student_from_another_teachers_course(
        self, mock_send_email
    ):
        """remove_student used to wrap self.get_object() itself inside its
        `except Exception`, downgrading the Http404 that get_queryset()'s
        teacher-scoping raises for another teacher's course into a generic
        500 - it must come back as a real 404 instead (same reasoning as
        the students() action's ownership check, see the comment there)."""
        course = Course.objects.create(
            name="Astronomy", teacher=self.teacher1, session=self.session
        )
        student = User.objects.create_user(
            email="astro.student@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Astro",
            last_name="Student",
        )
        student.user_type = UserTypes.STUDENT
        student.is_active = True
        student.save()
        StudentCourse.objects.create(student=student, course=course)

        self.authenticate(self.teacher2)
        url = reverse(
            "course-remove-student",
            kwargs={"pk": course.id, "student_id": student.id},
        )

        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(
            StudentCourse.objects.filter(student=student, course=course).exists()
        )

    def test_removing_a_student_not_enrolled_returns_400(self):
        """Same swallowed-exception bug, the other raise site: the
        ParseError for "not enrolled" must come back as a 400, not 500."""
        course = Course.objects.create(
            name="Zoology", teacher=self.teacher1, session=self.session
        )
        student = User.objects.create_user(
            email="not.enrolled@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Not",
            last_name="Enrolled",
        )
        student.user_type = UserTypes.STUDENT
        student.is_active = True
        student.save()

        self.authenticate(self.teacher1)
        url = reverse(
            "course-remove-student",
            kwargs={"pk": course.id, "student_id": student.id},
        )

        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not enrolled", str(response.data.get("detail", "")))

    def test_hacker_modify_others_course(self):
        t1_course = Course.objects.create(
            name="T1 Course", teacher=self.teacher1, session=self.session
        )

        self.authenticate(self.teacher2)
        url = reverse("course-detail", kwargs={"pk": t1_course.id})

        # Try to update T1's course
        response = self.client.patch(url, {"name": "Hacked"})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_hacker_malformed_data(self):
        self.authenticate(self.teacher1)
        url = reverse("course-list")

        # Send garbage data
        response = self.client.post(url, {"name": "", "session": "not-a-uuid"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class SessionLicenseGatingTest(ClassroomBaseAPITest):
    """
    Covers the school-license session-sharing feature: license teachers are
    blocked from creating their own sessions and instead see/use their
    school admin's shared SCHOOL sessions, while individual-subscription
    teachers are completely unaffected. Also covers the "removed from
    license" edge case where school_id stays set but is_under_license()
    correctly flips back to False.
    """

    def setUp(self):
        super().setUp()
        self.school_admin = User.objects.create_user(
            email="admin@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="School",
            last_name="Admin",
        )
        self.school_admin.user_type = UserTypes.SCHOOL_ADMIN
        self.school_admin.is_active = True
        self.school_admin.school = self.school
        self.school_admin.save()

        self.plan = SubscriptionPlan.objects.create(
            name="LICENSE_TEST_PLAN",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            interval=BillingInterval.MONTHLY,
            monthly_credits=20000,
            carry_over_percent=0,
            is_active=True,
        )
        now = timezone.now()
        self.license_sub = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.school_admin,
            plan=self.plan,
            contract_months=12,
            max_seats=10,
            billing_cycle_start=now,
            billing_cycle_end=now + timedelta(days=365),
            is_active=True,
            auto_renew=True,
        )

        # teacher1 is enrolled under the school's license.
        self.teacher1.school = self.school
        self.teacher1.save()
        self.allocation = SchoolCreditAllocation.objects.create(
            license_subscription=self.license_sub,
            user=self.teacher1,
            monthly_allocation=20000,
            is_active=True,
            next_credit_grant_at=now + timedelta(days=30),
        )

    def test_license_teacher_cannot_create_session(self):
        self.authenticate(self.teacher1)
        url = reverse("session-list")
        response = self.client.post(url, {"name": "Fall 2026"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_school_admin_session_visible_to_license_teacher(self):
        self.authenticate(self.school_admin)
        create_url = reverse("session-list")
        create_response = self.client.post(create_url, {"name": "Fall 2026"})
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)

        self.authenticate(self.teacher1)
        response = self.client.get(reverse("session-list"))
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["name"], "Fall 2026")

    def test_individual_teacher_unaffected_by_license_feature(self):
        # teacher2 has no school/license at all - unrelated to this fixture.
        self.authenticate(self.teacher2)
        url = reverse("session-list")
        response = self.client.post(url, {"name": "Fall 2026"})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        list_response = self.client.get(url)
        self.assertEqual(len(list_response.data["results"]), 1)

    def test_teacher_removed_from_license_regains_individual_sessions(self):
        # Simulate offboarding: allocation deactivated, but school_id is
        # never cleared anywhere in the codebase.
        self.allocation.is_active = False
        self.allocation.save()

        self.assertFalse(self.teacher1.is_under_license())

        self.authenticate(self.teacher1)
        url = reverse("session-list")
        response = self.client.post(url, {"name": "Personal Session"})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        list_response = self.client.get(url)
        self.assertEqual(len(list_response.data["results"]), 1)
        self.assertEqual(list_response.data["results"][0]["name"], "Personal Session")
