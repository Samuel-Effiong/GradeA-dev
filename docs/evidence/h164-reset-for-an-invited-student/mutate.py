"""H-164 mutants. Each is one textual change to users/views.py; the new test module and the existing OTP and
reset modules run against it; the failing tests are recorded; the file is restored. The runner is H-178's.

Written BEFORE any run: every mutant names the tests it must fail (EXPECTED, full dotted names). KILLED needs a non-zero
inner run with its own "Ran" line, every module loaded and every expected test among the failures; other outcomes are
reported as what they are (SURVIVED, KILLED_NOT_AS_EXPECTED, BROKEN). EVIDENCE.md says, for each mutant, whether the
failing set is exactly the written one and names any extra.

Rule 17: PYTHONDONTWRITEBYTECODE=1 and __pycache__ deleted before each mutant and after each restore.
Rule 18: each inner run writes straight to a file; nothing is piped.

    python docs/evidence/h164-reset-for-an-invited-student/mutate.py --check
checks the anchors (each exactly once) and that every mutant parses, runs nothing.
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
    "users.tests_reset_for_an_invited_student",
    "users.tests_otp_no_oracle",
    "users.tests_reset_otp_budget",
]
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
HERE = pathlib.Path("docs/evidence/h164-reset-for-an-invited-student")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))
PYCACHE_ROOTS = ["billing", "users", "AutoGrader", "classrooms"]

VIEWS = "users/views.py"

C = "users.tests_reset_for_an_invited_student.InvitedStudentResetTests."
T1 = C + "test_an_invited_student_who_never_signed_in_is_sent_a_reset_code"
T2 = C + "test_the_reply_is_the_same_as_for_an_address_with_no_account"
T3 = C + "test_an_inactive_never_verified_row_is_still_refused"
T4 = C + "test_a_locked_reset_sends_nothing_and_answers_the_same"
T5 = C + "test_the_request_alone_does_not_verify_the_email"
T6 = C + "test_a_successful_reset_sets_the_password_and_stamps_the_email"
T7 = C + "test_a_wrong_code_stamps_nothing"
T8 = C + "test_a_successful_reset_clears_must_change_password"
T9 = C + "test_a_reset_leaves_an_established_accounts_flag_alone"
T10 = C + "test_a_reset_signs_the_student_in"
T11 = C + "test_a_later_add_to_another_course_keeps_the_password_they_chose"
# T9 is a guard, green on the old code and under every mutant below but R7.
T12 = C + "test_a_reset_is_refused_for_a_switched_off_never_verified_account"
T13 = C + "test_that_refusal_is_the_same_as_for_an_address_with_no_account"
T14 = C + "test_a_refused_reset_leaves_the_code_and_its_budget_alone"
T15 = C + "test_a_switched_off_account_that_verified_its_email_still_resets"
# N3 (Senior Manager's ruling): the licence teacher road and no road for a
# never-verified account with admin power.
LREQ = C + "test_a_licence_invited_teacher_is_sent_a_reset_code"
LRESET = C + "test_a_licence_invited_teachers_reset_stamps_and_signs_them_in"
LREADD = C + "test_a_licence_re_add_after_a_reset_keeps_the_password_they_chose"
QSTAFF = C + "test_the_request_refuses_a_never_verified_staff_account"
QFLAG = C + "test_the_request_refuses_a_never_verified_superuser_flag"
QTYPE = C + "test_the_request_refuses_a_never_verified_super_admin_type"
QCMD = C + "test_the_request_refuses_a_command_line_superuser"
QWORDS = C + "test_that_refusal_says_what_the_inactive_refusal_says"
RSTAFF = C + "test_the_reset_refuses_a_never_verified_staff_account"
RFLAG = C + "test_the_reset_refuses_a_never_verified_superuser_flag"
RTYPE = C + "test_the_reset_refuses_a_never_verified_super_admin_type"
RCMD = C + "test_the_reset_refuses_a_command_line_superuser"
RWORDS = C + "test_that_reset_refusal_is_the_same_as_for_an_address_with_no_account"
CVREQ = C + "test_a_verified_super_admin_is_still_sent_a_reset_code"
CVRESET = C + "test_a_verified_super_admin_can_still_reset"
# The four reset-step refusal tests of a never-verified account with admin power.
RPOWER = [RSTAFF, RFLAG, RTYPE, RCMD]
QPOWER = [QSTAFF, QFLAG, QTYPE, QCMD]
NEW_TESTS = [T1, T2, T3, T4, T5, T6, T7, T8, T10, T11, T12, T13, T14]
NEW_TESTS += [LREQ, LRESET, LREADD] + QPOWER + [QWORDS] + RPOWER + [RWORDS]

REFUSE = (
    "            if not user.email_verified_at and (\n"
    "                not user.is_active or _holds_admin_power(user)\n"
    "            ):\n"
)
HELPER = "        user.is_staff or user.is_superuser or user.user_type == UserTypes.SUPER_ADMIN\n"
GUARD = (
    "        if not user.email_verified_at and (\n"
    "            not user.is_active or _holds_admin_power(user)\n"
    "        ):\n"
    '            raise ParseError("Invalid email, OTP code, or new password.")\n'
)
STAMP = (
    "        if not user.email_verified_at:\n"
    "            user.email_verified_at = timezone.now()\n"
)

MUTANTS = {
    "R1_the_request_refuses_every_never_verified_account_again": (
        VIEWS,
        REFUSE,
        "            if not user.email_verified_at:\n",
        [T1, T2, T4, LREQ],
    ),
    "R2_the_request_refuses_nobody": (
        VIEWS,
        REFUSE,
        "            if False:\n",
        [T3] + QPOWER,
    ),
    "R3_the_reset_does_not_stamp_the_email": (
        VIEWS,
        STAMP,
        "        if False:\n            user.email_verified_at = timezone.now()\n",
        [T6, LRESET],
    ),
    "R4_the_request_stamps_the_email": (
        VIEWS,
        '                raise ParseError("Email not verified.")\n',
        '                raise ParseError("Email not verified.")\n'
        "            if not user.email_verified_at:\n"
        "                user.email_verified_at = timezone.now()\n"
        '                user.save(update_fields=["email_verified_at"])\n',
        [T5],
    ),
    "R5_the_reset_keeps_must_change_password": (
        VIEWS,
        "        user.must_change_password = False\n        user.save()\n        stamp_last_login(user)\n",
        "        user.save()\n        stamp_last_login(user)\n",
        [T8, LRESET, LREADD],
    ),
    "R6_the_reset_does_not_stamp_last_login": (
        VIEWS,
        "        user.save()\n        stamp_last_login(user)\n",
        "        user.save()\n",
        [T10, T11, LRESET],
    ),
    "R7_a_wrong_code_stamps_the_email": (
        VIEWS,
        "            otp_obj.register_failure()\n            # The guess that spends the budget",
        "            user.email_verified_at = user.email_verified_at or timezone.now()\n"
        '            user.save(update_fields=["email_verified_at"])\n'
        "            otp_obj.register_failure()\n            # The guess that spends the budget",
        [T7],
    ),
    # Delta (Senior Manager's ruling, 2026-10-08): the guard in reset_password.
    "R8_the_reset_guard_is_removed": (
        VIEWS,
        GUARD,
        "",
        [T12, T13, T14] + RPOWER + [RWORDS],
    ),
    "R9_the_guard_refuses_every_switched_off_account": (
        VIEWS,
        GUARD,
        GUARD.replace("not user.email_verified_at and ", ""),
        [T15, CVRESET],
    ),
    "R10_the_guard_answers_in_its_own_words": (
        VIEWS,
        GUARD,
        GUARD.replace(
            "Invalid email, OTP code, or new password.", "Account is not active."
        ),
        [T13, RWORDS],
    ),
    "R11_the_guard_deletes_the_issued_code": (
        VIEWS,
        GUARD,
        GUARD.replace(
            "            raise", "            otp_obj.delete()\n            raise"
        ),
        [T14],
    ),
    "R12_the_guard_stamps_the_email_before_refusing": (
        VIEWS,
        GUARD,
        GUARD.replace(
            "            raise",
            "            CustomUser.objects.filter(pk=user.pk).update(\n"
            "                email_verified_at=timezone.now()\n"
            "            )\n"
            "            raise",
        ),
        [T12] + RPOWER,
    ),
    "R13_the_guard_sets_the_password_before_refusing": (
        VIEWS,
        GUARD,
        GUARD.replace(
            "            raise",
            "            user.set_password(new_password)\n            user.save()\n            raise",
        ),
        [T12] + RPOWER,
    ),
    # N3: the admin-power refusal, per dropped condition and per step.
    "R14_the_helper_forgets_is_staff": (
        VIEWS,
        HELPER,
        HELPER.replace("user.is_staff or ", ""),
        [QSTAFF, RSTAFF],
    ),
    "R15_the_helper_forgets_is_superuser": (
        VIEWS,
        HELPER,
        HELPER.replace("user.is_superuser or ", ""),
        [QFLAG, RFLAG, QWORDS, RWORDS],
    ),
    "R16_the_helper_forgets_the_super_admin_type": (
        VIEWS,
        HELPER,
        HELPER.replace(" or user.user_type == UserTypes.SUPER_ADMIN", ""),
        [QTYPE, RTYPE],
    ),
    "R17_the_request_has_no_admin_power_refusal": (
        VIEWS,
        REFUSE,
        "            if not user.email_verified_at and not user.is_active:\n",
        QPOWER + [QWORDS],
    ),
    "R18_the_request_refuses_a_verified_admin_too": (
        VIEWS,
        REFUSE,
        "            if (not user.email_verified_at and not user.is_active) or _holds_admin_power(user):\n",
        [CVREQ],
    ),
    "R19_the_request_answers_an_admin_in_its_own_words": (
        VIEWS,
        REFUSE + '                raise ParseError("Email not verified.")\n',
        "            if not user.email_verified_at and not user.is_active:\n"
        '                raise ParseError("Email not verified.")\n'
        "            if not user.email_verified_at and _holds_admin_power(user):\n"
        '                raise ParseError("Account is not verified.")\n',
        [QWORDS],
    ),
    "R20_the_reset_has_no_admin_power_refusal": (
        VIEWS,
        GUARD,
        GUARD.replace(" or _holds_admin_power(user)", ""),
        RPOWER + [RWORDS],
    ),
    "R21_the_reset_refuses_a_verified_admin_too": (
        VIEWS,
        GUARD,
        GUARD.replace(
            "if not user.email_verified_at and (\n"
            "            not user.is_active or _holds_admin_power(user)\n"
            "        ):",
            "if (not user.email_verified_at and not user.is_active) "
            "or _holds_admin_power(user):",
        ),
        [CVRESET],
    ),
    "R22_the_reset_answers_an_admin_in_its_own_words": (
        VIEWS,
        GUARD,
        "        if not user.email_verified_at and not user.is_active:\n"
        '            raise ParseError("Invalid email, OTP code, or new password.")\n'
        "        if not user.email_verified_at and _holds_admin_power(user):\n"
        '            raise ParseError("Account is not verified.")\n',
        [RWORDS],
    ),
}

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
