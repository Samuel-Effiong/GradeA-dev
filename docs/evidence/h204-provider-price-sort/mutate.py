# flake8: noqa: E501,B950
"""H-204 provider price sort and no fp4: deliberate breaks, and the tests that must catch each.

Each mutant is applied, its test modules run, the failing tests are recorded,
the file is restored. Judged (rule 18): SURVIVED = the inner run exits 0;
KILLED = non-zero exit, the run's own "Ran" line, no module that failed to
load, and EVERY test named in EXPECTED among the failing tests (any other
failing test is recorded as also_failed_not_expected, never hidden); BROKEN =
anything else. EXPECTED was written before any run, as a list of every test
reckoned to fail under the mutant, each traced against every assertion of the
two modules (controls too).

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

HERE = pathlib.Path("docs/evidence/h204-provider-price-sort")
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))

SV = "ai_processor/services.py"
TESTS = [
    "ai_processor.tests_h204_provider_price_sort",
    "ai_processor.tests_provider_block",
]
K = "keepdb"
REQUEST = '                "provider": _provider_block(),\n'
STRIP = 'k not in ("sort", "quantizations")'

# id, what is broken, file, text replaced, new text, tests, database kind
MUTANTS = [
    (
        "P1",
        "the sort key dropped",
        SV,
        '        "sort": PROVIDER_SORT,\n',
        "",
        TESTS,
        K,
    ),
    (
        "P2",
        "the quantizations key dropped",
        SV,
        '        "quantizations": list(PROVIDER_QUANTIZATIONS),\n',
        "",
        TESTS,
        K,
    ),
    (
        "P3",
        "the sort value changed to throughput",
        SV,
        'PROVIDER_SORT = "price"',
        'PROVIDER_SORT = "throughput"',
        TESTS,
        K,
    ),
    (
        "P4",
        "fp4 added back",
        SV,
        'PROVIDER_QUANTIZATIONS = ("fp8", "fp16", "bf16", "fp32", "unknown")',
        'PROVIDER_QUANTIZATIONS = ("fp4", "fp8", "fp16", "bf16", "fp32", "unknown")',
        TESTS,
        K,
    ),
    (
        "P5",
        '"unknown" dropped (grok and haiku would have no endpoint)',
        SV,
        'PROVIDER_QUANTIZATIONS = ("fp8", "fp16", "bf16", "fp32", "unknown")',
        'PROVIDER_QUANTIZATIONS = ("fp8", "fp16", "bf16", "fp32")',
        TESTS,
        K,
    ),
    (
        "P6",
        "the two keys left out when a model is pinned (the second opinion)",
        SV,
        REQUEST,
        '                "provider": {k: v for k, v in _provider_block().items() if not override_model or '
        + STRIP
        + "},\n",
        TESTS,
        K,
    ),
    (
        "P7",
        "the two keys left out when zdr is on",
        SV,
        '        block["zdr"] = True\n',
        '        block = {"data_collection": "deny", "zdr": True}\n',
        TESTS,
        K,
    ),
    (
        "P8",
        "the two keys left out for the reading task types",
        SV,
        REQUEST,
        '                "provider": {k: v for k, v in _provider_block().items() if task_type not in '
        '("extract_answer", "extract_assignment") or ' + STRIP + "},\n",
        TESTS,
        K,
    ),
]

# tests_h204_provider_price_sort
_CHEAP = "test_the_cheapest_supplier_is_tried_first"
_ALLOWED = "test_the_allowed_quantizations"
_UNKNOWN = "test_unknown_stays_because_grok_and_haiku_publish_none"
_NO_FP4 = "test_no_fp4_endpoint"
_ZDR_ON = "test_with_zdr_on_the_block_keeps_zdr_and_has_both_keys"
_NEW_LIST = "test_each_call_gives_a_new_list"
_TEXT = "test_a_text_grading_request_and_its_deepseek_backup"
_TEXT_ZDR = "test_a_text_grading_request_with_zdr_on"
_IMAGE = "test_an_image_grading_request_and_its_haiku_backup"
_SECOND = "test_the_second_opinion_request"
_READ = "test_a_reading_request"
_REQUESTS = [_TEXT, _TEXT_ZDR, _IMAGE, _SECOND, _READ]
# tests_provider_block (H-206), whose EXPECTED and OFF now carry the two keys
_EXACT = "test_the_provider_block_that_reaches_the_sdk_is_exact_on_every_path"
_AT_DEFAULT = "test_at_the_default_no_request_carries_zdr_anywhere"
_OFF_ON = "test_off_sends_no_zdr_and_on_sends_zdr_true_on_every_path"
_SWITCH_OFF = "test_with_the_switch_off_the_block_is_exactly_data_collection_deny"
_READ_EACH = "test_the_switch_is_read_at_each_call_not_once_at_import"
_EXISTS = "test_it_exists_and_returns_the_block"
_NEW_DICT = "test_it_returns_a_new_dict_each_call"
_CHANGE = "test_a_change_to_a_sent_block_cannot_reach_the_next_request"
_OVERRIDE = "test_the_override_and_the_fallback_list_do_not_change_the_block"
_H206_EXACT = [
    _EXACT,
    _AT_DEFAULT,
    _OFF_ON,
    _SWITCH_OFF,
    _READ_EACH,
    _EXISTS,
    _NEW_DICT,
    _CHANGE,
]

#: Written before any run: every test reckoned to fail under each mutant,
#: traced against every assertion of both modules.
EXPECTED = {
    "P1": [_CHEAP, _ZDR_ON, *_REQUESTS, *_H206_EXACT],
    "P2": [_ALLOWED, _UNKNOWN, _NO_FP4, _ZDR_ON, _NEW_LIST, *_REQUESTS, *_H206_EXACT],
    "P3": [_CHEAP, _ZDR_ON, *_REQUESTS, *_H206_EXACT],
    "P4": [_ALLOWED, _NO_FP4, _ZDR_ON, _NEW_LIST, *_REQUESTS, *_H206_EXACT],
    "P5": [_ALLOWED, _UNKNOWN, _ZDR_ON, _NEW_LIST, *_REQUESTS, *_H206_EXACT],
    "P6": [_SECOND, _EXACT, _AT_DEFAULT, _OFF_ON, _SWITCH_OFF, _OVERRIDE],
    "P7": [
        _ZDR_ON,
        _TEXT_ZDR,
        _EXACT,
        _OFF_ON,
        _READ_EACH,
        _EXISTS,
        _NEW_DICT,
        _CHANGE,
    ],
    "P8": [_READ],
}

#: A test module that could not be loaded.
LOAD_FAILURE = "unittest.loader._FailedTest"


def clear_pycache(path):
    shutil.rmtree(pathlib.Path(path).parent / "__pycache__", ignore_errors=True)


def _expected(mid):
    """The failing tests written beforehand for a mutant: one name or a list.
    A list names EVERY test reckoned to fail under it, not only the one the
    mutant was made for (Senior Manager, 8 October 2026)."""
    value = EXPECTED[mid]
    return [value] if isinstance(value, str) else list(value)


def judge(mid, returncode, text):
    """(status, the run's "Ran" line, the failing tests' names)."""
    failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", text, re.M)))
    ran = re.findall(r"^Ran \d+ tests? in .*$", text, re.M)
    ran_line = ran[-1] if ran else None
    if returncode == 0:
        status = "SURVIVED"
    elif ran_line and LOAD_FAILURE not in text and set(_expected(mid)) <= set(failed):
        status = "KILLED"
    else:
        status = "BROKEN"
    return status, ran_line, failed


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
        if path.endswith(".py"):
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
        status, ran_line, failed = judge(mid, p.returncode, text)
        results[mid] = {
            "what": what,
            "file": path,
            "kind": kind,
            "status": status,
            "exit": p.returncode,
            "ran_line": ran_line,
            "expected": _expected(mid),
            "failing_tests": failed,
            "also_failed_not_expected": sorted(set(failed) - set(_expected(mid))),
        }
        print(mid, results[mid], flush=True)
    with open(OUT, "w") as f:
        json.dump(results, f, indent=2)
        f.write("\n")
    for status in ("KILLED", "SURVIVED", "BROKEN"):
        print(f"{status}:", [k for k, v in results.items() if v["status"] == status])


if __name__ == "__main__":
    main()
