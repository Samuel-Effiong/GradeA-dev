# H-242: flag an upload that does not match its assignment, at answer extraction

Reviewer note: a student's upload that does not match the assignment (an
essay for another subject, answers to another paper, answers in the wrong
form, a page the reader could not read) must be flagged "Needs Review"
with a plain message, not reported as "answer not submitted". The answers
must still be extracted. The flags must show on the assignments tab, the
AI feedback tab, the top of the full submission view, and the student's
side. The owner clarified that this happens at **answer extraction from
the upload**, not at grading.

Branch `task/h242-submission-mismatch-flags`, rebased onto beta `42d564b4`;
tip `73d929a0`.

## What changed

### The reader reports what it noticed

- **Schema** (`ai_processor/extraction_schemas.py`).
  - Each answer gets `answer_flag` (`NONE` / `OFF_TOPIC` / `TYPE_MISMATCH` / `READING_PROBLEM`) and `answer_flag_detail`.
  - The paper gets `submission_matches_assignment` and `mismatch_detail`.
  - All are required in the strict schema.
- **Prompt** (`ANSWERS_EXTRACTION_PROMPT_HTML_4.txt`).
  - Off-topic writing is still the student's work and is transcribed, never marked not found.
  - A flag never moves an answer: numbered answers stay under the student's number.
  - Only an unnumbered single piece of work goes under the first question.
  - A not-found status must agree with the reader's own notes.

### One place turns the report into flags

- **`ai_processor/extraction_flags.py`** (pure Python) turns the reader's report into the stored flags.
  - **Flag kinds:** `SUBMISSION_MISMATCH`, `OFF_TOPIC`, `TYPE_MISMATCH`, `READING_PROBLEM`, `ANSWER_NOT_FOUND`.
  - **Severity:** each kind has a tier and a plain-language message. The mismatch, not-found and reading kinds are critical; off-topic and wrong-form are moderate.
  - **Multi-part papers:** a long paper read in parts is a mismatch only when every part says so.
  - **Cross-check:** a mismatch verdict is dropped when even one answer is on topic (seen live: a maths paper with a right Q3 was called "another subject").

### Stored, queued for review and shown

- **Stored and queued** (`students/services.py`, migration `0035`).
  - Flags are stored in `StudentSubmission.extraction_flags` at upload and at a typed-text edit. A new reading replaces them.
  - They are filed in the review queue (`needs_review`, `review_reasons`, tier) when the upload is read, and again when it is graded, without duplicates.
- **Shown** (serializers). `submission_flags` and `flagged_for_review` appear on:
  - the submission list, detail and upload responses;
  - the assignment's per-student status rows (assignments tab);
  - the student's own assignment view.

  Students see the flag messages. They never see `needs_review` or `review_reasons` (H-127).
- **Grading.** An objective question whose answer was not found gives a student-facing message that does not reveal the model answer.

### The second reading (owner chose option A)

**The problem.** Seen live on a numbered English paper submitted to a maths assignment: the first reading returned Q2–Q4 as "not found", even though its own notes said the student wrote under them. The blank re-check found the writing. But its fragment is evidence only and never becomes the answer, so the teacher saw empty answer boxes.

**The fix.** `AIProcessor._retranscribe_found_answers` makes **one more reading call**, but only when the re-check found writing.
- It uses the ordinary reading prompt and schema, with a note asking for those questions alone.
- A recovered answer replaces the empty one and stays flagged. It keeps its own flag if it raised one; otherwise it is flagged `READING_PROBLEM` (critical). Either way it carries the note "The first reading missed this answer and a second reading recovered it: check it against the upload."
- The question keeps its assignment label, the re-check evidence, and `retranscribed: true`. The reading label credits the second reading's reply.
- Non-fatal: a failure, a reply with no writing, or a reply about other questions changes nothing. A cancellation is still raised.
- Its note's version is part of the reading's settings version (`RETRANSCRIPTION_INSTRUCTION_VERSION`).

**Re-check wording.** The re-check used to ask for writing "responding to" each question. Live run 1 showed it then calls an English paragraph under number 2 of a maths paper "nothing". It now asks for any writing "in that question's place … WHATEVER it is about".

## Gate 1: tests and regression

- `students/tests_h242_submission_flags.py`: 31 tests. They cover the flag rules, storage, the review queue at upload and at grading, the serializers and the student-facing fields.
- `ai_processor/tests_h242_retranscription.py`: 31 tests. They cover cost (no call unless the re-check found writing), what is sent, recovery, flags, labels, empty markup, unrequested questions, malformed replies, failures and cancellation, the re-check wording, and the order inside `extract_answer_with_retry`.
- `ai_processor/tests_reading_config.py` gains a test that the second reading's note version is part of the reading version. `ai_processor/tests_ai_call_trace.py` registers the new call's prompt version.
- Pinned key lists were extended: `students/tests_student_upload_answer.py` and `AutoGrader/tests_cache_bespoke_1114.py`.
- Full suite on `8467571e` (flags, before the second reading): **7974 tests, OK (skipped=30)**.
- Final gate on the committed tip `73d929a0`: **8014 tests, OK** (see
  "Final gate" below).

## Gate 2: mutation

Every mutant was restored from `git show <commit>:<path>` and checked by sha256. All restores matched.

| Chain | Commit | Result | File |
|---|---|---|---|
| Flags, first run | `8467571e` | 28/30 killed. K8 and K17 survived; tests added | `mutation_flags_first_8467571e.json` |
| Flags, re-run | `62072acf` | **30/30 killed** | `mutation_flags_62072acf.json` |
| Second reading | `8ac6dbf1` | **24/24 killed** | `mutation_second_reading_8ac6dbf1.json` |

These commits are from before the rebase. The rebase onto beta changed only beta's own dashboard files (`git diff --stat 8ac6dbf1 1d78bee9` lists only `dashboard/*` and the cache coverage test), so none of the mutated files differ.

## Gate 3: real AI calls (billed)

Probe: five synthetic one-page PDFs uploaded to a four-question maths assignment through `upload_answers_engine`. The probe script is not committed.

### The flags (`live_five_papers_flags.txt`)

| Paper | Result |
|---|---|
| P1: unrelated essay | Mismatch, off-topic Q1, not-found Q2–4, critical |
| P2: numbered English answers | Mismatch, 4 off-topic, critical |
| P3: wrong form for the question type | Wrong-form flags; the mismatch is held against the on-topic Q3 |
| P4: one recipe answer | Off-topic Q4, moderate |
| P5: degraded scan | No flags (it still read cleanly) |

The flags survive grading.

### The second reading (`live_second_reading_runs.json`)

| Run | Code | Result |
|---|---|---|
| run1 | old re-check wording | Reader put all four paragraphs under Q1. The re-check confirmed Q2–4 blank. This led to the wording fix. |
| run2 | second reading | The real miss happened. The re-check found writing; the second reading recovered all 3 in full. |
| run3 | second reading | The first reading got all 4. No extra calls. |
| forced1, forced2 | final | Q2–4 forced to not found with "no writing located" notes. The re-check found writing; the second reading recovered 3/3 both times. |
| essay1, essay2 | final | The re-check correctly confirmed Q2–4 empty. The new wording does not invent answers. |
| natural4 | final | All 4 read first time. |

**Known limit.** If the first reading still puts every paragraph under Q1 (run1's shape), Q1 keeps them all while Q2–4 get their own text. The text then appears twice. Nothing is lost and the paper is flagged critical.

## Final gate

Run in a detached worktree at the committed tip, on a fresh test database
(`test_h242_gate`, no `--keepdb`), under `systemd-inhibit`, with the
unfiltered log kept in the scratchpad; its result lines and sha256 are
in `final_gate_73d929a0_excerpt.txt`.

- First run on `1d78bee9`: **8014 tests, 1 failure**.
  `students.tests_reading_label_static` pins the number of answer-reading
  calls that must sit inside a reading scope at 3. The second reading is a
  fourth, and it is inside its scope. The count was raised to 4 in
  `73d929a0`.
- Re-run on `73d929a0`: **8014 tests, OK (skipped=30)**.
  Tree fingerprint (`git ls-files -s | sha256sum`): `c8f0b325be1c59ac`.
