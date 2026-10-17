"""H-71: apply each mutant, run its tests, record killers, restore.
Every anchor must occur exactly once in its file: replace(..., 1) on a
non-unique anchor silently mutates the wrong site.
"""

import json
import os
import re
import subprocess
import sys

S = "classrooms/serializers.py"
N = "classrooms/services/enrollment.py"
RAISE = "            raise serializers.ValidationError(NOT_A_STUDENT_MESSAGE)\n"
ROLE_NAMED = (
    "            raise serializers.ValidationError(\n"
    '                "This email already exists in the system and cannot be added as a "\n'
    '                f"{existing_user.get_user_type_display().lower()}."\n'
    "            )\n"
)
SINGLE_CTX = "        # doesn't tell a teacher which addresses belong to staff.\n"
DIRECT_CTX = (
    "        # and fail later as a 500, so the status code told the roles apart.\n"
)
NON_STUDENT = (
    "        if existing_user and existing_user.user_type != UserTypes.STUDENT:\n"
)

MUTANTS = {
    "M1_service_names_the_role": (
        N,
        "        raise EnrollmentError(NOT_A_STUDENT_MESSAGE)\n",
        "        raise EnrollmentError(\n"
        '            f"This email belongs to a {student.get_user_type_display().lower()} "\n'
        '            "account and cannot be added as a student."\n'
        "        )\n",
    ),
    "M2_single_add_names_the_role": (
        S,
        SINGLE_CTX + NON_STUDENT + RAISE,
        SINGLE_CTX + NON_STUDENT + ROLE_NAMED,
    ),
    "M3_direct_add_refuses_only_teachers": (
        S,
        DIRECT_CTX + NON_STUDENT,
        DIRECT_CTX
        + "        if existing_user and existing_user.user_type == UserTypes.TEACHER:\n",
    ),
    "M4_direct_add_names_the_role": (
        S,
        DIRECT_CTX + NON_STUDENT + RAISE,
        DIRECT_CTX + NON_STUDENT + ROLE_NAMED,
    ),
    "M5_refusal_not_logged": (
        N,
        '            "Refused to enrol non-student account %s (%s) in course %s",\n'
        "            student.pk,\n",
        '            "Refused to enrol a non-student account (%s) in course %s",\n',
    ),
}

TESTS = [
    "classrooms.tests_h71_student_add_role",
]
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

with open("docs/evidence/h71-student-add-role/mutation_results.json", "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
