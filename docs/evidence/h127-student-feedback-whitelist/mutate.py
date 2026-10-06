"""H-127 / H-128 mutants. Each is one textual change to one production
file; six test modules run against it; the failing tests are recorded; the
file is restored.

Written BEFORE any run: every mutant names the tests it must fail
(EXPECTED, full dotted names). A mutant is KILLED only if the inner run
ends non-zero, shows its own "Ran" line, loaded every module, and every
expected test is among those that failed. Other outcomes are reported as
what they are: SURVIVED (exit 0), KILLED_NOT_AS_EXPECTED (it failed, but
not all the named tests did), BROKEN (anything else). Only KILLED counts.

Rule 17: the inner run has PYTHONDONTWRITEBYTECODE=1 and every
__pycache__ under the mutated apps is deleted before each mutant and after
each restore. Rule 18: each inner run writes straight to a file
(mutant_logs/<name>.txt), stdin from /dev/null; nothing is piped.

    python docs/evidence/h127-student-feedback-whitelist/mutate.py --check
checks the anchors (each exactly once) and that every mutant parses, and
runs nothing.

MUT_ONLY=name,name,... runs just those mutants (rule 17 addendum: after a
change to one module, the mutants on that module are run again on the
final module; the others keep their earlier result, which the evidence
names). MUT_RESULTS and MUT_LOGS say where that run writes.
"""

import ast
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

TESTS = [
    "students.tests_student_feedback_routes",
    "students.tests_formatter_input",
    "students.tests_student_feedback_scoping",
    "ai_processor.tests_second_opinion_error_code",
    "ai_processor.tests_second_opinion_pipeline",
    "AutoGrader.tests_student_feedback_guard",
]
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
HERE = pathlib.Path("docs/evidence/h127-student-feedback-whitelist")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))
PYCACHE_ROOTS = ["students", "assignments", "dashboard", "ai_processor", "AutoGrader"]

R = "students.tests_student_feedback_routes."
F = "students.tests_formatter_input."
S = "students.tests_student_feedback_scoping.StudentFeedbackScopingTest."
C = "ai_processor.tests_second_opinion_error_code.SecondOpinionErrorCodeTest."
G = "AutoGrader.tests_student_feedback_guard.StudentFeedbackGuardTest."
RULE_1 = G + "test_rule_1_every_raw_read_is_a_named_one"
FG = R + "StudentFormattedGradeTest."
N = R + "NestedValuesUnderAllowedNamesTest."
LIST_UNPUBLISHED = (
    R + "StudentSubmissionListReviewFieldsTest."
    "test_an_unpublished_grade_shows_a_student_no_review_field"
)
LIST_PUBLISHED = (
    R + "StudentSubmissionListReviewFieldsTest."
    "test_a_published_grade_shows_a_student_no_review_field"
)
NO_FILTER = (
    R + "StudentSubmissionListReviewFiltersTest."
    "test_a_student_cannot_filter_on_the_review_queue"
)
RULE_3 = G + "test_rule_3_a_student_is_refused_every_review_queue_filter"
SITE_FOLLOW_UP = (
    F + "AutomaticFollowUpFormatterPromptTest."
    "test_the_prompt_dispatched_after_a_grading"
)
SITE_TEACHER_FEEDBACK = (
    F + "TeacherRoutesFormatterPromptTest."
    "test_the_prompt_dispatched_by_teacher_feedback"
)
SITE_UPDATE_GRADE = (
    F + "TeacherRoutesFormatterPromptTest.test_the_prompt_dispatched_by_update_grade"
)

# name: (file, text as it is, text of the mutant, tests that must fail)
MUTANTS = {
    # ---- H-127, the three routes: each back to the saved column. Each is
    # expected to be caught twice, by its route test and by the guard.
    "A1_assignment_detail_returns_the_saved_column": (
        "assignments/serializers.py",
        "            return student_safe_feedback(submission.feedback or submission.ai_feedback)\n",
        "            return submission.feedback or submission.ai_feedback\n",
        [
            R + "AssignmentDetailPerformanceSummaryTest."
            "test_a_published_grade_is_shown_as_the_student_projection",
            R + "AssignmentDetailPerformanceSummaryTest."
            "test_the_older_ai_feedback_column_is_projected_too",
            RULE_1,
        ],
    ),
    "A2_student_dashboard_returns_the_saved_column": (
        "dashboard/views.py",
        "                        student_safe_feedback(released.feedback) if released else None\n",
        "                        released.feedback if released else None\n",
        [
            R + "StudentDashboardAssignmentsFeedbackTest."
            "test_a_published_grade_is_shown_as_the_student_projection",
            RULE_1,
        ],
    ),
    "A3_student_submission_detail_returns_the_saved_column": (
        "students/serializers.py",
        "            return student_safe_feedback(obj.feedback)\n",
        "            return obj.feedback\n",
        [
            S + "test_second_opinion_is_never_visible_to_the_student",
            S + "test_internal_and_teacher_only_fields_are_stripped",
            RULE_1,
            G + "test_rule_2_the_student_detail_serializer_projects_both_columns",
        ],
    ),
    "A4_a_value_that_is_not_a_dictionary_is_returned_as_stored": (
        "students/feedback_projection.py",
        "    if not isinstance(feedback, dict):\n        return None\n",
        "    if not isinstance(feedback, dict):\n        return feedback\n",
        [
            R + "StudentSafeFeedbackFunctionTest."
            "test_a_value_that_is_not_a_dictionary_is_shown_as_nothing",
        ],
    ),
    # ---- H-127, formatted_grade (found by Verifier 1's pre-read).
    "F1_the_student_page_returns_the_formatted_grade_as_stored": (
        "students/serializers.py",
        "        if not obj.is_published:\n"
        "            return None\n"
        "        return student_safe_formatted_grade(obj.formatted_grade)\n",
        "        if not obj.is_published:\n"
        "            return None\n"
        "        return obj.formatted_grade\n",
        [
            FG + "test_a_released_grade_stored_in_python_text_form",
            FG + "test_json_text_that_is_also_python_text_is_projected_the_same_way",
            FG + "test_text_that_is_not_a_dictionary_is_shown_as_nothing",
            RULE_1,
            G + "test_rule_2_the_student_detail_serializer_projects_both_columns",
        ],
    ),
    "F2_the_allow_list_is_widened_to_for_teacher": (
        "students/feedback_projection.py",
        'STUDENT_FORMATTED_RECOMMENDATION_FIELDS = ("for_student",)\n',
        'STUDENT_FORMATTED_RECOMMENDATION_FIELDS = ("for_student", "for_teacher")\n',
        [
            FG + "test_a_released_grade_stored_in_python_text_form",
            FG + "test_json_text_that_is_also_python_text_is_projected_the_same_way",
        ],
    ),
    "F3_text_that_cannot_be_read_is_shown_as_stored": (
        "students/feedback_projection.py",
        '        except Exception:  # noqa: BLE001 - every failure to read is "nothing"\n'
        "            return None\n",
        '        except Exception:  # noqa: BLE001 - every failure to read is "nothing"\n'
        "            return stored\n",
        [
            FG + "test_text_that_is_not_a_dictionary_is_shown_as_nothing",
            FG + "test_json_text_that_is_not_python_text_is_shown_as_nothing",
            FG + "test_a_deeply_nested_value_is_shown_as_nothing",
            FG + "test_text_that_would_run_code_is_only_ever_read_never_run",
            FG + "test_an_empty_value_is_shown_as_nothing",
        ],
    ),
    "F4_a_literal_that_is_not_a_dictionary_is_shown_as_stored": (
        "students/feedback_projection.py",
        "    if not isinstance(formatted, dict):\n        return None\n",
        "    if not isinstance(formatted, dict):\n        return stored\n",
        [FG + "test_a_list_is_shown_as_nothing"],
    ),
    "F5_no_size_limit": (
        "students/feedback_projection.py",
        "        if len(stored) > MAX_FORMATTED_GRADE_CHARACTERS:\n            return None\n",
        "        if False:\n            return None\n",
        [FG + "test_a_very_long_value_is_shown_as_nothing"],
    ),
    "F6_unknown_sections_are_copied": (
        "students/feedback_projection.py",
        "    return str(safe)\n",
        "    return str({**formatted, **safe})\n",
        [
            FG + "test_a_released_grade_stored_in_python_text_form",
            FG + "test_json_text_that_is_also_python_text_is_projected_the_same_way",
        ],
    ),
    "F7_an_unreleased_formatted_grade_is_shown": (
        "students/serializers.py",
        "        if not obj.is_published:\n"
        "            return None\n"
        "        return student_safe_formatted_grade(obj.formatted_grade)\n",
        "        return student_safe_formatted_grade(obj.formatted_grade)\n",
        [FG + "test_an_unreleased_grade_shows_nothing"],
    ),
    # ---- H-127, a value nested under an allowed name (Verifier 1's read).
    "G1_any_value_under_an_allowed_name_is_copied": (
        "students/feedback_projection.py",
        "    return _is_plain(value) or (\n"
        "        isinstance(value, list) and all(_is_plain(item) for item in value)\n"
        "    )\n",
        "    return True\n",
        [
            N + "test_the_feedback_on_the_assignment_detail",
            N + "test_the_feedback_and_formatted_grade_on_the_submission_page",
        ],
    ),
    "G2_a_list_is_copied_whatever_it_holds": (
        "students/feedback_projection.py",
        "        isinstance(value, list) and all(_is_plain(item) for item in value)\n",
        "        isinstance(value, list)\n",
        [
            N + "test_the_feedback_on_the_assignment_detail",
            N + "test_the_feedback_and_formatted_grade_on_the_submission_page",
        ],
    ),
    # ---- H-127, the student's list: the review-queue fields.
    "B1_the_list_replaces_no_review_field": (
        "students/serializers.py",
        "            data.update(self.STUDENT_REVIEW_FIELD_VALUES)\n",
        "            pass\n",
        [LIST_UNPUBLISHED, LIST_PUBLISHED],
    ),
    "B2_the_list_does_not_replace_review_reasons": (
        "students/serializers.py",
        '        "review_reasons": None,\n        "review_severity": None,\n',
        '        "review_severity": None,\n',
        [
            LIST_UNPUBLISHED,
            LIST_PUBLISHED,
            G + "test_rule_2_the_list_serializer_replaces_every_review_field",
        ],
    ),
    "B3_the_list_does_not_replace_needs_review": (
        "students/serializers.py",
        '        "needs_review": False,\n        "review_reasons": None,\n',
        '        "review_reasons": None,\n',
        [
            LIST_UNPUBLISHED,
            LIST_PUBLISHED,
            G + "test_rule_2_the_list_serializer_replaces_every_review_field",
        ],
    ),
    "B4_the_list_does_not_replace_severity_and_tier": (
        "students/serializers.py",
        '        "review_severity": None,\n        "review_tier": None,\n',
        "",
        [
            LIST_UNPUBLISHED,
            LIST_PUBLISHED,
            G + "test_rule_2_the_list_serializer_replaces_every_review_field",
        ],
    ),
    "B5_the_list_does_not_replace_grading_confidence": (
        "students/serializers.py",
        '        "review_tier": None,\n        "grading_confidence": None,\n',
        '        "review_tier": None,\n',
        [
            LIST_UNPUBLISHED,
            LIST_PUBLISHED,
            G + "test_rule_2_the_list_serializer_replaces_every_review_field",
        ],
    ),
    "B6_an_unreleased_grade_shows_when_it_was_made": (
        "students/serializers.py",
        '            if not instance.is_published:\n                data["graded_at"] = None\n',
        '            if False:\n                data["graded_at"] = None\n',
        [LIST_UNPUBLISHED],
    ),
    "B7_the_teacher_loses_the_review_fields_too": (
        "students/serializers.py",
        '        if request and request.user.user_type == "STUDENT":\n'
        "            data.update(self.STUDENT_REVIEW_FIELD_VALUES)\n",
        "        if request:\n            data.update(self.STUDENT_REVIEW_FIELD_VALUES)\n",
        [
            R + "StudentSubmissionListReviewFieldsTest."
            "test_the_teacher_still_sees_the_review_queue_fields",
        ],
    ),
    # ---- H-127, the list's filters and ordering: one mutant for each.
    "C1_a_student_may_filter_on_needs_review": (
        "students/views.py",
        '    TEACHER_ONLY_FILTERS = ("needs_review", "review_tier")\n',
        '    TEACHER_ONLY_FILTERS = ("review_tier",)\n',
        [NO_FILTER, RULE_3],
    ),
    "C2_a_student_may_filter_on_review_tier": (
        "students/views.py",
        '    TEACHER_ONLY_FILTERS = ("needs_review", "review_tier")\n',
        '    TEACHER_ONLY_FILTERS = ("needs_review",)\n',
        [NO_FILTER, RULE_3],
    ),
    "C3_a_student_may_order_by_review_severity": (
        "students/views.py",
        '    TEACHER_ONLY_ORDERINGS = ("review_severity",)\n',
        "    TEACHER_ONLY_ORDERINGS = ()\n",
        [NO_FILTER, RULE_3],
    ),
    "C4_nobody_is_refused": (
        "students/views.py",
        "        if self.request.user.user_type != UserTypes.STUDENT:\n            return\n",
        "        if True:\n            return\n",
        [NO_FILTER],
    ),
    "C5_the_teacher_is_refused_too": (
        "students/views.py",
        "        if self.request.user.user_type != UserTypes.STUDENT:\n            return\n",
        "        if False:\n            return\n",
        [
            R + "StudentSubmissionListReviewFiltersTest."
            "test_the_teacher_can_still_filter_and_order_the_review_queue",
        ],
    ),
    # ---- H-127, the formatter: each of the three sites, then the function.
    "D1_the_follow_up_sends_the_whole_result": (
        "students/services.py",
        "    {grading_result_for_formatter(grading)}\n",
        "    {grading}\n",
        [SITE_FOLLOW_UP],
    ),
    "D2_teacher_feedback_sends_the_whole_result": (
        "students/views.py",
        "                {grading_result_for_formatter(grading)}\n",
        "                {grading}\n",
        [SITE_TEACHER_FEEDBACK],
    ),
    "D3_update_grade_sends_the_whole_result": (
        "students/views.py",
        "        {grading_result_for_formatter(submission.feedback)}\n",
        "        {submission.feedback}\n",
        [SITE_UPDATE_GRADE],
    ),
    "D4_the_function_leaves_nothing_out": (
        "students/feedback_projection.py",
        '    return {key: value for key, value in grading.items() if key != "second_opinion"}\n',
        "    return {key: value for key, value in grading.items()}\n",
        [
            F + "GradingResultForFormatterTest."
            "test_only_the_second_opinion_block_is_left_out",
            SITE_FOLLOW_UP,
            SITE_TEACHER_FEEDBACK,
            SITE_UPDATE_GRADE,
        ],
    ),
    # ---- H-128, the saved error.
    "E1_out_of_credits_saves_the_text": (
        "ai_processor/services.py",
        '                "error": _second_opinion_error_code(e),\n'
        '                "needs_review": True,\n',
        '                "error": str(e),\n                "needs_review": True,\n',
        [C + "test_out_of_credits"],
    ),
    "E2_any_other_failure_saves_the_text": (
        "ai_processor/services.py",
        '            result["second_opinion"] = {"error": _second_opinion_error_code(e)}\n',
        '            result["second_opinion"] = {"error": str(e)}\n',
        [
            C + "test_access_refused",
            C + "test_provider_error",
            C + "test_evidence_rejected",
            C + "test_incomplete_response",
            C + "test_anything_else",
        ],
    ),
    "E3_the_batch_failure_forgets_its_cause": (
        "ai_processor/services.py",
        '            f"[Grading] Batch {batch_number}/{total_batches} failed after 3 attempts. "\n'
        '            f"Last error: {last_error}"\n'
        "        ) from last_error\n",
        '            f"[Grading] Batch {batch_number}/{total_batches} failed after 3 attempts. "\n'
        '            f"Last error: {last_error}"\n'
        "        )\n",
        [
            C + "test_provider_error",
            C + "test_evidence_rejected",
            C + "test_incomplete_response",
        ],
    ),
    "E4_the_out_of_credits_text_is_not_logged": (
        "ai_processor/services.py",
        '                "processing_task_id=%s - %s",\n'
        "                processing_task_id,\n"
        "                e,\n",
        '                "processing_task_id=%s",\n                processing_task_id,\n',
        [C + "test_out_of_credits"],
    ),
    "E5_a_provider_failure_is_not_told_apart": (
        "ai_processor/services.py",
        "        if isinstance(exc, OpenAIError):\n"
        '            return "provider_error"\n',
        "",
        [C + "test_provider_error"],
    ),
}


def clear_pycache():
    for root in PYCACHE_ROOTS:
        for cache in pathlib.Path(root).rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)


def remaining_pycache():
    return sum(
        1 for root in PYCACHE_ROOTS for _ in pathlib.Path(root).rglob("__pycache__")
    )


def check():
    for name, (path, old, new, expected) in MUTANTS.items():
        source = pathlib.Path(path).read_text(encoding="utf-8")
        assert source.count(old) == 1, f"{name}: anchor found {source.count(old)} times"
        ast.parse(source.replace(old, new, 1))
        assert expected and len(set(expected)) == len(expected), name
    print(len(MUTANTS), "mutants: anchors unique, all parse, expected tests named")


def main():
    check()
    if "--check" in sys.argv:
        return
    only = [name for name in os.environ.get("MUT_ONLY", "").split(",") if name]
    unknown = sorted(set(only) - set(MUTANTS))
    assert not unknown, f"MUT_ONLY names no such mutant: {unknown}"
    selected = {name: MUTANTS[name] for name in (only or MUTANTS)}
    print(len(selected), "of", len(MUTANTS), "mutants selected", flush=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    results = {}
    for name, (path, old, new, expected) in selected.items():
        target = pathlib.Path(path)
        original = target.read_text(encoding="utf-8")
        try:
            clear_pycache()
            target.write_text(original.replace(old, new, 1), encoding="utf-8")
            log = LOGS / f"{name}.txt"
            with open(log, "w") as out, open(os.devnull) as devnull:
                p = subprocess.run(
                    [sys.executable, "manage.py", "test", *TESTS]
                    + [f"--settings={SETTINGS}", "--keepdb", "--noinput"],
                    stdin=devnull,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    env=env,
                    timeout=900,
                )
            exit_status = p.returncode
        except subprocess.TimeoutExpired:
            exit_status = "timeout"
        finally:
            target.write_text(original, encoding="utf-8")
            clear_pycache()
        text = log.read_text(errors="replace")
        failed = sorted(
            set(re.findall(r"^(?:FAIL|ERROR): \w+ \(([\w.]+)\)", text, re.M))
        )
        ran = re.findall(r"^Ran (\d+) tests?", text, re.M)
        loaded = not re.search(
            r"unittest\.loader\._FailedTest"
            r"|^(?:ImportError|ModuleNotFoundError|SyntaxError)\b",
            text,
            re.M,
        )
        missing = sorted(set(expected) - set(failed))
        if exit_status == 0:
            status = "SURVIVED"
        elif exit_status != "timeout" and ran and failed and loaded and not missing:
            status = "KILLED"
        elif exit_status != "timeout" and ran and failed and loaded:
            status = "KILLED_NOT_AS_EXPECTED"
        else:
            status = "BROKEN"
        results[name] = {
            "status": status,
            "exit": exit_status,
            "ran": int(ran[-1]) if ran else None,
            "expected": expected,
            "expected_but_passed": missing,
            "failing_tests": failed,
            "pycache_left": remaining_pycache(),
        }
        print(
            name,
            status,
            "exit",
            exit_status,
            "ran",
            results[name]["ran"],
            "failed",
            len(failed),
            "expected-but-passed",
            missing,
            flush=True,
        )
    with open(OUT, "w") as handle:
        json.dump(results, handle, indent=2)
        handle.write("\n")
    for wanted in ("SURVIVED", "KILLED_NOT_AS_EXPECTED", "BROKEN"):
        print(wanted + ":", [k for k, v in results.items() if v["status"] == wanted])
    print(
        "KILLED:",
        sum(v["status"] == "KILLED" for v in results.values()),
        "of",
        len(results),
    )


if __name__ == "__main__":
    main()
