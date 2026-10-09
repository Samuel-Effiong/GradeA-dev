# Verification addendum: Epic A S7d delta, 16a6786 → 7468e7e5 (ed)

- **Verifier:** v2, 2026-10-01. Static review; no v2 run (rule 15).
- **Verdict:** **VERIFIED.** The S7d verdict (VERIFIED-WITH-NOTES at 16a6786) stands, and note 2 is closed.

## 32d941ef: base update onto phase2/epic-a dc0475aa (the bundle 4 merge-down)
- **Remerge-diff:** billing/license_service.py conflicted in 2 hunks. Both are resolved to S7d's side (SM ruling).
  1. The "Skipped enrolling" log line: S7d's ids-only form with the school id, `type(exc).__name__`. The epic's 4 "lost" lines are its older comment and format string for the same line (vf_merge_survival: lost_lines=4). They are superseded, not dropped.
  2. `_get_or_invite_teacher`: S7d's H-78 order (the other school before the subscription, SM Q1). The epic's old order with the subscription first is dropped, which is correct.
- **Bundle 4's grace_expiry:** present in all 5 sites, including `_enroll_teacher_internal`.

## 291d8ca7: the H-78 fold
- The `_enroll_teacher_internal` hunk is identical to 9675d17's: the subscription refusal is logged by teacher.id, not by error_msg.
- The test module is byte-identical to 8c248e8, which is the current source tip b9e4ccb.

## 05579172: v2 note 2, closed
- Comment only. sync_only_violations' docstring names (1) an error bound to a variable and raised later, and (2) a task reaching a builder through a helper module.

## Batch-5 byte-identity (c4ac9e08)
- `_invite_and_enroll_one_teacher` and `_get_or_invite_teacher` are identical (AST segments).
- `_enroll_teacher_internal` differs exactly as EVIDENCE says:
  - epic-only: "Created CreditWallet" logs teacher.id;
  - beta-only: H-76's is_processed retirement, which arrives with the bundle 5 merge-down.

## ed's re-run on 734bef49 (read)
- Step 0: the prefix fails exactly 1 (9 tests).
- Step 1: changed modules + 12 guards, 590 OK.
- Step 2: billing, 1976 OK.
- makemigrations --check: clean.
- No mutation step in this delta (rule 17 does not apply).
