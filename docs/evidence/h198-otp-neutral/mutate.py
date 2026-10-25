# flake8: noqa: E501,B950
"""H-198 neutral /auth/otp answers: deliberate breaks, and the tests that must catch each.

Each mutant is applied, its test modules run, the failing tests are recorded,
the file is restored. Judged (rule 18): SURVIVED = the inner run exits 0;
KILLED = non-zero exit, the run's own "Ran" line, no module that failed to
load, EVERY test named in EXPECTED among the failing tests, and (rule 22)
every FAIL/ERROR block of each expected test holding the fragment written for
it in EXPECTED. Any other failing test is recorded as
also_failed_not_expected, never hidden; BROKEN = anything else. EXPECTED was
written before any run, traced against every assertion of the five modules
(controls too).

Rule 17: PYTHONDONTWRITEBYTECODE=1 on every inner run; the mutated file's
__pycache__ is deleted before each mutant and after each restore.
Rule 18: each inner run writes straight to its own file, stdin from the null
device, nothing piped. Each mutant is restored in a `finally` block and
SIGTERM is turned into an ordinary exit; the gate script's EXIT trap restores
the files from the commit.
"""

import ast
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys

HERE = pathlib.Path("docs/evidence/h198-otp-neutral")
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))

VW = "users/views.py"
TESTS = [
    "users.tests_h198_otp_neutral",
    "users.tests_reset_for_an_invited_student",
    "users.tests_switched_off_means_out",
    "users.tests_otp_no_oracle",
    "users.tests_auth_endpoints",
]
K = "keepdb"

NEUTRAL = (
    "                return Response(\n"
    '                    {"detail": OTP_SENT_DETAIL}, status=status.HTTP_202_ACCEPTED\n'
    "                )\n"
)
# C: VERIFY_EMAIL on a verified, active account (the If line makes it unique).
C_IF = "            if user.email_verified_at and user.is_active:\n"
C = C_IF + NEUTRAL
# G: RESET_PASSWORD on a never-verified account, switched off or with admin power.
G_SEND = (
    "                if not _holds_admin_power(user) and not verify_lock_until(user.email):\n"
    "                    send_user_activation_email(user)\n"
)
G = G_SEND + NEUTRAL

# id, what is broken, file, text replaced, new text, tests, database kind
MUTANTS = [
    (
        "M1",
        "C's 400 put back",
        VW,
        C,
        C_IF
        + '                raise ParseError("Email already verified. Please login.")\n',
        TESTS,
        K,
    ),
    (
        "M2",
        "G's 400 put back",
        VW,
        G,
        '                raise ParseError("Email not verified.")\n',
        TESTS,
        K,
    ),
    (
        "M3",
        "C answers 202 with other bytes",
        VW,
        C,
        C_IF + NEUTRAL.replace("OTP_SENT_DETAIL", '"Sent."'),
        TESTS,
        K,
    ),
    (
        "M4",
        "G issues a reset code",
        VW,
        G_SEND,
        G_SEND
        + "                PasswordResetOTP.objects.get_or_create(user=user)[0].generate_code()\n",
        TESTS,
        K,
    ),
    (
        "M5",
        "C re-sends the activation email",
        VW,
        C,
        C_IF + "                send_user_activation_email(user)\n" + NEUTRAL,
        TESTS,
        K,
    ),
    (
        "M6",
        "G with admin power is sent the activation email too",
        VW,
        G_SEND,
        "                if not verify_lock_until(user.email):\n"
        "                    send_user_activation_email(user)\n",
        TESTS,
        K,
    ),
    (
        "M7",
        "G ignores H-53's lock",
        VW,
        G_SEND,
        "                if not _holds_admin_power(user):\n"
        "                    send_user_activation_email(user)\n",
        TESTS,
        K,
    ),
    (
        "M8",
        "G without admin power is sent nothing",
        VW,
        G_SEND,
        "",
        TESTS,
        K,
    ),
]

# tests_h198_otp_neutral
_R1 = "test_a_verified_active_account_asking_for_verification_is_told_nothing"
_R2 = "test_a_never_verified_switched_off_account_asking_for_a_reset_is_told_nothing"
_R3 = "test_that_account_is_sent_the_activation_email_and_no_reset_code"
_R4 = "test_while_the_address_is_locked_that_account_is_sent_nothing"
_R5 = "test_a_never_verified_account_with_admin_power_is_told_and_sent_nothing"
_K1 = "test_c_sends_and_issues_nothing"
# tests_reset_for_an_invited_student (changed by this row)
_INACTIVE = "test_an_inactive_never_verified_row_is_still_refused"
_STAFF = "test_the_request_refuses_a_never_verified_staff_account"
_FLAG = "test_the_request_refuses_a_never_verified_superuser_flag"
_SA_TYPE = "test_the_request_refuses_a_never_verified_super_admin_type"
_CMDLINE = "test_the_request_refuses_a_command_line_superuser"
_V_ADMIN = "test_a_verified_admin_asking_for_verification_gets_the_neutral_answer"
# tests_switched_off_means_out (changed by this row)
_ACTIVE = "test_an_active_verified_account_gets_the_neutral_answer"
# tests_auth_endpoints (changed by this row after the red gate on 039c6135)
_EP_C = "test_verify_email_on_an_already_verified_account_gets_the_neutral_answer"
_EP_G = "test_reset_password_on_an_unverified_account_gets_the_neutral_answer"

S400 = "400 != 202"
NOT_SENT = "Called 0 times"
SENT = "to not have been called"
CODE_MADE = "True is not false"
BYTES = "' != b'"

#: Written before any run: {mutant: {test: the fragment its every block holds}},
#: every test reckoned to fail under it, traced against every assertion of the
#: five modules. tests_otp_no_oracle covers only states that already answered
#: 202 (unknown, F, I, a locked reset), so no mutant reaches it.
EXPECTED = {
    "M1": {_R1: S400, _V_ADMIN: S400, _ACTIVE: S400, _EP_C: S400},
    # H-164's "same words" test compares two G answers: both 400 again, equal.
    "M2": {
        _R2: S400,
        _R3: NOT_SENT,
        _R4: S400,
        _R5: S400,
        _INACTIVE: S400,
        _STAFF: S400,
        _FLAG: S400,
        _SA_TYPE: S400,
        _CMDLINE: S400,
        _EP_G: S400,
    },
    # The status checks still pass; R1 and _EP_C compare C's bytes.
    "M3": {_R1: BYTES, _EP_C: BYTES},
    "M4": {
        _R3: CODE_MADE,
        _R4: CODE_MADE,
        _R5: CODE_MADE,
        _INACTIVE: CODE_MADE,
        _STAFF: CODE_MADE,
        _FLAG: CODE_MADE,
        _SA_TYPE: CODE_MADE,
        _CMDLINE: CODE_MADE,
        _EP_G: CODE_MADE,
    },
    # The active account's token is set ("'NNNNNN' is not None").
    "M5": {_K1: SENT, _ACTIVE: "is not None", _EP_C: "is not None"},
    # In the invited-student module the activation send is replaced by a
    # mock, so only R5 sees it.
    "M6": {_R5: SENT},
    "M7": {_R4: SENT},
    "M8": {_R3: NOT_SENT, _EP_G: NOT_SENT},
}

#: A test module that could not be loaded.
LOAD_FAILURE = "unittest.loader._FailedTest"


def clear_pycache(path):
    shutil.rmtree(pathlib.Path(path).parent / "__pycache__", ignore_errors=True)


def blocks_of(text, name):
    blocks = re.split(r"^={70}$", text, flags=re.M)
    return [
        b
        for b in blocks
        if re.match(r"\s*(FAIL|ERROR): " + re.escape(name) + r"[ (]", b)
    ]


def wrong_reasons(mid, text):
    """Expected tests with a block that does not hold its written fragment."""
    return sorted(
        name
        for name, fragment in EXPECTED[mid].items()
        if not all(fragment in b for b in blocks_of(text, name))
    )


def judge(mid, returncode, text):
    """(status, the run's "Ran" line, the failing tests' names, wrong reasons)."""
    failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", text, re.M)))
    ran = re.findall(r"^Ran \d+ tests? in .*$", text, re.M)
    ran_line = ran[-1] if ran else None
    wrong = wrong_reasons(mid, text)
    if returncode == 0:
        status = "SURVIVED"
    elif (
        ran_line
        and LOAD_FAILURE not in text
        and set(EXPECTED[mid]) <= set(failed)
        and not wrong
    ):
        status = "KILLED"
    else:
        status = "BROKEN"
    return status, ran_line, failed, wrong


def edits(old, new):
    """The (old, new) pairs of one mutant: one pair, or several."""
    if isinstance(old, str):
        return [(old, new)]
    assert len(old) == len(new)
    return list(zip(old, new, strict=True))


def broken(source, old, new):
    for one_old, one_new in edits(old, new):
        source = source.replace(one_old, one_new, 1)
    return source


def _exit_on_sigterm(signum, _frame):
    raise SystemExit(128 + signum)


def main():
    signal.signal(signal.SIGTERM, _exit_on_sigterm)
    assert [m[0] for m in MUTANTS] == list(EXPECTED), "EXPECTED names every mutant"
    originals = {}
    for mid, _what, path, old, new, _tests, kind in MUTANTS:
        source = originals.setdefault(path, open(path).read())
        for one_old, _one_new in edits(old, new):
            found = source.count(one_old)
            assert found == 1, f"{mid}: anchor found {found} times"
        assert kind in ("keepdb", "fresh"), mid
        assert broken(source, old, new) != source, f"{mid}: changes nothing"
        ast.parse(broken(source, old, new))
    if "--check" in sys.argv:
        print(len(MUTANTS), "mutants: every anchor found once, all parse")
        print(" ".join(m[0] for m in MUTANTS))
        return
    LOGS.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    results = {}
    for mid, what, path, old, new, tests, kind in MUTANTS:
        original = originals[path]
        database = ["--keepdb"] if kind == "keepdb" else []
        try:
            clear_pycache(path)
            open(path, "w").write(broken(original, old, new))
            log = LOGS / f"{mid}.txt"
            with open(log, "w") as out, open(os.devnull) as devnull:
                p = subprocess.run(
                    [sys.executable, "manage.py", "test", *tests]
                    + [f"--settings={SETTINGS}", "--noinput", "--verbosity", "2"]
                    + database,
                    stdin=devnull,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    env=env,
                    timeout=600,
                )
        finally:
            open(path, "w").write(original)
            clear_pycache(path)
        text = log.read_text(errors="replace")
        status, ran_line, failed, wrong = judge(mid, p.returncode, text)
        results[mid] = {
            "what": what,
            "file": path,
            "kind": kind,
            "status": status,
            "exit": p.returncode,
            "ran_line": ran_line,
            "expected": EXPECTED[mid],
            "failing_tests": failed,
            "wrong_reason": wrong,
            "also_failed_not_expected": sorted(set(failed) - set(EXPECTED[mid])),
        }
        print(mid, results[mid], flush=True)
    with open(OUT, "w") as f:
        json.dump(results, f, indent=2)
        f.write("\n")
    for status in ("KILLED", "SURVIVED", "BROKEN"):
        print(f"{status}:", [k for k, v in results.items() if v["status"] == status])


if __name__ == "__main__":
    main()
