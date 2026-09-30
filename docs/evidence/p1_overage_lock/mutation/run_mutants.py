#!/usr/bin/env python
"""
P1 mutation battery (doctrine Gate 2 / H3).

Each mutant runs in its OWN disposable worktree detached at the commit under
test, with its own test database, so a surviving mutant can never reach a
real tree. After the run the mutated file is restored from the commit's blob
(`git show <commit>:<path>`, never `git checkout`), its sha256 is verified
against the blob, and the worktree is removed.

    python docs/evidence/p1_overage_lock/mutation/run_mutants.py <commit> [--jobs K] [--only M01,M02]

Writes one log per mutant plus results.tsv next to this script.
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = subprocess.check_output(
    ["git", "rev-parse", "--show-toplevel"], cwd=HERE, text=True
).strip()

FLOW = "billing.tests.test_receipt_lookup_outside_transaction"
REPRO = "billing.tests.test_overage_lock_across_network"
UNIT = "billing.tests.test_receipts"
CAP = "billing.tests.test_overage_cap"
# Class-level targets: each mutant runs only the tests that should catch it,
# which keeps a battery worker to a handful of database connections.
LOOKUP = f"{UNIT}.LookupReceiptUrlTests"
FILL = f"{UNIT}.FillReceiptUrlTests"
SCHEDULE = f"{UNIT}.ScheduleReceiptUrlFillTests"
SWEEP = f"{UNIT}.SweepMissingReceiptUrlsTests"
BACKFILL = f"{UNIT}.BackfillReceiptUrlsCommandTests"
RECOVERY = f"{UNIT}.WebhookReceiptRecoveryTests"
CONCURRENCY = f"{UNIT}.ReceiptConcurrencyTests"
ALL = [FLOW, REPRO, UNIT, CAP]

SS = "billing/stripe_service.py"
RC = "billing/receipts.py"
ST = "AutoGrader/settings.py"
BF = "billing/management/commands/backfill_receipt_urls.py"
ER = "billing/event_replay.py"
AD = "billing/admin.py"

# P1c test classes. None of them is the 20-thread ReplayConcurrencyTests,
# so the P1c battery can run without a 20-thread slot.
REPLAY = "billing.tests.test_event_replay"
PINNING = f"{REPLAY}.AllowListPinningTests"
DENY = f"{REPLAY}.DenyByDefaultTests"
WIDENED = f"{REPLAY}.WidenedAllowListTests"
GUARDS = f"{REPLAY}.ReplayOneGuardTests"
ALLOWED = f"{REPLAY}.ReplayTheAllowedFlowTests"
WIRING = f"{REPLAY}.ReplayTaskWiringTests"
LOCKDOWN = f"{REPLAY}.StripeEventAdminLockdownTests"

# (id, guard, file, line or None, old, new, test modules)
# `line` pins a replacement to one line where the text repeats; otherwise
# `old` must occur exactly once in the file.
MUTANTS = [
    # -- the root fix: no lookup inside the webhook transaction -------------
    (
        "M01",
        "on_commit deferral (all flows)",
        RC,
        None,
        "    transaction.on_commit(enqueue, robust=True)",
        "    enqueue()",
        ALL,
    ),
    (
        "M02",
        "lookup inline at overage grant site (put lookup back inside)",
        SS,
        3003,
        "schedule_receipt_url_fill(billing_transaction)",
        "__import__('billing.receipts', fromlist=['x']).fill_receipt_url(billing_transaction.pk)",
        ALL,
    ),
    # -- every site schedules the fill --------------------------------------
    (
        "M03",
        "schedule at individual_checkout trial conversion",
        SS,
        2817,
        "schedule_receipt_url_fill(billing_transaction)",
        "pass",
        [FLOW],
    ),
    (
        "M04",
        "schedule at individual_checkout fresh activation",
        SS,
        2866,
        "schedule_receipt_url_fill(billing_transaction)",
        "pass",
        [FLOW],
    ),
    (
        "M05",
        "schedule at overage block purchase",
        SS,
        3003,
        "schedule_receipt_url_fill(billing_transaction)",
        "pass",
        [FLOW, REPRO],
    ),
    (
        "M06",
        "schedule at license overage, inactive license",
        SS,
        3141,
        "schedule_receipt_url_fill(billing_transaction)",
        "pass",
        [FLOW],
    ),
    (
        "M07",
        "schedule at license overage fulfilled",
        SS,
        3234,
        "schedule_receipt_url_fill(billing_transaction)",
        "pass",
        [FLOW],
    ),
    (
        "M08",
        "schedule at upgrade, subscription changed",
        SS,
        3327,
        "schedule_receipt_url_fill(billing_transaction)",
        "pass",
        [FLOW],
    ),
    (
        "M09",
        "schedule at upgrade applied",
        SS,
        3384,
        "schedule_receipt_url_fill(billing_transaction)",
        "pass",
        [FLOW],
    ),
    (
        "M10",
        "schedule at individual_subscribe replay",
        SS,
        3427,
        "schedule_receipt_url_fill(billing_transaction)",
        "pass",
        [FLOW],
    ),
    (
        "M11",
        "schedule at license_create",
        SS,
        3510,
        "schedule_receipt_url_fill(billing_transaction)",
        "pass",
        [FLOW],
    ),
    (
        "M12",
        "schedule at trial_to_paid replay",
        SS,
        3634,
        "schedule_receipt_url_fill(billing_transaction)",
        "pass",
        [FLOW],
    ),
    # -- stored payment intent id (5 sites that did not store it) -----------
    (
        "M13",
        "store PI: trial conversion",
        SS,
        2812,
        'stripe_payment_intent_id=session.get("payment_intent"),',
        "",
        [FLOW],
    ),
    (
        "M14",
        "store PI: fresh activation",
        SS,
        2861,
        'stripe_payment_intent_id=session.get("payment_intent"),',
        "",
        [FLOW],
    ),
    (
        "M15",
        "store PI: individual_subscribe",
        SS,
        3422,
        'stripe_payment_intent_id=session.get("payment_intent"),',
        "",
        [FLOW],
    ),
    (
        "M16",
        "store PI: license_create",
        SS,
        3505,
        'stripe_payment_intent_id=session.get("payment_intent"),',
        "",
        [FLOW],
    ),
    (
        "M17",
        "store PI: trial_to_paid",
        SS,
        3629,
        'stripe_payment_intent_id=session.get("payment_intent"),',
        "",
        [FLOW],
    ),
    # -- fill-if-null --------------------------------------------------------
    (
        "M18",
        "conditional UPDATE (fill-if-null)",
        RC,
        None,
        "        BillingTransaction.objects.filter(pk=transaction_id)\n        .filter(_MISSING_RECEIPT)\n",
        "        BillingTransaction.objects.filter(pk=transaction_id)\n",
        [FILL, CONCURRENCY],
    ),
    (
        "M19",
        "pre-read already-set short-circuit",
        RC,
        None,
        '    if row["receipt_url"]:\n        return FillOutcome.ALREADY_SET\n',
        "",
        [FILL],
    ),
    (
        "M20",
        "no-reference short-circuit in fill",
        RC,
        None,
        "        return FillOutcome.NO_STRIPE_REFERENCE\n",
        "        pass\n",
        [FILL],
    ),
    (
        "M21",
        "row-missing guard",
        RC,
        None,
        "    if row is None:\n        return FillOutcome.MISSING\n",
        "",
        [FILL],
    ),
    # -- bounded lookup ------------------------------------------------------
    (
        "M22",
        "short timeout",
        RC,
        None,
        "RECEIPT_LOOKUP_TIMEOUT_SECONDS = 10",
        "RECEIPT_LOOKUP_TIMEOUT_SECONDS = 80",
        [LOOKUP],
    ),
    (
        "M23",
        "no automatic retries",
        RC,
        None,
        "        max_network_retries=0,",
        "        max_network_retries=2,",
        [LOOKUP],
    ),
    (
        "M24",
        "generic exception guard",
        RC,
        None,
        "    except Exception:  # noqa: BLE001 - a receipt link must never break a caller",
        "    except ZeroDivisionError:  # noqa: BLE001 - a receipt link must never break a caller",
        [LOOKUP],
    ),
    (
        "M25",
        "StripeError guard",
        RC,
        None,
        "    except stripe.StripeError as exc:",
        "    except ZeroDivisionError as exc:",
        [LOOKUP],
    ),
    (
        "M26",
        "unexpanded latest_charge check",
        RC,
        None,
        "            if isinstance(latest_charge, str):",
        "            if False:",
        [LOOKUP],
    ),
    (
        "M27",
        "invoice-first priority",
        RC,
        None,
        "        if invoice_id:\n            return client.v1.invoices",
        "        if invoice_id and False:\n            return client.v1.invoices",
        [LOOKUP],
    ),
    # -- scheduling ----------------------------------------------------------
    (
        "M28",
        "skip when link already known",
        RC,
        None,
        "    if billing_transaction.receipt_url:\n        return\n",
        "",
        [SCHEDULE],
    ),
    (
        "M29",
        "skip when no Stripe reference",
        RC,
        None,
        "        or billing_transaction.stripe_payment_intent_id\n    ):\n        return\n",
        "        or billing_transaction.stripe_payment_intent_id\n    ):\n        pass\n",
        [SCHEDULE],
    ),
    (
        "M30",
        "robust on_commit",
        RC,
        None,
        "transaction.on_commit(enqueue, robust=True)",
        "transaction.on_commit(enqueue)",
        [SCHEDULE],
    ),
    (
        "M31",
        "broker-tolerant dispatch (safe_delay)",
        RC,
        None,
        "        safe_delay(fill_billing_transaction_receipt_url, transaction_id)",
        "        fill_billing_transaction_receipt_url.delay(transaction_id)",
        [SCHEDULE, RECOVERY],
    ),
    # -- sweep ---------------------------------------------------------------
    (
        "M32",
        "sweep window lower bound",
        RC,
        None,
        "            occurred_at__gte=now - RECEIPT_SWEEP_WINDOW,\n",
        "",
        [SWEEP],
    ),
    (
        "M33",
        "sweep minimum age",
        RC,
        None,
        "            occurred_at__lte=now - RECEIPT_SWEEP_MIN_AGE,\n",
        "",
        [SWEEP],
    ),
    (
        "M34",
        "sweep excludes already-filled rows",
        RC,
        None,
        "        BillingTransaction.objects.filter(_MISSING_RECEIPT)\n        .filter(_HAS_STRIPE_REFERENCE)\n",
        "        BillingTransaction.objects.filter(_HAS_STRIPE_REFERENCE)\n",
        [SWEEP],
    ),
    (
        "M35",
        "sweep excludes reference-less rows",
        RC,
        None,
        "        BillingTransaction.objects.filter(_MISSING_RECEIPT)\n        .filter(_HAS_STRIPE_REFERENCE)\n",
        "        BillingTransaction.objects.filter(_MISSING_RECEIPT)\n",
        [SWEEP],
    ),
    (
        "M36",
        "sweep newest first",
        RC,
        None,
        '        .order_by("-occurred_at")',
        '        .order_by("occurred_at")',
        [SWEEP],
    ),
    (
        "M37",
        "sweep batch bound",
        RC,
        None,
        '.values_list("pk", flat=True)[:RECEIPT_SWEEP_BATCH_SIZE]',
        '.values_list("pk", flat=True)',
        [SWEEP],
    ),
    (
        "M38",
        "sweep time budget",
        RC,
        None,
        "        if monotonic() - started > RECEIPT_SWEEP_TIME_BUDGET_SECONDS:",
        "        if False:",
        [SWEEP],
    ),
    (
        "M39",
        "hourly beat entry",
        ST,
        None,
        '        "task": "billing.tasks.sweep_missing_receipt_urls",',
        '        "task": "billing.tasks.sweep_stale_stripe_events",',
        [SWEEP],
    ),
    # -- backfill command ----------------------------------------------------
    (
        "M40",
        "backfill conditional update",
        BF,
        None,
        "                    pk=txn.pk, receipt_url__isnull=True\n",
        "                    pk=txn.pk\n",
        [BACKFILL],
    ),
    (
        "M41",
        "backfill dry-run",
        BF,
        None,
        "            if not dry_run:",
        "            if True:",
        [BACKFILL],
    ),
    # -- P1c: automatic replay ---------------------------------------------
    (
        "P01",
        "allow-list membership (widen to the upgrade flow)",
        ER,
        None,
        "    ): StripeWebhookHandler._handle_overage_checkout_completed,\n}",
        "    ): StripeWebhookHandler._handle_overage_checkout_completed,\n"
        '    ("checkout.session.completed", "individual_upgrade_checkout"): '
        "StripeWebhookHandler._handle_individual_upgrade_checkout_completed,\n}",
        [PINNING],
    ),
    (
        "P02",
        "vetted-handler second gate",
        ER,
        None,
        "    if qualname not in VETTED_HANDLERS:",
        "    if False:",
        [WIDENED],
    ),
    (
        "P03",
        "VETTED_HANDLERS membership",
        ER,
        None,
        'frozenset({"StripeWebhookHandler._handle_overage_checkout_completed"})',
        'frozenset({"StripeWebhookHandler._handle_overage_checkout_completed",'
        ' "StripeWebhookHandler.handle_charge_refunded"})',
        [PINNING, WIDENED],
    ),
    (
        "P04",
        "missing-flow guard",
        ER,
        None,
        "    if not flow:\n        return ReplayOutcome.NO_FLOW_IN_PAYLOAD, None\n",
        "",
        [DENY],
    ),
    (
        "P05",
        "not-allow-listed guard",
        ER,
        None,
        "    if handler is None:\n        return ReplayOutcome.NOT_ALLOW_LISTED, None\n",
        "",
        [DENY],
    ),
    (
        "P06",
        "attempts cap in classify",
        ER,
        None,
        "    if event_row.auto_replay_attempts >= MAX_AUTO_REPLAY_ATTEMPTS:",
        "    if False:",
        [GUARDS],
    ),
    (
        "P07",
        "attempts cap in the selection query",
        ER,
        None,
        "            auto_replay_attempts__lt=MAX_AUTO_REPLAY_ATTEMPTS,\n",
        "",
        [DENY],
    ),
    (
        "P08",
        "claim only from FAILED",
        ER,
        175,
        "status=StripeEventStatus.FAILED,",
        "",
        [GUARDS],
    ),
    (
        "P09",
        "claim fenced on the attempts seen",
        ER,
        None,
        "        auto_replay_attempts=event_row.auto_replay_attempts,\n",
        "",
        [GUARDS],
    ),
    (
        "P10",
        "stored payload, never re-fetch Stripe",
        ER,
        None,
        '    session = (event_row.payload or {}).get("object") or {}',
        '    session = dict(__import__("stripe").checkout.Session.retrieve('
        '(event_row.payload or {}).get("object", {}).get("id", "cs_x")))',
        [ALLOWED],
    ),
    (
        "P11",
        "handler idempotency guard (_overage_already_granted)",
        SS,
        2916,
        "if payment_intent_id and StripeWebhookHandler._overage_already_granted(",
        "if False and StripeWebhookHandler._overage_already_granted(",
        [ALLOWED],
    ),
    (
        "P12",
        "per-event skip reason recorded on the row",
        ER,
        None,
        "    StripeEvent.objects.filter(pk=event_row.pk).update(auto_replay_note=note[:200])",
        "    pass",
        [DENY, WIDENED],
    ),
    (
        "P13",
        "direct flow handler, not the dispatcher",
        ER,
        None,
        '            handler(obj, obj.get("metadata") or {})',
        "            StripeWebhookHandler.handle_checkout_completed(obj)",
        [GUARDS],
    ),
    (
        "P14",
        "only FAILED events selected",
        ER,
        264,
        "status=StripeEventStatus.FAILED,",
        "",
        [DENY],
    ),
    (
        "P15",
        "hourly beat entry",
        ST,
        None,
        '        "task": "billing.tasks.replay_safe_failed_stripe_events",',
        '        "task": "billing.tasks.sweep_stale_stripe_events",',
        [WIRING],
    ),
    # -- the admin lockdown P1c depends on (red-team recommendation) -------
    (
        "A01",
        "admin add refused",
        AD,
        None,
        "        # Rows are only ever created by an authenticated Stripe delivery.\n"
        "        return False",
        "        # Rows are only ever created by an authenticated Stripe delivery.\n"
        "        return True",
        [LOCKDOWN],
    ),
    (
        "A02",
        "admin change refused",
        AD,
        None,
        "        # View-only: the ledger decides whether money-moving handlers run.\n        return False",
        "        # View-only: the ledger decides whether money-moving handlers run.\n        return True",
        [LOCKDOWN],
    ),
    (
        "A03",
        "admin delete refused",
        AD,
        None,
        "        # prove happened.\n        return False",
        "        # prove happened.\n        return True",
        [LOCKDOWN],
    ),
    (
        "A04",
        "payload read-only in the admin",
        AD,
        None,
        '        "last_error",\n        "payload",\n',
        '        "last_error",\n',
        [LOCKDOWN],
    ),
]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def apply(path, line, old, new):
    with open(path) as fh:
        text = fh.read()
    if line is None:
        if text.count(old) != 1:
            raise SystemExit(
                f"{path}: expected exactly one match for {old!r}, found {text.count(old)}"
            )
        return text.replace(old, new)
    lines = text.split("\n")
    target = lines[line - 1]
    if target.count(old) != 1:
        raise SystemExit(f"{path}:{line}: expected {old!r} in {target!r}")
    lines[line - 1] = target.replace(old, new)
    return "\n".join(lines)


def run_one(commit, mutant, out_dir):
    mid, guard, rel, line, old, new, modules = mutant
    base = tempfile.mkdtemp(prefix=f"p1mut-{mid}-")
    wt = os.path.join(base, "wt")
    log_path = os.path.join(out_dir, f"{mid}.log")
    subprocess.run(
        ["git", "worktree", "add", "--detach", wt, commit],
        cwd=REPO,
        check=True,
        capture_output=True,
    )
    try:
        env_file = os.path.join(REPO, ".env")
        if os.path.exists(env_file) and not os.path.exists(os.path.join(wt, ".env")):
            os.symlink(os.path.realpath(env_file), os.path.join(wt, ".env"))
        with open(os.path.join(wt, "settings_worktree.py"), "w") as fh:
            fh.write(
                "from AutoGrader.settings import *  # noqa: F401,F403\n"
                "from AutoGrader.settings import DATABASES\n"
                'DATABASES["default"].setdefault("TEST", {})\n'
                f'DATABASES["default"]["TEST"]["NAME"] = "test_p1mut_{mid.lower()}"\n'
            )
        target = os.path.join(wt, rel)
        pristine = subprocess.check_output(["git", "show", f"{commit}:{rel}"], cwd=REPO)
        mutated = apply(target, line, old, new)
        with open(target, "w") as fh:
            fh.write(mutated)
        started = time.time()
        proc = subprocess.run(
            [
                sys.executable,
                "manage.py",
                "test",
                *modules,
                "--settings=settings_worktree",
                "--noinput",
                "--keepdb",
                "-v",
                "2",
            ],
            cwd=wt,
            capture_output=True,
            text=True,
            timeout=1800,
        )
        elapsed = time.time() - started
        with open(target, "wb") as fh:
            fh.write(pristine)
        with open(target, "rb") as fh:
            restored_ok = sha256(fh.read()) == sha256(pristine)
        output = proc.stdout + proc.stderr
        with open(log_path, "w") as fh:
            fh.write(
                f"# {mid}: {guard}\n# file: {rel} line: {line}\n# old: {old!r}\n# new: {new!r}\n"
                f"# modules: {' '.join(modules)}\n# commit: {commit}\n# exit: {proc.returncode}\n"
                f"# elapsed_s: {elapsed:.1f}\n# restored_sha256_matches_commit_blob: {restored_ok}\n\n"
            )
            fh.write(output)
        summary = next(
            (
                ln
                for ln in reversed(output.splitlines())
                if ln.startswith(("FAILED", "OK"))
            ),
            "NO SUMMARY",
        )
        # A test that could not even be loaded (the target class or module
        # is missing at this commit, or the mutant broke an import) fails the
        # run without any assertion having caught anything. That is not a
        # kill: it would let a mutant "die" against tests that do not exist.
        load_failure = any(
            marker in output
            for marker in (
                "has no attribute",
                "ImportError",
                "ModuleNotFoundError",
                "SyntaxError",
                "Failed to import test module",
            )
        )
        killed = proc.returncode != 0 and "Ran " in output and not load_failure
        if killed:
            status = "KILLED"
        elif proc.returncode == 0:
            status = "SURVIVED"
        else:
            status = "BROKEN"
        return mid, guard, status, summary, restored_ok, f"{elapsed:.1f}"
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", wt], cwd=REPO, capture_output=True
        )
        shutil.rmtree(base, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("commit")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    commit = subprocess.check_output(
        ["git", "rev-parse", args.commit], cwd=REPO, text=True
    ).strip()
    only = set(filter(None, args.only.split(",")))
    selected = [m for m in MUTANTS if not only or m[0] in only]
    out_dir = os.path.join(HERE, "logs")
    os.makedirs(out_dir, exist_ok=True)
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(lambda m: run_one(commit, m, out_dir), selected))
    with open(os.path.join(HERE, "results.tsv"), "a") as fh:
        for row in results:
            fh.write("\t".join([commit[:12], *map(str, row)]) + "\n")
    for row in results:
        print("\t".join(map(str, row)))
    survivors = [r for r in results if r[2] != "KILLED" or not r[4]]
    print(
        f"\n{len(results) - len(survivors)}/{len(results)} killed with verified restore"
    )
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
