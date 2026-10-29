# H-158: an AI reply's question numbers are made 1..N before anything is saved

Design note, d5, 2026-10-07. For the Senior Manager's ruling before any
test is written; Verifier 2 verifies. MEDIUM, second row of batch 13.
Reading only: nothing here was run. Line numbers are `220f9cd6`.

## The fault, in one paragraph
An assignment's questions are saved with the `question_number` the model
wrote. Nothing checks that the numbers are distinct. Grading pairs each
question with the student's answer BY that number
(`_pair_question_with_answers`, `ai_processor/services.py` 2639), so two
questions numbered alike are paired with the same answer, and the
evaluations that come back under one number are what H-154 had to settle
(the lowest is kept). The cure ruled: every AI path numbers the questions
1..N before the serializer sees them; the serializer refuses a repeat
only as a last defence.

## What I found by reading: two of the AI paths already do it
`ai_processor/services.py`, lines 1080-1082 and 1247-1249: both CHUNKED
extraction paths end with "Re-index all question numbers globally
(1, 2, 3 ... N)". The single-call extraction and the generation from a
prompt do not. So "always 1..N, in the order the model gave" is not a new
behaviour for the product: it is what a long paper already gets. The
prompts ask the model for the same thing ("Assign sequential numbers
starting from 1"; `question_number`: "Sequential integer starting from
1"). This row makes the short paper and the generated one the same as
the long one. That answers the question I would otherwise have put to
you (renumber always, or only on a repeat): **always**, as the chunked
paths do; a rule that acts only on a repeat would leave two behaviours.

## What changes

### 1. One function
`assignments/services.py`: `number_questions_in_order(questions)` returns
a new list in which each entry that is an object carries
`question_number` = its position, from 1, and the count of entries whose
number changed. An entry that is not an object is left as it is (the
serializer refuses it; what that costs the teacher is H-159). The input
is not mutated: one caller passes a stored draft snapshot.

### 2. The three places, each before anything reads the numbers
| Path | Where | Before what |
|---|---|---|
| every extraction (upload, text create, text edit sync and background) | `AssignmentProcessingService.extract_assignment_data`, straight after `ai_assignment_content_only(...)` (line 863) | before `ai_raw_payload` is set (878), before the document is built (`generate_raw_input`), before the serializer |
| generation from a prompt | `_build_generated_assignment_draft` (`assignments/views.py` 1049) | before the draft's document is built and before the snapshot is stored |
| saving a draft generated BEFORE this row | the save route (`views.py` 1518) | before the document is rebuilt and before the serializer |

**Why before `ai_raw_payload`.** `AssignmentSerializer.update` compares
the stored `ai_raw_payload` questions with the teacher's version to set
`was_overridden`. If the stored AI copy kept the model's numbers and the
saved questions had ours, every renumbered AI assignment would count as
"overridden by the teacher" at its first edit. So the AI copy holds the
numbers as saved.

### 3. The model's own numbers: not kept under a separate key
You asked how the original number is kept. My proposal: it is NOT kept on
the question. A new key on a question is dropped by `QuestionSerializer`
(it has a fixed field list) or, if added there, changes a payload and
the "overridden" comparison. Instead, when any number changed, **one log
line**: the user id, the path, how many questions, how many numbers
changed. No question text. If you want the model's numbers kept, the
place is a new key beside `questions` inside `ai_raw_payload` (which is
write-only, never sent); say so and I add it.

### 4. The last defence
`AssignmentSerializer.validate`: when `questions` is sent and two entries
carry the same number (compared as grading compares them, through the
same normalising as `_question_number_key`: `3` and `"3"` are one
number), refuse with a plain sentence. After 1 to 3 no AI path can reach
it. By my reading no teacher route reaches it either: create and edit by
text go through `AssignmentTextSerializer` and then the AI. I will name
every route that hands `questions` to `AssignmentSerializer`, each with
its serializer, in the evidence, from the code and not from memory.
The user's word (via you): no stored assignment has a repeated number.

## How a student's answers map after the numbers change
1. **A new assignment:** nothing maps yet. The document students see is
   built from the questions after numbering (upload, generation), so the
   printed numbers and the stored ones agree.
2. **Create or edit by TEXT:** the stored document is the teacher's own
   text. If the teacher numbered the paper 5, 6, 7, the document says
   5, 6, 7 and the stored questions are 1, 2, 3. That is already so
   whenever the model obeys its prompt, and always so for a long paper.
   A student's uploaded answers are mapped to the official questions by
   the answers prompt, which uses the number, then the position, then
   the content (`ANSWERS_EXTRACTION_PROMPT_HTML_4.txt`, its decision
   tree at 216-219 and its example "Incorrect numbering (3, 5, 6, 10)
   corrected to sequential (1-4)"). So the mapping does not rest on the
   teacher's numbers. A limit, not a change: stated in the evidence.
3. **An assignment that already has submissions and is extracted again**
   (edit by text): stored answers carry the numbers of the questions as
   they were. If the old questions were not 1..N (a short paper where
   the model kept the paper's numbers) and the new ones are, an old
   answer under "5" pairs with no question at the next grading: every
   question is graded "answer not found" and the paper goes to the
   review queue (the existing `answer_not_found` reason). Nothing is
   lost or graded wrongly in silence, and students are already told the
   assignment was edited. The same happens today when such a paper is
   re-extracted and the model numbers it differently. **I do not propose
   to rewrite stored answers in this row.** Your ruling, question 2.
4. A question's text that refers to another by number ("as in question
   2") is not rewritten. A limit.

## Rule 20: cached answers
The assignment detail and list carry `questions`. For a reply already
numbered 1..N no payload changes. No key changes. AutoGrader's cache
tests join the regression; the existing test modules of the three routes
and of `extract_assignment_data` go into the chain's modules step, their
fixtures read first: I expect fixtures whose AI reply is numbered other
than 1..N and which assert the number back; each is listed with what
becomes of it before any slot.

## What H-159 gets from this
H-159 is the charge kept when the serializer refuses after the paid
call. With this row a repeated number no longer reaches the serializer
from an AI path, so that cause of a refusal is gone; the others (an
entry that is not an object, a missing field) remain and are H-159's.

## Questions for your ruling
1. The model's own numbers: a log line only (my proposal), or kept
   beside the questions in `ai_raw_payload`.
2. Re-extraction of an assignment that has submissions (point 3 above):
   leave as described and state it, or something more.
3. The last defence's sentence. Proposed: "Two questions have the same
   number (N). Each question needs its own number."
4. A non-positive or non-integer number from the model (0, -1, "2a")
   is replaced like any other: agreed?

## Tests, first, as their own commit (each new one seen red)
- the function: distinct 1..N unchanged and count 0; a repeat; a gap; a
  start at 5; strings; a missing number; a non-object entry left alone;
  the input not mutated;
- each of the three paths with a model reply holding a repeat: saved
  questions 1..N in the model's order, the document's printed numbers
  the same, and the numbers in `ai_raw_payload` equal to the saved ones.
  (Whether a first edit that changes nothing leaves `was_overridden`
  unset TODAY for a reply numbered 1..N I have not established: the AI
  copy holds the model's raw objects and the saved questions hold the
  serializer's, e.g. points as 5 against 5.0. I look before writing that
  test; if the comparison already differs for such reasons I say so, as
  a finding of its own, and test only the numbers);
- the draft save from a snapshot stored with a repeat (a row made by a
  queryset write, as an older draft would be);
- the serializer's refusal on a repeat, `3` against `"3"` included, and
  that a list numbered 1..N passes;
- grading such an assignment end to end: each question paired with its
  own answer;
- one mutant per condition and per path; expected sets written before
  any run; every assertion read against the real form of what it
  inspects, per test, in the evidence (as for H-165).

## Rulings (Senior Manager, 2026-10-07), added 17:13
The design is approved: ALWAYS 1..N in the model's order, at the three
places, before `ai_raw_payload` is set.

1. **The model's own numbers are KEPT**, inside `ai_raw_payload` under
   one separate key of ours: new number -> the model's number as it
   wrote it, as text, of bounded length. Not only a log line: they are
   the only trace of how the paper itself numbers its questions, and a
   later row may give them to the answers prompt. The key is sent to no
   client: the evidence names every serializer that could carry
   `ai_raw_payload` and shows that none does. It must not disturb the
   `was_overridden` comparison; my test decides, and the "5 against 5.0"
   point is reported as its own finding if it differs.
2. **Re-extraction of an assignment that has submissions:** left as
   described and stated, with the sentence in the evidence and in the
   package. No stored answers are rewritten here.
3. **The last defence's sentence** as proposed.
4. **Agreed:** a non-positive or non-integer number is replaced like any
   other.

**A limit to state in plain words** (the Senior Manager told the user it
is not verified): for a paper whose own numbering repeats (Section A 1,
Section B 1) the stored questions are 1..N, but a student's sheet says
"B 1". The answers prompt maps by number, then position, then content,
so whether that student's answer reaches the right question rests on the
model, and no test of ours can show it without a paid call.

**A follow-up row** (number from the Release Engineer; LOW until shown
otherwise): the answers prompt is given each question's original label
from the key in 1. It is decided after one live check with a sectioned
paper, which needs the founder's word for a paid call.

Tests first; the per-test "read against the real value" table from the
start.

## Two more rulings (Senior Manager), added 17:15
- **Where the model's own numbers are kept:** on the three EXTRACTION
  paths only, in `ai_raw_payload` under our key. Generation and the
  draft save set no `ai_raw_payload` today, and a draft's snapshot is
  sent to the client (`AssignmentGenerationMessageSerializer` lists
  `assignment_snapshot`): there a renumbering gets a log line (ids,
  counts, no question text) and nothing new is stored. The key never
  rides in a draft's snapshot.
- **A finding of its own, LOW, a row of its own (number from the Release
  Engineer), NOT in H-158:** the "teacher overrode the AI" flag never
  sets on rows made by today's code. The comparison in
  `AssignmentSerializer.update` needs `ai_generated` True AND a stored
  payload; extraction stores the payload with `ai_generated=False`,
  generation stores True with no payload. The dashboard counts the flag.
  A second copy of the check (`detect_ai_assignment_override`,
  `assignments/views.py` 506) calls a name that does not exist; I found
  no caller in that file. By reading only. H-158 keeps one test on a row
  built with both set: our key does not change the comparison.
