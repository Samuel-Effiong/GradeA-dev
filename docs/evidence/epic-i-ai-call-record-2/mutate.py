"""AI-call record, slice 2: deliberate breaks, and the test that must catch each.

Each mutant is applied, its test modules run, the failing tests are
recorded, the file is restored. Judged three ways (rule 18, SM 2026-10-05):
  SURVIVED  the inner run exits 0;
  KILLED    non-zero exit, the run's own "Ran" line, no test module that
            failed to load, and the test named in EXPECTED for that mutant
            among the failing tests; for a mutant in REASONS, each named
            test's own FAIL/ERROR block also holds its written fragment
            (rule 22);
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

The "fresh" mutants (the migrations, D1 to D7) are last: each builds its own test
database from the mutated migration, and the gate script drops that
database afterwards.
"""

# flake8: noqa: E501,B950
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
#: A subset re-run (SM ruling 2026-10-14, after the laptop suspended during D3 of the
#: b9a222cb battery): MUT_ONLY names mutant ids, comma- or space-separated. An unknown
#: id STOPS the run before anything is mutated; every anchor is still checked for all
#: mutants. A subset run writes, by default, to its own results file and log folder
#: (never over the full battery's) and its results file names the subset.
ONLY = [mid for mid in os.environ.get("MUT_ONLY", "").replace(",", " ").split() if mid]
_SUBSET = "_".join(ONLY)
OUT = pathlib.Path(
    os.environ.get(
        "MUT_RESULTS",
        HERE
        / (
            f"mutation_results_subset_{_SUBSET}.json"
            if ONLY
            else "mutation_results.json"
        ),
    )
)
LOGS = pathlib.Path(
    os.environ.get(
        "MUT_LOGS", HERE / (f"mutant_logs_subset_{_SUBSET}" if ONLY else "mutant_logs")
    )
)

RR = "ai_processor/reading_run.py"
RC = "ai_processor/reading_config.py"
SV = "ai_processor/services.py"
STEP = "ai_processor/step_run.py"
WORDS = "students/step_label.py"
SS = "students/services.py"
M32 = "students/migrations/0032_submission_answers_label.py"
M33 = "students/migrations/0033_submission_answers_recheck_pair_check.py"
M34 = "students/migrations/0034_submission_answers_recheck_pair_validate.py"

TESTS = [
    "ai_processor.tests_reading_config",
    "ai_processor.tests_reading_run",
    "ai_processor.tests_reading_pipeline",
    "ai_processor.tests_step_run_scope",
    "ai_processor.tests_step_run_adopted",
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
        "X1",
        "a refused mark still leaves 'received' set (the Checker's XS14)",
        STEP,
        "        if not self.call_left:\n"
        '            raise RuntimeError("a reply cannot be received before the call left")\n'
        "        self.reply_received = True\n",
        "        self.reply_received = True\n"
        "        if not self.call_left:\n"
        '            raise RuntimeError("a reply cannot be received before the call left")\n',
        TESTS,
        K,
    ),
    (
        "X2",
        "the single-call reading opens its scope by hand, not with 'with'",
        SV,
        "            with run.reading.scope():\n                run.reading.begin_call()\n",
        "            _scope = run.reading.scope()\n"
        "            _scope.__enter__()\n"
        "            if True:\n                run.reading.begin_call()\n",
        TESTS,
        K,
    ),
    (
        "X3",
        "a scope is not closed when its body raises",
        STEP,
        "            _CURRENT.reset(token)\n",
        "            pass\n",
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
        '    "OR (\\"answers_recheck_result\\" = \'not_run\' AND \\"answers_recheck_model\\" = \'not_run\') "\n',
        '    "OR (\\"answers_recheck_result\\" = \'not_run\') "\n',
        TESTS,
        F,
    ),
    (
        "D2",
        "the database lets a result word outside the five stand beside a real model",
        M33,
        "    \"('unlabelled', 'not_run', 'failed', 'confirmed_blank', 'found_writing') \"\n",
        "    \"('unlabelled', 'not_run', 'failed', 'confirmed_blank', 'found_writing', 'maybe') \"\n",
        TESTS,
        F,
    ),
    (
        "D3",
        "the second step does not validate the constraint",
        M34,
        '            sql=\'ALTER TABLE "students_studentsubmission" VALIDATE CONSTRAINT "answers_recheck_pair_allowed"\',\n',
        '            sql="SELECT 1",\n',
        TESTS,
        F,
    ),
    (
        "D4",
        "the first migration has no lock timeout",
        M33,
        "SET_LOCK_TIMEOUT = \"SET LOCAL lock_timeout = '5s'\"\n",
        'SET_LOCK_TIMEOUT = "SELECT 1"\n',
        TESTS,
        F,
    ),
    (
        # Added 2026-10-14 (Builder 58, SM ruling on the Verifier's N2):
        # 0032's own lock timeout, written before any run of it.
        "D5",
        "the column migration has no lock timeout",
        M32,
        "        migrations.RunSQL(SET_LOCK_TIMEOUT, reverse_sql=migrations.RunSQL.noop),\n",
        "",
        TESTS,
        F,
    ),
    (
        # Added 2026-10-14 (Builder 58, SM ruling on the Verifier's N2 of
        # 74fb0e89): 0032's closing operation, which sets the timeout before a
        # rollback's DROP COLUMNs. Written before any run of them.
        "D6",
        "the column migration's rollback has no lock timeout (the closing operation dropped)",
        M32,
        "        migrations.RunSQL(migrations.RunSQL.noop, reverse_sql=SET_LOCK_TIMEOUT),\n",
        "",
        TESTS,
        F,
    ),
    (
        "D7",
        "the closing operation's reverse sets no lock timeout",
        M32,
        "reverse_sql=SET_LOCK_TIMEOUT)",
        "reverse_sql=migrations.RunSQL.noop)",
        TESTS,
        F,
    ),
]

#: The failing tests each mutant must be caught by (written before any run): EVERY test of the eight modules
#: reckoned to fail under it, controls included, each traced by reading against all 175 tests (the Checker's pre-read
#: of 65577225 corrected the first lists). A name occurs once in the eight modules (three duplicated names were
#: renamed), so a bare-name match is exact. Two mutants, X2 and X3, leave a scope open in the process: tests that
#: only see the leak of an EARLIER test (the scope tests that expect none open, the four tests of
#: ACallWithNoScopeFailsLoudly and the enforced-with-no-scope test) fail or not by the order of the battery, so they are
#: NOT named; the tests named fail by their own call in any order. Any of those that fails in the battery is
#: recorded as also_failed_not_expected and explained in its own commit. Mutant V11 is caught by two tests (the
#: settings scan and the "settings" name walk). D1 to D3 build a fresh database from the mutated migration.
EXPECTED = {
    "R1": [
        "test_a_placeholder_has_no_vote",
        "test_a_placeholder_has_no_vote_through_the_real_extraction",
        "test_the_last_try_degrades_and_still_counts_only_itself",
    ],
    "R2": [
        "test_an_answer_the_check_dropped_has_no_vote",
        "test_no_saved_answer_with_a_reply_behind_it_is_unknown",
        "test_an_answer_the_check_dropped_has_no_vote_from_the_part_that_made_it",
    ],
    "R3": [
        "test_an_answer_repeated_in_a_reply_votes_once",
        "test_a_later_reply_that_wins_a_question_replaces_the_earlier_origin",
        "test_an_answer_joined_across_two_parts_counts_for_each_models_part",
        "test_an_answer_the_check_dropped_has_no_vote_from_the_part_that_made_it",
    ],
    "R4": [
        "test_an_answer_of_two_parts_counts_for_each_parts_model",
        "test_a_joined_answer_can_tip_the_label_to_the_later_model",
        "test_the_sum_of_votes_may_exceed_the_number_of_saved_answers",
        "test_an_answer_joined_across_two_parts_counts_for_each_models_part",
    ],
    "R5": [
        "test_the_spelling_of_a_question_number_does_not_hide_an_origin",
        "test_a_label_written_as_the_assignment_spells_the_number_still_votes",
    ],
    "R6": [
        "test_no_call_left_is_not_run_beside_not_run",
        "test_a_new_attempt_forgets_the_recheck",
        "test_a_one_call_reading_is_labelled_with_the_named_model",
        "test_a_key_inside_the_replys_own_text_never_reaches_a_label",
        "test_a_cancellation_before_the_call_is_not_run_though_it_was_caught",
        "test_every_way_the_recheck_does_not_run_is_not_run",
        "test_a_wallet_that_refuses_before_the_call_is_not_run",
        "test_a_first_upload_leaves_a_complete_label_in_the_one_insert",
        "test_an_edit_is_a_new_reading_and_replaces_the_label_in_one_update",
    ],
    "R7": [
        "test_a_reply_received_and_then_lost_keeps_the_providers_name",
        "test_a_reply_received_and_used_is_confirmed_blank",
        "test_a_reply_received_and_used_with_a_finding_is_found_writing",
        "test_the_values_come_from_what_the_run_kept",
        "test_a_finding_that_is_applied_is_found_writing",
        "test_a_reply_read_and_no_finding_applied_is_confirmed_blank",
        "test_a_finding_for_a_question_that_was_not_blank_is_not_applied",
        "test_a_reply_that_could_not_be_read_is_failed_with_its_name",
        "test_the_rechecks_own_model_setting_is_the_model_it_is_asked_for",
        "test_a_reply_received_and_then_lost_to_the_credit_step_is_failed_named",
        "test_the_rechecks_pair_reaches_the_row",
    ],
    "R8": [
        "test_a_reply_received_and_used_is_confirmed_blank",
        "test_a_reply_received_and_used_with_a_finding_is_found_writing",
        "test_the_values_come_from_what_the_run_kept",
        "test_a_finding_that_is_applied_is_found_writing",
        "test_a_reply_read_and_no_finding_applied_is_confirmed_blank",
        "test_a_finding_for_a_question_that_was_not_blank_is_not_applied",
        "test_the_rechecks_own_model_setting_is_the_model_it_is_asked_for",
        "test_the_rechecks_pair_reaches_the_row",
        "test_an_unnamed_reply_that_was_used_is_unknown",
        "test_an_unnamed_reply_that_was_used_is_unknown_through_the_real_recheck",
    ],
    "R9": [
        "test_the_values_come_from_what_the_run_kept",
        "test_a_one_call_reading_is_labelled_with_the_named_model",
        "test_a_finding_that_is_applied_is_found_writing",
        "test_a_reply_read_and_no_finding_applied_is_confirmed_blank",
        "test_a_finding_for_a_question_that_was_not_blank_is_not_applied",
        "test_a_call_that_left_and_raised_is_failed_unknown",
        "test_a_reply_that_could_not_be_read_is_failed_with_its_name",
        "test_an_unnamed_reply_that_could_not_be_read_is_failed_unknown",
        "test_an_unnamed_reply_that_was_used_is_unknown_through_the_real_recheck",
        "test_the_rechecks_own_model_setting_is_the_model_it_is_asked_for",
        "test_a_cancellation_before_the_call_is_not_run_though_it_was_caught",
        "test_every_way_the_recheck_does_not_run_is_not_run",
        "test_a_reply_received_and_then_lost_to_the_credit_step_is_failed_named",
        "test_a_wallet_that_refuses_before_the_call_is_not_run",
        "test_a_first_upload_leaves_a_complete_label_in_the_one_insert",
        "test_the_rechecks_pair_reaches_the_row",
        "test_a_recheck_that_failed_reaches_the_row_as_failed",
        "test_an_edit_is_a_new_reading_and_replaces_the_label_in_one_update",
    ],
    "R10": [
        "test_a_new_attempt_forgets_every_origin",
        "test_the_last_try_degrades_and_still_counts_only_itself",
    ],
    "R11": [
        "test_a_new_attempt_forgets_the_recheck",
    ],
    "R12": [
        "test_a_reply_cannot_be_used_before_it_was_received",
    ],
    "R13": [
        "test_an_answer_of_two_parts_counts_for_each_parts_model",
        "test_a_joined_answer_can_tip_the_label_to_the_later_model",
        "test_the_sum_of_votes_may_exceed_the_number_of_saved_answers",
        "test_an_answer_joined_across_two_parts_counts_for_each_models_part",
    ],
    "R14": [
        "test_two_parts_by_the_same_model_count_once",
    ],
    "R15": [
        "test_an_answer_of_two_parts_counts_for_each_parts_model",
        "test_a_joined_answer_can_tip_the_label_to_the_later_model",
        "test_the_sum_of_votes_may_exceed_the_number_of_saved_answers",
    ],
    "X1": [
        "test_a_refused_mark_changes_nothing",
    ],
    "X2": [
        "test_each_call_for_an_enforced_type_is_in_a_with_scope",
        "test_every_scope_call_is_a_with_item",
        "test_after_an_extraction_that_failed_no_scope_is_open",
        "test_after_a_cancellation_inside_the_call_no_scope_is_open",
        "test_after_a_failed_recheck_no_scope_is_open",
    ],
    "X3": [
        "test_after_the_scope_nothing_is_current",
        "test_the_scope_is_restored_after_an_exception",
        "test_the_scope_is_restored_after_an_exception_that_is_not_an_exception",
        "test_a_nested_scope_restores_the_outer_one",
        "test_an_exception_in_the_inner_scope_restores_the_outer_one",
        "test_two_steps_one_after_the_other_do_not_leak",
        "test_the_same_run_can_be_entered_again",
        "test_after_an_extraction_that_failed_no_scope_is_open",
        "test_after_a_cancellation_inside_the_call_no_scope_is_open",
        "test_after_a_failed_recheck_no_scope_is_open",
    ],
    "V1": [
        "test_a_one_call_reading_is_labelled_with_the_named_model",
        "test_the_model_asked_for_is_not_the_label",
        "test_a_provider_that_names_no_model_is_unknown_never_the_one_asked_for",
        "test_a_provider_name_equal_to_one_of_our_words_is_unknown",
        "test_a_key_inside_the_replys_own_text_never_reaches_a_label",
        "test_a_try_the_completeness_check_threw_away_leaves_nothing",
        "test_the_last_try_degrades_and_still_counts_only_itself",
        "test_a_placeholder_has_no_vote_through_the_real_extraction",
        "test_an_answer_the_check_dropped_has_no_vote_in_a_one_call_reading",
        "test_a_label_written_as_the_assignment_spells_the_number_still_votes",
        "test_a_finding_that_is_applied_is_found_writing",
        "test_a_reply_received_and_then_lost_to_the_credit_step_is_failed_named",
        "test_a_first_upload_leaves_a_complete_label_in_the_one_insert",
        "test_a_provider_that_names_no_model_leaves_unknown",
        "test_the_rechecks_pair_reaches_the_row",
        "test_a_re_read_replaces_the_label_in_the_one_update",
        "test_a_row_made_before_the_columns_existed_gets_its_first_label",
        "test_an_edit_is_a_new_reading_and_replaces_the_label_in_one_update",
    ],
    "V2": [
        "test_a_provider_that_names_no_model_is_unknown_never_the_one_asked_for",
        "test_an_unnamed_reply_is_unknown_not_the_model_asked_for",
        "test_an_unnamed_reply_that_could_not_be_read_is_failed_unknown",
        "test_an_unnamed_reply_that_was_used_is_unknown_through_the_real_recheck",
        "test_a_provider_that_names_no_model_leaves_unknown",
    ],
    "V3": [
        "test_the_call_is_marked_before_it_leaves",
        "test_a_call_that_raises_left_and_received_nothing",
        "test_a_call_that_left_and_raised_is_failed_unknown",
        "test_a_recheck_that_failed_reaches_the_row_as_failed",
    ],
    "V4": [
        "test_a_named_reply_marks_the_call_and_the_reply",
        "test_an_unnamed_reply_is_unknown_not_the_model_asked_for",
        "test_a_call_that_raises_left_and_received_nothing",
        "test_the_call_is_marked_before_it_leaves",
        "test_an_enforced_task_type_with_no_scope_fails_before_the_provider",
        "test_a_one_call_reading_is_labelled_with_the_named_model",
        "test_the_model_asked_for_is_not_the_label",
        "test_a_key_inside_the_replys_own_text_never_reaches_a_label",
        "test_a_try_the_completeness_check_threw_away_leaves_nothing",
        "test_the_last_try_degrades_and_still_counts_only_itself",
        "test_a_placeholder_has_no_vote_through_the_real_extraction",
        "test_an_answer_the_check_dropped_has_no_vote_in_a_one_call_reading",
        "test_a_label_written_as_the_assignment_spells_the_number_still_votes",
        "test_the_part_that_supplied_most_saved_answers_names_the_model",
        "test_an_answer_joined_across_two_parts_counts_for_each_models_part",
        "test_an_answer_the_check_dropped_has_no_vote_from_the_part_that_made_it",
        "test_a_part_read_again_after_unreadable_json_counts_the_good_reply",
        "test_a_finding_that_is_applied_is_found_writing",
        "test_a_reply_read_and_no_finding_applied_is_confirmed_blank",
        "test_a_finding_for_a_question_that_was_not_blank_is_not_applied",
        "test_a_call_that_left_and_raised_is_failed_unknown",
        "test_a_reply_that_could_not_be_read_is_failed_with_its_name",
        "test_an_unnamed_reply_that_could_not_be_read_is_failed_unknown",
        "test_an_unnamed_reply_that_was_used_is_unknown_through_the_real_recheck",
        "test_the_rechecks_own_model_setting_is_the_model_it_is_asked_for",
        "test_a_reply_received_and_then_lost_to_the_credit_step_is_failed_named",
        "test_a_first_upload_leaves_a_complete_label_in_the_one_insert",
        "test_the_rechecks_pair_reaches_the_row",
        "test_a_re_read_replaces_the_label_in_the_one_update",
        "test_a_row_made_before_the_columns_existed_gets_its_first_label",
        "test_an_edit_is_a_new_reading_and_replaces_the_label_in_one_update",
        "test_a_recheck_that_failed_reaches_the_row_as_failed",
    ],
    "V5": [
        "test_a_named_reply_marks_the_call_and_the_reply",
        "test_an_unnamed_reply_is_unknown_not_the_model_asked_for",
        "test_a_one_call_reading_is_labelled_with_the_named_model",
        "test_the_model_asked_for_is_not_the_label",
        "test_a_key_inside_the_replys_own_text_never_reaches_a_label",
        "test_a_try_the_completeness_check_threw_away_leaves_nothing",
        "test_the_last_try_degrades_and_still_counts_only_itself",
        "test_a_placeholder_has_no_vote_through_the_real_extraction",
        "test_an_answer_the_check_dropped_has_no_vote_in_a_one_call_reading",
        "test_a_label_written_as_the_assignment_spells_the_number_still_votes",
        "test_the_part_that_supplied_most_saved_answers_names_the_model",
        "test_an_answer_joined_across_two_parts_counts_for_each_models_part",
        "test_an_answer_the_check_dropped_has_no_vote_from_the_part_that_made_it",
        "test_a_part_read_again_after_unreadable_json_counts_the_good_reply",
        "test_a_finding_that_is_applied_is_found_writing",
        "test_a_reply_read_and_no_finding_applied_is_confirmed_blank",
        "test_a_finding_for_a_question_that_was_not_blank_is_not_applied",
        "test_a_reply_that_could_not_be_read_is_failed_with_its_name",
        "test_an_unnamed_reply_that_was_used_is_unknown_through_the_real_recheck",
        "test_the_rechecks_own_model_setting_is_the_model_it_is_asked_for",
        "test_a_reply_received_and_then_lost_to_the_credit_step_is_failed_named",
        "test_a_first_upload_leaves_a_complete_label_in_the_one_insert",
        "test_the_rechecks_pair_reaches_the_row",
        "test_a_re_read_replaces_the_label_in_the_one_update",
        "test_a_row_made_before_the_columns_existed_gets_its_first_label",
        "test_an_edit_is_a_new_reading_and_replaces_the_label_in_one_update",
    ],
    "V6": [
        "test_the_last_try_degrades_and_still_counts_only_itself",
    ],
    "V7": [
        "test_an_answer_joined_across_two_parts_counts_for_each_models_part",
        "test_an_answer_the_check_dropped_has_no_vote_from_the_part_that_made_it",
    ],
    "V8": [
        "test_an_answer_joined_across_two_parts_counts_for_each_models_part",
    ],
    "V9": [
        "test_a_finding_for_a_question_that_was_not_blank_is_not_applied",
    ],
    "V10": [
        "test_a_reply_read_and_no_finding_applied_is_confirmed_blank",
        "test_an_unnamed_reply_that_was_used_is_unknown_through_the_real_recheck",
        "test_the_rechecks_own_model_setting_is_the_model_it_is_asked_for",
    ],
    "V11": [
        "test_no_function_of_the_reading_reads_settings",
        "test_no_other_file_names_an_answer_setting",
    ],
    "V12": [
        "test_the_settings_are_read_once_for_the_whole_extraction",
    ],
    "V13": [
        "test_a_finding_that_is_applied_is_found_writing",
        "test_a_reply_read_and_no_finding_applied_is_confirmed_blank",
        "test_a_finding_for_a_question_that_was_not_blank_is_not_applied",
        "test_a_call_that_left_and_raised_is_failed_unknown",
        "test_a_reply_that_could_not_be_read_is_failed_with_its_name",
        "test_an_unnamed_reply_that_could_not_be_read_is_failed_unknown",
        "test_an_unnamed_reply_that_was_used_is_unknown_through_the_real_recheck",
        "test_the_rechecks_own_model_setting_is_the_model_it_is_asked_for",
        "test_a_reply_received_and_then_lost_to_the_credit_step_is_failed_named",
        "test_the_rechecks_pair_reaches_the_row",
        "test_a_recheck_that_failed_reaches_the_row_as_failed",
    ],
    "V14": [
        "test_a_key_inside_the_replys_own_text_never_reaches_a_label",
    ],
    "C1": [
        "test_the_six_settings_are_these_six",
        "test_each_of_the_six_settings_changes_the_version",
        "test_every_setting_starting_with_answer_is_decided",
        "test_the_settings_are_read_once_for_the_whole_extraction",
        "test_a_setting_changed_while_the_extraction_runs_is_not_seen",
        "test_the_label_names_the_version_the_extraction_began_with",
        "test_a_one_call_reading_is_labelled_with_the_named_model",
        "test_the_model_asked_for_is_not_the_label",
        "test_a_provider_that_names_no_model_is_unknown_never_the_one_asked_for",
        "test_a_provider_name_equal_to_one_of_our_words_is_unknown",
        "test_a_key_inside_the_replys_own_text_never_reaches_a_label",
        "test_the_reading_settings_version_and_release_are_the_runs",
        "test_a_try_the_completeness_check_threw_away_leaves_nothing",
        "test_the_last_try_degrades_and_still_counts_only_itself",
        "test_a_placeholder_has_no_vote_through_the_real_extraction",
        "test_an_answer_the_check_dropped_has_no_vote_in_a_one_call_reading",
        "test_a_label_written_as_the_assignment_spells_the_number_still_votes",
        "test_the_part_that_supplied_most_saved_answers_names_the_model",
        "test_an_answer_joined_across_two_parts_counts_for_each_models_part",
        "test_an_answer_the_check_dropped_has_no_vote_from_the_part_that_made_it",
        "test_a_part_read_again_after_unreadable_json_counts_the_good_reply",
        "test_the_prompt_version_is_the_one_every_part_was_sent",
        "test_a_finding_that_is_applied_is_found_writing",
        "test_a_reply_read_and_no_finding_applied_is_confirmed_blank",
        "test_a_finding_for_a_question_that_was_not_blank_is_not_applied",
        "test_a_call_that_left_and_raised_is_failed_unknown",
        "test_a_reply_that_could_not_be_read_is_failed_with_its_name",
        "test_an_unnamed_reply_that_could_not_be_read_is_failed_unknown",
        "test_an_unnamed_reply_that_was_used_is_unknown_through_the_real_recheck",
        "test_the_rechecks_own_model_setting_is_the_model_it_is_asked_for",
        "test_what_the_recheck_observed_never_reaches_a_label",
        "test_a_cancellation_before_the_call_is_not_run_though_it_was_caught",
        "test_every_way_the_recheck_does_not_run_is_not_run",
        "test_a_reply_received_and_then_lost_to_the_credit_step_is_failed_named",
        "test_a_wallet_that_refuses_before_the_call_is_not_run",
        "test_after_an_extraction_that_failed_no_scope_is_open",
        "test_after_a_cancellation_inside_the_call_no_scope_is_open",
        "test_after_a_failed_recheck_no_scope_is_open",
        "test_a_caller_that_passes_no_run_still_reads",
        "test_a_first_upload_leaves_a_complete_label_in_the_one_insert",
        "test_a_provider_that_names_no_model_leaves_unknown",
        "test_the_rechecks_pair_reaches_the_row",
        "test_a_recheck_that_failed_reaches_the_row_as_failed",
        "test_a_re_read_replaces_the_label_in_the_one_update",
        "test_a_row_made_before_the_columns_existed_gets_its_first_label",
        "test_an_extraction_that_fails_leaves_the_row_and_its_label_alone",
        "test_an_edit_is_a_new_reading_and_replaces_the_label_in_one_update",
        "test_an_edit_whose_reading_failed_keeps_the_old_label",
    ],
    "C2": [
        "test_the_release_is_not_part_of_the_version",
    ],
    "C3": [
        "test_the_version_is_rcfg_and_twelve_hex_characters",
        "test_a_one_call_reading_is_labelled_with_the_named_model",
        "test_a_first_upload_leaves_a_complete_label_in_the_one_insert",
    ],
    "W1": [
        "test_a_stand_in_word_beside_any_other_word_is_refused",
        "test_the_python_function_and_the_database_agree_on_every_combination",
    ],
    "E1": [
        "test_in_slice_2_only_the_reading_is_enforced",
        "test_an_enforced_task_type_with_no_scope_fails_before_the_provider",
        "test_slice_2_enforces_the_reading_and_only_it",
        "test_each_call_for_an_enforced_type_is_in_a_with_scope",
    ],
    "S1": [
        "test_a_re_read_replaces_the_label_in_the_one_update",
        "test_a_row_made_before_the_columns_existed_gets_its_first_label",
    ],
    "S2": [
        "test_an_edit_is_a_new_reading_and_replaces_the_label_in_one_update",
        "test_each_route_applies_the_label_from_its_run",
    ],
    "S3": [
        "test_a_first_upload_leaves_a_complete_label_in_the_one_insert",
        "test_a_provider_that_names_no_model_leaves_unknown",
        "test_the_rechecks_pair_reaches_the_row",
        "test_a_recheck_that_failed_reaches_the_row_as_failed",
        "test_a_re_read_replaces_the_label_in_the_one_update",
        "test_a_row_made_before_the_columns_existed_gets_its_first_label",
        "test_each_route_applies_the_label_from_its_run",
    ],
    "D1": [
        "test_every_pair_the_table_forbids_is_refused_by_the_database",
        "test_the_python_function_and_the_database_agree_on_every_combination",
        "test_a_new_row_with_a_forbidden_pair_is_refused_too",
        "test_a_row_made_before_0032_reads_the_placeholder_after_0033",
    ],
    "D2": [
        "test_a_result_word_outside_the_five_is_refused_beside_a_real_model",
        "test_the_python_function_and_the_database_agree_on_every_combination",
    ],
    "D3": [
        "test_the_constraint_is_validated_once_every_migration_has_run",
        "test_0034_validates_the_constraint_and_nothing_else",
        "test_a_row_made_before_0032_reads_the_placeholder_after_0033",
    ],
    "D4": [
        "test_0033_adds_the_one_constraint_not_valid_and_nothing_else",
    ],
    "D5": [
        "test_0032_sets_the_lock_timeout_both_ways_around_six_add_fields",
    ],
    "D6": [
        "test_0032_sets_the_lock_timeout_both_ways_around_six_add_fields",
    ],
    "D7": [
        "test_0032_sets_the_lock_timeout_both_ways_around_six_add_fields",
    ],
}

#: Rule 22 for the lock-timeout mutants (the Verifier's N1 on 74fb0e89): each named test must fail for the reason
#: written here, a fragment looked for inside that test's own FAIL/ERROR block. Written before any run, from
#: unittest's own messages: D5 leaves ['AddField'] * 6 + ['RunSQL'] against the expected
#: ['RunSQL'] + ['AddField'] * 6 + ['RunSQL'] (they differ at element 0); D6 leaves ['RunSQL'] + ['AddField'] * 6 (the
#: expected list is one longer); D7 passes the list and the closing forward, and fails at the closing reverse.
LIST_TEST = "test_0032_sets_the_lock_timeout_both_ways_around_six_add_fields"
REASONS = {
    "D5": {LIST_TEST: "First differing element 0:\n'AddField'\n'RunSQL'"},
    "D6": {
        LIST_TEST: "Second list contains 1 additional elements.\nFirst extra element 7:\n'RunSQL'"
    },
    "D7": {LIST_TEST: "'' != \"SET LOCAL lock_timeout = '5s'\""},
}

#: One test's own FAIL/ERROR block: from its header to the next header or the "Ran" line.
BLOCK = re.compile(
    r"^(?:FAIL|ERROR): (test_\w+) \([\w.]+\)[^\n]*\n(.*?)(?=^(?:FAIL|ERROR): |^Ran \d+ tests?)",
    re.M | re.S,
)

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


def wrong_reasons(mid, text):
    """The tests of REASONS[mid] whose own blocks lack the written fragment."""
    blocks = {}
    for name, body in BLOCK.findall(text):
        blocks.setdefault(name, []).append(body)
    return sorted(
        test
        for test, fragment in REASONS.get(mid, {}).items()
        if not any(fragment in body for body in blocks.get(test, []))
    )


def judge(mid, returncode, text):
    """(status, the run's "Ran" line, the failing tests' names, the tests red for another reason)."""
    failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", text, re.M)))
    ran = re.findall(r"^Ran \d+ tests? in .*$", text, re.M)
    ran_line = ran[-1] if ran else None
    wrong = wrong_reasons(mid, text)
    if returncode == 0:
        status = "SURVIVED"
    elif (
        ran_line
        and LOAD_FAILURE not in text
        and set(_expected(mid)) <= set(failed)
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
    for mid, reasons in REASONS.items():
        assert set(reasons) <= set(
            _expected(mid)
        ), f"{mid}: a reason for a test not expected"
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
    unknown = sorted(set(ONLY) - {m[0] for m in MUTANTS})
    if unknown:
        print("STOP: MUT_ONLY names unknown mutant id(s):", unknown, flush=True)
        raise SystemExit(2)
    selected = [m for m in MUTANTS if not ONLY or m[0] in ONLY]
    if "--check" in sys.argv:
        print(len(MUTANTS), "mutants: every anchor found once, all parse")
        print(" ".join(m[0] for m in MUTANTS))
        if ONLY:
            print("SUBSET:", [m[0] for m in selected], "of", len(MUTANTS))
        return
    if ONLY:
        print("SUBSET:", [m[0] for m in selected], "of", len(MUTANTS), flush=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    results = {}
    for mid, what, path, old, new, tests, kind in selected:
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
        status, ran_line, failed, wrong = judge(mid, p.returncode, text)
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
            "written_reasons": REASONS.get(mid, {}),
            "wrong_reason": wrong,
        }
        print(mid, results[mid], flush=True)
    with open(OUT, "w") as f:
        if ONLY:
            json.dump(
                {
                    "subset": [m[0] for m in selected],
                    "of": len(MUTANTS),
                    "results": results,
                },
                f,
                indent=2,
            )
        else:
            json.dump(results, f, indent=2)
        f.write("\n")
    for status in ("KILLED", "SURVIVED", "BROKEN"):
        print(f"{status}:", [k for k, v in results.items() if v["status"] == status])


if __name__ == "__main__":
    main()
