"""Tests for the unauthenticated health-check endpoint."""

from unittest.mock import patch

from django.urls import reverse
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
