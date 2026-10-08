# Plan: one real paid check of a sectioned paper (follow-up row H-171)

d5, 2026-10-07 18:27. For the Senior Manager's approval BEFORE any call.
The user approved one real paid AI run. Nothing has been run. The
Release Engineer grants the slot.

## The question
H-158 stores an assignment's questions as 1..N. A paper whose own
numbering repeats (Section A: 1, 2; Section B: 1, 2) is stored as 1, 2,
3, 4, while a student's sheet says "Section B, 1". Does the answer
reader put that student's answer under question 3? No test of ours can
show it without the real model.

## Data: made up, all of it
- One assignment, typed text: a title, "Section A" with questions 1 and
  2, "Section B" with questions 1 and 2. Four short factual questions I
  write, each with an answer nobody could confuse with another's.
- One sheet, typed text, labelled the same way ("Section A 1.", ...,
  "Section B 2."), four short answers, each plainly belonging to one
  question, with the two "1"s and the two "2"s NOT in an order that
  position alone would get right (Section B written first).
- The student is a made-up account in a test database, with a made-up
  name. No real person's name, e-mail or work anywhere.

## Where and how it runs
- On H-158's code at its gated tip (`7f5d1743` today), in a scratch
  worktree; the probe file is mine, kept in `GAP-d5-runs/dupq/`, never
  committed to the branch.
- As ONE opt-in test, the way the project's existing paid tests run
  (`assignments/tests_real_extraction.py`: `RUN_REAL_AI=1`, a test
  database that is rolled back, a made-up teacher given a subscription
  and credits there). Under the usual wrappers: 6G cap, sleep inhibited,
  timeout, output to a file. It is a test run: it needs the Release
  Engineer's grant like any other, and it runs alone.
- Not on production, not on beta's database, no deploy.

## Exactly which calls
Both go through the product's own entry points, as a teacher's and a
student's requests do:
1. **Reading the assignment:** `AssignmentProcessingService.extract_assignment_data`
   on the typed text. One provider call in the ordinary case.
2. **Reading the sheet:** `ai_processor.extract_answer_with_retry` on the
   typed sheet, with the assignment just saved. One provider call in the
   ordinary case.
**No grading call.** The mapping is seen from the reader's output alone.

**Expected: 2 calls. Hard stop: 4.** Every provider call of this code
leaves through one function (`AIProcessor.execute_graded_task`). The
probe wraps it with a counter that lets the real call through and
raises before a FIFTH call would leave. The margin of two is for what
one normal request may itself do (the product's own retry of a reply it
rejects, and the reader's second look at answers it found blank). I add
no retry of my own; if the run fails, it is not repeated without your
word.

**The model:** whatever the settings name. By NAME of the setting only:
the code's `MAIN_MODEL` with its `DEFAULT_FALLBACK_MODELS`. I do not
choose or change a model.

## What is recorded
- For each of the four sheet answers: the question number the reader
  put it under, beside the stored question it belongs to (a table of
  four rows, made-up text only).
- The stored assignment's four question numbers and, from H-158's new
  key, the model's own numbers for them (what the model wrote: "1, 2,
  1, 2" or "1, 2, 3, 4" or labels).
- The model's name as each reply reports it; the number of calls; the
  credits the test account was charged (test database).
- The run's raw log, checked with the credential pattern before it is
  kept or committed.

## What is NOT recorded
The key; any request or response header; the full request body. The
probe prints none of them and logs at the product's ordinary level.

## The key
The run uses the key the project's settings already read from the
environment file, exactly as the existing paid tests do. I do not open
that file, print the variable, pass it on a command line or copy it;
the probe never names it. If the key is absent or refused, the run
fails closed with the product's own error and I report that sentence.

## The two outcomes, and what each means for H-171
- **Every answer lands under its own question** (Section B 1 under
  stored 3, and so on): the reader copes with a sectioned paper by
  content and position. H-171 stays LOW; my proposal would be to close
  it as "checked once, on made-up data, with the model named", with the
  limit that one paper is one paper.
- **An answer lands under the wrong question, or two under one**: the
  fault is real. H-171 is raised (MEDIUM by my reading: a student's
  answer graded against another question), and its cure is designed
  then: the reader is given each question's own label from H-158's key.
  That cure is a prompt change and would itself need a paid check.
- Either way: one run, reported with the table, and no second call
  without your word.

## Order
After H-158's regression is green and Verifier 2 has it; your approval
of this plan; the Release Engineer's grant; then the one run.

## Approved by the Senior Manager, with four conditions (added 18:29)
1. **The hard stop is at the 4th call** (three may leave, a fourth is
   refused), not the 5th as written above. By reading, three is the most
   an honest run needs: one call for the assignment, one for the sheet,
   and at most one for the reader's second look at answers it found
   blank (`ai_processor/services.py`, the blank re-check makes a single
   call, and only over scanned pages; my sheet is typed and has no
   blank). A retry of a rejected reply would be a fourth: the run stops
   there and I report it instead.
2. `--settings=settings_worktree` and the scratch worktree's own test
   database; nothing shared. No outbound call other than the
   provider's: the probe watches every outbound connection the test
   process opens, lets through the loopback ones (the local database
   and cache) and the provider's host, refuses any other, and the
   report gives the count of refused ones (expected 0) with their host
   names.
3. It runs AFTER H-158's regression is green, on that same tip, so the
   code checked is the code that will ship.
4. The report holds, in plain words for the user: the four answers and
   where each landed; the call count; the credits charged in the test
   account; the model names as answered; and the sentence "one paper,
   one run: this shows what happened once, not what always happens".

No repeat without the Senior Manager's word. The Release Engineer reads
the probe and grants.
