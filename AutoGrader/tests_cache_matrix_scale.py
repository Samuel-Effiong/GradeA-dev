"""H-1 Stage 3: scale gate + before/after Redis measurement.

The plan's gate requires the new fan-out bumps to be "O(1) queries per
mutation and one pipelined round trip" at realistic scale - a course
with 30 and 300 students - and requires measuring Redis commands per
mutation before vs after (expected: many -> 0 SCANs). The SAME mutation
(publishing an assignment, G1's fan-out) is measured at both roster sizes.

The "before" side was measured while the legacy wildcards still existed
(9 SCANs per publish at either size; docs/evidence/
H1_STAGE3_TARGETED_INVALIDATION_EVIDENCE.md §5). H-1 step 4 removed them,
so there is no before side left to run: this now measures the real code
and asserts the after side - one SCAN, and it is the PDF exact-prefix
clear for that one assignment.

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
from AutoGrader.tests_cache_matrix_support import (
    disallowed_scan_patterns,
    scan_patterns_sent_by_this_process,
)
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
    """G1 fan-out cost with the wildcards gone, at two roster sizes."""

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
                with scan_patterns_sent_by_this_process() as scans:
                    response = client.patch(
                        reverse("assignment-detail", args=[assignment.pk]),
                        {"status": "PUBLISHED"},
                        format="json",
                    )
        self.assertEqual(response.status_code, 200, response.content)
        return len(queries.captured_queries), dict(sent), list(scans)

    def _measure_at(self, student_count):
        label = f"n{student_count}"
        teacher, assignment = self._build_course_with_students(label, student_count)

        cache.clear()
        queries, redis_sent, scans = self._measure_publish(teacher, assignment)
        return {
            "queries": queries,
            "scan": redis_sent.get("SCAN", 0),
            "scan_patterns": scans,
            "assignment_id": str(assignment.id),
        }

    def test_fan_out_cost_at_30_and_300_students(self):
        at_30 = self._measure_at(30)
        at_300 = self._measure_at(300)

        report = (
            "\n[H-1 step 4 scale measurement, wildcards removed]\n"
            f"  30 students : SCAN={at_30['scan']:<3} queries={at_30['queries']}\n"
            f"  300 students: SCAN={at_300['scan']:<3} queries={at_300['queries']}\n"
        )
        print(report)

        # The only SCAN left is assignments/pdf_cache.py's per-assignment
        # PDF clear - explicitly kept by the plan (§2: "not a legacy
        # wildcard"), scoped to ONE assignment_id, so it must stay flat
        # with roster size (1 SCAN), not grow into a wildcard sweep.
        for measured in (at_30, at_300):
            self.assertEqual(measured["scan"], 1, report)
            self.assertEqual(disallowed_scan_patterns(measured["scan_patterns"]), [])
            self.assertIn(
                f":assignmentpdf:v1:{measured['assignment_id']}:*",
                measured["scan_patterns"][0],
            )

        # O(1) queries: the query the fan-out uses to find enrolled
        # students must not grow with roster size.
        self.assertEqual(
            at_30["queries"],
            at_300["queries"],
            f"query count grew with roster size: {at_30['queries']} at "
            f"30 students vs {at_300['queries']} at 300 students\n{report}",
        )
