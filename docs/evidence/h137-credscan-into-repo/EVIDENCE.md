# H-137: the credential pattern check comes into the repository, with two required fixes

Author: the Release Engineer (0b), 2026-10-07. Branch `task/h137-credscan-into-repo`, off beta
`3457df56`. Verifier: Verifier 2. Tooling only: nothing the application runs is changed.

## What the row asks

The team's credential pattern check lived outside the repository
(`~/Documents/Projects/GAP-0b-runs/credscan/credscan.py`, sha256 prefix `bdbd2e3d5b0e4b70`, the
version that opens `.xz` and `.bz2`, H-136). The row: bring it into the repository under
`scripts/` with its test, and make two fixes that are REQUIRED, because a silent skip is the
worst thing a checker can do:

1. a line longer than 4000 characters must be scanned, or at the very least reported as skipped;
2. an archive nested deeper than the limit is reported as not opened.

Also seen by Verifier 2, the same family, and done here: a file with a NUL byte in its first 4096
bytes was passed over in silence. Two limits go into the docstring: text after the end of a
compressed stream is not read; archive types the tool does not claim are not opened.

## Order of work

1. `71b4fab3`: the tool moved as it is (reformatted; the report split into functions so a test
   can read it), with nine tests of what it already did. No behaviour changed.
2. This commit: fourteen more tests, TESTS ONLY, for the two fixes and the binary-file count.
3. Then the fixes.

## Expected at this commit, written 2026-10-07 11:42:16 WAT, before any run

Nothing of this row has been run through a test runner. What follows is from calling the tool's
own functions directly in plain Python (no test loader) and from reading.

`AutoGrader.tests_credscan`: 23 tests. **11 expected to FAIL, 12 to pass.**

Expected to FAIL on the tool as moved (11):

`LongLineTests` (6 of 7):
- `test_a_pattern_far_along_a_long_line_is_found`
- `test_a_pattern_at_the_start_of_a_long_line_is_found`
- `test_a_pattern_after_one_long_unbroken_run_is_found`
- `test_one_hit_is_counted_once_however_many_words_surround_it`
- `test_the_hit_on_a_long_line_carries_its_line_number`
- `test_a_long_line_is_said_to_be_long_in_the_report`

`NestingTests` (2 of 3):
- `test_a_fourth_archive_is_reported_as_not_opened`
- `test_the_archive_not_opened_is_listed_in_the_default_report`

`BinaryFileTests` (3 of 4):
- `test_a_file_with_an_early_nul_byte_is_counted_as_not_read`
- `test_the_files_not_read_are_named_when_every_row_is_asked_for`
- `test_a_report_with_nothing_unread_says_zero`

Expected to PASS already (12): the nine of `71b4fab3` (`ArchiveFormsTests` 3, `MaskingTests` 3,
`FileClassTests` 3) and three pins of behaviour that must not change:
`LongLineTests.test_a_line_of_exactly_the_limit_is_not_called_long`,
`NestingTests.test_three_archives_deep_is_opened`,
`BinaryFileTests.test_a_nul_byte_after_the_first_4096_bytes_does_not_stop_the_scan`.
Rule 19: those twelve have never been seen red; each needs a mutant that fails it before it
counts as evidence.

## The fixes (`5bff2762`)

- **Long lines are scanned.** Running the two patterns over a whole long line is what the 4000
  limit avoided: on one unbroken run of letters the name pattern can take minutes. So on a long
  line each "://" and each of the five words (PASS, PWD, SECRET, TOKEN, KEY) is looked at with
  the text around it: 200 characters before a word and 420 after; 600 after "://". An
  assignment is taken once, by where its name ends. Every long line is counted in a `longline`
  row that every form of the report lists.
- **What this gives up, stated in the docstring:** on a long line a password part longer than
  about 590 characters, or a name and value that together reach further than the window, is not
  seen.
- **A fourth archive inside three** is listed as `NOT-OPENED:nested-too-deep`, in every form of
  the report. It is then read as text like any other file, so an uncompressed tar that looks
  binary is also counted under the next point.
- **A file taken for binary** is counted in a line of the report, and named under `--all`.
- The two patterns, the masking and the test-file rule are unchanged. The recording of a hit
  moved into two functions shared by short and long lines.

**Checked by calling the tool's functions directly in plain Python (no test loader), before any
run:** the same text gives the same counts on a short line and after 4800 characters of
padding; and, for cost, one line each of 4 MB of base64 (0.23 s), 2 MB of one letter (0.14 s),
600,000 characters of one word repeated (5.3 s: the cost follows the number of words) and
100,000 repeats of "://" (0.18 s). These are observations on this laptop, not test results.

**Two checks I wrote and then removed, because no test could ever tell them apart from the code
without them:** a "this match belongs to another word" test (the take-once rule already covers
it), and a take-once rule for addresses (each "://" is tried once from its own place, so an
address cannot be found twice).

## The mutants: 20, with the exact failing set of each, written 2026-10-07 11:47:39 WAT before any run

`mutate.py` (in this folder). A kill needs a non-zero exit, the run's own "Ran" line, no load
failure, and the failing tests EXACTLY equal to the set named; a different set is BROKEN, not a
kill. All twenty sets are by READING, none from a run.

| Mutant | The break | Tests that must fail, and no others |
|---|---|---|
| L1 | a long line is passed over, as before | the six long-line tests that were red at the tests-only commit |
| L2 | a long line is not counted | long line said to be long |
| L3 | the report does not list long lines | long line said to be long |
| L4 | an assignment on a long line is taken once per word near it | one hit counted once |
| L5 | a line of exactly the limit is called long | exactly the limit is not called long |
| L6 | addresses are not looked for on a long line | far along a long line; at the start of a long line |
| L7 | line numbers start at 0 | hit list names the file and the line; hit on a long line carries its line number |
| N1 | an archive nested too deep is not reported | fourth archive reported; listed in the default report |
| N2 | four archives deep are opened | fourth archive reported; listed in the default report |
| N3 | only two archives deep are opened | three archives deep is opened; fourth archive reported; listed in the default report |
| N4 | the report does not list archive rows | listed in the default report |
| B1 | a binary file is not counted | early NUL counted as not read; named when every row is asked for |
| B2 | the binary probe reads 8192 bytes | early NUL counted; a NUL after 4096 bytes does not stop the scan; nothing unread says zero |
| B3 | the report has no line for files not read | early NUL counted; nothing unread says zero |
| M1 | the hit list holds the value, not the name | value never in the rows or the hit list; value never in the report |
| F1 | no file is taken for a test file | literal assignment in a test file counted but not listed |
| F2 | the report does not list addresses by default | an address in a test file is listed |
| F3 | a value is not compared with its other forms | one value written two ways |
| A1 | an .xz file is not opened | planted patterns found in every form; a broken xz is reported |
| A2 | an opened .gz is also given an archive row | planted patterns found in every form; a clean body gives nothing |

**Rule 19:** every one of the 23 tests is in at least one set, so each is to be seen red by a
run: the eleven in the red run, and all 23 under a mutant.

## The gate, as it will be asked for

`run_h137_gate.sh <tip>` (copy here as `run_h137_gate.sh.txt`): 0 the red run (the module
against the tool as at `840619b4`), 1 the module and 23 repo-wide guard modules, 2 the twenty
mutants. Serial, 6G cap, sleep inhibited, every run's output to a file. The module uses no
database. **Expected:** step 0 non-zero, Ran 23, exactly the eleven named above failing; step 1
OK; step 2 twenty KILLED.

**Not asked for:** a regression of an app. The change is one new script that nothing imports and
one new test module; the batch's full run covers the rest.
