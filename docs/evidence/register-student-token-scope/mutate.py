"""Apply each H-47 mutant, run the suites that cover it, record killers, restore.

Every anchor must occur exactly once in its file: replace(..., 1) on a
non-unique anchor silently mutates the wrong site.
"""

import json
import re
import subprocess
import sys

VIEWS = "users/views.py"
THROTTLING = "users/throttling.py"
ENROLLMENT = "classrooms/services/enrollment.py"
CLASSROOMS_VIEWS = "classrooms/views.py"

MUTANTS = {
    "M1_register_lookup_not_scoped_to_students": (
        VIEWS,
        "                    is_active=False,\n"
        "                    user_type=UserTypes.STUDENT,\n",
        "                    is_active=False,\n",
    ),
    "M2_renew_lookup_not_scoped_to_students": (
        ENROLLMENT,
        "activation_token=token, is_active=False, user_type=UserTypes.STUDENT",
        "activation_token=token, is_active=False",
    ),
    "M3_budget_never_checked": (
        VIEWS,
        "        if register_student_failure_budget_spent():\n",
        "        if False:\n",
    ),
    "M4_no_match_not_recorded": (
        VIEWS,
        '                    record_register_student_failure("no_match")\n',
        "                    pass\n",
    ),
    "M5_expired_not_recorded": (
        VIEWS,
        '                    record_register_student_failure("expired")\n',
        "                    pass\n",
    ),
    "M6_budget_off_by_one": (
        THROTTLING,
        "    return count >= settings.REGISTER_STUDENT_GLOBAL_FAILURE_LIMIT\n",
        "    return count > settings.REGISTER_STUDENT_GLOBAL_FAILURE_LIMIT\n",
    ),
    "M7_window_never_rolls_over": (
        THROTTLING,
        '    return f"register_student:failures:{bucket}"\n',
        '    return "register_student:failures:0"\n',
    ),
    # The refusal raised as something other than Throttled: the exception
    # handler then answers 500, not 429.
    "M8_refusal_is_not_a_throttled_error": (
        VIEWS,
        "            raise Throttled(\n",
        "            raise RuntimeError(\n",
    ),
    "M10_exhausted_error_fires_on_every_failure_past_the_limit": (
        THROTTLING,
        "    if count == settings.REGISTER_STUDENT_GLOBAL_FAILURE_LIMIT:\n",
        "    if count >= settings.REGISTER_STUDENT_GLOBAL_FAILURE_LIMIT:\n",
    ),
    "M11_renew_door_ignores_the_budget": (
        CLASSROOMS_VIEWS,
        "        if register_student_failure_budget_spent():\n"
        '            log_register_student_refused_by_budget(door="renew")\n',
        "        if False:\n"
        '            log_register_student_refused_by_budget(door="renew")\n',
    ),
    "M12_renew_failures_not_counted": (
        CLASSROOMS_VIEWS,
        '            record_register_student_failure("renew_refused")\n',
        "            pass\n",
    ),
    "M9_log_includes_the_token": (
        THROTTLING,
        '        extra={"reason": reason, "window_failures": count},\n',
        '        extra={"reason": reason, "window_failures": count, "t": "987654"},\n',
    ),
}

TESTS = [
    "users.tests_register_student_token_scope",
    "users.tests_auth_input_validation",
    "classrooms.tests",
]

originals = {}
results = {}
try:
    for name, (path, a, b) in MUTANTS.items():
        src = originals.setdefault(path, open(path).read())
        assert src.count(a) == 1, f"{name}: anchor found {src.count(a)} times"
        open(path, "w").write(src.replace(a, b, 1))
        p = subprocess.run(
            [sys.executable, "manage.py", "test", *TESTS]
            + ["--settings=settings_worktree", "--keepdb"],
            capture_output=True,
            text=True,
        )
        out = p.stdout + p.stderr
        failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", out, re.M)))
        results[name] = {"killed": p.returncode != 0, "failing_tests": failed}
        print(name, results[name], flush=True)
        open(path, "w").write(src)
finally:
    for path, src in originals.items():
        open(path, "w").write(src)

with open("docs/evidence/register-student-token-scope/mutation_results.json", "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
