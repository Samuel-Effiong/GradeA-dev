# Grading benchmark — findings

Findings from running the ground-truth benchmark against the live model.
Update this file each time a run surfaces something new; it is the
improvement backlog the benchmark exists to produce.

---

## Run 1 — first live baseline

**Setup:** 3 assignments (Mathematics 9q, Chemistry 6q, History &
Literature 4q), 7 students, 124 graded questions, 21 submissions.
Grader A `x-ai/grok-4.3`, grader B `deepseek/deepseek-v4-pro`.
495,602 tokens, 32 minutes.

### Headline

| Metric | Result |
|---|---|
| Exact rubric-level match | **84.7%** (105/124) |
| Within one rubric level | **99.2%** (123/124) |
| Mean signed level error | **+0.089** (very slightly lenient) |
| Student ranking (Spearman) | **0.90 – 0.97** across the three papers |
| Deterministic tier 0 accuracy | **100%** (31/31 claimed) |
| Rubric-snapping rate | **0%** — the model never returned an off-level score |
| Evidence verified | 92/92 of the quotes that survived to the result |
| Second-opinion disagreement | 17.9% (7/39), all `moderate` tier |

Read plainly: the grader is **accurate and very well ordered**. It
essentially never misplaces a student by more than one rubric band, it
ranks the cohort almost perfectly, and it is marginally generous rather
than harsh.

### Segment detail

| Segment | Exact | Within 1 | Bias |
|---|---|---|---|
| OBJECTIVE | 100.0% | 100.0% | 0.000 |
| SHORT-ANSWER | 80.0% | 98.5% | +0.108 |
| ESSAY | 77.8% | 100.0% | +0.148 |
| Mathematics | 92.6% | 98.2% | **0.000** |
| Chemistry | 78.6% | 100.0% | +0.167 |
| History & Literature | 78.6% | 100.0% | +0.143 |

Leniency rises as answers become more open-ended, which is the expected
shape. Mathematics is perfectly calibrated — unsurprising, since its
rubrics key off checkable facts.

---

## FINDING 1 (HIGH) — strict evidence can fail an entire submission on maths

**One submission in 21 (`maths/twin`) received no grade at all.**

The pipeline requires every points-awarding evaluation to cite a
verbatim quote from the student's answer. On long multi-step algebra the
model quotes by **eliding intermediate steps** — semantically faithful,
textually altered. Example from this run:

Student wrote:
```
\frac{a_{n+1}}{a_n} = \frac{(n+1)!}{(n+1)^{n+1}} \cdot \frac{n^n}{n!}
  = \frac{(n+1) n^n}{(n+1)^{n+1}} = \frac{n^n}{(n+1)^n}
  = \left(\frac{n}{n+1}\right)^n = ...
```
Model quoted:
```
\frac{a_{n+1}}{a_n} = \left(\frac{n}{n+1}\right)^n = ...
```

The middle of the chain is dropped, so the string match fails and
`enforce_evidence` reports *fabricated evidence*. Strict mode rejects the
batch, three retries all fail the same way, and the whole submission
errors out.

13 evidence rejections occurred across the run; one escalated to total
failure.

**Why this matters:** the student most likely to trigger it is the one
showing the most working — exactly the behaviour the rubric rewards. It
also burns 3× the credits before failing.

**Fixes, cheapest first:**
1. **Prompt** — instruct the grader to quote *short* spans (say ≤ 15
   words) and never elide with `...`. Near-zero cost, likely removes most
   occurrences.
2. **Degrade, don't destroy** — after the final retry, fall back to
   `log` mode for the offending question instead of failing the run. A
   grade carrying one unverified quote is strictly better for a student
   than no grade. *This is the structural fix and the one I would do
   first.*
3. **Sub-span matching** — accept a quote when a sufficiently long
   contiguous sub-span of it appears verbatim, rather than requiring the
   whole quote. Keeps the anti-fabrication guarantee while tolerating
   elision.
4. Split candidate quotes on `...`/`…` and require each fragment to
   match.

---

## FINDING 2 (MEDIUM) — the fluent-and-wrong student is caught, but unevenly

The adversarial student writes confident, well-structured, factually
incorrect answers. Results by paper (expected → awarded total):

- Chemistry **8 → 8** — caught exactly. The systematically swapped
  S<sub>N</sub>1/S<sub>N</sub>2 answer scored 0 as intended.
- History & Literature **25 → 15** — caught, and marked *harder* than
  ground truth. The fabricated historiography (invented Taylor thesis,
  the *Julius Caesar* quotation attributed to *Macbeth*) was penalised.
- Mathematics **12 → 16** — mildly over-rewarded (+4).

So style does not buy much, which is the important result. Maths is the
weak spot: the confidently-wrong working attracted slightly more credit
than it deserved.

---

## FINDING 3 (MEDIUM) — leniency concentrates on the strongest mid-tier answers

Two totals came in well above ground truth:

- `humanities/twin` 56 → 80
- `chemistry/twin` 44 → 60

Every individual question stayed within one rubric level (the run's
within-1 rate is 99.2%), but consistent one-level generosity compounds
across a paper. On a 4-question, 80-mark paper, four single-level lifts
is a whole grade boundary.

**Worth tracking, not yet worth acting on.** If successive runs keep the
bias positive, the lever is the prompt's Borderline Rule — currently
"round up only when core understanding is present", which is doing
exactly what it says.

---

## FINDING 4 (LOW) — self-reported confidence is not discriminative

120 of 124 questions came back at confidence ≥ 80, and the acceptable
grade rate at that band was 99.2%. With almost no spread there is
nothing for `GRADING_SECOND_OPINION_MIN_CONFIDENCE = 80` to select on —
the low-confidence trigger effectively never fires, leaving high-stakes
(≥15 points) and model self-flagging as the real triggers.

Not a defect, but it means confidence should not be trusted as a
routing signal until there is evidence it varies.

---

## FINDING 5 (LOW) — tier 0 defers on LaTeX whitespace differences

`objective_grading.normalize_text` does not collapse internal
whitespace, so a student who retypes option D as `$x^2\ln(x)$` when the
option reads `$x^2 \ln(x)$` produces a letter/text conflict and the
question is deferred to the LLM.

This is the **safe** direction — tier 0 declines to guess rather than
forcing a wrong answer, exactly as designed — and the LLM graded it
correctly. It costs a small amount of avoidable spend, nothing else.
Collapsing runs of whitespace inside `normalize_text` would close it.
Left as a deliberate probe in the dataset
(`maths/fluent_wrong` Q1) so the defer path stays covered.

---

## Corrections to the benchmark itself

**`maths/strong` Q4 expectation was wrong, not the grader.** Ground truth
said 12; the grader awarded 8 and was right. The student back-substituted
instead of changing the limits — mathematically valid, but this rubric's
*excellent* descriptor explicitly requires "limits correctly changed to
$0 \to 4$", and *good* is exactly "limits handled loosely". Corrected to
8 and kept as a deliberate rubric-adherence probe.

Worth stating plainly: on the single question where the benchmark and the
model disagreed out of band, **the model was correct**.

---

## Resolution — state after the fixes

Finding 1 was fixed in the same session (`_grade_question_batch` and the
single-pass path now degrade strict evidence to `log` on the *final*
attempt only, and never for the second-opinion pass, which has no grade
at stake). Ground truth for `maths/strong` Q4 was corrected. Re-scored:

| Metric | First live run | After fixes |
|---|---|---|
| Questions graded | 124 | **133** (the failed submission now grades) |
| Exact rubric-level match | 84.7% | **85.7%** |
| Within one level | 99.2% | **100.0%** |
| Out-of-band failures | 1 | **0** |
| Submissions failing outright | 1 | **0** |
| Deterministic tier 0 | 31/31 | **34/34 (100%)** |
| Evidence verified | 92/92 | 97/98 — the one unverified quote is the degraded case, correctly marked |
| Identical-answer consistency | 1 probe skipped | **both consistent** (20 = 20 on each) |

**Every question now lands within one rubric level of ground truth, and
no submission fails.** The cross-student consistency probes both pass:
byte-identical essays scored identically (20 and 20), so the gap that
worried me most is not currently manifesting.

Findings 2, 3, 4 and 5 remain open and are tracked for the next run.

## Notes for whoever runs this next

- Recordings are keyed by prompt hash, and a retried call overwrites the
  earlier attempt under the same key. A case that failed once then
  succeeded therefore replays as an immediate success. Outcomes match;
  the retry path does not.
- Tier 0 removes objective questions *before* the single-pass/batched
  split, so the count that matters is LLM-bound questions, not total
  questions. The Mathematics paper carries 9 questions specifically so
  that 6 reach the model and the batched path is exercised —
  `test_both_grading_paths_are_exercised` locks this.
- `GRADING_SECOND_OPINION_SAMPLE_RATE` is pinned to 0 for benchmark runs
  so cost and replay keys stay deterministic.
- `--mode record` merges into the existing recordings file rather than
  replacing it (deliberately, so a partial re-record can top up one
  student without a full billed pass — see `_Tape.__init__`). Every
  prompt/response text change therefore accumulates a new set of entries
  under new hashes, and the old ones for retired prompt text are never
  removed. 162 entries / 232KB after Round 3's prompt changes, still
  well under the repo's 500KB large-file limit, so not yet worth
  building a pruning step for — but if a future prompt rewrite pushes
  this close to that limit, the fix is tracking which keys a run
  actually looked up (`_Tape.wrap`) and dropping the rest on save,
  not raising the size limit.

## Round 2 — root-cause fix plus four new capabilities

Everything below is implemented and unit-tested (mocked model, no cost).
None of it has a live re-run yet — the recordings this benchmark replays
against predate all of it, so `--mode replay` currently reports stale
prompt hashes rather than results. A fresh `--mode record` pass is the
next paid step, deliberately gated as a separate go/no-go from the code
change itself.

**Evidence-quoting root cause (extends the Resolution above).** The
Resolution section fixed the *safety net* — a strict-evidence failure on
the final retry now degrades to a kept-but-flagged grade instead of
destroying the submission. `GRADING_ASSIGNMENT_PROMPT_5.txt`'s EVIDENCE
section now also targets the *cause*: an explicit rule that a quote must
be one continuous span, and that a multi-step justification should be
several short quotes rather than one quote elided with "...". If this
lands, the evidence-rejection count in the next live run should drop
below the first run's 13, not just fail more gracefully when it happens.

**Every batch now gets assignment-level context.** A batch previously
saw only its own question slice — no title, no instructions, no sense of
the whole paper. Every batch (and the single-pass prompt) now opens with
a short `### Assignment Context` block.

**Question diagrams reach the grader.** `question_image` existed on the
schema but the grading prompt was always text-only, so a question whose
content *is* a diagram was graded blind. It's now sent as an actual
`image_url` content block (capped at `GRADING_MAX_IMAGES_PER_CALL` per
call). Moot for the current benchmark dataset specifically (no question
in it carries an image), but real for any teacher-uploaded diagram
question in production.

**`Assignment.custom_ai_prompt` is wired up.** The field existed
(teacher-authored supplementary instructions, e.g. "always require
units") but was read by nothing — its one reference was commented-out
dead code in `dashboard/views.py`. It now splices into the grading system
prompt at all three call sites, explicitly framed as non-overriding, and
gated behind `GRADING_CUSTOM_INSTRUCTIONS_ENABLED` as a kill switch.

**Cross-student consistency is now a guarantee, not an outcome.** The
Resolution above reports both `twin` probes passing — but that was
temperature-0 luck, not a structural guarantee (OpenRouter fallback
routing means "temperature 0" isn't "always identical"). A new
content-addressed cache (`ai_processor/grading_cache.py`) now makes it
one: the second of two byte-identical (question, answer) pairs is served
the first's evaluation directly, no second model call, so identical
inputs cannot diverge. Deliberately turned OFF for benchmark runs
(`runner.py::_benchmark_settings`) — the `twin` probe exists specifically
to measure whether the *model* is consistent, and the cache would make it
trivially pass by construction instead of measuring anything. A question
whose grade drew a second-opinion disagreement is never cached, so a
disputed grade can't silently spread to a later student.

## Run 3 — Round 2 fixes verified live

Fresh `--mode record` pass against the live model with all of Round 2 in
place (evidence-quoting root-cause instruction, per-batch assignment
context, `custom_ai_prompt` wiring, question-image support — the answer
cache is deliberately off for benchmark runs, see above). `--mode replay`
immediately afterward reproduced every number below exactly, confirming
the recordings are faithful.

| Metric | Resolution (Run 2) | Run 3 |
|---|---|---|
| Questions graded | 133 | 133 |
| Exact rubric-level match | 85.7% | **82.7%** |
| Within one level | 100.0% | **100.0%** |
| Out-of-band failures | 0 | **0** |
| Submissions failing outright | 0 | **0** |
| Deterministic tier 0 | 34/34 (100%) | **34/34 (100%)** |
| Evidence verified | 97/98 | **95/98** |
| Identical-answer consistency | both consistent | **both consistent** |
| Second-opinion disagreement rate | — | **10.0% (4/40), all `moderate`** |
| Cost | — | **617,213 tokens, 21 submissions, ~56 min** |

**Read this honestly, not hopefully.** The floor held — zero out-of-band
failures, zero failed submissions, tier 0 still perfect, both
consistency probes still exactly consistent. But the two numbers Round 2
specifically targeted (evidence-verified rate, exact-match rate) both
moved in the wrong direction, not the right one: evidence-verified went
from 97/98 to 95/98, and exact match dropped 3 points. Neither move is
large against a 21-submission sample — this is very plausibly ordinary
run-to-run model variance rather than the fix backfiring — but a single
before/after live run **cannot distinguish "the fix didn't help" from
"noise"**, and I'm not going to claim it worked when the numbers say the
opposite of what I predicted. The honest state is: the root-cause
evidence-quoting instruction is unverified. It needs either a repeat run
or, ideally, the weekly scheduled job accumulating a few data points
before anyone trusts a trend here.

**New observation, consistent with Finding 3 (still open).** Individual
questions never left the one-level band, but per-student TOTALS swung
further than that implies, because small per-question leniencies
compound across a paper:

- `chemistry/twin`: expected 44, awarded 60 (+16)
- `maths/strong`: expected 12, awarded 28 (+16)
- `humanities/strong`: expected 25, awarded **5** (-20) — the one
  swing in the *harsh* direction, worth a second look next run since
  every other outlier this run and last was lenient.

None of this changes the recommendation from Finding 3: watch across
runs, don't tune the Borderline Rule on one data point.

**Correction (found while writing up Run 4):** the two labels above
attributed to `strong` were wrong. `scoring.py`'s ranking table sorts
student keys alphabetically (`excellent, fluent_wrong, middling,
partial, strong, twin, weak`), not in dataset order, and this section
was written by reading positions against the wrong order. Both swings
actually belong to `fluent_wrong` — the deliberately confident-and-wrong
student, not the strong one. That changes the reading substantially: a
harsh mark on `humanities/fluent_wrong` is the adversarial probe working
as intended (Finding 2 already documented this student being penalised
in History & Literature), not an unexplained swing against a good
student. Run 4 below reproduces the same pattern and is labelled
correctly.

## Round 3 — reason-before-score, borderline routing, LaTeX whitespace

Three more changes, all aimed at the same root problem: the pipeline was
structurally set up to let the model commit to a number before it had
justified one.

**Field order now forces evidence and reasoning before the score.**
Under strict structured output the model fills a JSON schema's fields in
the order they're declared, and every token written conditions the
ones after it. `QUESTION_EVALUATION_SCHEMA` used to declare
`score_awarded` and `level_achieved` *before* `evidence_quotes` and
`evaluation_rationale` — so on every single question, across every run
so far, the model was picking a score first and writing a rationale
that agreed with it afterward. The order is now evidence → rationale →
`level_decision` → level → score, and the prompt's numbered procedure
was rewritten to match, with an explicit line telling the model not to
decide a number and reason backwards to it. The same reordering was
applied one level up, in the single-pass schema: `question_evaluations`
now precedes `grading_summary`, so the model grades every question
before it is asked for a paper total.

**A new per-question uncertainty signal, `level_decision`.** Finding 4
established that the run-level `grading_confidence` field is useless for
routing — 120 of 124 questions came back ≥80, with no spread to
threshold on. `level_decision` asks the narrower question directly: did
*this* answer sit between two rubric levels? `"clear"` or
`"borderline"`, self-reported per question, and it now drives a new
second-opinion trigger (`REASON_BORDERLINE_LEVEL` in
`second_opinion.py`) — a rubric ladder has one rung between adjacent
grades, so a borderline call is exactly where a second, independent
reader is worth its cost. It's a self-reported field and could be
gamed by a grader that always says "clear", which is why the benchmark
scores it against ground truth (`level_decision_calibration` in
`scoring.py`) instead of trusting it — see below for what that showed.
Defaults are the safe direction throughout: anything but a literal
`"borderline"` normalizes to `"clear"` in
`_finalize_grading_result`, so a model that omits the field, or an
older recording predating it, escalates nothing rather than
everything.

**LaTeX whitespace no longer defeats deterministic matching.**
Finding 5 identified `$x^2\ln(x)$` vs `$x^2 \ln(x)$` as a false-defer:
mathematically the same expression, textually different, so tier 0 gave
up and paid for an LLM call to reach an answer it could have had for
free. `collapse_math_whitespace` now strips whitespace *inside* math
delimiters (`$...$`, `$$...$$`, `\(...\)`, `\[...\]`) only — prose stays
exactly as sensitive to spacing as before ("not able" must never compare
equal to "notable"), and two options that happen to collapse to the same
math string are deliberately left unresolvable by the fallback rather
than guessed between. The old whitespace probe in the dataset
(`maths/fluent_wrong` Q1) now correctly gets claimed by tier 0 instead of
deferring; it was replaced with a new probe (`maths/middling` Q1) that is
mathematically equivalent to the correct option but not exactly the same
string even after whitespace collapsing (reordered terms, `\ln x` vs
`\ln(x)`) — this one *should* still defer, and does, keeping the AMBIGUOUS
path under live coverage.

All three are unit-tested in `ai_processor/tests_reason_before_score.py`
(37 tests: schema field-order assertions, `level_decision`
normalization and trigger behaviour including the kill switch, and the
math-whitespace matching including the collision-safety and
prose-safety cases) without touching the model.

## Run 4 — Round 3 fixes verified live

Fresh `--mode record` pass, same 3 assignments / 7 students / 133
questions as Run 3. `--mode replay` immediately after reproduced every
accuracy number exactly (only tokens/wall-clock differ, as expected).

| Metric | Run 3 | Run 4 |
|---|---|---|
| Questions graded | 133 | 133 |
| Exact rubric-level match | 82.7% | **86.5%** |
| Within one level | 100.0% | **100.0%** |
| Out-of-band failures | 0 | **0** |
| Submissions failing outright | 0 | **0** |
| Deterministic tier 0 | 34/34 (100%) | **34/34 (100%)** |
| Evidence verified | 95/98 | **90/98 (91.8%)** |
| Identical-answer consistency | both consistent | **both consistent** |
| Second-opinion disagreement rate | 10.0% (4/40) | **12.5% (5/40), all `moderate`** |
| Cost | 617,213 tokens, ~56 min | **691,518 tokens, ~45 min** |

**Read this the same honest way as Run 3.** The floor held again — zero
out-of-band failures, zero failed submissions, tier 0 perfect, both
consistency probes exact. Exact-match recovered and then some (82.7% →
86.5%, now above the Run 2 baseline of 85.7%), which is consistent with
reason-before-score helping, though one run still can't separate that
from ordinary variance.

**Evidence-verified kept falling, for a third run running: 97/98 → 95/98
→ 90/98.** This is no longer a one-run wobble — it has moved the same
direction three times since the Round 2 "fix the root cause" instruction
shipped, and it is now noticeably worse than where it started. The
degrade-on-final-retry safety net is doing its job (nothing fails
outright), but the instruction meant to reduce *how often* it has to
fire is not visibly working, and the evidence points the other way. This
needs direct attention next, not another wait-and-see pass — possibly
sub-span matching (accepting a quote when a long contiguous piece of it
appears verbatim, from the original Finding 1 fix list) rather than more
prompt wording.

**`level_decision` got zero live use.** Every one of the 99 LLM-graded
questions came back `"clear"`; `level_decision_calibration` has nothing
to calibrate against, and the borderline second-opinion trigger never
fired this run (the 5 disagreements this run all came from the existing
high-stakes/flag triggers). Two honest readings, and this run can't
distinguish them: either this benchmark's answers really are mostly
unambiguous relative to their rubrics (plausible — they were authored to
hit specific levels cleanly, which is good test design but not
representative of messy real submissions), or the model defaults to
"clear" the way it defaulted to confidence ≥80, and the field isn't
discriminating anything yet. Real teacher-submitted answers are likely
to produce more genuine borderline cases than a hand-authored benchmark
ever will, so this is one to watch in production feedback and in the
weekly live job rather than conclude anything from here.

**The Run 3 outlier correction (see above) reproduces correctly
labelled.** Two large per-paper swings, both `fluent_wrong`:

- `maths/fluent_wrong`: expected 12, awarded 28 (+16) — same student,
  same subject, same direction as Run 3's (mislabelled) outlier. The
  confidently-wrong maths answer is being over-rewarded again;
  Finding 2 called mathematics the weak spot for this probe and this is
  the second run in a row backing that up.
- `humanities/fluent_wrong`: expected 25, awarded 5 (-20) — harsher
  than Run 1's -10 on the identical probe, but harsh in the *desired*
  direction (penalising fabricated content), so not a concern on its
  own. The size of the swing between runs on an unchanged probe is
  itself a data point about full-paper-level variance, independent of
  whether the direction is good.
- `chemistry/strong` (+12) and `chemistry/twin` (+16) reproduce the
  Finding 3 leniency pattern from Run 1 almost exactly (Run 1 had
  `chemistry/twin` +16 as well).

Findings 3, 4 and 5 (whitespace defer specifically closed by this round)
update as follows: **Finding 5 is resolved** (see Round 3 above).
Finding 4 remains open, now with a second dataset (`level_decision`) that
also shows no spread — worth reading together rather than as two
separate weak signals. Finding 3 keeps accumulating consistent evidence
across three runs now and is close to worth acting on the Borderline
Rule, though still on a 21-submission sample per run.

## Round 4 — root-causing the evidence-verified decline, not just re-running

Run 4 left one thing unresolved: evidence-verified had fallen for three
runs straight (97/98 → 95/98 → 90/98) despite Round 3's "quote short,
unbroken spans" instruction. Rather than try a fourth prompt tweak on
faith, the actual raw model quotes from Run 4 were pulled out of the
recordings and diffed character-by-character against what the student
actually wrote, before `enforce_evidence` had a chance to strip them.

**The failures were not spread evenly.** Split by subject:
Mathematics 41/42 (97.6%), Chemistry 21/28 (**75.0%**),
History & Literature 28/28 (100%) — 7 of 8 total failures were
Chemistry, and Humanities (pure prose, zero LaTeX) was perfect. That
alone pointed at LaTeX notation as the mechanism, and reading the actual
quotes confirmed it: the grade was correct in every single case, but the
model kept **re-typesetting its own LaTeX while quoting it** — `[H_2]`
became `[H2]`, `\frac{[HI]^2}{[H_2][I_2]}` became `[HI]² / [H2][I2]`,
`\rightarrow` became `→`. Faithful to the meaning, a fabrication by the
letter of a byte-for-byte check.

**Fix, built and validated against the real failure corpus before
shipping it** (`ai_processor/evidence.py::_desugar_latex`): both the
quote and the answer are desugared the same way before comparison —
subscript/superscript markers and `\text{}`-style wrappers stripped,
`$`/`$$` delimiters removed, a fixed table of unambiguous LaTeX↔glyph
synonyms folded (→, Δ, ×, · …), and `\frac{a}{b}` expanded to `a/b`
**only** when neither operand has a top-level `+` or binary `-` — i.e.
only where dropping the fraction bar cannot change how the expression
reads. `\frac{a+1}{b}` stays untouched rather than risk equating it with
the ambiguous `a+1/b`. Also: a quote joining two excerpts with `...`
(still against the rules — GRADING_ASSIGNMENT_PROMPT_5's EVIDENCE
section forbids it) is now split into fragments and each is verified
independently, which is what the ellipsis already claims to mean.

Validated by capturing every raw (quote, answer) pair from Run 4's
actual failures and replaying them through drafts of the normalizer
before touching the real module — the discipline this uncovered several
real bugs a synthetic test never would have (NFKC decomposing the
superscript minus U+207B into MINUS SIGN U+2212 rather than ASCII `-`,
so a charge like `Nu^-` and the model's own `Nu⁻` landed on two
different characters; a naive "delete all whitespace in `$...$`" version
that broke plain-text arrows and `+` signs the model rendered with
normal spacing outside any `$` span). Landed at 21 of 23 real
previously-failing quotes now verifying; the 2 remaining are documented,
deliberately unfixed edge cases (one pre-existing HTML-tag/punctuation
boundary quirk unrelated to LaTeX, one case of the model adding
protective parentheses around a multiplied denominator that this module
doesn't try to guess at). 39 new tests in
`ai_processor/tests_evidence_latex.py`, most pinning the exact captured
production strings rather than idealized versions of them.

The prompt also got a concrete before/after LaTeX-quoting example
(GRADING_ASSIGNMENT_PROMPT_5's EVIDENCE section) — general instructions
had already proven weak for this specific compulsion in Round 2/3, so
this targets the mechanism, not just the model's compliance with a rule.

## Run 5 — the evidence fix verified live

Fresh `--mode record` pass, same 133 questions. `--mode replay`
immediately after reproduced every metric exactly.

| Metric | Run 4 | Run 5 |
|---|---|---|
| Questions graded | 133 | 133 |
| Exact rubric-level match | 86.5% | 84.2% |
| Within one level | 100.0% | **100.0%** |
| Out-of-band failures | 0 | **0** |
| Submissions failing outright | 0 | **0** |
| Deterministic tier 0 | 34/34 (100%) | **34/34 (100%)** |
| **Evidence verified** | **90/98 (91.8%)** | **97/98 (99.0%)** |
| Identical-answer consistency | both consistent | **both consistent** |
| Second-opinion disagreement rate | 12.5% (5/40) | **8.3% (7/84)** |
| Cost | 691,518 tokens, ~45 min | **803,980 tokens, ~64 min** |

**Read this the way the last two rounds were read: what moved, and
whether it moved for the reason claimed.** Evidence-verified jumped
from 91.8% to 99.0% — the single largest movement of that metric across
all five runs, in the predicted direction, immediately after the
targeted fix. Split by subject to check it moved where predicted:
**Chemistry went from 75.0% to 100.0%**; Mathematics stayed at 97.6%
(41/42 — the one remaining failure there is the long-derivation elision
case from the original Finding 1, a different mechanism this round
didn't target); Humanities stayed at 100%. This is the strongest
before/after read of any fix in this benchmark so far, because it is
the first one checked against the *specific* failures it was built to
fix, not just an aggregate number.

Exact-match dipped slightly (86.5% → 84.2%), within the same
run-to-run noise band the last several rounds have shown on a
21-submission sample — nothing here suggests it's connected to the
evidence fix (LaTeX desugaring runs at verification time, after the
score is already decided; it cannot change which level the model
selected). Deterministic tier 0, both consistency probes, and the
zero-failure floor all held.

**`level_decision` still reported zero `"borderline"` calls**, now
across two live runs (99 questions in Run 4, 99 in Run 5). Two runs
with no spread is a stronger signal than one — worth treating this the
same way as `grading_confidence` (Finding 4) rather than waiting for a
third run to say the same thing: on ground-truth-authored benchmark
answers specifically, the model isn't finding genuinely close calls, or
it's defaulting to "clear" the way it defaulted to high confidence. Real
teacher-submitted answers are the more likely place this signal
actually earns its keep; production data is the next place to check it,
not another benchmark run.

**Per-student totals**, correctly labelled this time (see the Run 3
correction above for how that went wrong before) — `chemistry/twin`
+16, matching Run 1 and Run 3 exactly, three runs in a row now on the
same probe. Combined with `chemistry/strong` +12 this run, Finding 3
(leniency on the strongest chemistry answers) now has three consistent
data points and is close to worth a deliberate look at the Borderline
Rule rather than continued watching. `humanities/fluent_wrong` -20
reproduces Run 4 exactly (same probe, same direction, same magnitude —
the harsh-on-fabrication behavior is stable). `maths/fluent_wrong` +8
this run (was +16 in Run 4, +4 in Run 1) continues Finding 2's pattern
of leniency on confidently-wrong maths, though the size keeps varying.
Two swings appear for the first time this run — `humanities/twin` +17
and `maths/weak` -8 — logged here rather than acted on; one new
appearance is not yet a pattern.

**Bottom line:** the evidence-verified regression that opened this
round is fixed, and fixed for the reason claimed — the subject-level
breakdown is exactly what the root-cause diagnosis predicted it would
be. Findings 2 and 3 keep strengthening across runs; Finding 4 is ready
to be written off as "not discriminative on this dataset" pending a
production read.

## Run 6 — re-recorded after raising the chunk sizes

Not a bug fix like Rounds 1-4: `GRADING_QUESTIONS_PER_CHUNK` moved 5 → 10
and `ANSWERS_EXTRACTION_PAGES_PER_CHUNK` moved 1 → 3, based on a separate
10-runs-per-config live-endpoint speed/accuracy investigation
(see `benchmark_artifacts/EXTRACTION_ACCURACY_INVESTIGATION.md` and
`GRADING_QA_INVESTIGATION.md`) that found no accuracy cost to either
change on that dataset. This run exists to re-record this benchmark's
own cached fixtures against the new chunk sizes — the old recordings'
prompts no longer matched anything once the batch boundaries moved — and
to confirm this dataset agrees with that investigation's finding.

| Metric | Run 5 | Run 6 |
|---|---|---|
| Questions graded | 133 | 133 |
| Exact rubric-level match | 84.2% | 82.7% |
| Within one level | 100.0% | **100.0%** |
| Out-of-band failures | 0 | **0** |
| Submissions failing outright | 0 | **0** |
| Deterministic tier 0 | 34/34 (100%) | **34/34 (100%)** |
| Evidence verified | 97/98 (99.0%) | **97/98 (99.0%)** |
| Identical-answer consistency | both consistent | **both consistent** |
| Second-opinion disagreement rate | 8.3% (7/84) | **10.7% (9/84)** |
| Second-opinion errors | 0 | **1** (see below) |
| Cost | 803,980 tokens, ~64 min | **699,700 tokens, ~73 min** |

**Read this as: did raising the chunk sizes cost accuracy on THIS
dataset?** No stronger claim than that — this table can't speak to
production data or to chunk sizes beyond what was tested (3
pages/call, 10 questions/call). Exact-match moved 84.2% → 82.7%, inside
the same run-to-run noise band Runs 3-5 already established on this
21-submission sample; within-one-level, deterministic tier 0, and the
zero-failure floor all held exactly. Token cost dropped (fewer, larger
batches means less repeated context per call) despite wall-clock time
rising slightly — consistent with fewer round trips at a slightly
higher per-call latency each, not a regression in either direction.

**One new second-opinion error** appeared this run: a batch's evidence
enforcement rejected grader A's initial attempts (a long-derivation
elision, the same mechanism as Finding 1) and the *second-opinion*
re-grade of that same batch separately failed evidence enforcement
after its own 3 attempts. Grader A's primary score still stands — this
was a second-opinion-only failure — but it's a new interaction worth
watching: with GRADING_QUESTIONS_PER_CHUNK now larger, a batch that
fails evidence enforcement now costs more (both graders re-doing more
questions per retry) than it did at chunk size 5. One occurrence isn't
a pattern yet.

**Bottom line:** re-recording after the chunk-size change didn't reveal
a hidden accuracy cost on this dataset — the numbers move inside the
established noise band, not outside it. The live-endpoint investigation
that justified this change stands uncontradicted by this dataset.

## Run 7 — five new maths questions restore batched-path coverage

Run 6 raised the chunk sizes but left a gap: with
`GRADING_QUESTIONS_PER_CHUNK` at 10, `maths` — the largest assignment at
6 LLM-bound questions — no longer exceeded the chunk size, so the entire
benchmark silently stopped exercising the batched grading path at all
(`DatasetIntegrityTest.test_both_grading_paths_are_exercised` catching
exactly the gap it was written to catch). Fixed by adding five new
maths questions (Q10-14: box optimization, integration by parts, the
integral test, a Maclaurin series, and a separable ODE — see
`dataset.py`), each with a full rubric and answers for all 7 students,
bringing `maths` to 11 LLM-bound questions. This is a dataset content
change, not a pipeline change, so it needed its own `--mode record` pass
and its own golden-snapshot regeneration, separate from Run 6's.

| Metric | Run 6 | Run 7 |
|---|---|---|
| Questions graded | 133 | **168** |
| Exact rubric-level match | 82.7% | **84.5%** |
| Within one level | 100.0% | **100.0%** |
| Out-of-band failures | 0 | **0** |
| Submissions failing outright | 0 | **0** |
| Deterministic tier 0 | 34/34 (100%) | **34/34 (100%)** (unchanged - no new objective questions) |
| Evidence verified | 97/98 (99.0%) | **131/133 (98.5%)** |
| Cost | 699,700 tokens, ~73 min | **983,505 tokens, ~105 min** |

**Read this as: does the new maths content itself grade sensibly, not
just "does it exist."** The 35 new answers (5 questions x 7 students)
were authored the same way the rest of the dataset was — each answer
calibrated to land on a specific rubric level with a note explaining
why — and this run is the first check that the grader actually agrees.
Exact-match rose slightly (82.7% -> 84.5%), staying in the same band
established since Run 3; evidence-verified dropped a fraction of a
point (99.0% -> 98.5%, one more unverified quote out of 35 new
question-gradings) but stays consistent with Run 5/6's post-fix level,
not a regression signal on this sample size. Both new failure classes
this content type could plausibly introduce — the long-derivation
elision from Finding 1 (this maths content has multi-step algebra
answers, the exact shape that finding describes) and simple grading
mistakes on genuinely new rubrics — showed up as zero submission-level
failures in the final report, though one batch did hit the
evidence-enforcement retry-and-degrade path mid-run (see the live log)
before resolving.

**`DatasetIntegrityTest.test_both_grading_paths_are_exercised` now
passes again** — confirmed by running it directly before spending
anything on this record pass, so the content was known to be
structurally sufficient (11 > 10 LLM-bound questions) before the paid
run, not discovered after.

**Bottom line:** the batched-path coverage gap Run 6 left open is
closed, and the new content grades within the established accuracy
band rather than introducing a new one — no evidence the added
questions behave differently from the rest of the dataset.

## Run 8 — answer_status feedback wording forces a re-record; isolation experiment left incomplete

Editing `GRADING_ASSIGNMENT_PROMPT_5.txt` to add the `answer_status`
section (telling the grader not to accuse a student of skipping a
question whose answer extraction actually lost — see
`ai_processor/answer_completeness.py` for the extraction-side half of
that fix) changes the system prompt text, which changes every
`request_key` the tape hashes against. Every committed recording missed,
and replay correctly refused to fall through to a live call rather than
silently re-billing — five tests failed
(`tests_benchmark_golden`/`tests_benchmark_history`/`tests_grading_benchmark`)
pointing at the same root cause. This was not a choice to re-record; it
was forced by editing a file the request key is built from.

Re-recorded all 21 submissions, real endpoint, `--mode record`, 70 model
calls, ~1,001,000 tokens.

| Metric | Run 7 (old prompt) | Run 8 (new prompt) |
|---|---|---|
| Questions graded | 168 | 168 |
| Exact rubric-level match | 84.5% (142/168) | **82.7% (139/168)** |
| Within one level | 100.0% | **100.0%** |
| Mean level error | 0.0833 | **0.0774** |
| Deterministic tier 0 | 34/34 | **34/34** (unchanged) |
| Evidence verified | 131/133 | **132/133** |
| ESSAY exact | — | 78.6% (22/28) |
| OBJECTIVE exact | — | 100.0% (35/35) |
| SHORT-ANSWER exact | — | 78.1% (82/105) |

**Accepted, on the shape of the change rather than the headline number.**
Exact-match moved down 1.8 points (3 of 168 questions), but every one of
those three landed on the *adjacent* rubric level — `within_one_level`
stayed at exactly 1.0 across every question type and subject, the same
as every prior run. `mean_level_error` actually *improved*
(0.0833→0.0774), evidence verification improved by one question
(131→132), and the deterministic (OBJECTIVE) tier is untouched at 34/34,
as expected — this prompt section only ever changes short-answer/essay
feedback wording, never objective scoring. Nothing moved by more than one
level, which is the property that matters: a grade that lands one level
off is a defensible judgement call, not a broken grader.

Golden snapshot regenerated: `tests_benchmark_golden.py`'s pinned values
now read 0.8274 / evidence 132 (was 0.8452 / evidence 131), with the
rationale above recorded in the pinned-assertion's own comment so a
future reader doesn't need this file to understand why the number moved.

**One transient failure worth recording**, not because it's new but
because it recurred: the blind second-opinion grader failed its evidence
check three times on `maths/weak` and again on `maths/partial`
(`none of the 1 evidence quote(s) appear in the student's answer`) before
giving up — non-fatal by design (`_maybe_run_second_opinion` never
degrades the way the primary evidence check does; grader A's score
stands, the second opinion is simply skipped and annotated). Both
submissions graded fine. This is the same failure shape Finding 1
describes for long multi-step algebra answers, on the same two
deliberately-weak/garbled fixture students where it's most likely to
occur — consistent with, not contradicting, that finding.

### The isolation experiment: attempted, and lost to an external crash

Comparing 0.8452 (recorded weeks ago) against 0.8274 (recorded today)
confounds two things: the prompt edit, and ordinary run-to-run variance
(LLM grading is not bit-reproducible at temperature 0 — OpenRouter routes
across providers, and providers vary internally). To bound how much of
the 3-question delta is the prompt versus noise, a third arm was designed:
re-record the **old** prompt, **today**, into a scratch directory, giving

    A. old prompt, old run   = 0.8452  (committed baseline)
    B. old prompt, TODAY     = ?       (isolation arm)
    C. new prompt, TODAY     = 0.8274  (accepted above)

with `|A-B|` estimating pure day-to-day variance and `|B-C|` estimating
the prompt's own effect under identical conditions.

**This arm did not complete.** The run (`isolate_prompt_effect.py`) was
killed by an external session/model transition partway through — 7 of 21
submissions graded, 0 errors up to that point — and `_Tape.save()` only
persists recordings after the *entire* run finishes, so the kill left no
artifact at all: no scratch recordings, no score. The ~70 completed
model calls' worth of billed work produced nothing reusable.

The one thing that mattered was checked immediately on discovering the
crash: the script swaps the old prompt onto disk *before* Django imports
it, and restores the new prompt in a `finally` block on exit — but an
external kill bypasses `finally` entirely, so the repo was found holding
the OLD (pre-fix) prompt on disk, silently undoing the very change this
run was accepted above. It was NOT git-reverted (git showed no diff for
that file — the working copy matched `HEAD` exactly), so this could not
have been caught by a routine `git diff` skim. Recovered from the
script's own static pre-swap backup file, verified byte-identical, and
confirmed against the golden test before anything else proceeded. Flagged
here mainly as a process note: a prompt-swap-in-place technique needs a
crash-safe restore path (a file lock, a restore-on-next-boot check, or
running it somewhere that survives a session teardown) if it's used
again, because `finally` alone is not that.

**Bottom line:** the 0.8452→0.8274 delta is accepted on structural
grounds — adjacent-level only, other metrics flat or improved, one
recurring pre-existing failure mode unrelated to this edit — not on a
variance-isolated measurement. The isolation run remains undone. Anyone
re-litigating whether this delta is signal or noise should re-run
`isolate_prompt_effect.py` (the two prompt snapshots and the scoring
script are all still in the benchmark scratch tooling) rather than trust
a single-run comparison across two different days.

## Deferred to v2 — post-MVP

Everything above ships now. One thing is deliberately held back:

- **Calibration loop** — feed confirmed teacher corrections back as
  grading examples for that course/teacher. This is the largest
  remaining lever on accuracy, and also the riskiest one to ship without
  care: a teacher's grade change often has nothing to do with the AI
  being wrong (a mercy point, a bump after a complaint, a late-work
  adjustment), so a raw override is evidence, not truth. Building this
  needs curation gates — e.g. the new score lands on an actual rubric
  level, the teacher gave a reason, the change isn't an outlier against
  that teacher's own pattern, or (simplest and strongest) the teacher
  explicitly marked it as a teaching example — and needs a live
  benchmark baseline in place *first*, so the effect of turning it on is
  something this suite can actually measure rather than something we
  hope.
