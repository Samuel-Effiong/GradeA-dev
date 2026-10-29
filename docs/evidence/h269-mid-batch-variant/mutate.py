# flake8: noqa: E501,B950
"""H-269 MID_BATCH none_finished moved from raiser to spec: deliberate breaks.

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

HERE = pathlib.Path("docs/evidence/h269-mid-batch-variant")
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))

RC = "AutoGrader/reason_codes.py"
OWN_TESTS = ["AutoGrader.tests_reason_codes.SpecChosenVariantMidBatchTests"]
K = "keepdb"

# --- Anchors (exact text in reason_codes.py, including leading whitespace) ---

FUNC_BODY = '    return "none_finished" if params.get("completed") == 0 else None\n'

SPEC_REF = "        variant_from_params=_mid_batch_variant,\n"

# id, what is broken, file, text(s) replaced, new text(s), tests, database kind
MUTANTS = [
    # M1: drop variant_from_params (set to None). completed=0 gets the default
    # sentence "after 0 of 4", not "before any of the 4".
    (
        "M1",
        "drop variant_from_params (no spec selection)",
        RC,
        SPEC_REF,
        "        variant_from_params=None,\n",
        OWN_TESTS,
        K,
    ),
    # M2: always return "none_finished" regardless of completed.
    # completed > 0 gets the wrong sentence.
    (
        "M2",
        "always return none_finished (ignore completed)",
        RC,
        FUNC_BODY,
        '    return "none_finished"\n',
        OWN_TESTS,
        K,
    ),
    # M3: boundary — completed <= 1 instead of == 0. completed=1 would get
    # "none_finished" when it should get the default.
    (
        "M3",
        "boundary: completed <= 1 picks none_finished",
        RC,
        FUNC_BODY,
        '    return "none_finished" if params.get("completed") <= 1 else None\n',
        OWN_TESTS,
        K,
    ),
]

# --- test names ---
_SPEC_PICKS = "test_the_spec_picks_none_finished_from_completed"
_DEFAULT = "test_with_items_finished_the_spec_picks_the_default"
_BOUNDARY = "test_boundary_one_finished_is_the_default"
_RAISER_WINS = "test_a_raiser_that_names_the_variant_still_wins"
_ARGS = "test_args_keep_the_4_tuple"

NE = "!="  # assertEqual mismatch fragment
NOT_NONE = "unexpectedly None"  # assertIsNotNone failure message

#: Written before any run: {mutant: {test: the fragment its every block holds}}.
EXPECTED = {
    # M1: no spec selection → completed=0 gets the default, not none_finished.
    "M1": {
        _SPEC_PICKS: NOT_NONE,
        _DEFAULT: NOT_NONE,
        _ARGS: NE,
    },
    # M2: always none_finished → completed > 0 gets the wrong sentence.
    "M2": {
        _DEFAULT: NE,
        _BOUNDARY: NE,
    },
    # M3: boundary ≤1 → completed=1 gets none_finished instead of default.
    "M3": {
        _BOUNDARY: NE,
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
    return sorted(
        name
        for name, fragment in EXPECTED[mid].items()
        if not all(fragment in b for b in blocks_of(text, name))
    )


def judge(mid, returncode, text):
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
