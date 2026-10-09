# H-158, the second form: a paper's own numbers are kept when they are distinct positive integers

Read this with `EVIDENCE.md` (the first form, gated at `7f5d1743`) and
`DESIGN_NOTE.md`. **`EVIDENCE.md` and its commit `72cd7ae2` are SUPERSEDED
by this file wherever they differ:** the first form numbered every paper
1..N; the second keeps the numbers a paper already has when they are
distinct positive integers. The gates on `7f5d1743` stay as history. **The
code the gates below ran on is `38b4c6b6`.** Docs-only commits follow it.

## What a teacher sees (two plain sentences, for the package)
A paper the AI reads can no longer lose a question because the AI gave two
questions the same number: before this, a three-question paper numbered
1, 1, 2 was graded as two questions out of 20 instead of three out of 30.
A paper that already has sound numbers of its own (a paper that starts at
5, for example) keeps them; only a paper with a repeated number or a
number that is not a whole number above zero is numbered 1, 2, 3 in the
order given.

## The rulings that made the second form
- Senior Manager, 2026-10-07 19:32: renumber only when the numbers are NOT
  distinct positive integers. The paid check is not widened: one paper,
  one run.
- Verifier 2's question that started it: a paper numbered 5 to 10 works
  today (answer extraction relabels the model's answers to those numbers),
  so numbering it 1..N would have been a change nobody asked for.

## What changed since the first form
| Commit | What |
|---|---|
| `1c942820` | tests (red): numbers already distinct positive integers are kept |
| `a04f3908` | the change: `_positive_integer` and the keep-or-number rule in `number_questions_in_order` |
| `9f882c18` | runner N19 to N23 |
| `22e1559c` | tests (red): a string of very many digits is no number; a nine-digit string is |
| `e59894de` | the fix: a number sent as text may have at most 9 digits |
| `38b4c6b6` | runner N24; N23 re-anchored. **The tip the gates ran on.** |

**The fault found late, told:** Python refuses `int()` of a string of more
than 4300 digits (ValueError, seen by calling it). The model's reply is
free text, so such a value could have made the save fail after the paid
call. The string branch now takes at most nine digits; a longer one is no
number, and the paper is numbered.
(Nine is a bound I chose, far under 4300; I did not look for a smaller natural limit.)

## Facts about the form, stated
- A paper whose numbers are distinct positive integers is kept as it is,
  in the order given, even when out of order (3, 1, 2) or starting at 5.
  Digit strings count ("2" is 2); `2.0`, `"2a"`, `0`, negatives, true and
  null do not.
- **A label such as "1(a)" can never be stored as a question number:**
  the serializer's field is an integer, so a paper with such labels is
  always numbered 1..N. What the model wrote is kept as text (at most 32
  characters) in the stored AI copy under `model_question_numbers`.
- **Sectioned papers (Section A 1, Section B 1)** are stored 1..N while a
  student's sheet may say "B 1". The answer reader maps by number, then
  position, then content, so whether that student's answer reaches the
  right question rests on the model. No test of ours can show it without
  a paid call. H-171, one paper, one run, is planned
  (`SECTIONED_PAPER_CHECK_PLAN.md`) and NOT yet run.
- Papers of 5 to 10 questions with kept numbers: answer extraction
  relabels the model's answers to the kept numbers (the Verifier's point).

## The gates on `38b4c6b6`, 2026-10-08
All on the Release Engineer's grants: the chain "GO H-158 chain", the
regression "GO c_h158"; one outer `systemd-inhibit` around each script
(rule 16, revised); 6G cap, timeouts, own databases, rules 17 and 18. The
expected failing sets were written before any run
(`second_form_38b4c6b6/expected_kills.py.txt`, `7db5e23e1b89cb6e`).

| Step | What | Result |
|---|---|---|
| (r) | test module at `38b4c6b6` on the code of `7be08a58` | 07:10:05 to 07:10:27, Ran 42 tests, FAILED (failures=14, errors=27): 33 failing as written |
| (r2) | at `1c942820`, the keep-distinct tests before the change | 07:10:28 to 07:10:54, Ran 40 tests, FAILED (failures=9): the 9 written |
| (r3) | at `22e1559c`, the digit tests before the fix | 07:10:54 to 07:11:22, Ran 42 tests, FAILED (errors=1): exactly the 1 written |
| (a) | the module, the 19 touched-path modules, the guard list, the cache and migration-safety modules: 45 labels | 07:11:22 to 07:15:19, Ran 802 tests in 213.367s, OK; no FAIL/ERROR line |
| (b) | 24 mutants, each restored and verified | 07:15:19 to 07:18:39, baseline green, **24/24 killed**, restores verified |
| expected-sets check | each mutant's failing set against the written one | **STOPPED the chain: 5 of 24 differ** (below) |
| (c) | `c_h158.sh`: AutoGrader, assignments, students, billing, `--parallel 2` | 07:35:09 to 07:44:01, **Ran 3879 tests in 500.306s, OK (skipped=16)**, exit 0, stalled=0, 0 FAIL/ERROR lines; raw log sha `d7481e8dce146bcf` (the Release Engineer read the same) |

Load 2.9 at the chain start, 2.7 to 3.9 through it, 2.6 at the start of
(c) and 4.04 at its end. No kill looks like a timeout: each mutant's run
took 7 to 10 s and ended with a FAILED line (`battery_38b4c6b6.tar.gz`,
`results.tsv`).

### The five mutants that failed more than I wrote
19 of 24 failing sets were exactly as written. Five failed ONE or TWO
tests MORE than written (none fewer; none survived):

| Mutant | Extra failing test(s) | Why (from the code, not a re-run) |
|---|---|---|
| N6 every number counts as changed | the very-many-digits test | it asserts `changed == 1` for [1, huge, 3]; the mutant counts 3 |
| N8 kept text not bounded | the same | it asserts the kept text of the huge value is its first 32 characters; the mutant keeps all 5000 |
| N20 the "not a number" check removed | the same, and the long-number-kept-in-part test | the huge value and the 99-nines-plus-"a" value are no numbers, but the paper is kept as it is: no 1, 2, 3 numbering; no `kept["2"]` (keyed "None") |
| N21 keep-as-it-is branch off | the nine-digit test | every paper is numbered, so the second question is 2, not "999999999" |
| N23 digit-string branch off | the nine-digit test | "999999999" reads as no number, the paper is numbered: the same failure |

**Why I wrote them short:** the sets were written before the two digit
tests existed, and I did not re-read every test against every mutant after
adding them (the lesson already on file: a late test is re-read against
EVERY mutant, controls included). The Senior Manager ruled (2026-10-08,
07:13) that the chain stands as the gate and (b) is not re-run; Verifier 2
reads the five reasons and may ask for these five mutants alone to be
re-run. The reasons are in `CORRECTED_SETS_made_after_the_run.md`, its own
commit, made AFTER the run; the written file and the run's raw results are
unchanged. For the other 19 mutants the run is itself the reading: the two
digit tests did not fail there, as written.

## The two late tests, read against the real value (made after the run)
- **The very-many-digits test:** a 5000-digit string as the second
  question's number. Inspected: the numbered list's numbers and texts
  (read as `IN_ORDER`, non-empty), the kept dict (its three keys read),
  `changed`. Real value of `changed`: 1. Red under N6, N8, N20, N24.
- **The nine-digit test:** "999999999" as the second number. Inspected: the
  numbers of the returned list ([1, "999999999", 3], the string stays a
  string), the kept dict (key "999999999"), `changed` (0). Red under N21
  and N23. Its negative form (the string is NOT turned into 2) is decided
  by the exact list equality, which has three items.

## Limits, stated
- Not shown by a run: the sectioned-paper case (H-171, planned, one paper,
  one run, with the Release Engineer's grant and its own quiet).
- A question's text that refers to another by number is not rewritten.
- The key is kept on the three extraction paths only.
- H-172 (LOW, its own row): the "teacher overrode the AI" flag never sets
  on rows made by today's code; one test holds that the new key does not
  change that comparison.
- Not run against a browser, the frontend or a real model.

## The credential pattern
Checked on `second_form_38b4c6b6/` before its commit with the saved
`credcheck.sh` (plain files, .gz, .tar.gz members; masked output): no URL,
no encoded URL, no made-up password. Six assignment-form hits, judged by
reading the lines with the values cut: test output ("Sort Key:", "PASS:",
"KeyError:"), no secret. Checked again over this file before its commit.

## Files added by the second form
`second_form_38b4c6b6/` (the three red reproductions, the modules log, the
battery log and its archive, the regression's log and raw log, the scripts
as `.txt`, the written expected sets, the status files);
`CORRECTED_SETS_made_after_the_run.md`; this file.
