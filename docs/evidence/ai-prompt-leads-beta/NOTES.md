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
