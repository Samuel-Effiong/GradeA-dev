"""
Mutation battery for H-180 (rule 15): an upload the teacher's wallet cannot
pay for is refused at the door of the three upload-async routes, by the
same estimate method the billing gate asks; a student reads one fixed
sentence on the refusal and on the polled status of a task the gate refused.

One mutant per condition and per route. Each is run against the row's own
test module (the existing modules of the three routes are in the chain's
modules step, not here).

One disposable detached worktree at the commit under test. A baseline run
on the unmutated tree must pass first; then each mutant, with the file
restored from the commit's blob and sha256-checked after it. BROKEN (a
load failure) is never counted as a kill.

Rule 17: every run has PYTHONDONTWRITEBYTECODE=1, and the __pycache__
directories of the mutated modules' packages are deleted before the
baseline, before each mutant and after each restore.

Rule 18: each test run writes its stdout and stderr straight to a file,
with stdin from the null device; nothing is read through a pipe. A kill
needs the run's own "Ran" line, named failing tests and no load failure.

These tests use the database, and the runs pass --keepdb: the database
test_h180_mut is left on the local server afterwards.

    python docs/evidence/h180-student-upload-credit-door/run_mutants.py <commit>
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
WORKTREE = os.path.join(os.path.dirname(REPO), "Grade-Automator-Plus-h180-mut")
TEST_DB = "test_h180_mut"

SVW = "students/views.py"
AVW = "assignments/views.py"
DOOR = "assignments/upload_door.py"
TASKS = "assignments/tasks.py"
AIS = "ai_processor/services.py"
ERR = "AutoGrader/error_messages.py"
TESTS = ["students.tests_upload_credit_door"]

#: (id, what it guards, file, old, new, occurrence, labels)
MUTANTS = [
    (
        "M1",
        "the student route asks the door",
        SVW,
        "        refusal = upload_refusal_if_unaffordable(\n"
        "            request.user, assignment, [uploaded_file], prompt\n"
        "        )\n",
        "        refusal = None\n",
        1,
        TESTS,
    ),
    (
        "M2",
        "the teacher batch route asks the door",
        SVW,
        "        refusal = upload_refusal_if_unaffordable(\n"
        "            request.user, assignment, files, prompt\n"
        "        )\n",
        "        refusal = None\n",
        1,
        TESTS,
    ),
    (
        "M3",
        "the assignment upload route asks the door",
        AVW,
        "        refusal = upload_refusal_if_unaffordable(\n"
        "            request.user,\n"
        "            None,\n"
        "            [f for f in files if isinstance(f, UploadedFile)],\n"
        "            prompt_text,\n"
        "        )\n",
        "        refusal = None\n",
        1,
        TESTS,
    ),
    (
        "M4",
        "a balance equal to the estimate is enough",
        DOOR,
        "estimate is None or balance >= estimate",
        "estimate is None or balance > estimate",
        1,
        TESTS,
    ),
    (
        "M5",
        "the door asks the shared estimate method",
        DOOR,
        "        return ai_processor.estimate_messages_cost(\n"
        '            None, None, [{"role": "user", "content": content}]\n'
        "        )\n",
        "        return 20000\n",
        1,
        TESTS,
    ),
    (
        "M6",
        "the gate asks the shared estimate method",
        AIS,
        "        estimated_cost = self.estimate_messages_cost(\n"
        "            user_prompt, system_prompt, messages\n"
        "        )\n",
        '        estimated_cost = self.estimate_total_token("", [], [])\n',
        1,
        TESTS,
    ),
    (
        "M7",
        "a student at the door reads the fixed sentence",
        DOOR,
        "            message = STUDENT_UPLOAD_NOT_PROCESSED\n",
        "            message = INSUFFICIENT_CREDITS_MESSAGE\n",
        1,
        TESTS,
    ),
    (
        "M8",
        "a teacher at the door reads the usual credit message",
        DOOR,
        "            message = INSUFFICIENT_CREDITS_MESSAGE\n",
        "            message = STUDENT_UPLOAD_NOT_PROCESSED\n",
        1,
        TESTS,
    ),
    (
        "M9",
        "the file is rewound after the door read it",
        DOOR,
        "        uploaded_file.seek(0)\n",
        "        pass\n",
        1,
        TESTS,
    ),
    (
        "M10",
        "a file the door cannot read is left to the task",
        DOOR,
        "        return None\n    finally:\n",
        "        raise\n    finally:\n",
        1,
        TESTS,
    ),
    (
        "M11",
        "a teacher without a wallet is left to the permission",
        DOOR,
        "    if wallet is None:\n        return None\n",
        "",
        1,
        TESTS,
    ),
    (
        "M12",
        "a super admin is never refused",
        DOOR,
        "    if request_user.user_type == UserTypes.SUPER_ADMIN:\n        return None\n",
        "",
        1,
        TESTS,
    ),
    (
        "M13",
        "the task shows a student the fixed sentence",
        TASKS,
        '            getattr(user, "user_type", None) == UserTypes.STUDENT\n',
        "            False\n",
        1,
        TESTS,
    ),
    (
        "M14",
        "the task shows the fixed sentence to a student only",
        TASKS,
        '            getattr(user, "user_type", None) == UserTypes.STUDENT\n',
        "            True\n",
        1,
        TESTS,
    ),
    (
        "M15",
        "the error text of a student's refusal is passed through",
        ERR,
        "    if isinstance(error, StudentUploadNotProcessedError):\n"
        "        return str(error)\n",
        "",
        1,
        TESTS,
    ),
    (
        "M16",
        "a student's upload is billed to the course's teacher",
        DOOR,
        '        return getattr(course, "teacher", None)\n',
        "        return request_user\n",
        1,
        TESTS,
    ),
    (
        "M17",
        "the shared method counts a pdf",
        AIS,
        '                            pdf_bytes.append(item.get("bytes"))\n',
        "                            pass\n",
        1,
        TESTS,
    ),
    (
        "M18",
        "the shared method counts the system prompt",
        AIS,
        "        if system_prompt:\n            if isinstance(system_prompt, str):\n"
        "                total_prompt += system_prompt\n",
        "        if False:\n            if isinstance(system_prompt, str):\n"
        "                total_prompt += system_prompt\n",
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
