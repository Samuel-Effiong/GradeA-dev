"""
Mutation battery for H-165 (rule 15): the answer document builder never
raises on the shape of a submission's stored answers and says in one
fixed line what it left out; both writers refuse a value that is not a
list of objects; a paper with nothing printable is refused for grading
before any paid call and put in the review queue; one with some left out
is graded and flagged; one log line where a document or a grade is stored.

One mutant per condition. Each is run against the row's own test module.
U5 is there to show two controls red (the two cures).

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
test_h165_mut is left on the local server afterwards.

    python docs/evidence/h165-answer-document-unreadable-answers/run_mutants.py <commit>
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
WORKTREE = os.path.join(os.path.dirname(REPO), "Grade-Automator-Plus-h165-mut")
TEST_DB = "test_h165_mut"

SV = "students/services.py"
VW = "students/views.py"
EM = "AutoGrader/error_messages.py"
TESTS = ["students.tests_answers_unreadable"]

BUILDER_LINE = "    if left_out:\n        line = (\n"
REFUSAL = "    if printable_answers(submission.answers)[1] == ALL_LEFT_OUT:\n"
SOURCE_4 = (
    '        tiers.append("critical")\n'
    '        sort_keys.append(_review_sort_key("critical", 1.0))\n'
    '        reasons.append({"type": ANSWERS_UNREADABLE, "left_out": left_out})\n'
)
THE_LOG = "logger.warning(*unreadable_answers_log_line(submission, left_out))\n"

#: (id, what it guards, file, old, new, occurrence, labels)
MUTANTS = [
    (
        "U1",
        "a value that is not a list is refused, not walked",
        SV,
        "    if not isinstance(answers, list):\n        return [], ALL_LEFT_OUT\n",
        "",
        1,
        TESTS,
    ),
    (
        "U2",
        "the fixed line is printed when something was left out",
        SV,
        BUILDER_LINE,
        "    if False:\n        line = (\n",
        1,
        TESTS,
    ),
    (
        "U3",
        "which of the two lines is printed",
        SV,
        "            if left_out == ALL_LEFT_OUT\n",
        "            if left_out != ALL_LEFT_OUT\n",
        1,
        TESTS,
    ),
    (
        "U4",
        "a paper with nothing printable is refused before any paid call",
        SV,
        REFUSAL,
        "    if False:\n",
        1,
        TESTS,
    ),
    (
        "U5",
        "ONLY a paper with nothing printable is refused",
        SV,
        REFUSAL,
        "    if True:\n",
        1,
        TESTS,
    ),
    (
        "U6",
        "the refusal puts the paper in the review queue",
        SV,
        "    row.needs_review = True\n",
        "    row.needs_review = False\n",
        1,
        TESTS,
    ),
    (
        "U7",
        "a second refusal does not add the reason a second time",
        SV,
        "        if not (isinstance(reason, dict) and "
        'reason.get("type") == ANSWERS_UNREADABLE)\n',
        "        if True\n",
        1,
        TESTS,
    ),
    (
        "U8",
        "the refusal's tier is critical",
        SV,
        '    row.review_tier = _worst_tier([row.review_tier, "critical"])\n',
        '    row.review_tier = "moderate"\n',
        1,
        TESTS,
    ),
    (
        "U9",
        "grading a paper with some left out adds the reason",
        SV,
        '    if left_out:\n        tiers.append("critical")\n',
        '    if False:\n        tiers.append("critical")\n',
        1,
        TESTS,
    ),
    (
        "U10",
        "grading such a paper logs one line",
        SV,
        "        " + THE_LOG,
        "",
        1,
        TESTS,
    ),
    (
        "U11",
        "the read that stores a rebuilt document logs one line",
        VW,
        "            if left_out:\n                " + THE_LOG,
        "",
        1,
        TESTS,
    ),
    (
        "U12",
        "the log line carries the kind of value, not the value",
        SV,
        "        type(submission.answers).__name__,\n",
        "        submission.answers,\n",
        1,
        TESTS,
    ),
    (
        "U13",
        "the upload refuses a list that is not all objects",
        SV,
        "        if not is_a_list_of_objects(extracted_answers):\n",
        "        if not isinstance(extracted_answers, list):\n",
        1,
        TESTS,
    ),
    (
        "U14",
        "the edit refuses a list that is not all objects",
        SV,
        "        if not is_a_list_of_objects(answers):\n",
        "        if not isinstance(answers, list):\n",
        1,
        TESTS,
    ),
    (
        "U15",
        "the refusal is an error shown to the teacher as written",
        EM,
        "        SubmissionAnswersUnreadableError,\n        AssignmentNotOpenError,\n",
        "        AssignmentNotOpenError,\n",
        1,
        TESTS,
    ),
    (
        "U16",
        "grading's reason is critical",
        SV,
        SOURCE_4,
        SOURCE_4.replace("critical", "moderate"),
        1,
        TESTS,
    ),
    (
        "U17",
        "an empty value leaves nothing out",
        SV,
        "    if not answers:\n        return [], 0\n",
        "",
        1,
        TESTS,
    ),
    (
        "U18",
        "an ordinary paper's document has no such line",
        SV,
        BUILDER_LINE,
        "    if True:\n        line = (\n",
        1,
        TESTS,
    ),
    (
        "U19",
        "the line is fixed text: nothing of the stored value is printed",
        SV,
        '        questions_html += f"<p><em>{escape(line, quote=False)}</em></p>"\n',
        '        questions_html += f"<p><em>{escape(line, quote=False)} '
        '{submission.answers}</em></p>"\n',
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
