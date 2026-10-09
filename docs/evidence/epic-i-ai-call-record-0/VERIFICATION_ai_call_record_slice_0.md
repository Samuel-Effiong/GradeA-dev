# Verification record: AI-call record, slice 0 (task/epic-i-ai-call-record-0)

Written for: the Senior Manager and the Release Engineer (a record to be committed byte-identical).
Checker: Next-stage Checker (0c). Tip verified: **2b54b7e1**. Run: 8 Oct 2026, 18:01 to 18:05 WAT, granted by the Release Engineer, one outer systemd-inhibit, load 1.2 at start, nothing else running.

## Verdict: VERIFIED-WITH-NOTES

Slice 0 does what its brief says, and I found nothing that must change before it is merged. Three notes, none blocking; the first two carry into later slices.

## What was run (all against a clean scratch checkout at 2b54b7e1)

| Step | Result |
|---|---|
| Changed files against 75d82620 | exactly the 8 expected (7 new, 1 modified: grading_run.py, 31 diff lines) |
| ai_processor/services.py diff | 0 bytes (the provider seam is untouched) |
| makemigrations --check | no changes |
| My probe alone (16 tests, written before the run) | Ran 16, OK |
| Baseline: Builder's 4 new test modules + slice C's two label modules + grading-run modules + my probe | Ran 187, OK |
| My 22 mutants, own test database | **22 KILLED, 0 SURVIVED, 0 BROKEN**; each had a "Ran 187" line, no load failure, and every test I named beforehand is among the failures (no named test missing) |
| After the mutants | source clean, my probe file removed, mutant database dropped |
| Credential-pattern check of the tip | 5,032 files, archives opened, values masked; none of the 8 changed files appears. The hits are older files (.example.env, workflows, scripts, docs) |

Logs: GAP-0c-runs/ai-call-record-0/logs (0_ to 5_ files, mutants/results.json holds expected and failing tests per mutant).

## The 22 mutants and what killed them

Vote helper (XV1 to XV4): main model wins without being a leader; leaders not sorted; one vote not counted; main returned when no named leader exists. All killed (11, 10, 32, 13 failing tests).
Step run (XS1 to XS14): a new attempt keeps old facts (3); a non-text provider name kept; the fixed words "unknown" and "unlabelled" missing; a reply with no items bringing no prompt version; scope check inverted or returning None; error text without the task type; **the scope as a plain global instead of a context variable (XS11, 13 failing)**; tie ignores the main model; a received reply not marked; a refused mark (reply before the call left) still leaving "received" set. All killed.
Grading run (XG1): "deterministic" read when no winner. Killed (6).
Shared words (XL1 to XL3): a word differs, a fifth re-check word, "not_run" missing from the fixed words. Killed (4, 1, 3).

**XS14 is caught only by my probe** (test_a_refused_mark_changes_nothing, the one failing test): the Builder's own 33 mutants and tests do not catch a refused mark that still leaves "received" set. This is a gap in the Builder's suite, not a fault in the code: the code at 2b54b7e1 is correct, and my probe covers it. Recommend the Builder adopt that test.

## The Senior Manager's five points

1. **Silence in the staged form is accepted.** ENFORCED_STEP_TASK_TYPES is empty, so no provider call can raise yet; I showed this by the probe test that every call-site task type is silent with no scope open, and by services.py being unchanged (0 bytes). It is the staged form I accepted, with my L1 to L4 demands carried forward (see notes).
2. **Scope behaviour:** a new thread starts with no scope (a thread-pool worker does not see it); a copied context does see it; two tasks in one thread and one context do not leak after an error; the same run entered twice, nested, restores each level. All proven green by the probe, and the context-variable choice is held by XS11 (global instead of context variable: killed).
3. **Vote equivalence:** my probe compares majority_model with my own reference for every small case and for random orders (the dict order never changes the answer), and the label words for every small pattern equal the base's. The two slice C label modules pass unchanged in the baseline.
4. **Provider seam: 0 bytes changed** in ai_processor/services.py.
5. **Mutants right reason:** every kill is by the test(s) I named for that mutant, not by an unrelated failure.

## Notes (none blocks the merge)

1. **Docstring overclaim.** A docstring says a static test covers the callers of the step run. At this tip no production module uses the step run (my probe test_no_production_module_uses_the_step_run_yet passes), so nothing has callers yet and the sentence is not true yet. It becomes a promise for S2 and S3: the static test that lists every enforced call site inside a scope is my demand **L2**. Fix the wording or keep it as a stated future test; do not leave it as a claim.
2. **Carried demands for slices 1 to 3 (L1 to L4):** L1 enforcement for extract_answer (S2) and formatted_grade (S3) shown red at the provider-call seam; L2 a static test that every enforced call site is inside a scope; L3 S1 pins ENFORCED_STEP_TASK_TYPES equal to STEP_TASK_TYPES; L4 a static list of every task type so a new one forces a decision.
3. **Scope reaches only the thread and context it was opened in.** Celery prefork reuses one context across tasks in a process, so a scope left open by a task that dies in the wrong way would show in the next task. The probe shows no leak after an error; S2 and S3 must open the scope with `with run.scope()` only (never by hand) and tests must show the scope closed after an exception raised inside a task body.
4. The Builder's own 33 mutants have gaps I could not find by reading alone (XS14 is one); the 22 mutants here and the probe fill them. I list the probe's name for adoption: tests_0c_probe_ai_call_record_0.py, sha256 4edbdc6eda7a42767120df950f2922a61bec5d055dec9d436e7b3800490a1c1b.

## Files (sha256 as run)

- run_0c_verify_ai0.sh ffcb9d3a6594f809...
- mutate_0c_ai0.py fce1c8f195d6cd2c...
- tests_0c_probe_ai_call_record_0.py 4edbdc6eda7a4276...

I did not run the full application suite (rule 15: the author's regression is not repeated). Rule 21: I am done with GAP-0c-scratch/ai-call-record-0 once this record is committed and the slice is merged.
