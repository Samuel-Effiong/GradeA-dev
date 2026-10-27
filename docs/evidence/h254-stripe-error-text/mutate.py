# flake8: noqa: E501,B950
"""H-254 Stripe's text reaches a client only for a card error: deliberate breaks, and the tests that must catch each.

Each mutant is applied, its test modules run, the failing tests are recorded,
the file is restored. Judged (rule 18): SURVIVED = the inner run exits 0;
KILLED = non-zero exit, the run's own "Ran" line, no module that failed to
load, EVERY test named in EXPECTED among the failing tests, and (rule 22)
every FAIL/ERROR block of each expected test holding the fragment written for
it in EXPECTED. Any other failing test is recorded as
also_failed_not_expected, never hidden; BROKEN = anything else. EXPECTED was
written before any run, traced against every assertion of the two modules
(controls too).

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

HERE = pathlib.Path("docs/evidence/h254-stripe-error-text")
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))

EM = "AutoGrader/error_messages.py"
OP = "billing/overage_pricing.py"
QA = "billing/qa_time_travel.py"
SS = "billing/stripe_service.py"
TESTS = [
    "billing.tests.test_h254_stripe_error_text",
    "AutoGrader.tests_error_messages",
]
K = "keepdb"

PASS_THROUGH = (
    "    if isinstance(error, stripe.error.CardError) and error.user_message:\n"
)
LOG_ARGS = "            error.request_id,\n        )\n"
LOG_BLOCK = (
    "    if isinstance(error, stripe.error.StripeError):\n"
    "        logger.warning(\n"
    '            "Stripe call failed: %s (request %s)",\n'
    "            type(error).__name__,\n"
    "            error.request_id,\n"
    "        )\n"
)

# id, what is broken, file, text(s) replaced, new text(s), tests, database kind
MUTANTS = [
    (
        "S1",
        "every StripeError's text passes (the old rule)",
        EM,
        PASS_THROUGH,
        PASS_THROUGH.replace("stripe.error.CardError", "stripe.error.StripeError"),
        TESTS,
        K,
    ),
    (
        "S2",
        "an AuthenticationError's text passes",
        EM,
        PASS_THROUGH,
        PASS_THROUGH.replace(
            "stripe.error.CardError",
            "(stripe.error.CardError, stripe.error.AuthenticationError)",
        ),
        TESTS,
        K,
    ),
    (
        "S3",
        "the log line carries Stripe's message",
        EM,
        ['"Stripe call failed: %s (request %s)",\n', LOG_ARGS],
        [
            '"Stripe call failed: %s (request %s): %s",\n',
            "            error.request_id,\n            error,\n        )\n",
        ],
        TESTS,
        K,
    ),
    (
        "S4",
        "the log line carries the traceback",
        EM,
        LOG_ARGS,
        "            error.request_id,\n            exc_info=error,\n        )\n",
        TESTS,
        K,
    ),
    (
        "S5",
        "the failure is not logged",
        EM,
        LOG_BLOCK,
        "",
        TESTS,
        K,
    ),
    (
        "S6",
        "a card error's text is not shown",
        EM,
        "        return error.user_message\n",
        "        pass\n",
        TESTS,
        K,
    ),
    (
        "S7",
        "OveragePriceUnavailable's text is its operator detail again",
        OP,
        "        super().__init__(OVERAGE_PRICE_UNAVAILABLE_MESSAGE)\n",
        "        super().__init__(detail)\n",
        TESTS,
        K,
    ),
    (
        "S8",
        "the drift detail carries Stripe's repr again",
        OP,
        "            f\"{type(exc).__name__} (request {getattr(exc, 'request_id', None)})\"\n",
        '            f"{exc!r}"\n',
        TESTS,
        K,
    ),
    (
        "S9",
        "the Test Clock message passes user_message again",
        QA,
        '            + describe_stripe_error(exc, fallback_message="Please try again.")\n',
        '            + str(getattr(exc, "user_message", None) or exc)\n',
        TESTS,
        K,
    ),
    (
        "S10",
        "create_custom_price quotes the exception again",
        SS,
        '                "Failed to create custom price: "\n'
        "                + describe_stripe_error(\n"
        '                    exc, fallback_message="Please try again in a moment."\n'
        "                )\n",
        '                f"Failed to create custom price: {exc}"\n',
        TESTS,
        K,
    ),
    (
        "S11",
        "the release-failure message names the stale schedule again",
        SS,
        '                    "stale schedule is attached "\n',
        '                    f"stale schedule ({stale_schedule_id}) is attached "\n',
        TESTS,
        K,
    ),
    (
        "S12",
        "the retry-failure message names the stale schedule again",
        SS,
        '                    "after releasing a stale schedule: "\n',
        '                    f"after releasing stale schedule {stale_schedule_id}: "\n',
        TESTS,
        K,
    ),
]

# billing.tests.test_h254_stripe_error_text
_R1 = "test_an_expired_key_on_resume_answers_without_the_key_text"
_R2 = "test_the_resume_failure_is_logged_with_class_and_request_id_only"
_R3 = "test_an_expired_key_on_cancel_answers_without_the_key_text"
_K1 = "test_a_card_errors_text_still_reaches_the_body"
_R4 = "test_no_non_card_stripe_error_passes_its_text"
_R5 = "test_an_expired_key_on_overage_purchase_answers_without_the_key_text"
_R6 = "test_the_drift_report_names_the_failure_without_its_text"
_R7 = "test_an_expired_key_on_payment_methods_answers_without_the_key_text"
_R8 = "test_a_test_clock_failure_carries_no_stripe_text"
_R9 = "test_create_custom_price_carries_no_stripe_text"
_R10 = "test_a_stale_schedule_release_failure_names_no_schedule_id"
_R11 = "test_a_stale_schedule_retry_failure_names_no_schedule_id"
# AutoGrader.tests_error_messages (changed by this row)
_EM_CARD = "test_uses_a_card_errors_user_message"

LEAK = "unexpectedly found in"
NO_LOG = "no logs of level WARNING or higher triggered on AutoGrader.error_messages"
LISTS = "Lists differ"
TRACEBACK = "is not None"
CARD = "Your card was declined"

#: Written before any run: {mutant: {test: the fragment its every block holds}},
#: every test reckoned to fail under it, traced against every assertion of the
#: two modules. Every probe is an AuthenticationError, so S1 and S2 fail the
#: same tests; R5 and R6 do not go through the classifier.
_EVERY_CLASSIFIER_ROUTE = {
    _R1: LEAK,
    _R3: LEAK,
    _R7: LEAK,
    _R8: LEAK,
    _R9: LEAK,
    # The schedule id is gone, so the id check passes; the text check fails.
    _R10: LEAK,
    _R11: LEAK,
}
EXPECTED = {
    # The early return skips the log, so R2 sees none.
    "S1": {**_EVERY_CLASSIFIER_ROUTE, _R2: NO_LOG, _R4: LISTS},
    "S2": {**_EVERY_CLASSIFIER_ROUTE, _R2: NO_LOG, _R4: LISTS},
    "S3": {_R2: LEAK},
    # R2's text checks pass first; its exc_info check fails.
    "S4": {_R2: TRACEBACK},
    "S5": {_R2: NO_LOG},
    # A card error falls through to the fallback ("Please try again." on the
    # cancel route, the default sentence in the unit test).
    "S6": {_K1: CARD, _EM_CARD: CARD},
    # The detail holds no Stripe text, so R5 fails on the price id.
    "S7": {_R5: LEAK},
    "S8": {_R6: LEAK},
    "S9": {_R8: LEAK},
    "S10": {_R9: LEAK},
    "S11": {_R10: LEAK},
    "S12": {_R11: LEAK},
}

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
    """Expected tests with a block that does not hold its written fragment."""
    return sorted(
        name
        for name, fragment in EXPECTED[mid].items()
        if not all(fragment in b for b in blocks_of(text, name))
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


def edits(old, new):
    """The (old, new) pairs of one mutant: one pair, or several."""
    if isinstance(old, str):
        return [(old, new)]
    assert len(old) == len(new)
    return list(zip(old, new, strict=True))


def broken(source, old, new):
    for one_old, one_new in edits(old, new):
        source = source.replace(one_old, one_new, 1)
    return source


def _exit_on_sigterm(signum, _frame):
    raise SystemExit(128 + signum)


def main():
    signal.signal(signal.SIGTERM, _exit_on_sigterm)
    assert [m[0] for m in MUTANTS] == list(EXPECTED), "EXPECTED names every mutant"
    originals = {}
    for mid, _what, path, old, new, _tests, kind in MUTANTS:
        source = originals.setdefault(path, open(path).read())
        for one_old, _one_new in edits(old, new):
            found = source.count(one_old)
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
