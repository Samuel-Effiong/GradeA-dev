# Run 8 isolation experiment — raw data

The measurements behind the Run 8 write-up in `../FINDINGS.md`. Kept so
the conclusion can be re-checked rather than taken on trust: the headline
claim (the `answer_status` prompt edit is a **reproducibility** regression,
not a severity one) rests on repeated runs, and repeated-run claims are
worth nothing if the runs themselves are not inspectable.

## The question

Comparing one run of the old prompt against one run of the new one
confounds the prompt's effect with ordinary run-to-run variance — LLM
grading is not bit-reproducible even at temperature 0, because OpenRouter
routes across providers and providers vary internally. So both prompts
were run three times each, **alternating** B,C,B,C,B,C. Alternating
matters: running all-B then all-C would let provider drift across a
multi-hour window land entirely on one arm and look like the prompt.

    B = OLD prompt (before the answer_status section)
    C = NEW prompt (with it)

## Files

| file | contents |
|---|---|
| `results.jsonl` | one row per run: exact_rate, within_one_level_rate, mean_level_error, per-type and per-subject breakdowns, token cost |
| `questions.jsonl.gz` | one row per graded question per run (6 × 168 = 1008). The flip analysis runs off this; aggregates alone cannot distinguish "the same questions break every time" from "different questions break each time" |
| `quarantined_results.jsonl` | runs EXCLUDED from the analysis, kept deliberately — see below |
| `isolation_harness.py` | the harness that produced all of it |

## Result

    old prompt: 0.8333, 0.8333, 0.8393   mean 0.8353
    new prompt: 0.8095, 0.8155, 0.8155   mean 0.8135

    gap 3.7 questions, within-arm sd 0.5 questions -> 7.7x
    ranges disjoint: the old prompt's WORST run beats the new prompt's BEST

Real, then — but not because the new prompt breaks particular questions.
**Zero** questions are exact under all three old-prompt runs and non-exact
under all three new-prompt runs. What changed is stability: 19 of 168
questions change verdict between identical runs under the new prompt,
against 14 under the old. The error *character* is unchanged (±1 level, in
near-identical proportions).

`scoring.score_reproducibility()` was added off the back of this and
reproduces the 14/19 figures independently from `questions.jsonl.gz`.

## Why two runs are quarantined

Both are excluded for the same reason: they are **not comparable**, not
because their numbers were inconvenient.

* A run that lost 5 submissions to a network interruption graded 113/138
  instead of 168. A run that grades a *different question set* cannot be
  averaged with one that does not, and including it would have shifted
  every aggregate silently.
* Its per-question rows initially survived that quarantine under the same
  run id as its replacement, giving one arm 306 rows for a single run and
  inflating its instability count. That was caught only because a derived
  statistic (36.0 non-exact/run) was arithmetically impossible against
  that arm's own exact_rate (which implies 27.7).

The harness now rejects any run that is not a clean 168 questions with 0
errored submissions. The second failure is the more instructive one:
**cross-check derived statistics against each other** — an aggregate that
cannot be reconciled with a second aggregate is the cheapest corruption
detector there is.

## Reproducing

`isolation_harness.py` makes real, billed calls (~1M tokens per run, ~2h).
It resumes by run id, so completed runs are skipped and a kill costs at
most the run in flight. It selects the arm by rebinding
`ai_processor.services.GRADING_ASSIGNMENT_PROMPT` **in memory** — an
earlier version swapped the file on disk and restored it in a `finally`
block, and an external kill bypassed `finally`, leaving the repo holding
the wrong prompt with no git diff to reveal it.
