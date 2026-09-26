# Decision: no raw exception bodies from the extraction/grading call chain in logs shipped to Sentry

**Status:** DECIDED (policy/convention) — **not code-enforced in this PR**.
**Raised by:** BE-A-04 PII cleanup (`task/epic-a-pii-cleanup`), 2026-09-22, as
plan item `04_epic_a_implementation_plan.md` §0.5a #4.
**Affects:** `ai_processor/services.py` (~15+ sites: 2743, 2754, 2765, 2771,
2916, 2922, 3510…) and any caller in the same call chain.
**Not touched by this PR:** per explicit scope decision (Senior Manager,
2026-09-22) — `ai_processor/services.py` is shared grading-pipeline code
that the Epic A audit-emitter work (`grade-automator-plus-88`) also lands
call sites in (`mark_processing_task_success`/`failure`). Changing 15+
sites there now risks a collision with that PR. This document records the
policy so a fast-follow PR has an explicit rule to implement against,
rather than each engineer touching one of these sites making an
independent judgment call.

---

## The finding

`ai_processor/services.py` has a dominant `except Exception as e: logger.error(...,
exc_info=e)` / `str(e)` shape around AI-provider calls and JSON parsing of a
grading response that is itself built from submission content. Nothing
confirmed leaking at the time of the audit — but a provider/SDK exception
whose `__str__` echoes the response body (or a future
`ValidationError(str(request.data))` added inside one of these blocks)
would ship straight to Sentry: `LoggingIntegration(event_level="ERROR")`
in `AutoGrader/settings.py` turns every `logger.error`/`.exception` call
into a Sentry event, and `send_default_pii=False` does not touch log
message content (it only suppresses Sentry's automatic user/request
context — see `AutoGrader/sentry_scrubbing.py`'s docstring for the same
point made about the `before_send` scrubber, which is defense-in-depth for
this same gap but does not make fixing the call sites unnecessary).

**Confirmed downstream inheritors** (bounded investigation,
`task/epic-a-pii-cleanup`, 2026-09-22): `students/views.py:538`
(`upload_answers_engine`), `:668` (`update_submission_from_raw_text`) and
`:781` (`grade_engine`) all wrap this exact call chain with their own
`except Exception as e: logger.error(..., exc_info=e)`. Not proven to leak
today — no exception raised inside the chain was found to embed raw
submission text in its `__str__` — but they inherit this policy from the
chokepoint, not as a separate rule, because a fix at the chokepoint alone
would not stop these three from independently re-introducing the same
class of leak.

**Ruled out** (same investigation): `assignments/views.py:867,1420,1835`
(`exc_info=e` around assignment-generation and PDF rendering) and
`assignments/services.py:640,772` (`%r` on a malformed, non-dict
question/rubric entry inside `format_assignment_standard_html`). Both
operate on assignment content — teacher- or AI-authored, pre-submission —
not student submission data, so they are out of scope for FR-A-04
(student PII specifically) even though they share the surface pattern.

## The rule

Never log a raw provider/model exception body — `str(e)`, `exc_info=e`'s
rendered traceback text, or any parsed-response fragment — at a level that
ships to Sentry, when the exception originates inside the
extraction/grading call chain (`ai_processor/services.py`'s ~15+ sites,
plus `students/views.py:538/668/781` above). Log the exception's type name
and a short, hand-written static description of what failed instead. If a
dynamic detail is genuinely needed, it must come from a known-safe field —
an id, a status code, a provider name — never from the exception's own
`__str__`.

## Why not fixed now instead of documented

1. Out of this PR's assigned scope (§0.5a's own item 4 already says so).
2. `ai_processor/services.py` is about to be touched by a parallel PR
   (the audit emitter's `GRADING_FAILED` call site) — landing an
   unrelated rewrite of the same file's exception handling at the same
   time invites a merge conflict or a silent double-fix. Sequencing
   between that PR and this policy's fast-follow is the Senior Manager's
   call, not either engineer's.
3. `scripts/check_no_pii_in_logs.py` (the lint rule landed alongside this
   decision) only catches the `.email`/`.first_name`/`.last_name`/
   `.get_full_name()` shape confirmed elsewhere in this same audit — it
   does not and cannot catch "any exception message from this specific
   call chain" without a bespoke, call-chain-aware check, which is a
   separate, larger piece of work than this PR's scope.

## What closes this

A fast-follow PR that:
- Rewrites the ~15+ `ai_processor/services.py` sites (and the three
  `students/views.py` sites above) to the rule stated here.
- Adds a targeted regression test per the pattern this repo already uses
  for failure-injection tests (`AutoGrader/tests_sentry_scrubbing.py` in
  this PR is the template for asserting a fabricated PII-bearing exception
  does not survive to the logged/Sentry-bound message).
- Is sequenced after (or coordinated with) the audit emitter's grading
  chokepoint call sites, since both PRs touch the same except blocks.
