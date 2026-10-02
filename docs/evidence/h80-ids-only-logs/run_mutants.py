"""
Mutation battery for H-80 / H-86 (rule 15): billing/license_service.py and
users/signals.py log ids, never an address, and the enrolment's refusals
log an ids-only reason line.

Two defences, each isolated. The B mutants run against the behaviour tests
only (the source guard is not loaded), so a kill is a log line caught at
run time. The G mutants put a leak on a path no behaviour test drives and
run against the source guard only.
The P mutants (1a's pre-review P1) run against the tests of the lines they
break: the free-trial reason lines and the renewal-failure tracebacks.

One disposable detached worktree at the commit under test. A baseline run
of both test modules on the unmutated tree must pass first; then each
mutant, with the file restored from the commit's blob and sha256-checked
after it. BROKEN (a load failure) is never counted as a kill.

Rule 17: every run has PYTHONDONTWRITEBYTECODE=1, and the __pycache__
directories of the mutated modules' packages are deleted before the
baseline, before each mutant and after each restore, so a stale .pyc
(same size, same mtime second) can never stand in for the mutant.

    python docs/evidence/h80-ids-only-logs/run_mutants.py <commit>
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
WORKTREE = os.path.join(os.path.dirname(REPO), "Grade-Automator-Plus-h80-mut")
TEST_DB = "test_h80_mut"

LS = "billing/license_service.py"
SG = "users/signals.py"
MODULE = "billing.tests.test_logs_carry_no_email"
BEHAVIOUR = [
    f"{MODULE}.LicencePathsLogNoAddressTest",
    f"{MODULE}.SignalsLogNoAddressTest",
]
GUARD = [f"{MODULE}.NoEmailInLogCallsTest"]
TRIAL = [f"{MODULE}.FreeTrialRefusalsLogAReasonTest"]
RENEWAL = "billing.tests.test_license_renewal_partial_failure"
SV = "billing/services.py"
RENEWAL_FAILURE = (
    "                    allocation.user_id,\n"
    "                    license_sub.id,\n"
    "                    type(e).__name__,\n"
    "                    exc_info=True,\n"
)

CARRY = (
    "                    teacher.id,\n"
    "                    old_license.id,\n"
    "                    new_license.id,\n"
    "                    type(exc).__name__,\n"
)
FAILED = (
    '                "License %s creation: %d/%d teacher invitations FAILED.",\n'
    "                license_sub.id,\n"
    "                len(failed_results),\n"
    "                len(enrollment_results),\n"
)
FAILED_LEAK = (
    '                "License %s creation: %d/%d teacher invitations FAILED: %s",\n'
    "                license_sub.id,\n"
    "                len(failed_results),\n"
    "                len(enrollment_results),\n"
    "                failed_results,\n"
)

# (id, what it guards, file, old, new, occurrence, tests)
MUTANTS = [
    (
        "B1",
        "a carried-forward teacher's refusal names the teacher by id",
        LS,
        CARRY,
        CARRY.replace("teacher.id", "teacher.email"),
        1,
        BEHAVIOUR,
    ),
    (
        "B2",
        "a carried-forward teacher's refusal logs the class, not the text",
        LS,
        CARRY,
        CARRY.replace("type(exc).__name__", "exc"),
        1,
        BEHAVIOUR,
    ),
    (
        "B3",
        "the failed-creation summary does not log failed_results",
        LS,
        FAILED,
        FAILED_LEAK,
        1,
        BEHAVIOUR,
    ),
    (
        "B4",
        "a skipped enrolment logs the refusal's class, not its text",
        LS,
        "                school.id,\n                type(exc).__name__,\n            )\n",
        "                school.id,\n                exc,\n            )\n",
        1,
        BEHAVIOUR,
    ),
    (
        "B5",
        "the queued invitation line names the teacher by id",
        LS,
        "                    teacher_id,\n                    school_name,\n",
        "                    teacher_email,\n                    school_name,\n",
        1,
        BEHAVIOUR,
    ),
    (
        "B6",
        "the enrolment line names the teacher by id",
        LS,
        '                "Enrolled teacher %s in license %s with allocation %d credits",\n'
        "                teacher.id,\n",
        '                "Enrolled teacher %s in license %s with allocation %d credits",\n'
        "                teacher.email,\n",
        1,
        BEHAVIOUR,
    ),
    (
        "B7",
        "H-86: the seat-limit refusal logs a WARNING reason line",
        LS,
        "                logger.warning(\n"
        '                    "License %s is at its seat limit of %s: teacher %s not "\n',
        "                logger.debug(\n"
        '                    "License %s is at its seat limit of %s: teacher %s not "\n',
        1,
        BEHAVIOUR,
    ),
    (
        "B8",
        "H-86: the seat-limit reason line names the teacher by id",
        LS,
        "                    license_sub.max_seats,\n                    teacher.id,\n",
        "                    license_sub.max_seats,\n                    teacher.email,\n",
        1,
        BEHAVIOUR,
    ),
    (
        "B9",
        "H-86: the enrolment's other-school refusal logs a WARNING reason line",
        LS,
        "            logger.warning(\n"
        '                "Teacher %s belongs to school %s, not licence %s\'s school %s: "\n',
        "            logger.debug(\n"
        '                "Teacher %s belongs to school %s, not licence %s\'s school %s: "\n',
        1,
        BEHAVIOUR,
    ),
    (
        "B10",
        "the new-user signal line names the user by id",
        SG,
        '        "Post-save signal fired for new user %s (type: %s).",\n        user.id,\n',
        '        "Post-save signal fired for new user %s (type: %s).",\n        user.email,\n',
        1,
        BEHAVIOUR,
    ),
    (
        "B11",
        "the trial-check signal line names the user by id",
        SG,
        'logger.debug("Checking if user %s needs trial activation", user.id)',
        'logger.debug("Checking if user %s needs trial activation", user.email)',
        1,
        BEHAVIOUR,
    ),
    (
        "P1",
        "1a's P1: a used trial's refusal logs a WARNING reason line",
        SV,
        "            logger.warning(\n"
        '                "User %s has already used the free trial: not activated.", user.id\n',
        "            logger.debug(\n"
        '                "User %s has already used the free trial: not activated.", user.id\n',
        1,
        TRIAL,
    ),
    (
        "P2",
        "1a's P1: a missing trial plan is logged at ERROR",
        SV,
        "            logger.error(\n"
        '                "Free trial plan not found: no trial activated for user %s.",\n',
        "            logger.warning(\n"
        '                "Free trial plan not found: no trial activated for user %s.",\n',
        1,
        TRIAL,
    ),
    (
        "P3",
        "1a's P1: the missing-plan reason line names the user by id",
        SV,
        '                "Free trial plan not found: no trial activated for user %s.",\n'
        "                user.id,\n",
        '                "Free trial plan not found: no trial activated for user %s.",\n'
        "                user.email,\n",
        1,
        TRIAL,
    ),
    (
        "P4",
        "1a's P1: the Stripe renewal-failure line carries a traceback",
        LS,
        RENEWAL_FAILURE,
        RENEWAL_FAILURE.replace("                    exc_info=True,\n", ""),
        1,
        [RENEWAL],
    ),
    (
        "P5",
        "1a's P1: the offline renewal-failure line carries a traceback",
        LS,
        RENEWAL_FAILURE,
        RENEWAL_FAILURE.replace("                    exc_info=True,\n", ""),
        2,
        [RENEWAL],
    ),
    (
        "G6",
        "guard (1a's P2): an address formatted into the message with %",
        LS,
        'logger.info("Created CreditWallet for teacher %s", teacher.id)',
        'logger.info("Created CreditWallet for teacher %s" % teacher.email)',
        1,
        GUARD,
    ),
    (
        "G7",
        "guard (1a's P2): a message built by an f-string, even from an id",
        LS,
        'logger.info("Created CreditWallet for teacher %s", teacher.id)',
        'logger.info(f"Created CreditWallet for teacher {teacher.id}")',
        1,
        GUARD,
    ),
    (
        "G1",
        "guard: an `.email` in a logger call on an undriven path",
        LS,
        '            performed_by.id if performed_by else "unknown",\n',
        '            performed_by.email if performed_by else "unknown",\n',
        1,
        GUARD,
    ),
    (
        "G2",
        "guard: an exception's text in a logger call (license_service)",
        LS,
        "                    type(e).__name__,\n",
        "                    str(e),\n",
        1,
        GUARD,
    ),
    (
        "G3",
        "guard: an exception's text in a logger call (signals)",
        SG,
        "            type(exc).__name__,\n",
        "            exc,\n",
        1,
        GUARD,
    ),
    (
        "G4",
        "guard: failed_results in a logger call",
        LS,
        FAILED,
        FAILED_LEAK,
        1,
        GUARD,
    ),
    (
        "G5",
        "guard: a name holding an address in a logger call",
        LS,
        "                    teacher_id,\n                    school_name,\n",
        "                    teacher_email,\n                    school_name,\n",
        2,
        GUARD,
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


def run_tests(labels):
    return subprocess.run(
        [
            sys.executable,
            "manage.py",
            "test",
            *labels,
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
        baseline = run_tests([MODULE, RENEWAL])
        with open(os.path.join(logs, "baseline.log"), "w") as fh:
            fh.write(f"# baseline, commit {commit}, exit {baseline.returncode}\n\n")
            fh.write(baseline.stdout + baseline.stderr)
        print(f"baseline exit={baseline.returncode}", flush=True)
        if baseline.returncode != 0:
            raise SystemExit("baseline is red: no mutant is run")
        rows = []
        for mid, guard, rel, old, new, nth, labels in selected:
            path = os.path.join(WORKTREE, rel)
            pristine = sh("git", "show", f"{commit}:{rel}", cwd=REPO).stdout.encode()
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(replace_nth(text, old, new, nth))
            clear_pycache(WORKTREE)
            started = time.monotonic()
            proc = run_tests(labels)
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
                    f"# tests: {' '.join(labels)}\n"
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
