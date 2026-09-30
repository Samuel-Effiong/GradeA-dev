# Verification: Epic A S7a @ a9dd8bd

**Verifier:** Verification Engineer 2 (v2). **Author:** 1a. **Date:** 2026-09-30.
**Branch:** task/epic-a-s7a @ **a9dd8bd** (code a52d133; epic 522f818 merged as 4c8ba13; evidence 8a4cbdf + a docs-only "Frontend contract changes" commit). Design: 08a §4.3, §4.6 (30/12), §5 S7a; the two batch 413 bugs deferred from S6b. SM-approved decisions: an all-refused batch is 202 with every item failed; `item_index` is 1-based; `reference` is the hex X-Request-ID.

The run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`) in 0b's slot, beside Gate 10, from a scratch worktree detached at a9dd8bd. Rule 15: v2's probes + `students.tests_batch_item_results` + `AutoGrader.tests_reason_codes` + mutants. 1a's gates are cited: 179 OK outside the regression apps; students + users + assignments 1635 OK; 11/11 mutants; reproduce-first 10/10 on 4124d73.

**Verdict: VERIFIED-WITH-NOTES** (N1 is small, N2 procedural).

## Static
- **Migration** `students/0029_background_task_item_result`: in `students` (not billing). `reason_code` (`db_default=""`) and `retry_count` (`db_default=0`) are the NOT NULL columns, both with a DB default (brief rule 11 / the H-56 guard). `item_index` and `trace_id` are nullable.
- `item_result()` keeps the five old entry keys with the same meaning and adds the FR-A-07 fields. `reference` = `task.trace_id.hex`. `UNCLASSIFIED` is only a `failure_codes` key (a failure with no code reads as error_class SYSTEM), never stored as a `reason_code`.
- batch-upload: every file is an item. A too-large file becomes `record_refused_item` (FAILURE, FILE_TOO_LARGE, `task_id` None) and the others dispatch. There is no whole-batch 413 and no half-queue.

## Evidence (1a's `BatchFixture`: real batch-upload, real tasks inline, only the extraction faked; v2's own assertions)
| Check | Result |
|---|---|
| v2 probes (`tests_vf2_s7a_probe.py`) + `students.tests_batch_item_results` + `AutoGrader.tests_reason_codes` | **45 OK** (including 1a's `ThirtyFilesTwelveFailures`) |
| P1 backward compatibility (pinned from 522f818's `session_results`) | all **15** pre-S7a top-level keys present; every entry keeps the **5** old keys; `failure_count == len(failure_list)` |
| P2 reference | each item's `reference` = the batch-upload response's `X-Request-ID` (hex `4209d176…`), and `uuid(reference)` is present as an `AuditEvent.trace_id` |
| P3 all-refused batch (every file over the cap) | **202**; 2 FAILURE items, each FILE_TOO_LARGE with `task_id` None; **0 tasks launched** |
| P4 item_index | [1, 2, 3] in upload order (1-based) |
| P5 tenancy | another teacher reading the session's results → **404** |
| v2 mutants (`vf_s7a_mutants.py`) | **3/3 KILLED**, by 1a's tests as well as v2's: 0-based `item_index`; dashed (non-hex) `reference`; the whole-batch 413 restored |

## Notes
- **N1 (ordering).** `failure_list` is not in upload order: the all-refused batch returned items `[2, 1]`. Now that every entry carries `item_index`, sort each list by `item_index` (or state the order in the contract), so a client matching entries to the files it sent doesn't depend on DB row order.
- **N2 (procedural, SM).** S7a is to be base-updated onto d5's CodedError serialization fix once that merges into epic. That merge gets v2's usual check: remerge-diff, the added-lines survival, and the S7a + CodedError probes on the merged tree.

Logs: `runs/s7a_run1.log`, `runs/s7a_run2_mutants.log`.
