"""H-181 mutants. Each is one textual change to one production file; the
test module runs against it; the failing tests are recorded; the file is restored.
The runner is the main hot fix's (H-153's) with this list.

Written BEFORE any run: every mutant names the tests it must fail (EXPECTED, full
dotted names). A mutant is KILLED only if the inner run ends non-zero, shows its own
"Ran" line, loaded every module, and every expected test is among those that failed.
Other outcomes are reported as what they are: SURVIVED (exit 0), KILLED_NOT_AS_EXPECTED,
BROKEN. Only KILLED counts. EVIDENCE.md says, for each mutant, whether the failing set
is exactly the written one.

Rule 17: PYTHONDONTWRITEBYTECODE=1 and every __pycache__ under the mutated apps is
deleted before each mutant and after each restore. Rule 18: each inner run writes
straight to a file (mutant_logs/<name>.txt), stdin from /dev/null; nothing is piped.

    python docs/evidence/h181-wallet-lock-first/mutate.py --check
checks the anchors (each exactly once) and that every mutant parses, runs nothing.
MUT_ONLY=name,name,... runs just those; MUT_RESULTS and MUT_LOGS say where it writes.
"""

import ast
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

TESTS = ["billing.tests.test_wallet_lock_first"]
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
HERE = pathlib.Path("docs/evidence/h181-wallet-lock-first")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))
PYCACHE_ROOTS = ["billing", "users", "AutoGrader"]

SERVICES = "billing/services.py"
LICENCE = "billing/license_service.py"
LOCKS = "billing/locks.py"

W = "billing.tests.test_wallet_lock_first.WalletLockFirstTests."
L = "billing.tests.test_wallet_lock_first.LicenceWalletLockFirstTests."
T_ACTIVATE = W + "test_activate_subscription"
T_CHANGE = W + "test_apply_immediate_plan_change"
T_MID = W + "test_process_mid_cycle_credit_grant"
T_ROLL = W + "test_process_rollover_and_renewal"
T_VIA = W + "test_finalize_trial_conversion_via_stripe"
T_PAID = W + "test_finalize_trial_to_paid_conversion"
T_RENEW = L + "test_process_license_renewal"
T_OFFLINE = L + "test_process_offline_renewal"
T_REFRESH = L + "test_refresh_teacher_credits"
NEW_TESTS = [
    T_ACTIVATE,
    T_CHANGE,
    T_MID,
    T_ROLL,
    T_VIA,
    T_PAID,
    T_RENEW,
    T_OFFLINE,
    T_REFRESH,
]

CALL = (
    "        # H-181: the wallet lock before any bucket lock, the order\n"
    "        # consume_credits uses (billing/locks.py).\n"
    "        wallet = lock_wallet_first(wallet)\n"
)


def removed(after):
    """The call site that precedes the unique text `after`, removed."""
    return (CALL + after, after)


MUTANTS = {
    "M1_activate_subscription_no_wallet_first": (
        SERVICES,
        *removed("\n        # --- Trial forfeiture phase ---\n"),
        [T_ACTIVATE],
    ),
    "M2_apply_immediate_plan_change_no_wallet_first": (
        SERVICES,
        *removed(
            "\n        # --- Roll over unused credits from the bucket being replaced ---\n"
        ),
        [T_CHANGE],
    ),
    "M3_process_mid_cycle_credit_grant_no_wallet_first": (
        SERVICES,
        *removed(
            "\n        # Re-check, under the lock, what process_annual_plan_credit_grants\n"
        ),
        [T_MID],
    ),
    "M4_process_rollover_and_renewal_no_wallet_first": (
        SERVICES,
        *removed("        old_monthly_bucket = (\n"),
        [T_ROLL],
    ),
    "M5_finalize_trial_conversion_via_stripe_no_wallet_first": (
        SERVICES,
        *removed("\n        trial_bucket = (\n"),
        [T_VIA],
    ),
    "M6_finalize_trial_to_paid_conversion_no_wallet_first": (
        SERVICES,
        *removed("\n        # --- STEP 1: Expire the existing TRIAL bucket ---\n"),
        [T_PAID],
    ),
    "M7_licence_rollover_helper_no_wallet_first": (
        LICENCE,
        "        wallet = lock_wallet_first(wallet)\n        current_bucket = (\n",
        "        current_bucket = (\n",
        [T_RENEW, T_OFFLINE, T_REFRESH],
    ),
    "M8_the_helper_locks_nothing": (
        LOCKS,
        "    return CreditWallet.objects.select_for_update().get(pk=wallet.pk)\n",
        "    return wallet\n",
        NEW_TESTS,
    ),
    "M9_licence_rollover_helper_wallet_after_bucket": (
        LICENCE,
        "        wallet = lock_wallet_first(wallet)\n"
        "        current_bucket = (\n"
        "            wallet.buckets.select_for_update()\n"
        "            .filter(bucket_type=CreditBucketType.MONTHLY, is_processed=False)\n"
        '            .order_by("-created_at")\n'
        "            .first()\n"
        "        )\n",
        "        current_bucket = (\n"
        "            wallet.buckets.select_for_update()\n"
        "            .filter(bucket_type=CreditBucketType.MONTHLY, is_processed=False)\n"
        '            .order_by("-created_at")\n'
        "            .first()\n"
        "        )\n"
        "        wallet = lock_wallet_first(wallet)\n",
        [T_RENEW, T_OFFLINE, T_REFRESH],
    ),
    "M10_process_rollover_and_renewal_wallet_after_bucket": (
        SERVICES,
        CALL + "        old_monthly_bucket = (\n"
        "            wallet.buckets.select_for_update()\n"
        '            .filter(bucket_type="MONTHLY", is_processed=False)\n'
        '            .order_by("-created_at")\n'
        "            .first()\n"
        "        )\n",
        "        old_monthly_bucket = (\n"
        "            wallet.buckets.select_for_update()\n"
        '            .filter(bucket_type="MONTHLY", is_processed=False)\n'
        '            .order_by("-created_at")\n'
        "            .first()\n"
        "        )\n" + CALL,
        [T_ROLL],
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
