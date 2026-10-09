# Verification record: BE-I-04 slice C (the label written with the grade)

Verifier: the Next-stage Checker (0c), independent of the builder. Written 2026-10-07, 12:48 to 12:49 WAT
by the clock. Tip verified: `d37f6a7ef02ad1fd192da5b80b3d5281607be9a1` on
`task/epic-i-be-i-04-c` (base `142a5040`). I read the code at `8f192dd5` before the builder's gate
ran, on the Senior Manager's word; `d37f6a7e` differs from it in `docs/evidence/epic-i-be-i-04-c/`
only (checked by `git diff`).

## Verdict

**VERIFIED-WITH-NOTES, with five items required before merge** (one delta). The label is written
with the grade as designed and as ruled; the builder's 52 mutants are killed and I confirm it from
the committed logs; my own 23 breaks with named tests are all killed. What is owed is one change
of behaviour that the Senior Manager ruled after the freeze (the unknown rate), three tests for
gaps my run showed, and one correction of words.

**Credential pattern check (done 12:49 WAT, after the Release Engineer's LIFT; masked output,
no value printed, copied or tested):** the 81 files that differ between the Phase 2 line
`82c3108d` and this tip, archives opened (`.gz` and `.xz`). No address with a password position
filled. In slice C's own files: no literal assignment to a secret-shaped name outside test
files (2 such lines in test files, the usual fixture password with its allow mark). Two lines
match in a file that is NOT slice C's, the Release Engineer's full-run log that came with the
base (`docs/evidence/epic-a-refresh-11/gate10_release1_r1_82c3108d.log.xz`, lines 82039 and
82044): a warning line of the form "Blocked unsafe fetch_url_content request for
http://(host)/secret: '(host)'", where the matched value, 13 characters, is the same text as the
host name in the address on that line. By its shape it is a test's blocked address, not a
credential. Told to the Release Engineer, whose file it is.

## What I ran (one run, the Release Engineer's grant of 12:08:10)

`run_0c_verify_c.sh d37f6a7e`, 12:08:28 to 12:13:53 WAT, exit 0, in my own scratch checkout
(`GAP-0c-scratch/be-i-04-c`), 6G cap, every run to a file, bytecode off and caches cleared per
mutant, own mutation database, both databases dropped. Load 6.04 at the start, 7.58 at the end.
Logs: `~/Documents/Projects/GAP-0c-runs/be-i-04-c/logs/`. All four of my files were written and
their checksums given to the Release Engineer before the run; expected failing tests were named
per mutant beforehand.

| Step | Expected | Result |
|---|---|---|
| 1. The builder's six slice C test modules, once | OK | `Ran 126 tests`, OK |
| 2. My probes PC0 to PC4 | OK | `Ran 8 tests`, OK |
| 3. My probes PC5 and PC6 | both RED at the tip | both RED |
| 4. P1 to P8: red proofs of my probes | each fails its named probe | 8 of 8 KILLED |
| 4. Y1 to Y5, Y7 to Y14, Y16, Y17 against the builder's tests | killed by the named tests | 15 of 15 KILLED, every named test among the failures |
| 4. Y6 and Y15 | SURVIVE (predicted gaps) | both SURVIVED |

None unpredicted, none broken, no timeout (longest inner run 15 seconds). I did not repeat the
builder's regression (rule 15).

**Three things about my own run, said plainly.**
1. In my request I wrote "9 tests" for step 2. It is 8 (PC1 one, PC2 two, PC3 two, PC4 two, PC0
   one). My miscount; nothing was left out.
2. The inner runs of P1 to P5 ran my whole probe module, which includes PC5 and PC6, red at the
   tip. So a non-zero exit alone proves nothing there. Each kill rests on the named probe: green
   in step 2, and among the failures under its mutant with the assertion message I intended
   (I read each: `'yes' != 'no'`, `['_evidence_mode at line 2813'] != []`, `'no' != 'unknown'`,
   the second model left in its list, `'deterministic'` or the main model where the backup was
   expected).
3. **PC5 went red at an earlier assertion than the one I wrote it for.** See finding 5. My claim
   about the vote count is therefore still BY READING ONLY.

Rule 19, probe by probe: PC0 seen red under P6, P7 and P8; PC1 under P1; PC2 under P2; PC3 under
P3; PC4a under P4; PC4b under P5; PC6 red at the tip; PC5 red at the tip, at another assertion.

## The builder's gate, as I read it from the committed files

- `mutation_results.json`: 52 entries, all KILLED, each with a "Ran" line (125 tests; 1 for V1),
  its expected test among `failing_tests`, none with exit 0 or 124. I opened four mutant logs
  myself (S13, V1, T4, A2): each ends with its "Ran" line and names the expected test.
- `modules_and_guards.txt.gz` ends `Ran 891 tests`, `OK`; `red_run_code_as_at_red_commit.txt.gz`
  ends `Ran 125 tests`, `FAILED (failures=29, errors=91)`. The checksums of both unpacked files
  equal the lines in `gzipped_logs_sha256.txt`. I did not recount the 111 and 14 by distinct test.
- Tests first: five tests-only commits before the code commit; one test strengthened after the
  code and before any run, disclosed, with the reason.
- The regression is not run yet: by the Senior Manager's order it is the Release Engineer's one
  full run on the final tip.

## Findings

### 1. The unknown rate leaves out every grading where a backup answered (seen red: PC6)

`audit/emitter.py`: "no" gives the backup rate 0 and the unknown rate 0; "unknown" gives the
unknown rate 1; "yes" gives the backup rate 1 and NO sample to the unknown rate. PC6 at the tip:
`[('model_fallback_rate', 1.0)] != [('model_fallback_rate', 1.0), ('model_unknown_rate', 0.0)]`.
**Senior Manager's ruling (2026-10-07): not intended.** The unknown rate is over all measured
runs: unknown 1, no 0, yes 0. The backup rate keeps its meaning: yes 1, no 0, unknown no sample.
Also ruled: a run with a backup AND an unnamed model reads "yes" and gives the unknown rate 0,
accepted, and the documents must say the unknown rate measures "backup use not knowable", not "a
model not named"; a grading with no fresh call gives neither rate a sample, intended, to be
written down as a third stated case.

### 2. No builder's test rejects a reply that holds answers (Y6 SURVIVED)

Y6 counts a chunk's reply before its evidence check, so a reply that is then rejected and asked
again stays in the label. All 23 tests of `tests_grading_run_pipeline` pass under it. The
builder's test of a rejected reply uses a reply with no answers at all, and a count of none adds
nothing. My PC1 (a backup's reply with every answer present and a quote the student never wrote,
rejected, then the main model) fails under the same break. The code at the tip is right; the
test that holds it is missing.

### 3. A call site that drops the run is seen by nothing (Y15 SURVIVED)

Y15 reads the evidence mode without the run at one site. All 23 tests pass. The static check
looks for live `settings` reads; a dropped run reads live through `_grading_setting(None, ...)`,
which takes a fresh reading, and the scan cannot see that. So the evidence's stated limit, "the
other twelve rest on the static check", says more than the check gives. My PC2 (every call
inside the grading service to a method that takes the run hands the run on by name) fails under
the same break and passes at the tip.

### 4. No test goes from a real entry point through the real grading service to the row

The route tests replace the whole grading service with a stand-in that fills the run by hand;
the service tests drive the real service and read the run, not the row. The halves meet at
`run=run`. Each half is well held (Y-mutants on both sides die), and my PC0 passes at the tip:
the immediate route, the real service, only the provider call replaced, a backup answers, the
six columns and the stored entry agree; a second student with the same answer makes no call,
the row names the model that first answered, the entry says `no_fresh_call`. Seen red three
ways (P6, P7, P8). My condition G1 asks for one such test in the repository.

### 5. A reply that repeats an answer: what the run showed, and what is still reading

What I wrote PC5 for (by reading): votes for the grade's model are counted from the raw reply's
length (`run.keep_answers(model, len(evaluations))`), so a reply holding one answer three times
votes three times. **Not shown:** PC5 failed before it reached that assertion.

What the run did show: with two reused answers and a fresh reply that holds answer 3 three
times, the result of the grading holds **5 answers for a paper of 3**
(`AssertionError: 5 != 3`). By reading only, from there: `_finalize_grading_result` adds every
item's score into `total_score` while `max_total_points` counts each question once, so the
repeated answer would be counted three times in the score. I have NOT shown the score effect by
a run, and I have not checked whether beta and production reject such a reply earlier; the same
function is on both. **This is not slice C's work** (the slice does not touch that function). It
goes to the Senior Manager as a possible older fault that can change a grade. For slice C it
matters in one way: whatever is decided there decides what "the answers of the saved grade"
means for the vote.

### Smaller points, by reading only, none required

- A second opinion over more than one chunk whose later chunk fails leaves the first chunk's
  model in `models_second_opinion`, though the opinion was thrown away.
- `_populate_and_save_grade` handed no run labels the grade "deterministic / not_applicable"
  quietly. The call sites are pinned by a test; a loud refusal would be safer.
- The entry's `prompt_version` is the run's uncut text and the column is cut to 128; audit
  metadata drops a string over 128. Today's versions are about 40 characters.
- The serializer walk's own guard asks for "at least 2" serializers found, looks only in modules
  whose name contains "serializer", in a fixed list of eight apps, and does not see a field
  declared under another name with `source=`. A serializer defined elsewhere, or in a new app,
  is not seen.
- A teacher's manual change keeps the AI's label (as ruled). The documents should say a label
  can stand beside a score a person set.

## Required before merge (one delta)

1. **The unknown rate, as ruled:** "yes" gives `model_unknown_rate` a 0. The emitter's comment or
   docstring and `03a` state the two bases (backup rate over runs where it is known; unknown
   rate over all measured runs), that the unknown rate means "backup use not knowable", and the
   third case (no fresh call: no sample to either). A test of it, shown red against the tip's
   code. My PC6 may be adopted.
2. **A test in which a reply that HOLDS answers is rejected and not counted,** shown red under a
   break that counts the reply before its checks. My PC1 may be adopted (red proof: my P1).
3. **A check that sees a dropped run,** shown red under such a break; and the stated limit's
   words corrected to what the checks give. My PC2 may be adopted (red proof: my P2).
4. **One test from a real entry point through the real grading service to the row and the stored
   entry,** with its red proof. My PC0 may be adopted (red proofs: my P6, P7, P8).
5. **The vote count stated or changed,** after the Senior Manager's word on finding 5: either
   the count is taken from the answers that reach the saved grade, with a test seen red, or the
   evidence states as a limit that it is taken from the reply as received.

Probes offered but not required: PC3 (the rate's word equals the flag for every all-fresh mix),
PC4a and PC4b (a second opinion by a backup-list model; a failed second opinion).

## Carried items from slices A and B

Done at this tip, checked by reading: the reverse half of the migration test has its mutant (V1,
killed); the migration test's docstring states the one changed expression; "nothing said" is in
`build_cache_key`'s stated limits and in `03a`; "a reused answer is not stored a second time" is
held by a direct test and mutant S13 (killed), with the reason no end-to-end test of it is added;
the founder's sentence is in `_populate_and_save_grade`'s docstring with a test; the settings
reads are on the run's reading; the four audit keys are as ruled. My all-serializers probe is
replaced by the builder's own walk; see the smaller points.

## Not checked

The full regression (the Release Engineer's, after the delta). The live paid-AI test. The 111
and 14 of the red run by distinct test. Beta and production for finding 5.
