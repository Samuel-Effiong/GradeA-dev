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

    def test_reports_the_running_commit(self):
        """
        The whole point of the field: answering "is the fix deployed?"
        without inferring it from behaviour. A settings-only change
        alters no API surface, so without this "not deployed yet" and
        "deployed but misconfigured" are indistinguishable from outside.
        """
        # Deliberately not a realistic 40-char hex SHA: detect-secrets
        # flags any long hex literal as a possible leaked credential, and
        # black keeps relocating an inline `pragma: allowlist secret` off
        # the offending line. Nothing here depends on the value being
        # hex - only on it being longer than the 12 characters kept.
        sha = "commit-sha-not-hex-1234"

        with patch.dict("os.environ", {"RAILWAY_GIT_COMMIT_SHA": sha}, clear=False):
            response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["version"], "commit-sha-n")
        self.assertEqual(len(response.data["version"]), 12)

    def test_version_is_unknown_rather_than_absent_when_unset(self):
        """
        A stable key shape keeps the response trivial to parse, and the
        endpoint must never fail because of its own metadata.
        """
        with patch.dict("os.environ", {}, clear=True):
            response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["version"], "unknown")

    def test_version_does_not_gate_health(self):
        """
        A missing commit SHA is a metadata gap, not an outage: it must
        never turn a healthy node's 200 into a 503 and take it out of
        rotation.
        """
        with patch.dict("os.environ", {}, clear=True):
            response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "ok")

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

    def test_cache_write_that_silently_no_ops_is_reported_as_degraded(self):
        """
        _check_cache round-trips (set then get) specifically to catch a
        write that silently no-ops - a bare cache.set() would pass even
        against a misconfigured or evicting-immediately backend. Simulate
        that exact failure mode: the write "succeeds" but the readback
        doesn't match.
        """
        with patch("AutoGrader.health.cache") as mock_cache:
            mock_cache.get.return_value = None  # set() didn't actually stick

            response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["status"], "degraded")
        self.assertIn(
            "did not return the value just written", response.data["checks"]["cache"]
        )
        # An unrelated healthy dependency should still report as healthy.
        self.assertEqual(response.data["checks"]["database"], "ok")


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
