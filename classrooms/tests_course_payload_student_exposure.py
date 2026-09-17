"""A student's course payload must not expose drafts or classmates' emails.

`CourseSerializer` backs the course list, retrieve and `my-courses`
endpoints. For a STUDENT requester it used to nest every assignment in the
course - unpublished drafts included - count those drafts in
`assignment_count`, and list the whole active roster with each classmate's
real email address.

The rule now: a student sees only PUBLISHED assignments (the same rule the
assignments endpoints already apply) and no other student's email. Their own
email stays. Teachers' payloads are unchanged.

Rows are created the way production creates them: courses and enrollments
through the real endpoints, withdrawal through the teacher's enrollment
PATCH, and assignments with only the fields `AssignmentViewSet.create` sets
(its endpoint runs a billed AI extraction, so it is not called here).
"""

import hashlib
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from assignments.serializers import AssignmentListSerializer
from AutoGrader.test_cache import real_redis_caches
from classrooms.models import Session, StudentCourse
from students.serializers import StudentSerializer
from users.models import UserTypes

User = get_user_model()

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
REDIS_CACHE = real_redis_caches("redis://127.0.0.1:6379/14")

COURSE_FIELDS = [
    "id",
    "name",
    "session",
    "is_active",
    "created_at",
    "description",
    "student_count",
    "assignment_count",
    "students",
    "topics",
    "assignments",
]


def make_user(email, user_type, **fields):
    user = User.objects.create_user(email=email, password="password123")  # nosec
    user.user_type = user_type
    user.is_active = True
    for name, value in fields.items():
        setattr(user, name, value)
    user.save()
    return user


@override_settings(CACHES=LOCMEM)
class CoursePayloadBase(APITestCase):
    def setUp(self):
        cache.clear()
        self._seq = 0
        self.teacher = make_user(
            "exposure-teacher@x.test",
            UserTypes.TEACHER,
            first_name="Tess",
            last_name="Teacher",
        )
        self.session = Session.objects.create(name="Fall", teacher=self.teacher)
        # Every enrollment notice goes through this task; nothing here
        # is about email delivery.
        mailer = patch("classrooms.services.notifications.send_email_task.delay")
        mailer.start()
        self.addCleanup(mailer.stop)

    # -- production-shaped builders -------------------------------------

    def create_course(self, name, teacher=None):
        teacher = teacher or self.teacher
        session = (
            self.session
            if teacher == self.teacher
            else Session.objects.create(name=f"{name} session", teacher=teacher)
        )
        self.client.force_authenticate(teacher)
        response = self.client.post(
            reverse("course-list"),
            {"name": name, "session": str(session.id), "topic_names": ["Algebra"]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        return response.data["id"]

    def new_student(self, label):
        self._seq += 1
        return make_user(
            f"{label}-{self._seq}@real-mail.test",
            UserTypes.STUDENT,
            # Unique: enrollment rejects a second student with the same name.
            first_name=f"{label}{self._seq}",
            last_name="Student",
        )

    def enroll(self, course_id, student, teacher=None):
        self.client.force_authenticate(teacher or self.teacher)
        response = self.client.post(
            reverse("course-students", kwargs={"pk": course_id}),
            {"email": student.email},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def withdraw(self, course_id, student, teacher=None):
        enrollment = StudentCourse.objects.get(course_id=course_id, student=student)
        self.client.force_authenticate(teacher or self.teacher)
        response = self.client.patch(
            reverse("student-course-detail", kwargs={"pk": enrollment.id}),
            {"enrollment_status": "WITHDRAWN"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def add_assignment(self, course_id, title, assignment_status):
        raw_input = f"{title}: answer every question."
        return Assignment.objects.create(
            course_id=course_id,
            title=title,
            raw_input=raw_input,
            raw_input_hash=hashlib.sha256(raw_input.encode("utf-8")).hexdigest(),
            status=assignment_status,
        )

    def get_as(self, user, url):
        cache.clear()
        self.client.force_authenticate(user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data

    @staticmethod
    def rows(data):
        return data.get("results", data) if isinstance(data, dict) else data

    def course_in(self, data, course_id):
        matches = [r for r in self.rows(data) if str(r["id"]) == str(course_id)]
        self.assertEqual(len(matches), 1)
        return matches[0]

    def build_class(self):
        """One course: a published and a draft assignment, a viewer and a
        classmate who both have real email addresses."""
        self.course_id = self.create_course("Biology")
        self.published = self.add_assignment(
            self.course_id, "Cells quiz", AssignmentStatus.PUBLISHED
        )
        self.draft = self.add_assignment(
            self.course_id, "Unreleased exam", AssignmentStatus.DRAFT
        )
        self.viewer = self.new_student("viewer")
        self.classmate = self.new_student("classmate")
        self.enroll(self.course_id, self.viewer)
        self.enroll(self.course_id, self.classmate)

    # -- the student-side rule -------------------------------------------

    def assert_student_safe(self, payload, viewer, published_ids):
        assignment_ids = {str(a["id"]) for a in payload["assignments"]}
        self.assertEqual(assignment_ids, {str(i) for i in published_ids})
        for assignment in payload["assignments"]:
            self.assertEqual(assignment["status"], AssignmentStatus.PUBLISHED)
        self.assertEqual(payload["assignment_count"], len(published_ids))

        self.assertTrue(payload["students"], "the roster was empty - vacuous")
        for entry in payload["students"]:
            if str(entry["id"]) == str(viewer.id):
                self.assertEqual(entry["email"], viewer.email)
            else:
                self.assertIsNone(
                    entry["email"],
                    f"classmate {entry['first_name']}'s email reached a student",
                )


class StudentCoursePayloadExposure(CoursePayloadBase):
    def setUp(self):
        super().setUp()
        self.build_class()

    def test_student_retrieve_hides_drafts_and_classmate_emails(self):
        payload = self.get_as(
            self.viewer, reverse("course-detail", kwargs={"pk": self.course_id})
        )
        self.assert_student_safe(payload, self.viewer, [self.published.id])

    def test_student_list_hides_drafts_and_classmate_emails(self):
        data = self.get_as(self.viewer, reverse("course-list"))
        payload = self.course_in(data, self.course_id)
        self.assert_student_safe(payload, self.viewer, [self.published.id])

    def test_student_my_courses_hides_drafts_and_classmate_emails(self):
        data = self.get_as(self.viewer, reverse("course-my-courses"))
        payload = self.course_in(data, self.course_id)
        self.assert_student_safe(payload, self.viewer, [self.published.id])

    def test_student_keeps_their_own_email(self):
        payload = self.get_as(
            self.viewer, reverse("course-detail", kwargs={"pk": self.course_id})
        )
        own = [s for s in payload["students"] if str(s["id"]) == str(self.viewer.id)]
        self.assertEqual(len(own), 1)
        self.assertEqual(own[0]["email"], self.viewer.email)

    def test_unpublished_assignment_is_hidden_from_students_too(self):
        self.add_assignment(self.course_id, "Pulled back", AssignmentStatus.UNPUBLISHED)
        payload = self.get_as(
            self.viewer, reverse("course-detail", kwargs={"pk": self.course_id})
        )
        self.assert_student_safe(payload, self.viewer, [self.published.id])

    def test_payload_shape_is_unchanged_for_students(self):
        payload = self.get_as(
            self.viewer, reverse("course-detail", kwargs={"pk": self.course_id})
        )
        self.assertEqual(list(payload.keys()), COURSE_FIELDS)
        self.assertEqual(
            list(payload["students"][0].keys()),
            list(StudentSerializer().fields.keys()),
        )
        self.assertEqual(
            list(payload["assignments"][0].keys()),
            list(AssignmentListSerializer().fields.keys()),
        )

    def test_withdrawn_classmate_is_still_excluded(self):
        self.withdraw(self.course_id, self.classmate)
        payload = self.get_as(
            self.viewer, reverse("course-detail", kwargs={"pk": self.course_id})
        )
        ids = {str(s["id"]) for s in payload["students"]}
        self.assertEqual(ids, {str(self.viewer.id)})
        self.assertEqual(payload["student_count"], 1)


class StudentInTwoCourses(CoursePayloadBase):
    def test_each_course_carries_its_own_filtered_data(self):
        self.build_class()
        other_teacher = make_user(
            "exposure-teacher-2@x.test",
            UserTypes.TEACHER,
            first_name="Otto",
            last_name="Other",
        )
        second_id = self.create_course("Chemistry", teacher=other_teacher)
        second_published = [
            self.add_assignment(second_id, "Moles", AssignmentStatus.PUBLISHED),
            self.add_assignment(second_id, "Bonds", AssignmentStatus.PUBLISHED),
        ]
        self.add_assignment(second_id, "Chem draft", AssignmentStatus.DRAFT)
        second_classmate = self.new_student("labpartner")
        self.enroll(second_id, self.viewer, teacher=other_teacher)
        self.enroll(second_id, second_classmate, teacher=other_teacher)

        expected = {
            str(self.course_id): ([self.published.id], {self.classmate.id}),
            str(second_id): (
                [a.id for a in second_published],
                {second_classmate.id},
            ),
        }

        for url in (reverse("course-list"), reverse("course-my-courses")):
            data = self.get_as(self.viewer, url)
            self.assertEqual({str(r["id"]) for r in self.rows(data)}, set(expected))
            for course_id, (published_ids, classmates) in expected.items():
                payload = self.course_in(data, course_id)
                self.assert_student_safe(payload, self.viewer, published_ids)
                self.assertEqual(
                    {str(s["id"]) for s in payload["students"]},
                    {str(self.viewer.id)} | {str(c) for c in classmates},
                )

        for course_id, (published_ids, _) in expected.items():
            payload = self.get_as(
                self.viewer, reverse("course-detail", kwargs={"pk": course_id})
            )
            self.assert_student_safe(payload, self.viewer, published_ids)


class TeacherAndAdminPayloadUnchanged(CoursePayloadBase):
    def setUp(self):
        super().setUp()
        self.build_class()

    def expected_teacher_view(self):
        """What the teacher's payload has always held, built from the
        nested serializers directly rather than through CourseSerializer."""
        assignments = AssignmentListSerializer(
            Assignment.objects.filter(course_id=self.course_id), many=True
        ).data
        enrollments = StudentCourse.objects.filter(
            course_id=self.course_id
        ).select_related("student")
        students = StudentSerializer(
            [e.student for e in enrollments],
            many=True,
            context={
                "course": enrollments[0].course,
                "enrollment_status_by_student": {
                    e.student_id: e.enrollment_status for e in enrollments
                },
            },
        ).data
        by_id = lambda rows: sorted(  # noqa: E731
            (dict(r) for r in rows), key=lambda r: str(r["id"])
        )
        return by_id(assignments), by_id(students)

    def assert_teacher_view(self, payload):
        assignments, students = self.expected_teacher_view()
        self.assertEqual(list(payload.keys()), COURSE_FIELDS)
        by_id = lambda rows: sorted(  # noqa: E731
            (dict(r) for r in rows), key=lambda r: str(r["id"])
        )
        self.assertEqual(by_id(payload["assignments"]), assignments)
        self.assertEqual(by_id(payload["students"]), students)
        self.assertEqual(payload["assignment_count"], 2)
        self.assertEqual(payload["student_count"], 2)
        # The two things students must no longer see, spelled out.
        self.assertIn(str(self.draft.id), {str(a["id"]) for a in assignments})
        self.assertEqual(
            {s["email"] for s in payload["students"]},
            {self.viewer.email, self.classmate.email},
        )

    def test_teacher_retrieve_still_has_drafts_and_emails(self):
        self.assert_teacher_view(
            self.get_as(
                self.teacher, reverse("course-detail", kwargs={"pk": self.course_id})
            )
        )

    def test_teacher_list_still_has_drafts_and_emails(self):
        data = self.get_as(self.teacher, reverse("course-list"))
        self.assert_teacher_view(self.course_in(data, self.course_id))

    def test_school_admin_and_superadmin_cannot_reach_course_payloads(self):
        """Their querysets are empty and my-courses is student-only, so
        there is no payload of theirs to change - pinned so a future scope
        widening is noticed alongside this rule."""
        school_admin = make_user("exposure-admin@x.test", UserTypes.SCHOOL_ADMIN)
        superadmin = make_user(
            "exposure-super@x.test",
            UserTypes.SUPER_ADMIN,
            is_superuser=True,
            is_staff=True,
        )
        for user in (school_admin, superadmin):
            cache.clear()
            self.client.force_authenticate(user)
            listing = self.client.get(reverse("course-list"))
            self.assertEqual(listing.status_code, status.HTTP_200_OK)
            self.assertEqual(self.rows(listing.data), [])
            detail = self.client.get(
                reverse("course-detail", kwargs={"pk": self.course_id})
            )
            self.assertEqual(detail.status_code, status.HTTP_404_NOT_FOUND)
            mine = self.client.get(reverse("course-my-courses"))
            self.assertEqual(mine.status_code, status.HTTP_400_BAD_REQUEST)


class CoursePayloadQueryCounts(CoursePayloadBase):
    """Filtering reads the prefetched rows; it must add no queries."""

    def measure(self, user, url):
        cache.clear()
        self.client.force_authenticate(user)
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return len(captured)

    def counts_for_roster(self, size):
        cache.clear()
        course_id = self.create_course(f"Roster {size}")
        self.add_assignment(course_id, f"Pub {size}", AssignmentStatus.PUBLISHED)
        self.add_assignment(course_id, f"Draft {size}", AssignmentStatus.DRAFT)
        viewer = self.new_student(f"counter{size}")
        self.enroll(course_id, viewer)
        for _ in range(size - 1):
            self.enroll(course_id, self.new_student("peer"))
        detail = reverse("course-detail", kwargs={"pk": course_id})
        return {
            "student_detail": self.measure(viewer, detail),
            "student_list": self.measure(viewer, reverse("course-list")),
            "student_my_courses": self.measure(viewer, reverse("course-my-courses")),
            "teacher_detail": self.measure(self.teacher, detail),
        }

    def test_query_counts_stay_flat_as_the_roster_grows(self):
        small = self.counts_for_roster(2)
        large = self.counts_for_roster(6)
        # Recorded for the evidence trail; the assertion is the flatness.
        print(f"\ncourse payload query counts: roster=2 {small} roster=6 {large}")
        for name in ("student_detail", "student_list", "student_my_courses"):
            self.assertLessEqual(large[name], small[name], name)
        self.assertLessEqual(large["teacher_detail"], small["teacher_detail"])


@override_settings(CACHES=REDIS_CACHE)
class CachedCoursePayloadIsPerRole(CoursePayloadBase):
    """UserCacheMixin keys on the requesting user, so a teacher's cached
    payload (drafts, emails) must never be served to a student and a
    student's filtered payload must never be served to the teacher."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.build_class()
        self.detail = reverse("course-detail", kwargs={"pk": self.course_id})

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def fetch(self, user, url):
        """No cache.clear() - the point is to read what is cached."""
        self.client.force_authenticate(user)
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data, len(captured)

    def assert_teacher_payload(self, payload):
        self.assertEqual(
            {str(a["id"]) for a in payload["assignments"]},
            {str(self.published.id), str(self.draft.id)},
        )
        self.assertEqual(payload["assignment_count"], 2)
        self.assertEqual(
            {s["email"] for s in payload["students"]},
            {self.viewer.email, self.classmate.email},
        )

    def check_both_orders(self, url, pick):
        for first, second in ((self.teacher, self.viewer), (self.viewer, self.teacher)):
            cache.clear()
            cold = {}
            for user in (first, second):
                cold[user.id], _ = self.fetch(user, url)
            for user in (first, second):
                warm, queries = self.fetch(user, url)
                # Fewer queries than a cold render proves this came from
                # the cache rather than from a fresh serialization.
                self.assertEqual(warm, cold[user.id])
                payload = pick(warm)
                if user == self.teacher:
                    self.assert_teacher_payload(payload)
                else:
                    self.assert_student_safe(payload, self.viewer, [self.published.id])
                yield user, queries

    def test_cached_retrieve_differs_between_teacher_and_student(self):
        cache.clear()
        _, cold_queries = self.fetch(self.teacher, self.detail)
        for _, warm_queries in self.check_both_orders(self.detail, lambda d: d):
            self.assertLess(warm_queries, cold_queries)

    def test_cached_list_differs_between_teacher_and_student(self):
        url = reverse("course-list")
        for _ in self.check_both_orders(
            url, lambda d: self.course_in(d, self.course_id)
        ):
            pass

    def test_cached_my_courses_stays_filtered(self):
        url = reverse("course-my-courses")
        cache.clear()
        cold, cold_queries = self.fetch(self.viewer, url)
        warm, warm_queries = self.fetch(self.viewer, url)
        self.assertLess(warm_queries, cold_queries)
        self.assertEqual(warm, cold)
        self.assert_student_safe(
            self.course_in(warm, self.course_id), self.viewer, [self.published.id]
        )
