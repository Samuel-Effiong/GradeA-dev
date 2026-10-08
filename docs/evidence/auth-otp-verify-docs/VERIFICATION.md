# Verification: auth OTP/verify/reset docs @ 752c73f

**Verifier:** Verification Engineer (1a, grade-automator-plus-1d). **Author:** SM.
**Base:** task/beta-batch-2a ddfca77. **Date:** 2026-09-30.

**Verdict: VERIFIED-WITH-NOTES.** The change is docs and schema only, with no runtime change. Almost every documented fact matches the code. **D1 is a wording fix to make before merge:** two sentences in the `/auth/otp` docs tell the frontend something the API doesn't do.

## What I checked
| Check | Result |
|---|---|
| Scope | One commit on ddfca77, touching only `users/views.py`. The changes are the `@extend_schema` arguments of `verify`, `otp` and `reset_password`, plus two module-level example helpers. **No view body changes.** The drf-spectacular imports already existed. |
| Schema generation (my detached checkouts, `systemd-run` MemoryMax=6G, nice, timeout) | `spectacular` succeeds at 752c73f. The warning and error counts are the same as ddfca77 (61 / 25), and no error mentions the three auth views. Beyond the three auth paths, the only schema differences are (a) the `VerifyCustomUser` response component being dropped, and (b) assignment examples built from `now()` and `uuid4` at generation time, which is noise. |
| `--validate` | Fails with the same `'null' is not one of [...]` error at **ddfca77 too**, so it was already failing and is not caused by this change. |
| Dropped `VerifyCustomUser` component | That was the old, wrong response doc: it described a default 200 with `{token, email, user}`, while the view returns 202 with `{refresh, access, user}`. The request component `VerifyCustomUserRequest` is unchanged. This is a correction. |
| Error body shape | The example body `{"success": false, "message": m, "error": {"field_errors": {"detail": m}}}` matches real rendered 400 and 429 bodies, captured through the real renderer by my H-53 probe. |
| `/auth/verify` | Confirmed against the code: 202 with `refresh`, `access` and `user`; the three 400 messages, verbatim; the link `…/verify-email?email=…&token=…` (`users/services.py:100`); the 15-minute code (`services.py:89`); 5/hour per IP (`verify_email`). |
| `/auth/otp` | Confirmed: the 400 texts; `RESET_PASSWORD` while L2-locked gives 202 and sends nothing; 5/hour per IP (`otp_request`). **Two statements are wrong; see D1.** |
| `/auth/reset-password` | Confirmed: 200 with `detail`, `access` and `refresh`; one generic 400 text; `RESET_LOCKED` with `locked_until` and `retry_after_seconds` (`_reset_locked_response`); 10/hour per IP. **"Signs the user out of every other device" is true.** Refresh tokens are blacklisted, and `set_password` bumps `token_epoch` (H-3), so outstanding access tokens also stop authenticating. A weak password comes back as `field_errors.new_password`, because the serializer runs `validate_password`. |

## Notes
**D1 (fix before merge; wording only).** In the `/auth/otp` docs:
- "**Always 202 when the request is well formed**, whether or not the email has an account" is wrong. The documented 400s ("Email already verified. Please login.", "Email not verified.") are returned **only for existing accounts**.
- The 202 description "Same neutral answer whether or not the account exists" is wrong on the wire:
  - an unknown address gets `"If an account with that email exists, an OTP has been sent."`;
  - an existing account gets `"An OTP has been sent if an account with that email exists."`.
- The advice to the frontend (show one neutral confirmation, don't branch on `message`) is right; keep it.
- Suggested wording: "An unknown address always gets 202. Show the same neutral confirmation for every 202 and never branch on `message`."

**D2 (backlog, Security; code, not docs).** Both D1 differences are account-existence oracles already present on beta. The 202 wording difference is a one-line fix: use one string in both branches. The account-state 400s are a product decision, already recorded as H-53's note N3. Suggest one backlog row for ed.

**D3 (sequencing with H-53).** Once H-53 lands, `/auth/verify` gains a per-address 429 ("Too many incorrect codes for this email address…", `Retry-After` up to 1800), and `/auth/otp` `VERIFY_EMAIL` silently sends nothing while the address is locked. H-53's rebase onto beta should update these docs in the same commit. The docs edit the decorators and H-53 edits the view bodies, so the hunks are separate.

**D4 (informational).** The pre-existing `--validate` failure and the nondeterministic assignment examples make schema diffs noisy; both are backlog items. The 202, 200 and 400 response schemas are free-form `object`s, with the shape carried by the examples. That's acceptable for frontend docs.

## Re-verification: D1 @ 4ed4ee5. Verification Engineer, 2026-09-30
**Verdict for the tip 4ed4ee5: VERIFIED.** 4ed4ee5 is one wording-only commit on top of 752c73f (`users/views.py`, +5/−5, inside the `/auth/otp` docs only). It now says:
- an unknown address always gets 202;
- the frontend shows one neutral confirmation for every 202 and never branches on `message`, because its wording can differ;
- the account-state 400s happen only for existing accounts.

All of that matches the code, so D1 is closed. D2 goes to backlog H-43 (ed, batch-3). D3 is carried by H-53.
