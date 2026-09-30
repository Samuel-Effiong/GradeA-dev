"""audit.E001: the failed-auth floor must cover every sign-in lock threshold
(v2's N1 on the beta merge-down, SM order)."""

import os
import subprocess
import sys
from contextlib import AbstractContextManager
from unittest.mock import patch

from django.conf import settings
from django.core.checks import run_checks
from django.test import SimpleTestCase, override_settings

from audit.checks import check_failed_auth_floor_covers_every_lock, lock_thresholds
from users.models import CustomUser, PasswordResetOTP


def e001(errors):
    return [e for e in errors if e.id == "audit.E001"]


class FailedAuthFloorCheckTests(SimpleTestCase):
    def test_the_shipped_settings_pass(self):
        self.assertEqual(check_failed_auth_floor_covers_every_lock(None), [])

    def test_the_check_is_registered(self):
        """It runs with every `manage.py check`, not only when called."""
        with override_settings(FAILED_AUTH_TARGET_FLOOR=1):
            self.assertEqual(len(e001(run_checks(tags=["audit"]))), 1)

    def test_manage_py_check_fails_on_a_floor_set_below_a_threshold(self):
        """The deploy-time proof. This test module imports audit.checks
        itself, which registers the check in THIS process however the app is
        wired - so only a fresh process shows that AuditConfig.ready()
        registers it. The floor comes from the environment, as in
        production."""
        env = {**os.environ, "FAILED_AUTH_TARGET_FLOOR": "1"}
        env.setdefault("DJANGO_SETTINGS_MODULE", "AutoGrader.settings")
        result = subprocess.run(
            [sys.executable, "manage.py", "check", "--tag", "audit"],
            cwd=settings.BASE_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("audit.E001", result.stdout + result.stderr)

    def test_a_floor_below_the_thresholds_is_an_error(self):
        with override_settings(FAILED_AUTH_TARGET_FLOOR=4):
            errors = check_failed_auth_floor_covers_every_lock(None)
        self.assertEqual(len(e001(errors)), 1)
        for name in lock_thresholds():
            self.assertIn(name, errors[0].msg)

    def test_a_floor_equal_to_the_largest_threshold_passes(self):
        with override_settings(
            FAILED_AUTH_TARGET_FLOOR=max(lock_thresholds().values())
        ):
            self.assertEqual(check_failed_auth_floor_covers_every_lock(None), [])

    def test_each_threshold_raised_above_the_floor_is_caught(self):
        above = settings.FAILED_AUTH_TARGET_FLOOR + 1
        raised: dict[str, AbstractContextManager] = {
            "VERIFY_EMAIL_MAX_FAILURES": override_settings(
                VERIFY_EMAIL_MAX_FAILURES=above
            ),
            "CustomUser.MAX_LOGIN_ATTEMPTS": patch.object(
                CustomUser, "MAX_LOGIN_ATTEMPTS", above
            ),
            "PasswordResetOTP.MAX_ATTEMPTS": patch.object(
                PasswordResetOTP, "MAX_ATTEMPTS", above
            ),
        }
        self.assertEqual(set(raised), set(lock_thresholds()))
        for name, change in raised.items():
            with self.subTest(threshold=name), change:
                errors = e001(check_failed_auth_floor_covers_every_lock(None))
                self.assertEqual(len(errors), 1)
                self.assertIn(name, errors[0].msg)
