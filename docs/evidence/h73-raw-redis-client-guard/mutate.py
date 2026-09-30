"""H-73: apply each mutant, run its tests, record killers, restore.
Every anchor must occur exactly once in its file: replace(..., 1) on a
non-unique anchor silently mutates the wrong site.
"""

import ast
import json
import os
import re
import subprocess
import sys

G = "AutoGrader/tests_cache_invalidation_coverage.py"
BL = "AutoGrader/beat_locks.py"
HE = "AutoGrader/health.py"
# 0b (2026-09-30): mutation runs on its own test DB, never the regression's.
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree")

MUTANTS = {
    # P: production code gains a raw-client write the guard must see.
    "P1_beat_locks_gains_a_raw_write": (
        BL,
        "        value = _redis().get(self.key)\n",
        "        value = _redis().getset(self.key, self.token)\n",
    ),
    "P2_health_probe_moves_to_the_raw_client": (
        HE,
        '    cache.set("healthcheck", "ok", 10)\n',
        '    cache.client.set("healthcheck", "ok", 10)\n',
    ),
    # S: the scanner loses one of its rules.
    "S1_yield_factories_missed": (
        G,
        "            isinstance(r, (ast.Return, ast.Yield))\n",
        "            isinstance(r, ast.Return)\n",
    ),
    "S2_for_targets_missed": (
        G,
        "            elif isinstance(node, (ast.For, ast.AsyncFor)) and is_client(\n",
        "            elif False and is_client(\n",
    ),
    "S3_parameters_missed": (
        G,
        "                for arg, param in zip(node.args, params, strict=False):\n",
        "                for arg, param in zip([], params, strict=False):\n",
    ),
    "S4_cache_client_wrapper_missed": (
        G,
        '                and expr.attr in ("client", "_cache")\n',
        '                and expr.attr in ("_cache",)\n',
    ),
    "S5_imported_factories_missed": (
        G,
        "            if alias.name in factories_by_module.get(node.module, ())\n",
        "            if False\n",
    ),
}

TESTS = [
    "AutoGrader.tests_cache_invalidation_coverage.RawRedisClientTests",
    "AutoGrader.tests_cache_invalidation_coverage.KeyMapIsTheCodeTests",
]
originals: dict = {}
results = {}
try:
    for name, (path, a, b) in MUTANTS.items():
        src = originals.setdefault(path, open(path).read())
        assert src.count(a) == 1, f"{name}: anchor found {src.count(a)} times"
        mutated = src.replace(a, b, 1)
        # A mutant that doesn't parse is "killed" by an import error, not by
        # a test (run 2's A1/A3). Refuse it instead.
        ast.parse(mutated)
        open(path, "w").write(mutated)
        p = subprocess.run(
            [sys.executable, "manage.py", "test", *TESTS]
            + [f"--settings={SETTINGS}", "--keepdb", "--noinput"],
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

OUT = os.environ.get("MUT_RESULTS", "mutation_results.json")
with open(f"docs/evidence/h73-raw-redis-client-guard/{OUT}", "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
