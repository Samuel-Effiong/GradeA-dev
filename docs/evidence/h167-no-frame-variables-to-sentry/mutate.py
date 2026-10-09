"""H-167 mutants. Each is one textual change to one production file; the
three test modules run against it; the failing tests are recorded; the
file is restored. The runner is H-153's with this list.

Written BEFORE any run: every mutant names the tests it must fail
(EXPECTED, full dotted names), each set read assertion by assertion
against the mutant. A mutant is KILLED only if the inner run ends
non-zero, shows its own "Ran" line, loaded every module, and every
expected test is among those that failed. Other outcomes are reported as
what they are: SURVIVED (exit 0), KILLED_NOT_AS_EXPECTED (it failed, but
not all the named tests did), BROKEN (anything else). Only KILLED counts.
EVIDENCE.md also compares each failing set with the written one exactly.

Three tests of the new module are CONTROLS on the libraries (the SDK's
bare defaults attach a frame's variables; they keep a small body; a
pool prints its password). No change to OUR files can make them fail, so
no mutant names them, and they are not counted as evidence of the change.
Every other test of the new module, and the one reversed test of
tests_sentry_scrubbing, is named by at least one mutant (checked below).

Rule 17: the inner run has PYTHONDONTWRITEBYTECODE=1 and every
__pycache__ under the mutated apps is deleted before each mutant and after
each restore. Rule 18: each inner run writes straight to a file
(mutant_logs/<name>.txt), stdin from /dev/null; nothing is piped.

    python docs/evidence/h167-no-frame-variables-to-sentry/mutate.py --check
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
    "AutoGrader.tests_sentry_sends_no_variables",
    "AutoGrader.tests_sentry_scrubbing",
    "AutoGrader.tests_log_scrubbing",
]
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
HERE = pathlib.Path("docs/evidence/h167-no-frame-variables-to-sentry")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))
PYCACHE_ROOTS = ["AutoGrader"]

SETTINGS_PY = "AutoGrader/settings.py"
LOGS_PY = "AutoGrader/log_scrubbing.py"
HOOKS_PY = "AutoGrader/sentry_scrubbing.py"

N = "AutoGrader.tests_sentry_sends_no_variables."
INIT = N + "TheInitCallTests."
S = N + "WhatTheSdkDoesWithOurCallTests."
P = N + "APoolsPrintedFormTests."
V = N + "TheNamedValuePatternTests."
G = N + "WhatALogLinePrintsTests."
R = N + "TheRestOfTheEventTests."
OLD = "AutoGrader.tests_sentry_scrubbing."

VARS_OFF = INIT + "test_it_turns_frame_variables_off"
BODY_OFF = INIT + "test_it_sends_no_request_body"
PII_OFF = INIT + "test_it_still_sends_no_default_pii"
NO_VARS = S + "test_with_our_call_no_frame_has_variables"
NO_BODY = S + "test_with_our_call_no_body_is_within_bounds"
POOL_VAR = P + "test_as_a_frames_variable_text_it_is_replaced"
POOL_TEXT = P + "test_in_an_exceptions_text_and_a_message_it_is_replaced"
POOL_CRUMB = P + "test_in_a_breadcrumb_and_a_log_item_it_is_replaced"
FOUR = V + "test_each_of_the_four_names"
NO_AT = V + "test_a_text_with_no_at_sign_is_scrubbed_too"
CASE = V + "test_the_names_case_and_a_longer_name_that_ends_in_one"
TO_EOL = V + "test_the_value_is_taken_to_the_end_of_its_line_and_no_further"
TWO_LINES = V + "test_two_lines_each_with_one"
BESIDE = V + "test_an_address_and_a_url_password_are_still_replaced_beside_it"
ALIKE = V + "test_text_that_only_looks_alike_is_unchanged"
LOG_MSG = G + "test_a_message_built_with_a_pool"
LOG_EXC = G + "test_an_exceptions_text"
NONE_LEFT = R + "test_none_of_the_made_values_is_left"
EACH_PART = R + "test_each_part_by_itself"
KEPT = R + "test_what_holds_none_of_them_is_kept"
REVERSED = OLD + "BeforeSendTests.test_user_context_tags_and_request_are_scrubbed_too"
WIRING = OLD + "WiringTests.test_settings_pass_the_hooks_to_sentry"

#: The controls on the libraries: named by no mutant, on purpose.
CONTROLS = [
    S + "test_control_with_the_bare_defaults_a_frames_variable_is_in_the_event",
    S + "test_control_with_the_bare_defaults_a_small_body_is_within_bounds",
    P + "test_guard_the_library_still_prints_the_password",
]
MUST_BE_NAMED = [
    VARS_OFF, BODY_OFF, PII_OFF, NO_VARS, NO_BODY, POOL_VAR, POOL_TEXT,
    POOL_CRUMB, FOUR, NO_AT, CASE, TO_EOL, TWO_LINES, BESIDE, ALIKE,
    LOG_MSG, LOG_EXC, NONE_LEFT, EACH_PART, KEPT, REVERSED,
]  # fmt: skip

VARS_LINE = "            include_local_variables=False,\n"
BODY_LINE = '            max_request_body_size="never",\n'
PATTERN = '_NAMED_VALUE = re.compile(r"(?i)(password|passwd|secret|token)=[^\\n]+")\n'
REST_OF_LINE = "[^\\n]+"
APPLY = "        text = _NAMED_VALUE.sub(_name_kept, text)\n"
#: Every test in which a made value follows one of the names, whatever
#: else is in the text.
EVERY_NAMED = [
    POOL_VAR, POOL_TEXT, POOL_CRUMB, FOUR, NO_AT, CASE, TO_EOL, TWO_LINES,
    BESIDE, LOG_MSG, LOG_EXC, NONE_LEFT, EACH_PART,
]  # fmt: skip
#: (Re-derived 2026-10-07 17:10, before any run, on the Senior Manager's
#: word: every one of these tests now looks for each PIECE of the made
#: value, or compares the whole text, so a pattern that stops early and
#: leaves a tail fails all thirteen. G3 and G4 named six of them before.)

MUTANTS = {
    # ---- the two settings
    "S1_frame_variables_are_not_turned_off": (
        SETTINGS_PY,
        VARS_LINE,
        "",
        [VARS_OFF, NO_VARS],
    ),
    "S2_frame_variables_are_turned_on": (
        SETTINGS_PY,
        VARS_LINE,
        "            include_local_variables=True,\n",
        [VARS_OFF, NO_VARS],
    ),
    "S3_the_request_body_setting_is_not_passed": (
        SETTINGS_PY,
        BODY_LINE,
        "",
        [BODY_OFF, NO_BODY],
    ),
    "S4_small_request_bodies_are_sent": (
        SETTINGS_PY,
        BODY_LINE,
        '            max_request_body_size="small",\n',
        [BODY_OFF, NO_BODY],
    ),
    "S5_default_pii_is_sent": (
        SETTINGS_PY,
        "            send_default_pii=False,\n",
        "            send_default_pii=True,\n",
        [PII_OFF, WIRING],
    ),
    # ---- the pattern
    "G1_the_named_value_pattern_is_not_run": (
        LOGS_PY,
        APPLY,
        "        pass\n",
        EVERY_NAMED,
    ),
    "G2_it_is_run_only_on_a_text_with_an_at_sign": (
        LOGS_PY,
        '    if "=" in text:\n',
        '    if "=" in text and "@" in text:\n',
        # BESIDE's text has an "@"; so has the request's query string, but
        # its Referer header has none and holds the made value.
        [t for t in EVERY_NAMED if t != BESIDE],
    ),
    "G3_the_value_ends_at_a_comma": (
        LOGS_PY,
        PATTERN,
        PATTERN.replace(REST_OF_LINE, "[^\\n,]+"),
        EVERY_NAMED,
    ),
    "G4_the_value_ends_at_a_space": (
        LOGS_PY,
        PATTERN,
        PATTERN.replace(REST_OF_LINE, "[^\\s]+"),
        EVERY_NAMED,
    ),
    "G5_only_lower_case_names": (
        LOGS_PY,
        PATTERN,
        PATTERN.replace("(?i)", ""),
        [CASE],
    ),
    "G6_passwd_is_not_one_of_the_names": (
        LOGS_PY,
        PATTERN,
        PATTERN.replace("|passwd", ""),
        [FOUR],
    ),
    "G7_secret_is_not_one_of_the_names": (
        LOGS_PY,
        PATTERN,
        PATTERN.replace("|secret", ""),
        [FOUR, CASE, TWO_LINES, NONE_LEFT, EACH_PART],
    ),
    "G8_token_is_not_one_of_the_names": (
        LOGS_PY,
        PATTERN,
        PATTERN.replace("|token", ""),
        [FOUR, CASE, TO_EOL, NONE_LEFT, EACH_PART],
    ),
    "G9_the_name_is_not_kept": (
        LOGS_PY,
        '    return f"{match.group(1)}={SECRET}"\n',
        "    return SECRET\n",
        [t for t in EVERY_NAMED if t not in (NONE_LEFT, EACH_PART)],
    ),
    "G10_a_name_with_nothing_after_it_is_replaced": (
        LOGS_PY,
        PATTERN,
        PATTERN.replace(REST_OF_LINE, "[^\\n]*"),
        [ALIKE],
    ),
    # ---- the four parts of the event
    "H1_the_request_is_left_alone": (
        HOOKS_PY,
        '    "request",\n',
        "",
        [NONE_LEFT, EACH_PART, KEPT],
    ),
    "H2_the_tags_are_left_alone": (
        HOOKS_PY,
        '    "tags",\n',
        "",
        [NONE_LEFT, EACH_PART, REVERSED],
    ),
    "H3_the_user_is_left_alone": (
        HOOKS_PY,
        '    "user",\n',
        "",
        [NONE_LEFT, EACH_PART, REVERSED],
    ),
    "H4_the_contexts_are_left_alone": (
        HOOKS_PY,
        '    "contexts",\n',
        "",
        [NONE_LEFT, EACH_PART, KEPT],
    ),
}

COVERED = {test for mutant in MUTANTS.values() for test in mutant[3]}
assert set(MUST_BE_NAMED) <= COVERED, sorted(set(MUST_BE_NAMED) - COVERED)
assert not COVERED & set(CONTROLS)


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
