"""Every Phase 2 switch is on by default (owner, 2026-11-18).

On a server (web, worker, Beat) with none of the four variables set,
BILLING_HOLDS_ENABLED, CREDIT_REQUESTS_ENABLED, TAGS_ENABLED and
STUDENT_DATA_TOOLS_ENABLED are all on. Under the test runner they stay off,
because the tests are written for off and turn features on themselves.
Setting a variable to false on a server still turns it off. Each case loads
the settings in a fresh interpreter (billing/tests/test_fresh_settings_support.py).

    python manage.py test billing.tests.test_phase2_defaults_on
"""

from django.test import SimpleTestCase

from billing.tests.test_fresh_settings_support import PHASE2_SWITCHES, fresh_settings


class Phase2DefaultsOnTests(SimpleTestCase):
    def test_all_four_on_on_a_server_with_nothing_set(self):
        self.assertEqual(
            fresh_settings(PHASE2_SWITCHES),
            {name: True for name in PHASE2_SWITCHES},
        )

    def test_all_four_off_under_the_test_runner_with_nothing_set(self):
        self.assertEqual(
            fresh_settings(PHASE2_SWITCHES, under_test_runner=True),
            {name: False for name in PHASE2_SWITCHES},
        )

    def test_holds_set_false_turns_holds_and_credit_requests_off(self):
        values = fresh_settings(PHASE2_SWITCHES, env={"BILLING_HOLDS_ENABLED": "false"})
        self.assertIs(values["BILLING_HOLDS_ENABLED"], False)
        self.assertIs(values["CREDIT_REQUESTS_ENABLED"], False)
        self.assertIs(values["TAGS_ENABLED"], True)
        self.assertIs(values["STUDENT_DATA_TOOLS_ENABLED"], True)

    def test_tags_and_student_data_set_false_stay_off(self):
        values = fresh_settings(
            PHASE2_SWITCHES,
            env={"TAGS_ENABLED": "false", "STUDENT_DATA_TOOLS_ENABLED": "false"},
        )
        self.assertIs(values["TAGS_ENABLED"], False)
        self.assertIs(values["STUDENT_DATA_TOOLS_ENABLED"], False)
        self.assertIs(values["BILLING_HOLDS_ENABLED"], True)
