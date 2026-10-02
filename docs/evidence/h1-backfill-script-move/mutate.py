"""H1: apply each mutant, run the guard's test module, record the killers,
restore. The mutants add files (the fix is a move), so each one is a set of
files to create; none may exist beforehand, and each is deleted afterwards.

Rule 17: the test subprocess runs with PYTHONDONTWRITEBYTECODE=1, and the
__pycache__ of every touched directory is deleted before each mutant and
after each restore.
"""

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

SCRIPT = "scripts/one_off_backfill_stripe_schedules.py"
TESTS = ["AutoGrader.tests_management_commands_are_commands"]
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = os.environ.get(
    "MUT_RESULTS", "docs/evidence/h1-backfill-script-move/mutation_results.json"
)
# Harmless stand-in for a module-level script: no Command class, no writes.
NOT_A_COMMAND = '"""A script, not a command."""\n\nRAN = True\n'

MUTANTS = {
    # The pre-fix layout, with a harmless body: the live script is never
    # copied back into a place Django can import it from.
    "B1_backfill_is_back_in_commands": {
        "billing/management/commands/backfill.py": NOT_A_COMMAND,
    },
    "B2_another_script_in_a_commands_dir": {
        "ai_processor/management/commands/one_off_fixup.py": NOT_A_COMMAND,
    },
    "B3_scripts_becomes_a_package": {
        "scripts/__init__.py": "",
    },
}


def clear_pycache(path):
    shutil.rmtree(pathlib.Path(path).parent / "__pycache__", ignore_errors=True)


results = {}
env = {**os.environ, "EXEMPT_EMAIL_DOMAINS": "", "PYTHONDONTWRITEBYTECODE": "1"}
created: list = []
try:
    for name, files in MUTANTS.items():
        for path, body in files.items():
            assert not os.path.exists(path), f"{name}: {path} already exists"
            clear_pycache(path)
            created.append(path)
            with open(path, "w") as f:
                f.write(body)
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
        for path in files:
            os.remove(path)
            clear_pycache(path)
        created.clear()
finally:
    for path in created:
        if os.path.exists(path):
            os.remove(path)
        clear_pycache(path)

with open(OUT, "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
