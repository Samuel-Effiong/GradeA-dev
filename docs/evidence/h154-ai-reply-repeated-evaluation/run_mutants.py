"""
Mutation battery for H-154 (rule 15, rule 19): one evaluation per question
goes into the score. An AI grading reply that repeated a question, or
held an evaluation for a question that does not exist, was summed as it
came; the fix keeps one evaluation per question in
AIProcessor._finalize_grading_result, says so in the saved note, keeps
dropped evaluations out of the saved-answer store, flags the paper for
the teacher and keeps the correction from the feedback formatter.

One mutant per condition the fix adds (seventeen). Each is run against
the three test modules named in TESTS.

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
test_h154_mut is left on the local server afterwards.

    python docs/evidence/h154-ai-reply-repeated-evaluation/run_mutants.py <commit>
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
WORKTREE = os.path.join(os.path.dirname(REPO), "Grade-Automator-Plus-h154-mut")
TEST_DB = "test_h154_mut"

AI = "ai_processor/services.py"
SV = "students/services.py"
FP = "students/feedback_projection.py"
TESTS = [
    "ai_processor.tests_reply_repeated_evaluation",
    "ai_processor.tests_grading_arithmetic",
    "students.tests_ai_reply_corrected_review",
]

OUTRANKS = "            if self._outranks(corrected, held[0]):\n"
KEPT_SHORT = (
    "            fresh_evaluations = self._kept_among(fresh_evaluations, finalized)\n"
)
#: The long path's line is the short path's line less four spaces, so its
#: text is found first INSIDE the short path's line: occurrence 2 is its own.
KEPT_LONG = (
    "        fresh_evaluations = self._kept_among(fresh_evaluations, finalized)\n"
)

#: (id, what it guards, file, old, new, occurrence, labels)
MUTANTS = [
    (
        "R1",
        "of a question's repeats the last does not simply win",
        AI,
        OUTRANKS,
        "            if True:\n",
        1,
        TESTS,
    ),
    (
        "R2",
        "of a question's repeats the first does not simply win",
        AI,
        OUTRANKS,
        "            if False:\n",
        1,
        TESTS,
    ),
    (
        "R3",
        "an evaluation that matches no question is dropped",
        AI,
        "            elif points_by_question:\n",
        "            elif False:\n",
        1,
        TESTS,
    ),
    (
        "R4",
        "an evaluation the system held stands against a model's",
        AI,
        "        if candidate_system != held_system:\n",
        "        if False:\n",
        1,
        TESTS,
    ),
    (
        "R5",
        "among a model's repeats the LOWEST score is kept",
        AI,
        '        return candidate["score_awarded"] < held["score_awarded"]\n',
        '        return candidate["score_awarded"] > held["score_awarded"]\n',
        1,
        TESTS,
    ),
    (
        "R6",
        "the saved note does not say PASS when something was dropped",
        AI,
        '            verification["verification_status"] = REPLY_CORRECTED\n',
        "",
        1,
        TESTS,
    ),
    (
        "R7",
        "the long path's saved note carries what its first sum dropped",
        AI,
        '        if earlier.get("verification_status") != REPLY_CORRECTED:\n',
        "        if True:\n",
        1,
        TESTS,
    ),
    (
        "R8",
        "short path: only kept evaluations reach the saved-answer store",
        AI,
        KEPT_SHORT,
        "            pass\n",
        1,
        TESTS,
    ),
    (
        "R9",
        "long path: only kept evaluations reach the saved-answer store",
        AI,
        KEPT_LONG,
        "        pass\n",
        2,
        TESTS,
    ),
    (
        "R10",
        "a corrected reply puts the paper in the teacher's review queue",
        SV,
        "    if isinstance(note, dict) and note.get("
        '"verification_status") == REPLY_CORRECTED:\n',
        "    if False:\n",
        1,
        TESTS,
    ),
    (
        "R11",
        "the formatter is sent a corrected note as arithmetic only",
        FP,
        '    if isinstance(note, dict) and note.get("verification_status") not in (\n',
        '    if False and note.get("verification_status") not in (\n',
        1,
        TESTS,
    ),
    (
        "R12",
        "a warning line is logged for a corrected reply",
        AI,
        '                "[Grading] reply_corrected repeated_questions=%s "\n',
        '                "[Grading] reply repeated_questions=%s "\n',
        1,
        TESTS,
    ),
    (
        "R13",
        "the count of dropped repeats is the count",
        AI,
        "            repeated_dropped[key] = repeated_dropped.get(key, 0) + 1\n",
        "            repeated_dropped[key] = repeated_dropped.get(key, 0) + 0\n",
        1,
        TESTS,
    ),
    (
        "R14",
        "a reply's evaluation cannot say it came from the saved-answer store",
        AI,
        '                evaluation.pop("from_cache", None)\n',
        "",
        1,
        TESTS,
    ),
    (
        "R15",
        "a reply's evaluation cannot name its own grader",
        AI,
        '                evaluation["graded_by"] = model_name or "llm"\n',
        '                evaluation.setdefault("graded_by", model_name or "llm")\n',
        1,
        TESTS,
    ),
    (
        "R16",
        "short path: the reply's evaluations are stamped as a model's",
        AI,
        "            self._stamp_as_a_models(evaluations, model_name)\n",
        "            pass\n",
        1,
        TESTS,
    ),
    (
        "R17",
        "long path: the reply's evaluations are stamped as a model's",
        AI,
        "                self._stamp_as_a_models(evaluations, batch_model)\n",
        "                pass\n",
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
