"""
Mutation battery for Epic A S6d (rule 15).

One mutant per guard, applied in ONE disposable detached worktree at the
commit under test; the S6d test modules run against it, and the file is
restored from the commit's blob and sha256-checked before the next.
BROKEN (a load failure) is never counted as a kill.

    python docs/evidence/epic-a-s6d/run_mutants.py <commit>
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
WORKTREE = os.path.join(os.path.dirname(REPO), "Grade-Automator-Plus-s6d-mut")
TEST_DB = "test_s6d_mut"

SS = "students/services.py"
SV = "students/views.py"
GG = "students/grading_gates.py"
AV = "assignments/views.py"
AT = "assignments/tasks.py"
AX = "ai_processor/exceptions.py"
AS = "ai_processor/services.py"
TESTS = [
    "students.tests_s6d_rubric_gate",
    "students.tests_s6d_submission_empty",
    "students.tests_s6d_provider_failure",
    "students.tests_s6d_extraction_refund",
    "students.tests_async_edit_path",
    "assignments.tests_grading_audit_events",
]
GATE = "        ensure_gradable(submission.assignment)\n"
ALL_GATE = "        ensure_gradable(assignment)\n"
UPLOAD_SCOPE = (
    "    with billing_refund_scope(\n"
    '        reason="answer upload failed before the submission was persisted"\n'
    "    ):\n"
)

# Each mutant: its id, the guard it breaks, the file, the text replaced,
# the replacement, and which occurrence of the text.
MUTANTS = [
    (
        "S01",
        "grade_engine refuses before the claim",
        SS,
        "    ensure_gradable(submission.assignment)\n",
        "",
        1,
    ),
    (
        "S02",
        "a model answer is a marking guide",
        GG,
        "    return isinstance(model_answer, str) and bool(model_answer.strip())",
        "    return False",
        1,
    ),
    (
        "S03",
        "ANY question without a guide is missing",
        GG,
        "    return not all(_has_marking_guide(question) for question in questions)",
        "    return not any(_has_marking_guide(question) for question in questions)",
        1,
    ),
    (
        "S04",
        "a one-level rubric is a guide",
        GG,
        "    if isinstance(rubric, (list, tuple)) and any(",
        "    if isinstance(rubric, (list, tuple)) and len(rubric) >= 2 and any(",
        1,
    ),
    ("S05", "grade-async refuses before queuing", SV, GATE, "", 1),
    ("S06", "schedule-grade-async refuses before scheduling", SV, GATE, "", 2),
    ("S07", "grade-all refuses before queuing", AV, ALL_GATE, "", 1),
    ("S08", "schedule-grade-all refuses before scheduling", AV, ALL_GATE, "", 2),
    (
        "S09",
        "a scheduled batch re-checks at run time",
        AT,
        "    if assignment is not None and rubric_missing(assignment.questions):",
        "    if False:",
        1,
    ),
    (
        "S10",
        "auto-grade re-checks at run time",
        AT,
        "        if rubric_missing(assignment.questions):",
        "        if False:",
        1,
    ),
    (
        "S11",
        "empty text is SUBMISSION_EMPTY at the route",
        SV,
        "        if not str(raw_input).strip():",
        "        if False:",
        1,
    ),
    (
        "S12",
        "the service refuses empty text as SUBMISSION_EMPTY",
        SS,
        '        raise SubmissionEmptyError(params={"file_name": SUBMITTED_TEXT})',
        '        raise ValueError("There is no text to extract answers from.")',
        1,
    ),
    (
        "S13",
        "the credit clause says refunded when a charge is in scope",
        AX,
        "REFUNDED if charges_in_open_scope() else NOT_CHARGED",
        "NOT_CHARGED",
        1,
    ),
    (
        "S14",
        "the grading failure keeps its cause",
        AS,
        "raise provider_failure(last_error, max_retries) from last_error",
        "raise provider_failure(last_error, max_retries) from None",
        2,
    ),
    (
        "S15",
        "an upload's chunk charges are refunded (F1)",
        SS,
        UPLOAD_SCOPE,
        "    if True:\n",
        1,
    ),
    (
        "S16",
        "a non-infra provider failure is MODEL",
        AT,
        "            and classify_infra_error(exc.__cause__) is None",
        "            and False",
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
