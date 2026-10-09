"""H-182 mutants. Each is one textual change to one production file; the new test
module and the two older modules whose charges were wrapped run against it; the failing
tests are recorded; the file is restored. The runner is H-181's.

Written BEFORE any run: every mutant names the tests it must fail (EXPECTED, full dotted
names). KILLED needs a non-zero inner run with its own "Ran" line, every module loaded
and every expected test among the failures; other outcomes are reported as what they
are (SURVIVED, KILLED_NOT_AS_EXPECTED, BROKEN). EVIDENCE.md says, for each mutant,
whether the failing set is exactly the written one.

Rule 17: PYTHONDONTWRITEBYTECODE=1 and __pycache__ deleted before each mutant and after
each restore. Rule 18: each inner run writes straight to a file; nothing is piped.

    python docs/evidence/h182-licence-rollup-after-commit/mutate.py --check
checks the anchors (each exactly once) and that every mutant parses, runs nothing.
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
    "billing.tests.test_licence_rollup_after_commit",
    "billing.tests.test_license_consumption_accounting",
    "billing.tests.test_license_multi_month_budget",
    "billing.tests.test_credit_refund",
]
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
HERE = pathlib.Path("docs/evidence/h182-licence-rollup-after-commit")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))
PYCACHE_ROOTS = ["billing", "users", "AutoGrader"]

MODELS = "billing/models.py"
SERVICES = "billing/services.py"
ROLLUP = "billing/licence_rollup.py"

P = "billing.tests.test_licence_rollup_after_commit."
A = P + "RollupAfterCommitTests."
R1 = A + "test_a_charge_rolls_up_what_it_charged_once_it_commits"
R2 = A + "test_the_figure_is_not_touched_inside_the_charge"
R3 = A + "test_a_charge_that_rolls_back_rolls_up_nothing"
R4 = A + "test_a_refund_takes_it_back_after_commit"
R5 = A + "test_a_refund_never_takes_the_figure_below_zero"
R6 = A + "test_a_failed_roll_up_is_logged_with_its_licence_and_amount_not_raised"
R7 = (
    P
    + "LicenceBeforeWalletRaceTests.test_a_charge_racing_a_licence_overage_grant_does_not_deadlock"
)
# R3 (a rolled-back charge rolls up nothing) is a guard that no mutant here isolates:
# the inline update rolled back with the charge too, so it is green on the old code and
# under N1 to N9. It is not counted as seen red (EVIDENCE.md says so).
NEW_TESTS = [R1, R2, R4, R5, R6, R7]

CHARGE_CALL = (
    "        roll_up_after_commit(allocation.license_subscription_id, amount)\n"
)
REFUND_CALL = "                    roll_up_after_commit(allocation.license_subscription_id, -amount)\n"
INLINE_CHARGE = (
    "        LicenseSubscription.objects.filter(\n"
    "            pk=allocation.license_subscription_id\n"
    "        ).update(\n"
    '            total_credits_consumed=models.F("total_credits_consumed") + amount,\n'
    "            updated_at=timezone.now(),\n"
    "        )\n"
)
INLINE_REFUND = (
    "                    from .models import LicenseSubscription\n"
    "\n"
    "                    LicenseSubscription.objects.filter(\n"
    "                        pk=allocation.license_subscription_id\n"
    "                    ).update(\n"
    "                        total_credits_consumed=Greatest(\n"
    '                            F("total_credits_consumed") - amount, Value(0)\n'
    "                        ),\n"
    "                        updated_at=timezone.now(),\n"
    "                    )\n"
)

MUTANTS = {
    "N1_the_charge_writes_the_licence_inline": (
        MODELS,
        CHARGE_CALL,
        INLINE_CHARGE,
        [R2, R6, R7],
    ),
    "N2_the_charge_registers_no_roll_up": (
        MODELS,
        CHARGE_CALL,
        "        pass\n",
        [R1, R2, R4, R6, R7],
    ),
    "N3_the_refund_registers_no_roll_up": (
        SERVICES,
        REFUND_CALL,
        "                    pass\n",
        [R4, R5],
    ),
    "N4_the_refund_writes_the_licence_inline": (
        SERVICES,
        REFUND_CALL,
        INLINE_REFUND,
        [R4],
    ),
    "N5_the_roll_up_runs_at_once_not_after_commit": (
        ROLLUP,
        "    transaction.on_commit(apply)\n",
        "    apply()\n",
        [R2, R4, R7],
    ),
    "N6_a_failed_roll_up_is_raised": (
        ROLLUP,
        "                type(exc).__name__,\n            )\n",
        "                type(exc).__name__,\n            )\n            raise\n",
        [R6],
    ),
    "N7_a_failed_roll_up_is_not_logged_as_an_error": (
        ROLLUP,
        "            logger.error(\n",
        "            logger.debug(\n",
        [R6],
    ),
    "N8_the_refund_is_not_clamped_at_zero": (
        ROLLUP,
        '                figure = Greatest(F("total_credits_consumed") + delta, Value(0))\n',
        '                figure = F("total_credits_consumed") + delta\n',
        [R5],
    ),
    "N10_the_log_line_carries_the_errors_text": (
        ROLLUP,
        "                type(exc).__name__,\n",
        "                str(exc),\n",
        [R6],
    ),
    "N11_the_charge_registers_its_roll_up_twice": (
        MODELS,
        CHARGE_CALL,
        CHARGE_CALL + CHARGE_CALL,
        [R1, R2, R4, R6, R7],
    ),
    "N12_the_refund_registers_its_roll_up_twice": (
        SERVICES,
        REFUND_CALL,
        REFUND_CALL + REFUND_CALL,
        [R4],
    ),
    "N9_the_roll_up_adds_nothing": (
        ROLLUP,
        '                figure = F("total_credits_consumed") + delta\n            else:\n',
        '                figure = F("total_credits_consumed") + 0\n            else:\n',
        [R1, R2, R4, R7],
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
