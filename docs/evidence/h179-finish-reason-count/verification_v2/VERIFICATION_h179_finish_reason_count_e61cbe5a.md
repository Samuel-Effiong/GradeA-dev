# Verification by Verifier 2 (v2): H-179, a reply cut off at the length limit is logged once per metered call

Tip verified: `e61cbe5a` (H-179 on beta `8567a7a0`; code `51c759ed`, in `ai_processor/services.py`, 31 lines). Author: d5.
Run: Release Engineer's slot 2026-10-09 12:54:44 to 12:55:19 (load 2.54 at start). Probe `99ab2623e662dea9`, runner `e85a0dfe93ef23f2`, script
`4390c7d12d518c41`, written before the run. The author's chain (942 OK, 7 of 7 mutants) and `c_h179` (Ran 6142 OK) were NOT repeated (rule 15).

## Word: VERIFIED-WITH-NOTES

## What I checked
1. **Reading.** One helper, `note_if_reply_cut_off(response, task_type)`, and one call in `AIProcessor.execute_graded_task` after the provider reply. It logs one WARNING
   (`"AI reply cut off by the length limit: task_type=%s model=%s finish_reason=%s"`) when the first choice's `finish_reason` is the string `"length"`; it cannot raise (attribute-safe;
   a missing or non-string reason, or no choices, is "not cut off"; a non-string model is logged as None). Every provider call goes through `__ai_model`, which `execute_graded_task` calls at two
   places: the metered path (logs) and the unmetered SUPER_ADMIN branch (returns earlier and logs nothing: deliberate, tested by the author; internal tooling is not counted).
2. **Rule 22.** The author's failure fragments (`expected_kills.py`, written 9 Oct 10:00, before the red run of 11:31) are looked for inside each test's own block; I checked the raw red log: the
   three red tests fail with "0 != 1" at the count assertion and "not enough values to unpack" at the two unpacking lines, as written.
3. **The row changes no grading** (a log line; no change of flow), so the real-pipeline rule is met by the form of my probe: a REAL CALLER with its retry loop
   (`extract_answer_with_retry` -> `extract_answer_image` -> `execute_graded_task`), only the provider call replaced by a plain stand-in (no MagicMock reaches the code).
   v1: a reply cut off mid-JSON every time: the caller fails as it always did (decode error on `__cause__`), the provider is called 3 times, the log holds exactly 3 cut-off lines, each WARNING with args
   `("extract_answer", <the reply's model>, "length")`, and none carries the prompt, the assignment text or the reply text. v2: a normal reply ("stop"): returned, one provider call, no line. v3:
   `content_filter`: returned, no line. **Baseline Ran 3, OK.**
4. **Seven faults, each judged by my probe alone, failing set EQUAL to the written one, restores 7 of 7:** K1 the call removed {v1}; K2 content_filter counts too {v3}; K3 every reason counts {v2, v3};
   K4 the line carries the reply text {v1}; K5 INFO instead of WARNING {v1}; K6 the main model instead of the reply's {v1}; K7 logged twice {v1}.

## Not shown
- That production logs keep WARNING lines and let anyone count them: the row's brief named a metrics module that exists only on the next-stage line, so this is a log line, as the author states.
- The unmetered super-admin path is not counted by design.
- A cut-off reply is still charged and returned as before; the retry loops are unchanged (the author leaves "should a cut-off stop the retries" to after the counts).

## Files
`probe`, `runner`, `script`, driver and mutant logs (gzipped) in `logs/`.
