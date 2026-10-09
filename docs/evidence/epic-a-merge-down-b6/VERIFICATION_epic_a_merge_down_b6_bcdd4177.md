# Verification: bundle 6 → Epic A merge-down, with H-85 landing as TEACHER_CANNOT_JOIN_YET (ed)

- **Branch:** task/epic-a-merge-down-b6 at **bcdd4177**. Merge 748d0323 (0b): beta 141c8031 (bundle 6: H-94, H-88/H-93/H-81, H-85, H-99) into phase2/epic-a 7b4a6eaf. The code tip is 54cc7c1b; 54cc7c1b..bcdd4177 is evidence only (the diff outside docs/evidence is empty).
- **Commits after the merge:** afdc54fc (H-99's epic form, test-only), 17e36892 (H-85 tests first), e8b9242c (production), d4b5f8e0 (catalogue and backend documents).
- **Decision built:** the founder's option B (2026-10-05, via the SM).
- **Verifier:** v2 (independent), 2026-10-05.
- **Verdict:** **VERIFIED-WITH-NOTES**

## The merge commit, as read
| Check | Result |
|---|---|
| `git show --remerge-diff 748d0323` | Empty: nothing in the merge is on neither parent |
| Added-line survival (v2's script) | 0 lines lost from either side since the merge base 74bfc8d3 |
| The seven files kept on the epic side | Each identical to 7b4a6eaf: assignments/tasks.py, students/tests_h38_tasks_namespace.py, classrooms/tests_h71_student_add_role.py, classrooms/tests_security_penetration.py, AutoGrader/tests_beat_locks.py, audit/tests_retention_sweep.py, audit/tasks.py |
| phase2/epic-a since the merge | Still 7b4a6eaf, an ancestor of bcdd4177 |
| Migration | billing 0073 only; `makemigrations --check` in ed's gate: no changes |

## H-99 on the epic (afdc54fc, 17e36892), as read
- A refused caller-supplied placeholder address takes H-71's refusal on every route. A bulk row carries `ROW_STAFF_EMAIL` and the row-form sentence. No production change was needed.
- Three tests compare the placeholder answer with a staff address's answer per route (status and body, apart from the support reference and the echoed address).
- v2's pre-read note is closed: the assertion that could not fail now names each route's exact refusal status (400, 400, 200).
- **New recorded divergence from beta:** classrooms/tests_h99_placeholder_email.py (the bulk-row assertion and the three epic-only tests). Later merge-downs keep the epic's version.

## H-85 on the epic: the defect and the fix, as read
- **Before (the merge alone, red at e917f52a):** beta's H-85 made the exception's text neutral, but the epic's `teacher_failure()` chose S7d's entry by the exception's class. The add-teachers answer for a teacher with their own subscription still said so four ways: the code's name, the message, the remediation and `params.email`.
- **After:** `teacher_failure()` returns `TEACHER_CANNOT_JOIN_YET`: H-85's sentence, the remediation "Ask the teacher to contact support.", no params, not retryable. `TEACHER_HAS_INDIVIDUAL_SUBSCRIPTION` is gone from audit/enums.py, from the catalogue and from the three-code set that may carry an address.

| Check | Result |
|---|---|
| Production diff since the merge | Three files, three hunks: billing/license_service.py (`teacher_failure()`), AutoGrader/reason_codes.py, audit/enums.py |
| Both raise sites | Untouched and identical to beta: they raise beta's neutral constant, each beside an ids-only log line |
| The other catchers (the invite-and-enrol helper, the renewal carry-forward) | Untouched beta code: they answer `str(exc)`, the neutral sentence, and log the class only |
| AutoGrader/error_messages.py | Untouched: the exception passes its own text through, and that text is the neutral sentence |
| The audit trail | The audit model's reason-code column has no choices, so no migration. No migration file names the old code |
| The sync-only guard and beta's constant of the old name | The guard matches names against the codes in its set; with the code out of the set the two raises match nothing. Nothing was renamed |
| A param cannot come back by the call site alone | `CodedError` refuses a param the catalogue entry does not declare (see mutant Y1) |
| Documents | The QA catalogue carries a dated amendment under the approval record; the 08a design says three codes; docs/backend/billing-licenses.md has the client note |
| Rule 14 | The new tests use ed's recording stand-in for mail; no bare mock |

## ed's gates (read, not repeated: rule 15)
| Gate | Tip | Result |
|---|---|---|
| Run 1: changed modules and guards | e917f52a | **RED**, 922 run, 2 failures: H-85's response test and the sync-only guard. This is the reproduce-first evidence for H-85 on the epic |
| Step 0: H-99's module as merged, on the epic's code | 54cc7c1b | 16 run, 4 failures, as expected |
| Step 1: changed and caller modules, both sides' guards | 54cc7c1b | **956 OK** |
| Mutants: P1, P3–P10, E1–E4, H1–H6 | 54cc7c1b | **19 of 19 killed** |
| ONE regression: billing, classrooms, users, AutoGrader, dashboard, audit | d22dff5a | **4441 OK** (skipped=8) |

- **Rule 17:** mutate.py sets `PYTHONDONTWRITEBYTECODE=1` and deletes the mutated modules' `__pycache__` before each mutant and after each restore; the record states both.
- **Not recomputed by v2:** the committed step 1 and regression logs are the last 200 lines; the full logs' hashes quoted in the record were not recomputed. The tails show the run lines and no FAIL or ERROR header.
- **Disclosed by ed:** a start on 2026-10-05 that ed's own script stopped (its restore check counted a tracked log as a change); the red run 1.

## v2 runs (0b's grants, rules 16, 13 and 12, scratch worktree at bcdd4177, DB test_vf2_s1, `--settings=settings_worktree`)
**Rule 17:** every test subprocess ran with `PYTHONDONTWRITEBYTECODE=1`. `billing/__pycache__` was deleted before each baseline, before each mutant run and after each restore. Each restore was sha-checked against bcdd4177. Both attempts ran beside one other GAP run (d5's mutation battery, its own database); wall times are not a baseline.

| Step | Result | File (GAP-v2-handover/runs/) |
|---|---|---|
| Baseline, first attempt | **105 run, 2 failures, both in v2's probe** (a defect in the probe, below). ed's five modules: no failure. No mutant run; slot released | md6_bcdd4177_baseline_run1_probe_defect.log |
| Baseline, second attempt (0b's second grant) | **105 tests OK**: the five modules the H-85 commits touched (101) and the probe (4) | md6_bcdd4177_baseline.log |
| Mutant Y1 against ed's two billing modules | killed (3 tests) | md6_bcdd4177_mutants.log |
| Mutant Y1 against v2's probe | killed (3 tests) | md6_bcdd4177_mutants.log |

### The probe defect, disclosed
The first probe asserted that the bare word "subscription" is absent from the school admin's audit list and from every stored event. That expectation was wrong. The word is there for reasons that say nothing about the teacher: the route name `license-subscription-add-teachers` in the request event's metadata, and the action `SUBSCRIPTION_CHANGE` and target type `LicenseSubscription` of the licence's own events. The retired code's name was absent in that run too (its assertion passed before the word check failed).

| Test | First attempt | Second attempt |
|---|---|---|
| A1, the audit list | absent: the retired name, "subscription", "individual" | absent: the retired name, "individual", "own subscription"; no event's reason code contains SUBSCRIPTION |
| A2, stored events (reason code and metadata) | absent: the retired name, "subscription" | absent: the retired name, "individual"; no reason code contains SUBSCRIPTION |

**What the corrected probe no longer asserts:** that no audit event mentions a subscription in any wording. A sentence about the teacher's plan that used neither "individual" nor "own subscription" would pass it. v2 accepts the loss because the first attempt's failure output shows the school's whole list for this request: five events, all with outcome success and no reason code. The add request's own event is one STATE_CHANGE whose metadata holds only the route, the method and the status; the other four (SUBSCRIPTION_CHANGE, CREDIT_TRANSACTION, PERMISSION_CHANGE) come from the fixture's setup of the licence. No event is written for the refused teacher.

### The probe (tests_vf2_md6_probe.py), 4 OK
- **A1:** after the refusal the admin's answer has the new code, empty params, and none of the telling words; the admin's own audit list is as in the table above.
- **A2:** no stored event of any school names the reason.
- **A3:** the billing log lines never carry the address or the retired name; one line gives the reason by the teacher's id; one line gives the code `TEACHER_CANNOT_JOIN_YET`.
- **A4:** in one request beside a teacher refused for another reason, the paying teacher's entry has the same keys and error class, empty params, and no address in any text.

### Mutant Y1 (vf_md6_mutants.py): the mapping passes the address again
Killed by both, but **not by an assertion on the address** (0b's question). With the call site alone changed, `CodedError` raises because the entry declares no `email` param, so the address never reaches the answer; the tests fail on what the request then returns. v2's log keeps the failing tests' names, not their assertion text. For the address to come back, the call site and the catalogue entry must both change. That double change was not run. By reading, it meets `params == {}` in ed's S7d test and in v2's A1 and A4, the catalogue pin, and the guard that pins the address-carrying set to exactly three codes. ed's H5 covers the entry alone.

## Notes (none blocks)
1. **Neutral in wording, still distinct.** The new code and sentence are used for this one case only, so an admin who knows the catalogue can still tell it from the other refusals. Beta's H-85 answer has the same property; this is the founder's decision, stated so that nobody claims more.
2. **One sentence in two places.** The catalogue holds the sentence as its own literal and beta's constant keeps its old name (SM: no rename). `tests_reason_codes` pins the two equal.
3. **The word tripwire is not a proof.** ed's `test_no_code_says_a_teacher_has_a_subscription` checks TEACHER_ codes for four words; a new telling code under other words would pass it.
4. **No stored row holds the old code: by reading only.** Staging was not queried. An old row, if any, would still read (the column has no choices).
5. **Two stale comments** in billing/license_service.py still say the subscription refusal carries the address. Left on purpose (SM): fixed on beta as H-108, the epic copy follows with the bundle 7 merge-down.
6. **The frontend** must map the new code; any client handling of the old code's `email` param goes. Outside this repository.
7. **The disclosing answer has been on staging's Epic A build since S7d.** It ends when this merge reaches staging.
8. **Apps outside ed's six** (assignments, students, ai_processor and the rest) were not run for this merge-down; 0b's Gate 10 after the merge covers them.
