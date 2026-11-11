# custom_ai_prompt leads fix, beta (brief 5, SM -cf, 2026-11-11): run notes

The owner wants it fixed on beta as a bug fix (2026-11-11). Base origin/beta
84948e0ac (includes brief 4's billing/intent_signals.py), branch
task/ai-prompt-leads-beta, worktree Grade-Automator-Plus-ai-leads-beta, test
DB test_ai_leads_beta. Environment for every run: venv
~/Documents/Virtualenvs/h284_deps_env, --settings=settings_worktree,
--keepdb, --noinput, under flock ~/.machine-fullsuite.lock.

### M0 (survey, declared): what the AI actually receives today

A scratch test (copied into billing/, run, deleted, never committed) creates
3 teacher BetaProfiles with usage logs, mocks
ai_processor.custom_ai_prompt_retry, POSTs beta/custom-ai-prompt as a super
admin, and prints each "### ..." block of the context template it was
given.
Expected:
- the leads block is "[]" (the KeyError from slicing the paginated dict,
  swallowed by the bare except);
- the other blocks are not predicted (finding out is the point).

M0 result (M0_probe.log): POST 200. TOP 10 HIGH-INTENT LEADS = "[]", as
predicted. The bug is confirmed on beta 84948e0ac.
**Slip, recorded:** the declaration said the fixture had usage logs, but the
script created none. So the two other empty blocks (WEEKLY CREDIT TRENDS
METRIC "[]", PEAK USAGE HOURS "[]") prove nothing: no logs, no rows. They are
neither confirmed nor ruled out as hidden failures.
Also seen: the INTENT SIGNAL DISTRIBUTION and FEATURE MIXTURE blocks already
send user UUIDs to the AI today, but no names or emails.

## Rulings (SM -cf on ae8c9c11a): GO. Recorded in DESIGN sections 8-9.

The ZDR check found that zdr is OFF by default (DESIGN section 9). Reported
to the SM. All lead-row code and tests are HELD until the owner reconfirms
Q-L1. P0 does not depend on it.

### P0 (declared): the rest of the template, pinned on beta's base

billing/tests/test_custom_ai_prompt_leads.py, frozen clock 2026-06-15 12:00
UTC. Fixture: 12 teachers with distinct scores and a school admin's profile;
the AI call is mocked. The captured template has the lead block's content
cut out (replaced by "<LEADS>") and UUIDs replaced by emails.
Command: CUSTOM_AI_PROMPT_PIN_WRITE=1, label
billing.tests.test_custom_ai_prompt_leads.TemplatePinTests.test_the_rest_of_the_template_is_what_it_was.
Expected: Ran 1, OK (skipped=1, "pin written"). Then the module without the
variable: Ran 2, OK.

Q-L1 RECONFIRMED by the owner (via SM -cf, 2026-11-11), knowing ZDR is off by default: (a), names and emails included. The hold is lifted (DESIGN section 10).

P0 result (P0_pin_capture.log): pin written (Ran 1, OK skipped=1), then the
module Ran 2, OK, as predicted. The lead block is cut out as "<LEADS>"; the
rest of the template is captured.

### R1 red (declared): tests only, beta's base product code

Labels: billing.tests.test_custom_ai_prompt_leads billing.tests.test_custom_ai_prompt_beta billing.tests.test_intent_signals billing.tests.test_intent_signals_pins.
Count from build_suite: 38.
Expected: Ran 38, FAILED. By distinct test, **6 fail**, and every other test
passes.
- Pass (2 new): TemplatePinTests both (captured on this code).
- Fail:
  - LeadBlockTests.test_the_block_is_exactly_the_top_ten_leads ("[]" !=
    the 10 rows);
  - LeadBlockTests.test_the_block_equals_the_leads_list_first_page ([] !=
    10 rows);
  - LeadBlockTests.test_query_params_do_not_change_the_block (its
    non-empty guard: the block is "[]");
  - LeadBlockFailureTests both (error: billing.views has no top_leads to
    patch);
  - LeadBlockCostTests (error: no lead_rows/top_leads).
- The existing test_custom_ai_prompt_beta and brief 4's
  test_intent_signals/pins all pass.

R1 result (R1_red.log): Ran 38, FAILED (failures=3, errors=3): **the 6 declared, each for its declared reason** ('[]' != the rows; [] != 10 rows; the non-empty guard; AttributeError, no top_leads/lead_rows). The other 32 pass, as predicted.

## Build (product)

billing/views.py only:
- `top_leads(now, limit)` (teacher profiles + annotate_signals, the list's
  default order) and `lead_rows(leads)` (the row builder, moved out of
  intent_signals unchanged);
- intent_signals now calls lead_rows;
- custom_ai_prompt builds its block from
  `lead_rows(top_leads(request_now(request), 10))` through
  ConversionLeadSerializer, inside `except DatabaseError` only.
The log line is a fixed message plus the exception's type name, NOT
logger.exception: a traceback carries the error's own text, which can hold
row values (the test raises one that contains an email). The template text
is not touched.

### R2 green (declared)

The same 4 labels, Ran 38. Expected: OK.

R2 result (R2_green.log): Ran 38, OK, as predicted.

### Before the plants: a gap found while declaring plant 2 (R3, declared)

Plant 2 ("the block ignores the shared order", most credits first) would
SURVIVE: the fixture's scores and credits rise together. Added
LeadBlockTests.test_the_block_follows_the_score_not_the_credits (a
low-score big spender stays out of the top 10).
R3 label: that test. Expected: Ran 1, OK on the product.

R3 result (R3_gap_test.log): Ran 1, OK, as predicted.

## Plants (declared before any plant runs)

The harness plant_leads.py is brief 4's harness with plants L1-L5 (copied
into plants/ after the runs). One plant per run: copy, sha256 equals HEAD,
plant, run the 4 labels (39 tests), restore, sha256 again, delete the copy.
Logs go to plants/<id>.log, sha lines to plants/shas.txt. By distinct test:

| plant | change (billing/views.py) | killed (declared) |
|---|---|---|
| L1-slice | the block slices the leads list's Response again (narrow except kept) | 6: the KeyError now escapes as a 500, so every test that reads the template fails. That is TemplatePinTests.test_the_rest_of_the_template_is_what_it_was, LeadBlockTests' 4 tests (exactly, equals the list, score not credits, query params) and the DatabaseError test. The 500 test, the cost test and the not-vacuous pin survive |
| L2-order | top_leads orders by most credits only | 1: test_the_block_follows_the_score_not_the_credits (R3; the fixture alone cannot tell) |
| L3-email | email dropped from the block | 3: test_the_block_is_exactly_the_top_ten_leads, test_the_block_equals_the_leads_list_first_page, test_the_block_follows_the_score_not_the_credits (KeyError on "email") |
| L4-bare-except | `except Exception` again | 1: test_a_programming_error_is_a_500 |
| L5-params | the block honors ?page_size | 1: test_query_params_do_not_change_the_block |

## Plant results, first pass (logs plants/<id>.log; every restore sha256 OK; no .orig kept)

| plant | result |
|---|---|
| L1-slice | **Prediction incomplete**: 9 killed. The 6 declared, plus the 3 existing BetaAnalyticsCustomAIPromptViewTest tests (forwards the raw prompt, rate limit, kill switch). They POST the prompt too and now get the 500. I left that module out when tracing |
| L2-order | as declared: 1 |
| L3-email | **Prediction wrong**: 5 killed, but every one by a 500. ConversionLeadSerializer requires `email`, so dropping it before serialization crashed the request. The plant never produced a block without email, so it did not test what the SM asked for ("killed by the exact-string pin") |
| L4-bare-except | as declared: 1 |
| L5-params | as declared: 1 |

### L3b (declared): email dropped AFTER serialization

The same intent as L3: the request survives and the block simply lacks
email. Edit: after `.data`, rebuild intent_leads without the "email" key.
Expected kills, 3:
- test_the_block_is_exactly_the_top_ten_leads (string mismatch: the
  exact-string pin);
- test_the_block_equals_the_leads_list_first_page (row mismatch);
- test_the_block_follows_the_score_not_the_credits (KeyError "email" in the
  test).
Every other test passes (the request is 200).

L3b result (plants/L3b-email-after.log): as declared. FAILED (failures=2,
errors=1): the 3 named. test_the_block_is_exactly_the_top_ten_leads fails on
the string itself (the "email" key is missing), so the exact-string pin
kills it. Restore sha256 OK.

### N1 near (declared)

Environment: venv ~/Documents/Virtualenvs/h284_deps_env, --settings=settings_worktree,
--keepdb, --noinput, -v 2, --parallel 1, under flock ~/.machine-fullsuite.lock,
test DB test_ai_leads_beta, worktree Grade-Automator-Plus-ai-leads-beta.
Labels: users, billing, and the 60 beta guard labels (guards60.txt, the same
list as briefs 3 and 4).
Count from build_suite at HEAD: 4415 (brief 4's 4406 + the 9 tests brief 5
adds).
Expected: Ran 4415, OK, failures 0, errors 0. Skip count not predicted.

N1 result: Ran 4415 tests in 621.340s, OK (skipped=5). Matches the
prediction (Ran 4415, OK). The skip count was not predicted; it is the same 5
as briefs 3 and 4. Log N1_near.log.gz.
