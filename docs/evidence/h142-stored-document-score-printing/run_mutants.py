"""
Mutation battery for H-139, H-140 and H-142 (rule 15): the score printed
in the stored answer document. A graded row prints its score with two
decimals on every path, and a teacher's manual grade rebuilds the stored
document.

One mutant per condition the fix adds. Each is run against the row's own
test module.

One disposable detached worktree at the commit under test. A baseline run
on the unmutated tree must pass first; then each mutant, with the file
restored from the commit's blob and sha256-checked after it. BROKEN (a
load failure) is never counted as a kill.

Rule 17: every run has PYTHONDONTWRITEBYTECODE=1, and the __pycache__
directories of the mutated modules' packages are deleted before the
baseline, before each mutant and after each restore, so a stale .pyc
(same size, same mtime second) can never stand in for the mutant.

Rule 18: each test run writes its stdout and stderr straight to a file
of its own (logs/raw/<mutant>.out), with stdin from the null device;
nothing is read through a pipe. A kill needs the run's own "Ran" line,
named failing tests and no load failure.

These tests use the database, and the runs pass --keepdb: the database
test_h142_mut is left on the local server afterwards.

    python docs/evidence/h142-stored-document-score-printing/run_mutants.py <commit>
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
WORKTREE = os.path.join(os.path.dirname(REPO), "Grade-Automator-Plus-h142-mut")
TEST_DB = "test_h142_mut"

SV = "students/services.py"
VW = "students/views.py"
TESTS = ["students.tests_answer_document_score_printing"]

GRADED = "        if graded_at and score is not None:\n"
TWO = '            score = format(Decimal(str(score)), ".2f")\n'
TRY_BUILD = (
    "        try:\n"
    "            submission.raw_input = AssignmentProcessingService.html_to_prosemirror_text(\n"
    "                student_submission_to_html(submission)\n"
    "            )\n"
)
THE_GUARD = (
    "        except Exception as exc:  # noqa: BLE001 - see the comment above\n"
    "            logger.error(\n"
    '                "Manual grade: the answer document could not be rebuilt: "\n'
    '                "submission=%s error=%s",\n'
    "                submission.pk,\n"
    "                type(exc).__name__,\n"
    "            )\n"
)
THE_SAVE = (
    "        submission.save(\n"
    "            update_fields=[\n"
    '                "score",\n'
    '                "score_percentage",\n'
    '                "max_points",\n'
    '                "feedback",\n'
    '                "was_regraded",\n'
    '                "regraded_at",\n'
    '                "needs_review",\n'
    '                "review_reasons",\n'
    '                "raw_input",\n'
    "            ]\n"
    "        )\n"
)
GAP = "\n        # Update the formatted grade since the score/feedback changed\n\n"
GUARD_TESTS = ["students.tests_manual_grade_unreadable_answers"]

#: (id, what it guards, file, old, new, occurrence, labels)
MUTANTS = [
    (
        "M1",
        "the manual-grade route rebuilds the document (the defect itself)",
        VW,
        TRY_BUILD,
        "        try:\n            pass\n",
        1,
        TESTS,
    ),
    (
        "M2",
        "the rebuilt document is saved, not only held in memory",
        VW,
        '                "review_reasons",\n                "raw_input",\n            ]\n',
        '                "review_reasons",\n            ]\n',
        1,
        TESTS,
    ),
    (
        "M3",
        "a graded row's score is printed in one form on every path",
        SV,
        TWO,
        "            score = score\n",
        1,
        TESTS,
    ),
    (
        "M4",
        "a row with no grading time prints what it printed",
        SV,
        GRADED,
        "        if score is not None:\n",
        1,
        TESTS,
    ),
    (
        "M5",
        "a grading time with no score still says 'Not graded yet'",
        SV,
        GRADED,
        "        if graded_at:\n",
        1,
        TESTS,
    ),
    (
        "M6",
        "two decimals, not one",
        SV,
        TWO,
        '            score = format(Decimal(str(score)), ".1f")\n',
        1,
        TESTS,
    ),
    (
        "M7",
        "a genuine zero is printed",
        SV,
        GRADED,
        "        if graded_at and score:\n",
        1,
        TESTS,
    ),
    (
        "M8",
        "the route rebuilds the graded document, not the ungraded form",
        VW,
        TRY_BUILD,
        TRY_BUILD.replace(
            "student_submission_to_html(submission)",
            "student_submission_to_html(submission, show_grade=False)",
        ),
        1,
        TESTS,
    ),
    (
        "M9",
        "the guard: a builder fault does not stop the grade being saved",
        VW,
        TRY_BUILD + THE_GUARD,
        "        submission.raw_input = AssignmentProcessingService.html_to_prosemirror_text(\n"
        "            student_submission_to_html(submission)\n"
        "        )\n",
        1,
        GUARD_TESTS,
    ),
    (
        "M10",
        "the guard is around the build only, never the save",
        VW,
        TRY_BUILD + THE_GUARD + GAP + THE_SAVE,
        TRY_BUILD
        + GAP.replace("        #", "            #")
        + THE_SAVE.replace("\n        ", "\n            ", 0).replace(
            "        submission.save", "            submission.save", 1
        )
        + THE_GUARD,
        1,
        GUARD_TESTS,
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


def run_tests(log_path, labels):
    """One test run, output straight to `log_path` (rule 18)."""
    with open(log_path, "w") as log, open(os.devnull) as nothing:
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
        code, output = run_tests(os.path.join(raw, "baseline.out"), TESTS)
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
            code, output = run_tests(os.path.join(raw, f"{mid}.out"), labels)
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
                    f"# run against: {' '.join(labels)}\n"
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
