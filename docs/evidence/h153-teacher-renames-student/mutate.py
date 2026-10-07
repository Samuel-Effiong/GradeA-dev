"""H-153 mutants. Each is one textual change to one production file; the
test module runs against it; the failing tests are recorded; the file is
restored. The runner is H-127's (docs/evidence/h127-student-feedback-
whitelist/mutate.py) with this list.

Written BEFORE any run: every mutant names the tests it must fail
(EXPECTED, full dotted names). A mutant is KILLED only if the inner run
ends non-zero, shows its own "Ran" line, loaded every module, and every
expected test is among those that failed. Other outcomes are reported as
what they are: SURVIVED (exit 0), KILLED_NOT_AS_EXPECTED (it failed, but
not all the named tests did), BROKEN (anything else). Only KILLED counts.

The route these tests call did not exist when they were first committed,
so their first red (an error at the URL lookup) proved nothing test by
test. Every one of the module's 26 tests is therefore named by at least
one mutant below (Senior Manager, 2026-10-07); COVERED at the end of the
list is checked by --check.

Rule 17: the inner run has PYTHONDONTWRITEBYTECODE=1 and every
__pycache__ under the mutated apps is deleted before each mutant and after
each restore. Rule 18: each inner run writes straight to a file
(mutant_logs/<name>.txt), stdin from /dev/null; nothing is piped.

    python docs/evidence/h153-teacher-renames-student/mutate.py --check
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

TESTS = ["users.tests_teacher_renames_student"]
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
HERE = pathlib.Path("docs/evidence/h153-teacher-renames-student")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))
PYCACHE_ROOTS = ["classrooms", "users", "assignments", "students", "AutoGrader"]

VIEWS = "users/views.py"
SER = "users/serializers.py"

T = "users.tests_teacher_renames_student."
A = T + "ATeacherRenamesTheirStudentTest."
W = T + "WhoMayNotRenameTest."
N = T + "AStudentWithNoCurrentTeacherTest."
C = T + "TheNameClashRuleTest."
L = T + "EachRenameIsRecordedTest."
CARRIES = A + "test_the_name_is_changed_and_the_answer_carries_it"
TRIMMED = A + "test_the_name_is_trimmed_and_the_middle_name_is_optional"
NAMELESS = A + "test_a_student_with_no_name_is_named_this_way"
PENDING = A + "test_a_pending_student_can_be_named_before_they_first_sign_in"
EITHER = A + "test_either_of_two_teachers_may_and_the_other_sees_the_new_name"
TWO_LETTERS = A + "test_each_name_must_have_two_letters_and_cannot_be_blank"
ONLY_NAME = A + "test_nothing_but_the_name_can_be_changed_here"
ADMIN = W + "test_a_school_admin_may_not"
ADMIN_TEACHER = (
    W + "test_a_school_admin_who_is_still_named_as_a_courses_teacher_may_not"
)
FORMER_ELSEWHERE = (
    W + "test_a_former_teacher_may_not_though_the_student_has_another_teacher_now"
)
STUDENT = W + "test_the_student_may_not"
CLASSMATE = W + "test_a_classmate_may_not"
STRANGER = W + "test_a_teacher_with_no_course_of_the_student_may_not"
OUTSIDER = W + "test_an_outsider_cannot_tell_a_student_from_no_account_at_all"
WITHDRAWN = W + "test_a_teacher_whose_student_has_withdrawn_or_completed_may_not"
NOT_A_STUDENT = W + "test_only_a_student_can_be_renamed_here"
FORMER = N + "test_their_former_teacher_may_not"
SUPER = N + "test_a_super_admin_may"
OWN_CLASH = C + "test_a_clash_in_the_teachers_own_course_is_refused_and_quoted"
ELSEWHERE = C + "test_a_clash_in_another_teachers_course_is_refused_without_the_name"
GONE_MATE = C + "test_a_withdrawn_classmate_still_holds_the_name"
LEFT_COURSE = C + "test_a_course_the_student_has_withdrawn_from_still_counts"
OWN_NAME = C + "test_keeping_the_students_own_name_is_no_clash"
LOG_LINE = L + "test_a_rename_writes_one_line_with_ids_and_no_name_or_address"
LOG_SUPER = L + "test_a_super_admins_rename_is_recorded_with_no_course"
LOG_REFUSED = L + "test_a_refused_rename_writes_no_such_line"

ALL_TESTS = [
    CARRIES, TRIMMED, NAMELESS, PENDING, EITHER, TWO_LETTERS, ONLY_NAME,
    ADMIN, ADMIN_TEACHER, FORMER_ELSEWHERE, STUDENT, CLASSMATE, STRANGER,
    OUTSIDER, WITHDRAWN, NOT_A_STUDENT, FORMER, SUPER, OWN_CLASH, ELSEWHERE,
    GONE_MATE, LEFT_COURSE, OWN_NAME, LOG_LINE, LOG_SUPER, LOG_REFUSED,
]  # fmt: skip

WHO = (
    "        student = self.get_object()\n"
    "        actor = request.user\n"
    "        is_super_admin = actor.is_superuser and actor.user_type == UserTypes.SUPER_ADMIN\n"
    "        if not is_super_admin and actor.user_type != UserTypes.TEACHER:\n"
)
SAVE = "            student.save(update_fields=list(names))\n"
QUOTED = "                if is_super_admin or enrolment.course_id in through:\n"

MUTANTS = {
    # ---- the rename itself
    "M01_nothing_is_saved": (
        VIEWS,
        SAVE,
        "            pass\n",
        [CARRIES, TRIMMED, NAMELESS, PENDING, EITHER, SUPER],
    ),
    "M02_the_middle_name_is_not_taken": (
        VIEWS,
        '            "middle_name": serializer.validated_data.get("middle_name", ""),\n',
        '            "middle_name": student.middle_name or "",\n',
        [CARRIES],
    ),
    "M03_a_first_name_of_one_letter_is_accepted": (
        SER,
        "    first_name = serializers.CharField(\n"
        "        max_length=150, validators=[MinLengthValidator(2)], required=True\n"
        "    )\n"
        '    middle_name = serializers.CharField(max_length=150, default="", allow_blank=True)\n',
        "    first_name = serializers.CharField(max_length=150, required=True)\n"
        '    middle_name = serializers.CharField(max_length=150, default="", allow_blank=True)\n',
        [TWO_LETTERS],
    ),
    "M04_the_last_name_is_not_trimmed": (
        SER,
        '    middle_name = serializers.CharField(max_length=150, default="", allow_blank=True)\n'
        "    last_name = serializers.CharField(\n"
        "        max_length=150, validators=[MinLengthValidator(2)], required=True\n"
        "    )\n",
        '    middle_name = serializers.CharField(max_length=150, default="", allow_blank=True)\n'
        "    last_name = serializers.CharField(\n"
        "        max_length=150, trim_whitespace=False, required=True\n"
        "    )\n",
        [TRIMMED],
    ),
    "M05_other_fields_sent_with_the_name_are_saved_too": (
        VIEWS,
        SAVE,
        '            student.bio = request.data.get("bio", student.bio)\n'
        '            student.save(update_fields=[*names, "bio"])\n',
        [ONLY_NAME],
    ),
    "M06_the_answer_does_not_carry_the_name": (
        VIEWS,
        '        return Response({"id": str(student.pk), **names}, status=status.HTTP_200_OK)\n',
        '        return Response({"id": str(student.pk)}, status=status.HTTP_200_OK)\n',
        [CARRIES],
    ),
    # ---- who may
    "M07_everyone_who_can_read_the_account_is_treated_as_a_super_admin": (
        VIEWS,
        WHO,
        WHO.replace(
            "is_super_admin = actor.is_superuser and actor.user_type == UserTypes.SUPER_ADMIN",
            "is_super_admin = True",
        ),
        [ADMIN, STUDENT, WITHDRAWN, ELSEWHERE, LEFT_COURSE, LOG_REFUSED],
    ),
    "M08_the_role_is_not_asked_only_the_course": (
        VIEWS,
        WHO,
        WHO.replace(
            "if not is_super_admin and actor.user_type != UserTypes.TEACHER:",
            "if False:",
        ),
        [ADMIN_TEACHER],
    ),
    "M09_a_super_admin_is_not_let_in": (
        VIEWS,
        WHO,
        WHO.replace(
            "if not is_super_admin and actor.user_type != UserTypes.TEACHER:",
            "if actor.user_type != UserTypes.TEACHER:",
        ),
        [SUPER, LOG_SUPER, NOT_A_STUDENT],
    ),
    "M10_any_account_that_exists_can_be_reached": (
        VIEWS,
        WHO,
        WHO.replace(
            "student = self.get_object()",
            "student = CustomUser.objects.filter(pk=pk).first() or self.get_object()",
        ),
        [STRANGER, OUTSIDER, FORMER],
    ),
    "M11_any_account_can_be_reached_by_anyone": (
        VIEWS,
        WHO,
        WHO.replace(
            "student = self.get_object()",
            "student = CustomUser.objects.filter(pk=pk).first() or self.get_object()",
        ).replace(
            "is_super_admin = actor.is_superuser and actor.user_type == UserTypes.SUPER_ADMIN",
            "is_super_admin = True",
        ),
        [CLASSMATE, STRANGER],
    ),
    "M12_a_teacher_needs_no_current_place_of_the_student": (
        VIEWS,
        "            if not through:\n",
        "            if False:\n",
        [WITHDRAWN],
    ),
    "M13_a_pending_place_does_not_count": (
        VIEWS,
        "                        EnrollmentStatusType.ENROLLED,\n"
        "                        EnrollmentStatusType.PENDING,\n",
        "                        EnrollmentStatusType.ENROLLED,\n",
        [PENDING],
    ),
    "M14_any_teachers_course_counts_as_the_callers": (
        VIEWS,
        '                    teacher_course_access_q(actor, prefix="course__"),\n',
        "",
        [FORMER_ELSEWHERE],
    ),
    "M15_an_account_that_is_not_a_students_can_be_renamed": (
        VIEWS,
        "        if student.user_type != UserTypes.STUDENT:\n",
        "        if False:\n",
        [NOT_A_STUDENT],
    ),
    # ---- the name clash
    "M16_no_clash_is_looked_for": (
        VIEWS,
        "                if not clash:\n                    continue\n",
        "                if True:\n                    continue\n",
        [OWN_CLASH, ELSEWHERE, GONE_MATE, LEFT_COURSE],
    ),
    "M17_a_clash_elsewhere_is_quoted_to_the_caller": (
        VIEWS,
        QUOTED,
        "                if True:\n",
        [ELSEWHERE, LEFT_COURSE],
    ),
    "M18_a_clash_in_the_callers_own_course_is_not_quoted": (
        VIEWS,
        QUOTED,
        "                if False:\n",
        [OWN_CLASH, GONE_MATE],
    ),
    "M19_a_course_the_student_has_withdrawn_from_is_not_checked": (
        VIEWS,
        "                .filter(student=student)\n"
        '                .select_related("course", "course__session")\n',
        "                .filter(student=student)\n"
        "                .exclude(enrollment_status=EnrollmentStatusType.WITHDRAWN)\n"
        '                .select_related("course", "course__session")\n',
        [LEFT_COURSE],
    ),
    "M20_a_withdrawn_classmate_does_not_hold_the_name": (
        VIEWS,
        "                    **names,\n                ).exists()\n",
        "                    **names,\n"
        "                ).exclude(\n"
        "                    enrollment_status=EnrollmentStatusType.WITHDRAWN\n"
        "                ).exists()\n",
        [GONE_MATE],
    ),
    "M21_the_students_own_name_counts_as_a_clash": (
        VIEWS,
        "                    exclude_student_id=student.pk,\n",
        "                    exclude_student_id=None,\n",
        [OWN_NAME],
    ),
    # ---- the record
    "M22_no_line_is_written": (
        VIEWS,
        "        student_names_logger.info(\n",
        "        logger.debug(\n",
        [LOG_LINE, LOG_SUPER],
    ),
    "M23_the_line_carries_the_name_instead_of_the_students_id": (
        VIEWS,
        "            actor.pk,\n            student.pk,\n",
        "            actor.pk,\n            student.get_full_name(),\n",
        [LOG_LINE],
    ),
    "M24_the_line_names_no_course": (
        VIEWS,
        '            ",".join(str(course_id) for course_id in through) or "none",\n',
        '            "none",\n',
        [LOG_LINE],
    ),
}

COVERED = {test for mutant in MUTANTS.values() for test in mutant[3]}
assert COVERED == set(ALL_TESTS), sorted(set(ALL_TESTS) - COVERED)


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
