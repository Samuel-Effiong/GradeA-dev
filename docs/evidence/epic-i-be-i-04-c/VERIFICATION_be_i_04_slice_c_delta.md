# Verification record: BE-I-04 slice C, the delta after verification

Verifier: the Next-stage Checker (0c), independent of the builder. Written 2026-10-07, 13:52 WAT by
the clock. Tip verified: `752baa2ab044b26db3ac5a20b7d8c71cd19d2a59` on `task/epic-i-be-i-04-c`.
This record covers the difference from `d37f6a7e` only; the first record
(`VERIFICATION_be_i_04_slice_c.md`, sha256 d4b98836...bbbc82) stands unchanged beside it, and its
committed copy at this tip is byte-identical to mine (`cmp`).

## Verdict

**VERIFIED.** The five items required before merge are each closed. Nothing more is required of
the builder before the Release Engineer's one full run on the final tip. Four notes, none required.

## What the delta is, as I read it (`git diff d37f6a7e 752baa2a`)

- `6d28cc0f`, tests only: two new modules, `ai_processor/tests_grading_run_checker.py` (8 tests)
  and `students/tests_grading_label_end_to_end.py` (1 test). Compared by `diff` with my hand-over
  files (fe6f3a04..., a615c03a...): they differ in the formatter's line wrapping, one `dict(...)`
  written as a literal, the module docstrings and one docstring line of PC6; PC5 is left out.
  No assertion, fixture or patched name differs.
- `37af761b`, code and words: ONE line of behaviour (`audit/emitter.py`: "yes" now also gives
  `model_unknown_rate` a 0.0); the comment above it; a paragraph added to the docstring of the
  vote in `ai_processor/grading_run.py`; a note in `03a_data_model.md`; `EVIDENCE.md`; six
  mutants added to the builder's `mutate.py`, which can now make a break of two edits.
- `752baa2a`, files under `docs/` only (checked: no file outside `docs/` differs from
  `37af761b`): the gate's logs, and the row number H-154 in `EVIDENCE.md` and in one sentence of 03a.

## The five items

| Item | Closed by | How I know |
|---|---|---|
| 1. The unknown rate as ruled, the two bases and the third case in words, a test seen red | the emitter's line and comment; the 03a note; my PC6 adopted | read; PC6 red against the old code in my first run and again in the builder's red run (`[('model_fallback_rate', 1.0)] != [..., ('model_unknown_rate', 0.0)]`); green at this tip in my run; my D1, D2, D3 below |
| 2. A rejected reply that holds answers is not counted | my PC1 adopted; no code change | red under my P1 (first run) and under the builder's S14, the same two edits (its committed results) |
| 3. A check that sees a dropped run; the limit's words corrected | my PC2 adopted (two tests); `EVIDENCE.md`'s stated limit rewritten | red under my P2 and the builder's S15; the new words name both static checks and what neither sees (a new method not added to the hand-kept list; a caller outside the grading service) |
| 4. One test from a real entry point through the real service to the row and the entry | my PC0 adopted | red under my P6, P7, P8 and under the builder's S16, T2, T3 (PC0 is among the failing tests of each in the committed results) |
| 5. The vote's limit stated (Senior Manager's ruling: stated, not changed) | the docstring of the vote, the 03a note, `EVIDENCE.md`'s stated limits | read: all three say votes are counted over a reply's items as received and that a repeated item votes again; 03a and the evidence name the row H-154 |

The words asked for in item 1 are all there, in the emitter's comment and in 03a: the backup rate
over gradings where it is known; the unknown rate over all measured gradings; "it does not mean
a model was not named", with the case of a backup and an unnamed model; no fresh call gives
neither rate a sample.

## What I ran (one run, the Release Engineer's grant of 13:50)

`run_0c_verify_c_delta.sh 752baa2a`, 13:50:33 to 13:51:44 WAT, exit 0, in my own scratch checkout,
6G cap, every run to a file, bytecode off and caches cleared per mutant, own database, dropped at
the end. Load 4.45 at the start, 5.24 at the end. Logs:
`~/Documents/Projects/GAP-0c-runs/be-i-04-c/logs_delta/`. Both files' checksums and the expected
failing tests were given to the Release Engineer before the run. I did not repeat the builder's
900-test run (rule 15).

| Step | Expected | Result |
|---|---|---|
| Baseline: `students.tests_grading_label_written` and `ai_processor.tests_grading_run_checker` at the tip | OK | `Ran 41 tests`, OK |
| D1: "yes" gives the unknown rate a 1 | killed by `test_yes_no_and_unknown_each_give_one_unknown_sample` | KILLED, that test alone (`('model_unknown_rate', 1.0)` where 0.0 is wanted) |
| D2: the new sample replaces the backup rate's 1 | killed by that test and three of the builder's (`test_a_backup_among_the_fresh_calls_is_a_one`, `test_the_old_single_model_does_not_override_the_key`, `test_a_long_backup_name_is_still_a_one_in_the_rate`) | KILLED, exactly those four |
| D3: no fresh call gives the unknown rate a 0 | killed by `test_no_fresh_call_gives_no_sample` | KILLED, that test alone (`{'model_unknown_rate': 0.0} != {}`) |

Each with its "Ran 41 tests" line; none survived, none broken, no test outside the named ones
failed. D1 to D3 are mine and are not among the builder's six.

## The builder's delta gate, as I read it from the committed files (no run of mine)

- `mutation_results_delta.json`: 58 entries, the same names as the 58 expected tests in
  `mutate.py`; all KILLED; each has a "Ran" line (134 tests; 1 for V1), its expected test among
  the failing tests, and an exit that is neither 0 nor 124. The six new rows (A5, S14, S15, S16,
  S17, R21) each fail the test named for them. Under S6 the first PC4 test is among the failing
  tests; under T2 and T3 the PC0 test is, as the evidence says.
- `red_run_code_as_at_red_commit_delta.txt.gz`: `Ran 9 tests`, `FAILED (failures=1)`; the one
  failure is the PC6 test, with the message above; the other eight are listed and pass.
- `modules_and_guards_delta.txt.gz` ends `Ran 900 tests`, `OK`; no FAIL or ERROR header; all nine
  adopted tests are listed in it.
- `gzipped_logs_delta_sha256.txt`: 61 lines; the sha256 of each unpacked file equals its line.
- The gate script (`run_be_i_04_c_gate_delta.sh.txt`) is the first gate's with the red commit, the
  modules and the file names changed; every run goes to a file, bytecode is off in the red run,
  and the mutants use their own database.

**Credential pattern check (13:38 WAT, masked output; no value printed, copied or tested):** the
75 files that differ between `d37f6a7e` and this tip, archives opened. No address with a password
position filled. No literal assignment to a secret-shaped name outside test files; one such line
in a test file. The other matches are names that only contain the word KEY, as in cache keys and
the sub-check labels the builder's evidence describes.

## Notes, none required

1. `EVIDENCE.md`'s table for item 5 still says "I do not have its number yet", and the section's
   opening says "two commits"; the last paragraph gives H-154 and there are three commits. Words
   only.
2. The docstring of the vote in the code names the root without its number, on the Senior
   Manager's word (the code is as verified). 03a and the evidence carry H-154.
3. The limit of PC2 is stated by the builder: the list of methods that take the run is kept by
   hand. A new method that takes the run and is left off the list is seen by nothing.
4. The five smaller points of the first record are not taken up in this delta, as the builder
   says. They stand as notes: the second opinion over more than one part; the quiet label when no
   run is handed; the uncut prompt version in the entry; the reach of the serializer walk; the
   words about a label beside a score a person set.

## Not checked

The full regression (the Release Engineer's one run on the final tip). The live paid-AI test.
The other 52 mutant logs one by one (read through the results file only). H-154 itself, which is
another row's work with its own verifier.
