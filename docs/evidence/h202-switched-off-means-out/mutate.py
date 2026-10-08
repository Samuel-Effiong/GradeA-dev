"""H-202 mutants. Each is one textual change to users/views.py or users/admin.py; the new module and the older OTP and
reset modules run against it; the failing tests are recorded; the file is restored. The runner is H-164's.

Written BEFORE any run: every mutant names the tests it must fail (EXPECTED, full dotted names). KILLED needs a non-zero
inner run with its own "Ran" line, every module loaded and every expected test among the failures; other outcomes are
reported as what they are. EVIDENCE.md says, for each mutant, whether the failing set is exactly the written one.

Rule 17: PYTHONDONTWRITEBYTECODE=1 and __pycache__ deleted before each mutant and after each restore.
Rule 18: each inner run writes straight to a file; nothing is piped.

    python docs/evidence/h202-switched-off-means-out/mutate.py --check
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
    "users.tests_switched_off_means_out",
    "users.tests_reset_for_an_invited_student",
    "users.tests_otp_no_oracle",
    "users.tests_reset_otp_budget",
]
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
HERE = pathlib.Path("docs/evidence/h202-switched-off-means-out")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))
PYCACHE_ROOTS = ["billing", "users", "AutoGrader", "classrooms"]

VIEWS = "users/views.py"
ADMIN = "users/admin.py"

R = "users.tests_switched_off_means_out.SwitchedOffRoadsTests."
A = "users.tests_switched_off_means_out.SwitchingOffRevokesSessionsTests."
VR1 = R + "test_the_verify_code_request_for_a_switched_off_account_sends_nothing"
VR2 = R + "test_that_verify_code_request_answers_like_an_unknown_address"
V1 = R + "test_verify_does_not_switch_a_switched_off_account_back_on"
V2 = R + "test_that_verify_refusal_is_the_wrong_code_refusal"
V3 = R + "test_a_refused_verify_for_a_switched_off_account_spends_the_budget"
RR1 = R + "test_the_reset_code_request_for_a_switched_off_account_makes_no_code"
RR2 = R + "test_that_reset_code_request_answers_like_an_unknown_address"
RS1 = R + "test_a_reset_for_a_switched_off_account_changes_nothing_and_signs_nobody_in"
RS2 = R + "test_that_reset_refusal_is_the_same_as_for_an_address_with_no_account"
RS3 = R + "test_a_refused_reset_for_a_switched_off_account_spends_an_attempt"
REV = "users.tests_reset_for_an_invited_student.InvitedStudentResetTests."
T15 = REV + "test_a_switched_off_account_that_verified_its_email_no_longer_resets"
A1 = A + "test_the_bulk_switch_off_bumps_the_token_epoch_by_one"
A2 = A + "test_tokens_issued_before_the_switch_off_stay_dead_after_a_switch_on"
A3 = A + "test_a_switch_on_alone_does_not_bump_the_epoch"
A5 = A + "test_the_bulk_switch_off_leaves_an_already_inactive_rows_epoch_alone"
E1 = A + "test_an_edit_that_switches_a_user_off_bumps_the_epoch"
E3 = A + "test_an_edit_that_switches_a_user_on_does_not_bump"
E4 = (
    A
    + "test_an_edit_of_an_already_inactive_user_that_leaves_is_active_alone_does_not_bump"
)

VERIFY_GATE = (
    "            if user.email_verified_at and not user.is_active:\n"
    "                return Response(\n"
    '                    {"detail": OTP_SENT_DETAIL}, status=status.HTTP_202_ACCEPTED\n'
    "                )\n"
)
RESET_GATE = (
    "            if not user.is_active:\n"
    "                return Response(\n"
    '                    {"detail": OTP_SENT_DETAIL}, status=status.HTTP_202_ACCEPTED\n'
    "                )\n"
)
VERIFY_REFUSE = (
    "        if user.email_verified_at and not user.is_active:\n"
    '            refuse("Invalid email or token.")\n'
)
RESET_REFUSE = (
    "        if not user.is_active:\n"
    "            if not otp_obj.is_locked():\n"
    "                otp_obj.register_failure()\n"
    '            raise ParseError("Invalid email, OTP code, or new password.")\n'
)
BULK = "token_epoch=Case(\n"
BULK_FULL = (
    "            token_epoch=Case(\n"
    '                When(is_active=True, then=F("token_epoch") + 1),\n'
    '                default=F("token_epoch"),\n'
    "                # Said outright: the two branches would otherwise be an\n"
    "                # IntegerField and a PositiveIntegerField, which Django\n"
    "                # refuses to mix.\n"
    "                output_field=IntegerField(),\n"
    "            ),\n"
)
SAVE_MODEL = (
    '        if change and "is_active" in form.changed_data and not obj.is_active:\n'
    "            obj.revoke_all_sessions()\n"
)

MUTANTS = {
    "S1_a_switched_off_account_is_mailed_a_verify_code": (
        VIEWS,
        VERIFY_GATE,
        "",
        [VR1],
    ),
    "S2_that_verify_code_request_answers_with_a_refusal": (
        VIEWS,
        VERIFY_GATE,
        VERIFY_GATE.replace(
            "return Response(", 'raise ParseError("x"); return Response('
        ),
        [VR2],
    ),
    "S3_verify_switches_a_switched_off_account_back_on": (
        VIEWS,
        VERIFY_REFUSE,
        "",
        [V1, V2, V3],
    ),
    "S4_verify_refuses_a_switched_off_account_in_its_own_words": (
        VIEWS,
        VERIFY_REFUSE,
        VERIFY_REFUSE.replace("Invalid email or token.", "Account is off."),
        [V2],
    ),
    "S5_verify_refuses_without_spending_the_attempt": (
        VIEWS,
        VERIFY_REFUSE,
        VERIFY_REFUSE.replace(
            'refuse("Invalid email or token.")',
            'raise ParseError("Invalid email or token.")',
        ),
        [V3],
    ),
    "S6_a_switched_off_account_is_sent_a_reset_code": (VIEWS, RESET_GATE, "", [RR1]),
    "S7_that_reset_code_request_answers_with_a_refusal": (
        VIEWS,
        RESET_GATE,
        RESET_GATE.replace(
            "return Response(", 'raise ParseError("x"); return Response('
        ),
        [RR2],
    ),
    "S8_the_reset_step_accepts_a_switched_off_account": (
        VIEWS,
        RESET_REFUSE,
        "",
        [RS1, RS2, RS3, T15],
    ),
    "S9_the_reset_refusal_spends_no_attempt": (
        VIEWS,
        RESET_REFUSE,
        RESET_REFUSE.replace(
            "                otp_obj.register_failure()\n", "                pass\n"
        ),
        [RS3],
    ),
    "S10_the_reset_refusal_is_in_its_own_words": (
        VIEWS,
        RESET_REFUSE,
        RESET_REFUSE.replace(
            "Invalid email, OTP code, or new password.", "Account is off."
        ),
        [RS2],
    ),
    "S11_the_bulk_switch_off_bumps_nothing": (
        ADMIN,
        BULK_FULL,
        "",
        [A1, A2, A5],
    ),
    "S12_the_bulk_switch_off_bumps_every_selected_row": (
        ADMIN,
        BULK_FULL,
        BULK_FULL.replace(
            'When(is_active=True, then=F("token_epoch") + 1),\n                default=F("token_epoch"),',
            'When(is_active=True, then=F("token_epoch") + 1),\n                default=F("token_epoch") + 1,',
        ),
        [A5],
    ),
    "S13_the_bulk_switch_off_bumps_by_two": (
        ADMIN,
        BULK_FULL,
        BULK_FULL.replace('then=F("token_epoch") + 1', 'then=F("token_epoch") + 2'),
        [A1, A5],
    ),
    "S14_the_edit_form_does_not_revoke": (ADMIN, SAVE_MODEL, "", [E1]),
    "S15_the_edit_form_revokes_on_a_switch_on_too": (
        ADMIN,
        SAVE_MODEL,
        SAVE_MODEL.replace(" and not obj.is_active", ""),
        [E3],
    ),
    "S16_the_edit_form_revokes_on_any_edit_of_an_inactive_user": (
        ADMIN,
        SAVE_MODEL,
        SAVE_MODEL.replace(' "is_active" in form.changed_data and', ""),
        [E4],
    ),
    "S17_the_bulk_switch_on_bumps_the_epoch": (
        ADMIN,
        "updated = queryset.update(is_active=True)",
        'updated = queryset.update(is_active=True, token_epoch=F("token_epoch") + 1)',
        [A3],
    ),
}

NEW_TESTS = [
    VR1,
    VR2,
    V1,
    V2,
    V3,
    RR1,
    RR2,
    RS1,
    RS2,
    RS3,
    T15,
    A1,
    A2,
    A3,
    A5,
    E1,
    E3,
    E4,
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
