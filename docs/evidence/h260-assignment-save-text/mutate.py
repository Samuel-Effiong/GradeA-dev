# flake8: noqa: E501,B950
"""H-260 a failed assignment save answers a fixed sentence and logs the class: deliberate breaks, and the tests that must catch each.

Each mutant is applied, its test module run, the failing tests are recorded,
the file is restored. Judged (rule 18): SURVIVED = the inner run exits 0;
KILLED = non-zero exit, the run's own "Ran" line, no module that failed to
load, EVERY test named in EXPECTED among the failing tests, and (rule 22)
every FAIL/ERROR block of each expected test holding the fragment written for
it in EXPECTED. Any other failing test is recorded as
also_failed_not_expected, never hidden; BROKEN = anything else. EXPECTED was
written before any run, traced against every assertion of the module
(the control too).

Rule 17: PYTHONDONTWRITEBYTECODE=1 on every inner run; the mutated file's
__pycache__ is deleted before each mutant and after each restore.
Rule 18: each inner run writes straight to its own file, stdin from the null
device, nothing piped. Each mutant is restored in a `finally` block and
SIGTERM is turned into an ordinary exit; the gate script's EXIT trap restores
the file from the commit.
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

HERE = pathlib.Path("docs/evidence/h260-assignment-save-text")
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))

SER = "assignments/serializers.py"
TESTS = ["assignments.tests_h260_assignment_create_text"]
K = "keepdb"

LOG = '            logger.warning("Assignment save failed: %s", type(e).__name__)\n'
RAISE = "            raise serializers.ValidationError(ASSIGNMENT_SAVE_FAILED) from e\n"

# id, what is broken, file, text(s) replaced, new text(s), tests, database kind
MUTANTS = [
    # What the client reads
    (
        "A1",
        "the 400 quotes the error's text again",
        SER,
        RAISE,
        '            raise serializers.ValidationError(f"{ASSIGNMENT_SAVE_FAILED} {e}") from e\n',
        TESTS,
        K,
    ),
    (
        "A2",
        "the 400 reads another sentence",
        SER,
        RAISE,
        '            raise serializers.ValidationError("Failed to create assignment and rubric.") from e\n',
        TESTS,
        K,
    ),
    # What the server log gets
    (
        "L1",
        "the log line names the error's text, not its class",
        SER,
        LOG,
        '            logger.warning("Assignment save failed: %s", e)\n',
        TESTS,
        K,
    ),
    (
        "L2",
        "the log line names the class and the text",
        SER,
        LOG,
        '            logger.warning("Assignment save failed: %s: %s", type(e).__name__, e)\n',
        TESTS,
        K,
    ),
    (
        "L3",
        "the log line carries the traceback",
        SER,
        LOG,
        '            logger.warning("Assignment save failed: %s", type(e).__name__, exc_info=True)\n',
        TESTS,
        K,
    ),
    (
        "L4",
        "the log line is logger.exception",
        SER,
        LOG,
        '            logger.exception("Assignment save failed: %s", type(e).__name__)\n',
        TESTS,
        K,
    ),
    (
        "L5",
        "no log line",
        SER,
        LOG,
        "",
        TESTS,
        K,
    ),
    (
        "L6",
        "the log line drops to INFO",
        SER,
        LOG,
        '            logger.info("Assignment save failed: %s", type(e).__name__)\n',
        TESTS,
        K,
    ),
]

_TEXT = "test_the_400_carries_none_of_the_errors_text"
_SENTENCE = "test_the_400_reads_the_fixed_sentence"
_LOG = "test_the_server_log_names_the_class_at_warning"
_CONTROL = "test_control_the_draft_stays_unsaved_and_saves_after"

LEAK = "unexpectedly found in"
MISSING = "not found in"
NO_LOG = "no logs of level WARNING or higher triggered on assignments.serializers"

#: Written before any run: {mutant: {test: the fragment its every block holds}},
#: every test reckoned to fail under it, traced against every assertion of the
#: module. The log test asserts, in order: one record, its level, the class in
#: its message, no fragment of the error's text, no exception attached. The
#: control passes under every mutant: none changes the status or what is saved.
EXPECTED = {
    # The sentence is still there; the text is too.
    "A1": {_TEXT: LEAK},
    # No text comes back, so the text test passes; the sentence is missing.
    "A2": {_SENTENCE: MISSING},
    "L1": {_LOG: MISSING},
    "L2": {_LOG: LEAK},
    "L3": {_LOG: "is not None"},
    "L4": {_LOG: "'ERROR' != 'WARNING'"},
    "L5": {_LOG: NO_LOG},
    "L6": {_LOG: NO_LOG},
}


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
