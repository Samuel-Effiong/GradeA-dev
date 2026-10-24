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

BH = "AutoGrader/beat_health.py"

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
    # v2 F1 on the real module: a GETEX on a `with`-bound pipeline, a lock.
    "P3_beat_locks_gains_a_pipeline_getex": (
        BL,
        "        value = _redis().get(self.key)\n",
        "        value = _redis().get(self.key)\n"
        "        with _redis().pipeline() as p:\n"
        "            p.getex(self.key, ex=1)\n"
        "            p.execute()\n",
    ),
    "P4_beat_locks_gains_a_lock": (
        BL,
        "        value = _redis().get(self.key)\n",
        "        value = _redis().get(self.key)\n"
        '        _redis().lock("k2").acquire()\n',
    ),
    # v2 F2 on a real module: beat_locks's factory through the module.
    "P5_beat_health_uses_beat_locks_redis_via_the_module": (
        BH,
        "def check_beat_health():\n",
        "def _vf2_f2():\n"
        "    from AutoGrader import beat_locks\n\n"
        '    beat_locks._redis().set("k", 1)\n\n\n'
        "def check_beat_health():\n",
    ),
    # S: the scanner loses one of its earlier rules.
    "S1_yield_factories_missed": (
        G,
        "        if isinstance(node, (ast.Return, ast.Yield)) and node.value is not None:\n",
        "        if isinstance(node, ast.Return) and node.value is not None:\n",
    ),
    "S2_for_targets_missed": (
        G,
        "        if isinstance(node, (ast.For, ast.AsyncFor)) and self.is_client(\n",
        "        if False and self.is_client(\n",
    ),
    "S3_positional_parameters_missed": (
        G,
        "        bound = list(zip(params, call.args, strict=False))\n",
        "        bound = list(zip(params, [], strict=False))\n",
    ),
    "S4_cache_client_wrapper_missed": (
        G,
        '                and expr.attr in ("client", "_cache")\n',
        '                and expr.attr in ("_cache",)\n',
    ),
    "S5_imported_factories_missed": (
        G,
        "                if alias.name in factories_by_module.get(node.module, ()):\n",
        "                if False:\n",
    ),
    # F1 (v2): rule 1, rule 2 and each binding form, one at a time.
    "F1a_write_names_only_on_followed_clients": (
        G,
        "            by_name = bool(acquisitions) and method in RAW_WRITE_METHODS\n",
        "            by_name = False\n",
    ),
    "F1b_no_read_allow_list": (
        G,
        "            on_client = method not in (\n",
        "            on_client = method in RAW_WRITE_METHODS and method not in (\n",
    ),
    "F1c_with_as_missed": (
        G,
        "        if isinstance(node, (ast.With, ast.AsyncWith)):\n",
        "        if False:\n",
    ),
    "F1d_annotated_assign_missed": (
        G,
        "        if isinstance(node, ast.AnnAssign) and node.value is not None:\n",
        "        if False:\n",
    ),
    "F1e_walrus_missed": (
        G,
        "        if isinstance(node, ast.NamedExpr) and self.is_client(node.value, scope):\n",
        "        if False:\n",
    ),
    "F1f_factory_returning_a_bound_name_missed": (
        G,
        "                if self._holds_client(node.value, scope) or any(\n",
        "                if any(\n",
    ),
    "F1g_self_attribute_missed": (
        G,
        '                and node.value.id == "self"\n',
        '                and node.value.id == "__not_self__"\n',
    ),
    "F1h_keyword_argument_missed": (
        G,
        "            for kw in call.keywords\n",
        "            for kw in []\n",
    ),
    "F1i_closure_missed": (
        G,
        "            return any((s, expr.id) in self.names for s in self._scopes(scope))\n",
        "            return (scope, expr.id) in self.names or (None, expr.id) in self.names\n",
    ),
    # F2 (v2): a factory reached through its module.
    "F2a_module_attribute_factory_missed": (
        G,
        "            return call.func.attr in self.module_factories.get(owner, ())\n",
        "            return False\n",
    ),
    "F2b_from_package_import_module_missed": (
        G,
        "                if factories_by_module.get(submodule):\n",
        "                if False:\n",
    ),
    "F2c_import_module_missed": (
        G,
        "                if factories_by_module.get(alias.name):\n",
        "                if False:\n",
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
