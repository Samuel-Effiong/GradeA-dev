# custom_ai_prompt leads fix, beta: survey (checkpoint 1, no code)

Brief 5 (SM -31, queued 2026-11-11; started on SM -cf's go after brief 4 was
pushed to beta as 84948e0ac). The owner wants it fixed on beta as a bug fix.
The bug was written up in docs/evidence/signals-beta/DESIGN.md section 11.

## 1. The bug, confirmed on beta 84948e0ac

`BetaAnalyticViewSet.custom_ai_prompt` (POST analytics beta/custom-ai-prompt)
builds a context template from 11 blocks, each fetched by calling another
action and taking `.data`, each inside its own bare `except Exception` that
substitutes an empty value. For the leads (billing/views.py, the
`intent_leads = self.intent_signals(request, *args, **kwargs).data[:10]`
line, about 2625), `.data` is the paginated dict, slicing it raises
KeyError, and the block is always `[]`.

M0 (NOTES) captured the template the AI actually received: the TOP 10
HIGH-INTENT LEADS block is `[]`. The template wording sits in an f-string.
Each block is the Python repr of serializer data (DRF 3.18: plain dicts and
lists).

The existing billing/tests/test_custom_ai_prompt_beta.py checks access and
that the admin's prompt is passed through, never the context's contents.

## 2. Proposed fix

- **One source for the rows.** Extract the row-building loop of
  `intent_signals` into a shared helper, `lead_rows(profiles)`, and the
  default lead queryset into `top_leads(now, limit)`:
  - teacher profiles;
  - `annotate_signals(now)` from billing/intent_signals.py;
  - the leads list's default order: `-conversion_probability`,
    `-total_credits_used`;
  - `[:limit]`.
  `intent_signals` keeps using the same pieces, so its payload stays pinned
  byte-identical (brief 4's pins). `custom_ai_prompt` takes
  `top_leads(request_now(request), 10)`, with no Response and no slicing of
  paginated data.
- **The except** (see Q-L2).
- **No wording change.** The template text is not touched. Only the lead
  block's content changes, from `[]` to the rows (fields per Q-L1), rendered
  the way every other block is (the repr of a list of dicts).

## 3. Questions for the SM

- **Q-L1, PRIVACY: which fields go to the AI.** A lead row today is
  {id, name, email, score, metrics, flags}. The template's header says "(For
  specific outreach)", so the original intent was clearly to identify
  people. But fixing the bug as written would **start sending ten teachers'
  names and email addresses to the AI gateway** (OpenRouter; zero data
  retention is NOT on by default, see section 9. The original line here
  said "zero data retention", which was wrong, corrected 2026-11-11).
  Today the prompt carries no names or emails. It already carries user
  UUIDs (the distribution and quintile blocks, M0).
  - (a) Send the full row, as intended. Most useful for "who should we
    contact?".
  - **(b) Recommended: send id, score, metrics and flags; leave out name and
    email.** This is data minimization, matches what the prompt already
    carries, and the admin can look an id up in the leads list.
  - The privacy doc does not cover teacher data in this prompt, so this
    looks like an owner decision.
- **Q-L2, the except.** The brief allows removing it or narrowing it to a
  named exception and logging it.
  - Recommendation: narrow it to `django.db.DatabaseError`, log it with
    `logger.exception` (the message only, no lead data), and fall back to
    `[]`. A database hiccup in one block then degrades the prompt, as the
    other blocks do, rather than killing the answer.
  - A programming error (like today's KeyError) is no longer caught: it
    fails the request with a 500, and a test shows it.
  - Alternative: no try at all, so any failure is a 500.
- **Q-L3, query params.** custom_ai_prompt is a POST and its body is the
  prompt. If the old call had worked, the leads list's `?search`, `?ordering`
  and filters would have leaked in from the POST's query string. Proposal:
  the block is always the default top 10 over all teachers, and query
  params are ignored.
- **Q-L4, the pinned format.** The lead block is `{lead_rows}` rendered as
  the repr of a list of dicts in the leads list's order, keys in the
  serializer's order. The pin is an exact expected string for a frozen
  fixture, written in the test (red on the base, where the block is `[]`).
  Separately, the **rest of the template is pinned byte-identical** to the
  base (captured on the base before any change), which proves no other
  wording moved.

## 4. Tests and plants (proposed)

Tests first, red on the base:
- the block holds 10 leads out of 12 teachers, in the default order, with
  the Q-L1 fields and no others;
- the exact block string (Q-L4);
- the rest of the template is unchanged (base pin);
- query params do not change the block (Q-L3);
- a DatabaseError in the lead query is logged and gives `[]`; any other
  error is a 500 (Q-L2);
- the block's flags equal the leads list's flags for the same teachers
  (one shared source);
- query count: building the block adds a fixed number of queries
  (expected +1 select, same for 3 and 12 teachers).

Plants:
1. back to slicing the Response;
2. the block ignores the shared order (by -total_credits_used only);
3. email or name included (if Q-L1 = b);
4. the bare except restored (the 500 test kills it);
5. the block honors query params.

Near: users + billing + the 60 beta guard labels.

## 5. Side findings (not in scope, not fixed)

- The other 10 blocks still use a bare `except Exception`. In M0, WEEKLY
  CREDIT TRENDS and PEAK USAGE HOURS were `[]`, but the probe created no
  usage logs, so that proves nothing (NOTES records the slip). A later job
  could probe each block with real logs.
- The template's definitions say "HIGH INTENT: User has hit 80% of their 20M
  credit grant", while the leads block is ordered by conversion probability.
  That is a wording question, untouched here.

## 6. Staging-side differences

billing/views.py and billing/intent_signals.py are identical on beta and
staging once the SM's beta-into-staging merge of brief 4 lands (in progress,
no conflicts per SM -cf). The fix touches only billing/views.py (plus tests),
so I expect a clean merge.

## 7. Time

3-4 h after the rulings: tests and pins 1.5 h, product 45 min, plants 30
min, near 15 min, evidence and checkpoint 30 min.

## 8. Rulings (SM -cf on ae8c9c11a): GO

- **Q-L1: the OWNER chose (a), the full row including name and email
  (asked directly by the SM, 2026-11-11).** This is the owner's decision.
  Plant 3 becomes "name or email dropped from the block", which the
  exact-string pin must kill. The privacy page is not edited here; the SM
  will raise it with the owner.
- **Q-L2 accepted.** Narrow the except to django.db.DatabaseError and log
  with logger.exception, the message only, so no lead data (names, emails)
  reaches the logs; a test asserts that. Fall back to `[]`. A programming
  error is a 500 (tested).
- **Q-L3 accepted.** Query params are ignored. Plant 5 as proposed.
- **Q-L4 accepted.** The exact lead-block string comes from a frozen fixture;
  the rest of the template is pinned byte-identical to the base. The M0 slip
  stays recorded as it is, not amended.
- The other bare excepts and the HIGH INTENT wording go to backlog (section
  5), untouched.

## 9. ZDR check (asked by the SM): CORRECTION to section 3

Section 3 called the gateway "OpenRouter, zero data retention". The code
says otherwise by default:
- custom_ai_prompt → ai_processor.custom_ai_prompt_retry → custom_ai_prompt
  → execute_graded_task → __ai_model, the single provider call, which sends
  `"provider": _provider_block()` (ai_processor/services.py:1235).
- `_provider_block` (services.py:878-912) always sends `data_collection:
  "deny"` (by its own docstring this "by itself excludes nobody"). It sends
  `zdr: True` only while `AI_PROVIDER_ZDR_ENABLED` is on.
  AutoGrader/settings.py:694 defaults that to False, "OFF by default (the
  owner, 24 October 2026)", and "without it several of our suppliers retain
  prompts".
- The production value is not visible from here. The setting is unchanged
  by this job.

So unless production sets the variable, the lead block's names and emails
would go to suppliers that may retain prompts. That was reported to the SM
(2026-11-11) with a request that the owner confirm Q-L1 (a) knowing this.
All lead-row code and tests are held until then. Only the base pin (P0),
which does not depend on Q-L1, goes ahead.

## 10. Q-L1 reconfirmed by the owner, knowing the ZDR facts (2026-11-11)

The SM asked the owner again and gave them the section 9 facts:
- `data_collection: "deny"` is always sent;
- `zdr: True` is sent only while AI_PROVIDER_ZDR_ENABLED is on;
- that setting defaults to False (OFF since 24 October 2026, the owner's
  decision);
- without it several suppliers may retain prompts.

**The owner chose (a), the full row including names and emails,
"regardless", knowing these facts.** The build proceeds with (a). The ZDR
setting and the privacy page are untouched by this job. The SM is raising
the privacy page's "zero data retention" sentence with the owner
separately.
