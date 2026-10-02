from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from django_celery_beat.models import CrontabSchedule, PeriodicTask

from AutoGrader.beat_health import _humanize, check_beat_health, find_overdue_tasks


class HumanizeTests(TestCase):
    def test_minutes_only(self):
        self.assertEqual(_humanize(timedelta(minutes=42)), "42 minutes")

    def test_singular_minute(self):
        self.assertEqual(_humanize(timedelta(minutes=1)), "1 minute")

    def test_hours_and_minutes(self):
        self.assertEqual(_humanize(timedelta(hours=1, minutes=5)), "1 hour, 5 minutes")

    def test_days_hours_and_minutes(self):
        self.assertEqual(
            _humanize(timedelta(days=2, hours=3, minutes=1)),
            "2 days, 3 hours, 1 minute",
        )

    def test_exact_hour_omits_zero_minutes(self):
        self.assertEqual(_humanize(timedelta(hours=2)), "2 hours")

    def test_zero_renders_as_zero_minutes_not_empty(self):
        self.assertEqual(_humanize(timedelta(0)), "0 minutes")

    def test_negative_delta_clamped_to_zero(self):
        # Defensive: a clock skew or a reference_time in the future must
        # not render as a nonsensical negative duration.
        self.assertEqual(_humanize(timedelta(minutes=-5)), "0 minutes")


EXPECTATIONS = {
    "fast-task": (timedelta(minutes=5), timedelta(minutes=15)),
    "daily-task": (timedelta(days=1), timedelta(days=2)),
}


@override_settings(BEAT_HEALTH_EXPECTATIONS=EXPECTATIONS)
class FindOverdueTasksTests(TestCase):
    def setUp(self):
        self.crontab = CrontabSchedule.objects.create(minute="*/5")

    def _make(self, name, last_run_at, enabled=True):
        return PeriodicTask.objects.create(
            name=name,
            task="tests.dummy_task",
            crontab=self.crontab,
            enabled=enabled,
            last_run_at=last_run_at,
        )

    def test_on_schedule_task_is_not_overdue(self):
        now = timezone.now()
        self._make("fast-task", last_run_at=now - timedelta(minutes=3))
        self._make("daily-task", last_run_at=now - timedelta(hours=1))

        self.assertEqual(find_overdue_tasks(now=now), [])

    def test_overdue_task_is_reported_with_actionable_message(self):
        now = timezone.now()
        self._make("fast-task", last_run_at=now - timedelta(minutes=42))
        self._make("daily-task", last_run_at=now - timedelta(hours=1))

        overdue = find_overdue_tasks(now=now)

        self.assertEqual(len(overdue), 1)
        self.assertEqual(
            overdue[0],
            "fast-task has not run for 42 minutes; expected every 5 minutes.",
        )

    def test_multiple_overdue_tasks_are_all_reported(self):
        now = timezone.now()
        self._make("fast-task", last_run_at=now - timedelta(minutes=42))
        self._make("daily-task", last_run_at=now - timedelta(days=3))

        overdue = find_overdue_tasks(now=now)

        self.assertEqual(len(overdue), 2)

    def test_missing_periodic_task_row_is_reported(self):
        now = timezone.now()
        self._make("fast-task", last_run_at=now)
        # "daily-task" has no PeriodicTask row at all - schedule missing
        # or renamed without updating BEAT_HEALTH_EXPECTATIONS.

        overdue = find_overdue_tasks(now=now)

        self.assertEqual(len(overdue), 1)
        self.assertIn("daily-task has no matching scheduled task", overdue[0])

    def test_disabled_task_is_never_reported(self):
        now = timezone.now()
        self._make("fast-task", last_run_at=now - timedelta(days=10), enabled=False)
        self._make("daily-task", last_run_at=now)

        self.assertEqual(find_overdue_tasks(now=now), [])

    def test_never_run_task_uses_date_changed_as_reference(self):
        now = timezone.now()
        task = self._make("fast-task", last_run_at=None)
        PeriodicTask.objects.filter(pk=task.pk).update(
            date_changed=now - timedelta(minutes=42)
        )
        self._make("daily-task", last_run_at=now)

        overdue = find_overdue_tasks(now=now)

        self.assertEqual(len(overdue), 1)
        self.assertIn("fast-task has not run for 42 minutes", overdue[0])

    def test_no_expectations_configured_returns_empty(self):
        with override_settings(BEAT_HEALTH_EXPECTATIONS={}):
            self.assertEqual(find_overdue_tasks(), [])


@override_settings(BEAT_HEALTH_EXPECTATIONS=EXPECTATIONS)
class CheckBeatHealthTaskTests(TestCase):
    def setUp(self):
        self.crontab = CrontabSchedule.objects.create(minute="*/5")

    def test_returns_all_clear_message_when_nothing_overdue(self):
        now = timezone.now()
        PeriodicTask.objects.create(
            name="fast-task",
            task="tests.dummy_task",
            crontab=self.crontab,
            last_run_at=now,
        )
        PeriodicTask.objects.create(
            name="daily-task",
            task="tests.dummy_task",
            crontab=self.crontab,
            last_run_at=now,
        )

        result = check_beat_health()

        self.assertEqual(result, "All monitored Beat tasks are on schedule.")

    @patch("AutoGrader.beat_health.logger")
    def test_logs_error_and_names_the_overdue_task(self, mock_logger):
        now = timezone.now()
        PeriodicTask.objects.create(
            name="fast-task",
            task="tests.dummy_task",
            crontab=self.crontab,
            last_run_at=now - timedelta(minutes=42),
        )
        PeriodicTask.objects.create(
            name="daily-task",
            task="tests.dummy_task",
            crontab=self.crontab,
            last_run_at=now,
        )

        result = check_beat_health()

        mock_logger.error.assert_called_once()
        self.assertIn("fast-task has not run for 42 minutes", result)
        # "Celery is broken" style non-answers are exactly what this must
        # not collapse to.
        self.assertNotEqual(result, "Celery is broken.")
