"""
H-28 Change 1 mutation battery (rule 15).

One mutant per guard. Each is applied in ONE disposable detached worktree
at the commit under test, the H-28 test modules are run against it, and the
file is restored from the commit's blob and sha256-checked before the next.
Sequential, one test database (--keepdb after the first run).

    python docs/evidence/h28_p1b/mutation/run_mutants.py <commit> [--only L01,L02]

Classification:
  KILLED    the run failed, tests ran, and nothing failed to load
  SURVIVED  the run passed
  BROKEN    anything else (a load failure: unittest.loader._FailedTest,
            ImportError, SyntaxError), never counted as a kill
"""

import argparse
import hashlib
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
MAIN = os.path.join(os.path.dirname(REPO), "Grade-Automator-Plus")
WORKTREE = os.path.join(os.path.dirname(REPO), "Grade-Automator-Plus-h28-mut")
TEST_DB = "test_h28_mut"

LSM = "billing/license_stripe_mutation.py"
LS = "billing/license_service.py"
SS = "billing/stripe_service.py"
CMD = "billing/management/commands/resolve_licence_stripe_intent.py"

TESTS = [
    "billing.tests.test_h28_licence_stripe_divergence",
    "billing.tests.test_h28_licence_stripe_mutation_intent",
    "billing.tests.test_h28_cancel_phases",
    "billing.tests.test_h28_seat_phases",
    "billing.tests.test_h28_plan_phases",
    "billing.tests.test_h28_convert_phases",
    "billing.tests.test_h28_finalise_retry",
    "billing.tests.test_h28_alerting",
    "billing.tests.test_h28_stripe_budget",
]

PHASE_A = (
    "        # Phase A — or the whole operation, where Stripe is not involved.\n"
    "        try:\n"
    "            with transaction.atomic(durable=True):"
)
PHASE_A_NOT_DURABLE = (
    "        # Phase A — or the whole operation, where Stripe is not involved.\n"
    "        try:\n"
    "            with transaction.atomic():"
)

# Each mutant: its id, the guard it breaks, the file, the text replaced,
# the replacement, and which occurrence of the text.
MUTANTS = [
    ("L01", "cancel phase A durable", LS, PHASE_A, PHASE_A_NOT_DURABLE, 1),
    # Occurrences of PHASE_A in license_service.py, in file order: 1 cancel,
    # 2 change_license_plan, 3 update_seats, 4 convert_license_to_offline.
    ("L02", "seats phase A durable", LS, PHASE_A, PHASE_A_NOT_DURABLE, 3),
    (
        "L03",
        "unknown outcome: a timeout is read back",
        LSM,
        "    if isinstance(exc, stripe.error.APIConnectionError):\n        return True",
        "    if isinstance(exc, stripe.error.APIConnectionError):\n        return False",
        1,
    ),
    (
        "L04",
        "a CardError is not a refusal (payment_errors)",
        LSM,
        "    except payment_errors:\n        raise\n",
        "    except ():\n        raise\n",
        1,
    ),
    (
        "L05",
        "a refused delete is read back (read_back_on)",
        LSM,
        "if not (outcome_unknown(exc) or isinstance(exc, read_back_on)):",
        "if not outcome_unknown(exc):",
        1,
    ),
    (
        "L06",
        "the unpaid change's invoice is voided",
        LSM,
        '            if invoice.get("status") == "open":',
        '            if invoice.get("status") == "never":',
        1,
    ),
    (
        "L07",
        "only the change's own invoice (new_invoice_since)",
        LSM,
        "    return latest if latest and latest != invoice_before else None",
        "    return latest",
        1,
    ),
    (
        "L08",
        "finalise compensates where no money moved",
        LSM,
        "        if compensate is not None:",
        "        if False:",
        1,
    ),
    (
        "L09",
        "a paid seat increase is never compensated",
        LS,
        "compensate=revert if paid_invoice is None else None,",
        "compensate=revert,",
        1,
    ),
    (
        "L10",
        "a paid plan upgrade is never compensated",
        LS,
        "compensate=compensate if paid_invoice is None else None,",
        "compensate=compensate,",
        1,
    ),
    (
        "L11",
        "F0: a price change reaches Stripe",
        LS,
        "if not is_stripe or old_effective_price == new_effective_price:",
        "if True:",
        1,
    ),
    (
        "L12",
        "the per-licence guard is reported as busy",
        LSM,
        "    return GUARD_CONSTRAINT in str(exc)",
        "    return False",
        1,
    ),
    (
        "L13",
        "abandon frees the licence (FAILED)",
        LSM,
        '    is untouched, so FAILED is the truth, and the licence is free again."""\n    _set_status(',
        '    is untouched, so FAILED is the truth, and the licence is free again."""\n    return\n    _set_status(',
        1,
    ),
    (
        "L14",
        "stale check: only intents older than STALE_AFTER",
        LSM,
        "            updated_at__lt=now - STALE_AFTER,",
        "            updated_at__lt=now + STALE_AFTER,",
        1,
    ),
    (
        "L15",
        "stale check alerts once (ESCALATED not re-selected)",
        LSM,
        "                LicenseStripeMutationStatus.STRIPE_APPLIED,\n            ],\n            updated_at__lt",
        (
            "                LicenseStripeMutationStatus.STRIPE_APPLIED,\n"
            "                LicenseStripeMutationStatus.ESCALATED,\n"
            "            ],\n            updated_at__lt"
        ),
        1,
    ),
    (
        "L16",
        "alerts email the super admins",
        LSM,
        "    _email_super_admins(intent, why)\n",
        "    pass\n",
        1,
    ),
    (
        "L17",
        "resolve --apply needs a note",
        CMD,
        "        if not note:\n",
        "        if False:\n",
        1,
    ),
    (
        "L18",
        "the local write is retried",
        LSM,
        "FINALISE_ATTEMPTS = 3",
        "FINALISE_ATTEMPTS = 1",
        1,
    ),
    (
        "L19",
        "a retry after a lost commit reply writes nothing twice",
        LSM,
        "                if (\n                    attempt > 1\n",
        "                if (\n                    attempt > 99\n",
        1,
    ),
    (
        "L20",
        "the retry runs on a fresh connection",
        LSM,
        "            connection.close()\n            time.sleep(FINALISE_BACKOFF_SECONDS[attempt - 1])",
        "            time.sleep(FINALISE_BACKOFF_SECONDS[attempt - 1])",
        1,
    ),
    (
        "L21",
        "an abandoned started call stays PENDING",
        LSM,
        "        if exc.started:\n",
        "        if False:\n",
        1,
    ),
    (
        "L22",
        "the budget bounds each call",
        LSM,
        "    if deadline is None:\n        return fn(*args, **kwargs)",
        "    if True:\n        return fn(*args, **kwargs)",
        1,
    ),
    (
        "L23",
        "the worker reports its caller's transaction",
        LSM,
        "    return getattr(\n        threading.current_thread(), _CALLER_IN_ATOMIC, connection.in_atomic_block\n    )",
        "    return connection.in_atomic_block",
        1,
    ),
    (
        "L24",
        "the worker closes its own connections",
        LSM,
        "            connections.close_all()\n",
        "            pass\n",
        1,
    ),
    ("L25", "convert phase A durable", LS, PHASE_A, PHASE_A_NOT_DURABLE, 4),
    ("L26", "plan change phase A durable", LS, PHASE_A, PHASE_A_NOT_DURABLE, 2),
]

LOAD_FAILURE = ("unittest.loader._FailedTest", "ImportError", "SyntaxError")


def sh(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def replace_nth(text, old, new, nth):
    start = -1
    for _ in range(nth):
        start = text.find(old, start + 1)
        if start < 0:
            raise SystemExit(f"anchor not found (occurrence {nth}): {old[:60]!r}")
    return text[:start] + new + text[start + len(old) :]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("commit")
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    commit = sh("git", "rev-parse", args.commit, cwd=REPO).stdout.strip()
    only = set(filter(None, args.only.split(",")))
    selected = [m for m in MUTANTS if not only or m[0] in only]

    sh("git", "worktree", "add", "--detach", WORKTREE, commit, cwd=REPO)
    try:
        os.symlink(os.path.join(MAIN, ".env"), os.path.join(WORKTREE, ".env"))
        with open(os.path.join(WORKTREE, "settings_worktree.py"), "w") as fh:
            fh.write(
                "from AutoGrader.settings import *  # noqa: F401,F403\n"
                "from AutoGrader.settings import DATABASES\n\n"
                'DATABASES["default"].setdefault("TEST", {})\n'
                f'DATABASES["default"]["TEST"]["NAME"] = {TEST_DB!r}\n'
            )
        logs = os.path.join(HERE, "logs")
        os.makedirs(logs, exist_ok=True)
        rows = []
        for mid, guard, rel, old, new, nth in selected:
            path = os.path.join(WORKTREE, rel)
            pristine = sh("git", "show", f"{commit}:{rel}", cwd=REPO).stdout.encode()
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(replace_nth(text, old, new, nth))
            started = time.monotonic()
            proc = subprocess.run(
                [
                    sys.executable,
                    "manage.py",
                    "test",
                    *TESTS,
                    "--settings=settings_worktree",
                    "--noinput",
                    "--keepdb",
                ],
                cwd=WORKTREE,
                capture_output=True,
                text=True,
                env={**os.environ, "EXEMPT_EMAIL_DOMAINS": ""},
            )
            elapsed = time.monotonic() - started
            sh("git", "checkout", "--", rel, cwd=WORKTREE)
            with open(path, "rb") as fh:
                restored = sha256(fh.read()) == sha256(pristine)
            output = proc.stdout + proc.stderr
            summary = next(
                (
                    ln
                    for ln in reversed(output.splitlines())
                    if ln.startswith(("FAILED", "OK"))
                ),
                "NO SUMMARY",
            )
            loaded = not any(marker in output for marker in LOAD_FAILURE)
            if proc.returncode == 0:
                status = "SURVIVED"
            elif "Ran " in output and loaded:
                status = "KILLED"
            else:
                status = "BROKEN"
            failing = [
                ln for ln in output.splitlines() if ln.startswith(("FAIL:", "ERROR:"))
            ]
            with open(os.path.join(logs, f"{mid}.log"), "w") as fh:
                fh.write(
                    f"# {mid}: {guard}\n# file: {rel} (occurrence {nth})\n"
                    f"# old: {old!r}\n# new: {new!r}\n# commit: {commit}\n"
                    f"# exit: {proc.returncode}\n# elapsed_s: {elapsed:.1f}\n"
                    f"# restored_sha256_matches_commit_blob: {restored}\n\n"
                )
                fh.write("\n".join(failing) + f"\n\n{summary}\n")
            row = (mid, guard, status, summary, str(restored), f"{elapsed:.1f}")
            rows.append(row)
            print("\t".join(row), flush=True)
            if not restored:
                raise SystemExit(f"{mid}: restore did not match the commit blob")
        with open(os.path.join(HERE, "results.tsv"), "a") as fh:
            for row in rows:
                fh.write("\t".join([commit[:12], *row]) + "\n")
        killed = sum(1 for r in rows if r[2] == "KILLED")
        print(f"\n{killed}/{len(rows)} killed with verified restore")
        return 0 if killed == len(rows) else 1
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", WORKTREE],
            cwd=REPO,
            capture_output=True,
        )
        subprocess.run(["git", "worktree", "prune"], cwd=REPO, capture_output=True)


if __name__ == "__main__":
    sys.exit(main())
