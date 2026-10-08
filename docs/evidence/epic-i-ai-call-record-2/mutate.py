"""AI-call record, slice 2: deliberate breaks, and the test that must catch each.

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

The one "fresh" mutant (the migration) is last: it builds its own test
database from the mutated migration, and the gate script drops that
database afterwards.
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

HERE = pathlib.Path("docs/evidence/epic-i-ai-call-record-2")
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))

RR = "ai_processor/reading_run.py"
RC = "ai_processor/reading_config.py"
SV = "ai_processor/services.py"
STEP = "ai_processor/step_run.py"
WORDS = "students/step_label.py"
SS = "students/services.py"
M33 = "students/migrations/0033_submission_answers_recheck_pair_check.py"

TESTS = [
    "ai_processor.tests_reading_config",
    "ai_processor.tests_reading_run",
    "ai_processor.tests_reading_pipeline",
    "ai_processor.tests_step_run_scope",
    "students.tests_reading_label_columns",
    "students.tests_reading_label_routes",
    "students.tests_reading_label_static",
]
K = "keepdb"
F = "fresh"

# Each mutant: its id, what is broken, the file, the text replaced, the new text, the test modules, the database kind.
MUTANTS = [
    (
        "R1",
        "a placeholder (an answer no reply supplied) votes for an unnamed model",
        RR,
        '            for name in self._origins.get(_key(answer["question_number"]), ()):\n',
        '            for name in self._origins.get(_key(answer["question_number"]), (None,)):\n',
        TESTS,
        K,
    ),
    (
        "R2",
        "the vote counts every origin, not only the answers as saved",
        RR,
        (
            "        for answer in saved or []:\n"
            '            if not isinstance(answer, dict) or answer.get("question_number") is None:\n'
            "                continue\n"
            '            for name in self._origins.get(_key(answer["question_number"]), (main,)):\n'
            if False
            else "        for answer in saved or []:\n"
            '            if not isinstance(answer, dict) or answer.get("question_number") is None:\n'
            "                continue\n"
            '            for name in self._origins.get(_key(answer["question_number"]), ()):\n'
            "                votes[name] = votes.get(name, 0) + 1\n"
        ),
        "        for models in self._origins.values():\n"
        "            for name in models:\n"
        "                votes[name] = votes.get(name, 0) + 1\n",
        TESTS,
        K,
    ),
    (
        "R3",
        "a reply that repeats an answer makes it vote twice",
        RR,
        '                self._origins[_key(answer["question_number"])] = (name,)\n',
        '                self._origins[_key(answer["question_number"])] = (\n'
        '                    self._origins.get(_key(answer["question_number"]), ()) + (name,)\n'
        "                )\n",
        TESTS,
        K,
    ),
    (
        "R4",
        "an answer joined from two parts counts for the first part only",
        RR,
        "        if name not in known:\n",
        "        if False:\n",
        TESTS,
        K,
    ),
    (
        "R5",
        "the question number is not made canonical (Q3 is not 3)",
        RR,
        "    return _canonical_question_key(value)\n",
        "    return str(value)\n",
        TESTS,
        K,
    ),
    (
        "R6",
        "a re-check whose call never left reads failed, not not_run",
        RR,
        "    if not step.call_left:\n",
        "    if False:\n",
        TESTS,
        K,
    ),
    (
        "R7",
        "a reply received and then lost loses the providers name",
        RR,
        "    model = step.reply_model if step.reply_received else MODEL_UNKNOWN\n",
        "    model = MODEL_UNKNOWN\n",
        TESTS,
        K,
    ),
    (
        "R8",
        "a reply that was used still reads failed",
        RR,
        "    return (used or FAILED), (model or MODEL_UNKNOWN)\n",
        "    return FAILED, (model or MODEL_UNKNOWN)\n",
        TESTS,
        K,
    ),
    (
        "R9",
        "the re-check is labelled with the reading's model",
        RR,
        '            "answers_recheck_model": model[:MODEL_MAX],\n',
        '            "answers_recheck_model": self.reading_model(saved)[:MODEL_MAX],\n',
        TESTS,
        K,
    ),
    (
        "R10",
        "a new attempt keeps the origins of the thrown-away try",
        RR,
        "        self._origins = {}\n        self._used = None\n\n    # -- the reading",
        "        self._used = None\n\n    # -- the reading",
        TESTS,
        K,
    ),
    (
        "R11",
        "a new attempt keeps the re-check of the thrown-away try",
        RR,
        "        self.recheck.begin_attempt()\n",
        "",
        TESTS,
        K,
    ),
    (
        "R12",
        "a reply can be used before it was received",
        RR,
        "        if not self.recheck.reply_received:\n",
        "        if False:\n",
        TESTS,
        K,
    ),
    (
        "R13",
        "a joined answer counts for the first part's model only",
        RR,
        "            self._origins[key] = known + (name,)\n",
        "            self._origins[key] = known\n",
        TESTS,
        K,
    ),
    (
        "R14",
        "two parts by the same model count twice",
        RR,
        "        if name not in known:\n",
        "        if True:\n",
        TESTS,
        K,
    ),
    (
        "R15",
        "the votes are cut down to one per saved answer",
        RR,
        "        return votes\n\n    def reading_model",
        "        saved_count = len([a for a in (saved or []) if isinstance(a, dict)])\n"
        "        while votes and sum(votes.values()) > saved_count:\n"
        "            votes[max(votes, key=votes.get)] -= 1\n"
        "        return votes\n\n    def reading_model",
        TESTS,
        K,
    ),
    (
        "V1",
        "the label is the model asked for, not the one that answered",
        SV,
        '            run.keep_answers(json_data["answers"], reply_model)\n',
        '            run.keep_answers(json_data["answers"], MAIN_MODEL)\n',
        TESTS,
        K,
    ),
    (
        "V2",
        "an unnamed reply is recorded as the model asked for",
        SV,
        '                step_run.mark_reply_received(getattr(response, "model", None))\n',
        '                step_run.mark_reply_received(getattr(response, "model", None) or main_model)\n',
        TESTS,
        K,
    ),
    (
        "V3",
        "the call is marked as left only after it returned",
        SV,
        "            if step_run is not None:\n                step_run.mark_call_left()\n"
        "            response = self.client.chat.completions.create(**request)\n",
        "            response = self.client.chat.completions.create(**request)\n"
        "            if step_run is not None:\n                step_run.mark_call_left()\n",
        TESTS,
        K,
    ),
    (
        "V4",
        "no step run is looked for at the provider call",
        SV,
        "        step_run = require_step_scope(task_type)\n",
        "        step_run = None\n",
        TESTS,
        K,
    ),
    (
        "V5",
        "the reply is never marked as received",
        SV,
        "            if step_run is not None:\n"
        '                step_run.mark_reply_received(getattr(response, "model", None))\n',
        "            pass\n",
        TESTS,
        K,
    ),
    (
        "V6",
        "a thrown-away try is not forgotten",
        SV,
        "            run.begin_attempt()\n",
        "",
        TESTS,
        K,
    ),
    (
        "V7",
        "a part's winning answer does not take the origin",
        SV,
        "                    if kept is answer:\n",
        "                    if False:\n",
        TESTS,
        K,
    ),
    (
        "V8",
        "an answer joined from two parts is not credited to the later part",
        SV,
        "                        run.add_origin(kept, chunk_model)\n",
        "                        pass\n",
        TESTS,
        K,
    ),
    (
        "V9",
        "a finding for a question that was not blank still reads found_writing",
        SV,
        "        self._note_recheck_used(run, found_writing=applied > 0)\n",
        "        self._note_recheck_used(run, found_writing=True)\n",
        TESTS,
        K,
    ),
    (
        "V10",
        "a re-check whose reply was read with no finding is not marked used",
        SV,
        "            self._note_recheck_used(run, found_writing=False)\n",
        "            pass\n",
        TESTS,
        K,
    ),
    (
        "V11",
        "a setting is read live at one site",
        SV,
        '        cap = config.get("ANSWER_BLANK_VERIFICATION_MAX_QUESTIONS")\n',
        '        cap = getattr(settings, "ANSWER_BLANK_VERIFICATION_MAX_QUESTIONS", 12)\n',
        TESTS,
        K,
    ),
    (
        "V12",
        "the single-call reading takes the settings again",
        SV,
        "        system_prompt = ANSWERS_EXTRACTION_PROMPT\n"
        "        run = reading_run if reading_run is not None else ReadingRun()\n"
        "        if config is None:\n"
        "            config = run.config or ReadingConfig.read()\n",
        "        system_prompt = ANSWERS_EXTRACTION_PROMPT\n"
        "        run = reading_run if reading_run is not None else ReadingRun()\n"
        "        config = ReadingConfig.read()\n",
        TESTS,
        K,
    ),
    (
        "V13",
        "the re-check's call is made inside the reading's scope",
        SV,
        "            with run.recheck.scope():\n",
        "            with run.reading.scope():\n",
        TESTS,
        K,
    ),
    (
        "V14",
        "a key inside the reply's own answers sets the model",
        SV,
        '            run.keep_answers(json_data["answers"], reply_model)\n',
        "            run.keep_answers(\n"
        '                json_data["answers"],\n'
        '                json_data["answers"][0].get("answers_model") or reply_model,\n'
        "            )\n",
        TESTS,
        K,
    ),
    (
        "C1",
        "a setting that shapes a reading is left out of its version",
        RC,
        '    "ANSWER_COMPLETENESS_ENFORCEMENT",\n'
        '    "ANSWER_EXTRACTION_SCHEMA_ENABLED",\n)\n\n#: ANSWER_* settings deliberately',
        '    "ANSWER_EXTRACTION_SCHEMA_ENABLED",\n)\n\n#: ANSWER_* settings deliberately',
        TESTS,
        K,
    ),
    (
        "C2",
        "the release is part of the version",
        RC,
        '        blob = json.dumps(self.values, separators=(",", ":"), default=str)\n',
        '        blob = json.dumps([self.values, self.release], separators=(",", ":"), default=str)\n',
        TESTS,
        K,
    ),
    (
        "C3",
        "the version is eight characters long",
        RC,
        "HASH_LENGTH = 12\n",
        "HASH_LENGTH = 8\n",
        TESTS,
        K,
    ),
    (
        "W1",
        "a stand-in word is allowed beside any model",
        WORDS,
        "        return result == model\n    return True\n",
        "        return True\n    return True\n",
        TESTS,
        K,
    ),
    (
        "E1",
        "extract_answer is not enforced",
        STEP,
        'ENFORCED_STEP_TASK_TYPES = frozenset({"extract_answer"})',
        "ENFORCED_STEP_TASK_TYPES = frozenset()",
        TESTS,
        K,
    ),
    (
        "S1",
        "the re-save of a re-read leaves the label out of the one UPDATE",
        SS,
        '                                "extraction_confidence",\n'
        "                                *READING_LABEL_FIELDS,\n",
        '                                "extraction_confidence",\n',
        TESTS,
        K,
    ),
    (
        "S2",
        "the edit route does not replace the label",
        SS,
        "            apply_reading_label(locked, reading_run, answers)\n",
        "",
        TESTS,
        K,
    ),
    (
        "S3",
        "the upload route does not label a first upload",
        SS,
        "                apply_reading_label(submission, reading_run, submission.answers)\n",
        "",
        TESTS,
        K,
    ),
    (
        "D1",
        "the constraint on the pair lets 'not_run' stand beside any model",
        M33,
        '                        ("answers_recheck_result", "not_run"),\n'
        '                        ("answers_recheck_model", "not_run"),\n',
        '                        ("answers_recheck_result", "not_run"),\n',
        TESTS,
        F,
    ),
]

#: The failing test each mutant must be caught by (written before any run).
EXPECTED = {
    "R1": "test_a_placeholder_has_no_vote",
    "R2": "test_an_answer_the_check_dropped_has_no_vote",
    "R3": "test_an_answer_repeated_in_a_reply_votes_once",
    "R4": "test_an_answer_of_two_parts_counts_for_each_parts_model",
    "R5": "test_the_spelling_of_a_question_number_does_not_hide_an_origin",
    "R6": "test_no_call_left_is_not_run_beside_not_run",
    "R7": "test_a_reply_received_and_then_lost_keeps_the_providers_name",
    "R8": "test_a_reply_received_and_used_is_confirmed_blank",
    "R9": "test_the_values_come_from_what_the_run_kept",
    "R10": "test_a_new_attempt_forgets_every_origin",
    "R11": "test_a_new_attempt_forgets_the_recheck",
    "R12": "test_a_reply_cannot_be_used_before_it_was_received",
    "R13": "test_a_joined_answer_can_tip_the_label_to_the_later_model",
    "R14": "test_two_parts_by_the_same_model_count_once",
    "R15": "test_the_sum_of_votes_may_exceed_the_number_of_saved_answers",
    "V1": "test_the_model_asked_for_is_not_the_label",
    "V2": "test_an_unnamed_reply_is_unknown_not_the_model_asked_for",
    "V3": "test_the_call_is_marked_before_it_leaves",
    "V4": "test_an_enforced_task_type_with_no_scope_fails_before_the_provider",
    "V5": "test_a_named_reply_marks_the_call_and_the_reply",
    "V6": "test_the_last_try_degrades_and_still_counts_only_itself",
    "V7": "test_an_answer_joined_across_two_parts_counts_for_each_models_part",
    "V8": "test_an_answer_joined_across_two_parts_counts_for_each_models_part",
    "V9": "test_a_finding_for_a_question_that_was_not_blank_is_not_applied",
    "V10": "test_a_reply_read_and_no_finding_applied_is_confirmed_blank",
    "V11": "test_no_function_of_the_reading_reads_settings",
    "V12": "test_the_settings_are_read_once_for_the_whole_extraction",
    "V13": "test_a_finding_that_is_applied_is_found_writing",
    "V14": "test_a_key_inside_the_replys_own_text_never_reaches_a_label",
    "C1": "test_each_of_the_six_settings_changes_the_version",
    "C2": "test_the_release_is_not_part_of_the_version",
    "C3": "test_the_version_is_rcfg_and_twelve_hex_characters",
    "W1": "test_a_stand_in_word_beside_any_other_word_is_refused",
    "E1": "test_in_slice_2_only_the_reading_is_enforced",
    "S1": "test_a_re_read_replaces_the_label_in_the_one_update",
    "S2": "test_an_edit_is_a_new_reading_and_replaces_the_label_in_one_update",
    "S3": "test_a_first_upload_leaves_a_complete_label_in_the_one_insert",
    "D1": "test_every_pair_the_table_forbids_is_refused_by_the_database",
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
