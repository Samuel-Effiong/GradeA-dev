"""Apply each mutant, run the AUTHZ-L2 suite, record which tests kill it, restore."""

import json
import re
import subprocess
import sys

MODELS = "users/models.py"
VIEWS = "users/views.py"
MUTANTS = {
    "M1_generate_code_wipes_lock_again": (
        MODELS,
        "            if row.is_locked():\n                self.attempts = row.attempts",
        "            if False:\n                self.attempts = row.attempts",
    ),
    "M2_generate_code_always_resets_attempts": (
        MODELS,
        "if row.locked_until is not None or not row.is_valid():",
        "if True:",
    ),
    "M3_register_failure_read_modify_write": (
        MODELS,
        'type(self).objects.filter(pk=self.pk).update(attempts=F("attempts") + 1)\n'
        '        self.refresh_from_db(fields=["attempts", "locked_until"])',
        'self.attempts += 1\n        self.save(update_fields=["attempts"])',
    ),
    "M4_created_at_not_refreshed_on_resend": (
        MODELS,
        "            row.created_at = timezone.now()\n",
        "",
    ),
    "M5_never_locks": (
        MODELS,
        "if self.attempts >= self.MAX_ATTEMPTS and not self.is_locked():",
        "if False:",
    ),
    "M6_view_emails_even_when_locked": (
        VIEWS,
        "            if otp_code is None:",
        "            if False:",
    ),
    "M8_no_lock_warning": (
        MODELS,
        '                "password_reset_otp_locked",',
        '                "password_reset_otp_lockedX",',
    ),
    "M7_expired_lock_never_refills": (
        MODELS,
        "if row.locked_until is not None or not row.is_valid():",
        "if not row.is_valid():",
    ),
}
sources = {p: open(p).read() for p in {m[0] for m in MUTANTS.values()}}
results = {}
try:
    for name, (path, a, b) in MUTANTS.items():
        assert a in sources[path], f"{name}: anchor not found"
        open(path, "w").write(sources[path].replace(a, b, 1))
        p = subprocess.run(
            [
                sys.executable,
                "manage.py",
                "test",
                "users.tests_reset_otp_budget",
                "users.tests_throttling",
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
        open(path, "w").write(sources[path])
finally:
    for path, src in sources.items():
        open(path, "w").write(src)
with open("docs/evidence/authz-l2-otp-counter/mutation_results.json", "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
