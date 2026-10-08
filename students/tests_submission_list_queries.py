"""
H-16: the submission list endpoint must not issue queries per row.

Before the fix, `GET submissions` ran 4 fixed queries plus 3 per row on the
page (the row's student, its assignment, and that assignment's course, each
fetched lazily by StudentSubmissionListSerializer): 64 queries at the
default page size of 20, 304 at the maximum of 100.

Acceptance (docs/HARDENING_BACKLOG.md H-16): query count flat in page size,
asserted as flatness rather than an absolute budget; identical payload;
freshness unchanged.

* Flatness is asserted by comparing counts across page sizes, filters,
  orderings and search, for teachers and students, on cold builds (the
  per-user list cache is cleared before each request, so every request
  really runs the queryset and the serializer).
* Payload identity is asserted against the same serializer run over the
  plain, un-optimised queryset, so the proof does not depend on how the
  optimisation is written.
* Tenancy: loading relations up front must not widen what anyone sees.
* Freshness is covered by the existing cache suites, which run the same
  list endpoint (AutoGrader.tests_cache_user_fanout,
  tests_cache_invalidation_coverage, students.tests_submission_update_freshness).

Run with:
    python manage.py test students.tests_submission_list_queries
"""

import json
from datetime import timedelta

from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import GradingState, StudentSubmission
from students.serializers import StudentSubmissionListSerializer
from users.models import CustomUser, UserTypes

LIST_URL = "student-submission-list"


def _user(email, user_type, **extra):
    return CustomUser.objects.create_user(
        email=email,
        password="password123",  # pragma: allowlist secret
        user_type=user_type,
        **extra,
    )


class SubmissionListFixture:
    """Two teachers; the first has three courses, nine assignments (one a
    draft) and many submissions with varied grading/review/schedule state;
    the second has one course with one submission that must never leak."""

    N_SUBMISSIONS = 60

    @classmethod
    def build(cls):
        now = timezone.now()
        teacher = _user("list-q-teacher@example.com", UserTypes.TEACHER)
        other_teacher = _user("list-q-other@example.com", UserTypes.TEACHER)
        session = Session.objects.create(name="S", teacher=teacher)
        other_session = Session.objects.create(name="OS", teacher=other_teacher)
        courses = [
            Course.objects.create(name=f"C{i}", teacher=teacher, session=session)
            for i in range(3)
        ]
        assignments = []
        for index in range(9):
            assignments.append(
                Assignment.objects.create(
                    title=f"A{index}",
                    course=courses[index % 3],
                    status=(
                        AssignmentStatus.DRAFT
                        if index == 8
                        else AssignmentStatus.PUBLISHED
                    ),
                    total_points=10 + index,
                    questions=[
                        {"question_number": 1, "question_text": "Q", "points": 10}
                    ],
                )
            )
        students = []
        for i in range(cls.N_SUBMISSIONS):
            student = _user(
                f"list-q-s{i:03d}@example.com",
                UserTypes.STUDENT,
                first_name=f"S{i:03d}",
                last_name=f"L{i % 7}",
            )
            students.append(student)
            assignment = assignments[i % len(assignments)]
            StudentCourse.objects.create(
                student=student,
                course=assignment.course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )
            graded = i % 3 != 0
            StudentSubmission.objects.create(
                assignment=assignment,
                student=student,
                answers=[],
                attempt_count=i % 4,
                score=(i % 10) if graded else None,
                score_percentage=(i % 100) if graded else None,
                max_points=None if i % 5 == 0 else 20,
                graded_at=now - timedelta(hours=1) if graded else None,
                is_published=i % 2 == 0,
                grading_state=GradingState.DONE if graded else GradingState.IDLE,
                needs_review=i % 6 == 1,
                review_tier="critical" if i % 6 == 1 else None,
                review_severity=(i % 9) / 10 if i % 6 == 1 else None,
                scheduled_grading_at=now + timedelta(days=1) if i % 11 == 0 else None,
            )
        other_course = Course.objects.create(
            name="OC", teacher=other_teacher, session=other_session
        )
        other_assignment = Assignment.objects.create(
            title="OA", course=other_course, status=AssignmentStatus.PUBLISHED
        )
        leaked = StudentSubmission.objects.create(
            assignment=other_assignment, student=students[0], answers=[]
        )
        return teacher, other_teacher, students, assignments, leaked


class SubmissionListQueryFlatnessTest(SubmissionListFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        (
            cls.teacher,
            cls.other_teacher,
            cls.students,
            cls.assignments,
            cls.leaked,
        ) = cls.build()

    def _count(self, user, params):
        client = APIClient()
        client.force_authenticate(user)
        cache.clear()  # a cold build: the per-user list cache must miss
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(reverse(LIST_URL), params)
        self.assertEqual(response.status_code, 200, response.content[:300])
        return len(ctx.captured_queries), len(response.data["results"])

    def test_teacher_list_query_count_is_flat_in_page_size(self):
        counts = {}
        for size in (1, 5, 20, 50, 100):
            queries, rows = self._count(self.teacher, {"page_size": size})
            self.assertEqual(rows, min(size, self.N_SUBMISSIONS))
            counts[size] = queries
        self.assertEqual(
            len(set(counts.values())),
            1,
            f"query count grows with page size (per-row queries are back): {counts}",
        )

    def test_default_page_size_matches_a_single_row_page(self):
        one, _ = self._count(self.teacher, {"page_size": 1})
        default, rows = self._count(self.teacher, {})
        self.assertEqual(rows, 20)
        self.assertEqual(default, one)

    def test_count_is_flat_across_filters_orderings_and_search(self):
        assignment = str(self.assignments[1].id)
        baseline, _ = self._count(self.teacher, {"page_size": 1})
        shapes = {
            "second page": {"page": 2, "page_size": 20},
            "severity ordering": {"ordering": "-review_severity", "page_size": 50},
            "multi-field ordering": {
                "ordering": "student__last_name,-student__first_name",
                "page_size": 50,
            },
            "search": {"search": "S0", "page_size": 50},
            "review queue": {
                "needs_review": "true",
                "review_tier": "critical",
                "page_size": 50,
            },
            "published": {"is_published": "true", "page_size": 50},
        }
        for label, params in shapes.items():
            with self.subTest(label):
                queries, _ = self._count(self.teacher, params)
                self.assertEqual(queries, baseline, f"{label}: {queries} vs {baseline}")
        # Filtering by assignment validates the id with one extra lookup;
        # what matters is that it, too, does not grow with the page.
        small, _ = self._count(self.teacher, {"assignment": assignment, "page_size": 1})
        large, _ = self._count(
            self.teacher, {"assignment": assignment, "page_size": 50}
        )
        self.assertEqual(small, large)

    def test_student_list_query_count_is_flat_in_page_size(self):
        student = self.students[0]
        extra = []
        for assignment in self.assignments[1:8]:
            StudentCourse.objects.get_or_create(
                student=student,
                course=assignment.course,
                defaults={"enrollment_status": EnrollmentStatusType.ENROLLED},
            )
            extra.append(
                StudentSubmission.objects.get_or_create(
                    assignment=assignment, student=student, defaults={"answers": []}
                )[0]
            )
        one, rows_one = self._count(student, {"page_size": 1})
        many, rows_many = self._count(student, {"page_size": 50})
        self.assertEqual(rows_one, 1)
        self.assertGreater(rows_many, 1)
        self.assertEqual(one, many)


class SubmissionListPayloadIdentityTest(SubmissionListFixture, TestCase):
    """The optimised list must return exactly what the serializer returns
    for the plain queryset - field for field, row for row, in order."""

    @classmethod
    def setUpTestData(cls):
        (
            cls.teacher,
            cls.other_teacher,
            cls.students,
            cls.assignments,
            cls.leaked,
        ) = cls.build()

    def _api_rows(self, user, params):
        client = APIClient()
        client.force_authenticate(user)
        cache.clear()
        response = client.get(reverse(LIST_URL), params)
        self.assertEqual(response.status_code, 200)
        return json.loads(json.dumps(response.data["results"], default=str))

    def _reference_rows(self, user, queryset, params):
        """The same serializer, the same request context, over a queryset
        with no relation loading at all."""
        request = APIRequestFactory().get(reverse(LIST_URL), params)
        force_authenticate(request, user=user)
        from rest_framework.request import Request

        drf_request = Request(request)
        drf_request.user = user
        data = StudentSubmissionListSerializer(
            queryset, many=True, context={"request": drf_request}
        ).data
        return json.loads(json.dumps(data, default=str))

    def test_teacher_payload_is_identical_to_the_unoptimised_serializer(self):
        plain = StudentSubmission.objects.filter(
            assignment__course__teacher=self.teacher
        ).order_by("student__first_name", "id")
        expected = self._reference_rows(self.teacher, plain, {})
        actual = self._api_rows(
            self.teacher, {"page_size": 100, "ordering": "student__first_name"}
        )
        self.assertEqual(len(actual), self.N_SUBMISSIONS)
        self.assertEqual(
            sorted(actual, key=lambda r: r["id"]),
            sorted(expected, key=lambda r: r["id"]),
        )
        # And the order the API returns is the ordering asked for.
        self.assertEqual(
            [r["student_name"].split()[0] for r in actual],
            sorted(r["student_name"].split()[0] for r in actual),
        )

    def test_student_payload_is_identical_to_the_unoptimised_serializer(self):
        student = self.students[1]  # a published, graded row and an unpublished one
        plain = StudentSubmission.objects.filter(student=student).exclude(
            assignment__status__in=[
                AssignmentStatus.DRAFT,
                AssignmentStatus.UNPUBLISHED,
            ]
        )
        expected = self._reference_rows(student, plain, {})
        actual = self._api_rows(student, {"page_size": 100})
        self.assertEqual(
            sorted(actual, key=lambda r: r["id"]),
            sorted(expected, key=lambda r: r["id"]),
        )

    def test_the_fields_that_walk_relations_are_populated(self):
        rows = self._api_rows(self.teacher, {"page_size": 100})
        by_id = {r["id"]: r for r in rows}
        for submission in StudentSubmission.objects.filter(
            assignment__course__teacher=self.teacher
        ).select_related("student", "assignment"):
            row = by_id[str(submission.id)]
            self.assertEqual(
                row["student_name"],
                f"{submission.student.first_name} {submission.student.last_name}",
            )
            self.assertEqual(row["assignment_title"], submission.assignment.title)
            self.assertEqual(row["course"], str(submission.assignment.course_id))
            self.assertEqual(
                row["max_points"],
                submission.max_points or submission.assignment.total_points,
            )


class SubmissionListTenancyTest(SubmissionListFixture, TestCase):
    """Relation loading must not widen the list."""

    @classmethod
    def setUpTestData(cls):
        (
            cls.teacher,
            cls.other_teacher,
            cls.students,
            cls.assignments,
            cls.leaked,
        ) = cls.build()

    def _ids(self, user, params=None):
        client = APIClient()
        client.force_authenticate(user)
        cache.clear()
        response = client.get(reverse(LIST_URL), {"page_size": 100, **(params or {})})
        self.assertEqual(response.status_code, 200)
        return {r["id"] for r in response.data["results"]}

    def test_teacher_sees_exactly_their_own_courses(self):
        expected = {
            str(pk)
            for pk in StudentSubmission.objects.filter(
                assignment__course__teacher=self.teacher
            ).values_list("id", flat=True)
        }
        self.assertEqual(self._ids(self.teacher), expected)
        self.assertNotIn(str(self.leaked.id), self._ids(self.teacher))

    def test_other_teacher_sees_only_their_one_submission(self):
        self.assertEqual(self._ids(self.other_teacher), {str(self.leaked.id)})

    def test_other_teacher_cannot_reach_a_submission_by_filtering_for_it(self):
        own_assignment = str(self.assignments[0].id)
        client = APIClient()
        client.force_authenticate(self.other_teacher)
        cache.clear()
        response = client.get(reverse(LIST_URL), {"assignment": own_assignment})
        # django-filter rejects an id outside the queryset's choices, or the
        # scoped queryset returns nothing; either way nothing leaks.
        if response.status_code == 200:
            self.assertEqual(response.data["results"], [])
        else:
            self.assertEqual(response.status_code, 400)

    def test_student_sees_only_their_own_non_draft_submissions(self):
        student = self.students[8]  # assignment index 8 is the draft
        ids = self._ids(student)
        expected = {
            str(pk)
            for pk in StudentSubmission.objects.filter(student=student)
            .exclude(assignment__status=AssignmentStatus.DRAFT)
            .values_list("id", flat=True)
        }
        self.assertEqual(ids, expected)
        draft = StudentSubmission.objects.get(
            student=student, assignment=self.assignments[8]
        )
        self.assertNotIn(str(draft.id), ids)

    def test_unscoped_user_types_see_nothing(self):
        admin = _user("list-q-admin@example.com", UserTypes.SCHOOL_ADMIN)
        self.assertEqual(self._ids(admin), set())
