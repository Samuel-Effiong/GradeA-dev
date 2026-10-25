# flake8: noqa: E501,B950
"""H-227 refusals logged at WARNING without a traceback: deliberate breaks, and the tests that must catch each.

Each mutant is applied, its test modules run, the failing tests are recorded,
the file is restored. Judged (rule 18): SURVIVED = the inner run exits 0;
KILLED = non-zero exit, the run's own "Ran" line, no module that failed to
load, EVERY test named in EXPECTED among the failing tests, and (rule 22)
every FAIL/ERROR block of each expected test holding the fragment written for
it in EXPECTED. Any other failing test is recorded as
also_failed_not_expected, never hidden; BROKEN = anything else. EXPECTED was
written before any run, traced against every assertion of the two modules
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

HERE = pathlib.Path("docs/evidence/h227-refusal-log-level")
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))

EX = "users/exceptions.py"
TESTS = [
    "users.tests_h227_refusal_logging",
    "users.tests_exception_handler",
]
K = "keepdb"

REFUSAL_LOG = (
    "        logger.warning(\n"
    '            "API request refused: %s (%s)",\n'
    "            type(exc).__name__,\n"
    "            drf_response.status_code,\n"
    "            extra=extra,\n"
    "        )\n"
)
CRASH_LOG = '    logger.error("API Exception", exc_info=exc, extra=extra)\n'

# id, what is broken, file, text(s) replaced, new text(s), tests, database kind
MUTANTS = [
    (
        "P1",
        "the refusal back at ERROR",
        EX,
        REFUSAL_LOG,
        REFUSAL_LOG.replace("logger.warning(", "logger.error("),
        TESTS,
        K,
    ),
    (
        "P2",
        "the refusal keeps its traceback",
        EX,
        REFUSAL_LOG,
        REFUSAL_LOG.replace(
            "            extra=extra,\n",
            "            exc_info=exc,\n            extra=extra,\n",
        ),
        TESTS,
        K,
    ),
    (
        "P3",
        "the refusal's line carries its text",
        EX,
        REFUSAL_LOG,
        REFUSAL_LOG.replace('(%s)",', '(%s): %s",').replace(
            "            drf_response.status_code,\n",
            "            drf_response.status_code,\n            exc,\n",
        ),
        TESTS,
        K,
    ),
    (
        "P4",
        "the crash logged at WARNING",
        EX,
        CRASH_LOG,
        '    logger.warning("API Exception", exc_info=exc, extra=extra)\n',
        TESTS,
        K,
    ),
    (
        "P5",
        "the crash logged without its traceback",
        EX,
        CRASH_LOG,
        '    logger.error("API Exception", extra=extra)\n',
        TESTS,
        K,
    ),
    (
        "P6",
        "the refusal not logged at all",
        EX,
        REFUSAL_LOG,
        "",
        TESTS,
        K,
    ),
]

# tests_h227_refusal_logging
_R1 = "test_a_refusal_is_logged_at_warning_without_a_traceback"
_R2 = "test_a_refusal_log_carries_none_of_the_refusals_text"
_R3 = "test_a_refusal_writes_no_error_record"
_R4 = "test_a_refused_code_request_logs_no_error_and_answers_as_before"
_R5 = "test_a_refused_door_logs_no_error_and_keeps_its_audit_row"
_K1 = "test_a_crash_keeps_its_error_record_with_the_traceback"
_K2 = "test_a_throttle_keeps_its_answer"
# tests_exception_handler (changed by this row)
_EH_DRF = "test_a_drf_exception_keeps_its_status_and_is_marked_handled"
_EH_VALID = "test_validation_errors_keep_their_field_payload"
_EH_500 = "test_an_unhandled_exception_becomes_a_500_carrying_the_original"
_EH_BODY = "test_the_unhandled_response_body_carries_no_exception_text"
_EH_CONTEXT = "test_every_exception_is_logged_with_request_context"
_EH_HANDLED = "test_a_handled_exception_is_logged_at_warning_without_a_traceback"

LEVEL = "'ERROR' != 'WARNING'"
UNEXPECTED = "Unexpected logs found"
TRACEBACK = "is not None"
TEXT = "unexpectedly found in"
NO_WARNING = "no logs of level WARNING or higher triggered on users.exceptions"
NO_ERROR = "no logs of level ERROR or higher triggered on users.exceptions"

#: Written before any run: {mutant: {test: the fragment its every block holds}},
#: every test reckoned to fail under it, traced against every assertion of the
#: two modules. A WARNING-level capture also catches an ERROR record, so the
#: helpers and K2 are blind to the refusal's level (P1); only the level and
#: no-ERROR checks see it.
EXPECTED = {
    # The ERROR line has no text and no traceback, so R2 passes.
    "P1": {
        _R1: LEVEL,
        _R3: UNEXPECTED,
        _R4: UNEXPECTED,
        _R5: UNEXPECTED,
        _EH_HANDLED: LEVEL,
    },
    # The traceback's last line is the ValidationError's field dict (R2).
    "P2": {_R1: TRACEBACK, _R2: TEXT, _EH_HANDLED: TRACEBACK},
    # R1 passes its level and traceback checks first, then fails the message.
    "P3": {_R1: "!= 'API request refused: NotFound (404)'", _R2: TEXT},
    "P4": {_K1: NO_ERROR, _EH_500: NO_ERROR, _EH_BODY: NO_ERROR, _EH_CONTEXT: NO_ERROR},
    "P5": {_K1: "unexpectedly None", _EH_CONTEXT: "unexpectedly None"},
    "P6": {
        _R1: NO_WARNING,
        _R2: NO_WARNING,
        _K2: NO_WARNING,
        _EH_DRF: NO_WARNING,
        _EH_VALID: NO_WARNING,
        _EH_HANDLED: NO_WARNING,
    },
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
