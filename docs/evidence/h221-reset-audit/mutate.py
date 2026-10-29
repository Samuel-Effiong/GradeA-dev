# flake8: noqa: E501,B950
"""H-221 reset refusals audited: deliberate breaks, and the tests that must catch each.

Each mutant is applied, its test modules run, the failing tests are recorded,
the file is restored. Judged (rule 18): SURVIVED = the inner run exits 0;
KILLED = non-zero exit, the run's own "Ran" line, no module that failed to
load, EVERY test named in EXPECTED among the failing tests, and (rule 22)
every FAIL/ERROR block of each expected test holding the mutant's fragment
written in REASON. Any other failing test is recorded as
also_failed_not_expected, never hidden; BROKEN = anything else. EXPECTED and
REASON were written before any run, each traced against every assertion of
the two modules (controls too).

Rule 17: PYTHONDONTWRITEBYTECODE=1 on every inner run; the mutated file's
__pycache__ is deleted before each mutant and after each restore.
Rule 18: each inner run writes straight to its own file, stdin from the null
device, nothing piped. Each mutant is restored in a `finally` block and
SIGTERM is turned into an ordinary exit; the gate script's EXIT trap restores
the files from the commit.
"""

import ast
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys

HERE = pathlib.Path("docs/evidence/h221-reset-audit")
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))

VW = "users/views.py"
TESTS = [
    "users.tests_h221_reset_audit",
    "users.tests_auth_audit_doors",
]
K = "keepdb"

# The H-164 call, with the two lines before it (the same call text is also the
# wrong-code branch's, further down).
A_HEAD = "            not user.is_active or _holds_admin_power(user)\n        ):\n"
A_CALL = '            sign_in_failed(request, user, "password_reset", "INVALID_CODE")\n'
B_ARGS = '                request, user, "password_reset", "ACCOUNT_DEACTIVATED", denied=True\n'
B_CALL = "            sign_in_failed(\n" + B_ARGS + "            )\n"
B_SPEND = "                otp_obj.register_failure()\n"

# id, what is broken, file, text replaced, new text, tests, database kind
MUTANTS = [
    ("M1", "the H-164 call removed", VW, A_HEAD + A_CALL, A_HEAD, TESTS, K),
    ("M2", "the H-202 call removed", VW, B_SPEND + B_CALL, B_SPEND, TESTS, K),
    (
        "M3",
        "the H-164 reason changed to ACCOUNT_DEACTIVATED",
        VW,
        A_HEAD + A_CALL,
        A_HEAD + A_CALL.replace('"INVALID_CODE"', '"ACCOUNT_DEACTIVATED"'),
        TESTS,
        K,
    ),
    (
        "M4",
        "the H-202 event no longer DENIED",
        VW,
        B_ARGS,
        B_ARGS.replace("denied=True", "denied=False"),
        TESTS,
        K,
    ),
    (
        "M5",
        "the H-202 reason changed to INVALID_CODE",
        VW,
        B_ARGS,
        B_ARGS.replace('"ACCOUNT_DEACTIVATED"', '"INVALID_CODE"'),
        TESTS,
        K,
    ),
    (
        "M6",
        "the H-202 event names no account",
        VW,
        B_ARGS,
        B_ARGS.replace("request, user,", "request, None,"),
        TESTS,
        K,
    ),
    (
        "M7",
        "the H-164 event names no account",
        VW,
        A_HEAD + A_CALL,
        A_HEAD + A_CALL.replace("request, user,", "request, None,"),
        TESTS,
        K,
    ),
    (
        "M8",
        "the H-202 call made only when the reset is not locked",
        VW,
        B_SPEND + B_CALL,
        B_SPEND + "".join("    " + line + "\n" for line in B_CALL.splitlines()),
        TESTS,
        K,
    ),
    (
        "M9",
        "the H-164 branch also spends an attempt",
        VW,
        A_HEAD + A_CALL,
        A_HEAD + "            otp_obj.register_failure()\n" + A_CALL,
        TESTS,
        K,
    ),
]

# tests_h221_reset_audit
_T1 = "test_a_never_verified_switched_off_account_is_recorded_as_a_wrong_code"
_T2 = "test_a_never_verified_account_with_admin_power_is_recorded_as_a_wrong_code"
_T3 = "test_a_switched_off_account_is_recorded_as_refused_for_deactivation"
_T4 = "test_a_switched_off_account_with_a_locked_reset_is_recorded_the_same_way"
_T6 = "test_the_account_and_its_code_are_left_as_before"

#: Written before any run: every test reckoned to fail under each mutant,
#: traced against every assertion of both modules. No test of
#: tests_auth_audit_doors reaches either branch (its accounts are verified
#: and active).
EXPECTED = {
    "M1": [_T1, _T2],
    "M2": [_T3, _T4],
    "M3": [_T1, _T2],
    "M4": [_T3, _T4],
    "M5": [_T3, _T4],
    "M6": [_T3, _T4],
    "M7": [_T1, _T2],
    "M8": [_T4],
    "M9": [_T6],
}

#: Rule 22: the fragment each expected test's every block must hold.
#: M1, M2, M8: the middleware's fallback event (FAILURE, INVALID_REQUEST).
#: M6, M7: the target is None against the account's UUID. M9: T6's two H-164
#: subtests see 1 attempt where 0 were spent.
REASON = {
    "M1": "'INVALID_REQUEST' != 'INVALID_CODE'",
    "M2": "'FAILURE' != 'DENIED'",
    "M3": "'ACCOUNT_DEACTIVATED' != 'INVALID_CODE'",
    "M4": "'FAILURE' != 'DENIED'",
    "M5": "'INVALID_CODE' != 'ACCOUNT_DEACTIVATED'",
    "M6": "None != UUID('",
    "M7": "None != UUID('",
    "M8": "'FAILURE' != 'DENIED'",
    "M9": "1 != 0",
}

#: A test module that could not be loaded.
LOAD_FAILURE = "unittest.loader._FailedTest"


def clear_pycache(path):
    shutil.rmtree(pathlib.Path(path).parent / "__pycache__", ignore_errors=True)


def blocks_of(text, name):
    blocks = re.split(r"^={70}$", text, flags=re.M)
    return [
        b
        for b in blocks
        if re.match(r"\s*(FAIL|ERROR): " + re.escape(name) + r"[ (]", b)
    ]


def wrong_reasons(mid, text):
    """Expected tests with a block that does not hold REASON[mid]."""
    return sorted(
        name
        for name in EXPECTED[mid]
        if not all(REASON[mid] in b for b in blocks_of(text, name))
    )


def judge(mid, returncode, text):
    """(status, the run's "Ran" line, the failing tests' names, wrong reasons)."""
    failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", text, re.M)))
    ran = re.findall(r"^Ran \d+ tests? in .*$", text, re.M)
    ran_line = ran[-1] if ran else None
    wrong = wrong_reasons(mid, text)
    if returncode == 0:
        status = "SURVIVED"
    elif (
        ran_line
        and LOAD_FAILURE not in text
        and set(EXPECTED[mid]) <= set(failed)
        and not wrong
    ):
        status = "KILLED"
    else:
        status = "BROKEN"
    return status, ran_line, failed, wrong


def broken(source, old, new):
    return source.replace(old, new, 1)


def _exit_on_sigterm(signum, _frame):
    raise SystemExit(128 + signum)


def main():
    signal.signal(signal.SIGTERM, _exit_on_sigterm)
    assert (
        [m[0] for m in MUTANTS] == list(EXPECTED) == list(REASON)
    ), "every mutant named"
    originals = {}
    for mid, _what, path, old, new, _tests, kind in MUTANTS:
        source = originals.setdefault(path, open(path).read())
        found = source.count(old)
        assert found == 1, f"{mid}: anchor found {found} times"
        assert kind in ("keepdb", "fresh"), mid
        assert broken(source, old, new) != source, f"{mid}: changes nothing"
        ast.parse(broken(source, old, new))
    if "--check" in sys.argv:
        print(len(MUTANTS), "mutants: every anchor found once, all parse")
        print(" ".join(m[0] for m in MUTANTS))
        return
    LOGS.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    results = {}
    for mid, what, path, old, new, tests, kind in MUTANTS:
        original = originals[path]
        database = ["--keepdb"] if kind == "keepdb" else []
        try:
            clear_pycache(path)
            open(path, "w").write(broken(original, old, new))
            log = LOGS / f"{mid}.txt"
            with open(log, "w") as out, open(os.devnull) as devnull:
                p = subprocess.run(
                    [sys.executable, "manage.py", "test", *tests]
                    + [f"--settings={SETTINGS}", "--noinput", "--verbosity", "2"]
                    + database,
                    stdin=devnull,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    env=env,
                    timeout=600,
                )
        finally:
            open(path, "w").write(original)
            clear_pycache(path)
        text = log.read_text(errors="replace")
        status, ran_line, failed, wrong = judge(mid, p.returncode, text)
        results[mid] = {
            "what": what,
            "file": path,
            "kind": kind,
            "status": status,
            "exit": p.returncode,
            "ran_line": ran_line,
            "expected": EXPECTED[mid],
            "reason": REASON[mid],
            "failing_tests": failed,
            "wrong_reason": wrong,
            "also_failed_not_expected": sorted(set(failed) - set(EXPECTED[mid])),
        }
        print(mid, results[mid], flush=True)
    with open(OUT, "w") as f:
        json.dump(results, f, indent=2)
        f.write("\n")
    for status in ("KILLED", "SURVIVED", "BROKEN"):
        print(f"{status}:", [k for k, v in results.items() if v["status"] == status])


if __name__ == "__main__":
    main()
