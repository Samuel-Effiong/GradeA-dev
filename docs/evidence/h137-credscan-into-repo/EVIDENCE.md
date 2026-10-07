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
