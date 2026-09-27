"""Apply each mutant, run the AUTHZ-PATCHPW suites, record killers, restore."""

import json
import re
import subprocess
import sys

S = "users/serializers.py"
SELF = 'if "password" in attrs and self._is_acting_on_self():'
EMAIL_COND = (
    "            self._is_acting_on_self()\n"
    '            and "email" in attrs\n'
    '            and attrs["email"] != self.instance.email\n'
)
MUTANTS = {
    "P1_password_rejection_removed": (SELF, 'if "password" in attrs and False:'),
    "P2_password_rejected_for_everyone": (SELF, 'if "password" in attrs:'),
    "P3_self_check_inverted": (
        'and getattr(acting, "pk", None) == self.instance.pk',
        'and getattr(acting, "pk", None) != self.instance.pk',
    ),
    "E1_email_guard_removed": (
        "            self._require_current_password(current_password)\n",
        "            pass\n",
    ),
    "E2_guard_fires_on_same_value_email": (
        '            and attrs["email"] != self.instance.email\n',
        "            and True\n",
    ),
    "E3_wrong_password_not_counted": (
        "            user.register_failed_login()\n",
        "            pass\n",
    ),
    "E4_lockout_check_removed": (
        "        if user.is_account_locked():\n",
        "        if False:\n",
    ),
    "E5_wrong_password_accepted": (
        "        if not user.check_password(current_password):\n",
        "        if False:\n",
    ),
    "E6_unusable_password_case_removed": (
        "        if not user.has_usable_password():\n",
        "        if False:\n",
    ),
    "E7_success_keeps_failure_counter": (
        "        user.reset_login_lockout()\n",
        "        pass\n",
    ),
    "E8_guard_applies_to_other_users_too": (
        EMAIL_COND,
        EMAIL_COND.replace(
            "            self._is_acting_on_self()\n", "            True\n"
        ),
    ),
    "E9_current_password_not_consumed": (
        'current_password = attrs.pop("current_password", None)',
        'current_password = attrs.get("current_password", None)',
    ),
}
src = open(S).read()
results = {}
try:
    for name, (a, b) in MUTANTS.items():
        assert a in src, f"{name}: anchor not found"
        open(S, "w").write(src.replace(a, b, 1))
        p = subprocess.run(
            [
                sys.executable,
                "manage.py",
                "test",
                "users.tests_patch_password",
                "users.tests_patch_email",
                "--settings=settings_worktree",
                "--keepdb",
            ],
            capture_output=True,
            text=True,
        )
        out = p.stdout + p.stderr
        failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", out, re.M)))
        results[name] = {"killed": p.returncode != 0, "failing_tests": failed}
        print(name, results[name], flush=True)
        open(S, "w").write(src)
finally:
    open(S, "w").write(src)
with open("docs/evidence/authz-patch-password/mutation_results.json", "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
