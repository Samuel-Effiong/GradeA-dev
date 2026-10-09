# Answer-extraction benchmark

Validates the pipeline that turns a student's uploaded script into the
answers that get graded:

```text
PDF -> PDFService rasterizer -> extract_answer_with_retry
    -> single call, or chunked extraction + merge
    -> completeness gate -> blank re-read -> grading -> review queue
```

It exists because that pipeline had no benchmark of any kind, which is how
a missing structured-output schema on the chunked path survived until a
code review found it. Building this benchmark then found nine more
defects (N1-N9 in the regression mapping below).

The generated report is `reports/BENCHMARK_REPORT.md`, with the same data
in `reports/benchmark_report.json`.

## What a pass proves, and what it does not

There are two levels, and they answer different questions.

| | Deterministic | Live provider |
|---|---|---|
| Question answered | Does OUR pipeline chunk, attribute, merge, classify and fail correctly? | Does the production model, on documents big enough to chunk, produce what the pipeline needs? |
| Model | `provider.FakeProvider`, answers from the scenario's ground truth | the real production model |
| Cost | free | billed |
| Runs in CI | always | never; opt-in `RUN_REAL_AI=1` |
| Oracle | exact: statuses, pages, ranges, call counts | exact statuses; answer text normalised |

A green deterministic run says nothing about whether a model can read a
page. The fake provider never looks at the images. It does answer per
chunk, seeing only the pages that chunk was given. So a chunk carrying the
wrong pages, or claiming the wrong range, changes its answer and fails the
scenario. That is what keeps the deterministic suite from being circular.

Answer content is compared after normalisation (lower case, markup and
punctuation stripped), never as exact prose (spec section 37). Statuses,
page ranges and call counts are exact.

## Layout

| File | Role |
|---|---|
| `documents.py` | Generates every PDF from a spec, byte-for-byte deterministically. No binary fixtures are committed. |
| `scenarios.py` | The scenario catalogue: stable `AE-NNN` ids, document specs and expected statuses. Pure data; it never imports the pipeline. |
| `provider.py` | The deterministic model: per-chunk answers, failure injection (`ProviderBehaviour`), and plausible model behaviour (`MODEL_QUIRKS`). |
| `harness.py` | Runs a scenario through the REAL pipeline, and holds the shared checks (`full_check`) and failure diagnostics (`describe`). |
| `live.py` | The live corpus and a recorder around the real billed call. |
| `report.py` | Builds the report and the regression mapping. |
| `mutations.py` | Mutation testing: removes one protection at a time and checks the tests notice. |

Test suites, in `ai_processor/` as the other benchmarks' are:

| Module | Spec sections |
|---|---|
| `tests_answer_benchmark_scenarios` | 5-9, 11-14, 16-18, 29, 35, 38 (shape), 39 |
| `tests_answer_benchmark_failures` | 10, 14, 15, 19-22, 27 |
| `tests_answer_benchmark_inputs` | 30 |
| `tests_answer_benchmark_concurrency` | 23, 24, 25 |
| `tests_answer_benchmark_grading` | 26 |
| `tests_answer_chunk_merge` | the merge rule and error handling, at unit level |
| `tests_answer_benchmark_live` | 4B, 26 (real grading), 27, 38 (billed, opt-in) |

## Running it

Deterministic suites (free; about 2.5 minutes, most of it rasterizing):

```bash
python manage.py test \
  ai_processor.tests_answer_benchmark_scenarios \
  ai_processor.tests_answer_benchmark_failures \
  ai_processor.tests_answer_benchmark_inputs \
  ai_processor.tests_answer_benchmark_concurrency \
  ai_processor.tests_answer_benchmark_grading \
  ai_processor.tests_answer_chunk_merge
```

Report (free; about 15 seconds). It exits non-zero on any failing
scenario, or on a known gap that has started passing:

```bash
python manage.py answer_extraction_benchmark
```

Mutation testing (free, slow). **Never run it in a shared checkout.** A
mutant is live on disk while its tests run, so copy the tree first; the
runner refuses any `--root` that contains `.git`. Give the copy its own
test database name as well:

```bash
rsync -a --exclude .git --exclude .mypy_cache --exclude node_modules \
      --exclude __pycache__ ./ /tmp/answer-mutations/
python -m ai_processor.benchmark.answers.mutations \
  --root /tmp/answer-mutations \
  --settings <settings module with a distinct TEST NAME> \
  --report ai_processor/benchmark/answers/reports/mutations.json
```

Each mutant runs with `--failfast`. The test recorded against it is the
first test Django ran that failed (database-backed suites run first). That
proves an assertion caught the mutant; it is not a list of every test that
would. A survivor is re-run against the full label set before it is
reported.

Live provider (**billed**, and slow):

```bash
RUN_REAL_AI=1 python manage.py test ai_processor.tests_answer_benchmark_live

# validate one change on the document it concerns before spending the corpus
RUN_REAL_AI=1 ANSWER_LIVE_ONLY=AE-905 ANSWER_LIVE_REPORT_SUFFIX=_targeted \
  python manage.py test ai_processor.tests_answer_benchmark_live.LiveAnswerExtractionBenchmarkTest
```

That run writes `reports/live_run.json` before it asserts anything. The
record includes, per document:
- pages per chunk and the claimed ranges
- the raw responses
- expected and actual statuses
- discrepancies
- the model that served each call
- tokens, seconds and wallet movement

Rerun the report command afterwards to fold the live results in.

### AI credits

- Deterministic suites, report and mutations make **no** provider calls.
  `execute_graded_task` is replaced, not wrapped.
- The live run makes one billed call per chunk, plus one blank re-read for
  each document of 10 pages or fewer that has blanks. It checks that the
  wallet moved by exactly the tokens the provider reported.

## Scenario catalogue

Ids are stable and never reused. The full table, with each scenario's page
count, execution path, chunk count and result, is regenerated into
`reports/BENCHMARK_REPORT.md`.

| Range | Category | Covers |
|---|---|---|
| AE-001..004 | baseline / status / boundary | single page; blank; not found; two pages, just below the threshold |
| AE-013..030 | chunking | the size matrix: 3-10, 12, 15, 20 pages |
| AE-101..105 | chunking | one nine-page document at 1-5 pages per chunk |
| AE-200..206 | boundary | answers either side of a seam, a blank on a seam, not-found at a seam, an uneven final chunk, an answer spanning chunks (205) and its within-chunk control (206) |
| AE-300..305 | status | all three statuses together; blanks in every position; all blank; all not-found; a script that stops early |
| AE-400..403 | numbering | sequential, gaps, double digit, sub-questions |
| AE-500..502 | layout | many questions per page, mixed statuses, a seam between question pairs |
| AE-600..604 | difficult | synthetic handwriting, noise, rotation, image-heavy pages, combined |
| AE-700..701 | hallucination | a tempting neighbouring answer; an absent question among answered ones |
| AE-800..807 | merge | realistic model behaviour the chunk note invites; answers split across two (803) and three (805) chunks; a continuation sharing its page with another answer and a blank (806); an answer ending exactly at a seam (807) |
| AE-903..921, AE-905, AE-915, AE-916 | live | 3, 6, 7, 9, 12 and 21 pages, plus three split-answer documents (5, 8 and 5 pages); AE-905, AE-906 and AE-916 also go through real grading |

`ThresholdDerivedBoundaryTest` also builds documents at 1, 2,
threshold-1, threshold, threshold+1, 2x threshold and 2x threshold+1
pages. It computes those from `ANSWERS_EXTRACTION_PAGES_PER_CHUNK`, so
they move if the threshold does.

### Numbering

| Form | Status |
|---|---|
| `1, 2, 3`; `Q1, Q2`; `10, 11, 12` | supported (AE-400, AE-402) |
| gaps (`1, 2, 4, 5`) | supported (AE-401) |
| sub-questions (`1(a), 1(b), 2(a)`) | supported as string labels (AE-403) |
| questions in non-numeric order | the result follows the assignment's order when the completeness gate is on, and is sorted on the chunked path without it |
| the same label twice in one assignment | **not supported**: reported as a completeness violation, not mapped |
| labels embedded in surrounding text | depends on the model reading the page; live only |

## The merge rule

A chunk only sees its own pages. Its "not found" therefore means only "not
on my pages". `_merge_chunk_answer` in `ai_processor/services.py` applies:

1. An answer transcribed in two different chunks is **joined** in page
   order. It stays ILLEGIBLE if either part was.
2. Otherwise the better-informed entry wins: ANSWERED, then BLANK or
   ILLEGIBLE, then NOT_FOUND.
3. A BLANK or ILLEGIBLE only counts as informed if its `source_page` lies
   in the chunk. Both absolute and chunk-relative numbering are accepted.
   An empty verdict nobody can place is treated as "not found". This
   matters because the last chunk's note invites the model to write BLANK
   for questions it never saw.
4. On a tie between two empty entries, NOT_FOUND wins. The result does not
   depend on chunk order, and it errs towards review, not towards a zero.

The merge can only join parts the model returns. So the note sent with
every later chunk asks for them: it lists the questions already answered
and tells the model to transcribe any continuation of those answers on
its pages, under the same question number. Until 2026-09-14 it said those
questions "do NOT need to be extracted again", and the real model
dropped the second half of a split answer (AE-905).

**Requirement:** a student's answer must never be silently truncated
because it crosses a chunk boundary. AE-205, AE-803, AE-805, AE-806,
AE-807 and live AE-905, AE-915 and AE-916 hold it. `full_check` also checks
that no question's answer contains another question's answer, so a
continuation joined onto the wrong question cannot pass unnoticed.

**Question labels.** The real model does not write question numbers
consistently: it used `1` in one chunk and `Q1` in the next for the same
question. So before the merge, and before the completeness gate on the
single-call path, every answer is relabelled with the label the
assignment itself declares (`_relabel_answer`). `3`, `"3"`, `"Q3"` and
`"Question 3"` all mean question 3; any other label is kept as written.
An assignment that declares both `1` and `Q1` is left alone, so two real
questions are never merged. Without this, AE-912 was re-read and
re-billed three times (12 calls instead of 4).

## Regression mapping

Every extraction defect found so far, and what keeps it from returning.
The report carries the full lists of tests.

| ID | Defect | Scenarios | Mutations |
|---|---|---|---|
| R1 | shared PDFService state across concurrent uploads | concurrency suite | M20 |
| R2 | chunked path sent no schema | every scenario (`check_schema_sent`) | M01, M02 |
| R3 | `pages_per_chunk` ignored | AE-101..105, threshold tests | M05, M06 |
| R4 | page range taken from the caller | AE-204, every chunked scenario | M07, M08 |
| R5 | NOT_FOUND collapsed into BLANK | AE-003, 203, 300, 304, 305, 800, 802 | M13-M16 |
| R6 | null answer after successful calls | failures suite | - |
| R7 | cancellation retried | cancellation tests | M23-M25 |
| R8 | extraction errors swallowed or causeless | persistent-failure tests | M22, M26, M27 |
| N1 | BLANK after chunk 1 reported NOT_FOUND | AE-600, 601, 801 | M17 |
| N2 | split answer kept only its first half | AE-205, 206 | M18 |
| N3 | later chunks told every question was already found | AE-804 | M19 |
| N4 | single-call path hid credit/access/cancel error types | error-type tests | M25 |
| N5 | empty submission billed as a success | inputs suite | M28 |
| N6 | negative chunk size dropped every page | inputs suite | M12 |
| N7 | benchmark PDFs not byte-deterministic (harness) | determinism suite | M32 |
| N8 | chunk note told the model not to extract found questions again, dropping split answers' continuations (confirmed live) | AE-803, 805, 806, 807; live AE-905, 915, 916 | M33 |
| N9 | question labels merged raw: `1` and `Q1` became two questions, re-billing the script (live) and dropping real answers on a single call | live AE-912, 921, 905; `QuestionNumberStyleTest` | M34, M35 |

## Known limitations

These are recorded here so they cannot be mistaken for coverage.

- **Handwriting is synthetic.** No handwriting font is installed, and real
  scanned scripts are student data that do not belong in the repository.
  `Ink.HANDWRITTEN` is an oblique face with per-glyph jitter. It exercises
  irregular input; it does not validate real handwriting transcription.
- **A continuation joined onto the wrong question** is a model error the
  pipeline cannot always see. The cross-contamination check catches it only
  when the misplaced text matches another question's known answer. On real
  documents, the live split-answer documents are the evidence.
- **The retry policy re-bills successful chunks.** If one chunk never
  succeeds, each of the three outer attempts re-reads every chunk before
  it. For a six-page document that is 12 calls, 3 of them for chunk 1.
  This is pinned by `PersistentChunkFailureTest`; changing it is a billing
  decision.
- **Chunk size is one value per process.** `ANSWERS_EXTRACTION_PAGES_PER_CHUNK`
  is a module constant, so the concurrency suite cannot vary it per
  student. Production cannot either.
- **Not injected:** a database or Redis failure during extraction. The only
  database reads on this path are the cancellation check and the class
  roster, and nothing on it uses Redis.
- **Memory is not measured.** The large-document tests (up to 31 pages in
  11 chunks) assert coverage, ordering and assembly, but do not bound
  memory.
- **Out of scope and untouched, same shape:**
  - The assignment chunker, the grading batches and the grading summary
    raise "failed after 3 attempts" without chaining the cause.
  - `extract_grade_with_retry` does not re-raise `TaskCancelledError`.
  - The chunk note renders its already-found list with a doubled prefix
    (`QQ1`), which is prompt text.
