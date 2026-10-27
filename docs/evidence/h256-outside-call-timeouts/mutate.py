# flake8: noqa: E501,B950
"""H-256 timeouts on outside calls (Hardening Engineer, 4d): deliberate breaks, and the tests that must catch each.

Each mutant: the file is edited (anchor found exactly once), the mutated
module's __pycache__ removed (rule 17), the three H-256 modules run with
--keepdb on the mutant database, output straight to its own log with stdin
from the null device (rule 18), the file restored from the saved source and
its sha256 checked.

Judged (rules 18, 19, 22; H-229): KILLED = non-zero exit, the run's own "Ran"
line, no module that failed to load, the failing set EXACTLY the written one,
and every FAIL/ERROR block of each expected test holding that test's written
fragment. Anything else is reported as what it is (SURVIVED; DIFFERS with the
extra or missing tests named; WRONG REASON; BROKEN) and the battery ends
non-zero. EXPECTED and REASON were written before any run, each traced against
every assertion of the three modules (controls too).

`python -I mutate.py --check` only counts the anchors and parses each mutant.
"""

import ast
import hashlib
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys

SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
HERE = pathlib.Path("docs/evidence/h256-outside-call-timeouts")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))

VIEWS = "users/views.py"
SERVICES = "ai_processor/services.py"
SETTINGS_PY = "AutoGrader/settings.py"
IMPORTS = "billing/imports.py"
REFUNDS = "billing/refunds.py"

TESTS = [
    "users.tests_h256_google_exchange_timeout",
    "ai_processor.tests_h256_provider_timeout",
    "billing.tests.test_h256_stripe_network_retries",
]

G_POST = "                    token_url, data=payload, timeout=GOOGLE_TOKEN_EXCHANGE_TIMEOUT\n"
G_CONST = "GOOGLE_TOKEN_EXCHANGE_TIMEOUT = (5, 10)\n"
G_LOG = '                    "Google OAuth token exchange failed: %s", describe_error_for_log(e)\n'
AI_TIMEOUT = "            timeout=httpx.Timeout(settings.AI_PROVIDER_TIMEOUT_SECONDS, connect=10.0),\n"
AI_RETRIES = "            max_retries=2,\n"
SETTING = 'AI_PROVIDER_TIMEOUT_SECONDS = env.float("AI_PROVIDER_TIMEOUT_SECONDS", default=300.0)\n'
STRIPE = "stripe.max_network_retries = 2\n"
REFUND = "    except BaseException:\n        _refund_all(ids, reason=reason)\n        raise\n"
REFUND_SKIPS_TIMEOUTS = (
    "    except BaseException as exc:\n"
    "        link, timed_out, depth = exc, False, 0\n"
    "        while link is not None and depth < 10:\n"
    '            timed_out = timed_out or "Timeout" in type(link).__name__\n'
    "            link, depth = link.__cause__ or link.__context__, depth + 1\n"
    "        if not timed_out:\n"
    "            _refund_all(ids, reason=reason)\n"
    "        raise\n"
)

# id: (what is broken, file, old, new)
MUTANTS = {
    "M1": (
        "the Google exchange's timeout removed",
        VIEWS,
        G_POST,
        "                    token_url, data=payload\n",
    ),
    "M2": (
        "the Google exchange's timeout None",
        VIEWS,
        G_CONST,
        "GOOGLE_TOKEN_EXCHANGE_TIMEOUT = None\n",
    ),
    "M3": (
        "the failed exchange logged with its traceback again",
        VIEWS,
        G_LOG,
        '                    "Google OAuth token exchange failed", exc_info=e\n',
    ),
    "M4": (
        "the failed exchange logged by its text",
        VIEWS,
        G_LOG,
        '                    "Google OAuth token exchange failed: %s", e\n',
    ),
    "M5": (
        "the AI client's timeout removed (the SDK's 600 s)",
        SERVICES,
        AI_TIMEOUT,
        "",
    ),
    "M6": (
        "the AI client's timeout None (no limit)",
        SERVICES,
        AI_TIMEOUT,
        "            timeout=None,\n",
    ),
    "M7": (
        "the AI client's timeout 0",
        SERVICES,
        AI_TIMEOUT,
        "            timeout=0,\n",
    ),
    "M8": (
        "the AI client's 10 s connect wait dropped",
        SERVICES,
        AI_TIMEOUT,
        "            timeout=httpx.Timeout(settings.AI_PROVIDER_TIMEOUT_SECONDS),\n",
    ),
    "M9": (
        "the setting ignored (a fixed 300 s)",
        SERVICES,
        AI_TIMEOUT,
        "            timeout=httpx.Timeout(300.0, connect=10.0),\n",
    ),
    "M10": (
        "the setting's default 30 s",
        SETTINGS_PY,
        SETTING,
        SETTING.replace("default=300.0", "default=30.0"),
    ),
    "M11": (
        "the AI client's retries removed (max_retries=0)",
        SERVICES,
        AI_RETRIES,
        "            max_retries=0,\n",
    ),
    "M12": (
        "the refund skipped when the run timed out",
        REFUNDS,
        REFUND,
        REFUND_SKIPS_TIMEOUTS,
    ),
    "M13": ("Stripe's retries 0", IMPORTS, STRIPE, "stripe.max_network_retries = 0\n"),
    "M14": ("Stripe's retries left to the SDK (the line removed)", IMPORTS, STRIPE, ""),
}

G1 = "test_the_exchange_waits_5_s_to_connect_and_10_s_for_the_answer"
G3 = "test_a_timeout_is_logged_by_its_class_only"
P1 = "test_every_try_carries_the_timeout"
P2 = "test_the_read_timeout_follows_the_setting"
P3 = "test_a_provider_that_never_answers_is_tried_three_times"
R1 = "test_a_run_that_times_out_refunds_what_it_charged"
S1 = "test_the_retries_are_ours_not_the_sdks_default"

SDK_DEFAULT = "{'connect': 5.0, 'read': 600"

#: Written before any run: test -> the fragment each of its blocks must hold.
#: M3: both subtests' records carry exc_info. M4: exc_info is None, then the
#: message lacks "error=<Class>" (the text is there instead). M8: every wait is
#: the read value. M11: the client's max_retries is read first. M12: the one
#: charge is found and is 1000, then it is not refunded. M14: the patched 0
#: survives the reload.
REASON = {
    "M1": {G1: "None != (5, 10)"},
    "M2": {G1: "None != (5, 10)"},
    "M3": {G3: ") is not None"},
    # The message holds single quotes, so unittest shows it in double quotes.
    "M4": {G3: 'not found in "Google OAuth token exchange failed: HTTPSConnectionPool'},
    "M5": {P1: SDK_DEFAULT, P2: SDK_DEFAULT},
    "M6": {P1: "{'connect': None, 'read': None", P2: "{'connect': None, 'read': None"},
    "M7": {P1: "{'connect': 0, 'read': 0", P2: "{'connect': 0, 'read': 0"},
    "M8": {
        P1: "{'connect': 300.0, 'read': 300.0",
        # override_settings gives the int 42, which httpx keeps as an int.
        P2: "{'connect': 42, 'read': 42,",
    },
    "M9": {P2: "{'connect': 10.0, 'read': 300.0"},
    "M10": {P1: "{'connect': 10.0, 'read': 30.0"},
    "M11": {P3: "0 != 2"},
    "M12": {R1: "False is not true"},
    "M13": {S1: "0 != 2"},
    "M14": {S1: "0 != 2"},
}

LOAD_FAILURE = "unittest.loader._FailedTest"


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def clear_pycache(path):
    shutil.rmtree(pathlib.Path(path).parent / "__pycache__", ignore_errors=True)


def blocks_of(text, name):
    blocks = re.split(r"^={70}$", text, flags=re.M)
    return [
        b
        for b in blocks
        if re.match(r"\s*(FAIL|ERROR): " + re.escape(name) + r"[ (]", b)
    ]


def judge(mid, returncode, text):
    expected = REASON[mid]
    failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", text, re.M)))
    ran = re.findall(r"^Ran \d+ tests? in .*$", text, re.M)
    ran_line = ran[-1] if ran else None
    wrong = sorted(
        name
        for name, fragment in expected.items()
        if not blocks_of(text, name)
        or not all(fragment in b for b in blocks_of(text, name))
    )
    if returncode == 0:
        status = "SURVIVED"
    elif not ran_line or LOAD_FAILURE in text:
        status = "BROKEN"
    elif set(failed) != set(expected):
        status = "DIFFERS"
    elif wrong:
        status = "WRONG REASON"
    else:
        status = "KILLED"
    return status, ran_line, failed, wrong


def _exit_on_sigterm(signum, _frame):
    raise SystemExit(128 + signum)


def main():
    signal.signal(signal.SIGTERM, _exit_on_sigterm)
    assert list(MUTANTS) == list(REASON), "every mutant named"
    originals = {}
    for mid, (_what, path, old, new) in MUTANTS.items():
        source = originals.setdefault(path, open(path).read())
        found = source.count(old)
        assert found == 1, f"{mid}: anchor found {found} times"
        mutated = source.replace(old, new, 1)
        assert mutated != source, f"{mid}: changes nothing"
        ast.parse(mutated)
    if "--check" in sys.argv:
        print(len(MUTANTS), "mutants: every anchor found once, all parse")
        print(" ".join(MUTANTS))
        return 0
    LOGS.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    results = {}
    for mid, (what, path, old, new) in MUTANTS.items():
        original = originals[path]
        log = LOGS / f"{mid}.txt"
        try:
            clear_pycache(path)
            open(path, "w").write(original.replace(old, new, 1))
            with open(log, "w") as out, open(os.devnull) as devnull:
                p = subprocess.run(
                    [sys.executable, "manage.py", "test", *TESTS]
                    + [
                        f"--settings={SETTINGS}",
                        "--noinput",
                        "--verbosity",
                        "2",
                        "--keepdb",
                    ],
                    stdin=devnull,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    env=env,
                    timeout=600,
                )
        finally:
            open(path, "w").write(original)
            clear_pycache(path)
        assert sha(open(path).read()) == sha(original), f"{mid}: {path} not restored"
        text = log.read_text(errors="replace")
        status, ran_line, failed, wrong = judge(mid, p.returncode, text)
        results[mid] = {
            "what": what,
            "file": path,
            "status": status,
            "exit": p.returncode,
            "ran_line": ran_line,
            "expected": REASON[mid],
            "failing_tests": failed,
            "wrong_reason": wrong,
            "also_failed_not_expected": sorted(set(failed) - set(REASON[mid])),
            "missing": sorted(set(REASON[mid]) - set(failed)),
        }
        print(mid, results[mid], flush=True)
    with open(OUT, "w") as f:
        json.dump(results, f, indent=2)
        f.write("\n")
    for status in ("KILLED", "SURVIVED", "DIFFERS", "WRONG REASON", "BROKEN"):
        print(f"{status}:", [k for k, v in results.items() if v["status"] == status])
    return 0 if all(v["status"] == "KILLED" for v in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
