# Verification: Epic A S4 @ d23cf13

**Verifier:** Verification Engineer 2 (v2). **Author:** Security (ed). **Date:** 2026-09-30.
**Branch:** task/epic-a-s4 @ **d23cf13** (gates on f8267bc; d23cf13 adds evidence only; includes 0b's merge of be1147a = S6b). Design: DESIGN.md (the survey, R1–R6) + EVIDENCE.md (the SM's follow-up rulings).

Every run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`, RACE_COST 600/200) in 0b's slot, from a scratch worktree detached at d23cf13. Rule 15: v2's probes, mutants and the changed modules. ed's `users` regression (686 OK) is cited, not repeated.

**Verdict: VERIFIED-WITH-NOTES.** The notes are non-blocking.

## Static
- **Production `suppressed()` sites are exactly two:** `record_bulk`'s own update (`audit/history.py`) and the AI grading save (`students/services.py:493`). The third listed site, `audit/bench_history.py`, is the Gate 6 benchmark: an `APITestCase` run only by explicit label, importing test fixtures (`audit.tests_history.World`), imported by no view, task, service or command, and not matching the test pattern. It is **not reachable from the running app** (SM's question).
- **The four `acting_as(user)` sites** are verify, register_student, register_school_admin and Google (`users/views.py:887/1747/1857/2151`). Each names the account the credential check just established. The login activation passes `actor=student`, the authenticated account, never request input (R3).
- `record_bulk`: an unlocked pk read, then a lock by pk in pk order on the base manager, then an update by pk with the caller's queryset as a subquery. So a DISTINCT/annotated caller queryset is safe (v2's earlier concern; proven by P9 below).
- The registry and `BEFORE_AFTER_ALLOWLIST` are per action; the CustomUser tracked set is {user_type, is_active, is_staff, is_superuser, school_id}.

## Evidence
| Check | Result |
|---|---|
| Changed modules (the whole `audit` app + ed's 13 mapped labels) + v2's probes | **558 run**. All of ed's pass. v2's first-run probe failures were **fixture** issues (duplicate student names, an unfunded wallet, update-grade needing `feedback` as a dict and `max_points`), fixed; then v2's S4 probe file is **9 OK** and the S3 probe file OK |
| P1 publish-all (real route) | exactly 2 GRADE_CHANGE (is_published false→true), actor = teacher, targets = both submissions, no STATE_CHANGE, no sentinel |
| P2 single publish ×2 | 1 then 0 (D3) |
| P3 mark-reviewed ×2 | `[({needs_review: true}, {needs_review: false})]` then 0; the `review_reasons` sentinel is never recorded (R1) |
| P4 update-grade | 200; 1 GRADE_CHANGE; no STATE_CHANGE; the feedback sentinel is never recorded |
| P5 login activation (real `/auth/login`, 2 PENDING enrolments) | 2 ROSTER_CHANGE PENDING→ENROLLED, actor = the student; the second sign-in gives 0 (R3) |
| P6 user delete | one PERMISSION_CHANGE, `after=None`, `before` = {is_staff, is_active, school_id, user_type, is_superuser} only; the email/name sentinel and the password hash appear nowhere (R5) |
| P7 untracked `.update(raw_input=…)` / unchanged tracked save | no event |
| P8 cascade delete of a student | PERMISSION_CHANGE + GRADE_CHANGE + ROSTER_CHANGE, each `after=None`, no sentinel |
| **P9 race** (TransactionTestCase, 2 threads, `record_bulk` on a **`.distinct()`** queryset, 6 submissions) | **no error; every submission exactly 1 GRADE_CHANGE** |
| v2 mutants (`vf_s4_mutants.py`, self-consistent PII widenings: registry + both allow-lists) | **2/2 KILLED** by ed's own tests: N1 `review_reasons` tracked (`test_text_credentials_and_token_epoch_are_never_tracked`, …); N2 user `email` tracked (`test_no_recorded_key_names_a_person_or_their_work`, …) |
| ed's gates | prefix 17F/8E of 50 on be1147a; 530 OK; 17/17 mutants; users 686 OK; mypy passed; Gate 6 +4 ms p95 per tracked save, +10% per roster import of 30 (committed) |

## Notes
- **N1.** `audit/bench_history.py` is test tooling living as an app module. Move it under a test/bench path, or label it as such in the guard entry, so it is never mistaken for production code.
- **N2 (intended, recorded).** After S4, a school admin's add_teachers names the admin in **3** events: CREDIT_TRANSACTION (S3), the teacher's PERMISSION_CHANGE (`school_id`), and the seat's SUBSCRIPTION_CHANGE (R4). Each is a real change on a different record. S1's invariant (≥1 naming the requester, no STATE_CHANGE) holds. v2's S3 probe is updated to that invariant.
- **N3 (integration).** S4 and S6c both touch `assignments/tasks.py` and `students/services.py`. Per 0b, the S4 merge into epic-a (which now has S6c) gets a remerge-diff, a static read of those two files, and v2's S4 + S6c probes before Gate 10.

Logs: `runs/s4_run1.log` (+ `s4_run1b…1e` probe fixture reruns), `runs/s4_run2_mutants.log`.
