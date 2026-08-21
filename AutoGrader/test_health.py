"""Tests for the unauthenticated health-check endpoints."""

from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone
from django_celery_beat.models import CrontabSchedule, PeriodicTask
from rest_framework import status
from rest_framework.test import APITestCase


class HealthCheckTests(APITestCase):
    def test_health_is_reachable_without_authentication(self):
        """A load balancer polling this has no token, so it must not 401."""
        response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "ok")
        self.assertEqual(response.data["checks"]["database"], "ok")
        self.assertEqual(response.data["checks"]["cache"], "ok")

    def test_reports_503_and_names_the_failing_dependency(self):
        """
        A failing dependency has to be identifiable from the response, or
        the alert says only "health check failed" and someone has to go
        digging at 3am.
        """
        with patch(
            "AutoGrader.health._check_database",
            side_effect=RuntimeError("connection refused"),
        ):
            response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["status"], "degraded")
        self.assertIn("connection refused", response.data["checks"]["database"])
        # An unrelated healthy dependency should still report as healthy.
        self.assertEqual(response.data["checks"]["cache"], "ok")


class BeatHealthCheckTests(APITestCase):
    """
    AutoGrader.health.beat_health_check reads the watchdog task's own
    last_run_at (see AutoGrader/beat_health.py) - it does not run the
    watchdog logic itself, so these tests only need to set up the
    PeriodicTask row, not the wider CELERY_BEAT_SCHEDULE.
    """

    def setUp(self):
        self.crontab = CrontabSchedule.objects.create(minute="*/15")

    def test_reachable_without_authentication(self):
        PeriodicTask.objects.create(
            name="check-beat-health",
            task="AutoGrader.beat_health.check_beat_health",
            crontab=self.crontab,
            enabled=True,
            last_run_at=timezone.now(),
        )

        response = self.client.get(reverse("beat-health"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["checks"]["beat"], "ok")

    def test_missing_periodic_task_row_is_degraded(self):
        # No PeriodicTask created at all - e.g. a fresh environment whose
        # migrations haven't seeded CELERY_BEAT_SCHEDULE into the DB yet.
        response = self.client.get(reverse("beat-health"))

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertIn("not registered", response.data["checks"]["beat"])

    def test_disabled_watchdog_is_degraded(self):
        PeriodicTask.objects.create(
            name="check-beat-health",
            task="AutoGrader.beat_health.check_beat_health",
            crontab=self.crontab,
            enabled=False,
            last_run_at=timezone.now(),
        )

        response = self.client.get(reverse("beat-health"))

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertIn("disabled", response.data["checks"]["beat"])

    def test_stale_last_run_is_degraded(self):
        # Beat itself checks in every 15 minutes; a 2-hour gap is well
        # past the 45-minute alert threshold.
        PeriodicTask.objects.create(
            name="check-beat-health",
            task="AutoGrader.beat_health.check_beat_health",
            crontab=self.crontab,
            enabled=True,
            last_run_at=timezone.now() - timezone.timedelta(hours=2),
        )

        response = self.client.get(reverse("beat-health"))

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertIn("120 minutes ago", response.data["checks"]["beat"])

    def test_never_run_falls_back_to_date_changed(self):
        # A brand new row with no last_run_at yet must not crash the check
        # (None - now() would raise), and should be judged against when it
        # was created/last edited instead.
        stale_task = PeriodicTask.objects.create(
            name="check-beat-health",
            task="AutoGrader.beat_health.check_beat_health",
            crontab=self.crontab,
            enabled=True,
        )
        PeriodicTask.objects.filter(pk=stale_task.pk).update(
            date_changed=timezone.now() - timezone.timedelta(hours=2)
        )

        response = self.client.get(reverse("beat-health"))

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
