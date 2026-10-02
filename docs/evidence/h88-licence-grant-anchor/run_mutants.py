"""
Mutation battery for H-88, H-93 and H-81 (rule 15), one branch:
  M: licence refreshes are anchored to the allocation's own month (H-88);
  W: the licence consumption window reopens on the licence's monthly
     points (H-93);
  O: a renewal reports the monthly grants never made (H-81).

One disposable detached worktree at the commit under test. A baseline run
on the unmutated tree must pass first; then each mutant, with the file
restored from the commit's blob and sha256-checked after it. BROKEN (a
load failure) is never counted as a kill.

Rule 17: every run has PYTHONDONTWRITEBYTECODE=1, and the __pycache__
directories of the mutated modules' packages are deleted before the
baseline, before each mutant and after each restore, so a stale .pyc
(same size, same mtime second) can never stand in for the mutant.

    python docs/evidence/h88-licence-grant-anchor/run_mutants.py <commit>
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
MAIN = os.path.join(os.path.dirname(REPO), "Grade-Automator-Plus")
WORKTREE = os.path.join(os.path.dirname(REPO), "Grade-Automator-Plus-h88-mut")
TEST_DB = "test_h88_mut"

RT = "billing/refresh_timing.py"
LS = "billing/license_service.py"
SV = "billing/services.py"
TESTS = [
    "billing.tests.test_licence_grant_anchor",
    "billing.tests.test_allocation_anchor",
    "billing.tests.test_owed_grant_detection",
]

RENEWAL_ANCHOR = (
    "                    allocation.grant_anchor_at = now\n"
    "                    allocation.save(\n"
)
REPORT_CALL = (
    "        LicenseSubscriptionService._report_owed_refreshes(\n"
    "            license_sub, active_allocations, now\n"
    "        )\n"
)

MUTANTS = [
    (
        "M1",
        "an enrolment stores its anchor",
        LS,
        "        allocation.grant_anchor_at = now\n        allocation.save(\n",
        "        allocation.save(\n",
        1,
    ),
    (
        "M2",
        "the renewal task restarts the teacher's month",
        LS,
        RENEWAL_ANCHOR,
        "                    allocation.save(\n",
        1,
    ),
    (
        "M3",
        "the offline renewal restarts the teacher's month",
        LS,
        RENEWAL_ANCHOR,
        "                    allocation.save(\n",
        2,
    ),
    (
        "M4",
        "the chain is computed from the anchor, not the served due",
        LS,
        "            anchor, served_due, license_sub.billing_cycle_end\n",
        "            served_due, served_due, license_sub.billing_cycle_end\n",
        1,
    ),
    (
        "M5",
        "the helper is fed the served due, not now",
        LS,
        "            anchor, served_due, license_sub.billing_cycle_end\n",
        "            anchor, now, license_sub.billing_cycle_end\n",
        1,
    ),
    (
        "M6",
        "the fallback is the later of creation and the cycle start",
        LS,
        "            max(allocation.created_at, license_sub.billing_cycle_start),\n"
        "            served_due,\n",
        "            allocation.created_at,\n            served_due,\n",
        1,
    ),
    (
        "M7",
        "a due time off the anchor's chain is its own anchor",
        RT,
        "        return candidate\n    return served_due\n",
        "        return candidate\n    return candidate\n",
        1,
    ),
    (
        "M8",
        "the snap window covers a due time before its point",
        RT,
        "    if candidate + relativedelta(months=k) <= served_due + ANCHOR_SNAP:\n",
        "    if candidate + relativedelta(months=k) <= served_due:\n",
        1,
    ),
    (
        "M9",
        "the refresh stores the anchor it resolved",
        LS,
        "        allocation.next_credit_grant_at = next_refresh\n"
        "        allocation.grant_anchor_at = anchor\n",
        "        allocation.next_credit_grant_at = next_refresh\n",
        1,
    ),
    (
        "M10",
        "a caught-up refresh logs a WARNING",
        LS,
        "        if served_due + timedelta(days=1) < now:\n",
        "        if False:\n",
        1,
    ),
    (
        "M11",
        "a caught-up bucket lives from its grant time",
        LS,
        "            if next_refresh > now\n",
        "            if True\n",
        1,
    ),
    (
        "M12",
        "a caught-up bucket ends by the contract end",
        LS,
        "else min(now + relativedelta(months=1), license_sub.billing_cycle_end)",
        "else now + relativedelta(months=1)",
        1,
    ),
    (
        "M13",
        "a new admin allocation stores its anchor",
        LS,
        '                "grant_anchor_at": now,\n',
        "",
        1,
    ),
    (
        "M14",
        "a reactivated admin allocation restarts its month",
        LS,
        "            allocation.next_credit_grant_at = next_refresh\n"
        "            allocation.grant_anchor_at = now\n",
        "            allocation.next_credit_grant_at = next_refresh\n",
        1,
    ),
    (
        "M15",
        "1a's F1: a stored anchor is used only while the due time is on its chain",
        RT,
        "    candidate = stored_anchor if stored_anchor is not None else fallback_anchor\n",
        "    if stored_anchor is not None:\n        return stored_anchor\n"
        "    candidate = fallback_anchor\n",
        1,
    ),
    (
        "M16",
        "1a's F1: the QA time-travel tool clears the anchor with the due time",
        "billing/qa_time_travel.py",
        "                allocation.grant_anchor_at = None\n",
        "",
        1,
    ),
    (
        "W1",
        "H-93: a run at a licence point counts that point",
        RT,
        "    while anchor + relativedelta(months=k + 1) <= at:\n",
        "    while anchor + relativedelta(months=k + 1) < at - relativedelta(days=1):\n",
        1,
    ),
    (
        "W2",
        "H-93: a point within the due tolerance of the run reopens",
        LS,
        "                    license_sub.billing_cycle_start, refresh_due_by(now)\n",
        "                    license_sub.billing_cycle_start, now\n",
        1,
    ),
    (
        "W3",
        "H-93: the points are the licence's, not the teacher's",
        LS,
        "                    license_sub.billing_cycle_start, refresh_due_by(now)\n",
        "                    anchor, refresh_due_by(now)\n",
        1,
    ),
    (
        "W4",
        "H-93: the window reopens on the points, not after a calendar month",
        LS,
        "                consumption_window_start__lt=latest_monthly_point(\n"
        "                    license_sub.billing_cycle_start, refresh_due_by(now)\n"
        "                )\n",
        "                consumption_window_start__lte=refresh_due_by(now)\n"
        "                - relativedelta(months=1)\n",
        1,
    ),
    (
        "W5",
        "H-93: a refresh inside a licence month leaves the window alone",
        LS,
        "                consumption_window_start__lt=latest_monthly_point(\n"
        "                    license_sub.billing_cycle_start, refresh_due_by(now)\n"
        "                )\n",
        "                consumption_window_start__lt=now\n",
        1,
    ),
    (
        "O1",
        "H-81: the licence renewals log the owed refreshes",
        LS,
        "            owed = grants_owed(anchor, due, until)\n            if owed:\n",
        "            owed = grants_owed(anchor, due, until)\n            if False:\n",
        1,
    ),
    (
        "O2",
        "H-81: an early renewal's future refreshes are not owed",
        LS,
        "        until = min(license_sub.billing_cycle_end, now)\n",
        "        until = license_sub.billing_cycle_end\n",
        1,
    ),
    (
        "O3",
        "H-81: a due time within the snap of the end is not owed",
        RT,
        "    while next_due + ANCHOR_SNAP < until:\n",
        "    while next_due < until:\n",
        1,
    ),
    (
        "O4",
        "H-81: every owed grant is counted",
        RT,
        "        owed += 1\n        _, next_due = next_monthly_grant(anchor, next_due, until)\n",
        "        owed += 1\n        break\n",
        1,
    ),
    (
        "O5",
        "H-81: the renewal task reports",
        LS,
        REPORT_CALL,
        "",
        1,
    ),
    (
        "O6",
        "H-81: the offline renewal reports",
        LS,
        REPORT_CALL,
        "",
        2,
    ),
    (
        "O7",
        "H-81: the individual renewal logs the owed grants",
        SV,
        "            if owed:\n                logger.error(\n"
        '                    "Renewal of subscription %s (user %s): %d monthly "\n',
        "            if False:\n                logger.error(\n"
        '                    "Renewal of subscription %s (user %s): %d monthly "\n',
        1,
    ),
    (
        "O8",
        "H-81: the licence line names the allocation's user by id",
        LS,
        "                    allocation.id,\n                    allocation.user_id,\n"
        "                    owed,\n",
        "                    allocation.id,\n                    license_sub.id,\n"
        "                    owed,\n",
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


def clear_pycache(worktree):
    """Rule 17: drop the compiled copies of every mutated module."""
    for rel in sorted({m[2] for m in MUTANTS}):
        cache = os.path.join(worktree, os.path.dirname(rel), "__pycache__")
        if os.path.isdir(cache):
            shutil.rmtree(cache)


def run_tests():
    return subprocess.run(
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
        env={
            **os.environ,
            "EXEMPT_EMAIL_DOMAINS": "",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )


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
        clear_pycache(WORKTREE)
        baseline = run_tests()
        with open(os.path.join(logs, "baseline.log"), "w") as fh:
            fh.write(f"# baseline, commit {commit}, exit {baseline.returncode}\n\n")
            fh.write(baseline.stdout + baseline.stderr)
        print(f"baseline exit={baseline.returncode}", flush=True)
        if baseline.returncode != 0:
            raise SystemExit("baseline is red: no mutant is run")
        rows = []
        for mid, guard, rel, old, new, nth in selected:
            path = os.path.join(WORKTREE, rel)
            pristine = sh("git", "show", f"{commit}:{rel}", cwd=REPO).stdout.encode()
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(replace_nth(text, old, new, nth))
            clear_pycache(WORKTREE)
            started = time.monotonic()
            proc = run_tests()
            elapsed = time.monotonic() - started
            sh("git", "checkout", "--", rel, cwd=WORKTREE)
            clear_pycache(WORKTREE)
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
