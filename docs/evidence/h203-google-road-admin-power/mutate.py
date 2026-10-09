"""H-203 mutants. Each is one textual change to users/views.py, users/admin_power.py,
classrooms/services/enrollment.py, billing/license_service.py or users/services.py;
the new modules run against it; the failing tests
are recorded; the file is restored. The runner is H-164's.

Written BEFORE any run: every mutant names the tests it must fail (EXPECTED, full dotted names). KILLED needs a non-zero
inner run with its own "Ran" line, every module loaded and every expected test among the failures.

Rule 17: PYTHONDONTWRITEBYTECODE=1 and __pycache__ deleted before each mutant and after each restore.
Rule 18: each inner run writes straight to a file; nothing is piped.

    python docs/evidence/h203-google-road-admin-power/mutate.py --check
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
    "users.tests_admin_power_principle",
    "users.tests_roads_that_sign_in_or_activate",
    "billing.tests.test_licence_invite_admin_power",
    "classrooms.tests_h203_admin_power_routes",
]
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
HERE = pathlib.Path("docs/evidence/h203-google-road-admin-power")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))
PYCACHE_ROOTS = ["billing", "users", "AutoGrader", "classrooms"]

VIEWS = "users/views.py"
POWER = "users/admin_power.py"
ENROLL = "classrooms/services/enrollment.py"
SERVICES = "users/services.py"
LICENCE = "billing/license_service.py"

G = "users.tests_admin_power_principle.GoogleRoadTests."
S = "users.tests_admin_power_principle.SchoolAdminInvitationRoadTests."
E = "users.tests_admin_power_principle.AddStudentByEmailRoadTests."
P = "users.tests_roads_that_sign_in_or_activate.RoadsThatSignInOrActivateTests."
GS = G + "test_google_refuses_a_never_verified_staff_account"
GF = G + "test_google_refuses_a_never_verified_superuser_flag"
GT = G + "test_google_refuses_a_never_verified_super_admin_type"
GC = G + "test_google_refuses_a_command_line_superuser"
GW = G + "test_that_refusal_is_the_failed_google_sign_in_answer"
GV = G + "test_a_verified_admin_still_signs_in_with_google"
GO = G + "test_an_ordinary_never_verified_user_is_still_completed_by_google"
IS = S + "test_the_invitation_road_refuses_a_pending_row_carrying_is_staff"
ISU = S + "test_the_invitation_road_refuses_a_pending_row_carrying_is_superuser"
IW = S + "test_that_refusal_is_the_invalid_invitation_answer"
IO = S + "test_an_ordinary_pending_school_admin_still_registers"
ES = E + "test_a_student_typed_row_carrying_is_staff_is_refused"
EF = E + "test_a_student_typed_row_carrying_is_superuser_is_refused"
EO = E + "test_an_ordinary_student_is_still_enrolled"
PIN = P + "test_every_site_is_on_the_named_list_with_its_count"
L = "billing.tests.test_licence_invite_admin_power.LicenceInviteRoadTests."
LS = L + "test_a_never_verified_staff_teacher_is_refused_and_untouched"
LF = L + "test_a_never_verified_superuser_flag_teacher_is_refused_and_untouched"
LC = L + "test_a_command_line_superuser_is_refused_and_untouched"
LN = L + "test_the_non_raising_path_returns_none_and_writes_nothing"
LO = L + "test_a_switched_off_verified_teacher_is_refused_and_untouched"
LW = L + "test_the_refusal_does_not_say_which_school_the_row_belongs_to"
LR = L + "test_through_the_route_the_row_fails_and_no_seat_or_mail_is_used"
LK_PLAIN = (
    L + "test_an_ordinary_never_signed_in_teacher_still_gets_a_new_password_and_mail"
)
LK_PENDING = L + "test_an_ordinary_never_verified_inactive_teacher_is_still_added"
LK_ONBOARDED = L + "test_a_verified_staff_teacher_who_has_onboarded_is_still_added"
LK_STUDENT = L + "test_an_account_that_is_not_a_teacher_gets_the_same_words"
R = "classrooms.tests_h203_admin_power_routes.StudentAddRoutesAdminPowerTests."
RS1 = R + "test_single_add_refuses_a_student_typed_row_carrying_is_staff"
RS2 = R + "test_single_add_refuses_a_student_typed_row_carrying_is_superuser"
RB1 = R + "test_bulk_import_refuses_a_student_typed_row_carrying_is_staff"
RB2 = R + "test_bulk_import_refuses_a_student_typed_row_carrying_is_superuser"
RD1 = R + "test_direct_add_refuses_a_student_typed_row_carrying_is_staff"
RD2 = R + "test_direct_add_refuses_a_student_typed_row_carrying_is_superuser"
RK1 = R + "test_single_add_still_adds_an_ordinary_student"
RK2 = R + "test_bulk_import_still_adds_an_ordinary_student"
RK3 = R + "test_direct_add_still_adds_an_ordinary_student"

HELPER = "        user.is_staff or user.is_superuser or user.user_type == UserTypes.SUPER_ADMIN\n"
G_ARM = (
    "                    if user.email_verified_at is None and _holds_admin_power(user):\n"
    "                        raise ParseError(GOOGLE_SIGN_IN_FAILED)\n"
)
S_ARM = (
    "                if not user or _holds_admin_power(user):\n"
    '                    raise ParseError("Invalid or expired activation token.")\n'
)
E_ARM = "    if student.user_type != UserTypes.STUDENT or holds_admin_power(student):\n"
L_GUARD = (
    "            if (holds_admin_power(user) and user.email_verified_at is None) or (\n"
    "                user.email_verified_at is not None and not user.is_active\n"
    "            ):\n"
)
L_BLOCK = (
    L_GUARD
    + '                error_msg = "This email can\'t be added as a teacher."\n'
    + "                logger.warning(\n"
    + '                    "User %s may not be invited by licence: not enrolled.", user.id\n'
    + "                )\n"
    + "                if raise_on_conflict:\n"
    + "                    raise ValueError(error_msg)\n"
    + "                return None\n\n"
)

MUTANTS = {
    "T1_the_helper_forgets_is_staff": (
        POWER,
        HELPER,
        HELPER.replace("user.is_staff or ", ""),
        [GS, IS, IW, ES, LN, LW, LR, RS1, RB1, RD1],
    ),
    "T2_the_helper_forgets_is_superuser": (
        POWER,
        HELPER,
        HELPER.replace("user.is_superuser or ", ""),
        [GF, GW, ISU, EF, LF, RS2, RB2, RD2],
    ),
    "T3_the_helper_forgets_the_super_admin_type": (
        POWER,
        HELPER,
        HELPER.replace(" or user.user_type == UserTypes.SUPER_ADMIN", ""),
        [GT],
    ),
    "T4_google_has_no_admin_power_refusal": (VIEWS, G_ARM, "", [GS, GF, GT, GC, GW]),
    "T5_google_refuses_every_never_verified_account": (
        VIEWS,
        G_ARM,
        G_ARM.replace(" and _holds_admin_power(user)", ""),
        [GO],
    ),
    "T6_google_refuses_a_verified_admin_too": (
        VIEWS,
        G_ARM,
        G_ARM.replace("user.email_verified_at is None and ", ""),
        [GV],
    ),
    "T7_google_answers_an_admin_in_its_own_words": (
        VIEWS,
        G_ARM,
        G_ARM.replace("GOOGLE_SIGN_IN_FAILED", '"Admin accounts cannot use Google."'),
        [GW],
    ),
    "T8_google_stamps_the_email_before_refusing": (
        VIEWS,
        G_ARM,
        G_ARM.replace(
            "                        raise",
            "                        user.email_verified_at = timezone.now()\n"
            '                        user.save(update_fields=["email_verified_at"])\n'
            "                        raise",
        ),
        [GS, GF, GT, GC],
    ),
    "T9_the_invitation_road_has_no_admin_power_refusal": (
        VIEWS,
        S_ARM,
        S_ARM.replace(" or _holds_admin_power(user)", ""),
        [IS, ISU, IW],
    ),
    "T10_the_invitation_road_refuses_every_pending_admin": (
        VIEWS,
        S_ARM,
        S_ARM.replace("_holds_admin_power(user)", "True"),
        [IO],
    ),
    "T11_the_invitation_road_answers_an_admin_in_its_own_words": (
        VIEWS,
        S_ARM,
        "                if not user:\n"
        '                    raise ParseError("Invalid or expired activation token.")\n'
        "                if _holds_admin_power(user):\n"
        '                    raise ParseError("Not an invitation for this account.")\n',
        [IW],
    ),
    "T12_add_by_email_has_no_admin_power_refusal": (
        ENROLL,
        E_ARM,
        E_ARM.replace(" or holds_admin_power(student)", ""),
        [ES, EF, RS1, RS2, RB1, RB2, RD1, RD2],
    ),
    "T13_add_by_email_refuses_every_student": (
        ENROLL,
        E_ARM,
        E_ARM.replace("holds_admin_power(student)", "True"),
        [EO, RK1, RK2, RK3],
    ),
    "T14_a_new_site_that_switches_an_account_on_is_added": (
        SERVICES,
        "def stamp_last_login(user):",
        '_H203_PROBE = {"is_active": True}\n\n\ndef stamp_last_login(user):',
        [PIN],
    ),
    "T15_the_licence_road_has_no_guard": (
        LICENCE,
        L_BLOCK,
        "",
        [LS, LF, LC, LN, LO, LW, LR],
    ),
    "T16_the_licence_guard_forgets_the_switched_off_clause": (
        LICENCE,
        L_GUARD,
        L_GUARD.replace(
            "(\n                user.email_verified_at is not None and not user.is_active\n            )",
            "(\n                False\n            )",
        ),
        [LO],
    ),
    "T17_the_licence_guard_forgets_the_admin_power_clause": (
        LICENCE,
        L_GUARD,
        L_GUARD.replace("holds_admin_power(user) and ", ""),
        [LK_PLAIN, LK_PENDING],
    ),
    "T18_the_licence_guard_refuses_a_verified_admin_too": (
        LICENCE,
        L_GUARD,
        L_GUARD.replace(" and user.email_verified_at is None)", ")", 1),
        [LK_ONBOARDED],
    ),
    "T19_the_licence_guard_answers_in_its_own_words": (
        LICENCE,
        L_BLOCK,
        L_BLOCK.replace(
            "This email can't be added as a teacher.", "Not for a licence."
        ),
        [LS, LF, LC, LO, LW, LR],
    ),
    "T20_the_licence_guard_attaches_the_school_before_refusing": (
        LICENCE,
        L_BLOCK,
        L_BLOCK.replace(
            "                error_msg =",
            "                user.school = school\n"
            '                user.save(update_fields=["school"])\n'
            "                error_msg =",
            1,
        ),
        [LS, LF, LC, LN, LO],
    ),
    "T21_a_new_site_that_sets_a_password_is_added": (
        SERVICES,
        "def stamp_last_login(user):",
        "def _h203_probe(account):\n    account.set_password(None)\n\n\ndef stamp_last_login(user):",
        [PIN],
    ),
    "T22_a_new_site_that_makes_a_credential_is_added": (
        SERVICES,
        "def stamp_last_login(user):",
        '_H203_PROBE = "generate_temporary_password("\n\n\ndef stamp_last_login(user):',
        [PIN],
    ),
    "T23_the_not_a_teacher_answer_changes_its_words": (
        LICENCE,
        'error_msg = "This email can\'t be added as a teacher."\n'
        '                logger.warning("User %s is not a teacher: not enrolled.", user.id)',
        'error_msg = "Not a teacher."\n'
        '                logger.warning("User %s is not a teacher: not enrolled.", user.id)',
        [LK_STUDENT],
    ),
}

NEW_TESTS = [
    GS,
    GF,
    GT,
    GC,
    GW,
    GV,
    GO,
    IS,
    ISU,
    IW,
    IO,
    ES,
    EF,
    EO,
    PIN,
    LS,
    LF,
    LC,
    LN,
    LO,
    LW,
    LR,
    LK_PLAIN,
    LK_PENDING,
    LK_ONBOARDED,
    LK_STUDENT,
    RS1,
    RS2,
    RB1,
    RB2,
    RD1,
    RD2,
    RK1,
    RK2,
    RK3,
]

COVERED = {test for mutant in MUTANTS.values() for test in mutant[3]}
assert set(NEW_TESTS) <= COVERED, sorted(set(NEW_TESTS) - COVERED)


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
        exit_status: int | str
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
