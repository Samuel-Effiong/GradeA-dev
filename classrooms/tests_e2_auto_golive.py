"""Epic E2: the go-live recalculation runs by itself, once (owner 2026-11-19,
"automatic").

The first time GRADE_CATEGORIES_ENABLED is on in an environment, the
recompute behind `manage.py recalculate_missing_work --apply` runs once on
its own, from an hourly Beat task, then never again:

- the run-once marker is a database row (GoLiveRun, unique name), claimed
  with a conditional UPDATE and a lease, so restarts and several workers
  can't run it twice at once, and a crashed run's claim lapses;
- each course is its own transaction, so an interrupted run keeps what it
  finished and the next tick finishes the rest (the recompute is
  idempotent, so redone courses change nothing);
- audit only for grades that move (source "missing_work_go_live"), no
  credits;
- nothing at all while the flag is off (not even the marker row);
- the manual command still works: a full --apply takes the same claim and
  sets the marker, and the command reports the marker.
"""

from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from audit.enums import AuditAction
from audit.models import AuditEvent
from classrooms import grade_categories, missing_work_go_live
from classrooms.models import Course, GoLiveRun

from .tests_e2_fixtures import (
    categories_off,
    categories_on,
    enroll,
    grade,
    make_assignment,
    make_course,
    make_student,
    make_teacher,
    past,
    stored_grade,
)

D = Decimal
NAME = "missing_work_go_live"
BEAT = "e2-missing-work-go-live-hourly"
TASK = "classrooms.tasks.run_missing_work_go_live"
# The Beat task runs under the Beat lock, which needs the real (test) Redis
# cache, so these change only the flag, not CACHES.
task_flag_on = override_settings(GRADE_CATEGORIES_ENABLED=True)
task_flag_off = override_settings(GRADE_CATEGORIES_ENABLED=False)


class _GoLiveCase(TestCase):
    """Two courses that count missing work, graded while the flag was off:
    each stored grade is 80 and becomes 40 once the missed one counts."""

    def setUp(self):
        with categories_off:
            self.teacher = make_teacher()
            self.student = make_student()
            self.courses = []
            for _ in range(2):
                course = make_course(self.teacher)
                enroll(self.student, course)
                done = make_assignment(course, "Done", due_date=past())
                grade(self.student, done, 80)
                make_assignment(course, "Missed", due_date=past())
                self.courses.append(course)

    def changes(self):
        return AuditEvent.objects.filter(action=AuditAction.GRADE_CHANGE)

    def grades(self):
        return [stored_grade(self.student, course) for course in self.courses]

    def marker(self):
        return GoLiveRun.objects.filter(name=NAME).first()

    def run_command(self, *args):
        out = StringIO()
        call_command("recalculate_missing_work", *args, stdout=out)
        return out.getvalue()


class FlagOffTests(_GoLiveCase):
    @categories_off
    def test_nothing_happens_while_the_flag_is_off(self):
        result = missing_work_go_live.run()
        self.assertEqual(result["status"], missing_work_go_live.FLAG_OFF)
        self.assertEqual(self.grades(), [D("80.00"), D("80.00")])
        self.assertFalse(self.changes().exists())
        self.assertFalse(GoLiveRun.objects.exists())


@categories_on
class FirstRunTests(_GoLiveCase):
    def test_the_first_run_counts_missing_work_and_marks_it_done(self):
        result = missing_work_go_live.run()
        self.assertEqual(result["status"], missing_work_go_live.COMPLETED)
        self.assertEqual(self.grades(), [D("40.00"), D("40.00")])
        marker = self.marker()
        self.assertIsNotNone(marker.completed_at)
        self.assertIsNone(marker.claimed_until)
        self.assertEqual((marker.courses_checked, marker.grades_changed), (2, 2))

    def test_it_audits_only_changes_with_the_go_live_source(self):
        missing_work_go_live.run()
        self.assertEqual(self.changes().count(), 2)
        for change in self.changes():
            self.assertEqual(change.metadata["source"], NAME)

    def test_it_makes_the_due_date_tasks_still_ahead(self):
        from django_celery_beat.models import PeriodicTask

        from .tests_e2_fixtures import future

        with categories_off:
            later = make_assignment(self.courses[0], "Later", due_date=future(2))
        missing_work_go_live.run()
        name = grade_categories.missing_work_task_name(later.id)
        self.assertTrue(PeriodicTask.objects.filter(name=name).exists())

    def test_a_course_that_switched_it_off_is_left_alone(self):
        Course.objects.filter(pk=self.courses[1].pk).update(
            missing_counts_as_zero=False
        )
        missing_work_go_live.run()
        self.assertEqual(self.grades(), [D("40.00"), D("80.00")])

    def test_it_spends_no_credits(self):
        from billing.models import BillingTransaction, CreditBucket

        def state():
            return (
                BillingTransaction.objects.count(),
                list(CreditBucket.objects.values_list("used_credits", flat=True)),
            )

        before = state()
        missing_work_go_live.run()
        self.assertEqual(state(), before)


@categories_on
class RunsOnceTests(_GoLiveCase):
    def test_a_second_run_does_nothing_at_all(self):
        missing_work_go_live.run()
        before = self.changes().count()
        with patch.object(grade_categories, "recalculate_course") as recompute:
            result = missing_work_go_live.run()
        recompute.assert_not_called()
        self.assertEqual(result["status"], missing_work_go_live.ALREADY_DONE)
        self.assertEqual(self.changes().count(), before)

    def test_the_marker_is_one_row(self):
        missing_work_go_live.run()
        with self.assertRaises(IntegrityError), transaction.atomic():
            GoLiveRun.objects.create(name=NAME)

    def test_a_run_holding_the_claim_blocks_another(self):
        GoLiveRun.objects.create(
            name=NAME,
            claim_token="another-worker",
            claimed_until=timezone.now() + timedelta(minutes=5),
        )
        result = missing_work_go_live.run()
        self.assertEqual(result["status"], missing_work_go_live.HELD_ELSEWHERE)
        self.assertEqual(self.grades(), [D("80.00"), D("80.00")])
        self.assertIsNone(self.marker().completed_at)

    def test_a_crashed_runs_claim_lapses_and_the_next_run_finishes(self):
        GoLiveRun.objects.create(
            name=NAME,
            claim_token="crashed-worker",
            claimed_until=timezone.now() - timedelta(seconds=1),
        )
        result = missing_work_go_live.run()
        self.assertEqual(result["status"], missing_work_go_live.COMPLETED)
        self.assertEqual(self.grades(), [D("40.00"), D("40.00")])


@categories_on
class InterruptedRunTests(_GoLiveCase):
    def test_an_interrupted_run_keeps_its_work_and_a_rerun_finishes(self):
        real = grade_categories.recalculate_course
        second = self.courses[1].pk

        def fail_on_second(course_id, **kwargs):
            if course_id == second:
                raise RuntimeError("worker killed")
            return real(course_id, **kwargs)

        with patch.object(
            grade_categories, "recalculate_course", side_effect=fail_on_second
        ):
            with self.assertRaises(RuntimeError):
                missing_work_go_live.run()
        self.assertEqual(self.grades(), [D("40.00"), D("80.00")])
        self.assertIsNone(self.marker().completed_at)
        self.assertEqual(self.changes().count(), 1)

        result = missing_work_go_live.run()
        self.assertEqual(result["status"], missing_work_go_live.COMPLETED)
        self.assertEqual(self.grades(), [D("40.00"), D("40.00")])
        self.assertEqual(self.changes().count(), 2)

    def test_a_run_that_loses_its_claim_stops(self):
        real = grade_categories.recalculate_course

        def steal_after_first(course_id, **kwargs):
            done = real(course_id, **kwargs)
            GoLiveRun.objects.filter(name=NAME).update(claim_token="thief")
            return done

        with patch.object(
            grade_categories, "recalculate_course", side_effect=steal_after_first
        ):
            result = missing_work_go_live.run()
        self.assertEqual(result["status"], missing_work_go_live.LOST_CLAIM)
        self.assertEqual(self.grades(), [D("40.00"), D("80.00")])
        self.assertIsNone(self.marker().completed_at)


class BeatEntryTests(TestCase):
    def test_the_task_is_on_the_beat_schedule_with_a_health_expectation(self):
        entry = settings.CELERY_BEAT_SCHEDULE[BEAT]
        self.assertEqual(entry["task"], TASK)
        self.assertIn(BEAT, settings.BEAT_HEALTH_EXPECTATIONS)

    @task_flag_off
    def test_the_task_does_nothing_while_the_flag_is_off(self):
        from classrooms.tasks import run_missing_work_go_live

        summary = run_missing_work_go_live.run()
        self.assertIn(missing_work_go_live.FLAG_OFF, summary)
        self.assertFalse(GoLiveRun.objects.exists())


@task_flag_on
class BeatTaskTests(_GoLiveCase):
    def test_the_task_runs_it(self):
        from classrooms.tasks import run_missing_work_go_live

        summary = run_missing_work_go_live.run()
        self.assertIn(missing_work_go_live.COMPLETED, summary)
        self.assertEqual(self.grades(), [D("40.00"), D("40.00")])


@categories_on
class ManualCommandTests(_GoLiveCase):
    def test_a_full_apply_sets_the_marker_so_the_automatic_run_skips(self):
        self.run_command("--apply")
        self.assertIsNotNone(self.marker().completed_at)
        result = missing_work_go_live.run()
        self.assertEqual(result["status"], missing_work_go_live.ALREADY_DONE)

    def test_the_command_reports_the_marker_once_done(self):
        missing_work_go_live.run()
        out = self.run_command()
        self.assertIn("already ran", out)
        out = self.run_command("--apply")
        self.assertIn("already ran", out)
        self.assertEqual(self.changes().count(), 2)

    def test_a_full_apply_refuses_while_a_run_holds_the_claim(self):
        GoLiveRun.objects.create(
            name=NAME,
            claim_token="another-worker",
            claimed_until=timezone.now() + timedelta(minutes=5),
        )
        with self.assertRaises(CommandError):
            self.run_command("--apply")
        self.assertEqual(self.grades(), [D("80.00"), D("80.00")])

    def test_one_course_does_not_set_the_marker(self):
        self.run_command("--apply", "--course", str(self.courses[0].pk))
        self.assertEqual(self.grades(), [D("40.00"), D("80.00")])
        self.assertFalse(GoLiveRun.objects.filter(completed_at__isnull=False).exists())

    def test_a_dry_run_does_not_set_the_marker(self):
        self.run_command()
        self.assertFalse(GoLiveRun.objects.exists())
