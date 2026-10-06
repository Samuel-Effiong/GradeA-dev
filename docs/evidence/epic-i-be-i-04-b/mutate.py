"""BE-I-04 slice B: deliberate breaks, and the test that must catch each.

Each mutant is applied, its test modules run, the failing tests are
recorded, the file is restored. Judged three ways (rule 18, SM 2026-10-05):
  SURVIVED  the inner run exits 0;
  KILLED    non-zero exit, the run's own "Ran" line, no test module that
            failed to load, and the test named in EXPECTED for that mutant
            among the failing tests;
  BROKEN    anything else. Never counted as a kill.
EXPECTED was written before any run of a mutant.

Rule 17: every inner run has PYTHONDONTWRITEBYTECODE=1, and the mutated
file's __pycache__ is deleted before each mutant and after each restore.
Rule 18: each inner run writes straight to its own file
(mutant_logs/<name>.txt), stdin from the null device; nothing is piped.

An interrupted battery leaves no mutant behind: each mutant is restored in
a `finally` block, and SIGTERM (what `timeout` sends) is turned into an
ordinary exit so that block runs. After a SIGKILL nothing in this process
can run; the gate script's EXIT trap restores the files from the commit,
and the next start refuses to run unless every anchor is found once.
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

HERE = pathlib.Path("docs/evidence/epic-i-be-i-04-b")
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))

CACHE = "ai_processor/grading_cache.py"
SERVICES = "ai_processor/services.py"
CONFIG = "ai_processor/grading_config.py"

TESTS = [
    "ai_processor.tests_grading_cache_key_v2",
    "ai_processor.tests_grading_cache",
    "ai_processor.tests_grading_config",
]
K = "keepdb"

#: (name, what, file, old, new, tests, kind)
MUTANTS = [
    (
        "K1",
        "the teacher's extra instructions are left out of the key",
        CACHE,
        "        context.custom_instructions,\n",
        "",
        TESTS,
        K,
    ),
    (
        "K2",
        "the assignment's title is left out of the key",
        CACHE,
        "        context.assignment_title,\n",
        "",
        TESTS,
        K,
    ),
    (
        "K3",
        "the assignment's instructions are left out of the key",
        CACHE,
        "        context.assignment_instructions,\n",
        "",
        TESTS,
        K,
    ),
    (
        "K4",
        "only the question's text is in the key, not the whole question",
        CACHE,
        "        _question_as_sent(question),\n",
        '        str(question.get("question_text", "")),\n',
        TESTS,
        K,
    ),
    (
        "K5",
        "the prompt version is left out of the key",
        CACHE,
        "        context.prompt_version,\n",
        "",
        TESTS,
        K,
    ),
    (
        "K6",
        "the settings version is left out of the key",
        CACHE,
        "        context.config_version,\n",
        "",
        TESTS,
        K,
    ),
    (
        "K7",
        "the assignment is left out of the key",
        CACHE,
        "        context.assignment_id,\n",
        "",
        TESTS,
        K,
    ),
    (
        "K8",
        "the answer is left out of the key",
        CACHE,
        "        _normalize_answer(answer_html),\n",
        "",
        TESTS,
        K,
    ),
    (
        "K9",
        "the release is put into the key",
        CACHE,
        "            config_version=run.config.version,\n",
        "            config_version=run.config.version + run.config.release,\n",
        TESTS,
        K,
    ),
    (
        "K10",
        "the key takes the teacher's raw text, not the text as spliced",
        CACHE,
        '            custom_instructions=custom_instructions or "",\n',
        '            custom_instructions=getattr(assignment_model, "custom_ai_prompt", "")'
        ' or "",\n',
        TESTS,
        K,
    ),
    (
        "K11",
        "the keys stay at version one",
        CACHE,
        'CACHE_VERSION = "v2"',
        'CACHE_VERSION = "v1"',
        TESTS,
        K,
    ),
    (
        "R1",
        "the store takes a fresh reading of the settings",
        SERVICES,
        "        # The SAME reading the lookup of this run used: never a fresh one.\n"
        "        context = self._match_context(assignment_model, run or GradingRun.start())",
        "        context = self._match_context(assignment_model, GradingRun.start())",
        TESTS,
        K,
    ),
    (
        "R2",
        "each attempt of the retry loop takes its own reading",
        SERVICES,
        "                    run=run,\n                    # The single-pass path",
        "                    run=GradingRun.start(),\n                    # The single-pass path",
        TESTS,
        K,
    ),
    (
        "R3",
        "the pipeline ignores the reading it is given",
        SERVICES,
        "        # attempt shares it; a direct caller gets one for this call.\n"
        "        run = run or GradingRun.start()\n",
        "        # attempt shares it; a direct caller gets one for this call.\n"
        "        run = GradingRun.start()\n",
        TESTS,
        K,
    ),
    (
        "S1",
        "the splice reads the live switch, not the run's reading",
        SERVICES,
        '            enabled = run.config.get("GRADING_CUSTOM_INSTRUCTIONS_ENABLED")\n',
        '            enabled = getattr(settings, "GRADING_CUSTOM_INSTRUCTIONS_ENABLED", True)\n',
        TESTS,
        K,
    ),
    (
        "E1",
        "anything that is a dictionary is taken for an envelope",
        CACHE,
        ' or set(stored) != {"evaluation", "served_model"}',
        "",
        TESTS,
        K,
    ),
    (
        "E2",
        "a reused answer keeps the marker inside its evaluation",
        CACHE,
        '    reused["graded_by"] = served_model or UNNAMED_MODEL\n',
        '    reused.setdefault("graded_by", served_model or UNNAMED_MODEL)\n',
        TESTS,
        K,
    ),
    (
        "E3",
        "the envelope names the intended model, not the one that answered",
        CACHE,
        '    envelope = {"evaluation": kept, "served_model": served_model}\n',
        '    envelope = {"evaluation": kept, "served_model": model_name}\n',
        TESTS,
        K,
    ),
    (
        "E4",
        'a reply that names no model is stored as "llm"',
        SERVICES,
        "                    and graded_by != grading_cache.UNNAMED_MODEL\n"
        "                    else None\n",
        "                    and graded_by != grading_cache.UNNAMED_MODEL\n"
        "                    else graded_by\n",
        TESTS,
        K,
    ),
    (
        "B1",
        "a graded_by in the AI's own reply is kept",
        SERVICES,
        '                evaluation["graded_by"] = model_name or grading_cache.UNNAMED_MODEL\n',
        "                evaluation.setdefault(\n"
        '                    "graded_by", model_name or grading_cache.UNNAMED_MODEL\n'
        "                )\n",
        TESTS,
        K,
    ),
    (
        "B2",
        "a from_cache in the AI's own reply is kept",
        SERVICES,
        '                evaluation.pop("from_cache", None)\n',
        "",
        TESTS,
        K,
    ),
    (
        "T1",
        "the provider call uses a literal temperature again",
        SERVICES,
        '"temperature": AI_TEMPERATURE,',
        '"temperature": 0.0,',
        TESTS,
        K,
    ),
    (
        "T2",
        "the temperature is left out of the settings version",
        CONFIG,
        '    "AI_TEMPERATURE",\n',
        "",
        TESTS,
        K,
    ),
]

#: The failing test each mutant must produce. Written before any run.
EXPECTED = {
    "K1": "test_edited_teacher_instructions_are_a_fresh_grade",
    "K2": "test_an_edited_assignment_title_is_a_fresh_grade",
    "K3": "test_edited_assignment_instructions_are_a_fresh_grade",
    "K4": "test_a_changed_additional_note_on_the_question_is_a_fresh_grade",
    "K5": "test_a_changed_grading_prompt_is_a_fresh_grade",
    "K6": "test_a_changed_grading_setting_is_a_fresh_grade",
    "K7": "test_another_assignment_with_the_same_question_is_a_fresh_grade",
    "K8": "test_miss_on_different_answer",
    "K9": "test_a_new_release_still_reuses_the_saved_answer",
    "K10": "test_teacher_instructions_that_are_switched_off_do_not_count",
    "K11": "test_the_keys_are_version_two",
    "R1": "test_a_setting_changed_during_the_call_does_not_split_lookup_and_store",
    "R2": "test_a_retried_run_still_reads_them_once",
    "R3": "test_the_settings_are_read_exactly_once_for_a_run",
    "S1": "test_a_switch_flipped_after_the_run_started_is_not_seen",
    "E1": "test_an_entry_that_is_not_an_envelope_is_a_miss",
    "E2": "test_a_reused_answer_is_marked_by_our_code_not_by_its_content",
    "E3": "test_the_model_that_answered_is_stored_beside_the_evaluation",
    "E4": "test_a_reply_that_names_no_model_is_stored_with_none",
    "B1": "test_a_graded_by_in_the_reply_is_replaced_by_the_model_that_answered",
    "B2": "test_a_from_cache_in_the_reply_is_dropped_and_the_answer_is_stored",
    "T1": "test_the_call_that_leaves_the_app_uses_the_constant",
    "T2": "test_it_is_in_the_settings_version",
}

#: A test module that could not be loaded.
LOAD_FAILURE = "unittest.loader._FailedTest"


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


def _exit_on_sigterm(signum, _frame):
    raise SystemExit(128 + signum)


def main():
    signal.signal(signal.SIGTERM, _exit_on_sigterm)
    assert [m[0] for m in MUTANTS] == list(EXPECTED), "EXPECTED names every mutant"
    originals = {}
    for mid, _what, path, old, new, _tests, kind in MUTANTS:
        source = originals.setdefault(path, open(path).read())
        assert source.count(old) == 1, f"{mid}: anchor found {source.count(old)} times"
        assert kind in ("keepdb", "fresh"), mid
        if path.endswith(".py"):
            ast.parse(source.replace(old, new, 1))
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
            open(path, "w").write(original.replace(old, new, 1))
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
