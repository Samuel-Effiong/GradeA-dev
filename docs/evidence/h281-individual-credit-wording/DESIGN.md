# H-281 DESIGN: credit refusal wording for individual teachers

Branch `task/h281-individual-credit-wording` from origin/beta `e11e56536`.
This is the design only. There is no code yet, and none will be written
until the SM approves this note.

## Wording (owner-approved 2026-11-03, verbatim)

| | Today (unchanged for everyone else) | Individual teacher |
|---|---|---|
| error | There aren't enough AI credits to do this. Top up your credits or ask your school administrator, then try again. | There aren't enough AI credits to do this. Top up your credits, then try again. |
| remediation | Top up credits, or ask your school administrator, then try again. | Top up credits, then try again. |

There is no other new wording.

## (a) How "individual" is decided

**Rule:** the **billed user** (the owner of the wallet being charged) is a
`TEACHER` and `not billed_user.is_under_license()`.

Why I don't use the wallet alone, which was your lean. There is no school
wallet.
- Every user has their own `CreditWallet`, created by the user signal
  (`users/signals.py:325`).
- A school teacher's credits arrive in **their own** wallet, through a
  `SchoolCreditAllocation` under the school's `LicenseSubscription`.
- So "whose wallet is charged" is always the teacher's own and can't tell
  the two kinds of teacher apart.
- What does tell them apart is **who funds that wallet**. That is exactly
  the existing helper `CustomUser.is_under_license()` (`users/models.py:242`):
  an active allocation under an active license means school-funded;
  anything else means individual.
- The codebase already uses it for this split, deliberately instead of
  `school_id` (`classrooms/permissions.py:139`, `classrooms/views.py:2028`):
  "school_id stays set even after a teacher is removed from a license or
  the license is cancelled."

So I key on the billed user's funding, and I reuse the existing helper. The
only new code is a one-line predicate, `is_individual_teacher(user)`, next
to `INSUFFICIENT_CREDITS_MESSAGE` in `billing/errors.py`.

The billed user and the requester give the same answer for every
non-student:
- a teacher is billed to themselves;
- a school admin is billed to themselves, so a school admin gets the
  unchanged sentence;
- a student is billed to their teacher, but the student sentence
  (H-216/H-225) is applied first and stays.

**Decision for the SM: how to treat a teacher who still has a `school_id`
but no active license.** That is a teacher removed from a license, or one
whose license lapsed. Under my rule they are *individual*: no administrator
funds them any more, and they would have to top up themselves. If the owner
meant "no school" literally (`school_id is None`), that teacher would keep
the school sentence instead. I recommend `is_under_license()`, for
consistency with sessions.

## (b) Every refusal site

**Where a credit refusal is created** (each one knows the billed user):

| # | Site | Billed user there |
|---|---|---|
| R1 | `billing/models.py:927` `consume_credits`, blocked by a deficit | `self.user` (wallet owner) |
| R2 | `billing/models.py:938` `consume_credits`, balance too low | `self.user` |
| R3 | `billing/models.py:1043` `consume_credits`, defensive re-check | `self.user` |
| R4 | `users/permissions.py:52` `HasCreditBalance`, `EmptyWalletError` | `target_user` (the requester when not a student; the student case is R4s, unchanged) |
| R5 | `billing/access_control.py:599` `require_ai_access`, no or trial credits | `request.user` |
| R6–R8 | `ai_processor/services.py:5592, 5644, 5650` `execute_graded_task` pre-checks | `target_teacher` (its wallet is the one checked) |
| R9 | `assignments/upload_door.py:100` single-upload door | `_billed_user(...)` |
| R10 | `assignments/upload_door.py:139` parked per-file refusal (H-223) | `_billed_user(...)` |

**Where the text reaches a client.** All of these resolve through the same
three places today:
- `AutoGrader/error_messages._passthrough_message`, which gives the message;
- `AutoGrader/reason_codes._refusal_reason` and `coded_response`/`coded_body`;
- `spec.remediation`, which gives the remediation.

| # | Surface | Path |
|---|---|---|
| S1 | HasCreditBalance 402, and any uncaught refusal | `users/exceptions.custom_exception_handler` → `coded_response` |
| S2 | in-view refusals: `billing/refusals.refusal_response` (e.g. `dashboard/views.py:218` custom-ai-prompt) and `students/views.py:160` (H-216 helper; the student override stays) | `coded_response` |
| S3 | `require_ai_access` 402 | `coded_response` (R5) |
| S4 | `describe_user_error(...)` in bodies: `dashboard/views.py:227, 2518, 3597` | `_passthrough_message` (message only) |
| S5 | upload door, sync 402: `upload_door.py:107` (message picked by hand) and `:150` `batch_refusal_response()`, which has no user today and gets the billed user passed in | the constant, directly |
| S6 | polled task errors: `students/task_tracking.mark_processing_task_failure` → `describe_task_error`, which stores `error` and `reason_code` | `_passthrough_message` |
| S7 | session-results items: `students/item_results.item_result`. Its `remediation` is re-derived at **read** time from `reason_code` alone (`spec.remediation`), so the variant has to be stored with the task | see (c) |

**Mid-batch (`INSUFFICIENT_CREDITS_MID_BATCH`) is out of scope.** Its
remediation is "Top up credits, then resume the remaining items. Unfinished
uploads need their files uploaded again." It has no school sentence, and
both of its messages say nothing about who to ask.

## (c) How the spec carries the variant (existing mechanism, no new path)

- **Spec.** `REASON_CODES[INSUFFICIENT_CREDITS]` gets
  `alternative_messages={"individual": INSUFFICIENT_CREDITS_INDIVIDUAL_MESSAGE}`
  and `alternative_remediations=("Top up credits, then try again.",)`. These
  are the same two fields as MID_BATCH, OVERAGE_CAP and TEACHER_LIST_EMPTY.
  `INSUFFICIENT_CREDITS_INDIVIDUAL_MESSAGE` is a constant next to the
  existing one.
- **Variant on the refusal.** An `InsufficientCreditsError` is not a
  `CodedError` (it carries raw balance detail for the logs), so it can't take
  `variant=`. Instead it gets one keyword-only attribute,
  `individual: bool = False`, set at R1–R10 from
  `is_individual_teacher(billed_user)`.
  - It survives pickle, because `BaseException.__reduce__` keeps `__dict__`.
  - Polled failures are recorded in-process, so no serializer is involved.
- **One reader.** `credit_refusal_variant(error)` returns `"individual"` or
  `None`. It is used by:
  - `_passthrough_message` (message);
  - `_refusal_reason` (message, so `reason_of` and `coded_response` follow);
  - `coded_response` (remediation, which today is `None`, meaning the spec
    default, for a non-CodedError).
- **Polled items (S7).** `mark_processing_task_failure` also writes
  `meta["message_variant"] = "individual"` for such a refusal. `meta` is
  JSON already, so there is **no migration**. `item_result` uses it to pick
  the individual remediation. Every other item, with no key, is unchanged.

Considered and rejected: deciding at render time from `request.user`. The
polled path (S6, S7) has no request, and S4, S5 and S6 would each need a
user threaded in. Raise-time tagging gives one decision per refusal and
keeps S1–S7 on the existing resolvers.

## (d) Super admin with `is_superuser` unticked (documented, out of scope)

Real super admins (both flags) are unmetered at every gate and never see
this message. A half-set-up account (`user_type=SUPER_ADMIN`,
`is_superuser=False`) gets the following, **from reading the code**. I will
pin it with characterization tests rather than assert it:

- **HasCreditBalance** (`users/permissions.py:19-24`): the bypass needs both
  flags (H-19), so the account falls through to its **own** wallet, which
  exists via the signal. At 0 credits it gets `EmptyWalletError` → **402
  `insufficient_credits` with the unchanged sentence** ("…or ask your school
  administrator…"). It is not a `TEACHER`, so H-281 leaves it unchanged.
- **`require_ai_access` / `can_user_access_ai`**: no active subscription →
  **403 `ai_feature_not_available`**, "This feature isn't included in your
  plan." That is not a credit message.
- **`execute_graded_task`** (`ai_processor/services.py`, the H-19 branch):
  **refused as `AIFeatureNotAvailableError` (403)**, never a credit refusal.
- **Upload door** (`upload_door.py`, the `user_type == SUPER_ADMIN` early
  returns): checks `user_type` only, so the door lets the account through
  and the task then refuses it with the 403 above. Observation only, not
  changed by H-281. Do you want it as its own ticket? It is the same H-19
  pattern.

## Plan after approval

1. Red tests, one per surface. Each one gives an individual teacher the new
   message and remediation, and gives a licensed teacher, a school admin and
   a student their unchanged text. The cases:
   - S1 HasCreditBalance;
   - S2 refusal_response (custom-ai-prompt);
   - S3 require_ai_access;
   - S4 one describe_user_error body;
   - S5 both upload-door 402s;
   - S6 and S7 a polled failure with session-results remediation;
   - a removed-from-license teacher, following your decision in (a);
   - the characterization tests for (d).
2. One planted bug per guard: the predicate, each raise-site tag, each
   reader, and the S7 meta.
3. Near run: the touched modules plus the credit, refusal and reason-code
   modules. No migration is expected; if one appears, the run includes
   `AutoGrader.tests_migration_rollback_defaults`. The run is held until
   B33 is finished.

## Owner decision (2026-11-03, via the SM): (a) is final

A teacher who still has a `school_id` but no active license (the license
lapsed, or they were removed from it) gets the **INDIVIDUAL** wording. So
`is_under_license()` is confirmed, and `billing.errors.is_individual_teacher`
is final. `REMOVED_FROM_LICENSE_IS_INDIVIDUAL = True` in the test module now
pins the ruling; it is no longer a pending switch.
