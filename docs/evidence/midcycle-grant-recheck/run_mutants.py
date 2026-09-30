"""
Mutation battery for the overlapping-run re-checks (rule 15).

One mutant per guard, applied in ONE disposable detached worktree at the
commit under test; the new module runs against it, and the file is
restored from the commit's blob and sha256-checked before the next.
BROKEN (a load failure) is never counted as a kill.

    python docs/evidence/midcycle-grant-recheck/run_mutants.py <commit>
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
WORKTREE = os.path.join(os.path.dirname(REPO), "Grade-Automator-Plus-midcycle-mut")
TEST_DB = "test_midcycle_mut"

SV = "billing/services.py"
TK = "billing/tasks.py"
TESTS = ["billing.tests.test_overlapping_run_rechecks"]

GRANT_ACTIVE_TRIAL = (
    "            not user_subscription.is_active\n"
    "            or user_subscription.is_trial\n"
)
GRANT_LOCK = (
    "        user_subscription = UserSubscription.objects.select_for_update().get(\n"
    "            id=user_subscription.id\n"
    "        )\n"
    "        plan = user_subscription.plan\n"
)
TRIAL_RECHECK = "        if not (locked.is_trial and locked.is_active):\n"

# Each mutant: its id, the guard it breaks, the file, the text replaced,
# the replacement, and which occurrence of the text.
MUTANTS = [
    (
        "G1",
        "the grant re-checks at all",
        SV,
        "        if (\n" + GRANT_ACTIVE_TRIAL,
        "        if False and (\n" + GRANT_ACTIVE_TRIAL,
        1,
    ),
    (
        "G2",
        "grant: next_credit_grant_at still due",
        SV,
        "            or user_subscription.next_credit_grant_at > now\n",
        "",
        1,
    ),
    (
        "G3",
        "grant: still active",
        SV,
        GRANT_ACTIVE_TRIAL,
        "            user_subscription.is_trial\n",
        1,
    ),
    (
        "G4",
        "grant: not a trial",
        SV,
        GRANT_ACTIVE_TRIAL,
        "            not user_subscription.is_active\n",
        1,
    ),
    (
        "G5",
        "grant: cycle not ended",
        SV,
        "            or user_subscription.billing_cycle_end <= now\n",
        "",
        1,
    ),
    (
        "G6",
        "grant: a NULL next grant is not due",
        SV,
        "            or user_subscription.next_credit_grant_at is None\n",
        "",
        1,
    ),
    (
        "G7",
        "grant: the row lock",
        SV,
        GRANT_LOCK,
        GRANT_LOCK.replace(".select_for_update()", ""),
        1,
    ),
    (
        "G8",
        "grant: a skip returns None",
        SV,
        "            )\n            return None\n",
        "            )\n            return user_subscription\n",
        1,
    ),
    (
        "G9",
        "task counts a skip as a skip",
        TK,
        "            if SubscriptionService.process_mid_cycle_credit_grant(sub) is None:\n",
        "            if SubscriptionService.process_mid_cycle_credit_grant(sub) is not None:\n",
        1,
    ),
    ("T1", "expiry re-checks at all", SV, TRIAL_RECHECK, "        if False:\n", 1),
    (
        "T2",
        "expiry: still active",
        SV,
        TRIAL_RECHECK,
        "        if not locked.is_trial:\n",
        1,
    ),
    (
        "T3",
        "expiry: still a trial",
        SV,
        TRIAL_RECHECK,
        "        if not locked.is_active:\n",
        1,
    ),
    (
        "T4",
        "expiry acts on the locked row",
        SV,
        "        user_subscription = locked\n",
        "",
        1,
    ),
    (
        "T5",
        "expiry: the row lock",
        SV,
        "        locked = UserSubscription.objects.select_for_update().get(\n",
        "        locked = UserSubscription.objects.get(\n",
        1,
    ),
    (
        "T6",
        "expiry: only an unprocessed trial bucket",
        SV,
        ".filter(bucket_type=CreditBucketType.TRIAL, is_processed=False)",
        ".filter(bucket_type=CreditBucketType.TRIAL)",
        1,
    ),
    ("T7", "expiry returns True when it expired", SV, "        return True\n", "", 1),
    (
        "T8",
        "task counts an expiry skip as a skip",
        TK,
        "                if not SubscriptionService.expire_trial(trial_sub):\n",
        "                if SubscriptionService.expire_trial(trial_sub) is None:\n",
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
