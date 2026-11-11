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
