"""Adversarial tests for the classrooms tenancy boundary.

These are written from the attacker's side: each one is an attempt to read
or change something the actor must not be able to touch, and the assertion
is that the attempt FAILS. They deliberately go past the happy path -
guessed UUIDs, filter-parameter smuggling, status manipulation, ordering
and search injection, cache poisoning between users, and abuse of the bulk
and upload paths.

A test here passing means an attack was repelled. Where an assertion checks
a status code it also checks the DATA, because a leaking endpoint returns
200 just like a correct one.
"""

import io
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from billing.models import CreditBucket, CreditBucketType, CreditWallet
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

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

#: Cache invalidation in this app is done with `delete_pattern`, which ONLY
#: django-redis provides - `delete_cache_patterns` silently no-ops on any
#: other backend. Testing revocation on LocMem therefore proves nothing
#: about production, so the cache attacks below run against a real Redis on
#: a dedicated database number (15, not the app's 0) to stay clear of a
#: developer's or another test run's keys.
REDIS_CACHE = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://127.0.0.1:6379/15",
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
    }
}


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
class AttackBase(APITestCase):
    """Two schools, two teachers, two students, no overlap whatsoever.

    Anything an actor from school A can see that belongs to school B is a
    breach, with no legitimate explanation available.
    """

    def setUp(self):
        cache.clear()
        self.school_a = School.objects.create(name="School A")
        self.school_b = School.objects.create(name="School B")

        self.teacher_a = make_user("ta@a.test", UserTypes.TEACHER, self.school_a)
        self.teacher_b = make_user("tb@b.test", UserTypes.TEACHER, self.school_b)
        self.admin_a = make_user("aa@a.test", UserTypes.SCHOOL_ADMIN, self.school_a)
        self.admin_b = make_user("ab@b.test", UserTypes.SCHOOL_ADMIN, self.school_b)

        self.session_a = Session.objects.create(name="Fall A", teacher=self.teacher_a)
        self.session_b = Session.objects.create(name="Fall B", teacher=self.teacher_b)

        self.course_a = Course.objects.create(
            name="Secret A", teacher=self.teacher_a, session=self.session_a
        )
        self.course_b = Course.objects.create(
            name="Secret B", teacher=self.teacher_b, session=self.session_b
        )

        self.topic_a = Topic.objects.create(name="Topic A", course=self.course_a)
        self.topic_b = Topic.objects.create(name="Topic B", course=self.course_b)

        self.assignment_b = Assignment.objects.create(
            title="B homework",
            course=self.course_b,
            teacher=self.teacher_b,
            status=AssignmentStatus.PUBLISHED,
        )

        self.student_a = make_user("sa@a.test", UserTypes.STUDENT, self.school_a)
        self.student_b = make_user("sb@b.test", UserTypes.STUDENT, self.school_b)
        self.enrollment_a = StudentCourse.objects.create(
            student=self.student_a,
            course=self.course_a,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        self.enrollment_b = StudentCourse.objects.create(
            student=self.student_b,
            course=self.course_b,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )

    def fund(self, user):
        """Give a teacher credits.

        HasCreditBalance runs BEFORE the queryset scoping on the summary
        endpoint and denies with a 400, which would mask whether the
        authorization check works at all. Funding the wallet removes that
        mask so the test actually exercises the boundary.
        """
        wallet, _ = CreditWallet.objects.get_or_create(user=user)
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=100_000,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )

    def as_(self, user):
        cache.clear()
        self.client.force_authenticate(user)

    def ids(self, response):
        data = response.data
        rows = data.get("results", data) if isinstance(data, dict) else data
        return {str(row["id"]) for row in rows}


class CrossTenantReadAttacks(AttackBase):
    """Can an actor from school A see school B's rows, by any route?"""

    def test_teacher_cannot_list_another_teachers_courses(self):
        self.as_(self.teacher_a)
        found = self.ids(self.client.get(reverse("course-list")))
        self.assertNotIn(str(self.course_b.id), found)

    def test_teacher_cannot_retrieve_another_teachers_course_by_uuid(self):
        self.as_(self.teacher_a)
        response = self.client.get(
            reverse("course-detail", kwargs={"pk": self.course_b.id})
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_teacher_cannot_reach_another_teachers_topic(self):
        self.as_(self.teacher_a)
        response = self.client.get(
            reverse("topic-detail", kwargs={"pk": self.topic_b.id})
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_teacher_cannot_reach_another_teachers_enrollment(self):
        self.as_(self.teacher_a)
        response = self.client.get(
            reverse("student-course-detail", kwargs={"pk": self.enrollment_b.id})
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_student_cannot_see_another_students_enrollment(self):
        self.as_(self.student_a)
        found = self.ids(self.client.get(reverse("student-course-list")))
        self.assertEqual(found, {str(self.enrollment_a.id)})

    def test_student_cannot_see_another_schools_course(self):
        self.as_(self.student_a)
        found = self.ids(self.client.get(reverse("course-list")))
        self.assertNotIn(str(self.course_b.id), found)

    def test_student_cannot_see_another_schools_topics(self):
        self.as_(self.student_a)
        found = self.ids(self.client.get(reverse("topic-list")))
        self.assertNotIn(str(self.topic_b.id), found)

    def test_student_cannot_see_another_schools_session(self):
        self.as_(self.student_a)
        found = self.ids(self.client.get(reverse("session-list")))
        self.assertNotIn(str(self.session_b.id), found)

    def test_school_admin_cannot_read_another_schools_sessions(self):
        self.as_(self.admin_a)
        found = self.ids(self.client.get(reverse("session-list")))
        self.assertNotIn(str(self.session_b.id), found)

    def test_school_admin_is_not_a_superadmin(self):
        """SCHOOL_ADMIN must not reach the superadmin-only school registry."""
        self.as_(self.admin_a)
        self.assertEqual(
            self.client.get(reverse("school-list")).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_teacher_cannot_read_the_school_registry(self):
        self.as_(self.teacher_a)
        self.assertEqual(
            self.client.get(reverse("school-list")).status_code,
            status.HTTP_403_FORBIDDEN,
        )


class FilterParameterSmugglingAttacks(AttackBase):
    """Scoping must survive attacker-controlled query parameters.

    A filter backend runs AFTER get_queryset(), so a filterable field is
    only safe if the base queryset is already scoped. These probe that
    ordering: if scoping were applied by the filter instead, passing an
    explicit filter value would widen it.
    """

    def test_course_session_filter_cannot_reach_another_teachers_session(self):
        self.as_(self.teacher_a)
        response = self.client.get(
            reverse("course-list"), {"session": str(self.session_b.id)}
        )
        self.assertEqual(self.ids(response), set())

    def test_my_students_course_filter_cannot_reach_another_teachers_course(self):
        self.as_(self.teacher_a)
        response = self.client.get(
            reverse("student-course-my-students"),
            {"enrollments__course": str(self.course_b.id)},
        )
        self.assertEqual(self.ids(response), set())

    def test_my_students_session_filter_cannot_reach_another_teachers_session(self):
        self.as_(self.teacher_a)
        response = self.client.get(
            reverse("student-course-my-students"),
            {"enrollments__course__session": str(self.session_b.id)},
        )
        self.assertEqual(self.ids(response), set())

    def test_topic_course_name_filter_cannot_reach_another_teachers_topic(self):
        self.as_(self.teacher_a)
        response = self.client.get(
            reverse("topic-list"), {"course__name": self.course_b.name}
        )
        self.assertEqual(self.ids(response), set())

    def test_topic_search_cannot_reach_another_teachers_topic(self):
        self.as_(self.teacher_a)
        response = self.client.get(reverse("topic-list"), {"search": "Topic B"})
        self.assertEqual(self.ids(response), set())

    def test_course_search_cannot_reach_another_teachers_course(self):
        self.as_(self.teacher_a)
        response = self.client.get(reverse("course-list"), {"search": "Secret B"})
        self.assertEqual(self.ids(response), set())

    def test_malformed_ordering_does_not_500(self):
        self.as_(self.teacher_a)
        for value in ["../../etc/passwd", "teacher__password", "'; DROP TABLE--"]:
            response = self.client.get(reverse("course-list"), {"ordering": value})
            self.assertEqual(response.status_code, status.HTTP_200_OK, value)

    def test_malformed_filter_uuid_is_400_not_500(self):
        self.as_(self.teacher_a)
        response = self.client.get(reverse("course-list"), {"session": "not-a-uuid"})
        self.assertNotEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)


class EnrollmentStatusAttacks(AttackBase):
    """WITHDRAWN and PENDING students must lose course access everywhere.

    Course, session, topic and assignment reads must agree - a student
    locked out of one but not the others is still leaking.
    """

    def _set_status(self, new_status):
        self.enrollment_a.enrollment_status = new_status
        self.enrollment_a.save(update_fields=["enrollment_status"])

    def _visible_to_student_a(self):
        self.as_(self.student_a)
        return {
            "courses": self.ids(self.client.get(reverse("course-list"))),
            "topics": self.ids(self.client.get(reverse("topic-list"))),
            "sessions": self.ids(self.client.get(reverse("session-list"))),
            "assignments": self.ids(self.client.get(reverse("assignment-list"))),
        }

    def test_enrolled_student_sees_everything(self):
        self._set_status(EnrollmentStatusType.ENROLLED)
        visible = self._visible_to_student_a()
        self.assertIn(str(self.course_a.id), visible["courses"])
        self.assertIn(str(self.topic_a.id), visible["topics"])
        self.assertIn(str(self.session_a.id), visible["sessions"])

    def test_completed_student_keeps_their_history_everywhere(self):
        self._set_status(EnrollmentStatusType.COMPLETED)
        visible = self._visible_to_student_a()
        self.assertIn(str(self.course_a.id), visible["courses"])
        self.assertIn(str(self.topic_a.id), visible["topics"])
        self.assertIn(str(self.session_a.id), visible["sessions"])

    def test_withdrawn_student_loses_course_access(self):
        self._set_status(EnrollmentStatusType.WITHDRAWN)
        self.assertEqual(self._visible_to_student_a()["courses"], set())

    def test_withdrawn_student_loses_topic_access(self):
        self._set_status(EnrollmentStatusType.WITHDRAWN)
        self.assertEqual(self._visible_to_student_a()["topics"], set())

    def test_withdrawn_student_loses_session_access(self):
        self._set_status(EnrollmentStatusType.WITHDRAWN)
        self.assertEqual(self._visible_to_student_a()["sessions"], set())

    def test_withdrawn_student_loses_assignment_access(self):
        self._set_status(EnrollmentStatusType.WITHDRAWN)
        self.assertEqual(self._visible_to_student_a()["assignments"], set())

    def test_pending_student_loses_course_access(self):
        self._set_status(EnrollmentStatusType.PENDING)
        self.assertEqual(self._visible_to_student_a()["courses"], set())

    def test_pending_student_loses_topic_access(self):
        self._set_status(EnrollmentStatusType.PENDING)
        self.assertEqual(self._visible_to_student_a()["topics"], set())

    def test_pending_student_loses_session_access(self):
        self._set_status(EnrollmentStatusType.PENDING)
        self.assertEqual(self._visible_to_student_a()["sessions"], set())

    def test_withdrawn_student_cannot_retrieve_the_course_directly(self):
        self._set_status(EnrollmentStatusType.WITHDRAWN)
        self.as_(self.student_a)
        response = self.client.get(
            reverse("course-detail", kwargs={"pk": self.course_a.id})
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_student_cannot_reactivate_their_own_withdrawn_enrollment(self):
        """Self-service re-enrollment would undo a teacher's removal."""
        self._set_status(EnrollmentStatusType.WITHDRAWN)
        self.as_(self.student_a)
        self.client.patch(
            reverse("student-course-detail", kwargs={"pk": self.enrollment_a.id}),
            {"enrollment_status": EnrollmentStatusType.ENROLLED},
            format="json",
        )
        self.enrollment_a.refresh_from_db()
        self.assertEqual(
            self.enrollment_a.enrollment_status, EnrollmentStatusType.WITHDRAWN
        )

    def test_student_cannot_award_themselves_a_final_grade(self):
        self.as_(self.student_a)
        self.client.patch(
            reverse("student-course-detail", kwargs={"pk": self.enrollment_a.id}),
            {"final_grade": "100.00"},
            format="json",
        )
        self.enrollment_a.refresh_from_db()
        self.assertIsNone(self.enrollment_a.final_grade)


class WriteAndPrivilegeAttacks(AttackBase):
    """Attempts to change something the actor does not own."""

    def test_student_cannot_create_a_course(self):
        self.as_(self.student_a)
        response = self.client.post(
            reverse("course-list"), {"name": "Mine", "session": str(self.session_a.id)}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_student_cannot_create_a_topic(self):
        self.as_(self.student_a)
        response = self.client.post(
            reverse("topic-list"), {"name": "X", "course": str(self.course_a.id)}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_student_cannot_delete_a_topic(self):
        self.as_(self.student_a)
        response = self.client.delete(
            reverse("topic-detail", kwargs={"pk": self.topic_a.id})
        )
        self.assertIn(
            response.status_code,
            (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND),
        )
        self.assertTrue(Topic.objects.filter(pk=self.topic_a.id).exists())

    def test_teacher_cannot_delete_another_teachers_topic(self):
        self.as_(self.teacher_a)
        self.client.delete(reverse("topic-detail", kwargs={"pk": self.topic_b.id}))
        self.assertTrue(Topic.objects.filter(pk=self.topic_b.id).exists())

    def test_teacher_cannot_enroll_into_another_teachers_course(self):
        self.as_(self.teacher_a)
        response = self.client.post(
            reverse("course-students", kwargs={"pk": self.course_b.id}),
            {"email": "victim@x.test"},
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_teacher_cannot_bulk_add_into_another_teachers_course(self):
        self.as_(self.teacher_a)
        response = self.client.post(
            reverse("course-bulk-add-students", kwargs={"pk": self.course_b.id}),
            {"raw_data": "Mal,Actor"},
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(StudentCourse.objects.filter(course=self.course_b).count(), 1)

    def test_teacher_cannot_direct_add_into_another_teachers_course(self):
        self.as_(self.teacher_a)
        response = self.client.post(
            reverse("course-direct-add-student", kwargs={"pk": self.course_b.id}),
            {"first_name": "Mal", "last_name": "Actor"},
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_teacher_cannot_remove_a_student_from_another_teachers_course(self):
        self.as_(self.teacher_a)
        self.client.delete(
            reverse(
                "course-remove-student",
                kwargs={"pk": self.course_b.id, "student_id": self.student_b.id},
            )
        )
        self.assertTrue(StudentCourse.objects.filter(pk=self.enrollment_b.pk).exists())

    def test_teacher_cannot_attach_a_course_to_another_teachers_session(self):
        self.as_(self.teacher_a)
        response = self.client.post(
            reverse("course-list"),
            {"name": "Trespass", "session": str(self.session_b.id)},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Course.objects.filter(name="Trespass").exists())

    def test_teacher_cannot_create_topics_on_another_teachers_course(self):
        self.as_(self.teacher_a)
        response = self.client.post(
            reverse("course-create-topics", kwargs={"pk": self.course_b.id}),
            ["Injected"],
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(Topic.objects.filter(name="Injected").exists())

    def test_teacher_cannot_generate_a_summary_for_another_teachers_student(self):
        self.fund(self.teacher_a)
        self.as_(self.teacher_a)
        response = self.client.get(
            reverse("course-student-summary", kwargs={"pk": self.course_b.id}),
            {"student_id": str(self.student_b.id)},
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_student_summary_rejects_a_student_not_in_the_course(self):
        """A teacher's OWN course, but a student who isn't on its roster -
        the summary is billed, so it must not run for an arbitrary uuid."""
        self.fund(self.teacher_a)
        self.as_(self.teacher_a)
        response = self.client.get(
            reverse("course-student-summary", kwargs={"pk": self.course_a.id}),
            {"student_id": str(self.student_b.id)},
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_unauthenticated_requests_are_refused_everywhere(self):
        self.client.force_authenticate(None)
        for name in [
            "course-list",
            "topic-list",
            "session-list",
            "student-course-list",
            "school-list",
        ]:
            response = self.client.get(reverse(name))
            self.assertIn(
                response.status_code,
                (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
                name,
            )

    def test_a_random_uuid_is_a_404_not_a_500(self):
        self.as_(self.teacher_a)
        stranger = uuid.uuid4()
        for name in ["course-detail", "topic-detail", "student-course-detail"]:
            response = self.client.get(reverse(name, kwargs={"pk": stranger}))
            self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, name)


@override_settings(CACHES=REDIS_CACHE)
class CacheIsolationAttacks(AttackBase):
    """UserCacheMixin caches list/retrieve responses. A key collision
    between two users would serve one tenant's rows to another, and a
    cached response that outlives a revocation is a bypass in its own
    right - the teacher removes the student and the student keeps reading.

    Runs on real Redis: see REDIS_CACHE above for why LocMem cannot test
    this.
    """

    def setUp(self):
        super().setUp()
        cache.clear()

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def test_two_teachers_do_not_share_a_cached_course_list(self):
        self.as_(self.teacher_a)
        a_ids = self.ids(self.client.get(reverse("course-list")))
        # Same URL, same (absent) query params, different user - the only
        # thing separating the two cache entries is the user id in the key.
        self.client.force_authenticate(self.teacher_b)
        b_ids = self.ids(self.client.get(reverse("course-list")))

        self.assertEqual(a_ids, {str(self.course_a.id)})
        self.assertEqual(b_ids, {str(self.course_b.id)})

    def test_a_students_cached_list_is_not_served_to_a_teacher(self):
        self.as_(self.student_a)
        self.client.get(reverse("course-list"))
        self.client.force_authenticate(self.teacher_b)
        self.assertEqual(
            self.ids(self.client.get(reverse("course-list"))),
            {str(self.course_b.id)},
        )

    def test_withdrawal_invalidates_the_students_cached_course_list(self):
        """Cached access outliving a revocation is a real bypass: the
        teacher removes the student, and the student keeps reading."""
        self.as_(self.student_a)
        self.assertIn(
            str(self.course_a.id), self.ids(self.client.get(reverse("course-list")))
        )

        self.enrollment_a.withdrawn()

        self.client.force_authenticate(self.student_a)
        self.assertEqual(self.ids(self.client.get(reverse("course-list"))), set())

    def test_my_courses_cache_is_invalidated_on_withdrawal(self):
        self.as_(self.student_a)
        self.assertEqual(len(self.client.get(reverse("course-my-courses")).data), 1)

        self.enrollment_a.withdrawn()

        self.client.force_authenticate(self.student_a)
        self.assertEqual(len(self.client.get(reverse("course-my-courses")).data), 0)

    def test_retrieve_cache_is_keyed_per_user(self):
        self.as_(self.teacher_b)
        self.client.get(reverse("course-detail", kwargs={"pk": self.course_b.id}))

        self.client.force_authenticate(self.teacher_a)
        response = self.client.get(
            reverse("course-detail", kwargs={"pk": self.course_b.id})
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class BulkAndUploadAbuseAttacks(AttackBase):
    """Abuse of the roster import: volume, encoding, and content."""

    def setUp(self):
        super().setUp()
        self.url = reverse("course-bulk-add-students", kwargs={"pk": self.course_a.pk})

    def test_a_zip_bomb_shaped_file_is_refused_by_size(self):
        from classrooms.services import MAX_FILE_BYTES

        payload = io.BytesIO(b"x,y\n" * (MAX_FILE_BYTES // 4 + 10))
        payload.name = "roster.csv"
        self.as_(self.teacher_a)
        response = self.client.post(self.url, {"file": payload}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(StudentCourse.objects.filter(course=self.course_a).count(), 1)

    def test_a_binary_file_renamed_to_csv_is_a_400(self):
        payload = io.BytesIO(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe\xfd")
        payload.name = "roster.csv"
        self.as_(self.teacher_a)
        response = self.client.post(self.url, {"file": payload}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_row_flood_is_refused_before_any_user_is_created(self):
        from classrooms.services import MAX_ROWS

        before = User.objects.count()
        self.as_(self.teacher_a)
        response = self.client.post(
            self.url,
            {"raw_data": "\n".join(f"A{i},B{i}" for i in range(MAX_ROWS + 5))},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(User.objects.count(), before)

    def test_a_csv_formula_injection_payload_is_stored_inert(self):
        """A cell starting with = is a spreadsheet formula when re-exported.

        It must be stored as the literal name it is, not executed or
        stripped into something else.
        """
        self.as_(self.teacher_a)
        self.client.post(self.url, {"raw_data": "=1+1,DDE|calc"})
        self.assertTrue(User.objects.filter(first_name="=1+1").exists())

    def test_bulk_import_cannot_hijack_an_existing_teacher_account(self):
        """A roster row carrying a teacher's email must not enroll that
        teacher as a student. The single-add path always refused this; the
        bulk path did not."""
        self.as_(self.teacher_a)
        response = self.client.post(
            self.url, {"raw_data": f"Mal,Actor,{self.teacher_b.email}"}
        )
        self.teacher_b.refresh_from_db()
        self.assertEqual(self.teacher_b.user_type, UserTypes.TEACHER)
        self.assertFalse(StudentCourse.objects.filter(student=self.teacher_b).exists())
        self.assertEqual(response.data["failure_count"], 1)
        self.assertIn("teacher", response.data["results"][0]["error"].lower())

    def test_bulk_import_cannot_hijack_a_school_admin_account(self):
        self.as_(self.teacher_a)
        self.client.post(self.url, {"raw_data": f"Mal,Actor,{self.admin_b.email}"})
        self.admin_b.refresh_from_db()
        self.assertEqual(self.admin_b.user_type, UserTypes.SCHOOL_ADMIN)
        self.assertFalse(StudentCourse.objects.filter(student=self.admin_b).exists())

    def test_invite_by_email_cannot_reach_another_schools_account(self):
        """POLICY CHANGED 2026-09-08, and this test changed with it.

        It previously asserted the opposite - that invite-by-email reached
        any existing account, across schools, with no consent step - and
        was written to pin that as the product's onboarding model while the
        question was raised. The owner decided school ownership must not be
        shared or silently reassigned, so the rule is now enforced on every
        path and this test asserts the rejection instead. Kept here, rather
        than only in tests_cross_school_enrollment, because the bulk roster
        is the route an attacker would actually use for volume.
        """
        self.as_(self.teacher_a)
        self.client.post(self.url, {"raw_data": f"Any,Name,{self.student_b.email}"})

        self.assertFalse(
            StudentCourse.objects.filter(
                student=self.student_b, course=self.course_a
            ).exists()
        )
        self.student_b.refresh_from_db()
        self.assertEqual(self.student_b.school_id, self.school_b.id)

    def test_bulk_import_cannot_pull_in_another_schools_student_by_name(self):
        self.student_b.first_name = "Common"
        self.student_b.last_name = "Name"
        self.student_b.save()
        self.as_(self.teacher_a)
        self.client.post(self.url, {"raw_data": "Common,Name"})
        self.assertFalse(
            StudentCourse.objects.filter(
                student=self.student_b, course=self.course_a
            ).exists()
        )


class SubmissionExposureAttacks(AttackBase):
    """The enrollment serializer walks a student's submissions. It must
    never surface another teacher's grades through that path."""

    def setUp(self):
        super().setUp()
        # student_a takes a course with BOTH teachers.
        self.shared_course_b = Course.objects.create(
            name="B second", teacher=self.teacher_b, session=self.session_b
        )
        StudentCourse.objects.create(
            student=self.student_a,
            course=self.shared_course_b,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        self.secret_assignment = Assignment.objects.create(
            title="B secret", course=self.shared_course_b, teacher=self.teacher_b
        )
        StudentSubmission.objects.create(
            student=self.student_a,
            assignment=self.secret_assignment,
            answers={},
            score=99,
        )

    def test_teacher_a_sees_no_submission_counts_from_teacher_bs_course(self):
        self.as_(self.teacher_a)
        rows = self.client.get(reverse("student-course-list")).data["results"]
        self.assertEqual(len(rows), 1)
        # The only assignment teacher A can account for is their own (none),
        # so a count of 1 here would mean teacher B's submission leaked in.
        self.assertEqual(rows[0]["total_assignment_submitted"], 0)

    def test_teacher_a_cannot_read_the_other_courses_enrollment_row(self):
        self.as_(self.teacher_a)
        found = self.ids(self.client.get(reverse("student-course-list")))
        self.assertEqual(found, {str(self.enrollment_a.id)})
