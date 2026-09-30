# Verification: the S4 + S6c merges into phase2/epic-a @ 4124d73

**Verifier:** Verification Engineer 2 (v2). **Merges by:** 0b. **Date:** 2026-09-30.
**Tree verified:** phase2/epic-a **4124d73**, the exact tree for Gate 10:
- **ce49cec**: the S4 merge (d23cf13 into 24c2e74, which has S6c);
- fda47d7: the S4 record;
- **58244bc**: the merge of S6c's N1 fix 43bfee2, which auto-merged `students/services.py` again, now against S4's version;
- 2f1c34a / 4124d73: records only.

Per 0b's plan: a remerge-diff of both merges, a static read of the two files both slices touch (`assignments/tasks.py`, `students/services.py`), and v2's S4 + S6c probe files. The run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, timeout) in 0b's slot.

**Verdict: VERIFIED.** OK for Gate 10 on 4124d73.

| Check | Result |
|---|---|
| `git show --remerge-diff ce49cec` / `58244bc` | **empty**: no hand edits in either merge; both files auto-merged |
| Added-lines survival (every non-blank line each side added since its base is present in the merged file) | `assignments/tasks.py`: S4 9/9, S6c 6/6. `students/services.py` @ 4124d73: S4 47/47, S6c 73/73, **S6c N1 10/10** |
| Static read (where the two slices meet) | S4: `history.suppressed()` around the AI grading save (`services.py:495`), the GRADING_COMPLETED `history.snapshot` before/after (`tasks.py:503/523`). S6c: the coded identity refusals, `_teachers_own_students`, `replaced_existing` in the task meta (`services.py:1046`, `tasks.py:854`), and UPLOAD_REFUSALS pass-through. No shared line; neither changes the other's control flow |
| v2 probes @ 4124d73: `audit.tests_vf2_s4_probe` (9) + `students.tests_vf2_s6c_probe` (9) | **18 OK**: S4 P1 publish-all → 2 GRADE_CHANGE; P9 DISTINCT race → exactly 1 event per submission; S6c C4 → "The paper belongs to Pending Pupil…" (N1 fix present); T1 a colleague's student → MISSING, nothing leaked |

Logs: `runs/s4merge_4124d73.log`.
