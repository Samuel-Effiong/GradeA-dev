"""
Mutation battery for H-98 (rule 15): at a licence's renewal, an unserved
monthly refresh in the cycle's last week is reported when it is exactly a
point of the allocation's stored anchor and came due at least a day before
the end; everything else stays as it was.

One disposable detached worktree at the commit under test. A baseline run
on the unmutated tree must pass first; then each mutant, with the file
restored from the commit's blob and sha256-checked after it. BROKEN (a
load failure) is never counted as a kill.

Rule 17: every run has PYTHONDONTWRITEBYTECODE=1, and the __pycache__
directories of the mutated modules' packages are deleted before the
baseline, before each mutant and after each restore, so a stale .pyc
(same size, same mtime second) can never stand in for the mutant.

Rule 18: each test run writes its stdout and stderr straight to a file,
with stdin from the null device; nothing is read through a pipe. (The
first battery, at 8513ae0d, collected them through a pipe that the runner
read; it was run again in this form.)

    python docs/evidence/h98-owed-refresh-last-week/run_mutants.py <commit>
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
WORKTREE = os.path.join(os.path.dirname(REPO), "Grade-Automator-Plus-h98-mut")
TEST_DB = "test_h98_mut"

RF = "billing/refresh_timing.py"
LS = "billing/license_service.py"
SV = "billing/services.py"
TESTS = [
    "billing.tests.test_allocation_anchor",
]

MUTANTS = [
    (
        "R1",
        "only a STORED anchor is trusted in the last week",
        RF,
        "        on_stored_anchor\n        and next_due + OWED_MARGIN <= until\n",
        "        next_due + OWED_MARGIN <= until\n",
        1,
    ),
    (
        "R2",
        "the due time is exactly a point of the anchor, not near one",
        RF,
        "        and is_anchor_point(anchor, next_due)\n",
        "",
        1,
    ),
    (
        "R3",
        "it lies a full day before the end (any time before the end counts)",
        RF,
        "        and next_due + OWED_MARGIN <= until\n",
        "        and next_due < until\n",
        1,
    ),
    (
        "R4",
        "exactly one full day is enough (the boundary)",
        RF,
        "        and next_due + OWED_MARGIN <= until\n",
        "        and next_due + OWED_MARGIN < until\n",
        1,
    ),
    (
        "R5",
        "a chain served to the end owes nothing (the margin removed)",
        RF,
        "        and next_due + OWED_MARGIN <= until\n",
        "        and True\n",
        1,
    ),
    (
        "R6",
        "the margin is one day",
        RF,
        "OWED_MARGIN = timedelta(days=1)\n",
        "OWED_MARGIN = timedelta(days=2)\n",
        1,
    ),
    (
        "R7",
        "is_anchor_point is equality with a point",
        RF,
        "    return anchor + relativedelta(months=k) == at\n",
        "    return anchor + relativedelta(months=k) >= at\n",
        1,
    ),
    (
        "C1",
        "the renewal says 'stored' only when the stored anchor is the one in use",
        LS,
        "                on_stored_anchor=stored is not None and anchor == stored,\n",
        "                on_stored_anchor=stored is not None,\n",
        1,
    ),
    (
        "C2",
        "a row with no stored anchor does not claim one",
        LS,
        "                on_stored_anchor=stored is not None and anchor == stored,\n",
        "                on_stored_anchor=True,\n",
        1,
    ),
    (
        "C3",
        "the renewal passes the claim at all",
        LS,
        "                on_stored_anchor=stored is not None and anchor == stored,\n",
        "",
        1,
    ),
    (
        "C4",
        "the individual-plan caller claims no stored anchor",
        SV,
        "                min(user_subscription.billing_cycle_end, timezone.now()),\n            )\n",
        "                min(user_subscription.billing_cycle_end, timezone.now()),\n"
        "                on_stored_anchor=True,\n            )\n",
        1,
    ),
]

#: A test module that could not be loaded, or a traceback that ends in one
#: of these. Matched on whole lines of the run's output: a failure message
#: that only mentions ImportError (Y9's test is about an `except
#: ImportError`) is a kill, not a load failure.
LOAD_FAILURE = "unittest.loader._FailedTest"
LOAD_ERRORS = ("ImportError", "ModuleNotFoundError", "SyntaxError")


def load_failed(output):
    return LOAD_FAILURE in output or any(
        line.startswith(LOAD_ERRORS) for line in output.splitlines()
    )


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


def run_tests(log_path):
    """One test run, output straight to `log_path` (rule 18)."""
    with open(log_path, "w") as log, open(os.devnull) as nothing:
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
            stdin=nothing,
            stdout=log,
            stderr=subprocess.STDOUT,
            env={
                **os.environ,
                "EXEMPT_EMAIL_DOMAINS": "",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
    with open(log_path, errors="replace") as log:
        return proc.returncode, log.read()


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
        raw = os.path.join(logs, "raw")
        os.makedirs(raw, exist_ok=True)
        code, output = run_tests(os.path.join(raw, "baseline.out"))
        with open(os.path.join(logs, "baseline.log"), "w") as fh:
            fh.write(f"# baseline, commit {commit}, exit {code}\n\n")
            fh.write(output)
        print(f"baseline exit={code}", flush=True)
        if code != 0:
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
            code, output = run_tests(os.path.join(raw, f"{mid}.out"))
            elapsed = time.monotonic() - started
            sh("git", "checkout", "--", rel, cwd=WORKTREE)
            clear_pycache(WORKTREE)
            with open(path, "rb") as fh:
                restored = sha256(fh.read()) == sha256(pristine)
            summary = next(
                (
                    ln
                    for ln in reversed(output.splitlines())
                    if ln.startswith(("FAILED", "OK"))
                ),
                "NO SUMMARY",
            )
            ran = next(
                (ln for ln in reversed(output.splitlines()) if ln.startswith("Ran ")),
                "NO RAN LINE",
            )
            loaded = not load_failed(output)
            failing = [
                ln for ln in output.splitlines() if ln.startswith(("FAIL:", "ERROR:"))
            ]
            if code == 0:
                status = "SURVIVED"
            elif ran.startswith("Ran ") and failing and loaded:
                status = "KILLED"
            else:
                status = "BROKEN"
            with open(os.path.join(logs, f"{mid}.log"), "w") as fh:
                fh.write(
                    f"# {mid}: {guard}\n# file: {rel} (occurrence {nth})\n"
                    f"# old: {old!r}\n# new: {new!r}\n# commit: {commit}\n"
                    f"# exit: {code}\n# elapsed_s: {elapsed:.1f}\n"
                    f"# restored_sha256_matches_commit_blob: {restored}\n\n"
                )
                fh.write("\n".join(failing) + f"\n\n{ran}\n{summary}\n")
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
