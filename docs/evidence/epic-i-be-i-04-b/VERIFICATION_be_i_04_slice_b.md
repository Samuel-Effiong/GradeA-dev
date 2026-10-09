# Verification record: BE-I-04 slice B (when a saved AI answer may be reused)

Verifier: the Next-stage Checker (reserve engineer 0c), independent of the author.
Author: the Next-stage Builder. Branch `task/epic-i-be-i-04-b`, base `phase2/epic-a` cfe55a0f.
Verified tip: **728491a2** = 6ca94c09 (the code as gated and as I ran it) plus one evidence-only
commit. Written 2026-10-06. Clock times are WAT, read from the clock or from the logs.

## Verdict

**VERIFIED-WITH-NOTES at 728491a2** (2026-10-06 18:18 WAT), with three items required before the
merge (section "Required before the merge"). All three are tests: none is a fault found in the
code the slice ships. I check that one delta and nothing else.

## What I did

| | What | Result |
|---|---|---|
| Reading | The whole diff cfe55a0f..728491a2, the tests, the evidence, the runner, every log | Section "By reading" |
| Run, step 1 | The three changed test modules once at 6ca94c09 (rule 15: nothing wider repeated) | Ran 94 tests, OK |
| Run, step 2 | Twelve probes of my own, expected results written before any run | Ran 12 tests, OK |
| Run, step 3 | Thirteen mutants of my own, expected failing test (or "predicted survivor") named before any run | 11 KILLED, 2 SURVIVED as predicted, 0 BROKEN |
| Full suite | The Release Engineer's one run on 6ca94c09; I read the raw log myself | Ran 6496 tests, OK (skipped=30), no FAIL or ERROR |
| Pattern check | Credential pattern check over the 85 files the slice changed, inside the 63 gzipped logs, and the .xz log unpacked by hand | No URL with a password position filled; the two known test stand-in lines only |

Not repeated (rule 15): the author's 627-test run, the author's 34 mutants, the full suite.
Not done: a whole-tree credential scan.

## How this slice reached this tip

I first read the slice at 59990797. One finding there was a gap in the code, not in a test: the
whole answer object is sent to the AI (text, `answer_status`, `transcription_notes`,
`source_page`, `confidence`, the answer's own `question_text`), and the key held the text
alone, so two students with the same text and a different status or different notes shared one
saved grade. The Senior Manager ruled that the key also takes `answer_status` and
`transcription_notes` and that the other three fields stay out as a stated limit; the author
made that delta (red tests first) before any full run. Three test gaps I found in the same
reading were passed to the author by the Senior Manager and are covered by the author's own
tests at this tip. What follows is the state at 728491a2.

## By reading

Checked and correct:
- Tests first, truly: 17890114, 831f4c26 and 88884176 change the one test module and the
  evidence only; the code is in 2401d461 and c239131b. One test was added with the first code
  commit and declared beforehand.
- Red runs: at the first tests-only commit, Ran 32 with the 25 named tests failing; at the
  delta's, Ran 53 with the 6 named tests failing.
- The 627-test run holds its own "Ran" and "OK" lines, no FAIL or ERROR header, no skip. All 37
  gzipped logs match `gzipped_logs_sha256.txt`. Each of the 34 mutant logs has its own "Ran"
  line, its expected test among the failing ones and no load failure; the tests were last
  changed before the battery ran.
- The older tests of the saved-answer module moved to the new call signatures through two
  small helpers; no assertion line changed (the Senior Manager asked me to compare).
- The key: version two; the whole question as serialised; the answer's text, status and notes
  through one helper used by the lookup and by the store; the assignment's title and
  instructions; the teacher's extra instructions as spliced; the prompt version; the settings
  version; the intended model. The parts are hashed as one JSON list. The release is not in it.
- One reading per run: started above the retry loop, used by the lookup, the store and the
  teacher-instructions splice at all three prompt sites.
- The reply's own "graded by" and "from cache" are overwritten at both places a reply is
  parsed; the store names the model our code read; anything under a key that is not our
  envelope is a miss.
- The temperature is a named constant, used by the provider call and part of the settings
  version; the pin changed for that reason and no grade carries a version yet.
- Stated limits are in the module, the evidence and document 03a: not the other questions, the
  other answers or the batch position; not `source_page`, `confidence` or the answer's own
  `question_text`, with the reason.
- The author's decision on the two new fields (missing, None, empty and whitespace-only are one
  and the same; outer whitespace of the notes is not compared; a value that is not text is
  serialised, never refused): sound. Four spellings of "nothing said" are one meaning, and a
  grading must not fail over the key.
- 728491a2 differs from 6ca94c09 in four files, all in the evidence folder; the committed .xz
  unpacks to the sha256 of the raw full-run log.

Findings:
- **F1. One new test cannot fail:**
  `test_the_last_part_of_the_context_and_the_answer_cannot_run_together`. In the key the
  question lies between the teacher's instructions and the answer, so the two keys it compares
  differ even with nothing at all between the parts. The answer's real neighbours are the
  question before it and the status after it.
- **F2. The answer side's boundaries have no test.** My mutant W19 joins the status and the
  notes into one part of the key: every test of the author's still passes. With it, a status
  "ab" with no notes and a status "a" with notes "b" are one key. My probe PB2 fails under it.
- **F3. Two of the three prompt sites are not held to the run's reading.** My mutant W12 makes
  the chunk call's teacher-instructions splice read the live switch again: every test of the
  author's still passes (the one test of the splice calls the helper directly). The property
  matters for this slice: the key says "with the teacher's text as spliced", so a chunk that
  spliced under another reading would file an answer under a key that does not describe it. The
  code is right at 6ca94c09 (my probe PB8 shows it); nothing guards it.
- **F4. Tests never seen red.** Before my run eighteen tests of the two saved-answer modules had
  been red in no run; the evidence names two. After my mutants three remain: the fixture guard
  for the chunked path, `test_kill_switch_restores_a_fresh_call_every_time`, and the test of
  F1.
- Notes for the stated limits, not faults: an image is matched by its address, not its content;
  the key's intended model is the module constant read at the time of the call, not from the
  run's reading (it is inside the settings version too, so a change still misses).

## My run

One slot from the Release Engineer, 2026-10-06 18:10:44 to 18:16:36, in my own detached
checkout of 6ca94c09 (`~/Documents/Projects/GAP-0c-scratch/be-i-04-b`), never the author's
worktree. Serial, 6G scope, rules 12, 13, 16, 17, 18: every run and every mutant's inner run
wrote straight to its own file; nothing was piped. Load 3.14 5.16 5.74 at the start, 14.42
13.70 9.58 at the end; no test of mine asserts on the wall clock. Before asking for the slot I
proved the checkout starts (`manage.py check`, no issues, both settings files). The source was
as committed after the probes and after the mutants; both test databases were dropped. One
run, nothing repeated.

### Step 1: the three changed modules

`ai_processor.tests_grading_cache`, `ai_processor.tests_grading_cache_key_v2`,
`ai_processor.tests_grading_config`: exit 0, "Ran 94 tests in 3.302s", "OK".

### Step 2: probes (all as expected)

| Probe | What | Result |
|---|---|---|
| PB1 | A 12-question paper graded twice through the pipeline | First time 3 provider calls (two chunks and the summary); second time none; all 12 answers marked as reused, naming the model that made them |
| PB2 | Status and notes, and text and status, cannot run together in the key | The keys differ in all five cases |
| PB3 | A chunk reply carrying its own "graded by" and "from cache", answered by a backup model | Every evaluation names the backup model and has no "from cache"; all 12 stored envelopes name the backup model |
| PB4 | A stored envelope whose maker was not named is reused | No provider call; marked reused; "graded by" is the not-named word |
| PB5 | First student answered by a backup model, second identical student | One provider call in all; the second student's answer says the backup model made it |
| PB6 | No teacher instructions written as None, empty, whitespace, or a missing attribute | One provider call in all |
| PB7 | The Senior Manager's order: two students, same question and answer text, all six answer fields present, ONE other field differing at a time | `answer_status` differs: fresh grade. `transcription_notes` differs: fresh grade. `source_page` differs: reused. `confidence` differs: reused. The answer's own `question_text` differs: reused. As ruled |
| PB7 | Both texts empty, one "blank", one "not found in the document" (at the store's own functions) | A miss; the same status again is a hit |
| PB7 | The two new fields missing, None, empty, whitespace-only | One provider call in all, as the author decided |
| PB8 | A long paper; inside the first chunk's call a version-moving setting changes and the teacher-instructions switch goes off | All three prompts of the run still carry the teacher's text; a later identical paper under the starting settings makes no provider call |
| PB9 | A paper with one answer reused and one fresh (the fresh one answered by a backup model) | Exactly one answer is stored, naming the backup model; the reused one keeps its maker |

### Step 3: mutants

| Mutant | What is broken | Result | Expected test, found among the failing |
|---|---|---|---|
| W1 | a random value is added to the key | KILLED | `test_the_same_submission_again_is_reused` |
| W3 | a disputed answer is stored | KILLED | `test_disagreed_evaluation_is_never_cached` |
| W4 | the store's off switch is ignored by the store's own functions | KILLED | `test_disabled_never_stores_or_hits` |
| W5 | a write error is not swallowed | KILLED | `test_backend_error_on_write_does_not_raise` |
| W6 | a read error is not swallowed | KILLED | `test_backend_error_degrades_to_a_miss_not_a_crash` |
| W7 | the answer's outer whitespace is no longer removed | KILLED | `test_answer_whitespace_edges_are_ignored` |
| W8 | the intended model is left out of the key | KILLED | `test_miss_on_different_model_name` |
| W9 | the lifetime is not passed to the store | KILLED | `test_store_uses_the_configured_ttl` |
| W10 | a reused answer is not marked as reused | KILLED | `test_cached_evaluation_is_not_selected_for_a_fresh_second_opinion` |
| W16 | the answer's own copy of the question text is put into the key | KILLED | `test_the_answers_own_copy_of_the_question_text_does_not_break_the_match` |
| W17 | the answer's confidence is put into the key | KILLED | `test_a_different_page_and_confidence_do_not_break_the_match` |
| W12 | the chunk call's prompt splice ignores the run's reading | SURVIVED, as predicted | none expected: finding F3 |
| W19 | the answer's status and notes are one part of the key | SURVIVED, as predicted | none expected: finding F2 |

All thirteen inner runs have their own "Ran 69 tests" line and no load failure; the eleven
kills exited 1, the two survivors exited 0. Numbers W2, W11 and W13 to W15 of my first list
were dropped before any run because the author's delta added mutants for the same breaks.

## The full suite (the Release Engineer's run, read by me, not repeated)

`~/Documents/Projects/GAP-0b-runs/gate10_slice_b_b1.log`, 8,677,854 bytes, sha256
`fdcc7d6dc898d1736f505d06815f4dfb5d02f6385e66be455337a3d9b4e60993` (computed by me; equal to
the Release Engineer's and to the committed .xz unpacked). One run at `--parallel 4` on
6ca94c09, 18:01:40 to 18:09:47, exit 0.
- Exactly one "Ran" line: "Ran 6496 tests in 454.078s"; result line "OK (skipped=30)"; after it
  only the five "Destroying test database" lines.
- FAIL or ERROR headers in the whole log: 0.
- The 30 skips, by their own reasons: 12 real AI calls, 9 load tests, 4 network, 1 Redis,
  2 audit benchmarks, 2 that cannot fork inside a parallel worker. None is a test of this slice.
- The slice's test module is in the run. Per the summary: watchdog never fired; makemigrations
  clean; mypy passed.

## Pattern check

By program, values never printed. The 85 files changed in cfe55a0f..728491a2, the 63 gzipped
logs opened by the tool, and the one .xz log unpacked by hand because the team's check does not
open .xz yet: no URL with a password position. Two literal hits, both in the full-run log: the
word "secret" in a test's blocked-fetch warning for a made-up host, the same two lines as in
slice A's and the batches' full-run logs, judged a test stand-in and accepted by the Senior
Manager on 2026-10-06. Every other match is code text. My own record: no URL and no secret-named match; two matches
in the tool's counted-only class for names holding just the word "key" (my own prose about the
saved-answer key).

## Required before the merge (one delta; I then check the delta only)

1. A committed test that fails when the chunk call's (and the summary call's) teacher-
   instructions splice stops using the run's reading (F3; my mutant W12 is the break to kill).
2. A committed test that the answer side's parts cannot run together: status against notes, and
   text against status (F2; my mutant W19 is the break to kill).
3. `test_the_last_part_of_the_context_and_the_answer_cannot_run_together` made able to fail,
   or removed, with the evidence saying which (F1).
Each new test needs its own red proof: the test's commit first with a red run, or a mutant
with the expected failing test named first.

## Notes (not required)

- F4: `test_kill_switch_restores_a_fresh_call_every_time` and the chunked fixture guard have
  still been red in no run.
- The stated limits could also say that an image is matched by its address.

## Files of this verification (kept outside the repository)

Under `~/Documents/Projects/`; sha256 of each:

```
7c6b9011d0742f871d588845aa1a7d45370b3bb9ab458572a8deadcb2b65c1bc  GAP-0c-runs/be-i-04-b/logs/console.txt
8ecc589613d5a5872ab14d585d522b540f348ca250b5cda87fb8134abc546de9  GAP-0c-runs/be-i-04-b/logs/1_changed_modules.txt
0e7967988ebcb517211f108b5123dc31be9592ece9b37597b6d4f4ba35f2dbff  GAP-0c-runs/be-i-04-b/logs/2_probes.txt
0e322ecead0a2d0b70ac0d144024c49400c9f9ac04668a01fa16f0bb950a5130  GAP-0c-runs/be-i-04-b/logs/3_mutation_log.txt
21bd21e917379627f1c34a9953c2611fffb14ab0c9e8d1bd39e38863eb726f9e  GAP-0c-runs/be-i-04-b/logs/mutants/mutation_results_0c.json
308457eebce9e168f9234e9a8e9171577b5ac6f44738efebe4a0fe37ac569e5a  GAP-0c-runs/be-i-04-b/tests_0c_probe_be_i_04_b.py
f3c2dc7d77582441a7d626fd93d80a8678a67cc9118839dbbd60b8e2112d6e36  GAP-0c-runs/be-i-04-b/mutate_0c_b.py
c1b59780a31b50792f3a2dd5c68160c377bd4a6bb3da33834ed8ed9e45ddde2c  GAP-0c-runs/be-i-04-b/run_0c_verify_b.sh
```
