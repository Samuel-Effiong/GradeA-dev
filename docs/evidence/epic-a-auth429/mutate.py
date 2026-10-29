"""Auth-lock envelope: apply each mutant, run its tests, record killers, restore.
Every anchor must occur exactly once in its file: replace(..., 1) on a
non-unique anchor silently mutates the wrong site.
"""

import json
import os
import re
import subprocess
import sys

RC = "AutoGrader/reason_codes.py"
EXC = "users/exceptions.py"
VIEWS = "users/views.py"
SER = "users/serializers.py"

MUTANTS = {
    "L1_envelope_overwrites_documented_keys": (
        RC,
        "        if key in body and key not in data:\n",
        "        if key in body:\n",
    ),
    "L2_reset_lock_without_envelope": (
        VIEWS,
        'add_coded_envelope(body, "RESET_LOCKED", message)',
        "body",
    ),
    "L3_handler_adds_no_envelope": (
        EXC,
        "        if envelope is not None and isinstance(drf_response.data, dict):\n",
        "        if False:\n",
    ),
    "L4_verify_lock_has_no_code": (
        VIEWS,
        '                code_value="VERIFY_LOCKED",\n',
        "                code_value=None,\n",
    ),
    "L5_login_lock_loses_legacy_code": (
        SER,
        '                code_value="account_locked",\n',
        "                code_value=None,\n",
    ),
    "L6_a_second_text_key_changes_the_display": (
        RC,
        '    if code_value is not None and "code" not in data:\n',
        '    data.setdefault("error", message)\n'
        '    if code_value is not None and "code" not in data:\n',
    ),
}

TESTS = [
    "users.tests_auth_lock_envelope",
    "users.tests_auth_audit_doors",
    "AutoGrader.tests_reason_codes",
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

with open("docs/evidence/epic-a-auth429/mutation_results.json", "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
