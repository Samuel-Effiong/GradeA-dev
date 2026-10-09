"""Add-teachers school-name leak: apply each mutant, run its tests, record killers, restore.
Every anchor must occur exactly once in its file: replace(..., 1) on a
non-unique anchor silently mutates the wrong site.
"""

import json
import os
import re
import subprocess
import sys

LS = "billing/license_service.py"

MUTANTS = {
    "N1_names_the_other_school_again": (
        LS,
        '                error_msg = "This teacher already belongs to another school."\n',
        '                error_msg = f"This teacher already belongs to {user.school.name}."\n',
    ),
    "N2_log_carries_the_email": (
        LS,
        '                    "Teacher %s belongs to school %s, not %s: not enrolled.",\n'
        "                    user.id,\n",
        '                    "Teacher %s belongs to school %s, not %s: not enrolled.",\n'
        "                    user.email,\n",
    ),
    "N3_the_teacher_is_enrolled_anyway": (
        LS,
        "            if user.school and user.school != school:\n",
        "            if False:\n",
    ),
    # SM ruling: the role of a non-teacher account is not disclosed.
    "N4_names_the_role_again": (
        LS,
        '                error_msg = "This email can\'t be added as a teacher."\n',
        '                error_msg = f"This email belongs to a {user.user_type} account."\n',
    ),
    "N5_batch_log_carries_the_email_again": (
        LS,
        '                "Skipped enrolling a teacher in license %s: %s",\n'
        "                license_sub.id,\n",
        '                "Skipped enrolling %s in license %s: %s",\n'
        "                email,\n"
        "                license_sub.id,\n",
    ),
}

TESTS = ["billing.tests.test_add_teachers_other_school_not_disclosed"]
originals: dict = {}
results = {}
try:
    for name, (path, a, b) in MUTANTS.items():
        src = originals.setdefault(path, open(path).read())
        assert src.count(a) == 1, f"{name}: anchor found {src.count(a)} times"
        open(path, "w").write(src.replace(a, b, 1))
        p = subprocess.run(
            [sys.executable, "manage.py", "test", *TESTS]
            + ["--settings=settings_worktree", "--keepdb", "--noinput"],
            capture_output=True,
            text=True,
            env={**os.environ, "EXEMPT_EMAIL_DOMAINS": ""},
        )
        out = p.stdout + p.stderr
        failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", out, re.M)))
        results[name] = {"killed": p.returncode != 0, "failing_tests": failed}
        print(name, results[name], flush=True)
        open(path, "w").write(src)
finally:
    for path, src in originals.items():
        open(path, "w").write(src)

with open("docs/evidence/add-teachers-school-leak/mutation_results.json", "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
