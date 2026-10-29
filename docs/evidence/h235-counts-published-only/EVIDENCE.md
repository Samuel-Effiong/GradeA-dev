# H-235: my-students' submission counts are over published work only

Branch task/h235-counts-published-only, stacked on H-234's final tip abcc69e5. All times are WAT on 2026-10-14.

## The rule (the user's decision, through the SM)
- **The total:** `total_assignments_in_course` counts the PUBLISHED assignments of the counted courses (H-234's basis). DRAFT and UNPUBLISHED ones are left out; the SM ruled that UNPUBLISHED is treated like a draft. A published assignment that is not yet due is IN.
- **Submitted:** `total_assignments_submitted` counts the student's submissions to those published assignments.
- **The percent:** `percentage_of_submission` follows from the two counts.
- **Unchanged:** `at_risk` keeps its published-and-due basis.

"Published" is the definition the code already used for at_risk: `assignment.status == AssignmentStatus.PUBLISHED`. AssignmentStatus has exactly DRAFT, PUBLISHED and UNPUBLISHED, and the model's default is DRAFT.

## Outside this row (taken to the user by the SM; not changed here)
- **All-students** (`dashboard.views.all_students`) still reports `assignment_assigned` as every assignment of the course, drafts included. Its `assignment_submitted` counts every submission to the course. For a course with drafts, it therefore differs from my-students.
- **at_risk's submitted figure** counts submissions to drafts and to work not yet due, while its expected figure counts only published-and-due work.

## Change
- **b08ef0a2, tests only:** classrooms/tests_h235_counts_published_only.py, 7 tests.
  - The fixture: one course C1 with P1, published, due and graded 80%, which the student Wren submitted.
  - p1, p2: a draft, or an unpublished assignment, is not in the total.
  - p3 (control): a published assignment not yet due is in the total.
  - p4, p5: a submission to a draft, or to an unpublished assignment, is not counted.
  - p6: the percent follows.
  - r1 (control): at_risk still counts only work due by now. With three published assignments not yet due, the counts are (4, 1, 25.0) and at_risk is false.
- **34d70fff, the fix:** students/serializers.py.
  - A static `_is_published(assignment)` helper. `_is_expected_by_now` now calls it, so its meaning is unchanged.
  - The total counts each counted course's prefetched assignments where `_is_published`.
  - The submitted count adds the same check on `submission.assignment`, which is already select_related, so there is no new query.

## Mutants (GAP-58-runs/h235/config.py, copied as `chain_34d70fff/config.py.txt`)
| Mutant | What it does | Expected failing set |
|---|---|---|
| Q1 | the total counts every assignment again | {p1, p2, p4, p5, p6} |
| Q2 | the submitted count takes every submission again | {p4, p5, p6} |
| Q3 | "published" also requires "due by now" | {p3, r1} |
| Q4 | "published" is read as "not a draft" | {p2, p5} |
| Q5 | at_risk moved onto the counts' basis | {r1} |

Each written reason was checked against unittest's own message in plain Python before the run. The operand types are int, int and float for the counts and the rounded percent, and bool for at_risk.

## The chain on 34d70fff (logs gzipped in `chain_34d70fff/`)
- The Verifier (28) pre-read it and cleared it for its slot. Release Engineer bd granted it at 23:30:41.
- It ran 23:31:02–23:34:40 under one systemd-inhibit, in scope gap-h235-chain with MemoryMax=18G.
- Runner row_chain.py sha256 f3fe0bdde92d9065aeaf99aecfc3a2a9c69dc67a9598e561da2faa108d805b8e. Config sha256 3426678f7fecdab10dc909b7e41d5b8fdf64b0ed91faaccb8d3e2f9b27aef57f.

Copied from chain.status and the logs:

| Stage | Result |
|---|---|
| (r) the test file from the tip, on abcc69e5 | "Ran 7 tests in 0.480s", FAILED (failures=5); "5 failing, as expected"; "the red set is the expected one, each for its written reason" |
| (a) 28 labels | "Ran 445 tests in 96.366s", OK |
| (b) baseline | "Ran 7 tests in 0.577s", OK |
| Q1 | FAILED (failures=5); "5 failing, as expected" |
| Q2 | FAILED (failures=3); "3 failing, as expected" |
| Q3 | FAILED (failures=2); "2 failing, as expected" |
| Q4 | FAILED (failures=2); "2 failing, as expected" |
| Q5 | FAILED (failures=1); "1 failing, as expected" |

"(b) every mutant killed, each failing set and reason as written"; "h235 CHAIN END: green as written" at 23:34:40. Rule 24: the row's regression is its batch's full run.

## Files
Every gzip was checked to decompress to its raw original. The logs have no trailing whitespace and contain no credential URLs or passwords.
