# flake8: noqa: E501,B950
"""H-219 no error text or traceback in assignments/tasks.py's last six sites: deliberate breaks, and the tests that must catch each.

Each mutant is applied, its test modules run, the failing tests are recorded,
the file is restored. Judged (rule 18): SURVIVED = the inner run exits 0;
KILLED = non-zero exit, the run's own "Ran" line, no module that failed to
load, EVERY test named in EXPECTED among the failing tests, and (rule 22)
every FAIL/ERROR block of each expected test holding the fragment written for
it in EXPECTED. Any other failing test is recorded as
also_failed_not_expected, never hidden; BROKEN = anything else. EXPECTED was
written before any run, traced against every assertion of the three modules
(controls too).

Rule 17: PYTHONDONTWRITEBYTECODE=1 on every inner run; the mutated file's
__pycache__ is deleted before each mutant and after each restore.
Rule 18: each inner run writes straight to its own file, stdin from the null
device, nothing piped. Each mutant is restored in a `finally` block and
SIGTERM is turned into an ordinary exit; the gate script's EXIT trap restores
the files from the commit.
"""

import ast
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys

HERE = pathlib.Path("docs/evidence/h219-task-error-text")
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))

TK = "assignments/tasks.py"
TESTS = [
    "assignments.tests_h219_task_error_text",
    "assignments.tests_prerender",
    "assignments.tests_task_results_carry_no_text",
]
K = "keepdb"

STORED = "            error=describe_background_task_error(e, fallback_message),\n"
BATCH = (
    '            "Failed to clear scheduling info or create session: assignment=%s %s",\n'
    "            assignment_id,\n"
    "            describe_error_for_log(e),\n"
)
TEACHER = (
    "                logger.error(\n"
    '                    "Failed to queue due reminder email for teacher: %s",\n'
    "                    describe_error_for_log(exc),\n"
)
STUDENT = (
    "                logger.error(\n"
    '                    "Failed to queue due reminder email for student: %s",\n'
    "                    describe_error_for_log(exc),\n"
)
NOTICE = (
    "                logger.error(\n"
    '                    "Failed to queue new assignment notification for student: %s",\n'
    "                    describe_error_for_log(exc),\n"
)
PDF = '            logger.error(\n                "[PDF] pre-render failed for assignment %s (%s view): %s",\n'


def traceback_back(text):
    """The same line, logged with logger.exception (the text and traceback)."""
    return text.replace("logger.error(", "logger.exception(", 1)


# id, what is broken, file, text(s) replaced, new text(s), tests, database kind
MUTANTS = [
    (
        "W1",
        "the upload's stored error back to str(e)",
        TK,
        STORED,
        "            error=str(e),\n",
        TESTS,
        K,
    ),
    (
        "W2",
        "the batch line carries the text again",
        TK,
        BATCH,
        BATCH.replace("describe_error_for_log(e)", "e"),
        TESTS,
        K,
    ),
    (
        "W3",
        "the teacher reminder logged with its traceback",
        TK,
        TEACHER,
        traceback_back(TEACHER),
        TESTS,
        K,
    ),
    (
        "W4",
        "the student reminder logged with its traceback",
        TK,
        STUDENT,
        traceback_back(STUDENT),
        TESTS,
        K,
    ),
    (
        "W5",
        "the new-assignment notice logged with its traceback",
        TK,
        NOTICE,
        traceback_back(NOTICE),
        TESTS,
        K,
    ),
    (
        "W6",
        "the pre-render failure logged with its traceback",
        TK,
        PDF,
        traceback_back(PDF),
        TESTS,
        K,
    ),
    (
        "W7",
        "the teacher reminder line loses the error's class",
        TK,
        TEACHER,
        TEACHER.replace("describe_error_for_log(exc)", '"an error"'),
        TESTS,
        K,
    ),
]

# tests_h219_task_error_text
_S1 = "test_a_failed_upload_stores_the_tracked_rows_sentence_not_the_text"
_S2 = "test_a_failed_batch_session_is_logged_by_class_and_id"
_S3 = "test_a_failed_teacher_reminder_is_logged_by_class_only"
_S4 = "test_a_failed_student_reminder_is_logged_by_class_only"
_S5 = "test_a_failed_new_assignment_notice_is_logged_by_class_only"
_S6 = "test_a_failed_pre_render_is_logged_by_class_only"

TEXT = "unexpectedly found in"
TRACEBACK = "is not None"

#: Written before any run: {mutant: {test: the fragment its every block holds}},
#: every test reckoned to fail under it, traced against every assertion of the
#: three modules. tests_prerender's failure test asserts only the result, and
#: tests_task_results_carry_no_text reaches only the tasks' outer handlers
#: (H-209), so neither sees any of these sites.
EXPECTED = {
    "W1": {_S1: TEXT},
    "W2": {_S2: TEXT},
    # assert_class_only checks exc_info first.
    "W3": {_S3: TRACEBACK},
    "W4": {_S4: TRACEBACK},
    "W5": {_S5: TRACEBACK},
    "W6": {_S6: TRACEBACK},
    # exc_info, the text and the level pass; the class check fails.
    "W7": {_S3: "'RuntimeError' not found in"},
}

#: A test module that could not be loaded.
LOAD_FAILURE = "unittest.loader._FailedTest"


def clear_pycache(path):
    shutil.rmtree(pathlib.Path(path).parent / "__pycache__", ignore_errors=True)


def blocks_of(text, name):
    blocks = re.split(r"^={70}$", text, flags=re.M)
    return [
        b
        for b in blocks
        if re.match(r"\s*(FAIL|ERROR): " + re.escape(name) + r"[ (]", b)
    ]


def wrong_reasons(mid, text):
    """Expected tests with a block that does not hold its written fragment."""
    return sorted(
        name
        for name, fragment in EXPECTED[mid].items()
        if not all(fragment in b for b in blocks_of(text, name))
    )


def judge(mid, returncode, text):
    """(status, the run's "Ran" line, the failing tests' names, wrong reasons)."""
    failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", text, re.M)))
    ran = re.findall(r"^Ran \d+ tests? in .*$", text, re.M)
    ran_line = ran[-1] if ran else None
    wrong = wrong_reasons(mid, text)
    if returncode == 0:
        status = "SURVIVED"
    elif (
        ran_line
        and LOAD_FAILURE not in text
        and set(EXPECTED[mid]) <= set(failed)
        and not wrong
    ):
        status = "KILLED"
    else:
        status = "BROKEN"
    return status, ran_line, failed, wrong


def edits(old, new):
    """The (old, new) pairs of one mutant: one pair, or several."""
    if isinstance(old, str):
        return [(old, new)]
    assert len(old) == len(new)
    return list(zip(old, new, strict=True))


def broken(source, old, new):
    for one_old, one_new in edits(old, new):
        source = source.replace(one_old, one_new, 1)
    return source


def _exit_on_sigterm(signum, _frame):
    raise SystemExit(128 + signum)


def main():
    signal.signal(signal.SIGTERM, _exit_on_sigterm)
    assert [m[0] for m in MUTANTS] == list(EXPECTED), "EXPECTED names every mutant"
    originals = {}
    for mid, _what, path, old, new, _tests, kind in MUTANTS:
        source = originals.setdefault(path, open(path).read())
        for one_old, _one_new in edits(old, new):
            found = source.count(one_old)
            assert found == 1, f"{mid}: anchor found {found} times"
        assert kind in ("keepdb", "fresh"), mid
        assert broken(source, old, new) != source, f"{mid}: changes nothing"
        ast.parse(broken(source, old, new))
    if "--check" in sys.argv:
        print(len(MUTANTS), "mutants: every anchor found once, all parse")
        print(" ".join(m[0] for m in MUTANTS))
        return
    LOGS.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    results = {}
    for mid, what, path, old, new, tests, kind in MUTANTS:
        original = originals[path]
        database = ["--keepdb"] if kind == "keepdb" else []
        try:
            clear_pycache(path)
            open(path, "w").write(broken(original, old, new))
            log = LOGS / f"{mid}.txt"
            with open(log, "w") as out, open(os.devnull) as devnull:
                p = subprocess.run(
                    [sys.executable, "manage.py", "test", *tests]
                    + [f"--settings={SETTINGS}", "--noinput", "--verbosity", "2"]
                    + database,
                    stdin=devnull,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    env=env,
                    timeout=600,
                )
        finally:
            open(path, "w").write(original)
            clear_pycache(path)
        text = log.read_text(errors="replace")
        status, ran_line, failed, wrong = judge(mid, p.returncode, text)
        results[mid] = {
            "what": what,
            "file": path,
            "kind": kind,
            "status": status,
            "exit": p.returncode,
            "ran_line": ran_line,
            "expected": EXPECTED[mid],
            "failing_tests": failed,
            "wrong_reason": wrong,
            "also_failed_not_expected": sorted(set(failed) - set(EXPECTED[mid])),
        }
        print(mid, results[mid], flush=True)
    with open(OUT, "w") as f:
        json.dump(results, f, indent=2)
        f.write("\n")
    for status in ("KILLED", "SURVIVED", "BROKEN"):
        print(f"{status}:", [k for k, v in results.items() if v["status"] == status])


if __name__ == "__main__":
    main()
