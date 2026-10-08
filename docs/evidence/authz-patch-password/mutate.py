"""Apply each mutant, run the AUTHZ-PATCHPW suites, record killers, restore."""

import json
import re
import subprocess
import sys

S = "users/serializers.py"
SELF = 'if "password" in attrs and self._is_acting_on_self():'
# Part 2 as reworked 2026-09-28 (refuse any email change, every caller).
# The previous E1-E9 mutants targeted the removed current_password
# mechanism; their record is in git history (mutation_log.txt @ 1db6e56).
# Two lines, not one: "self.instance is not None" at this indent also
# appears in _is_acting_on_self, and replace(..., 1) hits the first match.
GUARD_HEAD = '            self.instance is not None\n            and "email" in attrs\n'
GUARD_CMP = (
    '            and attrs["email"] != (self.instance.email or "").lower().strip()\n'
)
RAISE = '                {"email": "Email address can\'t be changed."}\n'
MUTANTS = {
    "P1_password_rejection_removed": (SELF, 'if "password" in attrs and False:'),
    "P2_password_rejected_for_everyone": (SELF, 'if "password" in attrs:'),
    "P3_self_check_inverted": (
        'and getattr(acting, "pk", None) == self.instance.pk',
        'and getattr(acting, "pk", None) != self.instance.pk',
    ),
    "E1_email_guard_removed": (
        GUARD_HEAD,
        '            False\n            and "email" in attrs\n',
    ),
    "E2_guard_fires_on_same_value_email": (GUARD_CMP, "            and True\n"),
    # The stored side compared without lower/strip (the incoming side is
    # already normalised by validate_email). The first version of this mutant
    # compared against .upper(), which duplicated E2; corrected per the
    # Verification Engineer.
    "E3_stored_email_not_normalised": (
        GUARD_CMP,
        '            and attrs["email"] != self.instance.email\n',
    ),
    "E4_guard_only_for_self": (
        GUARD_HEAD,
        '            self._is_acting_on_self()\n            and "email" in attrs\n',
    ),
    "E5_guard_also_on_create": (
        GUARD_HEAD,
        '            True\n            and "email" in attrs\n',
    ),
    "E6_refusal_on_wrong_field": (
        RAISE,
        '                {"non_field_errors": "Email address can\'t be changed."}\n',
    ),
}
src = open(S).read()
results = {}
try:
    for name, (a, b) in MUTANTS.items():
        assert src.count(a) == 1, f"{name}: anchor found {src.count(a)} times"
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
