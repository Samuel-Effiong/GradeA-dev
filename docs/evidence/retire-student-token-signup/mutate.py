"""Apply each landing-(A) mutant, run the suites that cover it, record
killers, restore. Every anchor must occur exactly once in its file:
replace(..., 1) on a non-unique anchor silently mutates the wrong site.
"""

import json
import re
import subprocess
import sys

ENROLL = "classrooms/services/enrollment.py"
ROSTER = "classrooms/services/roster_import.py"
BACKFILL = "classrooms/management/commands/backfill_pending_student_invites.py"

MUTANTS = {
    "R1_roster_names_not_passed": (
        ROSTER,
        "            first_name=row.first_name,\n",
        '            first_name="",\n',
    ),
    "R2_already_enrolled_not_skipped": (
        ROSTER,
        "    if (\n        existing is not None\n",
        "    if (\n        False\n        and existing is not None\n",
    ),
    "R3_new_student_ignores_names": (
        ENROLL,
        "        email=email,\n        first_name=first_name,\n",
        '        email=email,\n        first_name="",\n',
    ),
    "R4_promotion_keeps_the_old_code": (
        ENROLL,
        "        # no student row carries a code after onboarding.\n"
        "        student.activation_token = None\n",
        "        # no student row carries a code after onboarding.\n" "        pass\n",
    ),
    "B1_convert_keeps_the_old_code": (
        BACKFILL,
        "        student.must_change_password = True\n"
        "        student.activation_token = None\n",
        "        student.must_change_password = True\n",
    ),
    "B2_orphan_code_not_cleared": (
        BACKFILL,
        "                    self._clear_code(student)\n",
        "                    pass\n",
    ),
    "B3_dry_run_clears_codes": (
        BACKFILL,
        "                if not dry_run:\n                    self._clear_code(student)\n",
        "                if True:\n                    self._clear_code(student)\n",
    ),
    "B4_output_names_the_email": (
        BACKFILL,
        '                f"{prefix}convert: student {student.pk} (pending course "\n',
        '                f"{prefix}convert: student {student.email} (pending course "\n',
    ),
    "B5_placeholder_not_flagged": (
        BACKFILL,
        "            no_mailbox = student.email.endswith(PLACEHOLDER_DOMAIN)\n",
        "            no_mailbox = False\n",
    ),
}

TESTS = [
    "classrooms.tests_roster_ready_to_use",
    "classrooms.tests_backfill_pending_student_invites",
    "classrooms.test_bulk_enrollment",
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

with open("docs/evidence/retire-student-token-signup/mutation_results.json", "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
