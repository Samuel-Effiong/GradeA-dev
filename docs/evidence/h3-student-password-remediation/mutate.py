"""Apply each mutant to the command, run the H-3 tests, record killers, restore."""

import json
import re
import subprocess
import sys

CMD = "users/management/commands/remediate_student123_passwords.py"
orig = open(CMD).read()
MUTANTS = {
    "M1_remove_the_reset": (
        ".update(\n                        password=unusable\n                    )",
        ".update(\n                        password=old_hash\n                    )",
    ),
    "M2_execute_flag_ignored_dry_run_writes_nothing_or_all": (
        "        if not execute:\n",
        "        if False:\n",
    ),
    "M3_no_compare_and_set": (
        "User.objects.filter(pk=pk, password=old_hash).update",
        "User.objects.filter(pk=pk).update",
    ),
    "M4_model_check_password_rehashes_on_dry_run": (
        "check_password(LITERAL, user.password, setter=None)",
        "user.check_password(LITERAL)",
    ),
    "M5_target_everyone": (
        "if check_password(LITERAL, user.password, setter=None):",
        "if True:",
    ),
    "M6_report_leaks_hash": (
        '"previous_hash_algorithm": _algorithm(old_hash),',
        '"previous_hash_algorithm": old_hash,',
    ),
}
results = {}
try:
    for name, (a, b) in MUTANTS.items():
        assert a in orig, f"{name}: anchor not found"
        open(CMD, "w").write(orig.replace(a, b, 1))
        p = subprocess.run(
            [
                sys.executable,
                "manage.py",
                "test",
                "users.tests_remediate_student123",
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
finally:
    open(CMD, "w").write(orig)
json.dump(
    results,
    open("docs/evidence/h3-student-password-remediation/mutation_results.json", "w"),
    indent=2,
)
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
