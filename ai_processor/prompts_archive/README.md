# Superseded prompts

Every file in this directory is a **retired** version of a prompt. Nothing
in the codebase loads any of them, and nothing should: `services.py` reads
prompts from `ai_processor/` itself (see `PROMPT_DIR` / `_load_prompt`), so
a file here is structurally out of reach rather than merely discouraged.

## Why they are here rather than deleted

`docs/CODE_REVIEW_STANDARDS.md` §8 requires prompts to be versioned by
suffix rather than edited in place, and requires superseded versions to be
"either archived or clearly marked deprecated, not left ambiguous". They
used to sit directly beside the live prompts, which made the live one
findable only by reading `services.py` — a folder listing showed six
plausible `ASSIGNMENT_GENERATION_PROMPT*` files with nothing to say which
one production actually uses.

They are kept because they are the record of how the grading and
extraction contracts evolved. `grading_schemas.py`, for instance, explains
its own shape by contrasting it with `GRADING_ASSIGNMENT_PROMPT_4`.

## Before moving anything else here

Check for references across the whole repository, not just Python — the
docs under `docs/backend/` name several prompts explicitly:

```sh
grep -rIl --exclude-dir=.git -F "THE_PROMPT_FILE.txt" .
```

Archived only when that returns nothing.

## Which prompt is live

`ai_processor/services.py` is the single answer. As of this archive:

| Purpose | Live file |
|---|---|
| Assignment extraction (prose) | `ASSIGNMENT_EXTRACTION_PROMPT_4_PROSE.txt` |
| Assignment extraction (uploads) | `ASSIGNMENT_EXTRACTION_PROMPT_FROM_UPLOADS_HTML_2.txt` |
| Rubric extraction | `RUBRIC_EXTRACTION_PROMPT.txt` |
| Answer extraction | `ANSWERS_EXTRACTION_PROMPT_HTML_4.txt` |
| Grading | `GRADING_ASSIGNMENT_PROMPT_5.txt` |
| Assignment generation | `ASSIGNMENT_GENERATION_PROMPT_6.txt` |
| Grade formatting | `GRADE_FORMATTER_2.txt` |
| Student summary | `STUDENT_SUMMARY_PROMPT.txt` |
| Weekly course summary | `WEEKLY_COURSE_SUMMARY_PROMPT.txt` |
| Weekly school-admin summary | `WEEKLY_SCHOOL_ADMIN_SUMMARY_PROMPT.txt` |
| Dashboard chat (super admin) | `SUPERADMIN_CUSTOM_PROMPT_2.txt` |
| Dashboard chat (school admin) | `SCHOOLADMIN_CUSTOM_PROMPT.txt` |
| Dashboard chat (teacher) | `TEACHER_CUSTOM_PROMPT_2.txt` |
