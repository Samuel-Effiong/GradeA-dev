"""H-147 mutants. Each is one textual change to one production file; the
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

    python docs/evidence/h147-no-classmates/mutate.py --check
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
    "classrooms.tests_student_sees_no_classmates",
    "classrooms.tests_course_payload_student_exposure",
    "AutoGrader.tests_student_classmates_guard",
]
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
HERE = pathlib.Path("docs/evidence/h147-no-classmates")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))
PYCACHE_ROOTS = ["classrooms", "assignments", "students", "AutoGrader"]

N = "classrooms.tests_student_sees_no_classmates."
OWN = N + "StudentSeesOnlyTheirOwnEntryTest."
WORK = N + "StudentSeesTheirOwnViewOfTheAssignmentsTest."
SESS = N + "StudentSeesNoStaffIdsOnASessionTest."
SUB = N + "StudentReadsOnlyTheirOwnSubmissionTest."
QUERIES = (
    N + "StudentCourseAnswerQueryCountTest"
    ".test_the_count_stays_flat_as_the_assignments_grow"
)
OLD = "classrooms.tests_course_payload_student_exposure."
G = "AutoGrader.tests_student_classmates_guard.StudentClassmatesGuardTest."
ORDER = G + "test_rule_1_the_course_answer_serves_the_student_first"
ROSTER = OWN + "test_the_roster_is_the_students_own_entry_on_every_course_route"
ANY_STATUS = OWN + "test_a_classmate_of_any_enrolment_status_is_absent"
MUTANTS = {
    # ---- the roster
    "R1_the_student_is_given_the_whole_roster": (
        "classrooms/serializers.py",
        "        if viewer is not None:\n"
        "            return self._own_entry(obj, viewer)\n",
        "",
        [ROSTER, ANY_STATUS, ORDER],
    ),
    "R2_the_own_entry_is_every_enrolment": (
        "classrooms/serializers.py",
        "            own = [e for e in obj.active_enrollments if e.student_id == viewer.pk]\n",
        "            own = list(obj.active_enrollments)\n",
        [ROSTER, ANY_STATUS],
    ),
    "R3_no_class_size_is_sent": (
        "classrooms/serializers.py",
        '        if hasattr(obj, "student_count"):\n            return obj.student_count\n',
        '        if hasattr(obj, "student_count"):\n            return None\n',
        [
            OWN + "test_the_class_size_is_sent_as_a_bare_number",
            OWN + "test_the_teacher_still_sees_the_whole_roster_and_the_class_size",
        ],
    ),
    "R4_the_teacher_is_answered_as_a_student": (
        "classrooms/serializers.py",
        '        if getattr(user, "user_type", None) == UserTypes.STUDENT:\n'
        "            return user\n"
        "        return None\n",
        "        return user\n",
        [
            OWN + "test_the_teacher_still_sees_the_whole_roster_and_the_class_size",
            WORK + "test_the_teacher_still_gets_the_teachers_assignment_list",
        ],
    ),
    # ---- the nested assignments
    "A1_the_student_is_given_the_teachers_assignment_list": (
        "classrooms/serializers.py",
        "            return AssignmentListStudentSerializer(\n",
        "            return AssignmentListSerializer(\n",
        [
            WORK + "test_the_nested_assignments_are_the_students_own_view",
            WORK + "test_the_students_own_status_on_an_assignment_is_shown",
            ORDER,
        ],
    ),
    "A2_the_view_does_not_load_the_viewers_submissions": (
        "classrooms/views.py",
        '                to_attr="viewer_submissions",\n',
        '                to_attr="some_other_name",\n',
        [QUERIES, SUB + "test_the_view_loads_only_the_viewers_own_submissions"],
    ),
    "A3_the_serializer_does_not_use_what_was_loaded": (
        "assignments/serializers.py",
        "            if loaded is not None:\n",
        "            if False:\n",
        [QUERIES],
    ),
    "A4_the_view_loads_everyones_submissions": (
        "classrooms/views.py",
        "                queryset=StudentSubmission.objects.filter(student=user),\n",
        "                queryset=StudentSubmission.objects.all(),\n",
        [SUB + "test_the_view_loads_only_the_viewers_own_submissions"],
    ),
    "A5_the_serializer_reads_any_loaded_submission": (
        "assignments/serializers.py",
        "                    if submission.student_id == request.user.id:\n",
        "                    if submission.student_id:\n",
        [SUB + "test_the_serializer_reads_only_the_requesters_own_of_what_was_loaded"],
    ),
    "A6_the_loaded_submission_is_never_found": (
        "assignments/serializers.py",
        "                    if submission.student_id == request.user.id:\n"
        "                        return submission\n",
        "                    if submission.student_id == request.user.id:\n"
        "                        return None\n",
        [
            SUB + "test_the_students_own_paper_shows_as_submitted",
            SUB + "test_a_classmates_paper_changes_nothing_the_student_reads",
            SUB
            + "test_the_serializer_reads_only_the_requesters_own_of_what_was_loaded",
        ],
    ),
    # ---- sessions
    "S1_the_schools_id_is_sent": (
        "classrooms/serializers.py",
        '    STUDENT_FIELD_VALUES = {"school": None, "created_by": None}\n',
        '    STUDENT_FIELD_VALUES = {"created_by": None}\n',
        [SESS + "test_a_student_is_sent_neither_id"],
    ),
    "S2_the_creators_id_is_sent": (
        "classrooms/serializers.py",
        '    STUDENT_FIELD_VALUES = {"school": None, "created_by": None}\n',
        '    STUDENT_FIELD_VALUES = {"school": None}\n',
        [SESS + "test_a_student_is_sent_neither_id"],
    ),
    "S3_the_teacher_loses_the_ids_too": (
        "classrooms/serializers.py",
        '        if getattr(user, "user_type", None) == UserTypes.STUDENT:\n'
        "            data.update(self.STUDENT_FIELD_VALUES)\n",
        "        if user is not None:\n"
        "            data.update(self.STUDENT_FIELD_VALUES)\n",
        [SESS + "test_the_teacher_still_sees_who_created_their_session"],
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
