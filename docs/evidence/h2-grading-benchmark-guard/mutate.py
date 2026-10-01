"""H2: apply each mutant, run the guard's test module, record the killers,
restore. Every anchor must occur exactly once and every mutant must parse.

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

CMD = "ai_processor/management/commands/grading_benchmark.py"
TASKS = "ai_processor/tasks.py"
SETTINGS_PY = "AutoGrader/settings.py"
TESTS = ["ai_processor.tests_h2_grading_benchmark_guard"]
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = os.environ.get(
    "MUT_RESULTS", "docs/evidence/h2-grading-benchmark-guard/mutation_results.json"
)

MUTANTS = {
    "G1_no_guard_in_resolve_user": (
        CMD,
        "        ensure_benchmark_allowed(allow_non_debug)\n",
        "        pass\n",
    ),
    "G2_debug_does_not_allow": (
        CMD,
        "        settings.DEBUG\n        or allow_non_debug\n",
        "        allow_non_debug\n",
    ),
    "G3_the_flag_does_not_allow": (
        CMD,
        "        or allow_non_debug\n        or getattr",
        "        or getattr",
    ),
    "G4_the_setting_does_not_allow": (
        CMD,
        '        or getattr(settings, "ENABLE_GRADING_BENCHMARK", False)\n',
        "",
    ),
    "G5_handle_drops_the_flag": (
        CMD,
        '            allow_non_debug=options.get("allow_non_debug", False),\n',
        "            allow_non_debug=False,\n",
    ),
    "G6_nightly_does_not_skip_on_refusal": (
        TASKS,
        "    except BenchmarkRefused as exc:\n        return _refused(MODE_REPLAY, exc)\n",
        "",
    ),
    "G7_weekly_does_not_skip_on_refusal": (
        TASKS,
        "    try:\n"
        "        report, diff = _run(MODE_LIVE)\n"
        "    except BenchmarkRefused as exc:\n"
        "        return _refused(MODE_LIVE, exc)\n",
        "    report, diff = _run(MODE_LIVE)\n",
    ),
    "G8_the_switch_defaults_on": (
        SETTINGS_PY,
        'ENABLE_GRADING_BENCHMARK = env.bool("ENABLE_GRADING_BENCHMARK", default=False)\n',
        'ENABLE_GRADING_BENCHMARK = env.bool("ENABLE_GRADING_BENCHMARK", default=True)\n',
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
