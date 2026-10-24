# flake8: noqa: E501,B950
"""H-205 image-capable backup: deliberate breaks, and the tests that must catch each.

Each mutant is applied, its test modules run, the failing tests are recorded,
the file is restored. Judged (rule 18): SURVIVED = the inner run exits 0;
KILLED = non-zero exit, the run's own "Ran" line, no module that failed to
load, and EVERY test named in EXPECTED among the failing tests (any other
failing test is recorded as also_failed_not_expected, never hidden); BROKEN =
anything else. EXPECTED was written before any run, as a list of every test
reckoned to fail under the mutant, each traced against every assertion of the
five modules (controls too).

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

HERE = pathlib.Path("docs/evidence/h205-image-fallback-model")
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))

SV = "ai_processor/services.py"
ST = "AutoGrader/settings.py"
GR = "ai_processor/grading_run.py"
EM = "audit/emitter.py"
GC = "ai_processor/grading_config.py"
RC = "ai_processor/reading_config.py"
TESTS = [
    "ai_processor.tests_h205_image_fallback",
    "ai_processor.tests_grading_config",
    "ai_processor.tests_reading_config",
    "ai_processor.tests_grading_hardening",
    "audit.tests_metrics",
]
K = "keepdb"

# id, what is broken, file, text replaced, new text, tests, database kind
MUTANTS = [
    (
        "I1",
        "an image is never found (image calls fall back to deepseek)",
        SV,
        '    return any(\n        isinstance(part, dict) and part.get("type") == "image_url" for part in parts\n    )\n',
        "    return False\n",
        TESTS,
        K,
    ),
    (
        "I2",
        "the image branch picks the text backup",
        SV,
        "                IMAGE_FALLBACK_MODELS\n                if _carries_image(",
        "                GRADING_FALLBACK_MODELS\n                if _carries_image(",
        TESTS,
        K,
    ),
    (
        "I3",
        "the text branch picks the image backup (text calls go to haiku)",
        SV,
        "                else GRADING_FALLBACK_MODELS\n            )\n",
        "                else IMAGE_FALLBACK_MODELS\n            )\n",
        TESTS,
        K,
    ),
    (
        "I4",
        "the model pin no longer wins",
        SV,
        "        if override_model:\n            sub_models = [override_model]\n",
        "        if False:\n            sub_models = [override_model]\n",
        TESTS,
        K,
    ),
    (
        "I5",
        "messages are not searched for an image",
        SV,
        '    for message in messages or []:\n        content = message.get("content") if isinstance(message, dict) else None\n        if isinstance(content, list):\n            parts.extend(content)\n    return any(',
        "    return any(",
        TESTS,
        K,
    ),
    (
        "I6",
        "a list system prompt is not searched for an image",
        SV,
        "    for prompt in (user_prompt, system_prompt):",
        "    for prompt in (user_prompt,):",
        TESTS,
        K,
    ),
    (
        "I7",
        "the backup choice applies to every task type",
        SV,
        '        if task_type in ("grade_assignment", "extract_answer", "extract_assignment"):\n            sub_models = (',
        "        if True:\n            sub_models = (",
        TESTS,
        K,
    ),
    (
        "I8",
        "a pdf_url part counts as an image",
        SV,
        'part.get("type") == "image_url" for part in parts',
        'part.get("type") in ("image_url", "pdf_url") for part in parts',
        TESTS,
        K,
    ),
    (
        "I9",
        "a grading run counts only the text backup as a backup",
        GR,
        '        backups = set(self.config.get("GRADING_FALLBACK_MODELS")) | set(\n            self.config.get("IMAGE_FALLBACK_MODELS")\n        )',
        '        backups = set(self.config.get("GRADING_FALLBACK_MODELS"))',
        TESTS,
        K,
    ),
    (
        "I10",
        "the fallback-rate metric counts only the text backup",
        EM,
        "                backup = model in GRADING_FALLBACK_MODELS or (\n                    model in IMAGE_FALLBACK_MODELS\n                )",
        "                backup = model in GRADING_FALLBACK_MODELS",
        TESTS,
        K,
    ),
    (
        "I11",
        "the image list is not part of the grading config version",
        GC,
        '    "GRADING_QUESTIONS_PER_CHUNK",\n    "IMAGE_FALLBACK_MODELS",\n',
        '    "GRADING_QUESTIONS_PER_CHUNK",\n',
        TESTS,
        K,
    ),
    (
        "I12",
        "the image list is not part of the reading config version",
        RC,
        '    "GRADING_FALLBACK_MODELS",\n    "IMAGE_FALLBACK_MODELS",\n    "MAIN_MODEL",',
        '    "GRADING_FALLBACK_MODELS",\n    "MAIN_MODEL",',
        TESTS,
        K,
    ),
    (
        "I13",
        "the second-opinion default goes back to gemini-2.5-flash",
        ST,
        '    default=["deepseek/deepseek-v4-pro", "anthropic/claude-haiku-5.5"],',
        '    default=["deepseek/deepseek-v4-pro", "google/gemini-2.5-flash"],',
        TESTS,
        K,
    ),
    (
        "I14",
        "the image backup is a nano model",
        SV,
        'IMAGE_FALLBACK_MODELS = ["anthropic/claude-haiku-5.5"]',
        'IMAGE_FALLBACK_MODELS = ["openai/gpt-5.4-nano"]',
        TESTS,
        K,
    ),
]

_T1 = "test_an_image_in_the_user_prompt_of_each_page_task"
_T2 = "test_an_image_only_in_a_message"
_T3 = "test_an_image_only_in_a_list_system_prompt"
_T4 = "test_a_text_call_of_each_page_task_keeps_deepseek"
_T5 = "test_a_list_prompt_with_only_text_keeps_deepseek"
_T6 = "test_a_pinned_model_wins_over_an_image"
_T7 = "test_another_task_type_with_an_image_keeps_the_default_list"
_T8 = "test_a_pdf_part_alone_is_not_an_image"
_T9 = "test_an_image_call_sends_haiku_as_the_only_backup"
_T10 = "test_the_image_backup_is_haiku_and_never_a_nano_model"
_T11 = "test_a_grading_answered_by_haiku_says_a_backup_was_used"
_T13 = "test_the_grading_version_changes_with_the_image_list"
_T14 = "test_the_reading_version_changes_with_the_image_list"
_T15 = "test_the_default_candidates_are_deepseek_then_haiku"
_T16 = "test_no_production_file_names_gemini_2_5_flash"
_HARD_GRADING = "test_grading_calls_use_restricted_fallbacks"
_HARD_EXTRACT = "test_extraction_calls_use_restricted_fallbacks"
_HARD_DEFAULT = "test_non_grading_calls_keep_default_fallbacks"
_HARD_NANO = "test_image_fallbacks_never_include_a_nano_model"
_METRIC = "test_a_completed_grading_on_the_image_backup_emits_fallback_rate_one"
_COVER = "test_the_other_values_cover_every_name"
_PINNED = "test_the_version_for_a_fixed_reading_is_pinned"
_READING_EACH = "test_each_constant_that_shapes_a_reading_changes_the_version"

#: Written before any run: every test reckoned to fail under each mutant.
EXPECTED = {
    "I1": [_T1, _T2, _T3, _T9],
    "I2": [_T1, _T2, _T3, _T9],
    "I3": [_T4, _T5, _T8, _HARD_GRADING, _HARD_EXTRACT],
    "I4": [_T6],
    "I5": [_T2],
    "I6": [_T3],
    "I7": [_T7, _HARD_DEFAULT],
    "I8": [_T8],
    "I9": [_T11],
    "I10": [_METRIC],
    "I11": [_T13, _T11, _COVER, _PINNED],
    "I12": [_T14, _READING_EACH],
    "I13": [_T15, _T16],
    "I14": [_T10, _HARD_NANO, _T1, _T2, _T3, _T9, _METRIC, _T11],
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
