"""H-58 / H-59: apply each mutant, run the seat tests, record killers, restore.
Every anchor must occur exactly once in its file: replace(..., 1) on a
non-unique anchor silently mutates the wrong site.
"""

import json
import os
import re
import subprocess
import sys

SVC = "billing/license_service.py"

MUTANTS = {
    "S1_no_seats_not_refused": (
        SVC,
        "        if max_seats <= 0:\n"
        '            raise LicenseRequestError("max_seats must be a positive integer")\n',
        "",
    ),
    "S2_listed_teachers_not_deduplicated": (
        SVC,
        "        return list(\n            dict.fromkeys(\n",
        "        return list(\n            list(\n",
    ),
    "S3_listed_teachers_not_lower_cased": (
        SVC,
        "                e.strip().lower() for e in (teacher_emails or []) if e and e.strip()\n",
        "                e.strip() for e in (teacher_emails or []) if e and e.strip()\n",
    ),
    "S4_carried_teachers_not_lower_cased": (
        SVC,
        "                email.strip().lower()\n                for email in CustomUser",
        "                email\n                for email in CustomUser",
    ),
    "S5_add_teachers_not_normalized": (
        SVC,
        "        for email in LicenseSubscriptionService.normalize_teacher_emails(\n"
        "            teacher_emails\n"
        "        ):\n",
        "        for email in teacher_emails:\n",
    ),
    "S6_account_lookup_exact_only": (
        SVC,
        '            or CustomUser.objects.filter(email__iexact=email).order_by("email").first()\n',
        "",
    ),
    "S7_invite_lookup_exact": (
        SVC,
        "        # Check if user with this email already exists, whatever its case\n"
        "        user = LicenseSubscriptionService.teacher_account_for_email(email)\n",
        "        user = CustomUser.objects.filter(email=email).first()\n",
    ),
    "S8_add_teachers_active_check_exact": (
        SVC,
        "        ):\n"
        "            user = LicenseSubscriptionService.teacher_account_for_email(email)\n",
        "        ):\n"
        "            user = CustomUser.objects.filter(email=email).first()\n",
    ),
}

TESTS = [
    "billing.tests.test_license_seat_counting",
    "billing.tests.test_license_seat_400",
    "billing.tests.test_license_teacher_changes_400",
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

with open("docs/evidence/license-seat-counting/mutation_results.json", "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
