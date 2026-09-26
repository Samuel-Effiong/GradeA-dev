"""H-18 / H-19 under real concurrency (Gate 3).

Sequential tests prove each refusal once. These release 20 real threads at a
barrier against real PostgreSQL, for 10 rounds, and assert a single logical
outcome every round:

  * foreign-course IDOR: 10 threads POST new assignments into other
    teachers' courses (other school and same school) while 10 PATCH the
    attacker's own assignment into them. Zero may succeed; no row may appear
    in a victim course; the attacker's assignment never moves.
  * type-only superadmin AI: 20 threads request a billed AI summary as an
    account with user_type=SUPER_ADMIN but is_superuser=False. Zero may reach
    the provider; zero billing rows; the real superadmin, interleaved, stays
    unmetered.

Every thread closes its own DB connection in `finally`, and every join is
followed by an is_alive() assertion, so a thread that timed out can never
leave work the assertions then read. The provider is stubbed (no network in
a concurrency test); its real behaviour is covered separately.
"""

import threading
from typing import Any
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.db import connections
from django.urls import reverse
from rest_framework.test import APIClient, APITransactionTestCase

from ai_processor.services import AIProcessor, ai_processor
from ai_processor.tests_superadmin_unmetered_both_flags import (
    PROVIDER,
    SuperadminShapesMixin,
    provider_reply,
)
from assignments.models import Assignment
from assignments.tests_security import (
    enroll,
    make_assignment,
    make_course,
    make_student,
    make_teacher,
)
from billing.access_control import AIFeatureNotAvailableError
from billing.models import CreditLedger, CreditUsageLog
from classrooms.models import School

THREADS = 20
ROUNDS = 10
FAKE_TASK = MagicMock(id="00000000-0000-0000-0000-000000000001")


def run_at_barrier(test, worker, count):
    """Release `count` threads together; return per-thread results."""
    barrier = threading.Barrier(count)
    results: list[Any] = [None] * count

    def wrapped(index):
        try:
            barrier.wait(timeout=60)
            results[index] = worker(index)
        except BaseException as exc:  # noqa: BLE001 - recorded and asserted
            results[index] = exc
        finally:
            connections.close_all()

    threads = [threading.Thread(target=wrapped, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    test.assertEqual(
        [t.name for t in threads if t.is_alive()],
        [],
        "a worker thread never finished; its results cannot be trusted",
    )
    return results


class ConcurrentForeignCourseWritesTest(APITransactionTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        school_a = School.objects.create(name="Conc School A")
        school_b = School.objects.create(name="Conc School B")
        self.victim_other_school = make_teacher("conc-victim-a@example.com", school_a)
        self.attacker = make_teacher("conc-attacker-b@example.com", school_b)
        self.victim_same_school = make_teacher("conc-colleague-b@example.com", school_b)
        self.victim_courses = [
            make_course(self.victim_other_school, "Conc Victim Other School"),
            make_course(self.victim_same_school, "Conc Victim Same School"),
        ]
        self.own_course = make_course(self.attacker, "Conc Attacker Course")
        self.own_assignment = make_assignment(self.own_course, "Conc Own Quiz")
        self.victim_students = []
        for i, course in enumerate(self.victim_courses):
            student = make_student(f"conc-victim-student-{i}@example.com")
            enroll(student, course)
            self.victim_students.append(student)

    def test_twenty_simultaneous_foreign_creates_and_reparents_all_fail_for_ten_rounds(
        self,
    ):
        create_url = reverse("assignment-create-async")
        patch_url = reverse("assignment-detail", kwargs={"pk": self.own_assignment.pk})

        def worker(round_no):
            def attack(index):
                client = APIClient()
                client.force_authenticate(user=self.attacker)
                course = self.victim_courses[index % 2]
                if index % 2 == 0:
                    response = client.post(
                        create_url,
                        {
                            "course": str(course.id),
                            "raw_input": f"Q1. planted r{round_no} t{index}",
                            "title": f"PLANTED r{round_no} t{index}",
                            "status": "PUBLISHED",
                        },
                        format="json",
                    )
                else:
                    response = client.patch(
                        patch_url, {"course": str(course.id)}, format="json"
                    )
                return response.status_code, response.content.decode()

            return attack

        with patch(
            "assignments.views.launch_processing_task", return_value=FAKE_TASK
        ) as launch:
            for round_no in range(ROUNDS):
                with self.subTest(round=round_no):
                    results = run_at_barrier(self, worker(round_no), THREADS)

                    errors = [r for r in results if isinstance(r, BaseException)]
                    self.assertEqual(
                        errors, [], "a request raised instead of answering"
                    )
                    statuses = sorted({code for code, _ in results})
                    self.assertEqual(statuses, [400], f"round {round_no}: {statuses}")
                    for _code, body in results:
                        for course in self.victim_courses:
                            self.assertNotIn(course.name, body)

                    for course in self.victim_courses:
                        self.assertFalse(
                            Assignment.objects.filter(course=course).exists(),
                            f"round {round_no}: a row appeared in {course.name}",
                        )
                    self.own_assignment.refresh_from_db()
                    self.assertEqual(self.own_assignment.course_id, self.own_course.id)
            launch.assert_not_called()

        # Neither victim's class can see anything of the attacker's.
        for student in self.victim_students:
            client = APIClient()
            client.force_authenticate(user=student)
            body = client.get(reverse("assignment-list")).content.decode()
            self.assertNotIn("PLANTED", body)
            self.assertNotIn("Conc Own Quiz", body)


class ConcurrentTypeOnlySuperadminAITest(SuperadminShapesMixin, APITransactionTestCase):
    def setUp(self):
        self.build_superadmin_shapes()
        self.student = make_student("conc-ai-student@example.com")
        enroll(self.student, self.promoted_course)

    def test_twenty_simultaneous_type_only_ai_calls_never_reach_the_provider(self):
        calls = []
        lock = threading.Lock()

        def provider(*args, **kwargs):
            with lock:
                calls.append(1)
            return provider_reply()

        def worker(index):
            # Interleave: every 5th thread is the REAL superadmin, which must
            # stay unmetered while the type-only accounts are refused.
            if index % 5 == 4:
                return ai_processor.generate_student_summary(
                    self.superadmin, self.student, self.promoted_course
                )
            user = self.type_only[index % 2]
            return ai_processor.generate_student_summary(
                user, self.student, self.promoted_course
            )

        real_per_round = len([i for i in range(THREADS) if i % 5 == 4])
        with patch.object(AIProcessor, PROVIDER, side_effect=provider):
            for round_no in range(ROUNDS):
                with self.subTest(round=round_no):
                    before = len(calls)
                    results = run_at_barrier(self, worker, THREADS)

                    for index, result in enumerate(results):
                        if index % 5 == 4:
                            self.assertEqual(
                                result, "A steady term with strong quiz results."
                            )
                        else:
                            self.assertIsInstance(result, AIFeatureNotAvailableError)
                    self.assertEqual(
                        len(calls) - before,
                        real_per_round,
                        "a type-only account reached the provider",
                    )

        for user in (*self.type_only, self.superadmin):
            self.assertEqual(CreditUsageLog.objects.filter(user_id=user.id).count(), 0)
            self.assertEqual(CreditLedger.objects.filter(user_id=user.id).count(), 0)
