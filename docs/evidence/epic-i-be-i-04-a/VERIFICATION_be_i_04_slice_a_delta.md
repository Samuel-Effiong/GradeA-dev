# Verification record, delta: BE-I-04 slice A at 68f20e56

Verifier: the Next-stage Checker (reserve engineer 0c), independent of the author.
Follows `VERIFICATION_be_i_04_slice_a.md` (VERIFIED-WITH-NOTES at 58326e45, five items required
before the merge). This record covers the delta only. Written 2026-10-06; times are WAT.

## Verdict on the delta

**DELTA VERIFIED at 68f20e56: all five required items are met and nothing is required further.**
Slice A stands as VERIFIED-WITH-NOTES; the notes of the first record remain notes.

## The chain

58326e45 (verified) -> de05d7c6 (the Release Engineer's full-run log, evidence only) ->
670a7f65 (the delta, and my first record) -> 68f20e56 (the delta run's logs, evidence only).

- Outside `docs/evidence/`, 58326e45..68f20e56 changes exactly two files:
  `students/tests_grading_label_migration.py` (new) and `.example.env` (three added lines).
  No code under test, no model, no migration and no other test changed.
- 670a7f65..68f20e56 touches six files, all in the evidence folder.

## The five items

| # | Item | Found |
|---|---|---|
| 1 | The existing-row migration test adopted, with its own red proof | Met. See below |
| 2 | The owed docstring sentence declared | Met: EVIDENCE.md, "Differences from the accepted design note", point 3, says it is owed in slice C |
| 3 | The gate script in the evidence | Met: `run_be_i_04_a_gate.sh.txt`, byte-identical to the author's script as it stands (sha256 starts 5b31c53f for both) |
| 4 | `GRADING_RELEASE_ID` in the example environment file | Met: named, empty, one comment line |
| 5 | The full-suite log committed | Met at de05d7c6: three evidence files; the committed .xz unpacks to sha256 b16ab45546617954b6f16d6a332e8bd16e59b5b8359bef508378f0bc96705733, the raw log I read |

### Item 1, weighed specifically

The committed class is my probe as handed over. Differences, by `diff` against my hand-over
file, all of them: the class name, a module docstring, a class docstring, and one expression,
`float(fresh.score)` -> `float(fresh.score or 0)`.

- That expression cannot make the test pass vacuously. The row is created with score 7; a
  missing score gives 0.0, a zero score gives 0.0, and neither equals 7.0. It guards the grade
  only; the label assertions are untouched.
- The test still proves what the probe proved: at 0030 the six columns do not exist; a graded
  row is inserted; after 0031 the six columns exist and the row reads "unlabelled" in all six,
  by SQL and through the model; back at 0030 the columns are gone and the score is still 7.
- Red proof: mutant M4 (the migration gives `grading_model` another word as its database
  default), expected test named in `mutate.py` at 670a7f65, before the run's evidence commit.
  Its log has its own "Ran 1 test" line, "FAILED (failures=1)", no load failure, and the
  failing test is the expected one; the assertion that fails is the six-column comparison, with
  the mutated word in the fourth position. KILLED is right.
- M4 proves the forward half. The reverse half (the columns go and the grade stays) has no
  mutant; it passed in my run at 58326e45 and in the author's delta run. A note, not required.
- One inexact sentence, not required to change: the new module's docstring says only the name
  and the docstring are the author's; the one expression is too. EVIDENCE.md states it exactly.

## The delta run (the author's, read by me, not repeated)

One grant from the Release Engineer, 15:42:43 to 15:45:59, at 670a7f65.
- `modules_and_guards_670a7f65.txt`: its own "Ran 401 tests in 153.426s" and "OK"; no FAIL or
  ERROR header in the whole file; no skipped test; the new test is in it; 401 is the earlier
  400 plus the one new test, as the author predicted before the run.
- M4 as above. Source clean afterwards; the mutation database dropped.
- No second full run: the Release Engineer's ruling, argued in the evidence. I agree with the
  argument: no file that any test imports changed, except the one new test module, which ran.
- The author discloses that its release message for this run went out late (16:32 for a run
  that ended 15:46); nothing ran in between.

I ran nothing for the delta. The new test is my own probe, which I ran at 58326e45 (passed);
the one changed expression is weighed above; the author's log shows it passing at 670a7f65.

## My first record on the branch

`docs/evidence/epic-i-be-i-04-a/VERIFICATION_be_i_04_slice_a.md` at 68f20e56: sha256
e47b32fca3a3bf669913b59678c4860fba754a26cd14d1be5bf817d3c3494163, and `cmp` against my file
reports no difference. Byte-identical.

## Pattern check

Run by me at 16:59, by program, values never printed.
- The eleven files changed in de05d7c6..68f20e56 (no archive among them): no hit in anything the
  delta added. `.example.env` as a whole file shows one URL with a placeholder word in the
  password position and two short literal assignments; all three are in the file at the base
  9a581258, unchanged, and none is in the three added lines (the same rows at the base and at
  the tip; zero hits in the added lines). They are the example file's own stand-ins and not
  part of this slice.
- The committed full-suite log is .xz, which the team's check does not open yet. I scanned its
  unpacked content by hand: no URL with a password position; nine code-shaped "token" matches;
  two literal hits, lines 80444 and 80449, the word "secret" in a test's blocked-fetch warning
  for a made-up host. The same two lines are in batch 8's and batch 9's full-run logs; judged a
  test stand-in, accepted by the Senior Manager on 2026-10-06.
- My own two records: no hit.

## Not done

No test run of mine for the delta (reasons above). No whole-tree credential scan.
