"""Epic A S1b: apply each mutant, run the S1b tests, record killers, restore.
Every anchor must occur exactly once in its file: replace(..., 1) on a
non-unique anchor silently mutates the wrong site.
"""

import json
import os
import re
import subprocess
import sys

CAP = "audit/failed_auth_cap.py"
EM = "audit/emitter.py"
MW = "audit/middleware.py"

MUTANTS = {
    "C1_no_floor_for_known_accounts": (
        CAP,
        "            if target_count <= settings.FAILED_AUTH_TARGET_FLOOR:\n"
        "                return WRITE\n",
        "",
    ),
    "C2_no_per_target_cap": (
        CAP,
        "            if target_count > settings.FAILED_AUTH_TARGET_LIMIT:\n",
        "            if False:\n",
    ),
    "C3_no_global_cap": (
        CAP,
        "        if global_count > settings.FAILED_AUTH_GLOBAL_LIMIT:\n",
        "        if False:\n",
    ),
    "C4_fails_closed": (
        CAP,
        '            extra={"audit_kind": "failed_auth_cap_failed"},\n'
        "        )\n"
        "        return WRITE\n",
        '            extra={"audit_kind": "failed_auth_cap_failed"},\n'
        "        )\n"
        "        return Verdict(write=False)\n",
    ),
    "C5_no_summaries": (
        CAP,
        "    if not _is_threshold(suppressed):\n",
        "    if True:\n",
    ),
    "C6_a_summary_for_every_suppression": (
        CAP,
        "    if not _is_threshold(suppressed):\n",
        "    if False:\n",
    ),
    "C7_wrong_thresholds": (
        CAP,
        "    while n >= 10 and n % 10 == 0:\n",
        "    while n >= 2 and n % 2 == 0:\n",
    ),
    "C8_counter_not_atomic": (
        CAP,
        "        return cache.incr(key)\n",
        "        value = (cache.get(key) or 0) + 1\n"
        "        cache.set(key, value, timeout=timeout)\n"
        "        return value\n",
    ),
    "E1_successes_capped": (
        EM,
        "    if outcome == AuditOutcome.FAILURE.value:\n"
        '        return fields.get("target_id"), True\n',
        "    if True:\n" '        return fields.get("target_id"), True\n',
    ),
    "E2_signed_in_requesters_capped": (
        EM,
        '    if fields.get("actor_role") != ActorRole.ANONYMOUS.value:\n'
        "        return None\n",
        "",
    ),
    "E4_lock_denials_under_the_global_cap": (
        EM,
        '        return fields.get("target_id"), False\n',
        '        return fields.get("target_id"), True\n',
    ),
    "E5_lock_denials_uncapped": (
        EM,
        "    if outcome == AuditOutcome.DENIED.value:\n",
        "    if False:\n",
    ),
    "E6_crashes_uncapped": (
        EM,
        "        return None, True\n",
        "        return None\n",
    ),
    "E3_suppression_not_marked": (
        EM,
        "            record_suppressed_event()\n",
        "",
    ),
    "M1_door_fallback_ignores_suppression": (
        MW,
        "            if not a_stored_event_survives(state) and not state.suppressed:\n",
        "            if not a_stored_event_survives(state):\n",
    ),
}

TESTS = [
    "audit.tests_failed_auth_cap",
    "audit.tests_state_change",
    "users.tests_auth_audit_doors",
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

with open("docs/evidence/epic-a-s1b/mutation_results.json", "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
