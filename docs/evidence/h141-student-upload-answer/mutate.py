"""H-141 mutants. Each is one textual change to one production file; the
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

    python docs/evidence/h141-student-upload-answer/mutate.py --check
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
    "students.tests_student_upload_answer",
    "AutoGrader.tests_submission_audience_guard",
    "AutoGrader.tests_student_feedback_guard",
]
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
HERE = pathlib.Path("docs/evidence/h141-student-upload-answer")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))
PYCACHE_ROOTS = ["classrooms", "assignments", "students", "AutoGrader"]

SER = "students/serializers.py"
VIEWS = "students/views.py"

U = "students.tests_student_upload_answer."
FIRST = U + "AFirstUploadTest."
LATER = U + "AnUploadOnAPaperNotYetGradedTest."
ONLY = U + "OnlyTheAnswerProtectsTest."
KEYS = FIRST + "test_the_keys_are_the_thirty_the_route_always_answered_with"
OWN_FACTS = FIRST + "test_the_students_own_facts_keep_their_values"
DOC_PAGE = FIRST + "test_the_document_is_the_one_the_students_page_shows"
FIRST_CONST = FIRST + "test_every_staff_field_is_the_constant"
FIRST_TOTAL = FIRST + "test_the_assignments_total_is_the_maximum_shown"
SCHED = LATER + "test_a_scheduled_grading_run_is_not_shown"
FAILED = LATER + "test_a_failed_grading_run_is_not_shown"
STALE = LATER + "test_a_grading_claim_too_old_to_block_the_upload_is_not_shown"
WHOLE = LATER + "test_it_answers_as_a_first_upload_does_but_for_the_uploads_own_facts"
LATER_CONST = LATER + "test_every_staff_field_is_the_constant_on_a_later_upload_too"
GRADED_CONST = ONLY + "test_a_graded_paper_answers_with_every_staff_field_the_constant"
GRADED_MAX = ONLY + "test_a_graded_paper_shows_the_assignments_total_not_the_graders"
GRADED_DOC = ONLY + "test_a_graded_papers_document_is_the_one_a_submitted_paper_has"
GRADED_WHOLE = ONLY + "test_grading_changes_nothing_in_the_answer_but_the_attempts"
TEACHER = (
    U + "TheTeacherStillReadsEverythingTest.test_the_teachers_page_of_a_graded_paper"
)

G = "AutoGrader.tests_submission_audience_guard.SubmissionAudienceGuardTest."
G_SAYS = G + "test_rule_1_every_serializer_of_a_submission_says_who_it_is_for"
G_BOTH = G + "test_rule_1_a_serializer_for_both_names_where_a_student_is_answered"
G_TOLD = G + "test_rule_1_the_serializers_are_for_whom_this_guard_was_told"
G_NAMED = G + "test_rule_2_every_action_of_the_view_is_named"
G_PUT = G + "test_rule_2_put_is_not_routed"
G_WHO = G + "test_rule_2_who_can_call_each_action_is_as_named"
G_BUILDS = G + "test_rule_2_each_action_builds_the_serializers_named_and_no_other"
G_STAFF = G + "test_rule_3_a_student_action_builds_staff_only_in_the_staff_branch"
G_CONTEXT = G + "test_rule_3_a_student_action_gives_every_serializer_the_context"
G_GET = G + "test_rule_4_get_serializer_gives_a_student_no_staff_serializer"
F = (
    "AutoGrader.tests_student_feedback_guard.StudentFeedbackGuardTest."
    "test_rule_2_the_upload_answer_sends_every_guarded_key_as_a_constant"
)

ROUTE = (
    "            serializer = StudentUploadAnswerSerializer(\n"
    "                submission, context=self.get_serializer_context()\n"
    "            )\n"
)
FIELDS_TAIL = (
    '            "answers",\n'
    '            "grading_state",\n'
    '            "needs_review",\n'
    '            "review_reasons",\n'
    '            "review_severity",\n'
    '            "review_tier",\n'
    '            "second_opinion",\n'
    '            "question_breakdown",\n'
    '            "scheduled_grading_at",\n'
    '            "grading_task_name",\n'
    '            "is_grading_scheduled",\n'
    "        ]\n"
    "        read_only_fields = fields\n"
)
OWN_METHODS = (
    "        return obj.assignment.total_points\n"
    "\n"
    "    def get_raw_input(self, obj):\n"
    "        return answer_document_for_student(obj)\n"
)


def row_value(key, value, expected):
    """The staff key `key` is the row's own column again: its constant is
    taken away, and the model's field of that name answers."""
    return (SER, f"    {key} = SentAs({value})\n", "", expected)


MUTANTS = {
    # ---- the route
    "V1_the_route_answers_with_the_teachers_serializer_again": (
        VIEWS,
        ROUTE,
        "            serializer = StudentSubmissionDetailSerializer(\n"
        "                submission, context=self.get_serializer_context()\n"
        "            )\n",
        [SCHED, FAILED, STALE, WHOLE, GRADED_CONST, GRADED_WHOLE, G_BUILDS, G_STAFF],
    ),
    "V2_the_route_gives_the_serializer_no_context": (
        VIEWS,
        ROUTE,
        "            serializer = StudentUploadAnswerSerializer(submission)\n",
        [G_CONTEXT],
    ),
    # ---- every staff key, one at a time, is the row's value again
    "C01_score": row_value(
        "score", "None", [FIRST_CONST, LATER_CONST, GRADED_CONST, GRADED_WHOLE]
    ),
    "C02_grading_state": (
        SER,
        "    grading_state = SentAs(GradingState.IDLE.value)\n",
        "",
        [FAILED, STALE, WHOLE, GRADED_CONST, GRADED_WHOLE, F],
    ),
    "C03_scheduled_grading_at": row_value(
        "scheduled_grading_at", "None", [SCHED, WHOLE, GRADED_CONST, GRADED_WHOLE, F]
    ),
    "C04_grading_task_name": row_value(
        "grading_task_name", "None", [SCHED, WHOLE, GRADED_CONST, GRADED_WHOLE, F]
    ),
    "C05_is_grading_scheduled_says_true": (
        SER,
        "    is_grading_scheduled = SentAs(False)\n",
        "    is_grading_scheduled = SentAs(True)\n",
        [FIRST_CONST, LATER_CONST, SCHED, GRADED_CONST, F],
    ),
    "C06_needs_review": row_value(
        "needs_review", "False", [GRADED_CONST, GRADED_WHOLE, F]
    ),
    "C07_review_reasons": row_value(
        "review_reasons", "None", [GRADED_CONST, GRADED_WHOLE, F]
    ),
    "C08_formatted_grade": row_value(
        "formatted_grade", "None", [GRADED_CONST, GRADED_WHOLE, F]
    ),
    "C09_second_opinion_is_the_saved_feedback": (
        SER,
        "    second_opinion = SentAs(None)\n",
        '    second_opinion = serializers.ReadOnlyField(source="feedback")\n',
        [GRADED_CONST, GRADED_WHOLE],
    ),
    "C10_question_breakdown_is_the_saved_feedback": (
        SER,
        "    question_breakdown = SentAs([])\n",
        '    question_breakdown = serializers.ReadOnlyField(source="feedback")\n',
        [FIRST_CONST, LATER_CONST, GRADED_CONST, GRADED_WHOLE],
    ),
    "C11_score_percentage": row_value(
        "score_percentage", "None", [GRADED_CONST, GRADED_WHOLE]
    ),
    "C12_was_regraded": row_value(
        "was_regraded", "False", [GRADED_CONST, GRADED_WHOLE]
    ),
    "C13_regraded_at": row_value("regraded_at", "None", [GRADED_CONST, GRADED_WHOLE]),
    "C14_grade_status_says_graded": (
        SER,
        '    grade_status = SentAs("NOT GRADED")\n',
        '    grade_status = SentAs("GRADED")\n',
        [FIRST_CONST, LATER_CONST, GRADED_CONST],
    ),
    "C15_review_severity": row_value(
        "review_severity", "None", [GRADED_CONST, GRADED_WHOLE, F]
    ),
    "C16_review_tier": row_value(
        "review_tier", "None", [GRADED_CONST, GRADED_WHOLE, F]
    ),
    # ---- the student's own facts
    "O1_the_maximum_is_the_graders_when_the_row_has_one": (
        SER,
        OWN_METHODS,
        OWN_METHODS.replace(
            "return obj.assignment.total_points",
            "return obj.max_points or obj.assignment.total_points",
        ),
        [GRADED_MAX, GRADED_WHOLE],
    ),
    "O2_the_document_is_the_stored_one": (
        SER,
        OWN_METHODS,
        OWN_METHODS.replace(
            "return answer_document_for_student(obj)", "return obj.raw_input"
        ),
        [GRADED_DOC, GRADED_WHOLE],
    ),
    "O3_no_document_is_sent": (
        SER,
        OWN_METHODS,
        OWN_METHODS.replace("return answer_document_for_student(obj)", "return None"),
        [OWN_FACTS, DOC_PAGE],
    ),
    "O4_the_maximum_is_the_rows_own_only": (
        SER,
        OWN_METHODS,
        OWN_METHODS.replace(
            "return obj.assignment.total_points", "return obj.max_points"
        ),
        [FIRST_TOTAL, GRADED_MAX],
    ),
    "K1_a_key_is_dropped": (
        SER,
        FIELDS_TAIL,
        FIELDS_TAIL.replace('            "answers",\n', ""),
        [KEYS, OWN_FACTS],
    ),
    # ---- who a serializer is for
    "A1_the_upload_serializer_says_staff": (
        SER,
        '    audience = "student"\n\n    full_name',
        '    audience = "staff"\n\n    full_name',
        [G_TOLD, G_STAFF],
    ),
    "A2_the_list_serializer_says_nothing": (
        SER,
        '    audience = "both"\n',
        "",
        [G_SAYS, G_TOLD, G_GET],
    ),
    "A3_the_list_serializer_names_a_method_it_has_not": (
        SER,
        '("to_representation", "get_score", "get_score_percentage")',
        '("to_representation", "get_marks")',
        [G_BOTH],
    ),
    "A4_the_teachers_serializer_says_both": (
        SER,
        "    # route may build it (the guard holds that).\n" '    audience = "staff"\n',
        "    # route may build it (the guard holds that).\n" '    audience = "both"\n',
        [G_TOLD, G_BOTH],
    ),
    # ---- the view
    "P1_the_edit_is_for_teachers_only": (
        VIEWS,
        "            permission_classes = [IsAuthenticated, HasCreditBalance]\n",
        "            permission_classes = [IsAuthenticated, IsTeacher, HasCreditBalance]\n",
        [G_WHO],
    ),
    "P2_the_remaining_actions_are_open_to_a_student": (
        VIEWS,
        "            # Everything else (e.g., destroy) is teacher-only\n"
        "            permission_classes = [IsAuthenticated, IsTeacher]\n",
        "            # Everything else (e.g., destroy) is teacher-only\n"
        "            permission_classes = [IsAuthenticated]\n",
        [G_WHO],
    ),
    "N1_a_new_action_nobody_classified": (
        VIEWS,
        "    @extend_schema(exclude=True)\n    def create(",
        "    @action(detail=True, methods=['GET'])\n"
        "    def peek(self, request, pk=None):\n"
        "        return Response(\n"
        "            StudentSubmissionDetailSerializer(self.get_object()).data\n"
        "        )\n"
        "\n"
        "    @extend_schema(exclude=True)\n    def create(",
        [G_NAMED],
    ),
    "H1_put_is_routed": (
        VIEWS,
        '    http_method_names = ["get", "head", "post", "delete", "patch", "options"]\n',
        '    http_method_names = ["get", "head", "post", "put", "delete", "patch", "options"]\n',
        [G_PUT],
    ),
    # ---- the teacher's answer
    "T1_the_teacher_is_shown_the_assignments_total": (
        SER,
        "    return submission.max_points or submission.assignment.total_points\n",
        "    return submission.assignment.total_points\n",
        [TEACHER],
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
