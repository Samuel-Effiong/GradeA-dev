"""
H-31 probe: does the mixed-traffic lockout test pass when logins return 500?

Records outcomes, asserts nothing, so the same probe runs on the tree before
and after the fix. It makes chosen login requests raise the exact error a
starved Postgres produces (`OperationalError: ... too many clients already`)
inside the login serializer; the app's exception handler turns that into an
HTTP 500, which is what c5's starved run logged for five bystander logins.
Then it runs the real, unmodified test method under that injection.

    PYTHONPATH=docs/evidence/h31_users_false_pass python manage.py test \
        probe_tolerant_mixed --settings=settings_worktree --noinput

Each probe prints: H31-PROBE <scenario> PASSED|FAILED <detail>.
PASSED under injection = the test cannot tell a 500 from success.
"""

import threading
from unittest import mock

from django.db import OperationalError

from users import tests_login_lockout as lockout
from users.serializers import CustomTokenObtainPairSerializer

STARVED = OperationalError(
    'connection to server at "127.0.0.1", port 5432 failed: '
    "FATAL:  sorry, too many clients already"
)
BYSTANDER_EMAIL = "bystander.lockout@example.com"
ORIGINAL_VALIDATE = CustomTokenObtainPairSerializer.validate


def injecting(*, bystander_all=False, victim_first=0):
    """Patch login validation at class level, so every worker thread sees it."""
    lock = threading.Lock()
    seen = {"victim": 0, "bystander": 0, "injected": 0}

    def validate(self, attrs):
        email = str(self.initial_data.get("email", "")).lower().strip()
        with lock:
            if email == BYSTANDER_EMAIL:
                seen["bystander"] += 1
                fail = bystander_all
            else:
                seen["victim"] += 1
                fail = seen["victim"] <= victim_first
            if fail:
                seen["injected"] += 1
        if fail:
            raise STARVED
        return ORIGINAL_VALIDATE(self, attrs)

    return (
        mock.patch.object(CustomTokenObtainPairSerializer, "validate", validate),
        seen,
    )


class H31Probe(lockout.LoginLockoutConcurrencyTests):
    """Only the probe_* methods are run (use the label below); the inherited
    tests are not the subject here."""

    TARGET = "test_mixed_concurrent_traffic_one_account_locks_another_is_unaffected"

    def run_target(self, scenario, **injection):
        patcher, seen = injecting(**injection)
        with patcher:
            try:
                getattr(self, self.TARGET)()
                outcome, detail = "PASSED", ""
            except AssertionError as exc:
                outcome, detail = "FAILED", str(exc).splitlines()[0][:160]
        print(
            f"\nH31-PROBE {scenario} {outcome} injected={seen['injected']} "
            f"victim_requests={seen['victim']} bystander_requests={seen['bystander']} "
            f"{detail}"
        )

    def test_probe_1_clean(self):
        self.run_target("clean-no-injection")

    def test_probe_2_every_bystander_login_500s(self):
        self.run_target("all-10-bystander-logins-500", bystander_all=True)

    def test_probe_3_most_victim_requests_500(self):
        # 25 victim requests, 20 fail: only 5 reach the counter, which is
        # exactly MAX_LOGIN_ATTEMPTS, the lower bound the test accepts.
        self.run_target("20-of-25-victim-requests-500", victim_first=20)
