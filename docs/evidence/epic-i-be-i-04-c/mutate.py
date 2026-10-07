"""BE-I-04 slice C: deliberate breaks, and the test that must catch each.

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

HERE = pathlib.Path("docs/evidence/epic-i-be-i-04-c")
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))

RUN = "ai_processor/grading_run.py"
SVC = "ai_processor/services.py"
STU = "students/services.py"
META = "audit/metadata.py"
EMIT = "audit/emitter.py"
TASKS = "assignments/tasks.py"
VIEWS = "students/views.py"
SERIALIZERS = "students/serializers.py"
MIGRATION = "students/migrations/0031_submission_grading_label.py"

TESTS = [
    "ai_processor.tests_grading_run_label",
    "ai_processor.tests_grading_run_pipeline",
    "students.tests_grading_label_written",
    "students.tests_grading_label_routes",
    "students.tests_grading_label_not_exposed",
    # The delta after verification (2026-10-07): the Checker's tests.
    "ai_processor.tests_grading_run_checker",
    "students.tests_grading_label_end_to_end",
]
ROW_TESTS = ["students.tests_grading_label_migration"]
K = "keepdb"

KEEP_REUSED = (
    "                run.keep_reused(\n"
    "                    None if first_model == grading_cache.UNNAMED_MODEL else first_model\n"
    "                )\n"
)

KEEP_BATCH = (
    "                if run is not None:\n"
    "                    if override_model is not None:\n"
    "                        run.keep_second_opinion(batch_model)\n"
    "                    else:\n"
    "                        run.keep_answers(batch_model, len(evaluations))\n"
)
BEFORE_EVIDENCE = (
    "                is_final_attempt = (\n"
    "                    attempt == batch_attempts - 1 and override_model is None\n"
    "                )\n"
)
SECOND_PAIRS = (
    "            pairs = self._pair_question_with_answers(\n"
    "                selected_questions, selected_answers\n"
    "            )\n"
)

#: (name, what, file, old, new, tests, kind). `old` and `new` are one text
#: each, or two tuples of the same length for a break that needs more than
#: one edit of the file (every `old` is found once in the unbroken file).
MUTANTS = [
    (
        "R1",
        "the summary call votes for the grade's model",
        RUN,
        "votes = Counter(self.fresh_answers + self.reused_answers)",
        "votes = Counter(self.fresh_answers + self.reused_answers + self.other_calls)",
        TESTS,
        K,
    ),
    (
        "R2",
        "reused answers do not vote",
        RUN,
        "votes = Counter(self.fresh_answers + self.reused_answers)",
        "votes = Counter(self.fresh_answers)",
        TESTS,
        K,
    ),
    (
        "R3",
        "the main model is not preferred on a tie",
        RUN,
        "        if main in named:\n            return main\n",
        "",
        TESTS,
        K,
    ),
    (
        "R4",
        "a tie goes to the last by alphabet",
        RUN,
        "named = sorted(model for model in leaders if model is not None)",
        "named = sorted((m for m in leaders if m is not None), reverse=True)",
        TESTS,
        K,
    ),
    (
        "R5",
        "an unnamed model wins a tie",
        RUN,
        "        if named:\n            return named[0]\n        return MODEL_UNKNOWN\n",
        "        if None in leaders or not named:\n            return MODEL_UNKNOWN\n"
        "        return named[0]\n",
        TESTS,
        K,
    ),
    (
        "R6",
        "only an all-backup run is flagged yes",
        RUN,
        "        if any(model in backups for model in models if model is not None):\n",
        "        if models and all(model in backups for model in models):\n",
        TESTS,
        K,
    ),
    (
        "R7",
        "an unnamed model is taken for the main model",
        RUN,
        "        if any(model is None or model != main for model in models):\n",
        "        if any(model is not None and model != main for model in models):\n",
        TESTS,
        K,
    ),
    (
        "R8",
        "a model on neither list is taken for the main model",
        RUN,
        "        if any(model is None or model != main for model in models):\n",
        "        if any(model is None for model in models):\n",
        TESTS,
        K,
    ),
    (
        "R9",
        "reused answers are left out of the backup flag",
        RUN,
        "everything = self.fresh_answers + self.other_calls + self.reused_answers",
        "everything = self.fresh_answers + self.other_calls",
        TESTS,
        K,
    ),
    (
        "R10",
        "the summary call is left out of the backup flag",
        RUN,
        "everything = self.fresh_answers + self.other_calls + self.reused_answers",
        "everything = self.fresh_answers + self.reused_answers",
        TESTS,
        K,
    ),
    (
        "R11",
        "second opinions are put into the backup flag",
        RUN,
        "everything = self.fresh_answers + self.other_calls + self.reused_answers",
        "everything = (\n            self.fresh_answers + self.other_calls + self.reused_answers\n"
        "            + self.second_opinions\n        )",
        TESTS,
        K,
    ),
    (
        "R12",
        "a run with no AI call reads no, not not_applicable",
        RUN,
        "            fallback_used = FALLBACK_NOT_APPLICABLE\n",
        "            fallback_used = FALLBACK_NO\n",
        TESTS,
        K,
    ),
    (
        "R13",
        "a new attempt keeps the answers of the last one",
        RUN,
        "        self.fresh_answers.clear()\n",
        "",
        TESTS,
        K,
    ),
    (
        "R14",
        "reused answers count as fresh calls for the measurement",
        RUN,
        "        fresh = self.fresh_answers + self.other_calls\n",
        "        fresh = self.fresh_answers + self.other_calls + self.reused_answers\n",
        TESTS,
        K,
    ),
    (
        "R15",
        "a model name is cut before it is classified",
        RUN,
        "        return model if isinstance(model, str) and model else None\n",
        "        return model[:64] if isinstance(model, str) and model else None\n",
        TESTS,
        K,
    ),
    (
        "R16",
        "the grade's model is not cut to the column",
        RUN,
        '"grading_model": self._grading_model()[:MODEL_MAX_LENGTH],',
        '"grading_model": self._grading_model(),',
        TESTS,
        K,
    ),
    (
        "R17",
        "the main model is read live, not from the run's reading",
        RUN,
        '        main = self.config.get("MAIN_MODEL")\n        backups = set(',
        '        main = __import__("ai_processor.services", fromlist=["x"]).MAIN_MODEL\n'
        "        backups = set(",
        TESTS,
        K,
    ),
    (
        "R18",
        'an "@" in a model name is kept in the audit lists',
        RUN,
        '    return model.replace("@", "(at)")[:AUDIT_ITEM_MAX_LENGTH]\n',
        "    return model[:AUDIT_ITEM_MAX_LENGTH]\n",
        TESTS,
        K,
    ),
    (
        "R19",
        "an unnamed model is an empty item in the audit lists",
        RUN,
        "    if not isinstance(model, str) or not model:\n        return MODEL_UNKNOWN\n",
        '    if not isinstance(model, str) or not model:\n        return ""\n',
        TESTS,
        K,
    ),
    (
        "R20",
        "the label's release is read live",
        RUN,
        '"grading_release": self.config.release,',
        '"grading_release": GradingConfig.read().release,',
        TESTS,
        K,
    ),
    (
        "S1",
        "an attempt does not start from nothing",
        SVC,
        "        run.begin_attempt()\n",
        "",
        TESTS,
        K,
    ),
    (
        "S2",
        "a short paper's reply is not recorded",
        SVC,
        "            run.keep_answers(model_name, len(evaluations))\n",
        "",
        TESTS,
        K,
    ),
    (
        "S3",
        "a short paper's reply votes once, not once per answer",
        SVC,
        "            run.keep_answers(model_name, len(evaluations))\n",
        "            run.keep_answers(model_name, 1)\n",
        TESTS,
        K,
    ),
    (
        "S4",
        "a part's reply is not recorded",
        SVC,
        "                        run.keep_answers(batch_model, len(evaluations))\n",
        "                        pass\n",
        TESTS,
        K,
    ),
    (
        "S5",
        "a part's reply votes once, not once per answer",
        SVC,
        "                        run.keep_answers(batch_model, len(evaluations))\n",
        "                        run.keep_answers(batch_model, 1)\n",
        TESTS,
        K,
    ),
    (
        "S6",
        "a second opinion is recorded as the grader's own answer",
        SVC,
        "                    if override_model is not None:\n"
        "                        run.keep_second_opinion(batch_model)\n",
        "                    if False:\n"
        "                        run.keep_second_opinion(batch_model)\n",
        TESTS,
        K,
    ),
    (
        "S7",
        "the summary call is not recorded",
        SVC,
        "                if run is not None:\n                    run.keep_call(model_name)\n",
        "",
        TESTS,
        K,
    ),
    ("S8", "a reused answer is not recorded", SVC, KEEP_REUSED, "", TESTS, K),
    (
        "S9",
        'a reused answer whose model was not named is recorded as "llm"',
        SVC,
        KEEP_REUSED,
        "                run.keep_reused(first_model)\n",
        TESTS,
        K,
    ),
    (
        "S10",
        "the fixed-rule switch is read live",
        SVC,
        '_grading_setting(run, "GRADING_DETERMINISTIC_OBJECTIVE")',
        'getattr(settings, "GRADING_DETERMINISTIC_OBJECTIVE", True)',
        TESTS,
        K,
    ),
    (
        "S11",
        "the second-opinion switch is read live",
        SVC,
        '_grading_setting(run, "GRADING_SECOND_OPINION_ENABLED")',
        'getattr(settings, "GRADING_SECOND_OPINION_ENABLED", True)',
        TESTS,
        K,
    ),
    (
        "S12",
        "the image limit is read live",
        SVC,
        '_grading_setting(run, "GRADING_MAX_IMAGES_PER_CALL")',
        'getattr(settings, "GRADING_MAX_IMAGES_PER_CALL", 5)',
        TESTS,
        K,
    ),
    (
        "S13",
        "an answer marked as reused is stored again",
        SVC,
        '                evaluation.get("from_cache")\n'
        '                or evaluation.get("graded_by") == "deterministic"\n',
        '                evaluation.get("graded_by") == "deterministic"\n',
        TESTS,
        K,
    ),
    (
        "T1",
        "the label is not among the fields the grading save writes",
        STU,
        "    *LABEL_FIELDS,\n",
        "",
        TESTS,
        K,
    ),
    (
        "T2",
        "the grading service is not handed the run",
        STU,
        "            processing_task_id=processing_task_id,\n            run=run,\n        )\n",
        "            processing_task_id=processing_task_id,\n        )\n",
        TESTS,
        K,
    ),
    (
        "T3",
        "the save takes the label from another run",
        STU,
        "        _populate_and_save_grade(submission, grading, processing_task_id, run)\n",
        "        _populate_and_save_grade(submission, grading, processing_task_id)\n",
        TESTS,
        K,
    ),
    (
        "T4",
        "the label is written by a second UPDATE as well",
        STU,
        "    submission._grading_run = run\n",
        "    submission._grading_run = run\n"
        "    submission.save(update_fields=list(LABEL_FIELDS))\n",
        TESTS,
        K,
    ),
    (
        "T5",
        "the audit entry has no lists of models",
        STU,
        "            **run.audit_models(),\n",
        "",
        TESTS,
        K,
    ),
    (
        "T6",
        "the audit entry has no word for the fresh calls",
        STU,
        '            "fresh_backup_used": run.fresh_backup_used(),\n',
        "",
        TESTS,
        K,
    ),
    (
        "T7",
        "the audit entry's model is not the label's",
        STU,
        '        grading_model = submission.grading_model.replace("@", "(at)")[:128]\n',
        "        grading_model = grading_model\n",
        TESTS,
        K,
    ),
    (
        "T8",
        "the audit entry has no settings version",
        STU,
        '            "grading_config_version": submission.grading_config_version,\n',
        "",
        TESTS,
        K,
    ),
    (
        "T9",
        "the instance grade_engine returns does not carry the run",
        STU,
        "    submission._grading_run = run\n",
        "",
        TESTS,
        K,
    ),
    (
        "T10",
        "the founder's sentence is gone from the function that writes the label",
        STU,
        "    FIRST FORM OF THE GRADING RECORD. A later stage improves on it: a table\n",
        "    This is the grading record: a table\n",
        TESTS,
        K,
    ),
    (
        "A1",
        "one of the lists is not permitted on the grading entry",
        META,
        '            "models_reused",\n',
        "",
        TESTS,
        K,
    ),
    (
        "A2",
        "an unknown model is counted as a zero in the backup rate",
        EMIT,
        '                elif fresh == "unknown":\n'
        '                    audit_metrics.distribution("model_unknown_rate", 1.0)\n',
        '                elif fresh == "unknown":\n'
        '                    audit_metrics.distribution("model_fallback_rate", 0.0)\n',
        TESTS,
        K,
    ),
    (
        "A3",
        "a grading with no fresh call gives a sample",
        EMIT,
        '                # "no_fresh_call": no sample.\n',
        "                else:\n"
        '                    audit_metrics.distribution("model_fallback_rate", 0.0)\n',
        TESTS,
        K,
    ),
    (
        "A4",
        "the entry's one model overrides the word for the fresh calls",
        EMIT,
        "                model = None\n            else:\n",
        '                model = metadata.get("model")\n            else:\n',
        TESTS,
        K,
    ),
    (
        "F1",
        "the formatting job saves the whole row",
        TASKS,
        "        # read back over a grading that landed meanwhile.\n"
        "        with cancellable_final_save(processing_task_id):\n"
        '            submission.save(update_fields=["formatted_grade"])\n',
        "        # read back over a grading that landed meanwhile.\n"
        "        with cancellable_final_save(processing_task_id):\n"
        "            submission.save()\n",
        TESTS,
        K,
    ),
    (
        "M1",
        "a teacher's manual change writes a label column",
        VIEWS,
        "        submission.needs_review = False\n\n"
        "        # Update the formatted grade since the score/feedback changed\n\n"
        "        submission.save(\n            update_fields=[\n",
        "        submission.needs_review = False\n"
        '        submission.grading_model = "manual"\n\n'
        "        # Update the formatted grade since the score/feedback changed\n\n"
        "        submission.save(\n            update_fields=[\n"
        '                "grading_model",\n',
        TESTS,
        K,
    ),
    (
        "X1",
        "a serializer of the submission exposes a label column",
        SERIALIZERS,
        '        fields = [\n            "id",\n            "score",\n        ]\n',
        '        fields = [\n            "id",\n            "score",\n'
        '            "grading_model",\n        ]\n',
        TESTS,
        K,
    ),
    (
        "E1",
        "a third place emits the grading entry",
        TASKS,
        '            print(f"Assignment saved: {index + 1}/{submissions_count}")\n',
        "            emit_grading_completed(submission, actor=user, before=None)\n"
        '            print(f"Assignment saved: {index + 1}/{submissions_count}")\n',
        TESTS,
        K,
    ),
    (
        "V1",
        "the migration's way back spoils the grade (the reverse half)",
        MIGRATION,
        "                max_length=64,\n            ),\n        ),\n    ]\n",
        "                max_length=64,\n            ),\n        ),\n"
        "        migrations.RunSQL(\n"
        '            "SELECT 1",\n'
        '            reverse_sql="UPDATE students_studentsubmission SET score = 0",\n'
        "        ),\n    ]\n",
        ROW_TESTS,
        "fresh",
    ),
    # ---- the delta after verification (2026-10-07) ------------------------
    (
        "A5",
        "a grading where a backup answered gives the unknown rate no sample",
        EMIT,
        '                    audit_metrics.distribution("model_fallback_rate", 1.0)\n'
        '                    audit_metrics.distribution("model_unknown_rate", 0.0)\n'
        '                elif fresh == "no":\n',
        '                    audit_metrics.distribution("model_fallback_rate", 1.0)\n'
        '                elif fresh == "no":\n',
        TESTS,
        K,
    ),
    (
        "S14",
        "a part's reply is counted before its evidence check (a rejected reply stays)",
        SVC,
        (BEFORE_EVIDENCE, KEEP_BATCH),
        (
            BEFORE_EVIDENCE
            + "                if run is not None and override_model is None:\n"
            "                    run.keep_answers(\n"
            "                        self._response_model_name(response), len(evaluations)\n"
            "                    )\n",
            "                if run is not None and override_model is not None:\n"
            "                    run.keep_second_opinion(batch_model)\n",
        ),
        TESTS,
        K,
    ),
    (
        "S15",
        "one site reads the evidence mode without the run",
        SVC,
        "                effective_mode = self._evidence_mode(run)\n",
        "                effective_mode = self._evidence_mode()\n",
        TESTS,
        K,
    ),
    (
        "S16",
        "a reused answer is counted as the main model's",
        SVC,
        KEEP_REUSED,
        "                run.keep_reused(MAIN_MODEL)\n",
        TESTS,
        K,
    ),
    (
        "S17",
        "the second-opinion model is recorded when it is asked, not when its reply is kept",
        SVC,
        (SECOND_PAIRS, KEEP_BATCH),
        (
            SECOND_PAIRS + "            if run is not None:\n"
            "                run.keep_second_opinion(second_model)\n",
            "                if run is not None and override_model is None:\n"
            "                    run.keep_answers(batch_model, len(evaluations))\n",
        ),
        TESTS,
        K,
    ),
    (
        "R21",
        "the rate's word passes over an unnamed model (a second, differing copy of the rule)",
        RUN,
        "        return self._backup_used(fresh)\n",
        "        return self._backup_used([m for m in fresh if m is not None])\n",
        TESTS,
        K,
    ),
]

#: The failing test each mutant must produce. Written before any run.
EXPECTED = {
    "R1": "test_the_summary_call_does_not_vote",
    "R2": "test_fresh_and_reused_answers_are_counted_together",
    "R3": "test_a_two_way_tie_goes_to_the_main_model",
    "R4": "test_a_tie_without_the_main_model_goes_to_the_first_by_alphabet",
    "R5": "test_unknown_loses_a_tie_to_a_named_model",
    "R6": "test_any_backup_answer_is_yes",
    "R7": "test_main_with_an_unnamed_model_is_unknown",
    "R8": "test_main_with_a_model_on_neither_list_is_unknown",
    "R9": "test_a_reused_answer_first_made_by_a_backup_is_yes",
    "R10": "test_a_backup_summary_call_alone_is_yes",
    "R11": "test_it_is_in_neither_the_model_nor_the_flag",
    "R12": "test_no_ai_call_and_nothing_reused_is_not_applicable",
    "R13": "test_a_new_attempt_starts_from_nothing",
    "R14": "test_a_reused_backup_answer_does_not_make_the_fresh_calls_yes",
    "R15": "test_a_backup_name_longer_than_an_audit_item_is_still_yes",
    "R16": "test_a_long_model_name_is_cut_to_the_column_not_refused",
    "R17": "test_the_lists_are_read_from_the_runs_reading_not_live",
    "R18": "test_a_model_name_shaped_like_an_address_is_not_dropped_silently",
    "R19": "test_an_unnamed_model_is_the_explicit_word_unknown",
    "R20": "test_the_versions_and_the_release_come_from_the_runs_reading",
    "S1": "test_a_whole_attempt_that_failed_leaves_nothing_behind",
    "S2": "test_marked_by_a_backup_model",
    "S3": "test_one_call_marking_three_answers_votes_three_times",
    "S4": "test_a_backup_marking_one_part_is_seen",
    "S5": "test_the_part_with_the_most_answers_names_the_model",
    "S6": "test_its_model_is_in_its_own_list_and_nowhere_else",
    "S7": "test_a_backup_summary_call_is_flagged_but_does_not_name_the_model",
    "S8": "test_a_wholly_reused_paper_names_the_model_that_first_answered",
    "S9": "test_a_reused_answer_whose_first_model_was_not_named_is_unknown",
    "S10": "test_fixed_rule_marking_stays_on_for_a_run_that_started_with_it_on",
    "S11": "test_a_second_opinion_switched_on_mid_run_is_not_asked_for",
    "S12": "test_nothing_outside_the_settings_version_module_reads_one",
    "S13": "test_the_store_skips_an_answer_marked_as_reused",
    "T1": "test_the_six_columns_after_a_grading_by_the_main_model",
    "T2": "test_the_grading_service_is_handed_a_run",
    "T3": "test_a_backup_model_is_flagged",
    "T4": "test_the_label_and_the_score_are_one_update",
    "T5": "test_it_carries_the_three_lists_of_models",
    "T6": "test_an_entry_is_stored_with_the_lists",
    "T7": "test_its_model_is_the_labels_model",
    "T8": "test_it_carries_the_settings_version_and_the_strictness",
    "T9": "test_what_grade_engine_returns_carries_the_run",
    "T10": "test_the_function_that_writes_the_label_says_it",
    "A1": "test_the_three_lists_are_permitted_for_this_entry_and_pass_validation",
    "A2": "test_unknown_is_counted_apart_and_not_in_the_rate",
    "A3": "test_no_fresh_call_gives_no_sample",
    "A4": "test_the_old_single_model_does_not_override_the_key",
    "F1": "test_a_score_and_label_saved_meanwhile_are_not_written_back_over",
    "M1": "test_a_teachers_change_of_the_score_does_not_touch_the_label",
    "X1": "test_no_submission_serializer_has_a_label_field",
    "E1": "test_the_entry_is_emitted_in_exactly_the_two_callers",
    "V1": "test_a_row_made_before_0031_reads_the_placeholder_after_it",
    "A5": "test_yes_no_and_unknown_each_give_one_unknown_sample",
    "S14": "test_a_backups_reply_rejected_for_its_quote_leaves_nothing",
    "S15": "test_no_call_inside_the_grading_service_drops_the_run",
    "S16": "test_a_backup_answers_then_a_second_student_reuses_it",
    "S17": "test_a_second_opinion_that_failed_leaves_no_model",
    "R21": "test_every_mix_of_fresh_calls",
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
