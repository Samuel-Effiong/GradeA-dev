"""H-194 (AUDIT-ANON-FLOOD) mutants. Each is one textual change to audit/emitter.py; the new test module and the
existing failed-auth cap tests run against it; the failing tests are recorded; the file is restored. The runner is
the null-school row's.

Written BEFORE any run: every mutant names the tests it must fail (EXPECTED, full dotted names). KILLED needs a non-zero
inner run with its own "Ran" line, every module loaded and every expected test among the failures; other outcomes are
reported as what they are (SURVIVED, KILLED_NOT_AS_EXPECTED, BROKEN).

Rule 17: PYTHONDONTWRITEBYTECODE=1 and __pycache__ deleted before each mutant and after each restore.
Rule 18: each inner run writes straight to a file; nothing is piped.

    python docs/evidence/audit-anon-flood-cap/mutate.py --check
"""

import ast
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

TESTS = [
    "audit.tests_anonymous_admin_denial_cap",
    "audit.tests_failed_auth_cap",
]
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
HERE = pathlib.Path("docs/evidence/audit-anon-flood-cap")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))
PYCACHE_ROOTS = ["audit", "users", "AutoGrader"]

EMITTER = "audit/emitter.py"
METADATA = "audit/metadata.py"

C = "audit.tests_anonymous_admin_denial_cap.AnonymousAdminDenialCapTests."
G0 = C + "test_the_route_exists_and_refuses_an_anonymous_caller"
G1 = C + "test_anonymous_probes_stop_writing_rows_at_the_global_limit"
G2 = C + "test_the_first_suppressed_probe_leaves_one_summary_row"
G3 = C + "test_naming_a_different_target_each_time_does_not_get_round_the_cap"
G4 = C + "test_a_signed_in_refusal_is_never_capped"
OLD_SIGNED_IN = (
    "audit.tests_failed_auth_cap.FailedAuthCapTests."
    "test_a_signed_in_requesters_failures_are_never_capped"
)
# G0 is a guard no mutant isolates (it only proves the probe reaches the refusal).
NEW_TESTS = [G1, G2, G3, G4]

TAIL = (
    "        # summary row. A signed-in refusal never reaches this point.\n"
    "        return None, True\n"
)

MUTANTS = {
    "F1_the_denied_admin_action_is_not_capped": (
        EMITTER,
        TAIL,
        "        # summary row. A signed-in refusal never reaches this point.\n"
        "        return None\n",
        [G1, G2, G3],
    ),
    "F2_the_route_target_is_the_cap_key": (
        EMITTER,
        TAIL,
        "        # summary row. A signed-in refusal never reaches this point.\n"
        '        return fields.get("target_id"), True\n',
        [G3],
    ),
    "F3_the_global_cap_does_not_apply": (
        EMITTER,
        TAIL,
        "        # summary row. A signed-in refusal never reaches this point.\n"
        "        return None, False\n",
        [G1, G2, G3],
    ),
    "F4_a_signed_in_refusal_is_capped_too": (
        EMITTER,
        '    if fields.get("actor_role") != ActorRole.ANONYMOUS.value:\n'
        "        return None\n"
        '    outcome = fields.get("outcome")\n',
        '    outcome = fields.get("outcome")\n',
        [G4, OLD_SIGNED_IN],
    ),
    # Found by ed's hand trace on taking the row over: the summary's metadata
    # keys were dropped for ADMIN_ACTION (its allow-list was only "source").
    "F5_the_admin_action_summary_loses_its_metadata_keys": (
        METADATA,
        '    AuditAction.ADMIN_ACTION: frozenset({"source"} | _FAILED_AUTH_SUMMARY_KEYS),\n',
        '    AuditAction.ADMIN_ACTION: frozenset({"source"}),\n',
        [G2],
    ),
}

COVERED = {test for mutant in MUTANTS.values() for test in mutant[3]}
assert set(NEW_TESTS) <= COVERED, sorted(set(NEW_TESTS) - COVERED)


def clear_pycache():
    for root in PYCACHE_ROOTS:
        for cache in pathlib.Path(root).rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)


def remaining_pycache():
    return sum(
        1 for root in PYCACHE_ROOTS for _ in pathlib.Path(root).rglob("__pycache__")
    )


def check():
    for name, (path, old, new, expected) in MUTANTS.items():
        source = pathlib.Path(path).read_text(encoding="utf-8")
        assert source.count(old) == 1, f"{name}: anchor found {source.count(old)} times"
        ast.parse(source.replace(old, new, 1))
        assert expected and len(set(expected)) == len(expected), name
    print(len(MUTANTS), "mutants: anchors unique, all parse, expected tests named")


def main():
    check()
    if "--check" in sys.argv:
        return
    only = [name for name in os.environ.get("MUT_ONLY", "").split(",") if name]
    unknown = sorted(set(only) - set(MUTANTS))
    assert not unknown, f"MUT_ONLY names no such mutant: {unknown}"
    selected = {name: MUTANTS[name] for name in (only or MUTANTS)}
    print(len(selected), "of", len(MUTANTS), "mutants selected", flush=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    results = {}
    for name, (path, old, new, expected) in selected.items():
        target = pathlib.Path(path)
        original = target.read_text(encoding="utf-8")
        exit_status: int | str
        try:
            clear_pycache()
            target.write_text(original.replace(old, new, 1), encoding="utf-8")
            log = LOGS / f"{name}.txt"
            with open(log, "w") as out, open(os.devnull) as devnull:
                p = subprocess.run(
                    [sys.executable, "manage.py", "test", *TESTS]
                    + [f"--settings={SETTINGS}", "--keepdb", "--noinput"],
                    stdin=devnull,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    env=env,
                    timeout=900,
                )
            exit_status = p.returncode
        except subprocess.TimeoutExpired:
            exit_status = "timeout"
        finally:
            target.write_text(original, encoding="utf-8")
            clear_pycache()
        text = log.read_text(errors="replace")
        failed = sorted(
            set(re.findall(r"^(?:FAIL|ERROR): \w+ \(([\w.]+)\)", text, re.M))
        )
        ran = re.findall(r"^Ran (\d+) tests?", text, re.M)
        loaded = not re.search(
            r"unittest\.loader\._FailedTest"
            r"|^(?:ImportError|ModuleNotFoundError|SyntaxError)\b",
            text,
            re.M,
        )
        missing = sorted(set(expected) - set(failed))
        if exit_status == 0:
            status = "SURVIVED"
        elif exit_status != "timeout" and ran and failed and loaded and not missing:
            status = "KILLED"
        elif exit_status != "timeout" and ran and failed and loaded:
            status = "KILLED_NOT_AS_EXPECTED"
        else:
            status = "BROKEN"
        results[name] = {
            "status": status,
            "exit": exit_status,
            "ran": int(ran[-1]) if ran else None,
            "expected": expected,
            "expected_but_passed": missing,
            "failing_tests": failed,
            "pycache_left": remaining_pycache(),
        }
        print(
            name,
            status,
            "exit",
            exit_status,
            "ran",
            results[name]["ran"],
            "failed",
            len(failed),
            "expected-but-passed",
            missing,
            flush=True,
        )
    with open(OUT, "w") as handle:
        json.dump(results, handle, indent=2)
        handle.write("\n")
    for wanted in ("SURVIVED", "KILLED_NOT_AS_EXPECTED", "BROKEN"):
        print(wanted + ":", [k for k, v in results.items() if v["status"] == wanted])
    print(
        "KILLED:",
        sum(v["status"] == "KILLED" for v in results.values()),
        "of",
        len(results),
    )


if __name__ == "__main__":
    main()
