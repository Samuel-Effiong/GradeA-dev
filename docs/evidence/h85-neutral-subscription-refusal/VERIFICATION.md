# Verification: H-85, a neutral refusal for a teacher with an individual subscription @ ef67cee3

**Verifier:** 1a. **Author:** d5. **Date:** 2026-10-02.
**Branch:** `task/h85-neutral-subscription-refusal` @ **ef67cee3** (code tip `29bebdcb`), off beta `74bfc8d3`. Bundle 6.
- `85c71722`: the tests
- `7a68271a`: the fix, the two doc strings, three re-coded tests, the removal log line
- `273c2b30`: the removal-line test; `run_mutants.py`
- `e3952a12`: the evidence
- `15e5e62d`: my P1 (the schema example) and my probe Z1c adopted as a test; mutant N8
- `29bebdcb`: my P2 (two Markdown docs; docs only)
- `0ceee907`, `ef67cee3`: the delta's evidence and one wording fix in it (docs only)

The evidence is in `docs/evidence/h85-neutral-subscription-refusal/`.

**The decision.** H-85 came from my H-78 note N2. The founder decided it on 2026-10-02: a school admin who adds a teacher with an active individual subscription is told "This teacher can't be added to your school yet. Please ask them to contact support." There is no address and no mention of a subscription.

I ran twice in 0b's slots from my detached scratch checkout, with its own test DBs (`test_vf_h85`, mutant `test_vf_h85_mut`): at `e3952a12`, and again at the final tip `ef67cee3`. The wrapper was rule 16's `systemd-inhibit` (idle, sleep and lid switch), the 6G scope with `MemorySwapMax=0`, `nice -n 10` and `timeout -k 60 1800`. Under rule 15 I cite d5's regression (billing + 9 guards, 2128 OK at 273c2b30) and don't repeat it.

**Verdict: VERIFIED.**
- The founder's sentence, word for word, is what reaches the admin on every path I could find, and the log still gives support the reason by id.
- Both findings of my pre-review (P1, P2) were ruled into the branch by the SM and are closed. My probe is adopted as a test.
- One observation (O1) stays open by the founder's decision: see the table below.

## Static review
**The fix** (`billing/license_service.py`): one constant, `TEACHER_HAS_INDIVIDUAL_SUBSCRIPTION`, raised at both sites (`_get_or_invite_teacher` and `_enroll_teacher_internal`) with the same exception class as before, `IndividualSubscriptionConflictError`. Callers and `AutoGrader/error_messages.py` key on the class, so nothing else changes. The ids-only reason line beside each raise (H-86) stays.

**Where the old wording survived** (my grep of the whole tree at `e3952a12`):
- **P1:** the add-teachers "Partial success" example in `billing/stripe_view_schemas.py` still showed "Individual subscription conflict or invalid email domain." Documentation, not a runtime response. Closed by `15e5e62d`: it shows the neutral sentence.
- **P2:** `docs/backend/billing-licenses.md` and `docs/backend/BACKEND_REFERENCE.md` still described the old message and "cancel the individual plan first". Closed by `29bebdcb`. The HTML renders are left to H-92, as the SM ruled.
- At `ef67cee3` the old wording remains only in comments, in docstrings that describe the history, and in older evidence.

**The delta since my first run:** production code changed by one schema example string (`billing/stripe_view_schemas.py`). `billing/license_service.py` is identical at `e3952a12` and `ef67cee3`.

**Also in the branch (SM ruling):** the teacher-removal log line is one literal, "Removed teacher %s from license %s. Expired %d credit buckets." (it was two adjacent literals with no separator).

**Rule 14:** there is no MagicMock in the new test module.

## Evidence
| Check | Result |
|---|---|
| **Run** @ ef67cee3: my probes Z1a–Z1c, Z2 + `billing.tests.test_neutral_subscription_refusal` | **13 tests OK.** (At e3952a12: 12 OK.) |
| **Z1a: the add-teachers response**, for a paying teacher with no school and for the school's own | 200 with two errors, each exactly the neutral sentence. Apart from the two addresses the admin typed, the body holds no address and none of the telling words (subscription, individual, billing, cancel, plan, paid). Neither teacher is enrolled. Two WARNING reason lines, each with the teacher's id, none with an address. |
| **Z1b: licence creation with `teacher_emails`** | The creation summary's one error is the neutral sentence. |
| **Z1c: the race path** (the teacher subscribes between the invite check and the enrolment, so only the enrolment site refuses) | The result's error is the neutral sentence, and the enrolment site logs its own WARNING with the teacher's id. |
| **My mutant Y7** (the enrolment site's reason line dropped; on `test_vf_h85_mut`) | At ef67cee3: **KILLED by exactly two tests**, d5's adopted `test_the_enrolment_site_logs_its_own_reason` and my probe Z1c. At e3952a12, before the adoption, d5's 8 tests in this module all passed under it. See the correction below. |
| d5's gates (cited) | at 273c2b30: repro 6 tests / 5 failures; (a) 7 modules 95 OK; (b) N1–N7 7/7 killed; (c) billing + 9 guards 2128 OK (skipped=2). Delta at 29bebdcb: the touched modules and two schema guards 29 OK; N6 and N8 2/2 killed. |
| Hooks | `pre-commit run --from-ref 74bfc8d3 --to-ref ef67cee3` passes, and each of the 8 commits passes. |
| Merges | `git merge-tree --write-tree` against beta `74bfc8d3` is **clean**. It was also clean against H-88's `8ea91e21` (both edit `billing/license_service.py`, in different hunks). |

**Rule 17.** Every run had `PYTHONDONTWRITEBYTECODE=1`, and the mutant was applied with `python -B`. `billing/**/__pycache__` was deleted before each baseline, before the mutant and after the restore (the logs show 0 directories each time). The restored `billing/license_service.py` matched the commit blob's sha256, and no tracked file was changed afterwards.

**A correction to what I first reported.** After my run at e3952a12 I said Y7 was killed only by my probe and that the enrolment site's reason line had no test. That was true of the two modules I ran. d5's wider delta gate shows the same mutant is also killed by an H-78 test in another module (`test_a_teacher_who_subscribes_before_enrolment_is_not_logged_by_address`, itself an earlier probe of mine). So the line was already guarded in the suite; the gap was in the H-85 module's own tests. d5's evidence now says it that way.

## O1: what an admin can still tell apart (observation; the founder's decision stands)
My probe Z2 asks for six kinds of address and prints what the admin is told:

| The address belongs to | The admin is told |
|---|---|
| nobody (no account) | added (an invitation is sent) |
| a non-teacher account | "This email can't be added as a teacher." |
| a teacher of another school | "This teacher already belongs to another school." |
| a paying teacher of another school | "This teacher already belongs to another school." (H-78 holds) |
| a paying teacher with no school | the neutral sentence |
| a paying teacher of this school | the neutral sentence |

Four distinct answers. The neutral sentence is used for this one refusal only, so a person who knows the product can still read it as "a teacher who pays for their own plan". The wording itself no longer says so. The SM showed the founder this table; the founder decided to keep H-85 as built.

Logs: `runs/h85_e3952a12.log`, `runs/h85_mutant_Y7_e3952a12.log`, `runs/h85_ef67cee3.log`, `runs/h85_mutant_Y7_ef67cee3.log`. Probe: `h85_probe_test_vf1a_h85_probe.py`. Mutant: `h85_mutant_Y7.py`.
