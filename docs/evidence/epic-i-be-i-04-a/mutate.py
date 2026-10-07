"""BE-I-04 slice A: deliberate breaks, and the test that must catch each.

Each mutant is applied, its test modules run, the failing tests are
recorded, the file is restored. Judged three ways (rule 18, SM 2026-10-05):
  SURVIVED  the inner run exits 0;
  KILLED    non-zero exit, the run's own "Ran" line, no test module that
            failed to load, and the test named in EXPECTED for that mutant
            among the failing tests;
  BROKEN    anything else. Never counted as a kill.
EXPECTED was written before the first run of any of these tests.

Rule 17: every inner run has PYTHONDONTWRITEBYTECODE=1, and the mutated
file's __pycache__ is deleted before each mutant and after each restore.
Rule 18: each inner run writes straight to its own file
(mutant_logs/<name>.txt), stdin from the null device; nothing is piped.

Two kinds of run:
  keepdb  the test database is kept between mutants (quick);
  fresh   the three mutants of the MIGRATION need a database built from the
          mutated migration, so each builds its own and destroys it.

An interrupted battery leaves no mutant behind: each mutant is restored in
a `finally` block, and SIGTERM (what `timeout` sends) is turned into an
ordinary exit so that block runs. After a SIGKILL nothing in this process
can run; the gate script's EXIT trap restores the files from the commit,
and the next start refuses to run unless every anchor is found once.
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

HERE = pathlib.Path("docs/evidence/epic-i-be-i-04-a")
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))

CONFIG = "ai_processor/grading_config.py"
LABEL = "students/grading_label.py"
MODELS = "students/models.py"
ADMIN = "students/admin.py"
MIGRATION = "students/migrations/0031_submission_grading_label.py"
SERIALIZERS = "students/serializers.py"
DOC_07 = "docs/phase2/architecture/07_epic_i1_implementation_plan.md"

CONFIG_TESTS = ["ai_processor.tests_grading_config"]
LABEL_TESTS = ["students.tests_grading_label_fields"]
ROW_TESTS = ["students.tests_grading_label_migration"]
MIGRATION_TESTS = [
    "students.tests_grading_label_fields",
    "AutoGrader.tests_migration_rollback_defaults",
]

RELEASE_FIELD_IN_MIGRATION = (
    '            name="grading_release",\n'
    "            field=models.CharField(\n"
    '                db_default="unlabelled",\n'
)

#: (name, what, file, old, new, tests, kind)
MUTANTS = [
    (
        "G1",
        "a grade-shaping setting is forgotten in the list",
        CONFIG,
        '    "GRADING_EVIDENCE_ENFORCEMENT",\n',
        "",
        CONFIG_TESTS,
        "keepdb",
    ),
    (
        "G2",
        "a grade-shaping setting is also named as left out",
        CONFIG,
        '    "GRADING_ANSWER_CACHE_ENABLED": ',
        '    "GRADING_MAX_IMAGES_PER_CALL": "moved", "GRADING_ANSWER_CACHE_ENABLED": ',
        CONFIG_TESTS,
        "keepdb",
    ),
    (
        "G3",
        "the release is hashed into the version",
        CONFIG,
        "blob = json.dumps(self.values, ",
        "blob = json.dumps((self.values, self.release), ",
        CONFIG_TESTS,
        "keepdb",
    ),
    (
        "G4",
        "a list setting is kept as the caller's own list, not frozen",
        CONFIG,
        "        return tuple(_frozen(item) for item in value)\n    if isinstance(value, (set",
        "        return value\n    if isinstance(value, (set",
        CONFIG_TESTS,
        "keepdb",
    ),
    (
        "G5",
        "get() reads the live setting again, not the reading",
        CONFIG,
        "            if key == name:\n                return value\n",
        "            if key == name:\n"
        "                return getattr(settings, name, value)\n",
        CONFIG_TESTS,
        "keepdb",
    ),
    (
        "G6",
        'the version is written with "@", which audit metadata drops',
        CONFIG,
        'VERSION_PREFIX = "cfg:"',
        'VERSION_PREFIX = "cfg@"',
        CONFIG_TESTS,
        "keepdb",
    ),
    (
        "G7",
        "a model list is sorted, so its order is lost",
        CONFIG,
        "        return tuple(_frozen(item) for item in value)\n    if isinstance(value, (set",
        "        return tuple(sorted(_frozen(item) for item in value))\n"
        "    if isinstance(value, (set",
        CONFIG_TESTS,
        "keepdb",
    ),
    (
        "G8",
        "an empty release is stored as an empty string",
        CONFIG,
        "    return release[:RELEASE_MAX_LENGTH] or RELEASE_NONE\n",
        "    return release[:RELEASE_MAX_LENGTH]\n",
        CONFIG_TESTS,
        "keepdb",
    ),
    (
        "G9",
        "a long release is not cut to the column",
        CONFIG,
        "    return release[:RELEASE_MAX_LENGTH] or RELEASE_NONE\n",
        "    return release or RELEASE_NONE\n",
        CONFIG_TESTS,
        "keepdb",
    ),
    (
        "G10",
        "a code constant is forgotten in the list",
        CONFIG,
        '    "MAIN_MODEL",\n',
        "",
        CONFIG_TESTS,
        "keepdb",
    ),
    (
        "G11",
        "get() answers None for a name the version does not cover",
        CONFIG,
        "        raise KeyError(name)\n",
        "        return None\n",
        CONFIG_TESTS,
        "keepdb",
    ),
    (
        "G12",
        "the version is computed from the names only",
        CONFIG,
        "blob = json.dumps(self.values, ",
        "blob = json.dumps([key for key, _ in self.values], ",
        CONFIG_TESTS,
        "keepdb",
    ),
    (
        "G13",
        "the grading service is imported at module level",
        CONFIG,
        "from django.conf import settings\n",
        "from django.conf import settings\n\nfrom ai_processor import services  # noqa\n",
        CONFIG_TESTS,
        "keepdb",
    ),
    (
        "G14",
        "the way a version is computed changes",
        CONFIG,
        'separators=(",", ":")',
        'separators=(", ", ": ")',
        CONFIG_TESTS,
        "keepdb",
    ),
    (
        "G15",
        "the release setting is no longer named as left out",
        CONFIG,
        '    "GRADING_RELEASE_ID": "recorded beside the version, never inside it",\n',
        "",
        CONFIG_TESTS,
        "keepdb",
    ),
    (
        "L1",
        "one column's Django default is no longer the placeholder",
        MODELS,
        "    grading_model = models.CharField(\n"
        "        max_length=255,\n"
        "        default=UNLABELLED,\n",
        "    grading_model = models.CharField(\n"
        "        max_length=255,\n"
        '        default="",\n',
        LABEL_TESTS,
        "keepdb",
    ),
    (
        "L2",
        "one column's length differs from the design",
        MODELS,
        "    grading_strictness = models.CharField(\n        max_length=32,\n",
        "    grading_strictness = models.CharField(\n        max_length=16,\n",
        LABEL_TESTS,
        "keepdb",
    ),
    (
        "L3",
        "one column's help text loses the founder's sentence",
        MODELS,
        '            "strictness scale exists. " + FIRST_FORM_NOTE\n',
        '            "strictness scale exists. "\n',
        LABEL_TESTS,
        "keepdb",
    ),
    (
        "L4",
        "the label columns can be edited in the admin screen",
        ADMIN,
        '    readonly_fields = ("id", "submission_date", *LABEL_FIELDS)\n',
        '    readonly_fields = ("id", "submission_date")\n',
        LABEL_TESTS,
        "keepdb",
    ),
    (
        "L5",
        "the placeholder word changes in the code but not in the database",
        LABEL,
        'UNLABELLED = "unlabelled"',
        'UNLABELLED = "legacy"',
        LABEL_TESTS,
        "keepdb",
    ),
    (
        "L6",
        "the founder's sentence no longer says a later stage improves on it",
        LABEL,
        '    "First form of the grading record (BE-I-04). A later stage improves on "\n'
        '    "it: a table',
        '    "First form of the grading record (BE-I-04). See the plan for the "\n'
        '    "rest: a table',
        LABEL_TESTS,
        "keepdb",
    ),
    (
        "L7",
        "a serializer file names a label column",
        SERIALIZERS,
        "from django.utils import timezone\n",
        "from django.utils import timezone\n\n# grading_model\n",
        LABEL_TESTS,
        "keepdb",
    ),
    (
        "L8",
        "plan 07 no longer says this is the first form of the record",
        DOC_07,
        "**That is the first form of the grading record.",
        "**That is the record.",
        LABEL_TESTS,
        "keepdb",
    ),
    (
        "L9",
        "one column's database default is dropped from the model",
        MODELS,
        "    grading_model = models.CharField(\n"
        "        max_length=255,\n"
        "        default=UNLABELLED,\n"
        "        db_default=UNLABELLED,\n",
        "    grading_model = models.CharField(\n"
        "        max_length=255,\n"
        "        default=UNLABELLED,\n",
        LABEL_TESTS,
        "keepdb",
    ),
    (
        "M1",
        "the migration adds one column with no database default",
        MIGRATION,
        RELEASE_FIELD_IN_MIGRATION,
        RELEASE_FIELD_IN_MIGRATION.replace(
            '                db_default="unlabelled",\n', ""
        ),
        MIGRATION_TESTS,
        "fresh",
    ),
    (
        "M2",
        "the migration indexes one column",
        MIGRATION,
        RELEASE_FIELD_IN_MIGRATION,
        RELEASE_FIELD_IN_MIGRATION + "                db_index=True,\n",
        MIGRATION_TESTS,
        "fresh",
    ),
    (
        "M3",
        "the migration makes one column nullable",
        MIGRATION,
        RELEASE_FIELD_IN_MIGRATION,
        RELEASE_FIELD_IN_MIGRATION + "                null=True,\n",
        MIGRATION_TESTS,
        "fresh",
    ),
    (
        "M4",
        "the migration gives one column another word as its database default",
        MIGRATION,
        '            name="grading_model",\n'
        "            field=models.CharField(\n"
        '                db_default="unlabelled",\n',
        '            name="grading_model",\n'
        "            field=models.CharField(\n"
        '                db_default="legacy",\n',
        ROW_TESTS,
        "fresh",
    ),
]

#: The failing test each mutant must produce. Written before any run.
EXPECTED = {
    "G1": "test_every_grading_setting_is_in_the_version_or_named_as_left_out",
    "G2": "test_no_name_is_in_both_lists",
    "G3": "test_the_release_never_changes_the_version",
    "G4": "test_a_list_setting_mutated_after_the_reading_is_not_seen",
    "G5": "test_a_setting_changed_after_the_reading_is_not_seen",
    "G6": "test_audit_metadata_would_keep_it",
    "G7": "test_the_order_of_a_model_list_is_part_of_the_version",
    "G8": "test_no_release_from_the_host_is_the_word_none",
    "G9": "test_a_long_release_is_cut_to_the_column",
    "G10": "test_the_other_values_cover_every_name",
    "G11": "test_get_refuses_a_name_the_version_does_not_cover",
    "G12": "test_each_grade_shaping_setting_changes_the_version",
    "G13": "test_it_does_not_import_the_grading_service_at_module_level",
    "G14": "test_the_version_for_a_fixed_reading_is_pinned",
    "G15": "test_every_grading_setting_is_in_the_version_or_named_as_left_out",
    "L1": "test_each_has_the_placeholder_as_both_defaults",
    "L2": "test_each_is_a_not_null_text_column_of_the_designed_length",
    "L3": "test_each_column_carries_the_sentence_in_its_help_text",
    "L4": "test_the_six_columns_are_read_only_there",
    "L5": "test_each_column_is_not_null_with_the_placeholder_as_its_default",
    "L6": "test_the_sentence_says_first_form_and_names_the_later_table",
    "L7": "test_no_serializer_file_names_a_label_column",
    "L8": "test_each_document_says_first_form_and_names_the_later_table",
    "L9": "test_each_has_the_placeholder_as_both_defaults",
    "M1": "test_each_column_is_not_null_with_the_placeholder_as_its_default",
    "M2": "test_no_index_was_added_for_them",
    "M3": "test_each_column_is_not_null_with_the_placeholder_as_its_default",
    "M4": "test_a_row_made_before_0031_reads_the_placeholder_after_it",
}

#: A test module that could not be loaded.
LOAD_FAILURE = "unittest.loader._FailedTest"


def clear_pycache(path):
    shutil.rmtree(pathlib.Path(path).parent / "__pycache__", ignore_errors=True)


def judge(mid, returncode, text):
    """(status, the run's "Ran" line, the failing tests' names)."""
    failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", text, re.M)))
    ran = re.findall(r"^Ran \d+ tests? in .*$", text, re.M)
    ran_line = ran[-1] if ran else None
    if returncode == 0:
        status = "SURVIVED"
    elif ran_line and LOAD_FAILURE not in text and EXPECTED[mid] in failed:
        status = "KILLED"
    else:
        status = "BROKEN"
    return status, ran_line, failed


def _exit_on_sigterm(signum, _frame):
    raise SystemExit(128 + signum)


def main():
    signal.signal(signal.SIGTERM, _exit_on_sigterm)
    assert [m[0] for m in MUTANTS] == list(EXPECTED), "EXPECTED names every mutant"
    originals = {}
    for mid, _what, path, old, new, _tests, kind in MUTANTS:
        source = originals.setdefault(path, open(path).read())
        assert source.count(old) == 1, f"{mid}: anchor found {source.count(old)} times"
        assert kind in ("keepdb", "fresh"), mid
        if path.endswith(".py"):
            ast.parse(source.replace(old, new, 1))
    if "--check" in sys.argv:
        print(len(MUTANTS), "mutants: every anchor found once, all parse")
        print(" ".join(m[0] for m in MUTANTS))
        return
    LOGS.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    results = {}
    # MUT_ONLY="M4" runs the named mutants alone (a delta after a battery
    # that already ran). Give MUT_RESULTS another file then, so the first
    # battery's results are not overwritten.
    only = set(os.environ.get("MUT_ONLY", "").split())
    assert only <= set(EXPECTED), f"unknown mutants in MUT_ONLY: {only}"
    for mid, what, path, old, new, tests, kind in MUTANTS:
        if only and mid not in only:
            continue
        original = originals[path]
        database = ["--keepdb"] if kind == "keepdb" else []
        try:
            clear_pycache(path)
            open(path, "w").write(original.replace(old, new, 1))
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
            "expected": EXPECTED[mid],
            "failing_tests": failed,
        }
        print(mid, results[mid], flush=True)
    with open(OUT, "w") as f:
        json.dump(results, f, indent=2)
        f.write("\n")
    for status in ("KILLED", "SURVIVED", "BROKEN"):
        print(f"{status}:", [k for k, v in results.items() if v["status"] == status])


if __name__ == "__main__":
    main()
