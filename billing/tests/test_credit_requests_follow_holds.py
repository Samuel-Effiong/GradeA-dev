"""Credit requests (B1c) come on with B1b (owner, 2026-11-12).

CREDIT_REQUESTS_ENABLED, when not set in the environment, takes the value of
BILLING_HOLDS_ENABLED. Set explicitly, true or false, it wins. Settings are
read once at start-up, so each case loads them in a fresh interpreter.

    python manage.py test billing.tests.test_credit_requests_follow_holds
"""

import os
import subprocess  # nosec B404: a fixed command, no shell
import sys

from django.conf import settings
from django.test import SimpleTestCase

HOLDS = "BILLING_HOLDS_ENABLED"
REQUESTS = "CREDIT_REQUESTS_ENABLED"


def requests_flag(holds=None, requests=None):
    """settings.CREDIT_REQUESTS_ENABLED as a fresh start-up reads it, with the
    two variables set as given (None: not in the environment at all)."""
    env = {k: v for k, v in os.environ.items() if k not in (HOLDS, REQUESTS)}
    if holds is not None:
        env[HOLDS] = holds
    if requests is not None:
        env[REQUESTS] = requests
    env["DJANGO_SETTINGS_MODULE"] = "AutoGrader.settings"
    done = subprocess.run(  # nosec B603: a fixed command, no shell
        [
            sys.executable,
            "-c",
            "import sys; sys.argv = ['manage.py', 'test']; "
            "from django.conf import settings; "
            "print(settings.CREDIT_REQUESTS_ENABLED)",
        ],
        cwd=settings.BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return done.stdout.strip().splitlines()[-1]


class CreditRequestsFollowHoldsTests(SimpleTestCase):
    def test_both_unset_is_off(self):
        self.assertEqual(requests_flag(), "False")

    def test_holds_on_and_requests_unset_is_on(self):
        self.assertEqual(requests_flag(holds="true"), "True")

    def test_holds_on_and_requests_set_false_stays_off(self):
        self.assertEqual(requests_flag(holds="true", requests="false"), "False")

    def test_holds_off_and_requests_set_true_is_on(self):
        self.assertEqual(requests_flag(holds="false", requests="true"), "True")
