"""
Mutation battery for H-130 (rule 15): a student must not learn of a grade
before the teacher releases it. Part A: the course final grade a student
reads counts released work only. Part B: the answer document a student
reads says nothing of an unreleased grade.

One mutant per condition the two fixes add. Each names the test labels it
is run against.

One disposable detached worktree at the commit under test. A baseline run
on the unmutated tree must pass first; then each mutant, with the file
restored from the commit's blob and sha256-checked after it. BROKEN (a
load failure) is never counted as a kill.

Rule 17: every run has PYTHONDONTWRITEBYTECODE=1, and the __pycache__
directories of the mutated modules' packages are deleted before the
baseline, before each mutant and after each restore, so a stale .pyc
(same size, same mtime second) can never stand in for the mutant.

Rule 18: each test run writes its stdout and stderr straight to a file,
with stdin from the null device; nothing is read through a pipe. A kill
needs the run's own "Ran" line, named failing tests and no load failure.

These tests use the database, and the runs pass --keepdb: the database
test_h130_mut is left on the local server afterwards.

    python docs/evidence/h130-student-final-grade-released-only/run_mutants.py <commit>
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
WORKTREE = os.path.join(os.path.dirname(REPO), "Grade-Automator-Plus-h130-mut")
TEST_DB = "test_h130_mut"

FG = "classrooms/final_grade.py"
CS = "classrooms/serializers.py"
SV = "students/services.py"
SS = "students/serializers.py"
AS = "assignments/serializers.py"
GRADE = [
    "classrooms.tests_student_final_grade_released_only",
    "classrooms.tests_final_grade_zero_score",
]
DOCUMENT = ["students.tests_answer_document_before_release"]
TESTS = GRADE + DOCUMENT

STAFF = '        return getattr(user, "user_type", None) == UserTypes.TEACHER\n'
WHICH = "    if submission.is_published or not submission.raw_input:\n"
UNGRADED_SCORE = (
    '        score = StudentSubmission._meta.get_field("score").get_default()\n'
)

#: (id, what it guards, file, old, new, occurrence, labels)
MUTANTS = [
    (
        "A1",
        "the student's figure leaves out unreleased work",
        FG,
        "        and submission.is_published\n",
        "",
        1,
        GRADE,
    ),
    (
        "A2",
        "a student is not read as staff",
        CS,
        STAFF,
        "        return True\n",
        1,
        GRADE,
    ),
    (
        "A3",
        "the letter comes from the reader's figure, not the stored one",
        CS,
        "        grade = self._final_grade_for_reader(obj)\n",
        "        grade = obj.final_grade\n",
        1,
        GRADE,
    ),
    (
        "A4",
        "the number comes from the reader's figure, not the stored one",
        CS,
        "        if not self._reader_is_staff():\n",
        "        if False:\n",
        1,
        GRADE,
    ),
    (
        "A5",
        "released work with no stored maximum is weighted by the assignment's points",
        FG,
        "                submission.max_points\n"
        "                if submission.max_points is not None\n"
        "                else submission.assignment.total_points\n",
        "                submission.max_points\n",
        1,
        GRADE,
    ),
    (
        "A6",
        "another course's work is not counted",
        FG,
        "        if submission.assignment.course_id == course_id\n"
        "        and submission.is_published\n",
        "        if submission.is_published\n",
        1,
        GRADE,
    ),
    (
        "A7",
        "a row with no grading time is left out, as in the stored figure",
        FG,
        "        and submission.graded_at is not None\n",
        "",
        1,
        GRADE,
    ),
    (
        "A8",
        "a teacher still reads the stored figure",
        CS,
        STAFF,
        "        return False\n",
        1,
        GRADE,
    ),
    (
        "A9",
        "a reader that cannot be identified is not read as staff",
        CS,
        STAFF,
        '        return getattr(user, "user_type", None) != UserTypes.STUDENT\n',
        1,
        GRADE,
    ),
    (
        "B1",
        "after release the student reads the stored document",
        SV,
        WHICH,
        "    if not submission.raw_input:\n",
        1,
        DOCUMENT,
    ),
    (
        "B2",
        "a row with no stored document is served as it is",
        SV,
        WHICH,
        "    if submission.is_published:\n",
        1,
        DOCUMENT,
    ),
    (
        "B3",
        "before release the student does not read the stored document (the defect itself)",
        SV,
        WHICH,
        "    if True:\n",
        1,
        DOCUMENT,
    ),
    (
        "B4",
        "the ungraded form hides the grading date",
        SV,
        "        graded_at = None\n",
        "        graded_at = submission.graded_at\n",
        1,
        DOCUMENT,
    ),
    (
        "B5",
        "the ungraded form hides the row's score",
        SV,
        UNGRADED_SCORE,
        "        score = submission.score\n",
        1,
        DOCUMENT,
    ),
    (
        "B6",
        "the ungraded form prints the score as a new row's, not as 'Not graded yet'",
        SV,
        UNGRADED_SCORE,
        "        score = None\n",
        1,
        DOCUMENT,
    ),
    (
        "B7",
        "the student's submission serializer goes through the helper",
        SS,
        "        return answer_document_for_student(obj)\n",
        "        return obj.raw_input\n",
        1,
        DOCUMENT,
    ),
    (
        "B8",
        "the student's assignment serializer goes through the helper",
        AS,
        "            return answer_document_for_student(submission)\n",
        "            return submission.raw_input\n",
        1,
        DOCUMENT,
    ),
    (
        "B9",
        "the student's submission serializer does not carry the stored column",
        SS,
        "    # Not the stored column: before release it carries the grade (H-130).\n"
        "    raw_input = serializers.SerializerMethodField()\n",
        "",
        1,
        DOCUMENT,
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
