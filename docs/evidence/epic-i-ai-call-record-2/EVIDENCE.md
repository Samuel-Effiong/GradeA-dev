# AI-call record, slice 2: evidence (batch 20)

Branch task/epic-i-ai-call-record-2. Base e68f08cd (beta at the base update, a6). The final tip is this commit's parent b8191553: b9a222cb, the tip the full gate ran on, plus one docs-only commit adding MUT_ONLY to mutate.py for the D3 re-run. All times are WAT on 2026-10-14. Every figure below is copied from the logs in this folder.

## The gate that passed: cacce6ec on b9a222cb, 16:10:27–17:24:21 (`gate_b9a222cb/`)
Script `scripts/gate_cacce6ec.sh.txt` (also `run_ai_call_record_2_gate.sh.txt`), run as `b9a222cb 1 e68f08cd` under one systemd-inhibit. Release Engineer a6's grant 16:10, with the Verifier's clear.

| Step | Result |
|---|---|
| 0 the red run (the slice's tests against e68f08cd's code) | exit 1; "Ran 74 tests in 26.683s", "FAILED (failures=18, errors=133)". Every written reason held inside its own block, and every per-module FAIL/ERROR count matched (columns 9/121, static 8/0, routes 0/9, scope 1/0, adopted 0/0, loader 0/3). |
| 1a makemigrations --check | exit 0, "No changes detected" |
| 1b the seam | execute_graded_task identical to e68f08cd and fafed57f (9376 bytes); slice C's tests: 0 bytes of diff; "slice C pin: exactly the recorded patch" (`scripts/pin_0031_historical_model.patch.gz`) |
| 1c migration safety against e68f08cd | exit 0; OK "additive only" for 0032, 0033 and 0034 |
| 1 slice modules + 14 pins + near modules + guards (77 labels) | "Ran 1326 tests in 1077.849s", "OK (skipped=1)", 16:12:21–16:31:32 |
| 2 the 47 mutants | "mutants exit=0 17:24:17"; KILLED 47, SURVIVED [], BROKEN [] (`mutation_log.txt.gz`, `mutation_results.json.gz`) |

**The uncut summary.** The gate printed the mutation log through `cut -c1-260`. Its KILLED line was cut at 252 characters, which showed 40 names (R1 to S3). The uncut `mutation_log.txt` and `mutation_results.json` hold all 47, every one KILLED; the Verifier (28) reconciled this. The script now prints those summary lines whole (`scripts/gate_9c7d0e3b_current.sh.txt`, not yet used for a run).

**Failing tests outside the expected sets.** Every kill holds its whole expected set. Four mutants failed more tests than their sets name:
- **X2 (12) and X3 (6):** the scope-leak mutants. By mutate.py's own header, tests that see only an earlier test's leaked scope fail by battery order and are deliberately not named.
- **R9 (4):** test_a_provider_that_names_no_model_leaves_unknown, test_a_re_read_replaces_the_label_in_the_one_update, test_a_row_made_before_the_columns_existed_gets_its_first_label, test_an_extraction_that_fails_leaves_the_row_and_its_label_alone.
  - Each fails with `IntegrityError: … violates check constraint "answers_recheck_pair_allowed"`.
  - R9 writes the reading's model where the re-check did not run, giving the pair (not_run, <model>), which 0033's constraint refuses on save.
  - These route tests could not run before the fixture fix of 15:10 (below), so the set, written earlier, did not foresee them. EXPECTED was not changed after the run.
- **V14 (1):** test_only_the_listed_files_name_a_reading_column. The mutant adds a new `answers_model` mention in services.py, which the static guard reports.

## D3: void (suspend), then re-run and killed (`d3_rerun_b8191553/`)
- **The suspends.** The laptop suspended at 17:13:47–17:19:14 (requested 17:13:45) and again at 17:24:22–17:37:05.
  - The gate had ended at 17:24:21, so only the first window touched it.
  - The first was a GNOME menu suspend with a polkit override (gnome-shell, 17:13:42), which no inhibitor can block. The gate's inhibitor held otherwise.
- **The void.** From each mutant log's start and end times, only D3 (17:10:09–17:20:40) spans that window. D3 showed its expected three failures, but the SM ruled it VOID (any mutant overlapping a suspend). D1 and D2 ended by 17:09:57, and D4 started at 17:20:41.
- **The re-run.** MUT_ONLY was added to mutate.py (b8191553; an unknown id STOPS; all 47 anchors are still checked; a subset writes under its own names). The runner is `scripts/run_slice2_mutant_subset_c4912491.sh.txt`.
  - Release Engineer bd's grant 22:46, with the Verifier (28)'s clear.
  - It ran 22:46:18–22:46:37: "SUBSET: ['D3'] of 47", KILLED ['D3'], SURVIVED [], BROKEN [].
  - D3: "Ran 175 tests in 6.928s", FAILED (failures=3), exactly the expected three.
  - 0032, 0033 and 0034 were restored (sha256 against HEAD), the source was clean, and the _mut database was dropped.
- **The battery stands at 47 of 47 KILLED:** 46 on b9a222cb, and D3 on b8191553, whose only change from b9a222cb is the docs-only mutate.py.

## The three stops before it (`stopped/`)
1. **15:10:11–15:10:51 on 47fcee87, gate 9c655dfc: step 0, exit 4.**
   - Two set fragments were written as "frozenset() != frozenset(...)". unittest prints "Items in the second set but not the first:\n'extract_answer'".
   - The routes tests' teacher had a funded wallet but NO subscription, so six route tests died at the real access check (AIFeatureNotAvailableError, "No active subscription"). By reading, step 1 would have stopped on the same six at the tip.
   - Fixed by e4f3f0d7 (tests only: an active subscription in the routes fixture). Gate 5fea09f0: RED_CODE=$BASE; a check that the slice's code files relative to the base are exactly CHANGED + NEWFILES; the real set message; written reasons for the nine route tests; step 0 checks the WHOLE red picture (Ran line, FAILED totals, per-module counts).
2. **15:41:45–15:50:12 on e4f3f0d7, gate 5fea09f0: step 0 as written; step 1 "Ran 1326", FAILED (errors=3).** All three were test faults; no production fault. Fixed by b9a222cb (tests only); gate 796a75dd.
   - Two pipeline tests read as a metered teacher with a stand-in assignment that had no course. execute_graded_task records usage against assignment.course after the call, so they never reached their claims. They now use a real assignment of the teacher's course.
   - Slice C's pin read the CURRENT model after migrating to 0031: "slice 2 adds the first fields after 0031; the pin read the current model". It now reads the 0031 historical model, allowed in step 1b only as the exact recorded patch.
   - The same latent fault in this slice's own round-trip test was fixed too: it now reads the 0034 historical model.
   - A whole-tree check found no migration that uses a current model in RunPython.
3. **16:06:50–16:07:48 on b9a222cb, gate 796a75dd: step 1 ran NO tests** ("One of the test labels is a path to a file").
   - The new step-1b variable `PIN` overwrote the script's PIN list of 14 pinned modules.
   - Fixed in gate cacce6ec: renamed to PIN_FILE. The 77 labels were shown by a text parse before the re-grant.

## Files
- `gate_b9a222cb/`: the full gate's outputs, gzipped (step 0, 1a, 1b, 1c, 1; the mutation log and results; 47 mutant logs, D3's being the void run), and the gate's stdout and its start and exit stamps.
- `d3_rerun_b8191553/`: the D3 re-run's check, log, results, mutant log and stdout.
- `stopped/`: for each stop, the gate's stdout and the log that shows its cause.
- `scripts/`: each gate copy that ran (9c655dfc, 5fea09f0, 796a75dd, cacce6ec), the current one (9c7d0e3b), the D3 runner (c4912491) and the pin patch (9f78aa9c, gzipped: a diff's blank context lines carry a trailing space the whitespace hook would strip).
- `MIGRATIONS-WHAT-THE-CHECKS-MUST-SHOW.md`: written before the runs.
- Every gzip was checked to decompress to its raw original. No credential URLs or secret-like values were found in any output.
