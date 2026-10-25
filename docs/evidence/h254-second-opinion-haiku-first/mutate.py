# flake8: noqa: E501,B950
"""H-254 second grader haiku first: deliberate breaks, and the tests that must catch each.

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

HERE = pathlib.Path("docs/evidence/h254-second-opinion-haiku-first")
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))

SV = "ai_processor/services.py"
ST = "AutoGrader/settings.py"
TESTS = [
    "ai_processor.tests_h254_second_opinion_haiku_first",
    "ai_processor.tests_h205_image_fallback",
]
K = "keepdb"
DEFAULT = '    default=["anthropic/claude-haiku-5.5", "deepseek/deepseek-v4-pro"],'

# id, what is broken, file, text replaced, new text, tests, database kind
MUTANTS = [
    (
        "S1",
        "the order flipped back (deepseek first)",
        ST,
        DEFAULT,
        '    default=["deepseek/deepseek-v4-pro", "anthropic/claude-haiku-5.5"],',
        TESTS,
        K,
    ),
    (
        "S2",
        "gemini-2.5-flash restored in deepseek's place",
        ST,
        DEFAULT,
        '    default=["anthropic/claude-haiku-5.5", "google/gemini-2.5-flash"],',
        TESTS,
        K,
    ),
    (
        "S3",
        "haiku dropped (deepseek alone)",
        ST,
        DEFAULT,
        '    default=["deepseek/deepseek-v4-pro"],',
        TESTS,
        K,
    ),
    (
        "S4",
        "grader B's call is no longer pinned to the second model",
        SV,
        "                        override_model=second_model,\n",
        "                        override_model=None,\n",
        TESTS,
        K,
    ),
]

_ORDER = "test_the_default_candidates_are_haiku_then_deepseek"
_PICK_GROK = "test_grader_a_on_grok_gives_haiku"
_PICK_HAIKU = "test_grader_a_on_haiku_gives_deepseek"
_PICK_DEEPSEEK = "test_grader_a_on_deepseek_gives_haiku"
_PIPE_IMAGE = "test_a_question_with_an_image_is_re_graded_by_haiku"
_PIPE_TEXT = "test_a_question_without_an_image_is_re_graded_by_haiku_too"
_PIPE_OUTAGE = "test_when_grader_a_fell_back_to_haiku_grader_b_is_deepseek"
_H205_NAMES = "test_the_default_candidates_are_deepseek_and_haiku"
_H205_NO_GEMINI = "test_no_production_file_names_gemini_2_5_flash"

#: Written before any run: every test reckoned to fail under each mutant.
EXPECTED = {
    "S1": [_ORDER, _PICK_GROK, _PIPE_IMAGE, _PIPE_TEXT],
    "S2": [_ORDER, _PICK_HAIKU, _PIPE_OUTAGE, _H205_NAMES, _H205_NO_GEMINI],
    "S3": [_ORDER, _PICK_GROK, _PICK_DEEPSEEK, _PIPE_IMAGE, _PIPE_TEXT, _H205_NAMES],
    "S4": [_PIPE_IMAGE, _PIPE_TEXT, _PIPE_OUTAGE],
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
                    timeout=1200,
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
