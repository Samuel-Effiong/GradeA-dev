"""H-133 mutants. Each is one textual change to one production file; three
test modules run against it; the failing tests are recorded; the file is
restored. The runner is H-127's (docs/evidence/h127-student-feedback-
whitelist/mutate.py) with this list.

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

    python docs/evidence/h133-no-grade-tell-before-release/mutate.py --check
checks the anchors (each exactly once) and that every mutant parses, and
runs nothing. MUT_ONLY=name,name,... runs just those mutants; MUT_RESULTS
and MUT_LOGS say where that run writes.
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
    "students.tests_no_grade_tell_before_release",
    "students.tests_student_feedback_routes",
    "AutoGrader.tests_student_feedback_guard",
]
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
HERE = pathlib.Path("docs/evidence/h133-no-grade-tell-before-release")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))
PYCACHE_ROOTS = ["students", "assignments", "AutoGrader"]

T = "students.tests_no_grade_tell_before_release."
A = T + "StudentToldNothingOfAGradeTest."
B = T + "StudentCannotTellBusyKindsApartTest."
OTHER = T + "TheOtherRefusalsTest."
L = T + "StudentListShowsNoGradingStateTest."
G = "AutoGrader.tests_student_feedback_guard.StudentFeedbackGuardTest."
CLOSED_SYNC = [
    A + "test_the_upload_refusal",
    A + "test_the_queued_upload_refusal",
    A + "test_the_edit_refusal",
    A + "test_the_refusal_is_the_same_once_the_grade_is_released",
]
CLOSED_TASK = A + "test_a_queued_upload_that_runs_after_the_grade_landed"
BUSY = [
    B + "test_an_upload_while_the_paper_is_being_graded",
    B + "test_an_edit_while_the_paper_is_being_graded",
    B + "test_the_two_busy_cases_get_one_and_the_same_answer",
]
TEACHER_GRADED = OTHER + "test_the_teacher_is_still_told_that_the_paper_is_graded"
TEACHER_BEING_GRADED = (
    OTHER + "test_the_teacher_is_still_told_that_the_paper_is_being_graded"
)
LIKE_SUBMITTED = L + "test_scheduled_being_graded_and_failed_look_like_submitted"
GRADED_LIKE_SUBMITTED = (
    L + "test_graded_but_unreleased_looks_like_submitted_but_for_the_attempts"
)
RULE_3 = G + "test_rule_3_a_student_is_refused_every_review_queue_filter"
RULE_H133 = G + "test_h133_the_list_serializer_hides_the_grading_state_and_schedule"

# name: (file, text as it is, text of the mutant, tests that must fail)
MUTANTS = {
    # ---- the sentences: who is told what
    "S1_a_student_is_told_the_paper_is_graded": (
        "students/services.py",
        "            SUBMISSION_CLOSED_FOR_STUDENT\n            if told_to_student\n",
        "            SUBMISSION_CLOSED_FOR_STUDENT\n            if False\n",
        CLOSED_SYNC + [CLOSED_TASK],
    ),
    "S2_a_student_is_told_the_paper_is_being_graded": (
        "students/services.py",
        "        raise SubmissionBeingGradedError(\n"
        "            SUBMISSION_BUSY_FOR_STUDENT\n            if told_to_student\n",
        "        raise SubmissionBeingGradedError(\n"
        "            SUBMISSION_BUSY_FOR_STUDENT\n            if False\n",
        BUSY,
    ),
    "S3_a_student_is_told_an_upload_is_still_being_processed": (
        "students/services.py",
        "        raise SubmissionProcessingInProgressError(\n"
        "            SUBMISSION_BUSY_FOR_STUDENT\n            if told_to_student\n",
        "        raise SubmissionProcessingInProgressError(\n"
        "            SUBMISSION_BUSY_FOR_STUDENT\n            if False\n",
        [B + "test_the_two_busy_cases_get_one_and_the_same_answer"],
    ),
    "S4_the_teacher_is_given_the_neutral_sentence_for_graded": (
        "students/services.py",
        "            SUBMISSION_CLOSED_FOR_STUDENT\n            if told_to_student\n",
        "            SUBMISSION_CLOSED_FOR_STUDENT\n            if True\n",
        [TEACHER_GRADED],
    ),
    "S5_the_teacher_is_given_the_neutral_sentence_for_being_graded": (
        "students/services.py",
        "        raise SubmissionBeingGradedError(\n"
        "            SUBMISSION_BUSY_FOR_STUDENT\n            if told_to_student\n",
        "        raise SubmissionBeingGradedError(\n"
        "            SUBMISSION_BUSY_FOR_STUDENT\n            if True\n",
        [TEACHER_BEING_GRADED],
    ),
    "S6_the_edit_path_treats_every_caller_as_a_teacher": (
        "students/services.py",
        "    told_to_student = user.user_type == UserTypes.STUDENT\n"
        "    ensure_submission_open(submission, told_to_student=told_to_student)\n",
        "    told_to_student = False\n"
        "    ensure_submission_open(submission, told_to_student=told_to_student)\n",
        [
            A + "test_the_edit_refusal",
            A + "test_the_refusal_is_the_same_once_the_grade_is_released",
            B + "test_an_edit_while_the_paper_is_being_graded",
        ],
    ),
    # ---- the codes
    "C1_the_refusal_response_has_no_code": (
        "students/views.py",
        '        {"error": str(exc), "code": getattr(exc, "code", None)},\n',
        '        {"error": str(exc)},\n',
        CLOSED_SYNC
        + BUSY
        + [
            OTHER + "test_attempts_used_keeps_its_sentence_and_gains_a_code",
            TEACHER_GRADED,
            TEACHER_BEING_GRADED,
        ],
    ),
    "C2_the_queued_task_result_has_no_code": (
        "assignments/tasks.py",
        '        return {"status": states.FAILURE, "message": message, **_refusal_code(exc)}\n',
        '        return {"status": states.FAILURE, "message": message}\n',
        [CLOSED_TASK],
    ),
    "C3_the_two_busy_cases_have_different_codes": (
        "students/exceptions.py",
        "    must not queue a second billed extraction. User-facing.\n"
        '    """\n\n    code = "submission_busy"\n',
        "    must not queue a second billed extraction. User-facing.\n"
        '    """\n\n    code = "submission_processing"\n',
        [
            B + "test_the_two_busy_cases_get_one_and_the_same_answer",
            T
            + "TheRefusalCodesAreAClosedListTest.test_each_closing_refusal_has_its_code",
        ],
    ),
    # ---- the student's list
    "L1_the_list_shows_the_real_grading_state": (
        "students/serializers.py",
        '            data["grading_state"] = (\n'
        "                GradingState.DONE.value\n"
        "                if instance.is_published\n"
        "                else GradingState.IDLE.value\n"
        "            )\n",
        '            data["grading_state"] = instance.grading_state\n',
        [LIKE_SUBMITTED, GRADED_LIKE_SUBMITTED],
    ),
    "L2_a_released_grade_still_shows_idle": (
        "students/serializers.py",
        "                GradingState.DONE.value\n                if instance.is_published\n",
        "                GradingState.DONE.value\n                if False\n",
        [L + "test_a_released_grade_shows_done"],
    ),
    "L3_the_schedule_fields_are_not_replaced": (
        "students/serializers.py",
        "            data.update(self.STUDENT_SCHEDULE_FIELD_VALUES)\n",
        "            pass\n",
        [LIKE_SUBMITTED, GRADED_LIKE_SUBMITTED, RULE_H133],
    ),
    "L4_is_grading_scheduled_is_not_replaced": (
        "students/serializers.py",
        '        "grading_task_name": None,\n        "is_grading_scheduled": False,\n',
        '        "grading_task_name": None,\n',
        [LIKE_SUBMITTED, GRADED_LIKE_SUBMITTED, RULE_H133],
    ),
    "L5_scheduled_grading_at_is_not_replaced": (
        "students/serializers.py",
        '        "scheduled_grading_at": None,\n        "grading_task_name": None,\n',
        '        "grading_task_name": None,\n',
        [LIKE_SUBMITTED, GRADED_LIKE_SUBMITTED, RULE_H133],
    ),
    "L6_the_teacher_loses_the_state_and_the_schedule_too": (
        "students/serializers.py",
        '        if request and request.user.user_type == "STUDENT":\n'
        "            data.update(self.STUDENT_REVIEW_FIELD_VALUES)\n",
        "        if request:\n            data.update(self.STUDENT_REVIEW_FIELD_VALUES)\n",
        [L + "test_the_teacher_still_sees_the_state_and_the_schedule"],
    ),
    # ---- the filters
    "F1_a_student_may_filter_on_the_grading_state": (
        "students/views.py",
        '    TEACHER_ONLY_FILTERS = ("needs_review", "review_tier", "grading_state")\n',
        '    TEACHER_ONLY_FILTERS = ("needs_review", "review_tier")\n',
        [L + "test_a_student_cannot_filter_on_the_grading_state", RULE_3],
    ),
    "F2_a_student_is_refused_is_published_too": (
        "students/views.py",
        '    TEACHER_ONLY_FILTERS = ("needs_review", "review_tier", "grading_state")\n',
        "    TEACHER_ONLY_FILTERS = (\n"
        '        "needs_review",\n        "review_tier",\n        "grading_state",\n'
        '        "is_published",\n    )\n',
        [L + "test_a_student_can_still_ask_for_released_grades", RULE_3],
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
