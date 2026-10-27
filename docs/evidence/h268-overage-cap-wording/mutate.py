# flake8: noqa: E501,B950
"""H-268 OVERAGE_CAP_EXCEEDED, the owner's two sentences: deliberate breaks, and the tests that must catch each.

Each mutant is applied, its test module run, the failing tests are recorded,
the file is restored. Judged (rule 18): SURVIVED = the inner run exits 0;
KILLED = non-zero exit, the run's own "Ran" line, no module that failed to
load, EVERY test named in EXPECTED among the failing tests, and (rule 22)
every FAIL/ERROR block of each expected test holding the fragment written for
it in EXPECTED. Any other failing test is recorded as
also_failed_not_expected, never hidden; BROKEN = anything else. EXPECTED was
written before any run, traced against every assertion of the module
(the control too).

Rule 17: PYTHONDONTWRITEBYTECODE=1 on every inner run; the mutated file's
__pycache__ is deleted before each mutant and after each restore.
Rule 18: each inner run writes straight to its own file, stdin from the null
device, nothing piped. Each mutant is restored in a `finally` block and
SIGTERM is turned into an ordinary exit; the gate script's EXIT trap restores
the file from the commit.
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

HERE = pathlib.Path("docs/evidence/h268-overage-cap-wording")
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))

RC = "AutoGrader/reason_codes.py"
OWN_TESTS = ["AutoGrader.tests_reason_codes.SpecChosenVariantTests"]
CAP_TESTS = ["billing.tests.test_overage_cap"]
K = "keepdb"

# --- Anchors (exact text in reason_codes.py, including leading whitespace) ---

# The default message for OVERAGE_CAP_EXCEEDED (two string literals).
DEFAULT_MSG = (
    '        "Purchase limit exceeded — max {max_blocks} blocks. Reduce "\n'
    '        "your quantity to {remaining} or fewer to proceed.",\n'
)

# The alternative_messages entry for cap_reached.
CAP_REACHED_MSG = (
    '            "cap_reached": (\n'
    '                "Purchase limit reached — you have used all "\n'
    '                "{max_blocks} blocks for this billing cycle. You can buy "\n'
    '                "more when your next cycle starts."\n'
    "            ),\n"
)

# The variant_from_params lambda.
SELECTOR = (
    "        variant_from_params=lambda params: (\n"
    '            "cap_reached" if params.get("remaining") == 0 else None\n'
    "        ),\n"
)

# The chosen/variant logic in CodedError.__init__.
CHOSEN_GUARD = (
    "        chosen = variant\n"
    "        if chosen is None and spec.variant_from_params is not None:\n"
    "            chosen = spec.variant_from_params(params)\n"
)

# The args construction: variant (raiser's) goes in, not chosen.
VARIANT_IN_ARGS = "        if variant is not None:\n"

# id, what is broken, file, text(s) replaced, new text(s), tests, database kind
MUTANTS = [
    # M1: swap the two sentences. Both remaining > 0 and remaining == 0 tests fail.
    (
        "M1",
        "swap the two sentences",
        RC,
        [DEFAULT_MSG, CAP_REACHED_MSG],
        [
            # default slot gets the zero sentence
            '        "Purchase limit reached — you have used all "\n'
            '        "{max_blocks} blocks for this billing cycle. You can buy "\n'
            '        "more when your next cycle starts.",\n',
            # cap_reached slot gets the remaining sentence
            '            "cap_reached": (\n'
            '                "Purchase limit exceeded — max {max_blocks} blocks. Reduce "\n'
            '                "your quantity to {remaining} or fewer to proceed."\n'
            "            ),\n",
        ],
        OWN_TESTS + CAP_TESTS,
        K,
    ),
    # M2: drop the zero branch (variant_from_params always returns None).
    # The zero-left tests see the default sentence with {remaining}=0.
    (
        "M2",
        "drop the zero branch",
        RC,
        SELECTOR,
        "        variant_from_params=lambda params: None,\n",
        OWN_TESTS + CAP_TESTS,
        K,
    ),
    # M3: the boundary — remaining <= 1 instead of == 0. A test with
    # remaining=1 must get the default sentence and this kills it.
    (
        "M3",
        "boundary: remaining <= 1 picks cap_reached",
        RC,
        SELECTOR,
        "        variant_from_params=lambda params: (\n"
        '            "cap_reached" if params.get("remaining") <= 1 else None\n'
        "        ),\n",
        OWN_TESTS + CAP_TESTS,
        K,
    ),
    # M4: in CodedError.__init__, let the spec override a raiser's variant
    # (remove the `if chosen is None` guard). test_a_raiser_that_names_the_
    # variant_still_wins raises with variant="cap_reached" on remaining=1,
    # so the spec returns None and the None overrides the raiser's choice.
    # The test is in SpecChosenVariantTests (the fixture).
    (
        "M4",
        "the spec overrides the raiser's variant",
        RC,
        CHOSEN_GUARD,
        "        chosen = variant\n"
        "        if spec.variant_from_params is not None:\n"
        "            chosen = spec.variant_from_params(params)\n",
        OWN_TESTS,
        K,
    ),
    # M5: put the spec-chosen variant in args (if chosen is not None: instead
    # of if variant is not None:). A serialization round-trip at remaining=0
    # rebuilds an error with a 6-tuple that renders with _variant set, and
    # the args-shape test (len == 4) kills it.
    (
        "M5",
        "put the spec-chosen variant in args",
        RC,
        VARIANT_IN_ARGS,
        "        if chosen is not None:\n",
        OWN_TESTS,
        K,
    ),
]

# --- test names ---
_BLOCKS_LEFT = "test_with_blocks_left_the_sentence_names_what_is_left"
_NONE_LEFT = "test_with_none_left_the_sentence_sends_them_to_the_next_cycle"
_BOTH_PARAMS = "test_both_params_reach_the_body_whichever_sentence_shows"
_ARGS_SHAPE = "test_the_spec_chose_it_so_the_args_keep_their_shape"
_RAISER_WINS = "test_a_raiser_that_names_the_variant_still_wins"
_CONTROL_VARIANT = "test_control_the_caller_named_variant_is_untouched"

_CAP_REACHED = "test_cap_already_reached_rejects_even_a_single_block"
_REMAINING = "test_quantity_exceeding_remaining_cap_is_rejected_before_stripe"
_ENVELOPE = "test_exceeding_the_cap_answers_the_coded_envelope"
_BODY_BOTH = "test_the_rendered_body_carries_the_sentence_in_both_places"
_ZERO_BODY = "test_at_zero_left_both_places_read_the_other_sentence"

NE = "!="  # assertEqual mismatch fragment

#: Written before any run: {mutant: {test: the fragment its every block holds}}.
EXPECTED = {
    # M1: swap → every sentence assertion fails. Both SpecChosenVariant and
    # cap tests. Controls (grant-time, within_cap) are green.
    "M1": {
        _BLOCKS_LEFT: NE,
        _NONE_LEFT: NE,
        _ARGS_SHAPE: NE,
        _REMAINING: NE,
        _CAP_REACHED: NE,
        _ENVELOPE: NE,
        _BODY_BOTH: NE,
        _ZERO_BODY: NE,
    },
    # M2: no zero branch → remaining=0 still gets the default sentence.
    "M2": {
        _NONE_LEFT: NE,
        _ARGS_SHAPE: NE,
        _CAP_REACHED: NE,
        _ZERO_BODY: NE,
    },
    # M3: boundary ≤1 → remaining=1 tests get the zero sentence where they
    # expect the default.
    "M3": {
        _BLOCKS_LEFT: NE,
        _REMAINING: NE,
        _ENVELOPE: NE,
        _BODY_BOTH: NE,
    },
    # M4: spec overrides raiser → raiser_wins fails.
    "M4": {
        _RAISER_WINS: NE,
    },
    # M5: spec-chosen variant in args → the 4-tuple becomes a 6-tuple.
    "M5": {
        _ARGS_SHAPE: "!=",
    },
}


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
