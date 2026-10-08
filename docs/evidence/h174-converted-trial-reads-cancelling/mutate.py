"""H-174 mutants. Each is one textual change to one production file; four
test modules run against it; the failing tests are recorded; the file is
restored. The runner is H-153's with this list.

Written BEFORE any run: every mutant names the tests it must fail
(EXPECTED, full dotted names), each set read assertion by assertion
against the mutant. A mutant is KILLED only if the inner run ends
non-zero, shows its own "Ran" line, loaded every module, and every
expected test is among those that failed. Other outcomes are reported as
what they are: SURVIVED (exit 0), KILLED_NOT_AS_EXPECTED (it failed, but
not all the named tests did), BROKEN (anything else). Only KILLED counts.
EVIDENCE.md also says, for each mutant, whether the failing set is exactly
the written one or holds more (the four modules have many older tests I
have read only in part).

Every one of the new module's 20 tests is named by at least one mutant
(checked below), so the four that are green on the old code are seen red
here.

Rule 17: the inner run has PYTHONDONTWRITEBYTECODE=1 and every
__pycache__ under the mutated apps is deleted before each mutant and after
each restore. Rule 18: each inner run writes straight to a file
(mutant_logs/<name>.txt), stdin from /dev/null; nothing is piped.

    python docs/evidence/h174-converted-trial-reads-cancelling/mutate.py --check
checks the anchors (each exactly once) and that every mutant parses, and
runs nothing. MUT_ONLY=name,name,... runs just those mutants; MUT_RESULTS
and MUT_LOGS say where that run writes.
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
    "billing.tests.test_converted_trial_is_not_cancelling",
    "billing.tests.test_subscription_reactivation",
    "billing.tests.test_subscription_cancel",
    "billing.tests.test_cancellation_visibility",
]
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
HERE = pathlib.Path("docs/evidence/h174-converted-trial-reads-cancelling")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))
PYCACHE_ROOTS = ["billing", "users", "AutoGrader"]

SERVICES = "billing/services.py"
STRIPE = "billing/stripe_service.py"
VIEWS = "billing/views.py"

N = "billing.tests.test_converted_trial_is_not_cancelling."
P = N + "ThePurchaseTests."
S = N + "ATrialThatStripeConvertsTests."
K = N + "KeepSubscriptionTests."
C = N + "TheSharedCoreTests."
X = N + "CancelOnSuchARecordTests."
RENEW = P + "test_the_plan_is_set_to_renew"
PAGE = P + "test_the_page_is_not_told_a_cancellation_is_pending"
ANNUAL = P + "test_an_annual_plan_the_same"
STRAY = P + "test_a_stray_date_of_cancellation_on_the_trial_is_cleared"
S_RENEW = S + "test_it_is_set_to_renew_and_the_page_is_told_nothing_is_pending"
S_STRAY = S + "test_a_stray_date_of_cancellation_is_cleared"
K_AFTER = K + "test_right_after_the_purchase_the_answer_does_not_contradict_itself"
K_FIX = K + "test_a_record_converted_before_the_fix_is_corrected"
K_STALE = K + "test_a_stale_date_goes_with_it"
K_GUESS = K + "test_it_does_not_guess_when_stripe_does_not_say"
K_REAL = K + "test_a_real_cancellation_is_still_undone_at_stripe"
K_TRIAL = K + "test_a_trial_is_never_corrected_into_a_renewing_plan"
C_RESULT = C + "test_it_reports_a_local_correction_and_no_change_at_stripe"
C_PLAN = C + "test_choosing_another_plan_corrects_it_and_claims_no_undone_cancellation"
X_SAYS = X + "test_it_says_what_it_did_and_records_when"
X_SECOND = X + "test_a_second_cancel_still_says_already_and_keeps_the_date"
# The delta (a cancellation Stripe has scheduled), added 19:10 before any run of it.
K_DATED = K + "test_a_cancellation_scheduled_by_date_at_stripe_is_not_corrected_away"
K_RECORDED = K + "test_a_cancellation_stripe_has_recorded_is_not_corrected_away"
K_EMPTY = K + "test_an_answer_whose_two_dates_are_empty_is_still_corrected"
C_SCHEDULED = (
    C + "test_it_reports_a_cancellation_scheduled_at_stripe_and_changes_nothing"
)

# Older tests, read line by line for the mutants that name them.
REACT = (
    "billing.tests.test_subscription_reactivation."
    "SubscriptionReactivationServiceTestCase."
)
R_LOCAL = REACT + "test_local_record_is_corrected_when_stripe_is_not_cancelling"
R_PASTDUE = REACT + "test_surfaces_past_due_warning"
RESUMED_OK = (
    "billing.tests.test_subscription_reactivation."
    "SubscriptionResumeTestCase.test_successful_resume"
)
IDEM = "billing.tests.test_subscription_cancel.CancelIdempotencyTests."
CANCEL_ALREADY = IDEM + "test_already_not_renewing_reports_so"
CANCEL_TWICE = IDEM + "test_cancelling_twice_in_a_row_succeeds"
STAMPS = (
    "billing.tests.test_cancellation_visibility."
    "CancelEndpointStampsCancelledAtTests."
)
NO_SLIDE = STAMPS + "test_repeat_cancel_does_not_slide_the_date"
TRIAL_NO_DATE = STAMPS + "test_cancelling_a_trial_does_not_fabricate_a_date"

NEW_TESTS = [
    RENEW, PAGE, ANNUAL, STRAY, S_RENEW, S_STRAY, K_AFTER, K_FIX, K_STALE,
    K_GUESS, K_REAL, K_TRIAL, C_RESULT, C_PLAN, X_SAYS, X_SECOND,
    K_DATED, K_RECORDED, K_EMPTY, C_SCHEDULED,
]  # fmt: skip

# ---- anchors
PAID_SET = (
    "        # named in update_fields below.\n"
    "        trial_sub.auto_renew = True\n"
    "        trial_sub.cancelled_at = None\n"
)
PAID_FIELDS = (
    '                "stripe_status",\n'
    '                "auto_renew",\n'
    '                "cancelled_at",\n'
)
VIA_SET = (
    "        # while Stripe, never told to cancel, went on to charge.\n"
    "        trial_sub.auto_renew = True\n"
    "        trial_sub.cancelled_at = None\n"
)
VIA_FIELDS = (
    '                "next_credit_grant_at",\n'
    '                "auto_renew",\n'
    '                "cancelled_at",\n'
)
REPAIR_IF = '        elif stripe_sub.get("cancel_at_period_end") is False:\n'
REPAIR_TRIAL = (
    "                    and not locked_sub.is_trial\n"
    "                    and now < locked_sub.billing_cycle_end\n"
)
REPAIR_RENEW = (
    "                    if not locked_sub.auto_renew:\n"
    "                        locked_sub.auto_renew = True\n"
    '                        local_update_fields.append("auto_renew")\n'
)
REPAIR_DATE = (
    "                    if locked_sub.cancelled_at is not None:\n"
    "                        locked_sub.cancelled_at = None\n"
    '                        local_update_fields.append("cancelled_at")\n'
)
NOTICE = "                resumed_from_cancellation = reactivation.stripe_changed\n"
CORRECTED_MSG = "            elif not stripe_changed:\n"
NEVER = "                cancellation_never_recorded = (\n"
NEVER_TRIAL = "                    and not user_subscription.is_trial\n"
NEVER_DATE = "                    and user_subscription.cancelled_at is None\n"
NEVER_STAMP = (
    "                elif cancellation_never_recorded:\n"
    "                    user_subscription.cancelled_at = timezone.now()\n"
    '                    update_fields.append("cancelled_at")\n'
)

#: What fails when a purchase leaves the paid record "not renewing": the
#: three purchase tests and the stray-date one (their page reads pending),
#: and "right after the purchase" (keep then CORRECTS, so it answers
#: "resumed", not "already_active").
PURCHASE_NOT_RENEWING = [RENEW, PAGE, ANNUAL, STRAY, K_AFTER]
#: What fails when "keep" does not set auto_renew on a record Stripe
#: contradicts.
#: (Re-derived 19:10 with the delta: K_EMPTY is such a record too. The two
#: dated answers are caught by the branch before the correction, so R1,
#: R2 and R4, which change only the correcting branch, leave them green.)
KEEP_DOES_NOT_CORRECT = [
    K_FIX, K_STALE, C_RESULT, C_PLAN, R_LOCAL, R_PASTDUE, K_EMPTY,
]  # fmt: skip
SCHEDULED_IF = (
    '            stripe_sub.get("cancel_at") or stripe_sub.get("canceled_at")\n'
)
SCHEDULED_SET = "            scheduled_at_provider = True\n"
SCHEDULED_MSG = "            elif result.scheduled_at_provider:\n"
SCHEDULED_STATUS = '                            "cancellation_scheduled"\n'

MUTANTS = {
    # ---- the purchase from the plans page (finalize_trial_to_paid_conversion)
    "A1_the_purchase_does_not_set_auto_renew": (
        SERVICES,
        PAID_SET,
        "        # named in update_fields below.\n"
        "        trial_sub.cancelled_at = None\n",
        PURCHASE_NOT_RENEWING,
    ),
    "A2_the_purchase_sets_it_but_does_not_save_it": (
        SERVICES,
        PAID_FIELDS,
        '                "stripe_status",\n                "cancelled_at",\n',
        PURCHASE_NOT_RENEWING,
    ),
    "A3_the_purchase_keeps_a_stray_date": (
        SERVICES,
        PAID_SET,
        "        # named in update_fields below.\n"
        "        trial_sub.auto_renew = True\n",
        [STRAY],
    ),
    "A4_the_purchase_clears_the_date_but_does_not_save_it": (
        SERVICES,
        PAID_FIELDS,
        '                "stripe_status",\n                "auto_renew",\n',
        [STRAY],
    ),
    # ---- the trial Stripe converts (finalize_trial_conversion_via_stripe)
    "B1_the_stripe_conversion_does_not_set_auto_renew": (
        SERVICES,
        VIA_SET,
        "        # while Stripe, never told to cancel, went on to charge.\n"
        "        trial_sub.cancelled_at = None\n",
        [S_RENEW],
    ),
    "B2_the_stripe_conversion_sets_it_but_does_not_save_it": (
        SERVICES,
        VIA_FIELDS,
        '                "next_credit_grant_at",\n                "cancelled_at",\n',
        [S_RENEW],
    ),
    "B3_the_stripe_conversion_keeps_a_stray_date": (
        SERVICES,
        VIA_SET,
        "        # while Stripe, never told to cancel, went on to charge.\n"
        "        trial_sub.auto_renew = True\n",
        [S_STRAY],
    ),
    "B4_the_stripe_conversion_clears_the_date_but_does_not_save_it": (
        SERVICES,
        VIA_FIELDS,
        '                "next_credit_grant_at",\n                "auto_renew",\n',
        [S_STRAY],
    ),
    # ---- the keep request, in the function reactivate_if_cancelling
    "R1_keep_never_corrects_the_local_record": (
        STRIPE,
        REPAIR_IF,
        "        elif False:\n",
        KEEP_DOES_NOT_CORRECT,
    ),
    "R2_keep_corrects_when_stripe_does_not_say": (
        STRIPE,
        REPAIR_IF,
        '        elif not stripe_sub.get("cancel_at_period_end"):\n',
        [K_GUESS],
    ),
    "R3_keep_corrects_a_trial": (
        STRIPE,
        REPAIR_TRIAL,
        "                    and now < locked_sub.billing_cycle_end\n",
        [K_TRIAL],
    ),
    "R4_keep_clears_the_date_but_leaves_auto_renew": (
        STRIPE,
        REPAIR_RENEW,
        "",
        KEEP_DOES_NOT_CORRECT,
    ),
    "R5_keep_leaves_the_stale_date": (STRIPE, REPAIR_DATE, "", [K_STALE]),
    "R6_choosing_a_plan_claims_an_undone_cancellation": (
        STRIPE,
        NOTICE,
        "                resumed_from_cancellation = reactivation.changed\n",
        [C_PLAN],
    ),
    # ---- the delta: a cancellation Stripe has scheduled
    "D1_a_cancellation_by_date_counts_as_not_cancelling": (
        STRIPE,
        SCHEDULED_IF,
        '            stripe_sub.get("canceled_at")\n',
        [K_DATED, C_SCHEDULED],
    ),
    "D2_a_recorded_cancellation_counts_as_not_cancelling": (
        STRIPE,
        SCHEDULED_IF,
        '            stripe_sub.get("cancel_at")\n',
        [K_RECORDED],
    ),
    "D3_the_core_does_not_report_it": (
        STRIPE,
        SCHEDULED_SET,
        "            pass\n",
        [K_DATED, K_RECORDED, C_SCHEDULED],
    ),
    "D4_keep_says_already_active_and_set_to_renew": (
        VIEWS,
        SCHEDULED_MSG,
        "            elif False:\n",
        [K_DATED, K_RECORDED],
    ),
    "D5_keep_gives_the_status_already_active": (
        VIEWS,
        SCHEDULED_STATUS,
        '                            "already_active"\n',
        [K_DATED, K_RECORDED],
    ),
    # ---- what the two requests answer
    "V1_keep_says_resumed_normally_for_a_corrected_record": (
        VIEWS,
        CORRECTED_MSG,
        "            elif False:\n",
        [K_FIX],
    ),
    "V2_keep_says_corrected_for_a_real_resume": (
        VIEWS,
        CORRECTED_MSG,
        "            elif True:\n",
        [K_REAL, RESUMED_OK],
    ),
    "V3_cancel_treats_an_unrecorded_cancellation_as_already_done": (
        VIEWS,
        NEVER,
        "                cancellation_never_recorded = False and (\n",
        [X_SAYS],
    ),
    "V4_cancel_does_not_record_when": (VIEWS, NEVER_STAMP, "", [X_SAYS]),
    "V5_cancel_forgets_a_recorded_cancellation": (
        VIEWS,
        NEVER_DATE,
        "",
        [X_SECOND, CANCEL_ALREADY, CANCEL_TWICE, NO_SLIDE],
    ),
    "V6_cancel_fabricates_a_date_for_a_trial": (
        VIEWS,
        NEVER_TRIAL,
        "",
        [TRIAL_NO_DATE],
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
