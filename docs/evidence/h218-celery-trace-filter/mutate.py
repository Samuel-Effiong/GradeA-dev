# flake8: noqa: E501,B950
"""H-218 Celery's own task lines carry no error text, traceback or arguments: deliberate breaks, and the tests that must catch each.

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

HERE = pathlib.Path("docs/evidence/h218-celery-trace-filter")
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))

SL = "AutoGrader/safe_logging.py"
CE = "AutoGrader/celery.py"
TESTS = [
    "AutoGrader.tests_h218_celery_trace_filter",
    "students.tests_broker_text_not_logged",
    "assignments.tests_task_results_carry_no_text",
]
K = "keepdb"

INSTALL = "    _trace_logger.addFilter(CeleryTraceFilter())\n"
TRACE_LOGGER = '_trace_logger = logging.getLogger("celery.app.trace")\n'
DESCRIBE = "                exc = describe_error_for_log(error)\n"
KEPT = '_CELERY_TRACE_KEPT = ("hostname", "id", "name", "description", "internal")\n'
EXC_INFO = "        record.exc_info = None\n"
RETRY = "            exc = RETRY_TEXT_OMITTED\n"
KEEP_LINE = "        record.exc_text = None\n        return True\n"
NEVER_RAISE = (
    "            try:\n"
    "                exc = describe_error_for_log(error)\n"
    "            except Exception:\n"
    '                exc = f"error={type(error).__name__}"\n'
)

# id, what is broken, file, text(s) replaced, new text(s), tests, database kind
MUTANTS = [
    ("X1", "the filter is not installed", CE, INSTALL, "    pass\n", TESTS, K),
    (
        "X2",
        "the error's repr is kept",
        SL,
        DESCRIBE,
        '                exc = context["exc"]\n',
        TESTS,
        K,
    ),
    ("X3", "exc_info (the traceback) is kept", SL, EXC_INFO, "", TESTS, K),
    (
        "X4",
        "the traceback text is kept in the dict",
        SL,
        KEPT,
        KEPT.replace('"internal")', '"internal", "traceback")'),
        TESTS,
        K,
    ),
    (
        "X5",
        "the task's arguments are kept in the dict",
        SL,
        KEPT,
        KEPT.replace('"internal")', '"internal", "args", "kwargs")'),
        TESTS,
        K,
    ),
    (
        "X6",
        "installed on the parent logger celery",
        CE,
        TRACE_LOGGER,
        '_trace_logger = logging.getLogger("celery")\n',
        TESTS,
        K,
    ),
    (
        "X7",
        "the retry line keeps its text",
        SL,
        RETRY,
        '            exc = context["exc"]\n',
        TESTS,
        K,
    ),
    (
        "X8",
        "the filter drops the line",
        SL,
        KEEP_LINE,
        "        record.exc_text = None\n        return False\n",
        TESTS,
        K,
    ),
    (
        "X9",
        "a describe failure is not caught (the never-raise rule)",
        SL,
        NEVER_RAISE,
        "            exc = describe_error_for_log(error)\n",
        TESTS,
        K,
    ),
]

# tests_h218_celery_trace_filter
_T1 = "test_f1_celerys_own_failure_log_for_a_failed_grade_all_carries_no_text"
_T2 = "test_the_whole_record_holds_no_text_no_traceback_and_no_arguments"
_T3 = "test_a_retry_line_holds_no_text"
_T4 = "test_the_failure_line_keeps_its_level_name_id_and_the_errors_class"
_T5 = "test_a_describe_failure_still_strips_and_keeps_the_line"

F1 = "the made-up error text reached celery's own failure log"
TEXT = "unexpectedly found in"
CLASS = "'error=RuntimeError' not found in"
UNPACK = "not enough values to unpack"

#: Written before any run: {mutant: {test: the fragment its every block holds}},
#: every test reckoned to fail under it, traced against every assertion of the
#: three modules. The two neighbours do not read celery.app.trace records
#: (H-208's broker lines; H-209's capture reads only the project's own
#: loggers), so no mutant reaches them. K1 (another logger) and K2 (a success
#: line, whose dict has no `exc`) pass under every mutant.
EXPECTED = {
    # No filter: as at the base.
    "X1": {_T1: F1, _T2: TEXT, _T3: TEXT, _T4: CLASS, _T5: TEXT},
    # The repr in the line; the retry branch is untouched (T3 passes).
    "X2": {_T1: F1, _T2: TEXT, _T4: CLASS, _T5: TEXT},
    # The line reads by class (T4 passes); exc_info holds the error. T5's text
    # check passes, its exc_info check fails.
    "X3": {_T1: F1, _T2: TEXT, _T5: "is not None"},
    # Only the whole record holds the traceback text (its last line is the
    # error's text, MARKER), the first check of T2.
    "X4": {_T2: TEXT},
    # Only the whole record holds the arguments: MARKER is not in them, so T2's
    # second check (PROMPT_MARKER) fails, with the same fragment.
    "X5": {_T2: TEXT},
    # A parent logger's filter does not see a child's records: as X1.
    "X6": {_T1: F1, _T2: TEXT, _T3: TEXT, _T4: CLASS, _T5: TEXT},
    # Only the retry lines (no exc_info) change.
    "X7": {_T3: TEXT},
    # Every trace line with `exc` disappears: T1 has no records at all, the
    # unpacks of exactly one failure find none, T3 counts 0 retry lines.
    "X8": {
        _T1: "premise: Celery logged the failure",
        _T2: UNPACK,
        _T3: "0 != 3",
        _T4: UNPACK,
        _T5: UNPACK,
    },
    # The patched describe's ValueError comes out of the eager tracer
    # (celery/app/trace.py: `if eager: raise`), so T5 is an ERROR holding it.
    "X9": {_T5: "h218-describe-broke"},
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
