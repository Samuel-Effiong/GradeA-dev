"""H-148 mutants. Each is one textual change to one production file; the
test modules run against it; the failing tests are recorded; the file is
restored. The runner is H-127's (docs/evidence/h127-student-feedback-
whitelist/mutate.py) with this list.

Written BEFORE any run: every mutant names the tests it must fail
(EXPECTED, full dotted names). A mutant is KILLED only if the inner run
ends non-zero, shows its own "Ran" line, loaded every module, and every
expected test is among those that failed. Other outcomes are reported as
what they are: SURVIVED (exit 0), KILLED_NOT_AS_EXPECTED (it failed, but
not all the named tests did), BROKEN (anything else). Only KILLED counts.

Eight of these mutate files this row does not change (users/serializers.py,
users/views.py, classrooms/models.py): the tests of the old rule that a
student cannot change their own name, of Google sign-in and of the name
clash are green from their first run, and these mutants are their proof
that they can fail (rule 19).

Rule 17: the inner run has PYTHONDONTWRITEBYTECODE=1 and every
__pycache__ under the mutated apps is deleted before each mutant and after
each restore. Rule 18: each inner run writes straight to a file
(mutant_logs/<name>.txt), stdin from /dev/null; nothing is piped.

    python docs/evidence/h148-teacher-names-student/mutate.py --check
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
    "classrooms.tests_teacher_names_student_on_add",
    "users.tests_student_cannot_name_themselves",
    "classrooms.tests_staff_invitations_promise_no_password_change",
]
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
HERE = pathlib.Path("docs/evidence/h148-teacher-names-student")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))
PYCACHE_ROOTS = [
    "classrooms",
    "users",
    "billing",
    "assignments",
    "students",
    "AutoGrader",
]

SER = "classrooms/serializers.py"
VIEWS = "classrooms/views.py"
ENROL = "classrooms/services/enrollment.py"
MAIL = "classrooms/services/notifications.py"
MODELS = "classrooms/models.py"
USER_SER = "users/serializers.py"
USER_VIEWS = "users/views.py"

T = "classrooms.tests_teacher_names_student_on_add."
REQ = T + "TheNameIsRequiredTest."
NEW = T + "ANewAddressTest."
CLASH = T + "TheNameClashRuleTest."
HAS = T + "AnAddressThatAlreadyHasAnAccountTest."
REFUSED = T + "ARefusalDoesNotDependOnTheNameTest."
ALONE = REQ + "test_an_email_alone_is_refused"
EACH = REQ + "test_each_of_the_two_names_is_required"
ONE_LETTER = REQ + "test_a_name_of_one_letter_is_refused"
CARRIES = NEW + "test_the_account_carries_the_typed_name"
TRIMMED = NEW + "test_the_typed_name_is_trimmed"
ANSWER = NEW + "test_the_answer_says_which_name_stands"
GREETS = NEW + "test_the_invitation_greets_by_the_typed_name"
NO_PROMISE = NEW + "test_the_invitation_does_not_promise_a_password_change"
CLASH_NEW = CLASH + "test_a_new_address_with_a_classmates_exact_name_is_refused"
CLASH_MIDDLE = CLASH + "test_a_different_middle_name_is_a_different_name"
CLASH_FILLED = (
    CLASH + "test_a_filled_name_that_clashes_is_refused_and_nothing_is_filled"
)
STANDS = HAS + "test_a_stored_name_stands_and_the_teacher_is_shown_it"
FILLED = HAS + "test_an_empty_name_is_filled_with_the_typed_one"
HALF = HAS + "test_half_a_name_is_a_name_and_stands"
NEVER_FILLED = (
    HAS + "test_a_nameless_student_who_never_signed_in_is_filled_and_invited_by_name"
)
NEVER_KEEPS = HAS + "test_a_named_student_who_never_signed_in_keeps_the_stored_name"
R_STAFF = REFUSED + "test_a_staff_address"
R_OFF = REFUSED + "test_a_switched_off_account"
R_IN = REFUSED + "test_a_student_already_in_the_course"
STAFF = (
    "classrooms.tests_staff_invitations_promise_no_password_change."
    "StaffInvitationsPromiseNoPasswordChangeTest."
)
STAFF_SENTENCE = (
    '                f"temporary password: {generated_password}"\n            ),\n'
)
STAFF_SENTENCE_BACK = (
    '                f"temporary password: {generated_password}\\n\\n"\n'
    '                "You\'ll be asked to choose your own password the first time "\n'
    '                "you log in."\n'
    "            ),\n"
)
IMPORT = T + "TheClassListImportFillsAnEmptyNameTooTest."
I_FILLED = IMPORT + "test_an_empty_name_is_filled_from_the_row"
I_NEVER = IMPORT + "test_a_nameless_student_who_never_signed_in_is_filled_and_invited"
I_STANDS = IMPORT + "test_a_stored_name_stands_and_the_row_shows_it"
I_CLASH = IMPORT + "test_a_filled_name_that_clashes_fails_the_row_and_fills_nothing"

U = "users.tests_student_cannot_name_themselves."
OWN = U + "AStudentCannotChangeTheirNameTest."
WHOSE = U + "WhoseAccountNotWhoAsksTest."
GOOGLE = U + "GoogleSignInWritesNoNameTest."
S_EACH = OWN + "test_each_of_the_three_names_is_refused_and_nothing_changes"
S_EMPTIED = OWN + "test_a_name_cannot_be_emptied_either"
S_NAMELESS = OWN + "test_a_student_with_no_name_cannot_give_themselves_one"
S_TYPE = OWN + "test_asking_to_become_a_teacher_in_the_same_request_does_not_help"
S_UNCHANGED = OWN + "test_the_whole_account_sent_back_unchanged_is_accepted"
W_ADMIN = WHOSE + "test_a_super_admin_is_refused_on_a_students_account_too"
W_TEACHER = WHOSE + "test_a_teacher_cannot_edit_their_students_account_at_all"
W_OWN_T = WHOSE + "test_a_teacher_still_changes_their_own_name"
W_OWN_A = WHOSE + "test_a_super_admin_still_changes_their_own_name"
G_NAMED = GOOGLE + "test_a_student_with_a_name_keeps_it"
G_NAMELESS = GOOGLE + "test_a_student_with_no_name_is_not_named_by_google"
G_NEVER = (
    GOOGLE + "test_a_student_who_never_activated_is_let_in_and_keeps_the_stored_name"
)

FORM_FIRST = (
    "    email = serializers.EmailField(required=True)\n"
    "    first_name = serializers.CharField(\n"
    "        max_length=150, validators=[MinLengthValidator(2)], required=True\n"
    "    )\n"
)
FORM_LAST = (
    "    last_name = serializers.CharField(\n"
    "        max_length=150, validators=[MinLengthValidator(2)], required=True\n"
    "    )\n"
    "\n"
    "    def validate_email(self, value):\n"
    '        """\n'
    "        Validate that the email:\n"
)
RULE = (
    "        if not is_creating and user_type == UserTypes.STUDENT:\n"
    '            for field in ["first_name", "middle_name", "last_name"]:\n'
    "                if field in attrs and attrs.get(field) != getattr(self.instance, field):\n"
)

MUTANTS = {
    # ---- the form
    "N01_the_first_name_is_optional": (
        SER,
        FORM_FIRST,
        FORM_FIRST.replace(
            "validators=[MinLengthValidator(2)], required=True",
            'required=False, default="", allow_blank=True',
        ),
        [ALONE, EACH],
    ),
    "N02_the_last_name_is_optional": (
        SER,
        FORM_LAST,
        FORM_LAST.replace(
            "validators=[MinLengthValidator(2)], required=True",
            'required=False, default="", allow_blank=True',
        ),
        [ALONE, EACH],
    ),
    "N03_a_first_name_of_one_letter_is_accepted": (
        SER,
        FORM_FIRST,
        FORM_FIRST.replace("validators=[MinLengthValidator(2)], ", ""),
        [ONE_LETTER],
    ),
    # ---- the route
    "N04_the_typed_name_is_not_passed_on": (
        VIEWS,
        "                fill_empty_name=True,\n                **typed,\n",
        "                fill_empty_name=True,\n",
        [CARRIES, TRIMMED, GREETS, CLASH_NEW, FILLED],
    ),
    "N05_an_empty_name_is_not_filled": (
        VIEWS,
        "                fill_empty_name=True,\n                **typed,\n",
        "                **typed,\n",
        [FILLED, NEVER_FILLED, CLASH_FILLED],
    ),
    "N11_the_answer_does_not_say_which_name_stands": (
        VIEWS,
        '                "student_name": student_name,\n',
        "",
        [ANSWER, STANDS, FILLED],
    ),
    "N12_the_answer_always_says_the_typed_name_was_used": (
        VIEWS,
        '                "typed_name_used": student_name == typed,\n',
        '                "typed_name_used": True,\n',
        [STANDS, HALF, NEVER_KEEPS],
    ),
    # ---- the fill rule
    "N06_a_stored_name_is_replaced": (
        ENROL,
        "        if fill_empty_name and first_name and last_name and _has_no_name(student):\n",
        "        if fill_empty_name and first_name and last_name:\n",
        [STANDS, HALF, NEVER_KEEPS, I_STANDS],
    ),
    "N07_half_a_name_counts_as_none": (
        ENROL,
        '        not (student.first_name or "").strip() and not (student.last_name or "").strip()\n',
        '        not (student.first_name or "").strip() or not (student.last_name or "").strip()\n',
        [HALF],
    ),
    "N08_the_fill_is_not_saved_for_a_student_who_has_signed_in": (
        ENROL,
        "            if name_fields:\n"
        "                student.save(update_fields=name_fields)\n",
        "",
        [FILLED],
    ),
    "N09_the_fill_is_not_saved_for_a_student_who_never_signed_in": (
        ENROL,
        "                *name_fields,\n",
        "",
        [NEVER_FILLED],
    ),
    "N15_the_import_does_not_fill_an_empty_name": (
        "classrooms/services/roster_import.py",
        "            fill_empty_name=True,\n",
        "",
        [I_FILLED, I_NEVER, I_CLASH],
    ),
    # ---- the email
    "N10_the_invitation_promises_a_password_change_again": (
        MAIL,
        '        f"temporary password: {generated_password}"\n    )\n',
        '        f"temporary password: {generated_password}\\n\\n"\n'
        '        "You\'ll be asked to choose your own password the first time you "\n'
        '        "log in."\n'
        "    )\n",
        [NO_PROMISE],
    ),
    "N16_the_school_admins_invitation_promises_again": (
        SER,
        STAFF_SENTENCE,
        STAFF_SENTENCE_BACK,
        [STAFF + "test_the_school_admins_invitation"],
    ),
    "N17_the_licensed_teachers_invitation_promises_again": (
        "billing/license_service.py",
        STAFF_SENTENCE,
        STAFF_SENTENCE_BACK,
        [STAFF + "test_the_licensed_teachers_invitation"],
    ),
    # ---- the name clash (files this row does not change)
    "N13_an_enrolment_does_not_check_the_name": (
        MODELS,
        "        if existing_enrollments.exists():\n"
        '            full_name = f"{first_name} {middle_name} {last_name}".replace(\n',
        "        if False:\n"
        '            full_name = f"{first_name} {middle_name} {last_name}".replace(\n',
        [CLASH_NEW, CLASH_FILLED],
    ),
    "N14_the_clash_ignores_the_middle_name": (
        MODELS,
        "            student__middle_name__iexact=middle_name,\n",
        "",
        [CLASH_MIDDLE],
    ),
    # ---- a refusal that names the account
    "R1_the_staff_refusal_names_the_account": (
        SER,
        "        # H-71: one neutral answer for every non-student role, so the form\n"
        "        # doesn't tell a teacher which addresses belong to staff.\n"
        "        if existing_user and existing_user.user_type != UserTypes.STUDENT:\n"
        "            raise serializers.ValidationError(NOT_A_STUDENT_MESSAGE)\n",
        "        # H-71: one neutral answer for every non-student role, so the form\n"
        "        # doesn't tell a teacher which addresses belong to staff.\n"
        "        if existing_user and existing_user.user_type != UserTypes.STUDENT:\n"
        "            raise serializers.ValidationError(\n"
        '                f"{existing_user.get_full_name()} is not a student."\n'
        "            )\n",
        [R_STAFF],
    ),
    "R2_the_switched_off_refusal_names_the_account": (
        ENROL,
        "            raise AccountDisabledError(DEACTIVATED_ACCOUNT_MESSAGE)\n",
        "            raise AccountDisabledError(\n"
        '                f"{student.get_full_name()}: {DEACTIVATED_ACCOUNT_MESSAGE}"\n'
        "            )\n",
        [R_OFF],
    ),
    "R3_the_already_enrolled_refusal_names_the_account": (
        ENROL,
        '            raise EnrollmentError("Student is already enrolled in this course.")\n',
        "            raise EnrollmentError(\n"
        '                f"{student.get_full_name()} is already enrolled in this course."\n'
        "            )\n",
        [R_IN],
    ),
    # ---- the old rule on the account edit (a file this row does not change)
    "S1_a_student_may_change_their_name": (
        USER_SER,
        RULE,
        RULE.replace("if not is_creating and", "if False and not is_creating and"),
        [S_EACH, S_EMPTIED, S_NAMELESS, S_TYPE, W_ADMIN],
    ),
    "S2_the_middle_name_is_free": (
        USER_SER,
        RULE,
        RULE.replace(
            '["first_name", "middle_name", "last_name"]', '["first_name", "last_name"]'
        ),
        [S_EACH, S_EMPTIED],
    ),
    "S3_an_unchanged_name_is_refused_too": (
        USER_SER,
        RULE,
        RULE.replace(" and attrs.get(field) != getattr(self.instance, field)", ""),
        [S_UNCHANGED],
    ),
    "S4_nobody_may_change_a_name": (
        USER_SER,
        RULE,
        RULE.replace(" and user_type == UserTypes.STUDENT", ""),
        [W_OWN_T, W_OWN_A],
    ),
    "S5_a_teacher_may_edit_a_students_account": (
        USER_VIEWS,
        "        if instance.pk != request.user.pk and not is_super_admin:\n"
        '            raise PermissionDenied("You can only modify your own account.")\n',
        "        if False:\n"
        '            raise PermissionDenied("You can only modify your own account.")\n',
        [W_TEACHER],
    ),
    # ---- Google sign-in (a file this row does not change)
    "G1_google_writes_its_name_on_an_account_that_exists": (
        USER_VIEWS,
        "                    resurrected_fields = []\n",
        "                    user.first_name = first_name\n"
        "                    user.middle_name = middle_name\n"
        "                    user.last_name = last_name\n"
        '                    user.save(update_fields=["first_name", "middle_name", "last_name"])\n'
        "                    resurrected_fields = []\n",
        [G_NAMED, G_NAMELESS, G_NEVER],
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
