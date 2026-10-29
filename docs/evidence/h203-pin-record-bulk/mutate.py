"""
H-203 follow-up (the pin was blind to the audit history helper): mutation runner, 5 mutants.

Written BEFORE any run: every mutant names the tests it must fail (EXPECTED, full dotted names). KILLED needs a non-zero
inner run with its own "Ran" line, every module loaded and every expected test among the failures; other outcomes are
reported as what they are (SURVIVED, KILLED_NOT_AS_EXPECTED, BROKEN).
Rule 17: PYTHONDONTWRITEBYTECODE=1 and __pycache__ deleted before each mutant and after each restore.
Rule 18: each inner run writes straight to a file; nothing is piped.

    python docs/evidence/h203-pin-record-bulk/mutate.py --check
"""

import ast
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

TESTS = ["users.tests_roads_that_sign_in_or_activate"]
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
HERE = pathlib.Path("docs/evidence/h203-pin-record-bulk")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))
PYCACHE_ROOTS = ["users", "AutoGrader"]

PIN = "users/tests_roads_that_sign_in_or_activate.py"
ADMIN = "users/admin.py"

C = "users.tests_roads_that_sign_in_or_activate."
PIN_TEST = (
    C
    + "RoadsThatSignInOrActivateTests.test_every_site_is_on_the_named_list_with_its_count"
)
R = C + "PatternReachTests."
T_HELPER = R + "test_active_true_is_seen_through_the_history_helper"
T_LINES = R + "test_active_true_is_seen_across_lines_and_nested_calls"
T_FIRST = R + "test_active_true_is_seen_when_it_is_not_the_first_keyword"
T_VERIFIED = R + "test_email_verified_is_seen_through_the_history_helper_and_setattr"

ACTIVATE = "        updated = history.record_bulk(queryset, is_active=True)\n"

MUTANTS = {
    # (1) a SECOND record_bulk(..., is_active=True) site: the pin finds 2, pinned 1
    "P1_a_second_record_bulk_is_active_true_site": (
        ADMIN,
        ACTIVATE,
        ACTIVATE + "        history.record_bulk(queryset, is_active=True)\n",
        [PIN_TEST],
    ),
    # (2) one helper-form write of email_verified_at in users/admin.py (not pinned there)
    "P2_a_record_bulk_email_verified_write": (
        ADMIN,
        ACTIVATE,
        ACTIVATE
        + "        history.record_bulk(queryset, email_verified_at=timezone.now())\n",
        [PIN_TEST],
    ),
    # (3) the scanner forgets the history helper again: admin.py is found 0, the helper tests fail
    "P3_the_scanner_forgets_record_bulk": (
        PIN,
        r'_WRITE_CALL = r"\b(?:update|record_bulk)\(',
        r'_WRITE_CALL = r"\b(?:update)\(',
        [PIN_TEST, T_HELPER, T_LINES],
    ),
    # (4) the scanner forgets the setattr form of email_verified_at
    "P4_the_scanner_forgets_setattr_email_verified": (
        PIN,
        r"""|\bsetattr\([^,()]+,\s*["']email_verified_at["']""",
        r"""|\bsetattr\([^,()]+,\s*["']email_verified_at_x["']""",
        [T_VERIFIED],
    ),
    # (5) the call's arguments are no longer scanned: only a keyword right after the paren is seen
    "P5_only_the_first_keyword_is_seen": (
        PIN,
        r'\([^()]*\))*\))*?"' + "\nKINDS",
        r'\([^()]*\))*\)){0}"' + "\nKINDS",
        [PIN_TEST, T_HELPER, T_LINES, T_FIRST],
    ),
}

COVERED = {test for mutant in MUTANTS.values() for test in mutant[3]}
assert {
    T_HELPER,
    T_LINES,
    T_FIRST,
    T_VERIFIED,
} <= COVERED, "a new test is killed by no mutant"
# NAMED LIMIT: the setattr/is_active test and the "not a site" tests have no mutant of their own here.


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
