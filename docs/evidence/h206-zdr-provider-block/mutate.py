# flake8: noqa: E501,B950
"""H-206 zero retention: deliberate breaks, and the tests that must catch each.

Each mutant is applied, its test module run, the failing tests are recorded,
the file is restored. Judged (rule 18): SURVIVED = the inner run exits 0;
KILLED = non-zero exit, the run's own "Ran" line, no module that failed to
load, and EVERY test named in EXPECTED among the failing tests (any other
failing test is recorded as also_failed_not_expected, never hidden); BROKEN =
anything else. EXPECTED was written before any run, as a list of every test
reckoned to fail under the mutant.

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

HERE = pathlib.Path("docs/evidence/h206-zdr-provider-block")
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))

SV = "ai_processor/services.py"
ST = "AutoGrader/settings.py"
TL = "ai_processor/tools.py"
TESTS = ["ai_processor.tests_provider_block"]
K = "keepdb"

COMMENT_NEW = (
    '                    # data_collection "deny" filters suppliers that train on\n'
    "                    # data; zdr filters suppliers that retain prompts. Both\n"
    "                    # are in the block. Student names and submission content\n"
    "                    # pass through this call.\n"
)
COMMENT_OLD = (
    "                    # Refuse any upstream provider (e.g. DeepSeek) that may\n"
    "                    # retain or train on this request. Student names and\n"
    "                    # submission content pass through this call.\n"
)

# id, what is broken, file, text replaced (or a list), new text (or a list), tests, database kind
MUTANTS = [
    (
        "Z1",
        "the tools shape sends the old literal block, no zdr",
        SV,
        '                    # pass through this call.\n                    "provider": _provider_block(),\n',
        '                    # pass through this call.\n                    "provider": {"data_collection": "deny"},\n',
        TESTS,
        K,
    ),
    (
        "Z2",
        "the plain shape sends the old literal block, no zdr",
        SV,
        '                    "models": sub_models,\n                    "provider": _provider_block(),\n',
        '                    "models": sub_models,\n                    "provider": {"data_collection": "deny"},\n',
        TESTS,
        K,
    ),
    (
        "Z3",
        "zdr is hard-coded and the setting is ignored",
        SV,
        '    if getattr(settings, "AI_PROVIDER_ZDR_ENABLED", True):\n        block["zdr"] = True\n',
        '    block["zdr"] = True\n',
        TESTS,
        K,
    ),
    (
        "Z4",
        "the setting is inverted",
        SV,
        '    if getattr(settings, "AI_PROVIDER_ZDR_ENABLED", True):\n',
        '    if not getattr(settings, "AI_PROVIDER_ZDR_ENABLED", True):\n',
        TESTS,
        K,
    ),
    (
        "Z5",
        "the helper returns a shared dict",
        SV,
        ["def _provider_block():\n", '    block = {"data_collection": "deny"}\n'],
        [
            '_SHARED = {"data_collection": "deny"}\n\n\ndef _provider_block():\n',
            "    block = _SHARED\n",
        ],
        TESTS,
        K,
    ),
    (
        "Z6",
        "data_collection is dropped from the block",
        SV,
        '    block = {"data_collection": "deny"}\n',
        "    block = {}\n",
        TESTS,
        K,
    ),
    (
        "Z7",
        "zdr is sent as the string 'true'",
        SV,
        '        block["zdr"] = True\n',
        '        block["zdr"] = "true"\n',
        TESTS,
        K,
    ),
    (
        "Z8",
        "zdr is placed beside the provider object (in extra_body), not inside it",
        SV,
        '                    "models": sub_models,\n                    "provider": _provider_block(),\n                },\n',
        '                    "models": sub_models,\n                    "provider": {"data_collection": "deny"},\n                    "zdr": True,\n                },\n',
        TESTS,
        K,
    ),
    (
        "Z9",
        "zdr is sent only when the fallback list is the default one",
        SV,
        '                    "models": sub_models,\n                    "provider": _provider_block(),\n',
        '                    "models": sub_models,\n                    "provider": _provider_block() if sub_models is DEFAULT_FALLBACK_MODELS else {"data_collection": "deny"},\n',
        TESTS,
        K,
    ),
    (
        "Z10",
        "the setting is read once at import",
        SV,
        [
            "def _provider_block():\n",
            '    if getattr(settings, "AI_PROVIDER_ZDR_ENABLED", True):\n        block["zdr"] = True\n',
        ],
        [
            '_ZDR_AT_IMPORT = getattr(settings, "AI_PROVIDER_ZDR_ENABLED", True)\n\n\ndef _provider_block():\n',
            '    if _ZDR_AT_IMPORT:\n        block["zdr"] = True\n',
        ],
        TESTS,
        K,
    ),
    (
        "Z11",
        "the old comment (deny refuses providers that retain) is restored",
        SV,
        COMMENT_NEW,
        COMMENT_OLD,
        TESTS,
        K,
    ),
    (
        "Z12",
        "the setting's default is off",
        ST,
        'AI_PROVIDER_ZDR_ENABLED = env.bool("AI_PROVIDER_ZDR_ENABLED", default=True)',
        'AI_PROVIDER_ZDR_ENABLED = env.bool("AI_PROVIDER_ZDR_ENABLED", default=False)',
        TESTS,
        K,
    ),
    (
        "Z13",
        "a third create() call outside __ai_model",
        SV,
        "    def get_ai_model_function(self):\n",
        '    def _unguarded(self):\n        return self.client.chat.completions.create(model="x", messages=[])\n\n    def get_ai_model_function(self):\n',
        TESTS,
        K,
    ),
    (
        "Z14",
        "a file that builds a provider client is not on the named list",
        TL,
        "import ipaddress\n",
        'import ipaddress\nfrom openai import OpenAI\n\n_UNLISTED = OpenAI(api_key="x")\n',
        TESTS,
        K,
    ),
]

_EXACT = "test_the_provider_block_that_reaches_the_sdk_is_exact_on_every_path"
_BOOL = "test_zdr_is_the_boolean_true_not_a_string"
_INSIDE = "test_zdr_is_inside_the_provider_object_not_the_top_level_body"
_OFF = "test_with_the_switch_off_the_block_is_exactly_data_collection_deny"
_READ = "test_the_switch_is_read_at_each_call_not_once_at_import"
_EXIST = "test_it_exists_and_returns_the_block"
_FRESH = "test_it_returns_a_new_dict_each_call"
_CHANGE = "test_a_change_to_a_sent_block_cannot_reach_the_next_request"
_BOTH = "test_both_requests_take_the_block_from_the_helper"
_LIT = "test_the_data_collection_literal_exists_only_inside_the_helper"

#: Written before any run: every test reckoned to fail under each mutant.
EXPECTED = {
    "Z1": [_EXACT, _BOOL, _INSIDE, _BOTH, _LIT],
    "Z2": [_EXACT, _BOOL, _INSIDE, _BOTH, _LIT],
    "Z3": [_OFF, _READ],
    "Z4": [_EXACT, _BOOL, _INSIDE, _OFF, _READ, _EXIST, _FRESH, _CHANGE],
    "Z5": [_FRESH, _CHANGE, _OFF, _READ],
    "Z6": [_EXACT, _OFF, _READ, _EXIST, _FRESH, _CHANGE],
    "Z7": [_EXACT, _BOOL, _EXIST, _FRESH, _READ, _CHANGE],
    "Z8": [_EXACT, _INSIDE, _BOTH, _LIT],
    "Z9": [_EXACT, _BOOL, _INSIDE, _LIT],
    "Z10": [_OFF, _READ],
    "Z11": [
        "test_the_old_claim_that_deny_refuses_providers_that_retain_is_gone",
        "test_the_comment_says_deny_filters_suppliers_that_train_and_zdr_those_that_retain",
    ],
    "Z12": [
        "test_the_default_is_on",
        "test_the_settings_module_reads_the_variable_with_default_true",
        _EXACT,
        _BOOL,
        _INSIDE,
        _READ,
        _EXIST,
        _FRESH,
        _CHANGE,
    ],
    "Z13": ["test_the_only_create_calls_are_the_two_in_ai_model"],
    "Z14": ["test_every_file_that_builds_a_client_or_a_request_is_on_the_named_list"],
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
                    timeout=1200,
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
