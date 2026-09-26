# The Grading Handbook

Everything about how AI grading works in this project: how to run it, every
setting that changes its behaviour, and how to read every number it produces.

This is a reference. Jump to what you need.

| If you want to… | Go to |
|---|---|
| Understand how one submission gets graded | [Part 1](#part-1--how-a-submission-is-graded) |
| Read a grade the AI produced | [Part 2](#part-2--reading-the-grading-output) |
| Know what students vs teachers can see | [Part 2.6](#26-who-can-see-what) |
| Change how grading behaves | [Part 3](#part-3--every-setting) |
| Measure how accurate grading is | [Part 4](#part-4--the-benchmark) |
| Read a benchmark report | [Part 5](#part-5--reading-the-benchmark-report) |
| Tell a real change from random noise | [Part 6](#part-6--run-history-and-trends) |
| Fix something that broke | [Part 8](#part-8--troubleshooting) |

---

# Part 1 — How a submission is graded

## 1.1 The short version

A student's answers go through **four stages**. Each stage exists to remove a
specific way the AI can be wrong.

```
   Student's answers
          |
   [1] TIER 0        Multiple-choice questions answered by exact matching.
                     No AI involved. Free, instant, cannot hallucinate.
          |
   [2] CACHE         Has this exact answer been graded before? Reuse it.
                     Guarantees identical answers get identical grades.
          |
   [3] AI GRADING    The remaining questions go to the model, in batches.
                     Evidence is checked. Arithmetic is redone in Python.
          |
   [4] SECOND        Doubtful or high-stakes questions are re-graded by a
       OPINION       DIFFERENT model that cannot see the first grade.
          |
   Final grade + feedback
```

Nothing here can be skipped by the AI. Stages 1, 2 and 4 are code, not prompts.

## 1.2 Stage 1 — Tier 0, the deterministic answer key

**File:** `ai_processor/objective_grading.py`

Multiple-choice and true/false questions do not need an AI. This stage compares
the student's answer to the answer key by **exact text matching**, after
normalising away things that do not change meaning:

- HTML tags and entities (`<b>`, `&amp;`)
- Letter case and extra whitespace
- Smart quotes and dashes
- **Whitespace inside maths**: `$x^2\ln(x)$` and `$x^2 \ln(x)$` are the same
  expression — the space only ends the `\ln` command

It accepts an answer written as a letter (`B`), as a letter with the text
(`B) 10`), or as the option text alone (`10`).

**The critical rule: it refuses to guess.** If the letter says B but the text
matches option C, or the answer matches nothing, the question is marked
`AMBIGUOUS` and handed to the AI instead. Across five benchmark runs this stage
has claimed 34 questions and got **100% of them right**, every time — because
it only ever claims the ones it is certain about.

**Turn it off with:** `GRADING_DETERMINISTIC_OBJECTIVE=False` (costs money, gains nothing)

## 1.3 Stage 2 — The answer cache

**File:** `ai_processor/grading_cache.py`

Before calling the model, the system builds a fingerprint from the question,
the student's answer, and the model name. If that exact combination was graded
before, the stored grade is reused.

Two reasons this exists:

1. **Cost.** Re-grading identical work is wasted money.
2. **Fairness, which matters more.** Two students who write the *same answer*
   must get the *same grade*. Temperature-0 does not guarantee this — the
   provider can route to a different machine. The cache makes it a certainty
   rather than a hope.

**A grade that caused a second-opinion disagreement is never cached**, so a
disputed grade cannot silently spread to other students.

**Settings:** `GRADING_ANSWER_CACHE_ENABLED` (default `True`),
`GRADING_ANSWER_CACHE_TTL_SECONDS` (default 3 days)

## 1.4 Stage 3 — AI grading

Whatever tier 0 and the cache did not resolve goes to the model.

### Batching

If more than **5** questions remain, they are graded in batches of 5 rather
than all at once. Each batch gets a short summary of the whole assignment so
the model has context, not just an isolated slice.

### The rules the model must follow

The full prompt is `ai_processor/GRADING_ASSIGNMENT_PROMPT_5.txt`. The rules
that matter most:

**Discrete scores only.** Every score must be exactly one of the rubric's four
level values. On a rubric of poor=0 / fair=8 / good=15 / excellent=20, the only
legal scores are 0, 8, 15 and 20 — never 13.

Why this matters:

- The rubric is what the teacher actually wrote. A 13 corresponds to nothing
  they described.
- "13 out of 20" *looks* precise, implying a real difference between 13 and 14.
  There isn't one. Forcing a level choice is honest about how fine the
  judgement really is.
- It makes disagreement measurable — two graders can be compared as "one level
  apart", which has meaning.
- It is defensible to a student: *"you got good, not excellent, because X"*
  beats *"you got 13"*, which invites "why not 14?".

**Reason before scoring.** The output fields are deliberately ordered so the
model must quote the answer, argue for a level, *then* name the score. Models
generate text in order, so asking for the score first produced a guess followed
by a rationale written to agree with it.

**Evidence.** Every question awarding points must quote the student's own words
verbatim. See 1.6.

### Arithmetic is Python's, not the model's

After the model responds, `_finalize_grading_result` (`ai_processor/services.py`)
**recomputes everything**:

1. Coerces each score to a number
2. Clamps it to `[0, question points]`
3. **Snaps** it to the nearest rubric level (ties round *down*)
4. Sums the totals and recomputes the percentage

The model's own totals are discarded. It can misjudge an answer; it cannot
produce a total that does not add up.

If a score gets snapped, the original is recorded as `snapped_from` so nothing
is hidden. Across all five benchmark runs the snapping rate has been **0%** —
the model has never returned an off-level score. The guard has never been
needed, and stays because "never needed yet" is not "cannot happen".

## 1.5 Stage 4 — The second opinion

**File:** `ai_processor/second_opinion.py`

Selected questions are re-graded by a **different model** which is shown the
question and the answer but **not** the first grade. It cannot anchor to a
number it never saw.

### What gets selected

| Trigger | Meaning |
|---|---|
| `low_confidence` | The run's overall confidence was below the threshold |
| `flagged:<TYPE>` | The model itself flagged that question |
| `borderline_level` | The model said *this answer sat between two levels* |
| `high_stakes` | Worth ≥ 15 points |
| `subjective_type` | Essay or short-answer |
| `qa_sample` | Random sample, to keep "easy" cases measured |

**Never selected:** unanswered questions and tier-0 questions — there is no
judgement call to dispute.

### Disagreement severity

When the two graders differ, the gap is graded for the teacher's attention:

| Tier | When |
|---|---|
| `critical` | ≥ 2 rubric levels apart, or gap ≥ 50% of the question's points |
| `moderate` | Gap ≥ 25% of the points |
| `borderline` | Smaller than that |

**Severity never changes a score.** Grader A's grade stands; the disagreement
routes a human's attention. The submission gets `review_tier` and
`review_severity` for sorting the review queue.

## 1.6 The evidence check

**File:** `ai_processor/evidence.py`

The model must quote the student's actual words to justify awarding points, and
those quotes are **string-matched against the real answer**. A grader forced to
cite evidence that is mechanically checked cannot invent a justification. It is
the cheapest possible second opinion: zero extra model calls.

Matching tolerates formatting that does not change meaning — HTML, case,
whitespace, smart quotes — and, because it grades a lot of maths and chemistry,
**LaTeX typesetting**:

| Student wrote | Model quoted | Verdict |
|---|---|---|
| `$[H_2] = 0.100$` | `[H2] = 0.100` | ✅ same fact, re-typeset |
| `\frac{a}{b}` | `a/b` | ✅ same, where unambiguous |
| `\rightarrow` | `→` | ✅ same symbol |
| `\frac{a+1}{b}` | `a+1/b` | ❌ genuinely different — reads as `a + (1/b)` |
| "converts light energy" | "turns light into energy" | ❌ paraphrase, not a quote |

That last-but-one row is the important one: the tolerance stops exactly where
meaning could change.

**Modes** (`GRADING_EVIDENCE_ENFORCEMENT`):

- `strict` (default) — a failure rejects the batch and re-asks the model
- `log` — record it, do not reject
- `off` — do not check

**The safety net:** on the *final* retry, strict silently degrades to `log`.
A grade carrying one unverified quote is far better for a student than **no
grade at all** — which is exactly what used to happen. In the first benchmark
run one submission in 21 got no grade because of this, and the student most
likely to trigger it was the one showing the most working.

## 1.7 What the teacher can add

`Assignment.custom_ai_prompt` lets a teacher add instructions (*"always require
units"*). They are spliced in as **supplementary** — they cannot override the
rubric or the scoring rules.

**Kill switch:** `GRADING_CUSTOM_INSTRUCTIONS_ENABLED=False`

---

# Part 2 — Reading the grading output

Grading produces one JSON object, stored in `StudentSubmission.feedback`.

## 2.1 Top level

```json
{
  "grading_summary":              { "total_score": 15, "max_total_points": 80, "percentage": 18.75 },
  "question_evaluations":         [ ... one per question ... ],
  "score_calculation_verification": { ... proof the arithmetic is right ... },
  "overall_performance_analysis": { ... strengths and gaps across the paper ... },
  "grading_confidence":           85,
  "recommendations":              { "for_student": [...], "for_teacher": [...] },
  "grading_model":                "x-ai/grok-4.3",
  "second_opinion":               { ... only if it ran ... }
}
```

## 2.2 One question

```json
{
  "question_number": 1,
  "question_type": "ESSAY",
  "max_points": 20,

  "evidence_quotes": ["the student's own words, copied exactly"],
  "evaluation_rationale": "why this level and not the one above or below",
  "level_decision": "clear",
  "level_achieved": "good",
  "score_awarded": 15,

  "strengths": [...],
  "weaknesses": [...],
  "improvement_suggestions": [...],
  "feedback_for_student": "written to the student, directly",

  "flag_for_review": null,
  "evidence_verified": true,
  "graded_by": "x-ai/grok-4.3"
}
```

**Field by field:**

| Field | What it means |
|---|---|
| `score_awarded` | Always exactly one rubric level's points |
| `level_achieved` | `excellent`/`good`/`fair`/`poor`, or `correct`/`incorrect`/`not_attempted` |
| `evidence_quotes` | Verbatim student text. **Only verified quotes survive** — unverified ones are stripped |
| `evaluation_rationale` | The reasoning that *decided* the level |
| `level_decision` | `clear` or `borderline` — was this a close call? Routes second opinions; never changes the score |
| `evidence_verified` | `false` means the quote could not be found in the answer — grade kept, flagged |
| `unverified_evidence_count` | How many quotes were dropped (only present if some were) |
| `snapped_from` | The model's raw score before it was corrected (only present if snapped) |
| `graded_by` | Model name, or `deterministic` for tier 0 |
| `flag_for_review` | `BORDERLINE_SCORE`, `EXTRACTION_ERROR`, `PLAGIARISM_CONCERN`, `OFF_TOPIC_ANSWER` |

## 2.3 The arithmetic proof

```json
{
  "individual_scores": [0, 10, 0, 5],
  "manual_sum": 15,
  "verification_status": "PASS",
  "calculation_notes": "Score arithmetic calculated by the system... Model-reported totals are not used."
}
```

This is Python showing its work. `verification_status` is not the model's
opinion.

## 2.4 The second opinion block

```json
{
  "model": "deepseek/deepseek-v4-pro",
  "selected": { "1": ["low_confidence", "high_stakes"] },
  "agreements": [2, 3, 4],
  "disagreements": [
    {
      "question_number": 1,
      "a": { "score_awarded": 0,  "level_achieved": "poor", "evaluation_rationale": "..." },
      "b": { "score_awarded": 10, "level_achieved": "fair", "evaluation_rationale": "..." },
      "severity": { "gap_points": 10.0, "gap_fraction": 0.4, "levels_apart": 1, "tier": "moderate" }
    }
  ]
}
```

`a` is the grade that stands. `b` is the second grader's view — **information,
not an override.**

Other possible states: `{"skipped": "..."}` (e.g. out of credits, no
independent model available) or `{"error": "..."}`. In every case grader A's
result stands.

## 2.5 Confidence

`grading_confidence` (0–100) is the model's self-report for the whole run.

**Be sceptical of it.** Across benchmark runs, 120 of 124 questions came back at
≥ 80. With almost no spread there is nothing to threshold on, so the
`low_confidence` trigger effectively never fires. This is a documented open
finding, not a working signal.

`level_decision` was added to replace it with a per-question judgement — but it
has also reported `clear` on every question across two live runs, so it is
**equally unproven so far**. Watch both; trust neither yet.

## 2.6 Who can see what

Students and teachers see **different** things, enforced in
`students/serializers.py`.

**A student sees only:** question text, their answer, max points, score, level,
strengths, weaknesses, improvement suggestions, and feedback written for them —
plus the totals and their own recommendations.

**A student never sees:** `evaluation_rationale`, `evidence_quotes`,
`level_decision`, `graded_by`, `flag_for_review`, `evidence_verified`, the
**entire second-opinion block**, or `for_teacher` recommendations.

This is built as a **whitelist**, not a blocklist — so a new field added to the
grading output is hidden from students **by default** rather than leaking until
someone remembers to hide it. If you add a field and want students to see it,
you must add it to `_STUDENT_EVALUATION_FIELDS` deliberately.

The reason: internal grader disagreement is for the teacher's judgement, not
ammunition in a grade dispute.

---

# Part 3 — Every setting

All are environment variables read in `AutoGrader/settings.py`.

## 3.1 Core pipeline

| Setting | Default | Effect |
|---|---|---|
| `GRADING_DETERMINISTIC_OBJECTIVE` | `True` | Tier 0 answer-key matching |
| `GRADING_RESPONSE_SCHEMA_ENABLED` | `True` | Force structured JSON output |
| `GRADING_EVIDENCE_ENFORCEMENT` | `strict` | `strict` / `log` / `off` |
| `GRADING_ANSWER_CACHE_ENABLED` | `True` | Reuse grades for identical answers |
| `GRADING_ANSWER_CACHE_TTL_SECONDS` | `259200` (3 days) | Cache lifetime |
| `GRADING_CUSTOM_INSTRUCTIONS_ENABLED` | `True` | Honour `Assignment.custom_ai_prompt` |
| `GRADING_MAX_IMAGES_PER_CALL` | `5` | Cap on diagrams sent per call |

## 3.2 Second opinion

| Setting | Default | Effect |
|---|---|---|
| `GRADING_SECOND_OPINION_ENABLED` | `True` | Master switch |
| `GRADING_SECOND_OPINION_MODELS` | `deepseek/deepseek-v4-pro,google/gemini-2.5-flash` | Tried in order; must differ from grader A |
| `GRADING_SECOND_OPINION_MIN_CONFIDENCE` | `80` | Below this, re-read everything |
| `GRADING_SECOND_OPINION_HIGH_POINTS` | `15` | Points at which a question always qualifies |
| `GRADING_SECOND_OPINION_SAMPLE_RATE` | `0.05` | Random QA sampling |
| `GRADING_SECOND_OPINION_ON_BORDERLINE` | `True` | Escalate self-declared close calls |
| `GRADING_SECOND_OPINION_SUBJECTIVE_TYPES` | `ESSAY,SHORT-ANSWER` | Types always re-read |
| `GRADING_DISAGREEMENT_CRITICAL_FRACTION` | `0.5` | Critical tier threshold |
| `GRADING_DISAGREEMENT_MODERATE_FRACTION` | `0.25` | Moderate tier threshold |

## 3.3 Benchmark and archive

| Setting | Default | Effect |
|---|---|---|
| `ENABLE_AI_LIVE_QA` | `False` | Allows the **weekly paid** benchmark job |
| `GRADING_BENCHMARK_DAY_OF_WEEK` | `0` (Sunday) | When the weekly job runs |
| `BENCHMARK_ARCHIVE_ENABLED` | `True`, auto-`False` under tests | Upload raw run archives |
| `BENCHMARK_ARCHIVE_STORAGE` | `RawMediaCloudinaryStorage` | Where archives go |
| `BENCHMARK_ARCHIVE_PREFIX` | `benchmark_archives` | Folder name |

> **Note.** `BENCHMARK_ARCHIVE_*` are forced off while `manage.py test` runs.
> This project's tests run with `ENVIRONMENT=local`, and `local` maps to **real
> Cloudinary** — so without that guard, a test that saved a file would make a
> live network call using real credentials.

---

# Part 4 — The benchmark

## 4.1 What it is, and its one big limitation

The benchmark is a fixed exam with a known right answer, graded by the live
system so accuracy can be measured:

- **3 assignments** — Maths (9 questions), Chemistry (6), History & Literature (4)
- **7 students** — from excellent to barely trying
- **133 graded questions** per full run
- Every answer hand-written to land on a *specific* rubric level

**The limitation you must keep in mind: the same person wrote the student
answers and the "correct" grades.** So it measures agreement with that author,
not with a real teacher. On the one question where the benchmark and the model
disagreed out of band, **the model was right and the benchmark was wrong**.
Treat the numbers as directional, not authoritative.

### The deliberate probes

| Student | Tests |
|---|---|
| `fluent_wrong` | Confident, well-written, factually **wrong** — is polish mistaken for correctness? |
| `twin` | Byte-identical answers to `strong` on two questions — must score identically |
| `middling` Q1 | Mathematically equivalent but written differently — tier 0 must **defer**, the AI must award full marks |
| `partial` | Blank answers — must be `not_attempted`, not invented |

## 4.2 The three modes

| Mode | Cost | What it does |
|---|---|---|
| `replay` | **Free** | Re-uses saved model responses. Deterministic. |
| `record` | **Paid** | Real model calls, saves every response for future replays. |
| `live` | **Paid** | Real model calls, saves nothing. |

**Use `record`, not `live`** — same price, and `record` keeps the responses.

**What replay can and cannot catch:**

- ✅ Catches: *our* code changing grades (same inputs, different output)
- ❌ Cannot catch: the model provider changing behaviour — the responses are frozen

## 4.3 Running it

```bash
# Free. Safe to run any time. This is what the nightly job runs.
python manage.py grading_benchmark --mode replay

# PAID — roughly 800k tokens and 45-65 minutes.
python manage.py grading_benchmark --mode record

# Just one paper / one student (cheap for debugging)
python manage.py grading_benchmark --mode replay --assignment maths --student twin

# Readable PDFs of the exam papers and answers
python manage.py grading_benchmark --pdf --out ./benchmark_artifacts

# Machine-readable
python manage.py grading_benchmark --mode replay --json
```

### Before spending money — check three things

```bash
# 1. Are you pointed at a LOCAL database? (This repo has been pointed at
#    production before. Grading writes nothing, but check anyway.)
grep ENVIRONMENT .env
python -c "import django,os;os.environ.setdefault('DJANGO_SETTINGS_MODULE','AutoGrader.settings');django.setup();
from django.conf import settings;print(settings.DATABASES['default']['HOST'])"

# 2. Is the dataset self-consistent? The command refuses to run if not.
python manage.py test ai_processor.tests_grading_benchmark

# 3. Does the billing user have credits? A run needs ~800k tokens plus a
#    flat 20,000-credit headroom per call.
```

**Do not wrap a paid run in a timeout.** Recordings are written only when the
run *completes*; killing it at 15 minutes wastes the entire spend.

### All flags

| Flag | Purpose |
|---|---|
| `--mode {replay,record,live}` | Default `replay` |
| `--assignment KEY` | Limit to `maths` / `chemistry` / `humanities` (repeatable) |
| `--student KEY` | Limit to one student (repeatable) |
| `--pdf` / `--out DIR` | Render papers as PDFs |
| `--json` | JSON instead of a text report |
| `--baseline FILE` | Compare against a saved report |
| `--save-baseline FILE` | Save this run as the new baseline |
| `--teacher-email` | Bill a specific teacher |
| `--no-history` | Record nothing — behaves exactly as before run history existed |

## 4.4 The scheduled jobs

| Job | When | Cost | Purpose |
|---|---|---|---|
| `nightly_grading_benchmark_replay` | 01:30 daily | Free | Did *our* code change the grades? |
| `weekly_grading_benchmark_live` | 03:00 Sundays | **Paid** | Did the *model* change under us? |

The weekly one is a **no-op unless `ENABLE_AI_LIVE_QA=True`**.

> **Beat must be restarted** for schedule changes to register — this project
> uses the database scheduler, which reads the schedule only at startup.

---

# Part 5 — Reading the benchmark report

## 5.1 Rubric levels, and what "within one" means

Levels are a ladder. **Index 0 is the best.**

| Index | Level |
|---|---|
| 0 | excellent |
| 1 | good |
| 2 | fair |
| 3 | poor |

- **Exact match** — the AI picked the same level as the answer key
- **Within one level** — same, or one rung away
- **Out-of-band** — two or more rungs away. A real failure.

**Why "within one" is the honest bar:** two competent human teachers routinely
land one level apart on the same essay. Demanding exact matches would fail the
AI for behaving exactly like a human marker. Objective and computational
questions still require an *exact* score.

## 5.2 The sections

**1. ACCURACY vs ground truth**
```
questions graded      : 133
exact level match     : 112 (84.2%)
within one level      : 133 (100.0%)
mean level error      : 0.0827   (+ = lenient, - = harsh)
```
`mean level error` is the one to watch: **positive means too generous**,
negative means too harsh. Near zero is well-calibrated.

**2. BY QUESTION TYPE / 3. BY SUBJECT** — the same figures split up. This is
where problems become findable: an overall 84% hid Chemistry sitting at 75% for
evidence while other subjects were at 100%.

**4. STUDENT RANKING** — Spearman correlation (−1 to 1) between the AI's
ordering of students and the true ordering. **This is a different question from
accuracy.** A grader can be uniformly too generous yet still rank everyone
correctly; those are different problems with different fixes. Above 0.9 is
strong.

**5. DETERMINISTIC TIER 0 — must be 100%.** Anything less is a serious bug:
tier 0 only claims questions it is certain about.

**6. CROSS-STUDENT CONSISTENCY** — byte-identical answers must score
identically. A failure here is indefensible to a student regardless of which
score was "right".

**7. PIPELINE HEALTH**
```
scores snapped to a rubric level : 0 (0.0% of questions)
evidence verified                : 97/98 (99.0%)
second opinion coverage          : {'ran': 20, 'skipped': 0, 'error': 1, 'not_run': 0}
disagreements                    : 7/84 (8.3%) tiers={'moderate': 7}
```
- **snapped** — how often the model ignored "discrete scores only". Should be 0.
- **evidence verified** — how often quotes checked out. Dropping means the model
  is paraphrasing or re-typesetting its quotes.
- **disagreements** — how often the two graders differed. Some disagreement is
  healthy; zero would suggest the second grader is not independent.

**8. CONFIDENCE CALIBRATION** — does low confidence predict being wrong? See 2.5:
currently **no**, for lack of spread.

**9. LEVEL DECISION CALIBRATION** — does `borderline` predict being wrong? Also
unproven — the grader has reported `clear` on every question so far.

**10. COST** — tokens, submissions, wall-clock.

**11. OUT-OF-BAND FAILURES** — every question ≥ 2 levels off. **Read each one by
hand before accepting a run.** Sometimes the benchmark is wrong.

**12. RUN ERRORS** — submissions that failed entirely. Should be zero.

**13. VS BASELINE** — regression check against a saved report.

## 5.3 What "good" looks like

| Metric | Healthy | Investigate |
|---|---|---|
| Within one level | 100% | < 98% |
| Tier 0 accuracy | 100% | anything less |
| Consistency probes | all consistent | any failure |
| Run errors | 0 | any |
| Snapping rate | 0% | > 2% |
| Evidence verified | > 97% | falling across runs |
| Mean level error | −0.05 to +0.15 | drifting up over runs |
| Ranking (Spearman) | > 0.9 | < 0.85 |

---

# Part 6 — Run history and trends

## 6.1 The problem it solves

For three runs in a row, every write-up ended with a version of the same
sentence: *"a single run cannot distinguish 'the fix didn't help' from
'noise'."* Nothing kept the numbers — the baseline held only the latest run and
the scheduled jobs threw their reports away.

Now every run is kept at three levels:

| Tier | Contents | Where | Size/year |
|---|---|---|---|
| 1 | Headline metrics, one line per run | `benchmark/history/runs.jsonl` (git) | ~200 KB |
| 2 | One line per question per run | `benchmark/history/questions.jsonl` (git) | ~1.7 MB |
| 3 | Complete raw bundle | **Cloudinary**, paid runs only | ~50 MB |

**Why Tier 3 matters:** Tiers 1 and 2 store numbers we already thought to
record. Tier 3 keeps the raw material, so it can answer questions **nobody has
thought of yet** — and a metric invented next month can be computed for every
past run. This is not hypothetical: the LaTeX evidence bug was found by
re-reading the model's actual quotes, and that was only possible because that
run happened to be the most recent one.

Each archive contains its own Tier 1 and 2 rows, so the git files can be
**rebuilt from the archives** — which is what makes a run executed on the
server, where Celery cannot commit to git, recoverable.

## 6.2 Commands

```bash
# Is the latest change real, or normal variation?
python manage.py grading_benchmark_history --trends

# Which questions does the grader mark inconsistently?
python manage.py grading_benchmark_history --unstable

# One question's full history
python manage.py grading_benchmark_history --question maths/strong/4

# A page you can email to someone
python manage.py grading_benchmark_history --html trends.html

# Mirror files into the database (idempotent)
python manage.py grading_benchmark_history --sync-db

# Restore the files from downloaded archives
python manage.py grading_benchmark_history --rebuild-from-archives ./archives
```

## 6.3 Reading `--trends`

```
exact match
   values : 84.7%, 85.7%, 82.7%, 86.5%, 84.2%
   normal : 82.2% to 87.4%   (seen 82.7%–86.5% over 5 runs)
   latest : 84.2% (down) — within normal variation
```

- **normal** = average ± 2× the spread, computed from the *earlier* runs only.
  (Including the run being judged would drag the average toward it and hide the
  very change you are looking for.)
- **latest** = the verdict. `within normal variation` means *do not act on this.*

So the 2-point drop above is **noise, measured rather than guessed**. Whereas:

```
tokens per run
   values : 495,602, 617,213, 691,518, 803,980
   latest : 803,980 (up) OUTSIDE the normal range — 2.5x the usual spread
```

That is a **real finding**: grading has become steadily more expensive with each
round of accuracy work.

## 6.4 Two traps the statistics avoid

**Replay runs are excluded by default.** Replay re-reads fixed responses, so its
numbers are *identical every time*. Averaging in 365 identical nightly rows
would shrink the apparent spread to nearly zero, after which every genuine run
would look like a dramatic anomaly. They are still recorded — they answer a
different question: same inputs, changed code, so any movement is **our**
regression.

**Partial runs are excluded.** `--assignment maths` grades 63 of 133 questions;
its rates are not comparable.

Override with `--include-replay` / `--include-partial`.

**Fewer than 3 runs → no range is stated.** An honest "not enough runs yet"
beats a spread computed from two points that reads as authoritative.

## 6.5 `--unstable`, the genuinely new capability

For each question: has the same student answer received a **different grade on
different runs**? No other metric can ask this. The `twin` probe is close but
different — it checks two identical answers agree *within* one run, not that one
answer is graded the same way *across* runs.

A question that flip-flops is one the grader is unreliable on.

---

# Part 7 — Safety guarantees

## 7.1 Recording can never break grading

A paid run costs real money and about an hour. Losing one to a bug in the
bookkeeping would be far worse than having no bookkeeping. Therefore:

- Every recording step is individually guarded; a failure logs a warning and the
  run continues
- The report, `--json` output and exit code are **identical** whether recording
  succeeds or fails completely
- Order is cheapest-and-most-reliable first: history files and the local archive
  copy hit disk **before** anything slow; the upload goes **last**
- If the upload fails, the **local copy survives** and can be pushed later
- `--no-history` disables all of it

This is tested by forcing each component to fail and asserting the output is
byte-identical.

## 7.2 The golden-master test

`ai_processor/tests_benchmark_golden.py` pins the entire replay report against a
committed snapshot. Because replay is deterministic, **any** difference is a
change *we* made — never the model drifting — which makes exact equality both
safe and extremely sensitive.

Regenerate only when the numbers are *meant* to move:

```bash
UPDATE_BENCHMARK_GOLDEN=1 python manage.py test ai_processor.tests_benchmark_golden
```

Then read `git diff` on the fixture and confirm every change was intended.
**Never regenerate to make a red test go green.**

## 7.3 Tests

```bash
python manage.py test ai_processor students     # 553 tests
python manage.py test ai_processor.tests_benchmark_golden    # the tripwire
```

---

# Part 8 — Troubleshooting

**"No recorded response for prompt `<hash>`"**
The dataset or a prompt changed, so the saved responses no longer match. This is
the system *refusing to silently spend money*. Fix: `--mode record` (paid), or
revert the change.

**"Dataset is internally inconsistent — refusing to run"**
An expected score is not a reachable rubric level. The command blocks *before*
spending anything. Fix the dataset.

**"InsufficientCreditsError"**
The billing user is out of credits. Each call also reserves a flat 20,000-credit
headroom.

**"AI access denied: Could not verify subscription status"**
Usually an **unapplied migration** — the code references a column the database
does not have. Fix: `python manage.py migrate`.

**Evidence rejections in the logs**
```
evidence_rejected batch=1/2 attempt=1 — question 8: none of the 1 evidence
quote(s) appear in the student's answer (fabricated evidence)
```
Normal in small numbers; it retries. If followed by `evidence_degraded`, the
safety net fired and the student still got a grade. A *rising rate across runs*
is worth investigating.

**A test failed, then passed unchanged**
If another session is editing this repo at the same time, a file saved
mid-test-run causes phantom failures. Re-run before believing it.

**Scheduled jobs are not running**
Beat reads the schedule only at startup. Restart it.

---

# Appendix — File map

| File | Role |
|---|---|
| `ai_processor/services.py` | The grading pipeline; owns all arithmetic |
| `ai_processor/GRADING_ASSIGNMENT_PROMPT_5.txt` | The grader's instructions |
| `ai_processor/objective_grading.py` | Tier 0 answer-key matching |
| `ai_processor/evidence.py` | Verbatim quote verification |
| `ai_processor/second_opinion.py` | Selection, comparison, severity |
| `ai_processor/grading_cache.py` | Identical-answer cache |
| `ai_processor/grading_schemas.py` | Output shape — **field order is behaviour** |
| `ai_processor/benchmark/dataset.py` | The 3 assignments |
| `ai_processor/benchmark/submissions.py` | The 7 students' answers |
| `ai_processor/benchmark/scoring.py` | Metric computation |
| `ai_processor/benchmark/history.py` | Run history, Tiers 1–2 |
| `ai_processor/benchmark/archive.py` | Raw archive, Tier 3 |
| `ai_processor/benchmark/analysis.py` | Trends and normal ranges |
| `ai_processor/benchmark/FINDINGS.md` | **What every run actually showed** |

**Start with `FINDINGS.md`** if you want to know how well grading currently
works and what is still open.
