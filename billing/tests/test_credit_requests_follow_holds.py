"""Credit requests (B1c) come on with B1b (owner, 2026-11-12).

CREDIT_REQUESTS_ENABLED, when not set in the environment, takes the value of
BILLING_HOLDS_ENABLED. Set explicitly, true or false, it wins. Holds are on
by default on a server (owner, 2026-11-18), so both unset is on. Settings are
read once at start-up, so each case loads them in a fresh interpreter
(billing/tests/test_fresh_settings_support.py), as a server would.

    python manage.py test billing.tests.test_credit_requests_follow_holds
"""

from django.test import SimpleTestCase

from billing.tests.test_fresh_settings_support import fresh_settings

HOLDS = "BILLING_HOLDS_ENABLED"
REQUESTS = "CREDIT_REQUESTS_ENABLED"


def requests_flag(holds=None, requests=None):
    """settings.CREDIT_REQUESTS_ENABLED as a fresh server start-up reads it,
    with the two variables set as given (None: not in the environment)."""
    env = {}
    if holds is not None:
        env[HOLDS] = holds
    if requests is not None:
        env[REQUESTS] = requests
    return str(fresh_settings([REQUESTS], env=env, strip=[HOLDS, REQUESTS])[REQUESTS])


class CreditRequestsFollowHoldsTests(SimpleTestCase):
    def test_both_unset_is_on(self):
        self.assertEqual(requests_flag(), "True")

    def test_holds_on_and_requests_unset_is_on(self):
        self.assertEqual(requests_flag(holds="true"), "True")

    def test_holds_off_and_requests_unset_is_off(self):
        self.assertEqual(requests_flag(holds="false"), "False")

    def test_holds_on_and_requests_set_false_stays_off(self):
        self.assertEqual(requests_flag(holds="true", requests="false"), "False")

    def test_holds_off_and_requests_set_true_is_on(self):
        self.assertEqual(requests_flag(holds="false", requests="true"), "True")
