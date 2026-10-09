"""AI-call record, slice 0: deliberate breaks, and the test that must catch each.

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

HERE = pathlib.Path("docs/evidence/epic-i-ai-call-record-0")
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))

VOTE = "ai_processor/vote.py"
STEP = "ai_processor/step_run.py"
RUN = "ai_processor/grading_run.py"
WORDS = "students/step_label.py"

# The slice's own modules, and slice C's two label modules (the pin: the
# move of the vote must not change a grading label).
TESTS = [
    "ai_processor.tests_vote_helper",
    "ai_processor.tests_step_run",
    "ai_processor.tests_step_run_scope",
    "students.tests_step_label_words",
    "ai_processor.tests_grading_run_label",
    "ai_processor.tests_grading_run_pipeline",
]
K = "keepdb"

BEGIN_TAIL = (
    "        self.reply_received = False\n"
    "        self.reply_model = None\n"
    "\n"
    "    # -- gathering"
)

# Each mutant: its id, what is broken, the file, the text replaced, the new text, the test modules, the database kind.
MUTANTS = [
    (
        "V1",
        "a tie goes to the first by alphabet, not to the main model",
        VOTE,
        "    if main in named:\n        return main\n",
        "    if False:\n        return main\n",
        TESTS,
        K,
    ),
    (
        "V2",
        "a tie goes to the LAST by alphabet",
        VOTE,
        "        return named[0]\n",
        "        return named[-1]\n",
        TESTS,
        K,
    ),
    (
        "V3",
        "an unnamed model wins a tie against a named one",
        VOTE,
        "    named = sorted(model for model in leaders if model is not None)\n",
        "    if None in leaders:\n        return None\n"
        "    named = sorted(model for model in leaders if model is not None)\n",
        TESTS,
        K,
    ),
    (
        "V4",
        "a model with zero votes can lead",
        VOTE,
        "if count > 0}",
        "if count >= 0}",
        TESTS,
        K,
    ),
    (
        "V5",
        "the model with the FEWEST votes wins",
        VOTE,
        "    most = max(counts.values())\n",
        "    most = min(counts.values())\n",
        TESTS,
        K,
    ),
    (
        "V6",
        "the helper changes the votes it was given",
        VOTE,
        "    if not counts:\n        return None\n",
        "    votes.pop(None, None)\n    if not counts:\n        return None\n",
        TESTS,
        K,
    ),
    (
        "V7",
        "the grading run keeps its own copy of the rule instead of calling the helper",
        RUN,
        '        return majority_model(votes, self.config.get("MAIN_MODEL")) or MODEL_UNKNOWN\n',
        "        return (sorted(m for m in votes if m is not None) or [MODEL_UNKNOWN])[0]\n",
        TESTS,
        K,
    ),
    (
        "V8",
        "the grading run lets a helper answer of None through",
        RUN,
        '        return majority_model(votes, self.config.get("MAIN_MODEL")) or MODEL_UNKNOWN\n',
        '        return majority_model(votes, self.config.get("MAIN_MODEL"))\n',
        TESTS,
        K,
    ),
    (
        "V9",
        "the grading run gives the helper no main model",
        RUN,
        'majority_model(votes, self.config.get("MAIN_MODEL"))',
        "majority_model(votes, None)",
        TESTS,
        K,
    ),
    (
        "V10",
        "the grading run forgets the reused answers' votes",
        RUN,
        "        votes = Counter(self.fresh_answers + self.reused_answers)\n        if not votes:\n"
        "            # No answer was marked by an AI.",
        "        votes = Counter(self.fresh_answers)\n        if not votes:\n"
        "            # No answer was marked by an AI.",
        TESTS,
        K,
    ),
    (
        "S1",
        "a provider name equal to a fixed word is kept as a name",
        STEP,
        "    if isinstance(model, str) and model and model not in FIXED_MODEL_WORDS:\n",
        "    if isinstance(model, str) and model:\n",
        TESTS,
        K,
    ),
    (
        "S2",
        "not_run is not among the fixed words",
        STEP,
        "FIXED_MODEL_WORDS = frozenset({UNLABELLED, MODEL_UNKNOWN, NOT_RUN})",
        "FIXED_MODEL_WORDS = frozenset({UNLABELLED, MODEL_UNKNOWN})",
        TESTS,
        K,
    ),
    (
        "S3",
        "a new attempt keeps the votes of the one before",
        STEP,
        '        """Forget what was gathered; keep the step\'s name."""\n'
        "        self._votes = {}\n",
        '        """Forget what was gathered; keep the step\'s name."""\n',
        TESTS,
        K,
    ),
    (
        "S4",
        "a new attempt keeps the marks of the call before",
        STEP,
        "        self.call_left = False\n" + BEGIN_TAIL,
        "        pass\n" + BEGIN_TAIL,
        TESTS,
        K,
    ),
    (
        "S5",
        "a negative count votes",
        STEP,
        "        count = max(int(count), 0)\n",
        "        count = int(count)\n",
        TESTS,
        K,
    ),
    (
        "S6",
        "a reply that supplied no item is not a kept reply",
        STEP,
        '        the prompt of version `prompt_version`."""\n'
        "        self._kept = True\n",
        '        the prompt of version `prompt_version`."""\n',
        TESTS,
        K,
    ),
    (
        "S7",
        "nothing kept reads unknown instead of None",
        STEP,
        "        if not self._kept:\n            return None\n",
        "        if not self._kept:\n            return MODEL_UNKNOWN\n",
        TESTS,
        K,
    ),
    (
        "S8",
        "only unnamed replies give None instead of unknown",
        STEP,
        "        return majority_model(self._votes, main) or MODEL_UNKNOWN\n",
        "        return majority_model(self._votes, main)\n",
        TESTS,
        K,
    ),
    (
        "S9",
        "a reply can be marked received before the call left",
        STEP,
        "        if not self.call_left:\n"
        '            raise RuntimeError("a reply cannot be received before the call left")\n',
        "",
        TESTS,
        K,
    ),
    (
        "S10",
        "the reply's name is recorded raw, a fixed word included",
        STEP,
        "        self.reply_model = _name(model) or MODEL_UNKNOWN\n",
        "        self.reply_model = model or MODEL_UNKNOWN\n",
        TESTS,
        K,
    ),
    (
        "S11",
        "the scope is not restored after an exception",
        STEP,
        "        try:\n            yield self\n        finally:\n            _CURRENT.reset(token)\n",
        "        yield self\n        _CURRENT.reset(token)\n",
        TESTS,
        K,
    ),
    (
        "S12",
        "leaving a scope clears the variable, not restoring the outer scope",
        STEP,
        "            _CURRENT.reset(token)\n",
        "            _CURRENT.set(None)\n",
        TESTS,
        K,
    ),
    (
        "S13",
        "a call with no scope never fails",
        STEP,
        "    if run is None and task_type in enforced:\n",
        "    if False:\n",
        TESTS,
        K,
    ),
    (
        "S14",
        "a task type that is not enforced fails without a scope",
        STEP,
        "    if run is None and task_type in enforced:\n",
        "    if run is None:\n",
        TESTS,
        K,
    ),
    (
        "S15",
        "a task type is enforced already in slice 0",
        STEP,
        "ENFORCED_STEP_TASK_TYPES = frozenset()\n",
        'ENFORCED_STEP_TASK_TYPES = frozenset({"extract_answer"})\n',
        TESTS,
        K,
    ),
    (
        "S16",
        "grading is counted among the step task types",
        STEP,
        '    {"extract_assignment", "generate_assignment", "extract_answer", "formatted_grade"}\n',
        '    {"extract_assignment", "generate_assignment", "extract_answer", "formatted_grade", "grade_assignment"}\n',
        TESTS,
        K,
    ),
    (
        "P1",
        "a kept reply does not record its prompt version",
        STEP,
        "        if isinstance(prompt_version, str) and prompt_version:\n",
        "        if False:\n",
        TESTS,
        K,
    ),
    (
        "P2",
        "a new attempt keeps the prompt versions of the one before",
        STEP,
        "        self._votes = {}\n        self._prompts = {}\n"
        "        self._kept = False\n        self.call_left = False\n"
        "        self.reply_received = False\n        self.reply_model = None\n\n    # -- gathering",
        "        self._votes = {}\n        self._kept = False\n        self.call_left = False\n"
        "        self.reply_received = False\n        self.reply_model = None\n\n    # -- gathering",
        TESTS,
        K,
    ),
    (
        "P3",
        "the prompt version of the last reply wins, not the most used",
        STEP,
        "        return majority_model(self._prompts, None)\n",
        "        return list(self._prompts)[-1] if self._prompts else None\n",
        TESTS,
        K,
    ),
    (
        "P4",
        "a reply votes for its prompt version once per item",
        STEP,
        "            self._prompts[prompt_version] = self._prompts.get(prompt_version, 0) + 1\n",
        "            self._prompts[prompt_version] = self._prompts.get(prompt_version, 0) + max(count, 1)\n",
        TESTS,
        K,
    ),
    (
        "L1",
        "a word of the students app differs from the AI-processor's",
        WORDS,
        'NOT_RUN = "not_run"\n',
        'NOT_RUN = "notrun"\n',
        TESTS,
        K,
    ),
    (
        "L2",
        "the words module imports from the project",
        WORDS,
        "#: No label recorded: made before labels existed.",
        "from students import grading_label  # noqa\n\n#: No label recorded: made before labels existed.",
        TESTS,
        K,
    ),
    (
        "L3",
        "the failed result is missing from the re-check's results",
        WORDS,
        "RECHECK_RESULTS = (NOT_RUN, FAILED, CONFIRMED_BLANK, FOUND_WRITING)",
        "RECHECK_RESULTS = (NOT_RUN, CONFIRMED_BLANK, FOUND_WRITING)",
        TESTS,
        K,
    ),
]

#: The failing test each mutant must be caught by (written before any run).
EXPECTED = {
    "V1": "test_a_two_way_tie_goes_to_the_main_model",
    "V2": "test_a_tie_without_the_main_model_goes_to_the_first_by_alphabet",
    "V3": "test_an_unnamed_model_loses_a_tie_to_a_named_one",
    "V4": "test_only_zero_votes_give_none",
    "V5": "test_the_model_with_most_votes_wins",
    "V6": "test_the_input_is_not_changed",
    "V7": "test_the_run_calls_the_helper_with_the_counts_and_the_main_model",
    "V8": "test_a_helper_answer_of_none_reads_unknown",
    "V9": "test_the_run_calls_the_helper_with_the_counts_and_the_main_model",
    "V10": "test_the_same_model_over_many_random_patterns",
    "S1": "test_unlabelled_as_a_providers_name_reads_unknown",
    "S2": "test_not_run_as_a_providers_name_reads_unknown",
    "S3": "test_a_new_attempt_starts_from_nothing",
    "S4": "test_a_new_attempt_starts_from_nothing",
    "S5": "test_a_negative_count_votes_for_nobody",
    "S6": "test_a_reply_that_supplied_no_item_is_kept_but_does_not_vote",
    "S7": "test_nothing_kept_gives_none_so_the_caller_chooses_its_word",
    "S8": "test_only_unnamed_replies_read_unknown",
    "S9": "test_a_reply_cannot_be_marked_before_the_call_left",
    "S10": "test_a_reply_named_with_a_fixed_word_records_unknown",
    "S11": "test_the_scope_is_restored_after_an_exception",
    "S12": "test_a_nested_scope_restores_the_outer_one",
    "S13": "test_a_task_type_that_needs_a_scope_raises_when_none_is_open",
    "S14": "test_a_task_type_that_is_not_enforced_passes_without_a_scope",
    "S15": "test_in_slice_0_nothing_is_enforced_yet",
    "S16": "test_grading_is_not_a_step_task_type",
    "P1": "test_one_reply_with_a_version_gives_that_version",
    "P2": "test_a_new_attempt_forgets_the_prompt_versions",
    "P3": "test_the_version_most_replies_used_wins",
    "P4": "test_a_reply_votes_once_however_many_items_it_supplied",
    "L1": "test_the_words",
    "L2": "test_no_project_import",
    "L3": "test_the_recheck_results_are_these_four_and_unlabelled",
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
