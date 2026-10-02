"""
Mutation battery for H-91 (rule 15): no log or print call in the cleaned
repository passes an address or a name (the Epic A hook's rule, as a beta test).

Each mutant puts one leak back, one per file and per shape, and must be
killed by the source guard (NoPiiInAnyLogCallTest). P5 also breaks a line
a behaviour test reads.

One disposable detached worktree at the commit under test. A baseline run
on the unmutated tree must pass first; then each mutant, with the file
restored from the commit's blob and sha256-checked after it. BROKEN (a
load failure) is never counted as a kill.

Rule 17: every run has PYTHONDONTWRITEBYTECODE=1, and the __pycache__
directories of the mutated modules' packages are deleted before the
baseline, before each mutant and after each restore, so a stale .pyc
(same size, same mtime second) can never stand in for the mutant.

    python docs/evidence/h91-ids-only-logs-everywhere/run_mutants.py <commit>
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
WORKTREE = os.path.join(os.path.dirname(REPO), "Grade-Automator-Plus-h91-mut")
TEST_DB = "test_h91_mut"

TESTS = [
    "billing.tests.test_logs_carry_no_email",
    "billing.tests.test_price_drift_reconciliation",
]

MUTANTS = [
    (
        "P1",
        "billing/access_control.py: a user by id",
        "billing/access_control.py",
        "            user.id,\n",
        "            user.email,\n",
        1,
    ),
    (
        "P2",
        "billing/services.py: a user by id",
        "billing/services.py",
        "            user.id,\n",
        "            user.email,\n",
        1,
    ),
    (
        "P3",
        "billing/stripe_service.py: a subscription's user by id",
        "billing/stripe_service.py",
        "            user_sub.user_id,\n",
        "            user_sub.user.email,\n",
        1,
    ),
    (
        "P4",
        "billing/views.py: the requesting user by id",
        "billing/views.py",
        "                request.user.id,\n",
        "                request.user.email,\n",
        1,
    ),
    (
        "P5",
        "billing/tasks.py: a subscription's user by id",
        "billing/tasks.py",
        "                sub.user_id,\n",
        "                sub.user.email,\n",
        1,
    ),
    (
        "P6",
        "the rule covers a name, not only an address",
        "billing/tasks.py",
        "                sub.user_id,\n",
        "                sub.user.first_name,\n",
        1,
    ),
    (
        "P7",
        "the rule covers print()",
        "billing/qa_time_travel.py",
        '    return {"test_clock": clock_id}\n',
        '    print(clock_id, email.first_name)\n    return {"test_clock": clock_id}\n',
        1,
    ),
    (
        "P8",
        "the rule covers a keyword argument and any receiver",
        "billing/qa_time_travel.py",
        '    return {"test_clock": clock_id}\n',
        '    audit.log(20, "clock", extra={"who": clock.email})\n'
        '    return {"test_clock": clock_id}\n',
        1,
    ),
    (
        "P9",
        "the price-drift line names the user by id (read by a behaviour test)",
        "billing/tasks.py",
        "            sub.id,\n            sub.user_id,\n",
        "            sub.id,\n            sub.user.email,\n",
        1,
    ),
    (
        "Q1",
        "users/mailerlite_service.py: the user by id",
        "users/mailerlite_service.py",
        "                user.id,\n",
        "                user.email,\n",
        1,
    ),
    (
        "Q2",
        "classrooms/services/roster_import.py: a failed row by position, not by name",
        "classrooms/services/roster_import.py",
        "                position,\n                course.id,\n",
        "                row.first_name,\n                row.last_name,\n",
        1,
    ),
    (
        "Q3",
        "assignments/tasks.py: the print names the submission, not the student",
        "assignments/tasks.py",
        'print(f"Starting grading of Submission {submission.id}")',
        'print(f"Starting grading of Submission {submission.student.get_full_name}")',
        1,
    ),
    (
        "Q4",
        "scripts/: a one-off script's print names the user by id",
        "scripts/one_off_backfill_stripe_schedules.py",
        'print(f"FAILED: {user_sub.id} (user {user_sub.user_id}): {exc}")',
        'print(f"FAILED: {user_sub.id} (user {user_sub.user.email}): {exc}")',
        1,
    ),
    (
        "Q5",
        "classrooms/serializers.py: the admin by id",
        "classrooms/serializers.py",
        "            user.id,\n        )\n        return\n",
        "            user.email,\n        )\n        return\n",
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
