# flake8: noqa: E501,B950
"""H-215a automatic grading resilience: deliberate breaks, and the tests that must catch each.

Each mutant is applied, its test modules run, the failing tests are recorded,
the file is restored. Judged (rule 18): SURVIVED = the inner run exits 0;
KILLED = non-zero exit, the run's own "Ran" line, no module that failed to
load, and EVERY test named in EXPECTED among the failing tests (any other
failing test is recorded as also_failed_not_expected, never hidden); BROKEN =
anything else. EXPECTED was written before any run, as a list of every test
reckoned to fail under the mutant, each traced against every assertion of the
two modules (controls too).

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

HERE = pathlib.Path("docs/evidence/h215a-auto-grade-resilience")
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))

SV = "students/services.py"
TK = "assignments/tasks.py"
TESTS = [
    "students.tests_h215a_auto_grade_resilience",
    "assignments.tests_task_results_carry_no_text",
]
K = "keepdb"
AUTO_FLAG = "            session,\n            only_if_ungraded=True,\n            first_index=first_index,\n"
BATCH_FLAG = "            ),\n            only_if_ungraded=True,\n        )\n"

# id, what is broken, file, text replaced, new text, tests, database kind
MUTANTS = [
    (
        "G1",
        "the claim ignores only_if_ungraded",
        SV,
        "        candidates = candidates.filter(graded_at__isnull=True)\n",
        "        pass\n",
        TESTS,
        K,
    ),
    (
        "G2",
        "the automatic grading dispatches without the flag",
        TK,
        AUTO_FLAG,
        AUTO_FLAG.replace("=True", "=False"),
        TESTS,
        K,
    ),
    (
        "G3",
        "a paper graded meanwhile is taken for one being graded (no skip of its own)",
        SV,
        "            raise SubmissionGradedMeanwhileError(\n",
        "            raise SubmissionGradingInProgressError(\n",
        TESTS,
        K,
    ),
    (
        "G4",
        "every exception is retried",
        TK,
        "    except AUTO_GRADE_TRANSIENT_ERRORS as exc:\n",
        "    except Exception as exc:\n",
        TESTS,
        K,
    ),
    (
        "G5",
        "the transient tuple loses ProcessingTemporarilyUnavailable",
        TK,
        "    ProcessingTemporarilyUnavailable,\n    *BROKER_UNAVAILABLE_ERRORS,\n",
        "    *BROKER_UNAVAILABLE_ERRORS,\n",
        TESTS,
        K,
    ),
    (
        "G6",
        "the countdown does not double",
        TK,
        "    return min(60 * 2**retries, 900) + random.randint(0, 30)",
        "    return min(60 * (retries + 1), 900) + random.randint(0, 30)",
        TESTS,
        K,
    ),
    (
        "G7",
        "max_retries 50",
        TK,
        "            max_retries=AUTO_GRADE_MAX_RETRIES,\n",
        "            max_retries=50,\n",
        TESTS,
        K,
    ),
    (
        "G8",
        "the retry drops the batch row's id",
        TK,
        '                "session_id": str(session.id) if session else session_id,\n',
        '                "session_id": None,\n',
        TESTS,
        K,
    ),
    (
        "G9",
        "the resend ignores the celery id (sends every ungraded paper again)",
        TK,
        "    return ungraded_submissions.exclude(id__in=reached)\n",
        "    return ungraded_submissions\n",
        TESTS,
        K,
    ),
    (
        "G10",
        "the teacher's scheduled batch dispatches without the flag",
        TK,
        BATCH_FLAG,
        BATCH_FLAG.replace("=True", "=False"),
        TESTS,
        K,
    ),
]

_A1 = "test_an_auto_paper_graded_by_the_teacher_meanwhile_is_skipped"
_A2 = "test_two_deliveries_of_one_auto_paper_charge_once"
_AB = "test_a_scheduled_batch_paper_graded_meanwhile_is_skipped"
_FA = "test_every_auto_grade_dispatch_carries_the_flag"
_FB = "test_every_scheduled_batch_dispatch_carries_the_flag"
_DB = "test_a_database_outage_is_retried_once_with_the_first_countdown"
_TR = "test_each_transient_class_is_retried"
_CD = "test_the_countdown_doubles_to_fifteen_minutes_plus_up_to_thirty_seconds"
_BR = "test_a_broker_outage_mid_dispatch_resends_only_the_missed_papers"
_OTHER = "test_any_other_error_keeps_todays_answer"
_MISSING = "test_a_missing_assignment_is_not_retried"
_T1 = "test_t1_auto_grade_due_assignment"

#: Written before any run: every test reckoned to fail under each mutant.
EXPECTED = {
    "G1": [_A1, _A2, _AB],
    "G2": [_A1, _A2, _FA],
    "G3": [_A1],
    "G4": [_OTHER, _MISSING, _T1],
    "G5": [_TR, _BR],
    "G6": [_CD],
    "G7": [_DB],
    "G8": [_BR],
    "G9": [_BR],
    "G10": [_AB, _FB],
}

#: A test module that could not be loaded.
LOAD_FAILURE = "unittest.loader._FailedTest"


def clear_pycache(path):
    shutil.rmtree(pathlib.Path(path).parent / "__pycache__", ignore_errors=True)


def _expected(mid):
    """The failing tests written beforehand for a mutant: one name or a list.
    A list names EVERY test reckoned to fail under it, not only the one the
    mutant was made for (Senior Manager, 8 October 2026)."""
    value = EXPECTED[mid]
    return [value] if isinstance(value, str) else list(value)


def judge(mid, returncode, text):
    """(status, the run's "Ran" line, the failing tests' names)."""
    failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", text, re.M)))
    ran = re.findall(r"^Ran \d+ tests? in .*$", text, re.M)
    ran_line = ran[-1] if ran else None
    if returncode == 0:
        status = "SURVIVED"
    elif ran_line and LOAD_FAILURE not in text and set(_expected(mid)) <= set(failed):
        status = "KILLED"
    else:
        status = "BROKEN"
    return status, ran_line, failed


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
        if path.endswith(".py"):
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
        status, ran_line, failed = judge(mid, p.returncode, text)
        results[mid] = {
            "what": what,
            "file": path,
            "kind": kind,
            "status": status,
            "exit": p.returncode,
            "ran_line": ran_line,
            "expected": _expected(mid),
            "failing_tests": failed,
            "also_failed_not_expected": sorted(set(failed) - set(_expected(mid))),
        }
        print(mid, results[mid], flush=True)
    with open(OUT, "w") as f:
        json.dump(results, f, indent=2)
        f.write("\n")
    for status in ("KILLED", "SURVIVED", "BROKEN"):
        print(f"{status}:", [k for k, v in results.items() if v["status"] == status])


if __name__ == "__main__":
    main()
