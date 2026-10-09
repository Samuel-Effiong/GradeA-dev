"""
Mutation battery for the CodedError serialization slice (rule 15).

One mutant per guard, applied in ONE disposable detached worktree at the
commit under test; the serialization module and S6a's suite run against
it, and the file is restored from the commit's blob and sha256-checked
before the next. BROKEN (a load failure) is never counted as a kill.

    python docs/evidence/epic-a-codederror/run_mutants.py <commit>
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
WORKTREE = os.path.join(os.path.dirname(REPO), "Grade-Automator-Plus-codederror-mut")
TEST_DB = "test_codederror_mut"

RC = "AutoGrader/reason_codes.py"
TESTS = ["AutoGrader.tests_codederror_serialization", "AutoGrader.tests_reason_codes"]
ARGS = "        self.args = (code.value, params, None, display or None)"

# Each mutant: its id, the guard it breaks, the file, the text replaced,
# the replacement, and which occurrence of the text.
MUTANTS = [
    (
        "P1",
        "display travels in args",
        RC,
        ARGS,
        "        self.args = (code.value, params, None, None)",
        1,
    ),
    (
        "P2",
        "args are in constructor order",
        RC,
        ARGS,
        "        self.args = (params, code.value, None, display or None)",
        1,
    ),
    (
        "P3",
        "the constructor takes args positionally",
        RC,
        "    def __init__(self, reason_code=None, params=None, detail=None, display=None):",
        "    def __init__(self, reason_code=None, *, params=None, detail=None, display=None):",
        1,
    ),
    ("P4", "args are replaced (not the message)", RC, ARGS + "\n", "", 1),
    (
        "P5",
        "str() is the rendered message",
        RC,
        "    def __str__(self):\n        return self._message\n",
        "",
        1,
    ),
    (
        "P6",
        "the log-only detail stays out of args",
        RC,
        ARGS,
        "        self.args = (code.value, params, detail, display or None)",
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
