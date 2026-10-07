"""H-152 mutants. Each is one textual change to one production file; the
test module runs against it; the failing tests are recorded; the file is
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

    python docs/evidence/h152-old-activation-door-closed/mutate.py --check
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

TESTS = ["users.tests_old_activation_door_is_closed"]
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
HERE = pathlib.Path("docs/evidence/h152-old-activation-door-closed")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))
PYCACHE_ROOTS = ["classrooms", "users", "assignments", "students", "AutoGrader"]

USER_VIEWS = "users/views.py"
COURSE_VIEWS = "classrooms/views.py"
ENROL = "classrooms/services/enrollment.py"
COMMAND = "classrooms/management/commands/backfill_pending_student_invites.py"

T = "users.tests_old_activation_door_is_closed."
DOOR = T + "TheDoorTest."
VALID = DOOR + "test_a_valid_code_no_longer_opens_it"
SAME = DOOR + "test_every_knock_gets_the_same_answer_byte_for_byte"
EXPIRED = DOOR + "test_an_expired_code_is_not_offered_a_renewal"
BUDGET = DOOR + "test_wrong_codes_spend_no_budget_and_never_pause_the_door"
NAMES = DOOR + "test_the_answer_names_no_course_and_no_classmate"
TEACHER = (
    T + "ATeachersCodeOpensNothingHereTest."
    "test_a_pending_teachers_code_completes_nothing"
)
RENEW = T + "TheRenewalTest."
R_NOT = RENEW + "test_an_expired_code_is_not_renewed_and_nobody_is_mailed"
R_SAME = RENEW + "test_every_request_gets_the_same_answer_byte_for_byte"
CMD = T + "TheConversionCommandCountsTheNamelessTest."
C_DRY = CMD + "test_the_dry_run_counts_the_accounts_with_no_name"
C_REAL = CMD + "test_the_real_run_reports_the_same_count"
C_IDS = CMD + "test_the_output_still_carries_ids_only"

DOOR_ANSWER = (
    "        return Response(\n"
    '            {"detail": OLD_INVITATION_CLOSED_MESSAGE},\n'
    "            status=status.HTTP_410_GONE,\n"
    "        )\n"
)
RENEW_ANSWER = (
    "        return Response(\n"
    '            {"detail": services.OLD_INVITATION_CLOSED_MESSAGE},\n'
    "            status=status.HTTP_410_GONE,\n"
    "        )\n"
)

MUTANTS = {
    "D1_the_door_answers_200": (
        USER_VIEWS,
        DOOR_ANSWER,
        DOOR_ANSWER.replace("HTTP_410_GONE", "HTTP_200_OK"),
        [VALID, SAME, EXPIRED, BUDGET, NAMES, TEACHER],
    ),
    "D2_the_renewal_answers_200": (
        COURSE_VIEWS,
        RENEW_ANSWER,
        RENEW_ANSWER.replace("HTTP_410_GONE", "HTTP_200_OK"),
        [R_NOT, R_SAME],
    ),
    "D3_the_sentence_is_another": (
        ENROL,
        '    "Ask your teacher to add you to the class again."\n',
        '    "Ask for a new code."\n',
        [VALID, R_NOT],
    ),
    "D4_the_door_gives_the_code_back": (
        USER_VIEWS,
        DOOR_ANSWER,
        DOOR_ANSWER.replace(
            '{"detail": OLD_INVITATION_CLOSED_MESSAGE},',
            '{"detail": OLD_INVITATION_CLOSED_MESSAGE, "token": request.data.get("token")},',
        ),
        [SAME, EXPIRED],
    ),
    "D5_the_renewal_gives_the_code_back": (
        COURSE_VIEWS,
        RENEW_ANSWER,
        RENEW_ANSWER.replace(
            '{"detail": services.OLD_INVITATION_CLOSED_MESSAGE},',
            '{"detail": services.OLD_INVITATION_CLOSED_MESSAGE, "token": request.data.get("token")},',
        ),
        [R_SAME],
    ),
    "D6_a_knock_spends_the_budget_for_wrong_codes": (
        USER_VIEWS,
        DOOR_ANSWER,
        "        from users.throttling import record_register_student_failure\n"
        "\n"
        '        record_register_student_failure("closed")\n' + DOOR_ANSWER,
        [BUDGET],
    ),
    "C1_the_command_does_not_count_the_nameless": (
        COMMAND,
        '                f"{converted_without_a_name} of the converted have no name "\n',
        '                f"(count of nameless not shown) "\n',
        [C_DRY, C_REAL],
    ),
    "C2_the_command_counts_every_converted_account_as_nameless": (
        COMMAND,
        '            if not (student.first_name or "").strip() and not (\n',
        '            if True or not (student.first_name or "").strip() and not (\n',
        [C_DRY, C_REAL],
    ),
    "C3_the_command_prints_an_address": (
        COMMAND,
        '                f"{prefix}convert: student {student.pk} (pending course "\n',
        '                f"{prefix}convert: student {student.email} (pending course "\n',
        [C_IDS],
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
