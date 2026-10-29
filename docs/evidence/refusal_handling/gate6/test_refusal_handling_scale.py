"""
Refusal handling, Gate 6: what a refusal COSTS at realistic school scale.

Claimed in the evidence and measured here:
  1. The per-request cost of a refusal is CONSTANT in school size - it
     short-circuits before any provider call or per-row work, so nothing
     grows with students, courses or submissions.
  2. The change is SUBTRACTIVE: a permanent refusal used to be retried
     (4 executions of a refused answer task, 3 attempts of a refused
     extraction). It is now 1.
  3. The one path that iterates at scale - the weekly summary emails -
     got CHEAPER: a refusal used to log a full stack via logger.exception
     once per course, and now logs one WARNING line.

This module is deliberately written to run on the PRE-FIX commit too: it
imports nothing this branch added and asserts counts and timings, never
messages or status codes. That is what makes the before/after numbers
comparable - run it at b744c9f and at the fix commit and diff the tables.

Queries are counted with `connection.execute_wrapper`, NOT
CaptureQueriesContext: the test client fires `request_started`, which calls
`reset_queries()` mid-capture, and `connection.queries_log` is a bounded
deque - both silently under-count. A count of 0, or one that falls as data
grows, is a broken measurement, never an optimisation.

Run with (not part of the default suite's fast path - it builds a 6,000
student school):
    python manage.py test billing.tests.test_refusal_handling_scale \
        --settings=settings_worktree
"""

import json
import logging
import os
import re
import statistics
import time
import uuid
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from ai_processor.services import AIProcessor
from assignments.models import Assignment, AssignmentStatus
from billing.models import BillingInterval, PlanCategory, PlanTier, SubscriptionPlan
from billing.services import SubscriptionService
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskType,
    StudentSubmission,
)
from users.models import CustomUser, UserTypes

logger = logging.getLogger(__name__)

# Two sizes 10x apart (doctrine H7.2). The large one is the doctrine's
# default realistic school (H7.1: 6,000 students). Overridable ONLY to smoke
# the logic cheaply before committing a host to the real build; a run whose
# sizes were overridden says so in every line it emits, so a cheap run can
# never be mistaken for the real measurement.
SMALL_SCHOOL = int(os.environ.get("GATE6_SMALL", 600))
LARGE_SCHOOL = int(os.environ.get("GATE6_LARGE", 6000))
DOCTRINE_SIZES = (SMALL_SCHOOL, LARGE_SCHOOL) == (600, 6000)
STUDENTS_PER_COURSE = 30

# What a refusal is allowed to answer, on EITHER commit: 400 is the pre-fix
# answer (the defect this branch fixes), 402/403 the fixed one. A 5xx is
# never a refusal - under connection starvation the app answers 500, and a
# measurement that counted those would be measuring the pool, not the code
# (c5's users-app run passed with 20 "too many clients" lines in its log).
REFUSAL_STATUSES = {400, 402, 403}
SAMPLES = int(os.environ.get("GATE6_SAMPLES", 20))

# Hashing 6,000 passwords with the production hasher would dominate the
# runtime and measure bcrypt, not refusals. The rows are still created
# through CustomUser.objects.create_user, the way production creates them.
FAST_HASHER = ["django.contrib.auth.hashers.MD5PasswordHasher"]


class QueryCounter:
    """Counts every statement that actually reaches the database."""

    def __init__(self):
        self.count = 0

    def __call__(self, execute, sql, params, many, context):
        self.count += 1
        return execute(sql, params, many, context)


def server_connections():
    """How many connections the whole Postgres instance is serving right
    now. Recorded beside every figure: a query count or a latency taken
    while the pool is starved measures the pool, not the code under test
    (the same class of trap as counting queries with a bounded deque)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*), setting::int FROM pg_stat_activity, "
            "pg_settings WHERE name = 'max_connections' "
            "GROUP BY setting"
        )
        row = cursor.fetchone()
    return {"connections": row[0], "max_connections": row[1]} if row else {}


@contextmanager
def measured():
    """Yields a dict that ends up with {"queries": n, "seconds": s} plus the
    server's connection count before and after."""
    counter = QueryCounter()
    result = {"pool_before": server_connections()}
    started = time.perf_counter()
    with connection.execute_wrapper(counter):
        yield result
    result["seconds"] = time.perf_counter() - started
    result["queries"] = counter.count
    result["pool_after"] = server_connections()


def percentiles(samples):
    ordered = sorted(samples)
    return {
        "p50": statistics.median(ordered),
        "p95": ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))],
    }


def _user(tag, user_type, **extra):
    return CustomUser.objects.create_user(
        email=f"{tag}-{uuid.uuid4().hex[:10]}@example.com",
        password="password123",  # pragma: allowlist secret
        user_type=user_type,
        first_name=tag[:20],
        last_name="Scale",
        is_active=True,
        **extra,
    )


def underfunded_teacher(tag):
    """Subscribed through the production activation path to a plan whose
    grant is below execute_graded_task's estimate: every AI call is refused
    on balance, before the provider."""
    teacher = _user(tag, UserTypes.TEACHER)
    plan = SubscriptionPlan.objects.create(
        name=f"scale-{uuid.uuid4().hex[:8]}",
        category=PlanCategory.INDIVIDUAL,
        tier=PlanTier.PRO,
        interval=BillingInterval.MONTHLY,
        monthly_credits=1000,
        carry_over_percent=0,
        is_active=True,
    )
    SubscriptionService.activate_subscription(teacher, plan)
    return teacher


@contextmanager
def fixture_writes_without_wildcard_scans():
    """For BUILDING fixtures only - never around a measurement.

    Profiled (20 students, 84.8s total): 80s went to Redis `delete_pattern`
    SCANs fired by cache-invalidation signals on every user/enrollment save
    (via AutoGrader.cache_utils and users.signals.clear_user_cache). Each
    SCAN walks the whole shared Redis keyspace, so one student cost ~4s and a
    6,000-student school would take hours. That cost is the H-1 wildcard
    invalidation debt, owned by the H-1 Stage 3 task; it is not on any
    refusal path and is not what this module measures.

    Skipping ONLY those scans while rows are created leaves every database row
    exactly as production writes it (same managers, same signals, same
    columns); only the Redis side-effect of the fixture build is skipped. The
    measured requests below run with invalidation fully live.
    """
    # Patched on the backend class so EVERY caller is covered: the profile
    # showed two routes into these scans (cache_utils._delete_now and
    # users.signals.clear_user_cache, which calls the cache directly), and
    # patching only the first left the build as slow as before.
    from django_redis.cache import RedisCache

    with patch.object(RedisCache, "delete_pattern", return_value=0):
        yield


def build_school(students, tag):
    """A school of `students`, with a course per 30 of them, an assignment
    per course and a submission per student. Rows are created through the
    same managers production writes through."""
    with fixture_writes_without_wildcard_scans():
        return _build_school(students, tag)


def _build_school(students, tag):
    school = School.objects.create(name=f"Scale School {tag}")
    teacher = underfunded_teacher(f"scale-teacher-{tag}")
    teacher.school = school
    teacher.save(update_fields=["school"])
    session = Session.objects.create(name=f"Session {tag}", teacher=teacher)

    courses = []
    for index in range(max(1, students // STUDENTS_PER_COURSE)):
        course = Course.objects.create(
            name=f"Course {tag} {index}", teacher=teacher, session=session
        )
        courses.append(
            (
                course,
                Assignment.objects.create(
                    title=f"Assignment {tag} {index}",
                    course=course,
                    status=AssignmentStatus.PUBLISHED,
                    questions=[
                        {"question_number": 1, "question_text": "2 + 2?", "points": 10}
                    ],
                ),
            )
        )

    first_submission = None
    for index in range(students):
        course, assignment = courses[index % len(courses)]
        student = _user(f"s{index}", UserTypes.STUDENT, school=school)
        StudentCourse.objects.create(
            student=student,
            course=course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        submission = StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "4"}],
            attempt_count=1,
        )
        if first_submission is None:
            first_submission = submission
            first_student = student

    return {
        "school": school,
        "teacher": teacher,
        "courses": courses,
        "student": first_student,
        "submission": first_submission,
        "assignment": courses[0][1],
    }


@override_settings(PASSWORD_HASHERS=FAST_HASHER)
class RefusalCostIsFlatInSchoolSizeTest(TestCase):
    """Gate 6, claim 1: the per-request cost of a refusal does not grow
    with the school."""

    @classmethod
    def setUpTestData(cls):
        cls.schools = {
            SMALL_SCHOOL: build_school(SMALL_SCHOOL, "small"),
            LARGE_SCHOOL: build_school(LARGE_SCHOOL, "large"),
        }

    def _sample(self, call):
        queries, seconds = [], []
        for _ in range(SAMPLES):
            with measured() as result:
                status_code = call()
            self.assertIn(
                status_code, REFUSAL_STATUSES, "not a refusal - never measure it as one"
            )
            queries.append(result["queries"])
            seconds.append(result["seconds"])
        self.assertTrue(all(q > 0 for q in queries), queries)
        return queries, seconds

    def _report(self, label, size, queries, seconds):
        timing = percentiles(seconds)
        line = {
            "measurement": label,
            "doctrine_sizes": DOCTRINE_SIZES,
            "pool": server_connections(),
            "students": size,
            "queries_min": min(queries),
            "queries_max": max(queries),
            "p50_ms": round(timing["p50"] * 1000, 1),
            "p95_ms": round(timing["p95"] * 1000, 1),
        }
        logger.warning("GATE6 %s", json.dumps(line))
        return line

    def test_a_permission_layer_refusal_costs_the_same_at_10x_the_school(self):
        """The D11 path: HasCreditBalance refuses before the view body."""
        results = {}
        for size, school in self.schools.items():
            # HasCreditBalance refuses an EMPTY wallet; the fixture teacher
            # holds 1,000 credits (below the AI estimate, above zero), which
            # the permission lets through. Spend them through the production
            # consume path first - TestCase rolls this back afterwards.
            wallet = school["teacher"].credit_wallet
            wallet.consume_credits(
                wallet.total_remaining_credits(), feature="Gate 6 measurement"
            )
            self.assertEqual(wallet.total_remaining_credits(), 0)
            client = APIClient()
            client.force_authenticate(user=school["teacher"])
            url = reverse("assignment-grade-all", args=[str(school["assignment"].id)])
            queries, seconds = self._sample(
                lambda client=client, url=url: client.post(url, {}).status_code
            )
            results[size] = self._report("permission_refusal", size, queries, seconds)

        self.assertEqual(
            results[SMALL_SCHOOL]["queries_max"],
            results[LARGE_SCHOOL]["queries_max"],
            f"per-request queries grew with school size: {results}",
        )

    def test_a_service_gate_refusal_costs_the_same_at_10x_the_school(self):
        """The D9/D10 path: the balance check inside execute_graded_task,
        reached through a student's edit of their own submission."""
        results = {}
        for size, school in self.schools.items():
            client = APIClient()
            client.force_authenticate(user=school["student"])
            url = reverse(
                "student-submission-detail", args=[str(school["submission"].id)]
            )
            with patch.object(
                AIProcessor,
                "_AIProcessor__ai_model",
                side_effect=AssertionError("a refused request reached the provider"),
            ):
                queries, seconds = self._sample(
                    lambda client=client, url=url: client.patch(
                        url, {"raw_input": "Question 1: 4"}, format="json"
                    ).status_code
                )
            results[size] = self._report("service_refusal", size, queries, seconds)

        self.assertEqual(
            results[SMALL_SCHOOL]["queries_max"],
            results[LARGE_SCHOOL]["queries_max"],
            f"per-request queries grew with school size: {results}",
        )


@override_settings(PASSWORD_HASHERS=FAST_HASHER)
class RefusedTaskCostTest(TestCase):
    """Gate 6, claim 2: a refused background task runs the billed path
    ONCE. On the pre-fix commit the same measurement records 4 (one
    delivery plus three Celery retries), which is what makes this change
    subtractive at batch scale."""

    @classmethod
    def setUpTestData(cls):
        cls.school = build_school(SMALL_SCHOOL, "task")

    def test_a_refused_answer_task_reaches_the_gate_once_per_item(self):
        from assignments.tasks import extract_answer_background_task

        tracked = BackgroundProcessingTask.objects.create(
            requested_by=self.school["student"],
            task_type=BackgroundTaskType.ANSWER_EXTRACTION,
            assignment=self.school["assignment"],
            submission=self.school["submission"],
        )
        with patch.object(
            AIProcessor,
            "execute_graded_task",
            autospec=True,
            side_effect=AIProcessor.execute_graded_task,
        ) as gate, patch.object(
            AIProcessor,
            "_AIProcessor__ai_model",
            side_effect=AssertionError("a refused request reached the provider"),
        ):
            with measured() as result:
                extract_answer_background_task.apply(
                    args=(
                        str(self.school["submission"].id),
                        "Question 1: 4",
                        str(self.school["student"].id),
                    ),
                    kwargs={"processing_task_id": str(tracked.id)},
                )

        tracked.refresh_from_db()
        # On both commits a refused task ends as a recorded FAILURE (pre-fix:
        # after its retries). Anything else means the run broke for another
        # reason and its gate count is not a refusal measurement.
        self.assertEqual(tracked.status, "FAILURE", tracked.error)
        self.assertTrue(tracked.error)
        logger.warning(
            "GATE6 %s",
            json.dumps(
                {
                    "measurement": "refused_task",
                    "doctrine_sizes": DOCTRINE_SIZES,
                    "pool": result["pool_after"],
                    "gate_calls_per_item": gate.call_count,
                    "queries_per_item": result["queries"],
                    "ms_per_item": round(result["seconds"] * 1000, 1),
                }
            ),
        )
        self.assertEqual(gate.call_count, 1, "a permanent refusal was retried")


@override_settings(PASSWORD_HASHERS=FAST_HASHER)
class WeeklySummaryRefusalCostTest(TestCase):
    """Gate 6, claim 3: the one path that iterates at scale. A refused
    narration must cost a constant amount per course, and must not write a
    stack trace per course (which is what logger.exception did before)."""

    COURSE_COUNTS = (20, 200)

    def _run_for(self, course_count):
        # Build with the wildcard scans skipped; MEASURE with them live.
        with fixture_writes_without_wildcard_scans():
            teacher = self._build_weekly(course_count)
        return self._measure(teacher, course_count)

    def _build_weekly(self, course_count):
        teacher = underfunded_teacher(f"weekly-{course_count}")
        teacher.settings.notify_weekly_summary = True
        teacher.settings.save(update_fields=["notify_weekly_summary"])
        session = Session.objects.create(name="Weekly", teacher=teacher)
        for index in range(course_count):
            course = Course.objects.create(
                name=f"Weekly course {index}", teacher=teacher, session=session
            )
            student = _user(f"w{index}", UserTypes.STUDENT)
            StudentCourse.objects.create(
                student=student,
                course=course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )
            assignment = Assignment.objects.create(
                title=f"Weekly assignment {index}",
                course=course,
                status=AssignmentStatus.PUBLISHED,
                due_date=timezone.now() - timedelta(days=1),
                questions=[
                    {"question_number": 1, "question_text": "2 + 2?", "points": 10}
                ],
            )
            StudentSubmission.objects.create(
                assignment=assignment,
                student=student,
                answers=[{"question_number": 1, "answer_html": "4"}],
                attempt_count=1,
            )
        return teacher

    def _measure(self, teacher, course_count):
        from dashboard.tasks import send_weekly_course_summaries

        with patch("dashboard.tasks.send_email_task.delay"), patch.object(
            AIProcessor,
            "_AIProcessor__ai_model",
            side_effect=AssertionError("a refused narration reached the provider"),
        ):
            with self.assertLogs("dashboard.tasks", level=logging.DEBUG) as logs:
                with measured() as result:
                    summary = send_weekly_course_summaries.apply().get()

        # The task swallows every exception into "skipped": a course that
        # errored (a starved connection, say) would otherwise look like a
        # cheap refusal. Every course must have been processed and sent.
        # Divide by the courses the task ACTUALLY processed: every eligible
        # course in the database (an earlier, smaller run's courses included),
        # not the number this call built.
        processed = Course.objects.filter(
            is_active=True, teacher__settings__notify_weekly_summary=True
        ).count()
        queued = re.search(r"Queued (\d+) ", summary)
        skipped = re.search(r"skipped (\d+)", summary)
        assert queued and skipped, summary
        self.assertEqual(int(queued.group(1)), processed, summary)
        self.assertEqual(int(skipped.group(1)), 0, summary)
        course_count = processed

        with_stacks = [r for r in logs.records if r.exc_info is not None]
        line = {
            "measurement": "weekly_course_summaries",
            "doctrine_sizes": DOCTRINE_SIZES,
            "pool": result["pool_after"],
            "courses": course_count,
            "queries_per_course": round(result["queries"] / course_count, 2),
            "ms_per_course": round(result["seconds"] * 1000 / course_count, 1),
            "log_records_with_a_stack_trace": len(with_stacks),
        }
        logger.warning("GATE6 %s", json.dumps(line))
        return line

    def test_a_refused_narration_costs_a_constant_amount_per_course(self):
        results = {count: self._run_for(count) for count in self.COURSE_COUNTS}
        small, large = (results[count] for count in self.COURSE_COUNTS)

        # Flat per course, within a small tolerance for fixed startup work
        # amortised differently across the two sizes.
        self.assertLessEqual(
            large["queries_per_course"],
            small["queries_per_course"] * 1.2,
            f"per-course queries grew with course count: {results}",
        )
        # The point of claim 3: no stack trace per refused course.
        self.assertEqual(
            large["log_records_with_a_stack_trace"],
            0,
            "a refusal is still being logged as an exception with a stack",
        )
