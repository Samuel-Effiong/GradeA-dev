"""H-18 / H-19 at scale (Gate 6).

The new guards must cost the same whatever the size of the school:
validate_course reads `teacher_id` off the Course row the PK field already
fetched; the AI-output allow-list is a dict filter; the both-flags checks
compare attributes of the already-loaded user. None should issue a query
that grows with courses, students or assignments.

  * QueryCountFlatnessTest (always on): every guarded request's query count
    is asserted EQUAL at a small world and one 10x larger.
  * SixThousandStudentSchoolTest (opt-in, RUN_LOAD_TESTS=1 like the other
    load suites, because building the school takes minutes): the same
    counts on a 6,000-student school, plus p50/p95 latency and peak memory,
    printed for the evidence record.

Rows are created through production write paths: user-manager create_user
(students with an unusable password, as the direct-add flow does, which
also avoids hashing 6,000 passwords), the post_save signals, and courses
and enrollments as the classroom services write them.
"""

import os
import statistics
import time
import tracemalloc
import unittest
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APITestCase

from assignments.tests_extraction_service import extraction_payload
from assignments.tests_security import make_assignment, make_course, make_teacher
from billing.models import CreditBucket, CreditBucketType
from classrooms.models import EnrollmentStatusType, School, StudentCourse
from users.models import CustomUser, UserTypes

if TYPE_CHECKING:
    from rest_framework.test import APITestCase as _MixinBase
else:
    _MixinBase = object

LOAD_TESTS_ENABLED = os.environ.get("RUN_LOAD_TESTS") == "1"
EXTRACT = "assignments.services.ai_processor.extract_assignment_with_retry"
FAKE_TASK = MagicMock(id="00000000-0000-0000-0000-000000000001")


class ScaleWorld(_MixinBase):
    """A school of `students` students spread over `courses` courses and
    `teachers` teachers, plus an attacker teacher in the same school."""

    def build_scale_world(
        self, *, tag, teachers, courses, students, assignments_per_course
    ):
        self.school = School.objects.create(name=f"Scale School {tag}")
        self.teachers = [
            make_teacher(f"scale-{tag}-t{i}@example.com", self.school)
            for i in range(teachers)
        ]
        self.courses = [
            make_course(self.teachers[i % teachers], f"Scale {tag} Course {i}")
            for i in range(courses)
        ]
        for course in self.courses:
            for j in range(assignments_per_course):
                make_assignment(course, f"Scale {tag} {course.name} A{j}")
        for i in range(students):
            student = CustomUser.objects.create_user(
                email=f"scale-{tag}-s{i}@example.com",
                password=None,
                user_type=UserTypes.STUDENT,
                school=self.school,
                first_name="S",
                last_name=str(i),
            )
            StudentCourse.objects.create(
                student=student,
                course=self.courses[i % courses],
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )

        self.attacker = make_teacher(f"scale-{tag}-attacker@example.com", self.school)
        self.attacker_course = make_course(
            self.attacker, f"Scale {tag} Attacker Course"
        )
        self.attacker_assignment = make_assignment(
            self.attacker_course, f"Scale {tag} Own"
        )
        CreditBucket.objects.create(
            wallet=self.attacker.credit_wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=10_000,
            used_credits=0,
        )
        self.victim_course = self.courses[-1]

    def guarded_requests(self):
        """name -> zero-arg callable performing one guarded request."""
        self.client.force_authenticate(user=self.attacker)
        detail = reverse(
            "assignment-detail", kwargs={"pk": self.attacker_assignment.pk}
        )

        def foreign_create():
            return self.client.post(
                reverse("assignment-create-async"),
                {"course": str(self.victim_course.id), "raw_input": "Q1", "title": "X"},
                format="json",
            )

        def foreign_reparent():
            return self.client.patch(
                detail, {"course": str(self.victim_course.id)}, format="json"
            )

        def own_create_through_allow_list():
            # Unique text per call: an assignment's raw_input is unique per course.
            n = uuid4().hex
            return self.client.post(
                reverse("assignment-list"),
                {
                    "course": str(self.attacker_course.id),
                    "raw_input": f"Q1. 2+2? ({n})",
                    "title": f"Own {n}",
                },
                format="json",
            )

        return {
            "foreign_create_refused": (foreign_create, 400),
            "foreign_reparent_refused": (foreign_reparent, 400),
            "own_create_allow_list": (own_create_through_allow_list, 202),
        }

    def measure_query_counts(self):
        counts = {}
        requests = self.guarded_requests()
        with patch(
            "assignments.views.launch_processing_task", return_value=FAKE_TASK
        ), patch(
            EXTRACT,
            return_value={**extraction_payload(), "teacher": str(self.teachers[0].id)},
        ):
            for name, (request, expected_status) in requests.items():
                request()  # warm: first-hit caches must not skew the count
                cache.clear()
                with CaptureQueriesContext(connection) as queries:
                    response = request()
                self.assertEqual(response.status_code, expected_status, name)
                counts[name] = len(queries)
        return counts


class QueryCountFlatnessTest(ScaleWorld, APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_guarded_request_query_counts_are_equal_at_10x_data(self):
        self.build_scale_world(
            tag="small", teachers=2, courses=6, students=60, assignments_per_course=1
        )
        small = self.measure_query_counts()

        self.build_scale_world(
            tag="large",
            teachers=20,
            courses=60,
            students=600,
            assignments_per_course=10,
        )
        large = self.measure_query_counts()

        print(
            f"\n[H18/H19 scale] query counts small(60 students)={small} large(600)={large}"
        )
        self.assertEqual(small, large)


@unittest.skipUnless(
    LOAD_TESTS_ENABLED, "set RUN_LOAD_TESTS=1 to build the 6,000-student school"
)
class SixThousandStudentSchoolTest(ScaleWorld, APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_six_thousand_student_school(self):
        started = time.monotonic()
        self.build_scale_world(
            tag="small", teachers=2, courses=6, students=60, assignments_per_course=1
        )
        small = self.measure_query_counts()
        self.build_scale_world(
            tag="6k", teachers=120, courses=240, students=6000, assignments_per_course=5
        )
        build_seconds = time.monotonic() - started
        large = self.measure_query_counts()
        self.assertEqual(
            StudentCourse.objects.filter(course__teacher__school=self.school).count(),
            6000,
        )
        self.assertEqual(small, large)

        timings = {}
        tracemalloc.start()
        with patch(
            "assignments.views.launch_processing_task", return_value=FAKE_TASK
        ), patch(EXTRACT, return_value=extraction_payload()):
            for name, (request, expected_status) in self.guarded_requests().items():
                samples = []
                for _ in range(50):
                    t0 = time.perf_counter()
                    response = request()
                    samples.append((time.perf_counter() - t0) * 1000)
                    self.assertEqual(response.status_code, expected_status, name)
                samples.sort()
                timings[name] = {
                    "p50_ms": round(statistics.median(samples), 2),
                    "p95_ms": round(samples[int(len(samples) * 0.95) - 1], 2),
                }
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        print(
            f"\n[H18/H19 6k school] build={build_seconds:.1f}s query_counts={large} "
            f"timings={timings} peak_traced_memory_mb={peak / 1_048_576:.1f}"
        )
