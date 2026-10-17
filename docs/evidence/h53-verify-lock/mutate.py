"""H-53: apply each mutant, run the budget tests, record killers, restore.
Every anchor must occur exactly once in its file: replace(..., 1) on a
non-unique anchor silently mutates the wrong site.
"""

import json
import os
import re
import subprocess
import sys

VIEWS = "users/views.py"
THR = "users/throttling.py"

MUTANTS = {
    "M1_lock_ignored": (
        VIEWS,
        "        if lock_until or verify_attempt_over_budget(attempt):\n",
        "        if verify_attempt_over_budget(attempt):\n",
    ),
    "M2_counted_after_the_answer_not_before": (
        VIEWS,
        "        if lock_until or verify_attempt_over_budget(attempt):\n",
        "        if lock_until:\n",
    ),
    "M3_no_budget": (
        VIEWS,
        "        attempt = None if lock_until else reserve_verify_attempt(email)\n",
        "        attempt = None\n",
    ),
    "M4_unknown_address_not_budgeted": (
        VIEWS,
        "        attempt = None if lock_until else reserve_verify_attempt(email)\n",
        "        attempt = (\n"
        "            None\n"
        "            if lock_until\n"
        "            or not CustomUser.objects.filter(email__iexact=email).exists()\n"
        "            else reserve_verify_attempt(email)\n"
        "        )\n",
    ),
    "M5_lock_clears_the_stored_code": (
        VIEWS,
        "                lock_verify_address(email)\n",
        "                lock_verify_address(email)\n"
        "                CustomUser.objects.filter(email__iexact=email).update(\n"
        "                    activation_token=None, activation_expires=None\n"
        "                )\n",
    ),
    "M6_lock_not_set": (
        VIEWS,
        "                lock_verify_address(email)\n",
        "                pass\n",
    ),
    "M7_expired_code_spends_nothing": (
        VIEWS,
        '            refuse("Activation link has expired.")\n',
        '            raise ParseError("Activation link has expired.")\n',
    ),
    "M8_success_does_not_refund": (
        VIEWS,
        "        clear_verify_failures(email)\n",
        "",
    ),
    "M9_resend_while_locked": (
        VIEWS,
        "            if not verify_lock_until(user.email):\n",
        "            if True:\n",
    ),
    "M10_spent_one_attempt_late": (
        THR,
        "    return attempt is not None and attempt >= settings.VERIFY_EMAIL_MAX_FAILURES\n",
        "    return attempt is not None and attempt > settings.VERIFY_EMAIL_MAX_FAILURES\n",
    ),
    "M11_refused_one_attempt_early": (
        THR,
        "    return attempt is not None and attempt > settings.VERIFY_EMAIL_MAX_FAILURES\n",
        "    return attempt is not None and attempt >= settings.VERIFY_EMAIL_MAX_FAILURES\n",
    ),
    "M12_address_case_sensitive": (
        THR,
        '    digest = hashlib.sha256((email or "").strip().lower().encode()).hexdigest()\n',
        '    digest = hashlib.sha256((email or "").strip().encode()).hexdigest()\n',
    ),
    "M13_one_budget_for_every_address": (
        THR,
        '    return f"verify_email:{kind}:{digest[:32]}"\n',
        '    return f"verify_email:{kind}"\n',
    ),
    "M14_reserve_fails_closed": (
        THR,
        '        logger.error("verify_email budget unavailable (cache write failed)")\n'
        "        return None\n",
        '        logger.error("verify_email budget unavailable (cache write failed)")\n'
        "        raise\n",
    ),
    "M15_lock_read_fails_closed": (
        THR,
        '        logger.error("verify_email budget unavailable (cache read failed)")\n'
        "        return None\n",
        '        logger.error("verify_email budget unavailable (cache read failed)")\n'
        "        raise\n",
    ),
    "M16_lock_never_ends": (
        THR,
        "    if until and until > time.time():\n",
        "    if until:\n",
    ),
    # 1a's N2 / N3 (VERIFICATION.md): the window lengths.
    "M17_attempt_window_60s": (
        THR,
        "        cache.add(key, 0, timeout=window)\n",
        "        cache.add(key, 0, timeout=60)\n",
    ),
    "M18_lock_entry_60s": (
        THR,
        '            _verify_address_key("locked", email), time.time() + window, timeout=window\n',
        '            _verify_address_key("locked", email), time.time() + window, timeout=60\n',
    ),
}

TESTS = ["users.tests_verify_email_budget"]

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

with open("docs/evidence/h53-verify-lock/mutation_results.json", "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
