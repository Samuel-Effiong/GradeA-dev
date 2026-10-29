"""
Mutation battery for H-191 (rule 15): the HTML sanitizer builds its own bleach
Cleaner for each call, so threads never share a parser.

Two mutants: the module-level Cleaner restored (the defect itself), and a
per-call Cleaner built with a WEAKENED allowlist (an event-handler attribute
allowed on an image: the fail-open direction). Each is run against the row's
own test module. The first is a RACE: its tests fail because threads really
interleave inside the shared parser (the test sets a tiny switch interval),
and the observed failure rate over repeated runs is stated in the evidence.

One disposable detached worktree at the commit under test. A baseline run
on the unmutated tree must pass first; then each mutant, with the file
restored from the commit's blob and sha256-checked after it. BROKEN (a
load failure) is never counted as a kill.

Rule 17: PYTHONDONTWRITEBYTECODE=1 and the __pycache__ of the mutated
packages is deleted before the baseline, before each mutant and after each
restore. Rule 18: every test run writes straight to a file, stdin from the
null device. A kill needs the run's own "Ran" line, named failing tests and
no load failure. The tests are pure Python; the runs still pass --keepdb.

    python docs/evidence/h191-sanitizer-per-call-cleaner/run_mutants.py <commit>
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
WORKTREE = os.path.join(os.path.dirname(REPO), "Grade-Automator-Plus-h191-mut")
TEST_DB = "test_h191_mut"

PMC = "assignments/prosemirror_converter.py"
TESTS = ["assignments.tests_sanitizer_threads"]

THE_FUNCTION = (
    "def sanitize_editor_html(html_string: str) -> str:\n"
    '    """Strip control characters and everything outside the converter allowlist."""\n'
    "    return _new_cleaner().clean(\n"
    "        strip_raw_text_elements(strip_control_chars(html_string))\n"
    "    )\n"
)

#: (id, what it guards, file, old, new, occurrence, labels)
MUTANTS = [
    (
        "S1",
        "each call has its own Cleaner (the module-level instance restored)",
        PMC,
        THE_FUNCTION,
        "_SHARED = _new_cleaner()\n\n\n"
        + THE_FUNCTION.replace("_new_cleaner().clean(", "_SHARED.clean("),
        1,
        TESTS,
    ),
    (
        "S2",
        "the per-call Cleaner keeps the allowlist (an onerror attribute is not allowed)",
        PMC,
        "        attributes=CONVERTER_ALLOWED_ATTRIBUTES,\n",
        '        attributes={"img": ["src", "onerror"]},\n',
        1,
        TESTS,
    ),
]

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


#: A mutant that restores the shared Cleaner can wedge the test process (seen:
#: 11 minutes at 178% CPU, H-191). Each inner run is killed after this many
#: seconds; a run killed that way counts as KILLED, the reason is logged
#: ("timed_out: True" in the mutant's log), and the failing set of such a run
#: is not compared (it may be partial).
INNER_TIMEOUT_SECONDS = 400


def run_tests(log_path, labels):
    """One test run, output straight to `log_path` (rule 18). Returns
    (exit code, output, timed_out)."""
    timed_out = False
    with open(log_path, "w") as log, open(os.devnull) as nothing:
        try:
            proc = subprocess.run(
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
                stdin=nothing,
                stdout=log,
                stderr=subprocess.STDOUT,
                env={
                    **os.environ,
                    "EXEMPT_EMAIL_DOMAINS": "",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                timeout=INNER_TIMEOUT_SECONDS,
            )
            code = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            code = 124
    with open(log_path, errors="replace") as log:
        return code, log.read(), timed_out


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
        code, output, _ = run_tests(os.path.join(raw, "baseline.out"), TESTS)
        with open(os.path.join(logs, "baseline.log"), "w") as fh:
            fh.write(f"# baseline, commit {commit}, exit {code}\n\n")
            fh.write(output)
        print(f"baseline exit={code}", flush=True)
        if code != 0:
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
            code, output, timed_out = run_tests(os.path.join(raw, f"{mid}.out"), labels)
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
            elif timed_out:
                status = "KILLED"
                summary = f"TIMEOUT after {INNER_TIMEOUT_SECONDS} s (counted killed)"
            elif ran.startswith("Ran ") and failing and loaded:
                status = "KILLED"
            else:
                status = "BROKEN"
            with open(os.path.join(logs, f"{mid}.log"), "w") as fh:
                fh.write(
                    f"# {mid}: {guard}\n# file: {rel} (occurrence {nth})\n"
                    f"# run against: {' '.join(labels)}\n"
                    f"# old: {old!r}\n# new: {new!r}\n# commit: {commit}\n"
                    f"# exit: {code}\n# timed_out: {timed_out}\n# elapsed_s: {elapsed:.1f}\n"
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
