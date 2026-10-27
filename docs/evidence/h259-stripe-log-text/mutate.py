# flake8: noqa: E501,B950
"""H-259 a Stripe failure's log line carries its class and request id, not its text: deliberate breaks, and the tests that must catch each.

Each mutant is applied, its test module run, the failing tests are recorded,
the file is restored. Judged (rule 18): SURVIVED = the inner run exits 0;
KILLED = non-zero exit, the run's own "Ran" line, no module that failed to
load, EVERY test named in EXPECTED among the failing tests, and (rule 22)
every FAIL/ERROR block of each expected test holding the fragment written for
it in EXPECTED. Any other failing test is recorded as
also_failed_not_expected, never hidden; BROKEN = anything else. EXPECTED was
written before any run, traced against every assertion of the module
(the control too).

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

HERE = pathlib.Path("docs/evidence/h259-stripe-log-text")
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))

EM = "AutoGrader/error_messages.py"
LS = "AutoGrader/log_scrubbing.py"
LSM = "billing/license_stripe_mutation.py"
LV = "billing/license_views.py"
PM = "billing/payment_method_views.py"
RC = "billing/receipts.py"
SS = "billing/stripe_service.py"
TK = "billing/tasks.py"
TESTS = ["billing.tests.test_h259_stripe_log_text"]
K = "keepdb"


def traceback_back(message):
    """The line is `logger.exception` again: the traceback is logged."""
    old = f"logger.error(\n                {message}"
    return old, old.replace("logger.error(", "logger.exception(")


# id, what is broken, file, text(s) replaced, new text(s), tests, database kind
MUTANTS = [
    # The helper
    (
        "H1",
        "the label carries Stripe's message",
        EM,
        "    return f\"{type(error).__name__} (request {getattr(error, 'request_id', None)})\"\n",
        '    return f"{type(error).__name__}: {error}"\n',
        TESTS,
        K,
    ),
    (
        "H2",
        "the label drops the request id",
        EM,
        "    return f\"{type(error).__name__} (request {getattr(error, 'request_id', None)})\"\n",
        "    return type(error).__name__\n",
        TESTS,
        K,
    ),
    # license_stripe_mutation.py
    (
        "P1",
        "the lost-response warning names the exception again",
        LSM,
        "            intent.id,\n            stripe_error_label(exc),\n        )\n        result = None\n",
        "            intent.id,\n            exc,\n        )\n        result = None\n",
        TESTS,
        K,
    ),
    (
        "P2",
        "the alert quotes the read-back's error again",
        LSM,
        'f"also failed ({stripe_error_label(read_exc)}); the intent is left "',
        'f"also failed ({read_exc}); the intent is left "',
        TESTS,
        K,
    ),
    (
        "P3",
        "the alert quotes the call's error again",
        LSM,
        'f"outcome unknown ({stripe_error_label(exc)}) and the read-back "',
        'f"outcome unknown ({exc}) and the read-back "',
        TESTS,
        K,
    ),
    # license_views.py
    (
        "V1",
        "the licence overage line names the exception again",
        LV,
        "                license_sub.id,\n                stripe_error_label(exc),\n",
        "                license_sub.id,\n                exc,\n",
        TESTS,
        K,
    ),
    (
        "V2",
        "the licence setup-intent line logs the traceback again",
        LV,
        *traceback_back('"Failed to create setup intent for license %s: %s",'),
        TESTS,
        K,
    ),
    # payment_method_views.py
    (
        "W1",
        "the card-list line logs the traceback again",
        PM,
        *traceback_back('"Failed to list payment methods for customer %s: %s",'),
        TESTS,
        K,
    ),
    (
        "W2",
        "the add-card line logs the traceback again",
        PM,
        *traceback_back('"Failed to create setup intent for user %s: %s",'),
        TESTS,
        K,
    ),
    (
        "W3",
        "the portal line logs the traceback again",
        PM,
        *traceback_back('"Failed to create billing portal session for user %s: %s",'),
        TESTS,
        K,
    ),
    (
        "W4",
        "the detach line logs the traceback again",
        PM,
        *traceback_back('"Failed to detach payment method %s for customer %s: %s",'),
        TESTS,
        K,
    ),
    (
        "W5",
        "the set-default line logs the traceback again",
        PM,
        *traceback_back(
            '"Failed to set default payment method %s for customer %s: %s",'
        ),
        TESTS,
        K,
    ),
    (
        "W6",
        "the card-list line drops to WARNING (no Sentry event)",
        PM,
        'logger.error(\n                "Failed to list payment methods for customer %s: %s",',
        'logger.warning(\n                "Failed to list payment methods for customer %s: %s",',
        TESTS,
        K,
    ),
    # receipts.py
    (
        "R1",
        "the receipt line names the exception again",
        RC,
        "            payment_intent_id,\n            stripe_error_label(exc),\n",
        "            payment_intent_id,\n            exc,\n",
        TESTS,
        K,
    ),
    # stripe_service.py
    (
        "T1",
        "the reactivation revert line names the exception again",
        SS,
        "stripe_error_label(revert_exc),",
        "revert_exc,",
        TESTS,
        K,
    ),
    (
        "T2",
        "the duplicate-invoice line logs the traceback again",
        SS,
        *traceback_back('"Failed to void/refund a potential duplicate invoice for "'),
        TESTS,
        K,
    ),
    (
        "T3",
        "the price-revert line logs the traceback again",
        SS,
        *traceback_back('"Failed to revert subscription %s to its previous price %s "'),
        TESTS,
        K,
    ),
    (
        "T4",
        "the unpaid-invoice line logs the traceback again",
        SS,
        *traceback_back(
            '"Failed to void unpaid upgrade invoice %s for subscription %s: %s",'
        ),
        TESTS,
        K,
    ),
    (
        "T5",
        "the released-schedule line names the exception again",
        SS,
        "                user_sub.id,\n                stripe_error_label(exc),\n",
        "                user_sub.id,\n                exc,\n",
        TESTS,
        K,
    ),
    (
        "T6",
        "the setup_intent.succeeded line names the exception again",
        SS,
        "                customer_id,\n                stripe_error_label(exc),\n            )\n            return\n",
        "                customer_id,\n                exc,\n            )\n            return\n",
        TESTS,
        K,
    ),
    # tasks.py
    (
        "K1",
        "the invoice-listing line names the exception again",
        TK,
        "            stripe_subscription_id,\n            stripe_error_label(exc),\n",
        "            stripe_subscription_id,\n            exc,\n",
        TESTS,
        K,
    ),
    (
        "K2",
        "the licence-cancel line names the exception again",
        TK,
        "                            license_sub.id,\n                            stripe_error_label(exc),\n",
        "                            license_sub.id,\n                            str(exc),\n",
        TESTS,
        K,
    ),
    (
        "K3",
        "the reconciliation line logs the traceback again",
        TK,
        '            "subscriptions, so NO drift check was performed: %s",\n            stripe_error_label(exc),\n',
        '            "subscriptions, so NO drift check was performed: %s",\n            stripe_error_label(exc),\n            exc_info=True,\n',
        TESTS,
        K,
    ),
    (
        "K4",
        "the reconciliation line names the exception again",
        TK,
        '            "subscriptions, so NO drift check was performed: %s",\n            stripe_error_label(exc),\n',
        '            "subscriptions, so NO drift check was performed: %s",\n            str(exc),\n',
        TESTS,
        K,
    ),
    # log_scrubbing.py
    (
        "S1",
        "the scrubber has no Stripe pattern",
        LS,
        "        text = _STRIPE_SECRET.sub(_prefix_kept, text)\n",
        "        pass\n",
        TESTS,
        K,
    ),
    (
        "S2",
        "the scrubber drops the prefix",
        LS,
        '    return f"{match.group(1)}{SECRET}"\n',
        "    return SECRET\n",
        TESTS,
        K,
    ),
    (
        "S3",
        "the scrubber misses live keys",
        LS,
        "(?:test|live)_|whsec_)",
        "(?:test)_|whsec_)",
        TESTS,
        K,
    ),
    (
        "S4",
        "the scrubber misses webhook secrets",
        LS,
        "_|whsec_)[",
        "_)[",
        TESTS,
        K,
    ),
    (
        "S5",
        "the pattern no longer starts at a word",
        LS,
        'r"\\b((?:sk|rk)',
        'r"((?:sk|rk)',
        TESTS,
        K,
    ),
    (
        "S6",
        "the Stripe pattern's gate never opens",
        LS,
        '    if "_" in text:\n        text = _STRIPE_SECRET',
        '    if "__" in text:\n        text = _STRIPE_SECRET',
        TESTS,
        K,
    ),
]

_RECEIPT = "test_a_receipt_lookup_failure_logs_no_stripe_text"
_INVOICES = "test_an_invoice_listing_failure_logs_no_stripe_text"
_LICENCE_CANCEL = (
    "test_a_licence_cancel_failure_in_the_renewal_sweep_logs_no_stripe_text"
)
_RECONCILE = "test_a_price_reconciliation_listing_failure_logs_no_stripe_text"
_REACTIVATE = "test_a_failed_compensating_revert_on_reactivation_logs_no_stripe_text"
_DUPLICATE = "test_a_failed_duplicate_invoice_cleanup_logs_no_stripe_text"
_PRICE_REVERT = "test_a_failed_price_revert_logs_no_stripe_text"
_UNPAID_VOID = "test_a_failed_unpaid_invoice_void_logs_no_stripe_text"
_RELEASED = "test_an_already_released_schedule_logs_no_stripe_text"
_SETUP_SUCCEEDED = (
    "test_a_failed_default_card_on_setup_intent_succeeded_logs_no_stripe_text"
)
_LV_OVERAGE = "test_a_stripe_error_on_licence_overage_purchase_logs_no_stripe_text"
_LV_SETUP = "test_a_stripe_error_on_licence_setup_intent_logs_no_stripe_text"
_PM_LIST = "test_a_stripe_error_listing_cards_logs_no_stripe_text"
_PM_ADD = "test_a_stripe_error_adding_a_card_logs_no_stripe_text"
_PM_PORTAL = "test_a_stripe_error_opening_the_billing_portal_logs_no_stripe_text"
_PM_DETACH = "test_a_stripe_error_removing_a_card_logs_no_stripe_text"
_PM_DEFAULT = "test_a_stripe_error_setting_the_default_card_logs_no_stripe_text"
_LOST = "test_a_lost_response_that_applied_logs_no_stripe_text"
_ALERT = "test_the_reconciliation_alert_carries_no_stripe_text"
_SCRUB_LINE = "test_a_key_in_a_log_line_is_masked"
_SCRUB_TRACEBACK = "test_a_key_in_a_traceback_is_masked"
_SCRUB_SENTRY = "test_a_key_in_a_sentry_event_is_masked"
_CONTROL = "test_a_publishable_key_and_ordinary_words_are_left_alone"

LEAK = "unexpectedly found in"
MISSING = "not found in"
LEVEL = "'WARNING' != 'ERROR'"
CHANGED = " != "

SITES = [
    _RECEIPT,
    _INVOICES,
    _LICENCE_CANCEL,
    _RECONCILE,
    _REACTIVATE,
    _DUPLICATE,
    _PRICE_REVERT,
    _UNPAID_VOID,
    _RELEASED,
    _SETUP_SUCCEEDED,
    _LV_OVERAGE,
    _LV_SETUP,
    _PM_LIST,
    _PM_ADD,
    _PM_PORTAL,
    _PM_DETACH,
    _PM_DEFAULT,
    _LOST,
    _ALERT,
]
SCRUBBED = [_SCRUB_LINE, _SCRUB_TRACEBACK, _SCRUB_SENTRY]

#: Written before any run: {mutant: {test: the fragment its every block holds}},
#: every test reckoned to fail under it, traced against every assertion of the
#: module. Every site test asserts first that no record, rendered with its
#: traceback, holds "sk_test" (LEAK), then that its line names the class and
#: request id (MISSING), then the level and that no exception is attached.
EXPECTED = {
    # Every site goes through the helper.
    "H1": {t: LEAK for t in SITES},
    "H2": {t: MISSING for t in SITES},
    "P1": {_LOST: LEAK},
    "P2": {_ALERT: LEAK},
    "P3": {_ALERT: LEAK},
    "V1": {_LV_OVERAGE: LEAK},
    "V2": {_LV_SETUP: LEAK},
    "W1": {_PM_LIST: LEAK},
    "W2": {_PM_ADD: LEAK},
    "W3": {_PM_PORTAL: LEAK},
    "W4": {_PM_DETACH: LEAK},
    "W5": {_PM_DEFAULT: LEAK},
    # No text comes back, so the text and label checks pass; the level fails.
    "W6": {_PM_LIST: LEVEL},
    "R1": {_RECEIPT: LEAK},
    "T1": {_REACTIVATE: LEAK},
    "T2": {_DUPLICATE: LEAK},
    "T3": {_PRICE_REVERT: LEAK},
    "T4": {_UNPAID_VOID: LEAK},
    "T5": {_RELEASED: LEAK},
    "T6": {_SETUP_SUCCEEDED: LEAK},
    "K1": {_INVOICES: LEAK},
    "K2": {_LICENCE_CANCEL: LEAK},
    "K3": {_RECONCILE: LEAK},
    "K4": {_RECONCILE: LEAK},
    "S1": {t: LEAK for t in SCRUBBED},
    # The key's body is gone, so the first check passes; the prefix is missing.
    "S2": {t: MISSING for t in SCRUBBED},
    "S3": {t: LEAK for t in SCRUBBED},
    "S4": {t: LEAK for t in SCRUBBED},
    # Every key is still masked; "risk_live_report" and "task_test_runner" are not left alone.
    "S5": {_CONTROL: CHANGED},
    "S6": {t: LEAK for t in SCRUBBED},
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
