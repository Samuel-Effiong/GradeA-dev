# flake8: noqa: E501,B950
"""H-264 a numbered paper for another assignment is split back to its own questions: deliberate breaks, and the tests that must catch each.

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
the files from the commit.

Declared gaps (written before any run, not papered over with a mutant that
cannot be killed):

1. The `number == own` skip in _piled_answers (the pile's own number is not
   "named"): for the written inputs, the pile is always question 1 and
   section 1 is present. Removing `number == own` would still not name it
   because the pile itself is ANSWERED, and the code requires
   `answer_status == NOT_FOUND_IN_DOCUMENT`. So the mutant survives.

2. The `int(match.group(1)) in numbers` filter in _numbered_sections (only
   numbers from the assignment count): for the written inputs with 75
   questions, every small number IS in the assignment. A paper with numbers
   outside the assignment would need questions=4 with sections 5 and 6, but
   the test test_numbers_outside_the_assignment_are_not_counted does this
   and expects no pile — yet the check is redundant because `len(markers)
   < 2` already rejects a single marker, and the test's sections (5, 6) are
   outside the assignment's 4 numbers, leaving 0 markers. Removing the
   `in numbers` filter would add markers for 1, 5, 6 (1 is in the
   assignment, 5 and 6 are not, but with the filter removed all match):
   that gives 3 markers, a pile. The test would fail — so this is NOT a gap;
   the mutant can be killed. Included as M2b below.

3. The `_retranscription_evidence` ordering (fragment before pile_line): no
   test checks which line comes first; a mutant swapping the order survives.
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

HERE = pathlib.Path("docs/evidence/h264-numbered-offtopic-answers")
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))

SVC = "ai_processor/services.py"
TESTS = ["ai_processor.tests_h264_numbered_offtopic_answers"]
K = "keepdb"


# id, what is broken, file, text(s) replaced, new text(s), tests, database kind
MUTANTS = [
    # --- _numbered_sections: the patterns and their guards ---
    (
        "N1",
        "_LABELLED_SECTION loses case insensitivity (QUESTION 2 is not matched)",
        SVC,
        '|$)", re.IGNORECASE\n)',
        '|$)"\n)',
        TESTS,
        K,
    ),
    (
        "N2",
        "_BARE_SECTION matches decimals (3.5 looks like section 3)",
        SVC,
        r'r"^(\d{1,3})[.)](?!\d)"',
        r'r"^(\d{1,3})[.)]"',
        TESTS,
        K,
    ),
    (
        "N2b",
        "_numbered_sections counts numbers outside the assignment",
        SVC,
        " in numbers:\n",
        ":\n",
        TESTS,
        K,
    ),
    (
        "N3",
        "_in_order keeps all markers (out-of-order numbers are piles)",
        SVC,
        "if not kept or number > kept[-1][1]:",
        "if True:",
        TESTS,
        K,
    ),
    # --- _piled_answers: which entries are piles, which are named ---
    (
        "P1",
        "FLAG_TYPE_MISMATCH is no longer a pile flag",
        SVC,
        "            FLAG_OFF_TOPIC,\n            FLAG_TYPE_MISMATCH,\n        ):\n            continue",
        "            FLAG_OFF_TOPIC,\n        ):\n            continue",
        TESTS,
        K,
    ),
    (
        "P2",
        "a named question with a flag is named by the pile (bypasses H-253)",
        SVC,
        "                or answer_flag(other) != FLAG_NONE\n",
        "",
        TESTS,
        K,
    ),
    (
        "P3",
        "PILE_QUOTE_LENGTH is 0 (the note quotes nothing)",
        SVC,
        "PILE_QUOTE_LENGTH = 80\n",
        "PILE_QUOTE_LENGTH = 0\n",
        TESTS,
        K,
    ),
    (
        "P4",
        "the listed join uses spaces instead of commas",
        SVC,
        'listed = ", ".join(str(number) for number, _ in sections)',
        'listed = " ".join(str(number) for number, _ in sections)',
        TESTS,
        K,
    ),
    # --- _retranscribe_found_answers: targets and replacement ---
    (
        "T1",
        "piles are not targets (only H-253 route remains)",
        SVC,
        "                id(entry) in pile_lines\n",
        "                False\n",
        TESTS,
        K,
    ),
    (
        "T2",
        "issuperset becomes issubset (the pile is replaced even when not all sections came back)",
        SVC,
        "returned_ids.issuperset(\n",
        "returned_ids.issubset(\n",
        TESTS,
        K,
    ),
    (
        "T3",
        "SPLIT_ANSWER_NOTE says 'second' instead of 'first'",
        SVC,
        'SPLIT_ANSWER_NOTE = (\n    "The first reading put several',
        'SPLIT_ANSWER_NOTE = (\n    "The second reading put several',
        TESTS,
        K,
    ),
    (
        "T4",
        "_recovered_answer ignores the note argument (always RECOVERED_ANSWER_NOTE)",
        SVC,
        "note if note is not None else RECOVERED_ANSWER_NOTE\n",
        "RECOVERED_ANSWER_NOTE\n",
        TESTS,
        K,
    ),
    # --- _verify_blank_answers: the cap ---
    (
        "C1",
        "cap <= 0 does not return early (cap=0 still runs the verification)",
        SVC,
        "        if cap <= 0:\n            return answers\n",
        "",
        TESTS,
        K,
    ),
    (
        "C2",
        "blanks[:cap] becomes blanks[cap:] (the highest-numbered are re-checked)",
        SVC,
        "            blanks = blanks[:cap]\n",
        "            blanks = blanks[cap:]\n",
        TESTS,
        K,
    ),
    (
        "C3",
        "the log says cap instead of cap + unchecked (total count is wrong)",
        SVC,
        "                cap + unchecked,\n                cap,\n                cap,\n",
        "                cap,\n                cap,\n                cap,\n",
        TESTS,
        K,
    ),
    (
        "C4",
        "findings for questions not asked about are applied anyway",
        SVC,
        "            if key in asked:\n                found_by_key[key] = finding\n",
        "            found_by_key[key] = finding\n",
        TESTS,
        K,
    ),
]


# Test name aliases.
_NUMBERING = "test_each_way_of_numbering_at_the_start_of_a_paragraph_or_line"
_NO_SECTIONS = "test_writing_with_no_numbered_sections_changes_nothing"
_OUTSIDE = "test_numbers_outside_the_assignment_are_not_counted"
_OUT_OF_ORDER = "test_numbers_out_of_order_are_not_a_pile"
_WRONG_FORM = "test_a_pile_in_the_wrong_form_counts"
_FLAG_H253 = "test_a_named_question_with_a_flag_is_left_to_h253"
_NOTE_WHERE = "test_the_note_says_where_each_answer_was_put"
_EACH_NUMBER = "test_each_answer_goes_under_its_own_number"
_BETA_REPLY = "test_the_beta_reply_ends_with_six_answers_and_the_upload_flag"
_FIRST_12 = "test_the_beta_paper_has_its_first_12_blanks_re_checked"
_REST_LOGGED = "test_the_rest_are_logged_as_not_checked"
_NOT_ASKED = "test_a_finding_for_a_question_not_asked_about_is_not_applied"
_CAP_0 = "test_a_cap_of_0_checks_nothing"

# Fragment aliases: each must appear in every FAIL/ERROR block of the named test.
NOT_CALLED = "to not have been called"
COUNT_01 = "0 != 1"
NOT_IN = "not found in"
UNEXPECTED_IN = "unexpectedly found in"

#: Written before any run: {mutant: {test: the fragment its every block holds}},
#: every test reckoned to fail under it, traced against every assertion of the
#: module. The control too.
EXPECTED = {
    # N1: "QUESTION 2" no longer matches the labelled pattern (case dropped).
    # The 9th subTest in _NUMBERING has mixed case; without IGNORECASE the
    # pile is not detected and the mock is not called: call_count 0 != 1.
    "N1": {_NUMBERING: COUNT_01},
    # N2: "3.5" now matches as section 3; decimal numbers in prose become piles.
    # test_writing_with_no_numbered_sections_changes_nothing has "3.5 is more
    # than 2.5", which would parse as sections 3 and 4 (both in 1-75);
    # assert_untouched calls assert_not_called on the mock.
    "N2": {_NO_SECTIONS: NOT_CALLED},
    # N2b: numbers outside the assignment count; test_numbers_outside_the_
    # assignment has questions=4 with sections 1, 5, 6 — all three match,
    # giving a pile; assert_untouched calls assert_not_called.
    "N2b": {_OUTSIDE: NOT_CALLED},
    # N3: _in_order keeps all markers regardless of order; "<p>2. b</p><p>1.
    # a</p>" now gives two markers (2 then 1), making a pile; assert_untouched
    # calls assert_not_called.
    "N3": {_OUT_OF_ORDER: NOT_CALLED},
    # P1: FLAG_TYPE_MISMATCH entries are not piles; the test creates a pile
    # with FLAG_TYPE_MISMATCH and expects call_count == 1; mock is not called.
    "P1": {_WRONG_FORM: COUNT_01},
    # P2: a NOT_FOUND entry with a flag IS named (normally skipped to H-253);
    # test sets FLAG_OFF_TOPIC on Q2 and expects only "[2]" asked about via
    # H-253, but now Q1 is also a pile naming Q2 → "[1, 2]"; "[2]" not found.
    "P2": {_FLAG_H253: NOT_IN},
    # P3: _quoted_start returns "..." for every text; the note check expects
    # the full quote of Q2's answer.
    "P3": {_NOTE_WHERE: NOT_IN},
    # P4: the listed numbers use spaces "1 2 3 4 5 6" instead of commas
    # "1, 2, 3, 4, 5, 6"; the note check expects commas.
    "P4": {_NOTE_WHERE: NOT_IN},
    # T1: pile_lines entries are not targets, so no pile or named question is
    # sent to the second reading; no H-253 targets exist either → 0 targets →
    # mock not called → call_count 0 != 1.
    "T1": {_EACH_NUMBER: COUNT_01},
    # T2: issubset instead of issuperset; returned_ids includes the pile's own
    # id, which is NOT in named_by_pile's values list, so issubset is False
    # even when all sections came back; pile not replaced, answers[0] still
    # has the pile HTML. The whole-reading test fails on the answer_html list.
    "T2": {_BETA_REPLY: "Question 1:"},
    # T3: the note says "The second reading" not "The first reading"; the test
    # asserts SPLIT_NOTE_START ("The first reading put several...") is in the
    # pile entry's flag_detail.
    "T3": {_EACH_NUMBER: NOT_IN},
    # T4: _recovered_answer always uses RECOVERED_ANSWER_NOTE ("The first
    # reading missed this answer...") even when called with SPLIT_ANSWER_NOTE;
    # SPLIT_NOTE_START is not in RECOVERED_ANSWER_NOTE.
    "T4": {_EACH_NUMBER: NOT_IN},
    # C1: cap=0 does not short-circuit; blanks[:0] = [] → empty question list
    # → the mock is called (images exist) → assert_not_called fails.
    "C1": {_CAP_0: NOT_CALLED},
    # C2: blanks[cap:] re-checks the HIGHEST-numbered, not the lowest; the
    # test expects questions [2..13], gets [14..75]; assertIn fails.
    "C2": {_FIRST_12: NOT_IN},
    # C3: the log says "12 blank(s) exceeds the cap of 12" instead of "74
    # blank(s)"; the test's any() condition matches neither.
    "C3": {_REST_LOGGED: "12 blank(s) exceeds"},
    # C4: findings for any question_number are applied; Q40 was not asked about
    # but its finding IS applied → blank_verification key present.
    "C4": {_NOT_ASKED: UNEXPECTED_IN},
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
