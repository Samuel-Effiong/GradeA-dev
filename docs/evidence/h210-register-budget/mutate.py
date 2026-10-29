# flake8: noqa: E501,B950
"""H-210 the school-admin door's per-address budget: deliberate breaks, and the tests that must catch each.

Each mutant is applied, its test modules run, the failing tests are recorded,
the file is restored. Judged (rule 18): SURVIVED = the inner run exits 0;
KILLED = non-zero exit, the run's own "Ran" line, no module that failed to
load, EVERY test named in EXPECTED among the failing tests, and (rule 22)
every FAIL/ERROR block of each expected test holding the fragment written for
it in EXPECTED. Any other failing test is recorded as
also_failed_not_expected, never hidden; BROKEN = anything else. EXPECTED was
written before any run, traced against every assertion of the three modules
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

HERE = pathlib.Path("docs/evidence/h210-register-budget")
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))

VW = "users/views.py"
TESTS = [
    "users.tests_h210_register_budget",
    "users.tests_auth_audit_doors",
    "users.tests_register_throttle_school_admin",
]
K = "keepdb"

# The door's lines, at their 16- and 12-space indentation (verify's own copies
# sit at 8, so each anchor is found once; --check proves it).
READ = (
    "                lock_until = verify_lock_until(email)\n"
    "                attempt = None if lock_until else reserve_verify_attempt(email)\n"
)
GATE = "                if lock_until or verify_attempt_over_budget(attempt):\n"
LOCKED_AUDIT = (
    '                    audit_failure = (account_for_email(email), "VERIFY_LOCKED")\n'
)
SPEND_LOCK = "                        lock_verify_address(email)\n"
FLAG = '                        return {"extra_metadata": {"lock_triggered": True}}\n'
REFUND = "            clear_verify_failures(email)\n"
CATCH = "        except (ParseError, ValidationError, Throttled):\n"

# id, what is broken, file, text(s) replaced, new text(s), tests, database kind
MUTANTS = [
    (
        "N1",
        "the lock never read (attempts still counted)",
        VW,
        READ,
        "                lock_until = None\n"
        "                attempt = reserve_verify_attempt(email)\n",
        TESTS,
        K,
    ),
    (
        "N2",
        "no attempt reserved (only the lock read)",
        VW,
        READ,
        "                lock_until = verify_lock_until(email)\n"
        "                attempt = None\n",
        TESTS,
        K,
    ),
    (
        "N3",
        "its own bucket, not verify's",
        VW,
        [READ, SPEND_LOCK],
        [
            '                lock_until = verify_lock_until("h210:" + email)\n'
            '                attempt = None if lock_until else reserve_verify_attempt("h210:" + email)\n',
            '                        lock_verify_address("h210:" + email)\n',
        ],
        TESTS,
        K,
    ),
    (
        "N4",
        "the budget spent but the address never locked",
        VW,
        SPEND_LOCK,
        "                        pass\n",
        TESTS,
        K,
    ),
    (
        "N5",
        "no event for the locked attempt",
        VW,
        LOCKED_AUDIT,
        "",
        TESTS,
        K,
    ),
    (
        "N6",
        "no lock_triggered on the spending guess",
        VW,
        FLAG,
        "                        return {}\n",
        TESTS,
        K,
    ),
    (
        "N7",
        "a success does not refund the attempts",
        VW,
        REFUND,
        "",
        TESTS,
        K,
    ),
    (
        "N8",
        "Throttled not caught (the 429 becomes a 500)",
        VW,
        CATCH,
        "        except (ParseError, ValidationError):\n",
        TESTS,
        K,
    ),
    (
        "N9",
        "only addresses with an account are locked",
        VW,
        GATE,
        "                if (lock_until or verify_attempt_over_budget(attempt)) and account_for_email(email) is not None:\n",
        TESTS,
        K,
    ),
]

# tests_h210_register_budget
_B1 = "test_after_five_wrong_tokens_even_the_right_one_is_refused"
_B2 = "test_an_address_with_no_account_is_locked_the_same_way"
_B3 = "test_the_budget_is_shared_with_email_verification"
_B4 = "test_the_guess_that_spends_the_budget_is_flagged"
_B5 = "test_a_locked_attempt_is_recorded_as_refused"
_B6 = "test_a_locked_address_is_sent_no_new_invitation"
_B7 = "test_the_locked_answer_does_not_tell_whether_the_address_has_an_account"
_B8 = "test_a_lock_alone_refuses_after_the_attempt_window"
_K2 = "test_a_success_refunds_the_attempts"

S200 = "200 != 429"
S400 = "400 != 429"
S500 = "500 != 429"
NONE_TRUE = "None != True"
DENIED = "'FAILURE' != 'DENIED'"
RESENT = "!= 'invite-h210-token'"

#: Written before any run: {mutant: {test: the fragment its every block holds}},
#: every test reckoned to fail under it, traced against every assertion of the
#: three modules. No test of tests_auth_audit_doors or
#: tests_register_throttle_school_admin makes enough attempts on one address
#: to reach the budget.
EXPECTED = {
    # The attempt count still refuses a 6th try within its window, so only the
    # lock-alone test sees the lock go unread.
    "N1": {_B8: S200},
    # No count: this door never locks or refuses; a lock set elsewhere (B3,
    # B8) is still read.
    "N2": {
        _B1: S200,
        _B2: S400,
        _B4: NONE_TRUE,
        _B5: DENIED,
        _B6: RESENT,
        _B7: S200,
    },
    # Verify's lock is not seen (B3, B8), and this door's lock is not the one
    # /auth/otp reads (B6).
    "N3": {_B3: S200, _B6: RESENT, _B8: S200},
    # The 6th try is still refused by the count; only the shared lock's other
    # reader (/auth/otp) shows it was never set.
    "N4": {_B6: RESENT},
    # A 429 leaves no fallback event (the middleware skips 429s).
    "N5": {_B5: "0 != 1"},
    "N6": {_B4: NONE_TRUE},
    # 3 wrong + 1 right = 4 attempts left counted.
    "N7": {_K2: "4 is not None"},
    # The 500 path leaves the middleware's SERVER_ERROR FAILURE (B5).
    "N8": {
        _B1: S500,
        _B2: S500,
        _B3: S500,
        _B5: DENIED,
        _B7: S500,
        _B8: S500,
    },
    "N9": {_B2: S400, _B7: S400},
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
