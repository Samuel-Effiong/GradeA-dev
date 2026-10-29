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

---

## Move rendered-PDF storage out of Redis into Cloudinary (identified 2026-08-25)

**Identified while:** capacity-planning the PDF cache added in `c37f75c` /
`2168d11` (`assignments/pdf_cache.py`).

**The problem:** rendered assignment PDFs are cached in Redis, but that Redis
is shared with Celery — `CACHES["default"]`, `CELERY_BROKER_URL` and
`CELERY_RESULT_BACKEND` all point at the *same instance and the same db
index* (`redis://.../0`, see `AutoGrader/settings.py`). The instance runs
`maxmemory-policy: noeviction` with `maxmemory: 0` (unlimited).

That combination is the risk: PDFs are a far heavier writer (tens of KB to
the 5MB cap) than the small JSON payloads Redis previously held, and under
`noeviction` a full Redis returns *write errors* rather than evicting. A
cache that fills the instance therefore doesn't just lose cached PDFs — it
stops Celery being able to enqueue grading runs, notification emails and
due-date reminders.

**Why a separate db index does NOT fix this** (measured, don't re-litigate):
Redis db indexes are logical namespaces over one shared memory pool. Writing
20MB into db 1 raised `used_memory` by 26MB as observed from db 0, and both
`maxmemory` and `maxmemory-policy` are single instance-wide settings with no
per-db equivalent. A separate index buys key-namespace separation and a
scoped `FLUSHDB`, and nothing at all for memory exhaustion.

**The fix:** store rendered PDFs in Cloudinary (already a project
dependency) instead of Redis, keeping Redis for small metadata only.

Why this is the preferred option over just splitting Redis into two
services:

- Removes the memory-contention question entirely rather than managing it,
  so cache growth can never take down background jobs.
- Retention becomes cheap, which is what makes pre-rendering worthwhile:
  the TTL is currently 24h (`ASSIGNMENT_PDF_CACHE_TTL_SECONDS`), but
  teachers commonly publish a week ahead, so a pre-warmed PDF expires
  before most students ever open it.
- Results are shared across *all* workers and instances. The current
  single-flight in `pdf_cache.get_or_render()` is per-process, so an N-worker
  deploy still costs up to N duplicate renders per burst; a shared object
  store makes a render done anywhere reusable everywhere.

Tradeoff to accept: a fetch from object storage costs maybe 50–200ms versus
~20ms from Redis — still far cheaper than the ~600–2000ms re-render it
avoids (measured: cached download 18ms vs 1253ms cold).

**Scope sketch:** keep the existing cache-key design (assignment id + view
type + `updated_at`, so an edit is a natural miss and nothing needs manual
invalidation) and swap the storage backend behind `get_cached_pdf` /
`store_pdf`; keep single-flight in front of it. Needs a cleanup story for
superseded objects, which Redis currently gets for free via TTL.

**Related, and only worth building once storage is cheap:** pre-render both
views on publish (hook alongside `queue_new_assignment_posted_notification`
in `assignments/signals.py`) so a class opening a newly published assignment
hits warm storage instead of the render path. Stress testing measured 30
simultaneous requests for one uncached assignment; single-flight cut that
from 30 renders to 1, but pre-rendering would cut it to 0.

**Also worth doing regardless of where PDFs live:** set an explicit
`maxmemory` on the Redis instance. At `0` it will grow until the container
is OOM-killed, which is worse than any eviction policy. (Confirm how Railway
allocates memory per service first — whether the plan's RAM is per-service
or account-wide changes what to set it to.)
