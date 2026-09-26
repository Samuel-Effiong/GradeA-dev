# Prompt 1 — Section Code Review & Cleanup

Copy this prompt, fill in `<SECTION>` with one row from the task list in
`docs/CODEBASE_AUDIT_SECTIONS.md` (e.g. "Section 2 — billing"), and run it.
Do one section per prompt run so the review stays thorough instead of
skimming the whole repo at once.

---

You are doing a production-readiness code review of one section of the
Grade A+ backend (a Django/DRF app used to auto-grade student assignments
with an LLM pipeline, with Stripe billing and Celery async processing).

Read these two files first, in full, before touching any code:

- `docs/CODE_REVIEW_STANDARDS.md` — the checklist of industry practices this
  codebase must follow. Every finding you report must cite which numbered
  section of that checklist it violates.
- `docs/CODEBASE_AUDIT_SECTIONS.md` — the section breakdown and the task
  list table at the bottom.

Your assigned section for this pass is: **<SECTION>**

Do the following, in order:

1. **Read every file listed under that section** in
   `docs/CODEBASE_AUDIT_SECTIONS.md` in full — not a sample, not a skim.
   If the section references a file not listed there, read it too before
   forming an opinion.
2. **Check it against every relevant item in `docs/CODE_REVIEW_STANDARDS.md`.**
   Skip only the items that are genuinely not applicable to this section
   (state why, briefly). For each item, decide: pass, gap found, or N/A.
3. **For every gap found**, report:
   - File and line reference.
   - Which standards-doc section it violates.
   - Concrete failure scenario — what breaks, for whom, under what
     condition. Not "this could be cleaner" — an actual consequence
     (security hole, silent wrong grade, race condition, N+1 that falls
     over at scale, etc.).
   - Severity: blocker (ships a bug or security hole) / should-fix
     (real but not urgent) / nice-to-have (style/clarity only).
4. **Fix what's safe to fix directly**: style violations, missing
   `select_related`, missing indexes (as a migration), missing error
   handling, dead code removal within this section's own files, comment
   cleanup. Make these changes directly and note what you changed.
5. **Do NOT unilaterally do any of the following** — surface them as
   recommendations for the user to confirm instead:
   - Deleting or moving any file (especially anything flagged under
     Section 11, root-level hygiene, in the audit-sections doc).
   - Changing public API contracts (URL paths, serializer fields, response
     shape) that other systems (frontend, Stripe, mobile) may depend on.
   - Changing billing/financial logic, migrations that touch existing
     production data, or anything in `billing/immutable.py` and its
     callers — propose the change and the reasoning, do not apply it.
   - Changing AI prompts or grading logic without also running the
     relevant benchmark (see Prompt 2 for how) — propose it, flag it as
     needing the test pass, don't apply blind.
6. **Update the task list table** in `docs/CODEBASE_AUDIT_SECTIONS.md` for
   this section: set status to `gaps found` or `clean`, and add one line
   of notes summarizing the outcome.
7. **Run the pre-commit checks** (`pre-commit run --files <files you
   touched>`) on anything you changed and fix what it flags before
   reporting done.

At the end, give a plain-language summary (per this project's standing
preference): what you found, what you fixed automatically, and — as a
clearly separated list — what needs the user's explicit sign-off before it
can be applied, with your reasoning for each. Do not report the section as
"done" if any blocker-severity gap is still open.
