"""H-178 (part A) mutants. Each is one textual change to one production file; the new test module and the two
older modules that read the webhook mirror and the page's cancellation fields run against it; the failing tests are
recorded; the file is restored. The runner is H-182's.

Written BEFORE any run: every mutant names the tests it must fail (EXPECTED, full dotted names). KILLED needs a non-zero
inner run with its own "Ran" line, every module loaded and every expected test among the failures; other outcomes are
reported as what they are (SURVIVED, KILLED_NOT_AS_EXPECTED, BROKEN). EVIDENCE.md says, for each mutant, whether the
failing set is exactly the written one and names any extra.

Rule 17: PYTHONDONTWRITEBYTECODE=1 and __pycache__ deleted before each mutant and after each restore.
Rule 18: each inner run writes straight to a file; nothing is piped.

    python docs/evidence/h178-date-scheduled-cancellation/mutate.py --check
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
    "billing.tests.test_date_scheduled_cancellation",
    "billing.tests.test_subscription_updated_webhook",
    "billing.tests.test_cancellation_visibility",
]
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
HERE = pathlib.Path("docs/evidence/h178-date-scheduled-cancellation")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))
PYCACHE_ROOTS = ["billing", "users", "AutoGrader"]

STRIPE = "billing/stripe_service.py"
VIEWS = "billing/views.py"

M = "billing.tests.test_date_scheduled_cancellation.DateScheduledCancellationMirrorTests."
K = "billing.tests.test_date_scheduled_cancellation.KeepAnswersForADateScheduledRecordTests."
T1 = M + "test_a_date_with_the_period_end_flag_off_stops_the_record_renewing"
T2 = M + "test_the_recorded_date_is_stripes_own_when_it_gives_one"
T3 = M + "test_a_repeat_of_the_same_message_writes_nothing"
T4 = M + "test_a_message_with_a_date_but_without_the_flag_is_scheduled"
T5 = M + "test_clearing_the_flag_does_not_undo_a_date_that_is_still_set"
T6 = M + "test_clearing_both_puts_the_record_back_to_renewing"
T7 = M + "test_a_message_that_says_nothing_about_it_changes_nothing"
T8 = M + "test_the_period_end_flag_alone_still_mirrors"
T9 = M + "test_a_trial_is_left_alone"
K1 = K + "test_the_answer_says_to_contact_support_and_not_that_it_is_active"
NEW_TESTS = [T1, T2, T3, T4, T5, T6, T7, T8, T9, K1]

COND = "            if cancel_at_period_end is not None or cancel_at:\n"
SCHED = "                    bool(cancel_at_period_end) or bool(cancel_at),\n"

MUTANTS = {
    "P1_the_handler_does_not_run_the_mirror_for_a_date_alone": (
        STRIPE,
        COND,
        "            if cancel_at_period_end is not None:\n",
        [T4],
    ),
    "P2_a_date_does_not_count_as_scheduled": (
        STRIPE,
        SCHED,
        "                    bool(cancel_at_period_end),\n",
        [T1, T2, T3, T4, T5],
    ),
    "P3_the_flag_does_not_count_as_scheduled": (
        STRIPE,
        SCHED,
        "                    bool(cancel_at),\n",
        [T8],
    ),
    "P4_scheduled_needs_both_the_flag_and_a_date": (
        STRIPE,
        SCHED,
        "                    bool(cancel_at_period_end) and bool(cancel_at),\n",
        [T1, T2, T3, T4, T5, T8],
    ),
    "P5_the_recorded_date_ignores_stripes_own": (
        STRIPE,
        '                        stripe_subscription.get("canceled_at")\n',
        "                        None\n",
        [T2],
    ),
    "P6_a_trial_is_mirrored_too": (
        STRIPE,
        "        if user_sub.is_trial:\n"
        "            # A trial's auto_renew is False by design",
        "        if False:\n" "            # A trial's auto_renew is False by design",
        [T9],
    ),
    "P7_a_repeat_message_writes_again": (
        STRIPE,
        "            if not user_sub.auto_renew and user_sub.cancelled_at is not None:\n"
        "                return []\n",
        "",
        [T3],
    ),
    "P8_the_record_never_goes_back_to_renewing": (
        STRIPE,
        "        if user_sub.auto_renew and user_sub.cancelled_at is None:\n"
        "            return []\n",
        "        return []\n",
        [T6],
    ),
    "P9_silence_is_read_as_not_cancelling": (
        STRIPE,
        COND,
        "            if True:\n",
        [T7],
    ),
    "P10_the_keep_answer_no_longer_says_to_contact_support": (
        VIEWS,
        '                    "Nothing was changed. Please contact support if you "',
        '                    "Nothing was changed. Please ask again if you "',
        [K1],
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
