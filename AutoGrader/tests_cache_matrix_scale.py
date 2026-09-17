"""H-1 Stage 3: scale gate + before/after Redis measurement.

The plan's gate requires the new fan-out bumps to be "O(1) queries per
mutation and one pipelined round trip" at realistic scale - a course
with 30 and 300 students - and requires measuring Redis commands per
mutation before vs after (expected: many -> 0 SCANs). This test does
both at once: the SAME mutation (publishing an assignment, G1's fan-out)
measured first with the legacy wildcards LIVE (today's behaviour) and
then with them disabled (the new targeted-only behaviour), at both
roster sizes.

Fixture students are created with `create_user(save=False)` +
`bulk_create` for speed - a scale fixture, not the mutation under
measurement - and every user still gets exactly the fields
`create_user` gives it. The publish itself runs through the real routed
`PATCH assignments/<pk>` endpoint.

Real Redis + real Postgres.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from assignments.models import Assignment, AssignmentStatus
from AutoGrader.tests_cache_generation import redis_commands_sent_by_this_process
from AutoGrader.tests_cache_matrix_support import legacy_wildcards_disabled
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from users.models import UserTypes

User = get_user_model()


def question(number=1):
    return {
        "question_number": number,
        "question_text": f"Q{number}",
        "question_type": "OBJECTIVE",
        "points": 10,
        "options": ["one", "two"],
        "rubric": [],
        "model_answer": "one",
    }


class AssignmentPublishScaleTests(TransactionTestCase):
    """G1 fan-out cost, measured before and after, at two roster sizes."""

    reset_sequences = True

    def _build_course_with_students(self, label, student_count):
        teacher = User.objects.create_user(
            email=f"scale-{label}-teacher@x.test",
            password="password123",  # nosec  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
            first_name="ScaleT",
            last_name="Teacher",
        )
        session = Session.objects.create(name=f"Scale {label} term", teacher=teacher)
        course = Course.objects.create(
            name=f"Scale {label} course", teacher=teacher, session=session
        )

        # Fixture-only speed path: real create_user() field defaults,
        # bulk_create for the round trips. Not the mutation being measured.
        students = [
            User.objects.create_user(
                email=f"scale-{label}-student-{i}@x.test",
                password="password123",  # nosec  # pragma: allowlist secret
                user_type=UserTypes.STUDENT,
                is_active=True,
                first_name=f"Scale{label}S{i}",
                last_name="Student",
                save=False,
            )
            for i in range(student_count)
        ]
        User.objects.bulk_create(students)
        StudentCourse.objects.bulk_create(
            StudentCourse(
                student=student,
                course=course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )
            for student in students
        )

        assignment = Assignment.objects.create(
            title=f"Scale {label} assignment",
            course=course,
            status=AssignmentStatus.DRAFT,
            questions=[question()],
        )
        return teacher, assignment

    def _measure_publish(self, teacher, assignment):
        client = APIClient()
        client.force_authenticate(teacher)
        with CaptureQueriesContext(connection) as queries:
            with redis_commands_sent_by_this_process() as sent:
                response = client.patch(
                    reverse("assignment-detail", args=[assignment.pk]),
                    {"status": "PUBLISHED"},
                    format="json",
                )
        self.assertEqual(response.status_code, 200, response.content)
        return len(queries.captured_queries), dict(sent)

    def _measure_at(self, student_count):
        label = f"n{student_count}"
        teacher, assignment = self._build_course_with_students(label, student_count)

        cache.clear()
        before_queries, before_redis = self._measure_publish(teacher, assignment)

        # Publish is a one-way DRAFT -> PUBLISHED move; reset for the
        # second measurement so both runs exercise the identical mutation.
        assignment.status = AssignmentStatus.DRAFT
        assignment.save(update_fields=["status"])
        cache.clear()

        with legacy_wildcards_disabled():
            after_queries, after_redis = self._measure_publish(teacher, assignment)

        return {
            "before_queries": before_queries,
            "before_scan": before_redis.get("SCAN", 0),
            "after_queries": after_queries,
            "after_scan": after_redis.get("SCAN", 0),
        }

    def test_fan_out_cost_before_and_after_at_30_and_300_students(self):
        at_30 = self._measure_at(30)
        at_300 = self._measure_at(300)

        report = (
            "\n[H-1 Stage 3 scale + before/after measurement]\n"
            f"  30 students : before SCAN={at_30['before_scan']:<3} queries={at_30['before_queries']:<4}"
            f"  |  after SCAN={at_30['after_scan']:<3} queries={at_30['after_queries']}\n"
            f"  300 students: before SCAN={at_300['before_scan']:<3} queries={at_300['before_queries']:<4}"
            f"  |  after SCAN={at_300['after_scan']:<3} queries={at_300['after_queries']}\n"
        )
        print(report)

        # Before: the legacy wildcard sweeps the keyspace - SCAN count is
        # expected to be nonzero (that IS the problem H-1 exists to fix).
        self.assertGreater(
            at_30["before_scan"], 0, "expected the legacy path to SCAN at all"
        )

        # After: the legacy wildcard receivers are disabled, so the only
        # SCAN left is assignments/pdf_cache.py's per-assignment PDF clear
        # - explicitly kept by the plan (§2: "not a legacy wildcard"),
        # scoped to ONE assignment_id, so it must stay flat with roster
        # size (1 SCAN), not grow into a second wildcard sweep.
        self.assertEqual(at_30["after_scan"], 1, report)
        self.assertEqual(
            at_30["after_scan"],
            at_300["after_scan"],
            f"the kept per-assignment PDF SCAN grew with roster size - it "
            f"should stay flat at 1\n{report}",
        )

        # O(1) queries: the query the fan-out uses to find enrolled
        # students must not grow with roster size.
        self.assertEqual(
            at_30["after_queries"],
            at_300["after_queries"],
            f"query count grew with roster size: {at_30['after_queries']} at "
            f"30 students vs {at_300['after_queries']} at 300 students\n{report}",
        )
