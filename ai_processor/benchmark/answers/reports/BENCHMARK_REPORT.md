# Answer-extraction benchmark report

Generated 2026-09-14T13:30:14Z (deterministic run took 13.8s). Regenerate with `python manage.py answer_extraction_benchmark`.

## Summary

```text
Total scenarios:                   55
Passed:                            55
Failed:                            0
Known gaps (reported, not passed): 0
Known gaps now passing:            0
Skipped:                           0
Real-provider scenarios:           9
Deterministic scenarios:           55
Single-call scenarios:             13
Chunked scenarios:                 42
Multi-page scenarios:              45
Cross-boundary scenarios:          11
BLANK scenarios:                   14
NOT_FOUND scenarios:               11
Handwritten (synthetic) scenarios: 2
Failure-injection tests:           62
Concurrency tests:                 4
Grading-integration tests:         1
Mutation scenarios:                35
Mutations killed:                  35
Production pages per chunk:        3
```

## Coverage by category

```text
ANSWERED                         262/262
BLANK                             17/17
BOUNDARY                           9/9
CHUNKING                          42/42
CROSS-CHUNK                        4/4
NOT_FOUND_IN_DOCUMENT             16/16
PAGE ATTRIBUTION                  42/42
category: baseline                 1/1
category: boundary                 8/8
category: chunking                16/16
category: difficult                5/5
category: hallucination            2/2
category: layout                   3/3
category: merge                    8/8
category: numbering                4/4
category: status                   8/8
```

Status rows count expectations; the others count scenarios. A known gap counts against its row - it is not a pass.

## Scenarios

| ID | Name | Category | Pages | Path | Chunks | Status |
|---|---|---|---|---|---|---|
| AE-001 | single-page-basic | baseline | 1 | single-call | 1 | PASS |
| AE-002 | single-page-blank | status | 1 | single-call | 1 | PASS |
| AE-003 | single-page-not-found | status | 1 | single-call | 1 | PASS |
| AE-004 | two-page-below-threshold | boundary | 2 | single-call | 1 | PASS |
| AE-013 | size-matrix-3-pages | chunking | 3 | chunked | 1 | PASS |
| AE-014 | size-matrix-4-pages | chunking | 4 | chunked | 2 | PASS |
| AE-015 | size-matrix-5-pages | chunking | 5 | chunked | 2 | PASS |
| AE-016 | size-matrix-6-pages | chunking | 6 | chunked | 2 | PASS |
| AE-017 | size-matrix-7-pages | chunking | 7 | chunked | 3 | PASS |
| AE-018 | size-matrix-8-pages | chunking | 8 | chunked | 3 | PASS |
| AE-019 | size-matrix-9-pages | chunking | 9 | chunked | 3 | PASS |
| AE-020 | size-matrix-10-pages | chunking | 10 | chunked | 4 | PASS |
| AE-022 | size-matrix-12-pages | chunking | 12 | chunked | 4 | PASS |
| AE-025 | size-matrix-15-pages | chunking | 15 | chunked | 5 | PASS |
| AE-030 | size-matrix-20-pages | chunking | 20 | chunked | 7 | PASS |
| AE-101 | chunk-size-1 | chunking | 9 | chunked | 9 | PASS |
| AE-102 | chunk-size-2 | chunking | 9 | chunked | 5 | PASS |
| AE-103 | chunk-size-3 | chunking | 9 | chunked | 3 | PASS |
| AE-104 | chunk-size-4 | chunking | 9 | chunked | 3 | PASS |
| AE-105 | chunk-size-5 | chunking | 9 | chunked | 2 | PASS |
| AE-200 | answer-immediately-before-boundary | boundary | 4 | chunked | 2 | PASS |
| AE-201 | answer-immediately-after-boundary | boundary | 5 | chunked | 2 | PASS |
| AE-202 | blank-exactly-on-boundary | boundary | 4 | chunked | 2 | PASS |
| AE-203 | not-found-at-boundary | boundary | 4 | chunked | 2 | PASS |
| AE-204 | uneven-final-chunk | boundary | 7 | chunked | 3 | PASS |
| AE-205 | answer-spans-chunk-boundary | boundary | 5 | chunked | 2 | PASS |
| AE-206 | answer-spans-pages-within-one-chunk | boundary | 3 | chunked | 1 | PASS |
| AE-300 | all-three-states-together | status | 2 | single-call | 1 | PASS |
| AE-301 | blank-between-answered | status | 1 | single-call | 1 | PASS |
| AE-302 | blank-at-end-of-page | status | 1 | single-call | 1 | PASS |
| AE-303 | all-blank | status | 3 | chunked | 1 | PASS |
| AE-304 | all-not-found | status | 1 | single-call | 1 | PASS |
| AE-305 | document-ends-before-later-questions | status | 2 | single-call | 1 | PASS |
| AE-400 | numbering-sequential | numbering | 4 | chunked | 2 | PASS |
| AE-401 | numbering-with-gaps | numbering | 4 | chunked | 2 | PASS |
| AE-402 | numbering-double-digit | numbering | 3 | chunked | 1 | PASS |
| AE-403 | numbering-subquestions | numbering | 4 | chunked | 2 | PASS |
| AE-500 | many-questions-one-page | layout | 1 | single-call | 1 | PASS |
| AE-501 | mixed-status-dense-page | layout | 1 | single-call | 1 | PASS |
| AE-502 | two-per-page-across-chunks | layout | 4 | chunked | 2 | PASS |
| AE-600 | handwritten-synthetic | difficult | 4 | chunked | 2 | PASS |
| AE-601 | noisy-scan | difficult | 4 | chunked | 2 | PASS |
| AE-602 | rotated-pages | difficult | 3 | chunked | 1 | PASS |
| AE-603 | image-heavy | difficult | 4 | chunked | 2 | PASS |
| AE-604 | noisy-and-handwritten | difficult | 3 | chunked | 1 | PASS |
| AE-700 | tempting-neighbouring-answer | hallucination | 1 | single-call | 1 | PASS |
| AE-701 | absent-question-with-answered-neighbours | hallucination | 1 | single-call | 1 | PASS |
| AE-800 | last-chunk-blanks-questions-it-never-saw | merge | 4 | chunked | 2 | PASS |
| AE-801 | blank-with-chunk-relative-source-page | merge | 5 | chunked | 2 | PASS |
| AE-802 | blank-the-model-cannot-place | merge | 4 | chunked | 2 | PASS |
| AE-803 | split-answer-with-a-model-that-follows-the-chunk-note | merge | 5 | chunked | 2 | PASS |
| AE-804 | later-chunk-answers-with-an-obedient-model | merge | 6 | chunked | 2 | PASS |
| AE-805 | answer-spans-three-chunks | merge | 8 | chunked | 3 | PASS |
| AE-806 | continuation-shares-a-page-with-other-answers | merge | 5 | chunked | 2 | PASS |
| AE-807 | answer-ends-exactly-at-the-chunk-boundary | merge | 5 | chunked | 2 | PASS |

## Diagnostics for every scenario that did not pass

None.

## Mutation testing

Counts: {'KILLED': 35}. Generated 2026-09-14T13:07:01Z.

Each mutant runs with --failfast, so the last column is the FIRST test Django ran that failed (database-backed suites run first). Other tests may catch the same mutant; this column is proof that at least one assertion did, not a list of every one that would.

| ID | Protection removed | Stands for | Outcome | First failing test |
|---|---|---|---|---|
| M01 | chunked path sends no schema | remove extraction schema; Regression 2 | KILLED | ai_processor.tests_answer_benchmark_grading.ExtractionThroughGradingTest.test_status_score_and_review_agree_end_to_end |
| M02 | single-call path sends no schema | remove extraction schema | KILLED | ai_processor.tests_answer_benchmark_grading.ExtractionThroughGradingTest.test_status_score_and_review_agree_end_to_end |
| M03 | BLANK removed from the status enum | remove BLANK enum | KILLED | ai_processor.tests_answer_benchmark_grading.ExtractionThroughGradingTest.test_status_score_and_review_agree_end_to_end |
| M04 | NOT_FOUND_IN_DOCUMENT removed from the status enum | remove NOT_FOUND enum | KILLED | ai_processor.tests_answer_benchmark_grading.ExtractionThroughGradingTest.test_status_score_and_review_agree_end_to_end |
| M05 | answer chunker ignores the configured chunk size | ignore pages_per_chunk | KILLED | ai_processor.tests_answer_benchmark_failures.CancellationTest.test_during_the_final_chunk |
| M06 | assignment chunker reverts to the hard-coded CHUNK_SIZE | ignore pages_per_chunk; Regression 3 | KILLED | ai_processor.tests_chunked_extraction_contract.ChunkedAssignmentNoteReportsTheRealPagesTest.test_a_final_short_chunk_is_described_honestly |
| M07 | claimed page range shifted +1 | shift page range by +1; Regression 4 | KILLED | ai_processor.tests_answer_benchmark_grading.ExtractionThroughGradingTest.test_status_score_and_review_agree_end_to_end |
| M08 | claimed page range shifted -1 | shift page range by -1; Regression 4 | KILLED | ai_processor.tests_answer_benchmark_grading.ExtractionThroughGradingTest.test_status_score_and_review_agree_end_to_end |
| M09 | final chunk dropped | drop final chunk | KILLED | ai_processor.tests_answer_benchmark_failures.CancellationTest.test_between_chunks |
| M10 | first chunk dropped | drop first chunk | KILLED | ai_processor.tests_answer_benchmark_failures.CancellationTest.test_between_chunks |
| M11 | last chunk sent twice | duplicate a chunk | KILLED | ai_processor.tests_answer_benchmark_grading.ExtractionThroughGradingTest.test_status_score_and_review_agree_end_to_end |
| M12 | splitter accepts a negative size again (drops every page) | invalid chunk size rejected | KILLED | ai_processor.tests_answer_benchmark_inputs.ChunkSizeContractTest.test_sizes_below_one_are_rejected |
| M13 | completeness placeholder says BLANK | convert NOT_FOUND -> BLANK; Regression 5 | KILLED | ai_processor.tests_answer_benchmark_failures.MalformedModelOutputTest.test_an_omitted_question_becomes_not_found_never_blank |
| M14 | grading pairing placeholder says BLANK | convert NOT_FOUND -> BLANK; Regression 5 | KILLED | ai_processor.tests_answer_extraction_gate.AnswerStatusReachesGraderTest.test_fabricated_placeholder_carries_not_found_not_blank |
| M15 | merge trusts a BLANK the model cannot place | convert NOT_FOUND -> BLANK (AE-800, AE-802) | KILLED | ai_processor.tests_answer_benchmark_grading.ExtractionThroughGradingTest.test_status_score_and_review_agree_end_to_end |
| M16 | merge ties no longer favour NOT_FOUND | order-independent merge (AE-802 reversed) | KILLED | ai_processor.tests_answer_chunk_merge.MergeChunkAnswerTest.test_an_unplaced_blank_never_beats_not_found_in_either_order |
| M17 | merge reverts to the empty-html rule | BLANK -> NOT_FOUND collapse (AE-600, AE-601) | KILLED | ai_processor.tests_answer_benchmark_grading.ExtractionThroughGradingTest.test_status_score_and_review_agree_end_to_end |
| M18 | answer split across chunks keeps only its first half | cross-chunk answer (AE-205) | KILLED | ai_processor.tests_answer_benchmark_grading.ExtractionThroughGradingTest.test_status_score_and_review_agree_end_to_end |
| M19 | later chunks told every question was already found | later-chunk answers skipped (AE-804) | KILLED | ai_processor.tests_answer_benchmark_scenarios.ScenarioBenchmarkTest.test_every_catalogued_scenario |
| M20 | shared PDFService singleton restored | remove per-upload service isolation; Regression 1 | KILLED | ai_processor.tests_pdf_service_concurrency.PdfServiceInstancesAreIndependentTest.test_prepare_ai_content_does_not_mutate_the_shared_singleton |
| M21 | connected-peer SSRF check disabled | disable connected-peer SSRF check | KILLED | ai_processor.tests_ssrf_guard.DnsRebindingIsCaughtAfterConnectingTest.test_a_connection_that_lands_on_localhost_is_refused |
| M22 | a chunk that never succeeds is skipped | swallow provider error; Regression 8 | KILLED | ai_processor.tests_answer_benchmark_failures.PersistentChunkFailureTest.test_no_partial_result_is_returned |
| M23 | cancellation retried by extract_answer_with_retry | retry cancellation; Regression 7 | KILLED | ai_processor.tests_answer_chunk_merge.ErrorsKeepTheirTypeTest.test_a_cancellation_is_never_retried |
| M24 | cancellation retried inside the chunk loop | retry cancellation; Regression 7 | KILLED | ai_processor.tests_answer_chunk_merge.ErrorsKeepTheirTypeTest.test_a_cancellation_is_never_retried |
| M25 | single-call path wraps credit/cancel errors again | error type preserved on both paths | KILLED | ai_processor.tests_answer_chunk_merge.ErrorsKeepTheirTypeTest.test_a_cancellation_is_never_retried |
| M26 | exhausted retries lose their cause | failure classification; Regression 8 | KILLED | ai_processor.tests_answer_benchmark_failures.PersistentChunkFailureTest.test_the_real_cause_reaches_the_user_facing_message |
| M27 | failed chunk loses its cause | failure classification; Regression 8 | KILLED | ai_processor.tests_answer_benchmark_failures.PersistentChunkFailureTest.test_the_real_cause_reaches_the_user_facing_message |
| M28 | empty submission billed again | missing document rejected | KILLED | ai_processor.tests_answer_benchmark_inputs.EmptySubmissionTest.test_no_content_is_refused_before_any_billed_call |
| M29 | completeness gate keeps every duplicate | remove duplicate protection | KILLED | ai_processor.tests_answer_benchmark_failures.MalformedModelOutputTest.test_a_duplicated_entry_appears_once |
| M30 | chunked merge result no longer sorted | remove final result ordering | KILLED | ai_processor.tests_answer_benchmark_failures.MalformedModelOutputTest.test_without_the_gate_only_the_chunked_path_sorts |
| M31 | completeness gate output in reverse order | remove final result ordering | KILLED | ai_processor.tests_answer_benchmark_failures.MalformedModelOutputTest.test_result_order_follows_the_assignment_not_the_provider |
| M32 | generated PDFs no longer byte-deterministic | determinism (section 25) | KILLED | ai_processor.tests_answer_benchmark_concurrency.DeterminismTest.test_every_document_builds_to_identical_bytes |
| M33 | chunk note stops asking for the continuation of a found answer | split answer silently truncated at a chunk boundary (AE-803, AE-805, AE-806) | KILLED | ai_processor.tests_answer_benchmark_scenarios.ScenarioBenchmarkTest.test_every_catalogued_scenario |
| M34 | chunked merge keys on the model's raw question label again | question-number style switch re-bills the script (live AE-912, AE-921) | KILLED | ai_processor.tests_answer_benchmark_failures.QuestionNumberStyleTest.test_an_integer_numbered_assignment_accepts_q_prefixed_answers |
| M35 | single-call answers no longer relabelled before the completeness gate | a real answer dropped as numbering drift on the final attempt | KILLED | ai_processor.tests_answer_benchmark_failures.QuestionNumberStyleTest.test_a_single_call_in_bare_numbers_keeps_every_answer |

## Real-provider results

Generated 2026-09-14T13:19:47Z.

| ID | Pages | Path | Chunks | Retries | Discrepancies | Seconds | Credits | Credit invariant | Model |
|---|---|---|---|---|---|---|---|---|---|
| AE-903 | 3 | chunked | 1 | 0 | 0 | 17.06 | 22747 | True | x-ai/grok-4.3 |
| AE-906 | 6 | chunked | 2 | 0 | 0 | 51.71 | 46063 | True | x-ai/grok-4.3 |
| AE-907 | 7 | chunked | 3 | 0 | 0 | 76.45 | 59681 | True | x-ai/grok-4.3 |
| AE-909 | 9 | chunked | 3 | 0 | 0 | 69.62 | 69947 | True | x-ai/grok-4.3 |
| AE-912 | 12 | chunked | 4 | 0 | 0 | 84.1 | 62630 | True | x-ai/grok-4.3 |
| AE-921 | 21 | chunked | 7 | 0 | 0 | 147.12 | 111412 | True | x-ai/grok-4.3 |
| AE-905 | 5 | chunked | 2 | 0 | 0 | 44.77 | 28038 | True | x-ai/grok-4.3 |
| AE-915 | 8 | chunked | 3 | 0 | 0 | 38.24 | 42911 | True | x-ai/grok-4.3 |
| AE-916 | 5 | chunked | 2 | 0 | 0 | 108.24 | 41704 | True | x-ai/grok-4.3 |

## Regression mapping

| ID | Defect | Scenarios | Tests | Mutations |
|---|---|---|---|---|
| R1 | Shared PDFService singleton: one student's pages could be extracted for another student's submission. | - | ai_processor.tests_pdf_service_concurrency<br>ai_processor.tests_answer_benchmark_concurrency.CrossStudentIsolationTest<br>ai_processor.tests_answer_benchmark_concurrency.ConcurrentChunkedExtractionTest | M20 |
| R2 | Chunked answer path sent no structured-output schema. | every chunked scenario (harness.check_schema_sent) | ai_processor.tests_chunked_extraction_contract | M01, M02 |
| R3 | Chunker ignored the configured pages_per_chunk. | AE-101, AE-102, AE-103, AE-104, AE-105 | ai_processor.tests_answer_benchmark_scenarios.ThresholdDerivedBoundaryTest<br>ai_processor.tests_chunked_extraction_contract | M05, M06 |
| R4 | Claimed page range derived from the caller, not the chunk. | AE-204, every chunked scenario (check_claimed_ranges) | ai_processor.tests_chunked_extraction_contract | M07, M08 |
| R5 | NOT_FOUND_IN_DOCUMENT collapsed into BLANK (a silent zero). | AE-003, AE-203, AE-300, AE-304, AE-305, AE-800, AE-802 | ai_processor.tests_answer_benchmark_failures.MalformedModelOutputTest<br>ai_processor.tests_answer_benchmark_grading<br>ai_processor.tests_answer_chunk_merge.MergeChunkAnswerTest | M13, M14, M15, M16 |
| R6 | Null answer_html after successful provider calls. | - | ai_processor.tests_answer_benchmark_failures.MalformedModelOutputTest.test_a_null_answer_is_never_scored_as_a_blank | - |
| R7 | Cancellation retried as if it were a transient failure. | - | ai_processor.tests_answer_benchmark_failures.CancellationTest<br>ai_processor.tests_answer_chunk_merge.ErrorsKeepTheirTypeTest | M23, M24, M25 |
| R8 | Extraction failure swallowed or reported without its cause. | - | ai_processor.tests_answer_benchmark_failures.PersistentChunkFailureTest<br>ai_processor.tests_answer_chunk_merge.ErrorsKeepTheirTypeTest | M22, M26, M27 |
| N1 | A BLANK on a page after the first chunk came out NOT_FOUND_IN_DOCUMENT (found by this benchmark). | AE-600, AE-601, AE-801 | ai_processor.tests_answer_chunk_merge.MergeChunkAnswerTest | M17 |
| N2 | An answer split across chunks kept only its first half (found by this benchmark). | AE-205, AE-206 | ai_processor.tests_answer_chunk_merge.MergeChunkAnswerTest<br>ai_processor.tests_answer_benchmark_grading | M18 |
| N3 | Later chunks were told every question was already extracted (found by this benchmark). | AE-804 | ai_processor.tests_answer_chunk_merge.AlreadyFoundNoteTest | M19 |
| N4 | Single-call path hid credit, access and cancellation error types (found by this benchmark). | - | ai_processor.tests_answer_chunk_merge.ErrorsKeepTheirTypeTest | M25 |
| N5 | An empty submission was still billed and returned as a success (found by this benchmark). | - | ai_processor.tests_answer_benchmark_inputs.EmptySubmissionTest | M28 |
| N6 | A negative chunk size silently dropped every page (found by this benchmark). | - | ai_processor.tests_answer_benchmark_inputs.ChunkSizeContractTest | M12 |
| N7 | Benchmark PDFs were not byte-deterministic (a harness defect, found by the determinism suite). | - | ai_processor.tests_answer_benchmark_concurrency.DeterminismTest | M32 |
| N8 | The chunk note told later chunks an already-found question 'does NOT need to be extracted again', and the real model dropped the continuation of a split answer (confirmed live on AE-905). | AE-803, AE-805, AE-806, AE-807, live AE-905, live AE-915, live AE-916 | ai_processor.tests_answer_chunk_merge.AlreadyFoundNoteTest<br>ai_processor.tests_answer_benchmark_live | M33 |
| N9 | The chunk merge keyed answers on the model's raw question label, and the model switches between `1` and `Q1` from chunk to chunk: one question became two, the completeness gate re-read and re-billed the whole script (live AE-912: 12 calls, 189,526 tokens), and on a single call the final attempt dropped real answers as numbering drift. | live AE-912, live AE-921, live AE-905 | ai_processor.tests_answer_chunk_merge.QuestionLabelTest<br>ai_processor.tests_answer_benchmark_failures.QuestionNumberStyleTest | M34, M35 |
