"""Bundle 7 merge-down: mutants on the EPIC's versions of the files.

P/Q: the twelve of H-91's fourteen mutants whose anchors exist on the
     epic (read from d5's run_mutants.py as merged, not copied here).
     H-91's Q2 and Q3 have no anchor on the epic: their lines are in the
     two files where the merge kept the epic's side. E1 and E2 replace them.
E:   four leaks put back on lines only the epic has (E1 the roster
     import's refused row, E2 the tracked dispatch's log line, E3 the
     school admin invitation line, E4 the licence's reason-code line).
S/Y: H-89's six mutants on AutoGrader/settings.py, whose Sentry block the
     merge took from beta inside the epic's settings file.
H-89's other mutants are on files that are byte-identical to beta's here
and are not repeated.

Each mutant is applied, its test modules run, the killers are recorded,
the file is restored. Judged three ways (rule 18, SM 2026-10-05):
  SURVIVED  the inner run exits 0;
  KILLED    non-zero exit, the run's own "Ran" line, no test module that
            failed to load, and the failing test named in EXPECTED for
            that mutant among the failing tests;
  BROKEN    anything else (no "Ran" line, a load failure, no named
            failing test, or failures that do not include the expected
            one). Never counted as a kill.
EXPECTED was written before the first run. For d5's mutants the names
are the killers in d5's own battery logs on beta; for E1-E4 they are the
guard's one repository-wide test.

Rule 17: the test subprocess runs with PYTHONDONTWRITEBYTECODE=1, and the
mutated module's __pycache__ is deleted before each mutant and after each
restore. Rule 18: each run's output goes straight to a file
(mutant_logs/<name>.txt), stdin from /dev/null; nothing is piped.
"""

import ast
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

HERE = pathlib.Path("docs/evidence/epic-a-merge-down-b7")
H91 = pathlib.Path("docs/evidence/h91-ids-only-logs-everywhere/run_mutants.py")
H89 = pathlib.Path("docs/evidence/h89-log-address-scrubber/run_mutants.py")
SETTINGS_FILE = "AutoGrader/settings.py"
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))

GUARD_TESTS = [
    "AutoGrader.tests_no_pii_in_logs",
    "billing.tests.test_price_drift_reconciliation",
]
H89_TESTS = [
    "AutoGrader.tests_log_scrubbing",
    "billing.tests.test_log_scrubbing_end_to_end",
    "AutoGrader.tests_sentry_scrubbing",
    "AutoGrader.tests_student_frontend_domain",
]
NOT_ON_THE_EPIC = {"Q2", "Q3"}
H89_ON_SETTINGS = {"S11", "S16", "Y7", "Y8", "Y9", "Y12"}

EPIC_ONLY = [
    (
        "E1",
        "roster_import.py: a refused row by row number, not by name",
        "classrooms/services/roster_import.py",
        '"Bulk-add refused row %s of course %s: %s", row.row, course.id, exc\n',
        '"Bulk-add refused row %s of course %s: %s", row.first_name, course.id, exc\n',
    ),
    (
        "E2",
        "assignments/tasks.py: the tracked dispatch names the submission",
        "assignments/tasks.py",
        '        logger.info("Starting grading of submission %s", submission.id)\n',
        '        logger.info("Starting grading of submission %s", '
        "submission.student.get_full_name())\n",
    ),
    (
        "E3",
        "classrooms/serializers.py: the invitation line names the user by id",
        "classrooms/serializers.py",
        "                user_id,\n                school_id,\n",
        "                user.email,\n                school_id,\n",
    ),
    (
        "E4",
        "billing/license_service.py: the reason-code line carries no address",
        "billing/license_service.py",
        "                license_sub.school_id,\n"
        "                failure.reason_code,\n",
        "                license_sub.admin_user.email,\n"
        "                failure.reason_code,\n",
    ),
]

#: The test that must be among a mutant's failing tests. Written before
#: the first run of this battery.
THE_GUARD = "test_no_log_or_print_call_passes_an_address_or_a_name"
EXPECTED = {
    **{m: THE_GUARD for m in "P1 P2 P3 P4 P5 P6 P7 P8 P9 Q1 Q4 Q5".split()},
    **{m: THE_GUARD for m in "E1 E2 E3 E4".split()},
    "S11": "test_it_is_off_under_the_test_runner",
    "S16": "test_settings_import_nothing_from_the_project_at_the_top_level",
    "Y7": "test_settings_pass_the_hooks_to_sentry",
    "Y8": "test_settings_pass_the_hooks_to_sentry",
    "Y9": "test_a_failure_to_import_the_hooks_is_not_swallowed",
    "Y12": "test_settings_pass_the_hooks_to_sentry",
}

#: A test module that could not be loaded.
LOAD_FAILURE = "unittest.loader._FailedTest"


def _literal_mutants(path):
    """The MUTANTS list of one of d5's scripts, read without running it."""
    module = ast.parse(path.read_text())
    names = {}
    for node in module.body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if target.id == "MUTANTS":
            return [
                tuple(
                    names[e.id] if isinstance(e, ast.Name) else ast.literal_eval(e)
                    for e in entry.elts
                )
                for entry in node.value.elts
            ]
        try:
            names[target.id] = ast.literal_eval(node.value)
        except ValueError:
            pass
    raise AssertionError(f"no MUTANTS in {path}")


def all_mutants():
    """(name, what, file, old, new, tests), in the order they run."""
    out = []
    for mid, what, path, old, new, nth in (m[:6] for m in _literal_mutants(H91)):
        if mid in NOT_ON_THE_EPIC:
            continue
        assert nth == 1, f"{mid}: only first occurrences are supported"
        out.append((mid, what, path, old, new, GUARD_TESTS))
    for mid, what, path, old, new in EPIC_ONLY:
        out.append((mid, what, path, old, new, GUARD_TESTS))
    for mid, what, path, old, new, nth in (m[:6] for m in _literal_mutants(H89)):
        if mid in H89_ON_SETTINGS:
            assert path == SETTINGS_FILE and nth == 1, mid
            out.append((mid, what, path, old, new, H89_TESTS))
    return out


def clear_pycache(path):
    shutil.rmtree(pathlib.Path(path).parent / "__pycache__", ignore_errors=True)


def judge(mid, returncode, text):
    """(status, the run's "Ran" line, the failing tests' names)."""
    failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", text, re.M)))
    ran = re.findall(r"^Ran \d+ tests? in .*$", text, re.M)
    ran_line = ran[-1] if ran else None
    if returncode == 0:
        status = "SURVIVED"
    elif ran_line and LOAD_FAILURE not in text and EXPECTED[mid] in failed:
        status = "KILLED"
    else:
        status = "BROKEN"
    return status, ran_line, failed


def main():
    mutants = all_mutants()
    assert {m[0] for m in mutants} == set(EXPECTED), "EXPECTED names every mutant"
    originals = {}
    for mid, _what, path, old, new, _tests in mutants:
        source = originals.setdefault(path, open(path).read())
        count = source.count(old)
        # d5's P and Q anchors name the FIRST occurrence; ours are unique.
        assert count >= 1 and (count == 1 or mid[0] in "PQ"), f"{mid}: {count}"
        ast.parse(source.replace(old, new, 1))
    if "--check" in sys.argv:
        print(len(mutants), "mutants: anchors found, all parse")
        print(" ".join(m[0] for m in mutants))
        return
    LOGS.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    results = {}
    for mid, what, path, old, new, tests in mutants:
        original = originals[path]
        try:
            clear_pycache(path)
            open(path, "w").write(original.replace(old, new, 1))
            log = LOGS / f"{mid}.txt"
            with open(log, "w") as out, open(os.devnull) as devnull:
                p = subprocess.run(
                    [sys.executable, "manage.py", "test", *tests]
                    + [f"--settings={SETTINGS}", "--keepdb", "--noinput"],
                    stdin=devnull,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    env=env,
                    timeout=900,
                )
        finally:
            open(path, "w").write(original)
            clear_pycache(path)
        text = log.read_text(errors="replace")
        status, ran_line, failed = judge(mid, p.returncode, text)
        results[mid] = {
            "what": what,
            "file": path,
            "status": status,
            "exit": p.returncode,
            "ran_line": ran_line,
            "expected": EXPECTED[mid],
            "failing_tests": failed,
        }
        print(mid, results[mid], flush=True)
    with open(OUT, "w") as f:
        json.dump(results, f, indent=2)
        f.write("\n")
    for status in ("KILLED", "SURVIVED", "BROKEN"):
        print(f"{status}:", [k for k, v in results.items() if v["status"] == status])


if __name__ == "__main__":
    main()
