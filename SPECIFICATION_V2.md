# Grade Automator Plus — v2 Specification

This document collects proposed features and implementation plans for version 2
of the application. Each section is self-contained and detailed enough to be
picked up and implemented without needing the discussion that produced it.

Status legend: 🟡 Proposed (not started) · 🔵 In progress · 🟢 Done

---

## 1. 🟡 Per-question score overrides (fix stale breakdown after manual grade override)

### Problem

`StudentSubmissionViewSet.update_grade` (`students/views.py`, `update-grade`
action) lets a teacher PATCH a submission's total `score`. Today it only
rewrites the top-level total:

```python
feedback["grading_summary"]["total_score"] = score
feedback["grading_summary"]["percentage"] = percentage
submission.score = float(score)
submission.score_percentage = percentage
```

`feedback["question_evaluations"]` — the AI's original per-question scores,
rationale, and evidence quotes — is never touched. After an override, the
displayed per-question breakdown no longer sums to the displayed total, with
no indication to the student or teacher that this happened. This is
inconsistent with how the rest of the grading pipeline treats totals:
`AIProcessor._finalize_grading_result` (`ai_processor/services.py:1681`)
never trusts a self-reported total — it always derives it by summing
(clamped, rubric-snapped) per-question scores. The override endpoint should
follow the same rule: the total is *derived*, never independently settable.

This also under-uses information the system already has. When a submission
lands in the review queue because of a grader disagreement, `review_reasons`
already records exactly which `question_number`(s) were disputed and both
graders' scores for them (`students/services.py::_populate_and_save_grade`,
~line 313-330). The override UX should let a teacher resolve *those specific
questions* rather than only ever adjusting one opaque total.

### Goal

Let a teacher override individual question scores. The submission's total is
always recomputed server-side as the sum of (possibly overridden) per-question
scores — mirroring `_finalize_grading_result`'s own philosophy. Keep the
existing bare-total PATCH working for backward compatibility, but make it
explicit when a total was set without reconciling the breakdown underneath it.

### API design

**Endpoint:** `PATCH /api/.../student-submissions/{id}/update-grade/` (same
endpoint, extended request body — no new URL).

**New request shape** (either field may be sent; at least one is required):

```jsonc
{
  // Existing behavior, unchanged wire format:
  "score": 63.0,

  // New: per-question overrides. Keys are question_number as a string
  // (matches how review_reasons already serializes it), values are the
  // new score_awarded for that question.
  "question_scores": {
    "3": 8,
    "5": 10
  }
}
```

**Resolution rules, in order:**

1. If `question_scores` is present:
   - For each `question_number` → `score`, look up the matching entry in
     `feedback["question_evaluations"]` by `question_number` (use the same
     normalization helper the grading pipeline already uses for this —
     `AIProcessor._question_number_key`, `ai_processor/services.py`, so `"3"`
     and `3` match the same question — this class of bug already bit
     `second_opinion.py`, see its `key_fn` usage and
     `tests_second_opinion_selection.py::test_int_and_string_question_numbers_join`).
   - Reject (400) any `question_number` that doesn't exist on the submission.
   - Reject (400) any score outside `[0, question.points]` — reuse
     `AIProcessor._coerce_score` / the same clamping approach as
     `_finalize_grading_result` (`ai_processor/services.py:1572`,
     `:1663` `_snap_to_rubric_level`) so an override snaps to the nearest
     valid rubric level exactly like an AI-produced score would, rather than
     allowing an off-ladder value like 8.5 on a rubric of [0, 6, 10].
   - Write the new value into that question's `score_awarded`. Preserve the
     AI's original value alongside it for audit:
     ```jsonc
     {
       "question_number": 3,
       "score_awarded": 8,           // now the teacher's value
       "ai_score_awarded": 5,        // original AI value, added on first override
       "overridden_by_teacher": true,
       "overridden_by": "<user_id>",
       "overridden_at": "2026-08-13T...",
       // level_achieved, evaluation_rationale, evidence_quotes: unchanged,
       // still show the AI's original reasoning — do not fabricate new
       // rationale text on the teacher's behalf
       ...
     }
     ```
   - Recompute `grading_summary.total_score` and `.percentage` as the sum
     over **all** `question_evaluations` (touched and untouched), not just
     the ones in this request — same derive-don't-trust rule as the grading
     pipeline itself.
   - If `score` was *also* sent in the same request, validate it matches the
     recomputed total (small float tolerance, e.g. `0.01`); if it doesn't,
     400 with a clear message — the client should not be able to claim a
     total that disagrees with the parts it just supplied.
   - Do **not** set any "unreconciled" marker — the breakdown and total are
     consistent by construction.

2. If only `score` is present (today's existing behavior, unchanged):
   - Validate and clamp against `max_total_points`, exactly as today.
   - Write `grading_summary.total_score`/`.percentage` and `submission.score`
     as today.
   - **New:** also write a top-level marker so downstream consumers (the
     student-facing UI, the formatted-grade AI step, future audits) know the
     breakdown wasn't reconciled:
     ```jsonc
     "manual_override": {
       "total_score": 63.0,
       "by": "<user_id>",
       "at": "2026-08-13T...",
       "reconciled": false,
       "note": "Total score manually adjusted; per-question breakdown below reflects the original AI grade and may not sum to this total."
     }
     ```
   - When `question_scores` *is* used (path 1 above), write the same
     `manual_override` key but with `"reconciled": true` and no `note`, so
     the frontend has one place to check regardless of which path was taken.

3. If neither `score` nor `question_scores` is present: 400, same as today's
   existing "A 'score' value is required" guard, updated to mention both
   fields.

### Data model changes

None required — everything above lives inside the existing `feedback`
JSONField. No migration needed.

Optional (nice-to-have, not required for v1 of this feature): add a
`StudentSubmission.review_severity`-style denormalized boolean column,
e.g. `breakdown_reconciled = models.BooleanField(null=True)`, so the
"was this total ever set without touching the breakdown" state can be
filtered on directly instead of reading into the JSON — mirrors why
`review_tier` was denormalized out of `review_reasons` in the first place
(`students/models.py:140-149`, JSONField values aren't filterable). Skip
this unless product actually needs to query/report on it; until then reading
`feedback["manual_override"]["reconciled"]` is sufficient.

### Serializer changes

`StudentSubmissionGradeUpdateSerializer` (`students/serializers.py:564`)
currently only exposes `id`/`score`. It needs a non-model `question_scores`
field, since it's not a submission column:

```python
class StudentSubmissionGradeUpdateSerializer(serializers.ModelSerializer):
    question_scores = serializers.DictField(
        child=serializers.FloatField(min_value=0),
        required=False,
    )

    class Meta:
        model = StudentSubmission
        fields = ["id", "score", "question_scores"]
        extra_kwargs = {"score": {"required": False}}
```

(`score` must become non-required at the serializer level since
`question_scores`-only requests are now valid — the existing manual
"'score' value is required" check in the view needs to become an
either/or check instead.)

### Interaction with existing behavior — must not regress

- **Review-queue resolution** (`update_grade`'s existing
  `if submission.needs_review: ... submission.needs_review = False` block,
  `students/views.py:942-950`): unchanged. Applies regardless of which of
  the two paths above was used.
- **Student notification on republish** (`notify_student_of_graded_submission`,
  `students/views.py:967-968`): unchanged, still fires if `is_published`.
- **Formatted-grade regeneration** (`formatted_grade_async` dispatch,
  `students/views.py:983-998`): unchanged — still fires after every
  override. The prompt already includes the full `submission.feedback`, so
  it will naturally pick up the corrected per-question breakdown when path 1
  was used, or the `manual_override` note when path 2 was used. Worth
  checking `ai_processor.formatted_grade`'s prompt template renders
  `manual_override.note` if present, so the AI-written summary shown to the
  student doesn't imply a false confidence in the breakdown when it wasn't
  reconciled.
- **`review_reasons` pre-fill**: not required for v1, but the natural
  frontend pairing — when opening the override form for a `needs_review`
  submission, pre-populate `question_scores` with the disputed
  `question_number`s from `review_reasons`, offering "keep A / take B /
  enter your own" per question. This is a frontend concern, not part of this
  endpoint's contract, but the endpoint shape above is designed to make that
  UX possible without further backend changes.

### Also fix while touching this endpoint (see spec §1a below)

- **Race condition**: `update_grade` currently does a plain
  `submission.save()` with no concurrency guard, unlike `publish` and
  `mark_reviewed` which use a conditional
  `.filter(pk=..., needs_review=True).update(...)` "atomic claim" pattern
  (`students/views.py:1181-1187`, `:1238-1250`). Two near-simultaneous
  overrides on the same submission can silently clobber one another. Fix:
  wrap the read-modify-write in `transaction.atomic()` with
  `select_for_update()` on the submission row, or adopt the same
  conditional-update pattern if a suitable WHERE-clause guard exists (e.g.
  compare against a stored `updated_at`/version field — see §1b for a
  minimal version column if this is worth doing project-wide).

### Testing plan

Extend `students/tests_*` (find or create
`students/tests_update_grade.py`) with:

1. `question_scores`-only request: breakdown and total both update, and sum
   correctly, including a rubric-level snap case (score sent between two
   valid levels snaps down, matching `_snap_to_rubric_level`'s
   ties-resolve-down rule).
2. `score`-only request (today's path): still works exactly as before, now
   also asserts `feedback["manual_override"]["reconciled"] is False`.
3. Both fields sent, agreeing: succeeds.
4. Both fields sent, disagreeing (outside float tolerance): 400.
5. `question_scores` referencing a `question_number` not on the submission:
   400.
6. `question_scores` value outside `[0, points]` for that question: 400.
7. Int vs string `question_number` keys both resolve to the same question
   (mirrors `tests_second_opinion_selection.py`'s existing int/str-join
   coverage).
8. `needs_review` clears and `review_reasons` gets an `"overridden"` entry
   regardless of which path was used.
9. `formatted_grade_async` is still dispatched exactly once per request,
   for both paths (mirror the existing dispatch-tracking test pattern used
   elsewhere in this suite).
10. Race test: two concurrent overrides on the same submission — with the
    concurrency fix in place, the second should either serialize behind the
    first (via `select_for_update`) or be rejected with a clear conflict
    error, never silently lose data.

---

## 1a. 🟡 Concurrency guard on `update_grade`

Split out from §1 above since it's independently useful even before the
per-question work lands, and small enough to ship first.

**Problem:** `update_grade` reads `submission`, mutates several fields in
Python, then calls `submission.save(update_fields=[...])` with no guard
against a concurrent write to the same row. Contrast with `publish` and
`mark_reviewed` in the same file, which both use:

```python
updated = StudentSubmission.objects.filter(
    pk=submission.pk, <some-precondition>
).update(...)
```

as an explicit "atomic claim" — the comments in both call this out
deliberately (`students/views.py:1181-1187`, `:1234-1238`).

**Fix:** wrap `update_grade`'s body in:

```python
with transaction.atomic():
    submission = StudentSubmission.objects.select_for_update().get(pk=submission.pk)
    ... existing validation and mutation ...
    submission.save(update_fields=[...])
```

`select_for_update()` is sufficient here (unlike `publish`/`mark_reviewed`,
there's no natural boolean precondition to build a conditional `.update()`
from — the score itself is the thing changing) — it serializes concurrent
overrides on the same row rather than silently dropping one.

**Testing:** a test that fires two overrides for the same submission
concurrently (e.g. via threads or by asserting the second request, issued
after the first but before commit in a transaction-wrapped test, blocks/
retries rather than losing the first write) should confirm no lost update.

---

## Template for future entries

```markdown
## N. 🟡 <feature name>

### Problem
### Goal
### API design
### Data model changes
### Serializer changes
### Interaction with existing behavior — must not regress
### Testing plan
```
