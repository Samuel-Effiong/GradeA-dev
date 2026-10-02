# Verification: H1, the one-off Stripe schedule backfill is no longer a management command (ed)

- **Branch:** task/h1-backfill-script-move at **daf7179b**, on task/beta-batch-5 83fe58ca. The code tip is 88f019d1; 88f019d1..daf7179b is evidence only.
- **Commits:** 82a2c79a (the move), a7a07983 (the guard test), 88f019d1 (docs rows and the guard's stated limit, from v2's pre-review and the SM's ruling).
- **Verifier:** v2 (independent), 2026-10-02. **No v2 test run and no slot used** (rule 15): the checks below are static, plus one plain-Python sweep over git objects.
- **Verdict:** **VERIFIED-WITH-NOTES**

## Static checks
| Check | Result |
|---|---|
| Base updates f3cfae9f and f5f8b765: `git show --remerge-diff` | Empty on both |
| Added-line survival (`vf_merge_survival.py`) | 0 lines lost on both |
| The move, 82a2c79a | A rename (85% similar). From `import logging` to the end of the file, the script is identical to the pre-move file (`diff` empty). Only the docstring changed |
| No command of that name | No `backfill.py` exists anywhere in the tree at daf7179b |
| Nothing references the old module | No import, no `commands.backfill`, no `call_command("backfill")`, no allow-list entry with the old path, nothing in pre-commit, Docker or shell scripts |
| `scripts/` is not a package | No `scripts/__init__.py`. The file name doesn't match `test*.py`, so test discovery never imports it |
| The guard test | Parses with `ast`, never imports. It skips names starting with `_` and reads top-level modules only, which matches how Django finds commands (`find_commands` skips packages and `_` names) |
| Rule 14 | No mock in the test module |
| Docs (SM ruling) | `BACKEND_REFERENCE.md` and `billing-core.md` no longer list `backfill`; billing-core.md says where the script went and how to run it |

## v2's independent sweep (no Django, no test run)
A plain-Python script read every `*/management/commands/*.py` from git objects at daf7179b and parsed each with `ast`:
- 23 command modules; **every one defines a top-level `Command` class**.
- **None has a module-level statement** other than imports, definitions, assignments and docstrings. So the guard's stated limit (a `Command` class plus module-level side effects) has no instance in the tree today.
- No nested directory under any `commands/`.

## ed's gates (read, not repeated)
| Gate | Result | Log |
|---|---|---|
| Reproduce-first at 83fe58ca's layout | FAILED (failures=2 of 4), as expected: the sweep test and `test_backfill_is_not_a_management_command` | prefix_83fe58ca_failing.txt |
| (a) the module + all beta-line guards, at 88f019d1 | **119 OK**; the guard's 4 tests all ok | modules_and_guards.txt |
| Mutants B1–B3 | **3 of 3 killed** by named tests | mutation_log.txt, mutation_results.json |
| (b) ONE regression, `billing AutoGrader`, at 7616bbe9 | **Ran 2489 tests, OK**; no FAIL or ERROR line | regression_billing_autograder.txt |

- **The full regression log:** v2 recomputed its sha256 prefix, 85b516df8db72ab1 (35,694 lines). The committed file is its last 200 lines; the only differences are trailing spaces stripped at commit.
- **Rule 17:** mutate.py sets `PYTHONDONTWRITEBYTECODE=1` for the test subprocess and deletes `__pycache__` before each mutant and after each restore. EVIDENCE.md states both.
- **The mutants add files** holding a harmless stand-in, never the live script. In the reproduce-first step the real pre-fix file was in the tree, but the tests only parse it and list command names, so it never ran.

## Notes (none blocks)
1. **Two rendered HTML references still list `backfill` as a command:** `docs/backend/backend-reference.html` and `docs/backend/grade-automator-backend-reference.html` (one `<code>backfill</code>` each). They are renders of the Markdown just fixed. For the SM: regenerate them, or leave them as a known stale render.
2. **The script still has no dry run.** Unchanged by H1; EVIDENCE.md assigns it to H-69's "dry run as the default" rows.
3. **Runbooks outside the repo** may still say `manage.py backfill`; it now fails with "Unknown command", which is the safe outcome. ed discloses this as not verified.
