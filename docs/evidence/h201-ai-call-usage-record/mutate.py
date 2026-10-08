"""H-201 per-call usage record: deliberate breaks, and the tests that must catch each.

Each mutant is applied, its test modules run, the failing tests are
recorded, the file is restored. Judged (rule 18, SM 2026-10-05):
  SURVIVED  the inner run exits 0;
  KILLED    non-zero exit, the run's own "Ran" line, no test module that
            failed to load, and EVERY test named in EXPECTED among the
            failing tests; any other failing test is recorded as
            also_failed_not_expected, never hidden;
  BROKEN    anything else. Never counted as a kill.
EXPECTED was written before any run of a mutant, as a list of every test
reckoned to fail under it (Senior Manager, 8 October 2026).

Rule 17: PYTHONDONTWRITEBYTECODE=1 on every inner run; the mutated file's
__pycache__ is deleted before each mutant and after each restore.
Rule 18: each inner run writes straight to its own file, stdin from the null
device, nothing piped. Each mutant is restored in a `finally` block and SIGTERM
is turned into an ordinary exit; the gate script's EXIT trap restores the files
from the commit.
"""

# flake8: noqa: E501,B950
import ast
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys

HERE = pathlib.Path("docs/evidence/h201-ai-call-usage-record")
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))

CR = "ai_processor/call_record.py"
SV = "ai_processor/services.py"
MD = "ai_processor/models.py"
MG = "ai_processor/migrations/0006_aicallrecord.py"
BEHAVIOUR = "ai_processor.tests_call_record"
STATIC = "ai_processor.tests_call_record_static"
BOTH = [BEHAVIOUR, STATIC]
K = "keepdb"

# id, what is broken, file, text replaced, new text, test modules, database kind
MUTANTS = [
    (
        "Q1",
        "the swallow after a charge is narrowed to one exception class",
        CR,
        "            except Exception as recorder_error:\n                _count_failure(recorder_error, state)\n            return result\n",
        "            except ZeroDivisionError as recorder_error:\n                _count_failure(recorder_error, state)\n            return result\n",
        BOTH,
        K,
    ),
    (
        "Q2",
        "a recorder error on a call that raised replaces the original",
        CR,
        "                try:\n                    record(None, error)\n                except Exception as recorder_error:\n                    _count_failure(recorder_error, state)\n                raise\n",
        "                record(None, error)\n                raise\n",
        BOTH,
        K,
    ),
    (
        "Q3",
        "the insert has no savepoint of its own",
        CR,
        "    with transaction.atomic():\n        AICallRecord.objects.create(**fields)\n",
        "    AICallRecord.objects.create(**fields)\n",
        BOTH,
        K,
    ),
    (
        "Q4",
        "a second write is made INSIDE the charge transaction",
        SV,
        "            actual_cost = response.usage.total_tokens\n",
        '            actual_cost = response.usage.total_tokens\n            call_record.write_row(task_type="other", feature="other", outcome="charged")\n',
        BOTH,
        K,
    ),
    (
        "Q5",
        "the estimate is computed again by the recorder",
        CR,
        [
            "def _fields(arguments, state, result, error, started):\n",
            '        "estimate": _count(state.estimate),\n',
        ],
        [
            'def _recompute(state):\n    from ai_processor import services\n\n    return services.AIProcessor().estimate_total_token("", [], [])\n\n\ndef _fields(arguments, state, result, error, started):\n',
            '        "estimate": _count(_recompute(state)),\n',
        ],
        BOTH,
        K,
    ),
    (
        "Q6",
        "the failure log carries the exception message",
        CR,
        "        type(error).__name__,\n    )\n",
        "        str(error),\n    )\n",
        BOTH,
        K,
    ),
    (
        "Q7",
        "the switch is ignored",
        CR,
        '    return bool(getattr(settings, "AI_CALL_RECORD_ENABLED", True))\n',
        "    return True\n",
        BOTH,
        K,
    ),
    (
        "Q8",
        "the outcome is always charged",
        CR,
        "def _outcome(state, result, error):\n",
        "def _outcome(state, result, error):\n    return CallOutcome.CHARGED\n",
        BOTH,
        K,
    ),
    (
        "Q9",
        "the error class is the exception message",
        CR,
        "    if error is None:\n        return None\n    for cls in type(error).__mro__:",
        "    if error is None:\n        return None\n    return str(error)[:24]\n    for cls in type(error).__mro__:",
        BOTH,
        K,
    ),
    (
        "Q10",
        "the user's prompt text is stored in a column",
        CR,
        '        "model_asked": (arguments.get("override_model") or services.MAIN_MODEL)[:255],\n',
        '        "model_asked": str(arguments.get("user_prompt"))[:255],\n',
        BOTH,
        K,
    ),
    (
        "Q11",
        "the billed user is the caller, even a student",
        CR,
        "    billed = state.billed_user_id\n",
        '    billed = getattr(user, "id", None)\n',
        BOTH,
        K,
    ),
    (
        "Q12",
        "an absent cost is stored as zero",
        CR,
        '        "cost_usd": _cost(getattr(usage, "cost", None)),\n',
        '        "cost_usd": _cost(getattr(usage, "cost", 0)),\n',
        BOTH,
        K,
    ),
    (
        "Q13",
        "a count outside the column's range is not made None",
        CR,
        "    return number if 0 <= number <= _BIGINT_MAX else None\n",
        "    return number\n",
        BOTH,
        K,
    ),
    (
        "Q14",
        "the call state is never reset",
        CR,
        "        finally:\n            _CURRENT.reset(token)\n",
        "        finally:\n            pass\n",
        BOTH,
        K,
    ),
    (
        "Q15",
        "no record is written for a call that raised",
        CR,
        "                    record(None, error)\n                except Exception as recorder_error:\n                    _count_failure(recorder_error, state)\n                raise\n",
        "                    pass\n                except Exception as recorder_error:\n                    _count_failure(recorder_error, state)\n                raise\n",
        BOTH,
        K,
    ),
    (
        "Q16",
        "a call that raised comes out as a different exception",
        CR,
        "                except Exception as recorder_error:\n                    _count_failure(recorder_error, state)\n                raise\n",
        "                except Exception as recorder_error:\n                    _count_failure(recorder_error, state)\n                raise RuntimeError(str(error))\n",
        BOTH,
        K,
    ),
    (
        "Q17",
        "the time limit is swallowed after a charge",
        CR,
        "            except SoftTimeLimitExceeded:\n                raise\n            except Exception as recorder_error:",
        "            except Exception as recorder_error:",
        BOTH,
        K,
    ),
    (
        "Q18",
        "exit requests are swallowed after a charge",
        CR,
        "            except Exception as recorder_error:\n                _count_failure(recorder_error, state)\n            return result\n",
        "            except BaseException as recorder_error:\n                _count_failure(recorder_error, state)\n            return result\n",
        BOTH,
        K,
    ),
    (
        "Q19",
        "the charged credits are the prompt tokens",
        CR,
        '            numbers["total_tokens"] or 0 if outcome == CallOutcome.CHARGED else 0',
        '            numbers["prompt_tokens"] or 0 if outcome == CallOutcome.CHARGED else 0',
        BOTH,
        K,
    ),
    (
        "Q20",
        "grade_assignment is given a kind",
        CR,
        "KIND_BY_TASK_TYPE = {\n",
        'KIND_BY_TASK_TYPE = {\n    "grade_assignment": CallKind.FORMATTING.value,\n',
        BOTH,
        K,
    ),
    (
        "H1",
        "the estimate note is missing",
        SV,
        "        call_record.note_estimate(estimated_cost)\n",
        "",
        BOTH,
        K,
    ),
    (
        "H2",
        "the task id note is missing",
        SV,
        "        call_record.note_task_id(task_id)\n",
        "",
        BOTH,
        K,
    ),
    (
        "H3",
        "the response note is missing",
        SV,
        "        call_record.note_response(response)\n",
        "",
        BOTH,
        K,
    ),
    (
        "H4",
        "the billed note is missing",
        SV,
        "        call_record.note_billed(target_teacher)\n",
        "",
        BOTH,
        K,
    ),
    (
        "H5",
        "the decorator is missing",
        SV,
        "    @call_record.recorded_call\n    def execute_graded_task(",
        "    def execute_graded_task(",
        BOTH,
        K,
    ),
    (
        "M1",
        "a free-text column is added to the model",
        MD,
        "    processing_task_id = models.UUIDField(null=True, blank=True)\n",
        "    processing_task_id = models.UUIDField(null=True, blank=True)\n    error_message = models.TextField(null=True, blank=True)\n",
        [STATIC],
        K,
    ),
    (
        "M2",
        "the migration does more than create the table",
        MG,
        "    operations = [\n",
        '    operations = [\n        migrations.RunSQL("SELECT 1"),\n',
        [STATIC],
        K,
    ),
]

_SEVEN = [
    "test_a_refusal_at_the_gate_is_recorded_with_the_estimate_and_no_usage",
    "test_a_refusal_before_the_estimate_has_no_estimate",
    "test_a_provider_error_is_recorded_with_its_class_and_the_estimate",
    "test_a_cancellation_after_the_answer_keeps_the_usage_and_no_charge",
    "test_a_balance_that_changed_after_the_answer_is_recorded",
    "test_any_other_failure_of_the_charge_is_recorded_as_charge_failed",
]
_SWALLOW = [
    "test_a_failing_insert_of_any_database_kind_leaves_the_charge_and_return",
    "test_a_missing_table_is_survived_and_counted",
    "test_a_failure_is_logged_with_ids_and_the_class_never_the_message",
    "test_reading_the_reply_failing_is_swallowed_too",
    "test_a_failing_insert_inside_a_callers_transaction_leaves_it_usable",
    "test_a_database_error_from_the_real_insert_leaves_the_transaction_usable",
    "test_the_charge_is_the_same_with_recording_on_off_and_failing",
]
_STATIC_HOOKS = [
    "test_the_four_notes_are_each_called_once_beside_their_line",
    "test_the_function_makes_no_other_call_into_the_module",
    "test_the_hooks_are_five_lines_in_all",
]

#: The failing tests each mutant must be caught by (written before any run):
#: every test reckoned to fail under it. Overlaps are listed, not hidden.
EXPECTED = {
    "Q1": _SWALLOW[:],
    "Q2": [
        "test_a_recorder_error_in_a_failure_path_never_replaces_the_original",
        "test_the_time_limit_is_not_swallowed_after_a_charge_but_not_replacing_an_error",
    ],
    "Q3": [
        "test_a_database_error_from_the_real_insert_leaves_the_transaction_usable",
        "test_the_recorder_adds_one_insert_in_one_savepoint_and_reads_nothing",
        "test_a_missing_table_is_survived_and_counted",
    ],
    "Q4": [
        "test_the_function_makes_no_other_call_into_the_module",
        "test_the_hooks_are_five_lines_in_all",
        "test_a_charged_call_leaves_one_row_with_what_was_known",
    ],
    "Q5": [
        "test_the_estimate_is_computed_once_per_call_with_recording_on",
        "test_a_charged_call_leaves_one_row_with_what_was_known",
    ],
    "Q6": ["test_a_failure_is_logged_with_ids_and_the_class_never_the_message"],
    "Q7": [
        "test_off_writes_no_row_and_runs_no_query",
        "test_the_charge_is_the_same_with_recording_on_off_and_failing",
        "test_the_recorder_adds_one_insert_in_one_savepoint_and_reads_nothing",
    ],
    "Q8": ["test_a_super_admin_call_is_recorded_as_unmetered"] + _SEVEN,
    "Q9": [
        "test_a_provider_error_is_recorded_with_its_class_and_the_estimate",
        "test_no_text_from_the_call_reaches_a_column_or_a_log",
    ],
    "Q10": [
        "test_no_text_from_the_call_reaches_a_column_or_a_log",
        "test_a_charged_call_leaves_one_row_with_what_was_known",
        "test_the_model_asked_for_is_the_override_when_there_is_one",
    ],
    "Q11": ["test_the_row_carries_the_teacher_and_never_the_student"],
    "Q12": ["test_a_field_the_reply_does_not_carry_is_null_never_zero"],
    "Q13": ["test_a_weird_value_in_the_reply_becomes_null_and_the_call_goes_on"],
    "Q14": ["test_a_call_that_noted_nothing_carries_nothing_over_from_the_last"],
    "Q15": _SEVEN[:],
    "Q16": [
        "test_the_provider_exception_is_the_very_object_with_its_traceback",
        "test_each_exception_class_the_callers_branch_on_comes_out_unchanged",
        "test_a_provider_error_is_recorded_with_its_class_and_the_estimate",
        "test_a_cancellation_after_the_answer_keeps_the_usage_and_no_charge",
        "test_a_balance_that_changed_after_the_answer_is_recorded",
        "test_a_refusal_at_the_gate_is_recorded_with_the_estimate_and_no_usage",
        "test_a_refusal_before_the_estimate_has_no_estimate",
        "test_a_recorder_error_in_a_failure_path_never_replaces_the_original",
        "test_no_text_from_the_call_reaches_a_column_or_a_log",
        "test_the_gate_and_access_refusals_are_the_same_with_recording_off",
    ],
    "Q17": [
        "test_the_time_limit_is_not_swallowed_after_a_charge_but_not_replacing_an_error"
    ],
    "Q18": ["test_exit_requests_are_never_swallowed"],
    "Q19": [
        "test_the_row_reconciles_with_the_charge",
        "test_a_charged_call_leaves_one_row_with_what_was_known",
    ],
    "Q20": [
        "test_each_site_is_marked_exactly_when_its_task_type_proves_a_kind",
        "test_a_kind_is_given_only_to_a_task_type_every_site_of_which_agrees",
        "test_the_kind_is_set_only_where_the_task_type_proves_it",
    ],
    "H1": _STATIC_HOOKS
    + [
        "test_a_charged_call_leaves_one_row_with_what_was_known",
        "test_a_refusal_at_the_gate_is_recorded_with_the_estimate_and_no_usage",
    ],
    "H2": _STATIC_HOOKS
    + [
        "test_the_row_reconciles_with_the_charge",
        "test_a_provider_error_is_recorded_with_its_class_and_the_estimate",
    ],
    "H3": _STATIC_HOOKS
    + [
        "test_a_cancellation_after_the_answer_keeps_the_usage_and_no_charge",
        "test_a_balance_that_changed_after_the_answer_is_recorded",
        "test_any_other_failure_of_the_charge_is_recorded_as_charge_failed",
    ],
    "H4": _STATIC_HOOKS + ["test_the_row_carries_the_teacher_and_never_the_student"],
    "H5": [
        "test_execute_graded_task_is_decorated_once_with_recorded_call",
        "test_a_charged_call_leaves_one_row_with_what_was_known",
    ],
    "M1": [
        "test_the_columns_are_exactly_these_by_name_and_type",
        "test_no_free_text_no_json_no_foreign_key",
    ],
    "M2": ["test_it_is_one_create_model_and_nothing_else"],
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
