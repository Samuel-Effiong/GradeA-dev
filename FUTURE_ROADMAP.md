# Future Roadmap

Deferred work items, kept here so they don't get lost between sessions. Add
new items with the date they were identified and enough context to pick the
work back up cold.

---

## Stable per-question identity for grading (deferred 2026-08-22)

**Identified while fixing:** MCQ option letters being duplicated on
assignment download (e.g. "A. A. $x=5$"). See the root-cause writeup in that
session for the full trace; the short version is that editing an assignment
sends the whole document back through an AI "extract this document into
structured JSON" call, which fully replaces `Assignment.questions` with no
merge.

**The gap:** grading links a student's submitted answers/feedback to a
specific question only by `question_number`
(`ai_processor/services.py:_question_number_key`, used at 15+ call sites).
`question_number` is reassigned sequentially on every re-extraction
(`ai_processor/services.py:821,980`), so an edit that reorders, adds, or
removes a question can silently relink an existing student's answer to the
wrong question. Nothing currently detects or prevents this.

**What shipped instead (2026-08-22):** rather than blocking edits or trying
to solve linkage precisely, every student who has already submitted on an
assignment now gets a plain notification email whenever the assignment goes
through the AI re-extraction edit path
(`students.services.notify_students_of_assignment_edit`, called from
`AssignmentProcessingService.update_assignment_from_extraction`). It's
blanket, not targeted — it doesn't know whether that specific student's
answered question was actually affected, just that the assignment changed
after they submitted.

**Deferred, in two stages:**

1. **Stable `question_id` (UUID) per question, with content-matching on
   re-extraction.** Assign an id once, never regenerate it. After each
   re-extraction, match the old and new `questions` lists by content
   similarity (`difflib.SequenceMatcher` is enough, no new dependency) to
   carry the id forward for questions that didn't substantively change.
   This is the foundation for making the edit notification precise (only
   notify students whose specific answered question was actually affected,
   instead of everyone who submitted) and for stage 2.
2. **Migrate grading to key off `question_id` instead of `question_number`.**
   A real migration-scale project: 15+ call sites in
   `ai_processor/services.py` (`_question_number_key` and its callers), the
   submission `answers`/`feedback` JSON shape (`students/models.py:41,78`),
   the grading/extraction AI prompts (which currently ask the model to key
   output by `question_number`), and a compatibility path for submissions
   that predate `question_id`. Don't attempt this without planning it as
   its own project.

## Frontend/Tiptap schema redesign (identified 2026-08-22)

The assignment editor (Tiptap) produces a freeform ProseMirror document with
no custom node types for questions/options — a heading merely *looks like* a
question boundary, a lettered paragraph merely *looks like* an option. That
convention is why every edit currently has to go through an AI call to be
turned back into structured `Assignment.questions` at all.

Giving Tiptap custom node types (e.g. a `question` node with
`questionNumber`/`points`/`type` attrs, an `option` node with an `optionIndex`
attr) would let the backend deterministically parse an edit without any AI
call — eliminating the letter-duplication bug class at the root, removing
the AI cost/latency on every edit, and removing the question-identity churn
described above entirely (the id would just be a node attribute that
survives editing). This is the "truly correct" long-term fix, but it's
cross-stack: it needs a Tiptap extension on the frontend and a matching
schema + reverse-converter on the backend (mirroring
`assignments/prosemirror_converter.py`, which currently only goes
HTML → ProseMirror JSON, one direction). Needs frontend involvement to scope
properly — not something to start unilaterally from the backend.
