"""`my-students` stays scoped while another teacher's roster is changing.

The scoping fix filters the prefetches by `course__teacher=user`. A read that
races a write to someone else's enrollment must never catch a half-state in
which the other teacher's course, description, teacher name or grade shows
up in this teacher's payload, and a filter naming the other teacher's course
must return zero rows at every instant.

Real threads, real Postgres, real commits: each round releases 20 threads
through a barrier - 10 of them enrol or remove shared students in teacher
B's course through the production service functions, 10 read teacher A's
`my-students` (half unfiltered, half filtered by B's course).
"""

import json
import threading
from typing import Any

from django.core.cache import cache
from django.db import connections
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import Course, Session, StudentCourse
from classrooms.services import enroll_student_by_email, remove_student_from_course
from classrooms.tests_concurrency_and_resilience import (
    ThreadSafeTransactionTestCase,
    make_user,
)
from students.models import StudentSubmission
from users.models import UserTypes

URL = reverse("student-course-my-students")
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

THREADS = 20
ROUNDS = 10
STUDENTS = 10
B_SECRETS = ("Private Tutoring B", "B confidential notes", "Bea", "Bravo")


@override_settings(CACHES=LOCMEM)
class MyStudentsReadsRacingForeignRosterChanges(ThreadSafeTransactionTestCase):
    def setUp(self):
        cache.clear()
        self.teacher_a = make_user(
            "race-a@indiv.test", UserTypes.TEACHER, first_name="Ada", last_name="Alpha"
        )
        self.teacher_b = make_user(
            "race-b@indiv.test", UserTypes.TEACHER, first_name="Bea", last_name="Bravo"
        )
        self.course_a = Course.objects.create(
            name="Algebra A",
            description="A notes",
            teacher=self.teacher_a,
            session=Session.objects.create(name="SA", teacher=self.teacher_a),
        )
        self.course_b = Course.objects.create(
            name="Private Tutoring B",
            description="B confidential notes",
            teacher=self.teacher_b,
            session=Session.objects.create(name="SB", teacher=self.teacher_b),
        )
        assignment_b = Assignment.objects.create(
            title="B quiz",
            course=self.course_b,
            teacher=self.teacher_b,
            status=AssignmentStatus.PUBLISHED,
        )
        self.students = []
        for i in range(STUDENTS):
            student = make_user(f"race-s{i}@indiv.test", UserTypes.STUDENT)
            enroll_student_by_email(course=self.course_a, email=student.email)
            enroll_student_by_email(course=self.course_b, email=student.email)
            StudentSubmission.objects.create(
                student=student,
                assignment=assignment_b,
                answers={},
                score=37,
                max_points=100,
                graded_at=timezone.now(),
            )
            self.students.append(student)

    def _toggle_b_enrollment(self, student):
        if StudentCourse.objects.filter(student=student, course=self.course_b).exists():
            remove_student_from_course(course=self.course_b, student_id=student.id)
            return "removed"
        enroll_student_by_email(course=self.course_b, email=student.email)
        return "enrolled"

    def _read_as_a(self, filtered):
        client = APIClient()
        client.force_authenticate(self.teacher_a)
        params: dict[str, Any] = {"page_size": 100}
        if filtered:
            params["enrollments__course"] = str(self.course_b.id)
        response = client.get(URL, params)
        return response.status_code, response.data

    def _work(self, index, barrier, results):
        try:
            barrier.wait(timeout=30)
            if index % 2 == 0:
                student = self.students[(index // 2) % STUDENTS]
                results[index] = ("write", self._toggle_b_enrollment(student))
            else:
                filtered = index % 4 == 3
                results[index] = ("read", filtered, *self._read_as_a(filtered))
        except Exception as exc:  # noqa: BLE001 - asserted by the caller
            results[index] = ("error", repr(exc))
        finally:
            # Each thread has its own connection; a leaked one leaves the
            # test database un-droppable.
            connections.close_all()

    def test_teacher_a_never_sees_teacher_b_mid_change(self):
        violations = []
        rows_seen = 0
        writes = {"removed": 0, "enrolled": 0}
        errors = []

        for round_no in range(ROUNDS):
            barrier = threading.Barrier(THREADS)
            results: list[tuple[Any, ...] | None] = [None] * THREADS
            threads = [
                threading.Thread(target=self._work, args=(i, barrier, results))
                for i in range(THREADS)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=120)
            for t in threads:
                self.assertFalse(
                    t.is_alive(), f"round {round_no}: a thread did not finish"
                )

            finished = [result for result in results if result is not None]
            self.assertEqual(
                len(finished), THREADS, f"round {round_no}: a thread recorded nothing"
            )
            for result in finished:
                if result[0] == "error":
                    errors.append((round_no, result[1]))
                elif result[0] == "write":
                    writes[result[1]] += 1
                else:
                    _, filtered, code, data = result
                    if code != 200:
                        violations.append((round_no, "status", code))
                        continue
                    body = json.dumps(data, default=str)
                    leaked = [s for s in B_SECRETS if s in body]
                    if leaked:
                        violations.append((round_no, "leak", leaked))
                    if filtered and data["count"] != 0:
                        violations.append((round_no, "oracle", data["count"]))
                    if not filtered:
                        rows_seen += len(data["results"])
                        for row in data["results"]:
                            if (
                                row["enrolled_courses"] != ["Algebra A"]
                                or row["grade"] is not None
                            ):
                                violations.append((round_no, "row", row))

        self.assertEqual(errors, [])
        self.assertEqual(violations, [])
        # Not vacuous: the readers saw every shared student each time, and
        # the writers really did flip B's roster both ways.
        self.assertEqual(rows_seen, STUDENTS * ROUNDS * (THREADS // 4))
        self.assertEqual(writes["removed"] + writes["enrolled"], ROUNDS * THREADS // 2)
        self.assertGreater(writes["removed"], 0)
        self.assertGreater(writes["enrolled"], 0)
