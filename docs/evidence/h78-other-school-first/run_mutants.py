"""
Mutation battery for H-78 (rule 15): _get_or_invite_teacher refuses another
school's teacher before it looks at their subscriptions, and neither the
not-business nor the individual-subscription refusal logs the address.

One disposable detached worktree at the commit under test; the file is
restored from the commit's blob and sha256-checked after each mutant.
BROKEN (a load failure) is never counted as a kill.

    python docs/evidence/h78-other-school-first/run_mutants.py <commit>
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
WORKTREE = os.path.join(os.path.dirname(REPO), "Grade-Automator-Plus-h78-mut")
TEST_DB = "test_h78_mut"

LS = "billing/license_service.py"
TESTS = ["billing.tests.test_other_school_before_subscription"]

# The two blocks of _get_or_invite_teacher at the fix; O1 puts them back in
# the pre-fix order.
SCHOOL = (
    "            # 3. School validation. Before the subscription check (H-78): another\n"
    "            # school's teacher's billing status is not this admin's to learn.\n"
    "            if user.school and user.school != school:\n"
    "                # Generic on purpose: naming the other school told any school\n"
    "                # admin which school an arbitrary address belongs to (a\n"
    "                # cross-tenant disclosure). The log carries ids only.\n"
    '                error_msg = "This teacher already belongs to another school."\n'
    "                logger.warning(\n"
    '                    "Teacher %s belongs to school %s, not %s: not enrolled.",\n'
    "                    user.id,\n"
    "                    user.school_id,\n"
    "                    school.id,\n"
    "                )\n"
    "                if raise_on_conflict:\n"
    "                    raise ValueError(error_msg)\n"
    "                return None\n"
    "\n"
)
SUBSCRIPTION = (
    "            # 4. Check for active individual subscription\n"
    "            has_individual_sub = user.subscriptions.filter(is_active=True).exists()\n"
    "\n"
    "            if has_individual_sub:\n"
    "                error_msg = (\n"
    '                    f"Teacher {email} has an active individual subscription. "\n'
    '                    "Individual subscriptions cannot be converted to a license. "\n'
    '                    "Please cancel the individual subscription first."\n'
    "                )\n"
    "                logger.warning(\n"
    '                    "Teacher %s has an individual subscription: not enrolled.",\n'
    "                    user.id,\n"
    "                )\n"
    "                if raise_on_conflict:\n"
    "                    raise IndividualSubscriptionConflictError(error_msg)\n"
    "                return None\n"
    "\n"
)

MUTANTS = [
    (
        "O1",
        "the school check comes before the subscription check",
        LS,
        SCHOOL + SUBSCRIPTION,
        SUBSCRIPTION + SCHOOL,
        1,
    ),
    (
        "O2",
        "another school's teacher is refused",
        LS,
        "            if user.school and user.school != school:\n",
        "            if False:\n",
        1,
    ),
    (
        "L1",
        "the Skipped-enrolling line logs the class, not the text",
        LS,
        "                type(exc).__name__,\n",
        "                exc,\n",
        1,
    ),
    (
        "L2",
        "the subscription refusal logs the id, not its message",
        LS,
        "                logger.warning(\n"
        '                    "Teacher %s has an individual subscription: not enrolled.",\n'
        "                    user.id,\n"
        "                )\n",
        "                logger.warning(error_msg)\n",
        1,
    ),
    (
        "L3",
        "the not-business refusal logs no address",
        LS,
        '            logger.warning("Not a business email: teacher not enrolled.")\n',
        "            logger.warning(error_msg)\n",
        1,
    ),
    (
        "L4",
        "the enrolment re-check's refusal logs the id, not its message (1a's R1)",
        LS,
        "            logger.warning(\n"
        '                "Teacher %s has an individual subscription: not enrolled.",\n'
        "                teacher.id,\n"
        "            )\n",
        "            logger.warning(error_msg)\n",
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
