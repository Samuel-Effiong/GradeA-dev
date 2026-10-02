"""command_actor (H-69): apply each mutant, run the test module, record the
killers, restore. Every anchor must occur exactly once and every mutant must
parse.

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

CONTEXT = "audit/context.py"
EMITTER = "audit/emitter.py"
TESTS = ["audit.tests_command_actor"]
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = os.environ.get(
    "MUT_RESULTS", "docs/evidence/epic-a-command-actor/mutation_results.json"
)

MUTANTS = {
    "C3_emit_ignores_the_command_actor": (
        EMITTER,
        "        actor = current_command_actor()\n",
        "        actor = None\n",
    ),
    "C4_emit_overrides_an_explicit_actor": (
        EMITTER,
        "    if actor is None:\n        # H-69:",
        "    if True:\n        # H-69:",
    ),
    "C5_no_command_key_in_metadata": (
        EMITTER,
        '        clean_metadata = {**clean_metadata, "command": command}\n',
        "        pass\n",
    ),
    "C6_a_call_site_can_supply_command": (
        EMITTER,
        "    clean_metadata, dropped = sanitise_metadata_for_action(action, metadata)\n",
        "    clean_metadata, dropped = sanitise_metadata_for_action(action, metadata)\n"
        '    if isinstance(metadata, dict) and "command" in metadata:\n'
        '        clean_metadata = {**clean_metadata, "command": metadata["command"]}\n',
    ),
    "C7_any_user_may_be_named": (
        CONTEXT,
        "    if not _is_active_super_admin(user):\n",
        "    if user is None:\n",
    ),
    "C8_an_inactive_super_admin_may_be_named": (
        CONTEXT,
        '        and getattr(user, "is_active", False)\n',
        "",
    ),
    "C9_the_superuser_flag_is_not_needed": (
        CONTEXT,
        '        and getattr(user, "is_superuser", False)\n',
        "",
    ),
    "C10_the_super_admin_type_is_not_needed": (
        CONTEXT,
        '        and getattr(user, "user_type", None) == UserTypes.SUPER_ADMIN\n',
        "",
    ),
    "C11_an_unsaved_user_may_be_named": (
        CONTEXT,
        '        and not getattr(getattr(user, "_state", None), "adding", True)\n',
        "",
    ),
    "C12_any_command_name_is_accepted": (
        CONTEXT,
        "    if not _is_known_command(command):\n",
        "    if False:\n",
    ),
    "C13_a_command_need_not_exist": (
        CONTEXT,
        "        and command in get_commands()\n",
        "",
    ),
    "C14_the_name_shape_is_not_checked": (
        CONTEXT,
        "        and _COMMAND_NAME.fullmatch(command) is not None\n",
        "",
    ),
    "C15_the_context_is_not_reset": (
        CONTEXT,
        "    finally:\n        _command_var.reset(token)\n",
        "    finally:\n        pass\n",
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
