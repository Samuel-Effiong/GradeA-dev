"""Bundle 5 merge-down: one mutant per resolution choice that a test should
hold. Apply each, run the test modules, record the killers, restore. Every
anchor must occur the stated number of times and every mutant must parse.

Rule 17: the test subprocess runs with PYTHONDONTWRITEBYTECODE=1, and the
__pycache__ of every mutated module's directory is deleted before each
mutant and after each restore.
"""

import ast
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

SIGNALS = "users/signals.py"
AUDIT_TASKS = "audit/tasks.py"
BILLING_TASKS = "billing/tasks.py"
TESTS = [
    "billing.tests.test_logs_carry_no_email",
    "AutoGrader.tests_beat_locks",
    "audit.tests_sweep_beat_lock",
    "audit.tests_retention_sweep",
    "audit.tests_metrics",
]
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = os.environ.get(
    "MUT_RESULTS", "docs/evidence/epic-a-merge-down-b5/mutation_results.json"
)

SETTINGS_LINE = (
    '            "Failed to create Settings for user %s: %s",\n            user.id,\n'
)
WALLET_LINE = '            "Failed to create CreditWallet for user %s: %s",\n            user.id,\n'
LOCK = "@single_instance(max_hold=beat_locks.DAILY)\n"

# Each mutant names a file, an anchor in it and the text that replaces it.
MUTANTS = {
    "R1_settings_failure_logs_the_exception_text": (
        SIGNALS,
        SETTINGS_LINE + "            type(exc).__name__,\n",
        SETTINGS_LINE + "            str(exc),\n",
    ),
    "R2_wallet_failure_logs_the_exception_text": (
        SIGNALS,
        WALLET_LINE + "            type(exc).__name__,\n",
        WALLET_LINE + "            str(exc),\n",
    ),
    "R3_the_retention_sweep_has_no_beat_lock": (
        AUDIT_TASKS,
        LOCK + "def sweep_audit_retention(self):\n",
        "def sweep_audit_retention(self):\n",
    ),
    "R4_the_pii_sweep_has_no_beat_lock": (
        AUDIT_TASKS,
        LOCK + "def sweep_audit_pii_short_retention(self):\n",
        "def sweep_audit_pii_short_retention(self):\n",
    ),
    "R5_billing_tasks_loses_the_audit_metrics_import": (
        BILLING_TASKS,
        "from audit import metrics as audit_metrics\n",
        "",
    ),
    "R6_billing_tasks_loses_the_beat_lock_imports": (
        BILLING_TASKS,
        "from AutoGrader import beat_locks\n"
        "from AutoGrader.beat_locks import single_instance\n",
        "",
    ),
}


def clear_pycache(path):
    shutil.rmtree(pathlib.Path(path).parent / "__pycache__", ignore_errors=True)


originals: dict = {}
results = {}
env = {**os.environ, "EXEMPT_EMAIL_DOMAINS": "", "PYTHONDONTWRITEBYTECODE": "1"}
try:
    for name, (path, a, b) in MUTANTS.items():
        src = originals.setdefault(path, open(path).read())
        assert src.count(a) == 1, f"{name}: anchor found {src.count(a)} times"
        mutated = src.replace(a, b, 1)
        ast.parse(mutated)
        clear_pycache(path)
        open(path, "w").write(mutated)
        p = subprocess.run(
            [sys.executable, "manage.py", "test", *TESTS]
            + [f"--settings={SETTINGS}", "--keepdb", "--noinput"],
            capture_output=True,
            text=True,
            env=env,
        )
        out = p.stdout + p.stderr
        failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", out, re.M)))
        results[name] = {"killed": p.returncode != 0, "failing_tests": failed}
        print(name, results[name], flush=True)
        open(path, "w").write(src)
        clear_pycache(path)
finally:
    for path, src in originals.items():
        open(path, "w").write(src)
        clear_pycache(path)

with open(OUT, "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
