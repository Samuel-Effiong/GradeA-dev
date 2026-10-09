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

# The second module is the delta's (the conversion needs a queued email).
# The first gate, at 8b396aa9, ran the first module alone ("ran 11").
TESTS = [
    "users.tests_old_activation_door_is_closed",
    "classrooms.tests_conversion_needs_a_queued_email",
    # The second delta's (one account never stops the conversion). The
    # delta's gate, at 51cd2773, ran the first two ("ran 22").
    "classrooms.tests_conversion_goes_on_past_one_account",
]
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
HERE = pathlib.Path("docs/evidence/h152-old-activation-door-closed")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))
PYCACHE_ROOTS = ["classrooms", "users", "assignments", "students", "AutoGrader"]

USER_VIEWS = "users/views.py"
COURSE_VIEWS = "classrooms/views.py"
ENROL = "classrooms/services/enrollment.py"
COMMAND = "classrooms/management/commands/backfill_pending_student_invites.py"
NOTIFY = "classrooms/services/notifications.py"

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

# ---- the delta: the conversion needs a queued email
Q = "classrooms.tests_conversion_needs_a_queued_email."
Q_SENDER = Q + "TheSenderSaysWhetherTheEmailWasQueuedTest."
S_TRUE = Q_SENDER + "test_true_when_it_was_queued"
S_FALSE = Q_SENDER + "test_false_and_no_error_when_the_queue_could_not_be_reached"
Q_CMD = Q + "TheConversionNeedsAQueuedEmailTest."
LEFT = Q_CMD + "test_the_account_is_left_exactly_as_it_was"
GOES_ON = Q_CMD + "test_the_run_goes_on_and_converts_the_others"
SUMMARY = Q_CMD + "test_the_summary_counts_them"
SECOND = Q_CMD + "test_a_second_run_picks_up_exactly_those"
NOBODY = Q_CMD + "test_the_output_names_nobody_when_an_email_could_not_be_queued"
NONE_LEFT = Q_CMD + "test_a_run_with_the_queue_up_says_none_was_left"
PREVIEW = Q_CMD + "test_the_preview_queues_nothing_and_does_not_guess"
Q_ADD = Q + "AnOrdinaryAddByEmailIsUnchangedTest."
ADD_NEW = Q_ADD + "test_a_new_student_is_still_added_when_the_queue_is_down"
ADD_NEVER = (
    Q_ADD + "test_a_student_who_never_signed_in_is_still_added_when_the_queue_is_down"
)
DELTA_TESTS = [
    S_TRUE,
    S_FALSE,
    LEFT,
    GOES_ON,
    SUMMARY,
    SECOND,
    NOBODY,
    NONE_LEFT,
    PREVIEW,
    ADD_NEW,
    ADD_NEVER,
]

# ---- the second delta: one account never stops the conversion
G = "classrooms.tests_conversion_goes_on_past_one_account."
G_SENDER = G + "TheSenderDoesNotNeedATeacherTest."
NO_TEACHER = G_SENDER + "test_with_no_teacher_the_email_names_nobody"
WITH_TEACHER = G_SENDER + "test_with_a_teacher_the_wording_is_as_it_was"
ORPHAN = (
    G + "AStudentOfACourseWithNoTeacherIsConvertedTest."
    "test_the_conversion_converts_them_and_goes_on"
)
G_ERR = G + "OneAccountNeverStopsTheRunTest."
E_LEFT = G_ERR + "test_the_account_is_left_as_it_was_and_listed_by_the_errors_type"
E_GOES_ON = G_ERR + "test_the_run_goes_on_to_the_others"
E_SUMMARY = G_ERR + "test_the_summary_counts_them_apart_from_the_unqueued"
E_TYPE = G_ERR + "test_the_line_carries_the_errors_type_and_never_its_text"
E_SECOND = G_ERR + "test_a_second_run_takes_them_up_again"
E_INTERRUPT = G_ERR + "test_an_interrupt_from_the_keyboard_still_stops_the_run"
E_DATABASE = (
    G + "ADatabaseErrorStopsTheRunTest.test_the_run_stops_and_says_which_account"
)
DELTA2_TESTS = [
    NO_TEACHER,
    WITH_TEACHER,
    ORPHAN,
    E_LEFT,
    E_GOES_ON,
    E_SUMMARY,
    E_TYPE,
    E_SECOND,
    E_INTERRUPT,
    E_DATABASE,
]

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
        '                not (student.first_name or "").strip()\n',
        '                True\n                or not (student.first_name or "").strip()\n',
        [C_DRY, C_REAL],
    ),
    "C3_the_command_prints_an_address": (
        COMMAND,
        '                f"{prefix}convert: student {student.pk} (pending course "\n',
        '                f"{prefix}convert: student {student.email} (pending course "\n',
        [C_IDS],
    ),
    # ---- the delta. Each of the eleven delta tests is named by at least
    # one of these (asserted in check()).
    "Q01_the_sender_always_says_queued": (
        NOTIFY,
        "    return queued is not None\n",
        "    return True\n",
        [S_FALSE, LEFT, SUMMARY, SECOND, NOBODY],
    ),
    "Q02_the_sender_never_says_queued": (
        NOTIFY,
        "    return queued is not None\n",
        "    return False\n",
        [S_TRUE, GOES_ON, SUMMARY, SECOND, NONE_LEFT],
    ),
    "Q03_the_command_ignores_the_answer": (
        COMMAND,
        "        if not queued:\n            raise EmailNotQueued\n",
        "        if False:\n            raise EmailNotQueued\n",
        [LEFT, SUMMARY, SECOND, NOBODY],
    ),
    # Until the second delta this mutant stopped the whole run and failed
    # five tests (the delta's gate at 51cd2773 shows it). Since then an
    # error that is not caught as "not queued" is caught as a plain error:
    # the run goes on and a second run still takes the account up, so
    # GOES_ON and SECOND pass under it; what it breaks is the kind of line
    # and of count. GOES_ON is still named by Q02.
    "Q04_an_unqueued_email_is_reported_as_a_plain_error": (
        COMMAND,
        "                except EmailNotQueued:\n",
        "                except KeyError:\n",
        [LEFT, SUMMARY, NOBODY],
    ),
    "Q05_the_conversion_is_not_undone": (
        COMMAND,
        "    @staticmethod\n    @transaction.atomic\n    def _convert(",
        "    @staticmethod\n    def _convert(",
        [LEFT, SUMMARY, SECOND, E_LEFT, E_SUMMARY, E_SECOND, E_INTERRUPT],
    ),
    "Q06_the_summary_does_not_count_them": (
        COMMAND,
        "                    not_queued += 1\n",
        "                    not_queued += 0\n",
        [SUMMARY],
    ),
    "Q07_the_line_shows_the_address": (
        COMMAND,
        '                        "NOT converted (email could not be queued): "\n'
        '                        f"student {student.pk}"\n',
        '                        "NOT converted (email could not be queued): "\n'
        '                        f"student {student.email}"\n',
        [LEFT, NOBODY],
    ),
    "Q08_the_preview_converts": (
        COMMAND,
        "            if not dry_run:\n                try:\n",
        "            if True:\n                try:\n",
        [PREVIEW],
    ),
    "Q09_an_unconverted_account_is_listed_as_converted": (
        COMMAND,
        "                    not_queued += 1\n                    continue\n",
        "                    not_queued += 1\n                    pass\n",
        [LEFT, SUMMARY],
    ),
    "Q10_a_new_students_add_is_undone_when_the_queue_is_down": (
        ENROL,
        "            notifications.send_student_login_invitation_email(\n"
        "                student, course, generated_password\n"
        "            )\n"
        "            return student, True\n",
        "            if not notifications.send_student_login_invitation_email(\n"
        "                student, course, generated_password\n"
        "            ):\n"
        '                raise EnrollmentError("not queued")\n'
        "            return student, True\n",
        [ADD_NEW],
    ),
    "Q11_a_reinvited_students_add_is_undone_when_the_queue_is_down": (
        ENROL,
        "        notifications.send_student_login_invitation_email(\n"
        "            student, course, generated_password\n"
        "        )\n"
        "        return student, True\n",
        "        if not notifications.send_student_login_invitation_email(\n"
        "            student, course, generated_password\n"
        "        ):\n"
        '            raise EnrollmentError("not queued")\n'
        "        return student, True\n",
        [ADD_NEVER],
    ),
    # ---- the second delta. Each of its nine tests is named by at least
    # one mutant (asserted in check()).
    "R01_the_email_reads_the_teacher_whether_there_is_one_or_not": (
        NOTIFY,
        '    inviter = course.teacher.get_full_name() if course.teacher_id else ""\n',
        "    inviter = course.teacher.get_full_name()\n",
        [NO_TEACHER, ORPHAN],
    ),
    "R02_the_sentence_without_a_teacher_is_another": (
        NOTIFY,
        '        invitation = f"You have been invited to join {course.name} on Grade A+."\n',
        '        invitation = f"Someone has invited you to join {course.name} on Grade A+."\n',
        [NO_TEACHER],
    ),
    "R03_the_teachers_name_is_never_used": (
        NOTIFY,
        "    if inviter:\n",
        "    if False:\n",
        [WITH_TEACHER],
    ),
    "R04_an_error_in_one_account_stops_the_run_again": (
        COMMAND,
        "                except Exception as exc:\n",
        "                except KeyError as exc:\n",
        [E_LEFT, E_GOES_ON, E_SUMMARY, E_TYPE, E_SECOND],
    ),
    "R05_the_line_carries_the_errors_text": (
        COMMAND,
        '                        f"NOT converted ({type(exc).__name__}): "\n',
        '                        f"NOT converted ({exc}): "\n',
        [E_LEFT, E_TYPE],
    ),
    "R06_the_errors_are_not_counted": (
        COMMAND,
        "                    errors += 1\n",
        "                    errors += 0\n",
        [E_SUMMARY],
    ),
    "R07_an_account_that_failed_is_listed_as_converted": (
        COMMAND,
        "                    errors += 1\n                    continue\n",
        "                    errors += 1\n                    pass\n",
        [E_LEFT, E_SUMMARY],
    ),
    "R08_an_interrupt_is_swallowed_as_that_accounts_error": (
        COMMAND,
        "                except Exception as exc:\n",
        "                except BaseException as exc:\n",
        [E_INTERRUPT],
    ),
    "R09_a_database_error_is_taken_for_that_accounts_and_the_run_goes_on": (
        COMMAND,
        "                except AnyDatabaseError as exc:\n",
        "                except KeyError as exc:\n",
        [E_DATABASE],
    ),
    "R10_the_stop_line_shows_the_address": (
        COMMAND,
        '                        f"at student {student.pk}. That account is not "\n',
        '                        f"at student {student.email}. That account is not "\n',
        [E_DATABASE],
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
    named = {test for (_, _, _, expected) in MUTANTS.values() for test in expected}
    unnamed = [test for test in DELTA_TESTS + DELTA2_TESTS if test not in named]
    assert not unnamed, f"delta tests no mutant names: {unnamed}"
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
