"""Epic A S2: apply each mutant, run the S2 tests, record killers, restore.
Every anchor must occur exactly once in its file: replace(..., 1) on a
non-unique anchor silently mutates the wrong site.
"""

import json
import os
import re
import subprocess
import sys

RA = "audit/request_audit.py"
MW = "audit/middleware.py"
VIEWS = "users/views.py"

MUTANTS = {
    "D1_a_door_left_out_of_the_registry": (
        RA,
        '    "login": (AuditAction.AUTH_LOGIN, "password"),\n',
        "",
    ),
    "D2_refusal_never_recorded": (
        RA,
        "        if door is None:\n            return\n",
        "        return\n",
    ),
    "D3_throttled_request_recorded": (
        RA,
        "        if not 400 <= status_code < 500 or status_code == 429:\n",
        "        if not 400 <= status_code < 500:\n",
    ),
    "D4_signed_in_requester_recorded_as_a_door_refusal": (
        RA,
        "        if user is not None and user.is_authenticated:\n            return\n",
        "",
    ),
    "D5_refusal_recorded_even_when_the_door_recorded_its_own": (
        MW,
        "            if not a_stored_event_survives(state):\n",
        "            if True:\n",
    ),
    "E1_stripe_webhook_exclusion_removed": (
        RA,
        '    "stripe-webhook": (\n',
        '    "stripe-webhook-REMOVED": (\n',
    ),
    "A1_account_register_not_emitted": (
        VIEWS,
        "        emit(\n            AuditAction.ACCOUNT_REGISTER,\n",
        "        (lambda *a, **k: None)(\n            AuditAction.ACCOUNT_REGISTER,\n",
    ),
    "A2_account_register_names_the_new_account_as_actor": (
        VIEWS,
        "            AuditAction.ACCOUNT_REGISTER,\n            actor=request.user,\n",
        "            AuditAction.ACCOUNT_REGISTER,\n            actor=user,\n",
    ),
}

TESTS = [
    "audit.tests_route_coverage",
    "users.tests_auth_audit_doors",
    "audit.tests_state_change",
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

with open("docs/evidence/epic-a-s2/mutation_results.json", "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
