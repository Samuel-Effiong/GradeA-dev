# H-85: a neutral refusal for a teacher with an individual subscription

**Author:** d5. **Branch:** `task/h85-neutral-subscription-refusal`, off beta
`74bfc8d3`. Bundle 6.

**The decision.** The founder decided H-85 on 2026-10-02 (passed on by the
SM the same day): the school admin gets a neutral message. The exact text
the SM gave, used unchanged:

> This teacher can't be added to your school yet. Please ask them to contact support.

## The change (4 points)
1. **Before:** when a school admin added a teacher who has an active
   individual subscription, the refusal read "Teacher <address> has an
   active individual subscription. Individual subscriptions cannot be
   converted to a license. Please cancel the individual subscription
   first." Any school admin could type an address and learn that its owner
   pays for an individual plan (found in the H-78 verification).
2. **After:** both refusals (`_get_or_invite_teacher` and
   `_enroll_teacher_internal`) raise `IndividualSubscriptionConflictError`
   with one constant, `TEACHER_HAS_INDIVIDUAL_SUBSCRIPTION`: the sentence
   above, with no address and no mention of a subscription.
   - The exception class is unchanged: callers and
     `AutoGrader/error_messages.py` key on it.
   - The ids-only log line beside each raise (H-86) still tells support
     why.
   - One constant, because Epic A's `TEACHER_HAS_INDIVIDUAL_SUBSCRIPTION`
     catalogue entry takes the same text at the merge-down.
3. **Reach:** what a school admin sees from add-teachers and from licence
   creation with `teacher_emails`, for a teacher of their own school or one
   with no school. A teacher of another school already gets the
   other-school refusal first (H-78). The two API doc strings in
   `billing/stripe_view_schemas.py` that described the old behaviour are
   reworded.
4. **Tests:** `billing/tests/test_neutral_subscription_refusal.py` (both
   raise sites, the add-teachers response, the enrolment result, the log
   reason by id, the constant's text). Three existing tests asserted the
   old text and now expect the constant or the class:
   `test_other_school_before_subscription`, `test_track_separation`,
   `test_license_service`. A repo-wide grep finds the old text only in
   comments and docstrings that describe the history.

**Also in this branch (SM ruling, found in the merge-down):** the
teacher-removal log line was two adjacent literals with no separator
("...license 12Expired 3 credit buckets."). It is now one literal, the
same as Epic A's: "Removed teacher %s from license %s. Expired %d credit
buckets." Test: `test_the_removal_line_separates_its_two_sentences`.

| Commit | What |
|---|---|
| `85c71722` | the tests (red) |
| `7a68271a` | the fix, the doc strings, the three re-coded tests, the removal line |
| `273c2b30` | the removal-line test; `run_mutants.py` |

## Gates
On the frozen tip `273c2b30`, under 0b's grants.

| Gate | Result | Log |
|---|---|---|
| Repro: the test commit `85c71722` itself (h78-repro worktree, own DB) | 6 tests, 5 failures | `repro_85c71722.log` |
| (a) 7 modules | 95 OK | `a_modules_273c2b30.log` |
| (b) battery (`test_h85_mut`) | baseline green, N1–N7 7/7 killed, every restore sha-verified | `b_mutation_battery_273c2b30.log`, `logs/`, `results.tsv` |
| (c) billing + the 9 guards | 2128 OK (skipped=2), wall 533 s | `c_app_billing_guards_273c2b30.log.gz` |

The repro's one passing test is the log-reason test: the ids-only reason
line predates this change (H-86).

(a)'s modules: `test_neutral_subscription_refusal`,
`test_other_school_before_subscription`, `test_track_separation`,
`test_license_service`, `test_add_teachers_other_school_not_disclosed`,
`test_logs_carry_no_email`, `test_h38_teacher_removal`.

**The pause.** 0b asked me to pause between steps so that Epic A's
merge-down gates could take the slot. I stopped the chain script's shell
while (b) was running, so that (c) could not start, and let (b) finish.
(b)'s log therefore lacks the script's exit line; I added it by hand and
the log says so. (c) ran later under a new grant, on the same frozen tip
(`chain.status` has the times; `chain.sh` ran the repro, (a) and (b), `c_only.sh` ran (c)).

**How the runs were made.** Every run used `--settings=settings_worktree`
and an empty `EXEMPT_EMAIL_DOMAINS`, wrapped as `systemd-inhibit
--what=idle:sleep:handle-lid-switch … --mode=block systemd-run --user
--scope -p MemoryMax=6G -p MemorySwapMax=0 nice -n 10 timeout -k 60 1800`
(rules 12, 13, 16). (c) ran with `--parallel 2 --verbosity 2` through a
timestamper, under `flock ~/.machine-fullsuite.lock`.

**Rule 17.** The battery and its baseline ran with
`PYTHONDONTWRITEBYTECODE=1` (set for the runner and passed to every test
subprocess), and the runner deleted `billing/__pycache__` in its worktree
before the baseline, before each mutant and after each restore.

## After 1a's pre-review (delta at `29bebdcb`)
1a's static pre-review at `e3952a12` found three things; the SM ruled all
three into the branch.

| Commit | What |
|---|---|
| `15e5e62d` | **P1:** the add-teachers "Partial success" example in `billing/stripe_view_schemas.py` still showed "Individual subscription conflict or invalid email domain."; it now shows the neutral sentence. **1a's probe Z1c** adopted as `test_the_enrolment_site_logs_its_own_reason`: a teacher who subscribes between the invite check and the enrolment is refused by the enrolment's own check, with the neutral sentence and its own ids-only reason line. Mutant N8. |
| `29bebdcb` | **P2:** `docs/backend/billing-licenses.md` and `docs/backend/BACKEND_REFERENCE.md` described the old message and recovery. Docs only. The HTML renders are left to H-92. |

**Why Z1c.** My log test in the H-85 module reached only the invite site,
which refuses first. 1a's mutant Y7 (the enrolment site's reason line
dropped) survived that module's 8 tests at `e3952a12`; only their probe
failed. N8 is that mutant.

To be exact about what was unguarded: the enrolment site's line was already
covered by an H-78 test in another module,
`test_a_teacher_who_subscribes_before_enrolment_is_not_logged_by_address`
(`test_other_school_before_subscription.py`), which the first gate's
battery did run. The delta gate shows it: N8 is killed by two tests, that
one and Z1c. So the gap was in the H-85 module's own tests, not in the
suite.

**The delta's scope.** The SM's ruling, passed on by 1a: "P1: FOLD. Change
the 'Partial success' example ... After that commit only the touched
modules re-run (it is a schema example string)." So no regression re-run:
the production change is one example string in a schema file.

| Delta gate (at `29bebdcb`, under 0b's grant) | Result | Log |
|---|---|---|
| The touched modules and the two schema-extension guards | 29 OK | `delta_modules_29bebdcb.log` |
| Mutants N6 and N8 (`test_h85_mut`, rule 17) | baseline green, 2/2 killed, restores sha-verified | `delta_mutants_29bebdcb.log`, `logs/N6.log`, `logs/N8.log` |

The founder saw 1a's table of what an admin can tell apart and decided to
keep H-85 as built (SM, 2026-10-02).

## Mutants
| Id | Guards | Result |
|---|---|---|
| N1 | the invite refusal names no address and no subscription | killed |
| N2 | the enrolment refusal names no address and no subscription | killed |
| N3 | the sentence does not mention a subscription | killed |
| N4 | the invite refusal keeps its exception class | killed |
| N5 | the enrolment refusal keeps its exception class | killed |
| N6 | the invite refusal still logs its reason by id | killed |
| N7 | the removal line separates its two sentences | killed |
| N8 | the enrolment refusal still logs its own reason by id (1a's Y7; added at `15e5e62d`) | killed |

## For the verifier
- The sentence is the founder's, word for word; the test pins it and the
  constant separately.
- H-80's source guard passes on the tip (it is in (a)).
- This branch and H-88's both edit `billing/license_service.py`, in
  different hunks; 0b runs the overlap check at the merge.
- The logs' addresses are test fixtures only.
