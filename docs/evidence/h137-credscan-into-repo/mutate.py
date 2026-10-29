"""H-137: deliberate breaks of scripts/credscan.py, and the tests that must catch each.

Each mutant is applied, AutoGrader.tests_credscan is run, the failing tests
are recorded, the file is restored. Judged three ways (rule 18):
  SURVIVED  the inner run exits 0;
  KILLED    non-zero exit, the run's own "Ran" line, no test module that
            failed to load, and the failing tests are EXACTLY the set named
            in EXPECTED for that mutant;
  BROKEN    anything else, a differing set included. Never counted as a kill.
EXPECTED was written before any run of a mutant, by reading.

Rule 17: every inner run has PYTHONDONTWRITEBYTECODE=1, and the tool's
__pycache__ is deleted before each mutant and after each restore.
Rule 18: each inner run writes straight to its own file
(mutant_logs/<name>.txt), stdin from the null device; nothing is piped.
Each mutant is restored in a `finally` block; SIGTERM is turned into an
ordinary exit so that block runs. The tests use no database.
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

HERE = pathlib.Path("docs/evidence/h137-credscan-into-repo")
TOOL = "scripts/credscan.py"
MODULE = "AutoGrader.tests_credscan"
LISTED = '            or kind in ("url", "archive", "longline", "cut")\n'

#: (name, what, old, new)
MUTANTS = [
    (
        "L1",
        "a long line is passed over, as before",
        "            scan_long_line(name, lineno, line, rows, values)\n        else:\n",
        "            continue\n        else:\n",
    ),
    (
        "L2",
        "a long line is not counted",
        '    rows[("longline", name, "scanned-around-its-words", 0)] += 1\n',
        "    pass\n",
    ),
    (
        "L3",
        "the report does not list long lines",
        LISTED,
        '            or kind in ("url", "archive", "cut")\n',
    ),
    (
        "L4",
        "a second word in a name already tried is not passed over",
        "        if word.start() < tried_to:\n            continue\n",
        "        if False:\n            continue\n",
    ),
    (
        "L5",
        "a line of exactly the limit is called long",
        "        if len(line) > LONG_LINE:\n",
        "        if len(line) >= LONG_LINE:\n",
    ),
    (
        "L6",
        "addresses are not looked for on a long line",
        "        if m:\n            record_address(name, lineno, m.group(1), rows, values)\n        at = line.find",
        "        at = line.find",
    ),
    (
        "L7",
        "line numbers start at 0",
        'enumerate(data.split(b"\\n"), 1)',
        'enumerate(data.split(b"\\n"), 0)',
    ),
    (
        "L8",
        "a name is not followed back to its start",
        "        while (\n"
        "            start > 0\n"
        "            and word.start() - start < NAME_BEFORE\n"
        "            and line[start - 1] in NAME_BYTES\n"
        "        ):\n"
        "            start -= 1\n",
        "",
    ),
    (
        "L9",
        "the pattern is searched for over the stretch before the name too (the fault Verifier 2 found)",
        "        m = ASSIGN.match(line, start, stop)\n",
        "        m = ASSIGN.search(line, max(0, start - NAME_BEFORE), stop)\n",
    ),
    (
        "L10",
        "a name is not followed forward to its end",
        "        while (\n"
        "            end < len(line)\n"
        "            and end - word.end() < NAME_BEFORE\n"
        "            and line[end] in NAME_BYTES\n"
        "        ):\n"
        "            end += 1\n",
        "",
    ),
    (
        "C1",
        "a bare value cut off by the end of what is read is recorded as the part seen",
        "        if cut:\n",
        "        if False:\n",
    ),
    (
        "C2",
        "a bare value that ends where the reading ends is called cut",
        "            and stop < len(line)\n            and line[stop] not in VALUE_ENDS\n",
        "            and stop < len(line)\n",
    ),
    (
        "C3",
        "the report does not list cut values",
        LISTED,
        '            or kind in ("url", "archive", "longline")\n',
    ),
    (
        "N1",
        "an archive nested too deep is not reported",
        '            rows[("archive", name, "NOT-OPENED:nested-too-deep", 0)] += 1\n',
        "            pass\n",
    ),
    ("N2", "four archives deep are opened", "MAX_DEPTH = 3\n", "MAX_DEPTH = 4\n"),
    ("N3", "only two archives deep are opened", "MAX_DEPTH = 3\n", "MAX_DEPTH = 2\n"),
    (
        "N4",
        "the report does not list archive rows",
        LISTED,
        '            or kind in ("url", "longline", "cut")\n',
    ),
    (
        "B1",
        "a binary file is not counted",
        '        rows[("binary", name, "NOT-READ", 0)] += 1\n',
        "        pass\n",
    ),
    (
        "B2",
        "the binary probe reads 8192 bytes",
        "BINARY_PROBE = 4096\n",
        "BINARY_PROBE = 8192\n",
    ),
    (
        "B3",
        "the report has no line for files not read",
        "    out.write(\n"
        '        f"# not read: {unread} file(s) with a NUL byte in the first {BINARY_PROBE} bytes\\n"\n'
        "    )\n",
        "",
    ),
    (
        "M1",
        "the hit list holds the value, not the name",
        'HITS.append((name, lineno, m.group(1).decode("ascii", "replace"), len(v), sh))',
        'HITS.append((name, lineno, v.decode("ascii", "replace"), len(v), sh))',
    ),
    (
        "F1",
        "no file is taken for a test file",
        '    n = name.lower()\n    base = n.split("!")[0].rsplit("/", 1)[-1]\n',
        '    n = ""\n    base = ""\n',
    ),
    (
        "F2",
        "the report does not list addresses by default",
        LISTED,
        '            or kind in ("archive", "longline", "cut")\n',
    ),
    (
        "F3",
        "a value is not compared with its encoded and decoded forms",
        "        for f in {\n"
        "            v,\n"
        "            urllib.parse.unquote_to_bytes(v),\n"
        '            urllib.parse.quote_from_bytes(v, safe="").encode(),\n'
        "        }:\n",
        "        for f in {v}:\n",
    ),
    (
        "A1",
        "an .xz file is not opened",
        '        if low.endswith(".xz"):\n',
        '        if low.endswith(".xz-off"):\n',
    ),
    (
        "A2",
        "an opened .gz is also given an archive row",
        'scan(name + "!(gunzipped)", gzip.decompress(data), rows, values, depth + 1)\n            return True\n',
        'scan(name + "!(gunzipped)", gzip.decompress(data), rows, values, depth + 1)\n'
        '            rows[("archive", name, "OPENED", 0)] += 1\n            return True\n',
    ),
]

FAR = "test_a_pattern_far_along_a_long_line_is_found"
START = "test_a_pattern_at_the_start_of_a_long_line_is_found"
RUN = "test_a_pattern_after_one_long_unbroken_run_is_found"
ONCE = "test_one_hit_is_counted_once_however_many_words_surround_it"
LINENO = "test_the_hit_on_a_long_line_carries_its_line_number"
SAID = "test_a_long_line_is_said_to_be_long_in_the_report"
LIMIT = "test_a_line_of_exactly_the_limit_is_not_called_long"
THREE = "test_three_archives_deep_is_opened"
FOURTH = "test_a_fourth_archive_is_reported_as_not_opened"
DEFAULT = "test_the_archive_not_opened_is_listed_in_the_default_report"
EARLY = "test_a_file_with_an_early_nul_byte_is_counted_as_not_read"
NAMED = "test_the_files_not_read_are_named_when_every_row_is_asked_for"
LATE = "test_a_nul_byte_after_the_first_4096_bytes_does_not_stop_the_scan"
ZERO = "test_a_report_with_nothing_unread_says_zero"
PLANTED = "test_the_planted_patterns_are_found_in_every_form"
CLEAN = "test_a_clean_body_gives_nothing_in_every_form"
BROKEN_XZ = "test_a_broken_xz_is_reported_not_skipped"
IN_ROWS = "test_the_value_is_never_in_the_rows_or_the_hit_list"
IN_REPORT = "test_the_value_is_never_in_the_report"
WHERE = "test_the_hit_list_names_the_file_and_the_line"
TEST_FILE = "test_a_literal_assignment_in_a_test_file_is_counted_but_not_listed"
ADDRESS = "test_an_address_in_a_test_file_is_listed"
TWO_WAYS = "test_one_value_written_two_ways_is_reported_as_a_group"
WHOLE = "test_a_bare_value_far_after_an_earlier_word_is_found_whole"
SAME = "test_a_long_line_gives_what_the_same_text_gives_on_a_short_line"
CLOSE = "test_two_assignments_close_together_are_each_found_once"
CUT = "test_a_bare_value_cut_by_the_end_of_the_stretch_is_reported_as_cut"
EXACT = "test_a_bare_value_that_ends_where_the_stretch_ends_is_whole"

#: The exact set of failing tests per mutant, written before any run.
EXPECTED = {
    "L1": {FAR, START, RUN, ONCE, LINENO, SAID, WHOLE, SAME, CLOSE, CUT, EXACT},
    "L2": {SAID},
    "L3": {SAID},
    "L4": {ONCE},
    "L5": {LIMIT},
    "L6": {FAR, START},
    "L7": {WHERE, LINENO, WHOLE, CLOSE, CUT},
    "L8": {FAR, START, RUN, LINENO, WHOLE, SAME, CLOSE, CUT, EXACT},
    "L9": {ONCE, WHOLE, SAME},
    "L10": {ONCE, CUT, EXACT},
    "C1": {CUT},
    "C2": {EXACT},
    "C3": {CUT},
    "N1": {FOURTH, DEFAULT},
    "N2": {FOURTH, DEFAULT},
    "N3": {THREE, FOURTH, DEFAULT},
    "N4": {DEFAULT},
    "B1": {EARLY, NAMED},
    "B2": {EARLY, LATE, ZERO},
    "B3": {EARLY, ZERO},
    "M1": {IN_ROWS, IN_REPORT},
    "F1": {TEST_FILE},
    "F2": {ADDRESS},
    "F3": {TWO_WAYS},
    "A1": {PLANTED, BROKEN_XZ},
    "A2": {PLANTED, CLEAN},
}

#: A test module that could not be loaded.
LOAD_FAILURE = "unittest.loader._FailedTest"


def clear_pycache():
    shutil.rmtree(pathlib.Path(TOOL).parent / "__pycache__", ignore_errors=True)


def judge(mid, returncode, text):
    """(status, the run's "Ran" line, the failing tests' names)."""
    failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", text, re.M)))
    ran = re.findall(r"^Ran \d+ tests? in .*$", text, re.M)
    ran_line = ran[-1] if ran else None
    if returncode == 0:
        status = "SURVIVED"
    elif ran_line and LOAD_FAILURE not in text and set(failed) == EXPECTED[mid]:
        status = "KILLED"
    else:
        status = "BROKEN"
    return status, ran_line, failed


def _exit_on_sigterm(signum, _frame):
    raise SystemExit(128 + signum)


def main():
    signal.signal(signal.SIGTERM, _exit_on_sigterm)
    assert [m[0] for m in MUTANTS] == list(EXPECTED), "EXPECTED names every mutant"
    original = open(TOOL).read()
    for mid, _what, old, new in MUTANTS:
        assert (
            original.count(old) == 1
        ), f"{mid}: anchor found {original.count(old)} times"
        ast.parse(original.replace(old, new, 1))
    if "--check" in sys.argv:
        print(len(MUTANTS), "mutants: every anchor found once, all parse")
        print(" ".join(m[0] for m in MUTANTS))
        return
    tag = os.environ.get("MUT_TAG", "")
    logs = HERE / ("mutant_logs_" + tag if tag else "mutant_logs")
    logs.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    settings = os.environ.get("MUT_SETTINGS", "settings_worktree")
    results = {}
    for mid, what, old, new in MUTANTS:
        log = logs / f"{mid}.txt"
        try:
            clear_pycache()
            open(TOOL, "w").write(original.replace(old, new, 1))
            with open(log, "w") as out, open(os.devnull) as devnull:
                p = subprocess.run(
                    [
                        sys.executable,
                        "manage.py",
                        "test",
                        MODULE,
                        f"--settings={settings}",
                        "--noinput",
                        "--verbosity",
                        "2",
                    ],
                    stdin=devnull,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    env=env,
                    timeout=600,
                )
        finally:
            open(TOOL, "w").write(original)
            clear_pycache()
        status, ran_line, failed = judge(
            mid, p.returncode, log.read_text(errors="replace")
        )
        results[mid] = {
            "what": what,
            "status": status,
            "exit": p.returncode,
            "ran_line": ran_line,
            "expected": sorted(EXPECTED[mid]),
            "failing_tests": failed,
        }
        print(mid, results[mid], flush=True)
    with open(
        HERE
        / ("mutation_results_" + tag + ".json" if tag else "mutation_results.json"),
        "w",
    ) as f:
        json.dump(results, f, indent=2)
        f.write("\n")
    for status in ("KILLED", "SURVIVED", "BROKEN"):
        print(f"{status}:", [k for k, v in results.items() if v["status"] == status])
    sys.exit(0 if all(v["status"] == "KILLED" for v in results.values()) else 1)


if __name__ == "__main__":
    main()
