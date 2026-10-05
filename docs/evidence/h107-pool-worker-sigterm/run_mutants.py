"""
Mutation battery for H-107 (rule 15). W mutants: a parallel test worker
starts with SIGTERM's default action, so a terminated worker dies instead
of recording the signal as a test error and living on. P mutants: the
run's result stream waits when a write would block instead of raising.

One disposable detached worktree at the commit under test. A baseline run
on the unmutated tree must pass first; then each mutant, with the file
restored from the commit's blob and sha256-checked after it. BROKEN (a
load failure) is never counted as a kill.

Rule 17: every run has PYTHONDONTWRITEBYTECODE=1, and the __pycache__
directories of the mutated modules' packages are deleted before the
baseline, before each mutant and after each restore, so a stale .pyc
(same size, same mtime second) can never stand in for the mutant.

    python docs/evidence/h107-pool-worker-sigterm/run_mutants.py <commit>
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
WORKTREE = os.path.join(os.path.dirname(REPO), "Grade-Automator-Plus-h107-mut")
TEST_DB = "test_h107_mut"

RT = "AutoGrader/redis_test_runner.py"
PS = "AutoGrader/testing/patient_stream.py"
TESTS = [
    "AutoGrader.tests_pool_worker_sigterm",
    "AutoGrader.tests_patient_test_stream",
]

MUTANTS = [
    (
        "W1",
        "every worker gets the default action back (the pool initializer)",
        RT,
        "    _die_on_sigterm()\n    return ParallelTestSuite.init_worker(*args, **kwargs)\n",
        "    return ParallelTestSuite.init_worker(*args, **kwargs)\n",
        1,
    ),
    (
        "W2",
        "our suite starts its workers through our initializer",
        RT,
        "    init_worker = _init_worker\n",
        "",
        1,
    ),
    (
        "W3",
        "a spawned worker's setup gives the default action back too",
        RT,
        "    _die_on_sigterm()\n    isolate_beat_locks_per_test()\n",
        "    isolate_beat_locks_per_test()\n",
        1,
    ),
    (
        "W4",
        "the action is the default one (die), not ignore",
        RT,
        "    signal.signal(signal.SIGTERM, signal.SIG_DFL)\n",
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n",
        1,
    ),
    # Finding B: the result stream waits when a write would block.
    (
        "P1",
        "a write that would block waits and carries on (no retry: it raises)",
        PS,
        "            except BlockingIOError:\n                self._wait(fd)\n                continue\n",
        "            except BlockingIOError:\n                raise\n",
        1,
    ),
    (
        "P2",
        "it carries on from the byte it stopped at (nothing twice)",
        PS,
        "            data = data[written:]\n",
        "            data = data[len(data) if written else 0 :]\n",
        1,
    ),
    (
        "P3",
        "what the stream itself still holds goes out first",
        PS,
        "        # What the stream itself still holds goes out first.\n        self.flush()\n",
        "",
        1,
    ),
    (
        "P4",
        "a flush that would block waits too",
        PS,
        "            except BlockingIOError:\n"
        "                if fd is None:\n"
        "                    raise\n"
        "                self._wait(fd)\n",
        "            except BlockingIOError:\n                raise\n",
        1,
    ),
    (
        "P5",
        "a reader that takes nothing ends the wait (no bound: a new hang)",
        PS,
        "        if not writable:\n",
        "        if False:\n",
        1,
    ),
    (
        "P6",
        "the default patience is 120 seconds",
        PS,
        "PATIENCE = 120\n",
        "PATIENCE = 12000\n",
        1,
    ),
    (
        "P7",
        "the runner reports on the patient stream",
        RT,
        '        kwargs["stream"] = PatientStream(sys.stderr)\n',
        "",
        1,
    ),
    (
        "P8",
        "a stream with no file descriptor is written to as it is",
        PS,
        "        if fd is None:\n            return self.stream.write(text)\n",
        "",
        1,
    ),
    (
        "P9",
        "the stream wraps sys.stderr as it is when the run is set up (1a's Y10)",
        RT,
        '        kwargs["stream"] = PatientStream(sys.stderr)\n',
        '        kwargs["stream"] = PatientStream(sys.__stderr__)\n',
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
            loaded = not load_failed(output)
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
