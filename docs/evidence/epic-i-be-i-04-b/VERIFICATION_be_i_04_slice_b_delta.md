# Verification record, delta: BE-I-04 slice B at 6ec5af66, with a correction of my first record

Verifier: the Next-stage Checker (reserve engineer 0c), independent of the author.
Follows `VERIFICATION_be_i_04_slice_b.md` (VERIFIED-WITH-NOTES at 728491a2, three items required
before the merge). Written 2026-10-06; times are WAT, read from the clock or from the logs.

## Verdict

**DELTA VERIFIED at 6ec5af66 (19:24 WAT): the three required items are met and nothing is required
further. Slice B stands as VERIFIED-WITH-NOTES, and that verdict is now final**: every probe of
mine it leans on has been seen red in a run, or is struck below.

## A correction of my first record: reasoning presented as shown

My first record for this slice says, in finding F3, that the code is right at the chunk call's
splice and "my probe PB8 shows it", and its probe table says of PB8 that "all three prompts of
the run still carry the teacher's text". I also told the Senior Manager that PB8 kills my
mutant W12. None of that had been shown by a run. It was reasoning presented as shown.

- My own raw log said the opposite of "kills": W12 SURVIVED. My runner for that verification ran
  only the author's test modules under my mutants; my probes were never run under any mutant.
- PB8's first assertion could not fail. It looked for the teacher's text in a dump of ALL the
  keyword arguments of each provider call. Those include the assignment object, and my stand-in
  assignment prints its own instructions in its text form, so the text was always in the dump,
  spliced into the prompt or not.
- The author adopted the class as handed over; its two mutants for the two splice sites both
  SURVIVED (the author's run at 525fdf88, kept and committed). The Senior Manager found the
  fault from that run. The author corrected the assertion to look at the system prompt actually
  sent; both mutants are now killed (below).
- What my first record should have said: the three splice sites pass the run's reading to the
  splice helper, verified BY READING ONLY; nothing guards two of them (W12 survived).
The first record stays as committed. This section corrects it.

## The delta (728491a2..6ec5af66), checked by reading

Chain: cbb99e9c (tests only) -> 525fdf88 (mutants, document lines, my first record) -> 46a8b795
(evidence: the run where two mutants survived) -> c24f057c (one assertion corrected) ->
6ec5af66 (evidence: the second run).

Outside `docs/evidence/` four files changed:
- `ai_processor/tests_grading_cache_key_v2_checker.py` (new): my two handed-over classes.
- `ai_processor/tests_grading_cache_key_v2.py`: one test removed.
- `ai_processor/grading_cache.py`: eight docstring lines added to `build_cache_key`. No code:
  the syntax tree equals 6ca94c09's once docstrings are blanked (my script checks it before it
  runs; the Release Engineer checked the same). The other three code files are byte-identical
  to 6ca94c09. So the full run at 6ca94c09 stands for this tip (the Release Engineer's and the
  Senior Manager's ruling; I agree).
- `docs/phase2/architecture/03a_data_model.md`: the stated limits gain the image line and the
  "nothing said" choice.

| # | Item | Found |
|---|---|---|
| 1 | A test that fails when the chunk call's or the summary call's splice stops using the run's reading | Met. `OneReadingPerRunOnALongPaperTest`, my class with ONE assertion changed by the author: it reads `kwargs.get("system_prompt")` of every call. The author's mutant P1 (chunk call) is KILLED with the failure at call 2; P2 (summary call) is KILLED with the failure at call 3; each log has its own "Ran 96 tests" line and the assertion text shows the system prompt without the teacher's text |
| 2 | A test that the answer side's parts cannot run together | Met. `TheAnswerSidesPartsCannotRunTogetherTest`, my class unchanged but for its name. The author's P3 (status and notes joined) and P4 (text and status joined) are KILLED, each by its one test, the two keys shown equal in the log |
| 3 | The test that could not fail | Removed, and EVIDENCE.md says so and why |

Also checked: the author's first delta run (525fdf88) is disclosed whole, with P1 and P2
SURVIVED; the second (c24f057c) shows Ran 629 tests, OK, no FAIL or ERROR header, no skip, and
38 of 38 mutants killed; all 260 entries of the three hash lists match their logs; the image
limit is in the module and in 03a.

Pattern check of the delta, by program, values never printed: 94 files, 80 gzipped logs opened,
no .xz among them; no URL with a password position; no literal secret-named assignment.

## Red proofs of my own probes (rule 19)

One slot from the Release Engineer, 2026-10-06 19:16:01 to 19:22:58, in my own checkout at
6ec5af66. Serial, 6G, rules 12, 13, 16, 17, 18: every run and every mutant's inner run wrote
to its own file. Load 10.39 15.51 14.82 at the start, 16.28 15.37 14.83 at the end (the other
project had a full run going); no test of mine asserts on the wall clock. Source as committed
afterwards; probe files removed; both test databases dropped. One run, nothing repeated.

- Step 1, the two saved-answer test modules at 6ec5af66: "Ran 68 tests in 2.487s", OK.
- Step 2, my probes once, unchanged since my first run (12 tests) plus slice A's probe P6:
  "Ran 13 tests in 13.830s", OK.
- Step 3, 17 mutants, each running only a probe module of mine, the probe tests expected to
  fail written in the runner beforehand: 15 KILLED, 2 SURVIVED, 0 BROKEN. Every inner run has
  its own "Ran" line and no load failure.

| Mutant | What is broken | Result | Probe tests seen red |
|---|---|---|---|
| X1 | a random value is added to the key | KILLED | PB1 long-paper reuse; PB6; PB7 missing/None/empty; and five more |
| X2 | status and notes are one part of the key | KILLED | PB2 status against notes |
| X3 | text and status are one part of the key | KILLED | PB2 text against status |
| X4 | a chunk's reply keeps its own markers | KILLED | PB3 |
| X5 | an unnamed maker is given a guessed model | KILLED | PB4 |
| X6 | the envelope names the intended model | KILLED | PB5; PB9 (its model assertion); PB3 |
| X7 | the key takes the teacher's raw text | KILLED | PB6 |
| X8 | the status is left out of the key | KILLED | PB7 each field (the `answer_status` case); PB7 blank against not found |
| X9 | the notes are left out of the key | KILLED | PB7 each field (the `transcription_notes` case) |
| X10 | the confidence is put into the key | KILLED | PB7 each field (the `confidence` case) |
| X11 | the page is put into the key | KILLED | PB7 each field (the `source_page` case) |
| X12 | the answer's own question text is put into the key | KILLED | PB7 each field (the `question_text` case) |
| X13 | None is not the same as empty | KILLED | PB7 missing/None/empty |
| X14 | the store takes a fresh reading of the settings | KILLED | PB8, at its LAST assertion only (a later identical paper made 3 provider calls, not 0) |
| X17 | slice A's P6: the settings version depends on the interpreter's hash seed | KILLED | P6 (two different versions from the two interpreters) |
| X15 | a reused answer is stored again | **SURVIVED; I expected PB9 to fail** | none |
| X16 | the chunk call's splice ignores the run's reading | SURVIVED, as predicted | none: this is the break PB8 was said to catch |

What this means for each probe of my first record:
- **Seen red, evidence stands:** PB1, PB2 (both tests), PB3, PB4, PB5, PB6, PB7 (all three
  tests, and each of the five field cases separately), and slice A's P6.
- **PB8, struck in part.** Its statement that every prompt of the run carries the teacher's
  text is STRUCK: X16 shows the assertion cannot fail. Its statement that the answers of a run
  are filed under the reading the run started with stands (X14). The struck property is now
  held by the author's corrected test with its own two kills (item 1).
- **PB9, struck in part.** Its statement that exactly one answer is stored when a paper is part
  reused and part fresh is STRUCK: X15 survived. Reading the code after the run: the pipeline
  hands the store only the freshly graded evaluations, so the "skip a reused answer" line I
  broke is a second defence that this path never reaches, and my mutant changed nothing the
  probe could see. That is my reading, not a result. What stands from PB9, seen red under X6:
  the stored answer names the model that answered, and the reused answer keeps its maker.
  Not re-run, as the grant required.
- Nothing struck carried the verdict. The "one reading per run" property rests on the author's
  tests (R1, R2, R3, S1 and now P1, P2 killed). That a reused answer is not stored a second
  time was not a ground of the verdict; it holds by reading only (the store is handed the
  freshly graded evaluations) and no test of anyone's has been seen red for it.

## My first record on the branch

`docs/evidence/epic-i-be-i-04-b/VERIFICATION_be_i_04_slice_b.md` at 6ec5af66: `cmp` against my
file reports no difference (sha256 b49ba92c74eee1ce5ff1e05e33bab9b6fd9118017a53833bc7059891e59f8a4b).

## Notes (not required)

- The author's mutant list has no break for "a reused answer is stored again" on the pipeline
  path either; the line is a second defence. Worth one unit test of the store function alone,
  or a sentence, in slice C.
- Two tests of the saved-answer modules have still been red in no run: the kill-switch pipeline
  test and the chunked fixture guard.

## Files of this check (kept outside the repository)

Under `~/Documents/Projects/`; sha256 of each:

```
6bbe0126ae21fe4584651c2f26fad4118d9cb787478b089ae58ba467006182b8  GAP-0c-runs/be-i-04-b/logs_probe_proofs/console.txt
9bb0c0816147c745d8052d5e745c2dc1a03c79b11599143922e2b372890b338b  GAP-0c-runs/be-i-04-b/logs_probe_proofs/1_changed_modules.txt
77a57a2c2de004e6f287776b2443126c650d92f32dc3d372b7610778dfaf73a0  GAP-0c-runs/be-i-04-b/logs_probe_proofs/2_probes.txt
0d8b183acd3296a30a7381509a49512ef6b95cf9dd465021545ab8af673b2b8e  GAP-0c-runs/be-i-04-b/logs_probe_proofs/3_mutation_log.txt
45b974915dc92db52e5b341dbf643a41cd97f69d9586bfe3a4924699b3875906  GAP-0c-runs/be-i-04-b/logs_probe_proofs/mutants/mutation_results_0c_probes.json
da188dbb282bc018467106e70a3745d68fe98cbad20ab1a4e9350b012bc5a10a  GAP-0c-runs/be-i-04-b/run_0c_probe_proofs_b.sh
51ac5566ef4cd4daa8a64f9ee31444d7c1a8fc6398d30bc9218dee8e6114ffde  GAP-0c-runs/be-i-04-b/mutate_0c_b_probes.py
308457eebce9e168f9234e9a8e9171577b5ac6f44738efebe4a0fe37ac569e5a  GAP-0c-runs/be-i-04-b/tests_0c_probe_be_i_04_b.py
074db512cfca3fc72c15d4c2a38ef32fe99fb67533c5a732d9c57fcf4e05203c  GAP-0c-runs/be-i-04-b/tests_0c_probe_p6.py
```
