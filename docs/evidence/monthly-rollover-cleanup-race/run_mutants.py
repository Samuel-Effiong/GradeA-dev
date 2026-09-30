"""
Mutation battery for the monthly rollover / cleanup race fix (rule 15).

One mutant per guard, applied in ONE disposable detached worktree at the
commit under test; the fix's module runs against it, and the file is
restored from the commit's blob and sha256-checked before the next.
BROKEN (a load failure) is never counted as a kill.

    python docs/evidence/monthly-rollover-cleanup-race/run_mutants.py <commit>
"""

import argparse
import hashlib
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
MAIN = os.path.join(os.path.dirname(REPO), "Grade-Automator-Plus")
WORKTREE = os.path.join(os.path.dirname(REPO), "Grade-Automator-Plus-rollover-mut")
TEST_DB = "test_rollover_mut"

SV = "billing/services.py"
TK = "billing/tasks.py"
LS = "billing/license_service.py"
TESTS = ["billing.tests.test_monthly_rollover_cleanup_race"]

DUE_FILTER = "        next_credit_grant_at__lte=refresh_due_by(now),\n"

# Each mutant: its id, the guard it breaks, the file, the text replaced,
# the replacement, and which occurrence of the text.
MUTANTS = [
    (
        "R1",
        "annual task: due tolerance",
        TK,
        DUE_FILTER,
        "        next_credit_grant_at__lte=now,\n",
        1,
    ),
    (
        "R2",
        "annual service: the run's now, not its own",
        SV,
        "        now = now or timezone.now()\n        wallet = user.credit_wallet\n",
        "        now = timezone.now()\n        wallet = user.credit_wallet\n",
        1,
    ),
    (
        "R3",
        "annual task passes its start time",
        TK,
        "process_mid_cycle_credit_grant(sub, now=now)",
        "process_mid_cycle_credit_grant(sub)",
        1,
    ),
    (
        "R4",
        "annual re-check: due tolerance",
        SV,
        "            or user_subscription.next_credit_grant_at > refresh_due_by(now)\n",
        "            or user_subscription.next_credit_grant_at > now\n",
        1,
    ),
    (
        "R5",
        "annual task: a due time capped at the cycle end is excluded",
        TK,
        '        next_credit_grant_at__lt=F("billing_cycle_end"),\n',
        "",
        1,
    ),
    (
        "R6",
        "annual re-check: a due time capped at the cycle end is excluded",
        SV,
        "            or user_subscription.next_credit_grant_at\n"
        "            >= user_subscription.billing_cycle_end\n",
        "",
        1,
    ),
    (
        "R7",
        "annual grant: bucket grace",
        SV,
        "expires_at=grace_expiry(bucket_expiry, user_subscription.billing_cycle_end),",
        "expires_at=bucket_expiry,",
        1,
    ),
    (
        "R8",
        "activation: first month's bucket grace",
        SV,
        "expires_at=grace_expiry(monthly_bucket_expiry, billing_end),",
        "expires_at=monthly_bucket_expiry,",
        1,
    ),
    (
        "R9",
        "licence task: due tolerance",
        TK,
        DUE_FILTER,
        "        next_credit_grant_at__lte=now,\n",
        2,
    ),
    (
        "R10",
        "licence re-check: due tolerance",
        TK,
        "if locked_allocation.next_credit_grant_at > refresh_due_by(now):",
        "if locked_allocation.next_credit_grant_at > now:",
        1,
    ),
    (
        "R11",
        "licence task passes its start time",
        TK,
        "                    locked_allocation, now=now\n",
        "                    locked_allocation\n",
        1,
    ),
    (
        "R12",
        "licence service: the run's now, not its own",
        LS,
        "        now = now or timezone.now()\n        next_refresh",
        "        now = timezone.now()\n        next_refresh",
        1,
    ),
    (
        "R13",
        "licence refresh: bucket grace",
        LS,
        "new_expiry=grace_expiry(next_refresh, license_sub.billing_cycle_end),",
        "new_expiry=next_refresh,",
        1,
    ),
    (
        "R14",
        "cleanup keeps an owed bucket at all",
        TK,
        "        if bucket.pk in owed:\n",
        "        if False:\n",
        1,
    ),
    (
        "R15",
        "cleanup keeps only the NEWEST monthly bucket",
        TK,
        "        newest.setdefault(wallet_id, bucket_id)\n",
        "        newest[wallet_id] = bucket_id\n",
        1,
    ),
    (
        "R16",
        "cleanup: only an ACTIVE subscription entitles",
        TK,
        "            is_active=True, user__credit_wallet__in=wallet_ids\n",
        "            user__credit_wallet__in=wallet_ids\n",
        1,
    ),
    (
        "R17",
        "cleanup: an active licence allocation entitles",
        TK,
        "        SchoolCreditAllocation.objects.filter(\n            is_active=True,\n"
        "            license_subscription__is_active=True,\n",
        "        SchoolCreditAllocation.objects.none().filter(\n            is_active=True,\n"
        "            license_subscription__is_active=True,\n",
        1,
    ),
    (
        "R19",
        "immediate plan change: bucket grace",
        SV,
        "expires_at=grace_expiry(new_bucket_expiry, user_sub.billing_cycle_end),",
        "expires_at=new_bucket_expiry,",
        1,
    ),
    (
        "R18",
        "cleanup: an overdue refresh is logged",
        TK,
        "            if bucket.expires_at <= now - OWED_REFRESH_OVERDUE:\n",
        "            if False:\n",
        1,
    ),
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
