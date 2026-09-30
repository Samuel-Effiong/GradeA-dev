"""audit.E001 (floor check): apply each mutant, run its tests, record killers, restore.
Every anchor must occur exactly once in its file: replace(..., 1) on a
non-unique anchor silently mutates the wrong site.
"""

import json
import os
import re
import subprocess
import sys

CHK = "audit/checks.py"
APPS = "audit/apps.py"

MUTANTS = {
    "F1_equal_floor_is_an_error": (
        CHK,
        "items() if value > floor}\n",
        "items() if value >= floor}\n",
    ),
    "F2_off_by_one_allowed": (
        CHK,
        "items() if value > floor}\n",
        "items() if value > floor + 1}\n",
    ),
    "F3_login_threshold_not_checked": (
        CHK,
        '        "CustomUser.MAX_LOGIN_ATTEMPTS": CustomUser.MAX_LOGIN_ATTEMPTS,\n',
        "",
    ),
    "F4_check_not_registered": (
        APPS,
        "        from . import checks  # noqa: F401 - registers the audit system checks\n",
        "",
    ),
    "F5_error_without_its_id": (
        CHK,
        '            id="audit.E001",\n',
        '            id="audit.E999",\n',
    ),
}

TESTS = ["audit.tests_checks"]
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

with open("docs/evidence/epic-a-floor-check/mutation_results.json", "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
