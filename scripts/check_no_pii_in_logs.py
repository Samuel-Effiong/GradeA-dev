#!/usr/bin/env python
"""
Fails if a `logger.*`/`print` call anywhere in the repo passes
`.email`, `.first_name`, `.last_name` or `.get_full_name()` directly as an
argument (FR-A-04, NFR-CMP-02; plan 04_epic_a_implementation_plan.md §0.5a
item 2).

Confirmed at review time: 11 sites doing exactly this shipped identifiers
(and in two cases full names) to server logs and, via
`LoggingIntegration(event_level="ERROR")` in AutoGrader/settings.py, to
Sentry — regardless of `send_default_pii=False`, which only suppresses
Sentry's *automatic* user/request context, not log message content. Fixed
call sites now log `.id` instead. This check exists so the next one fails
CI instead of waiting for the next manual audit.

AST-based, not grep: a grep pattern only catches an offending attribute on
the same source line as the logging call, and this codebase's actual leaks
were routinely split across several lines (a multi-line `logger.error(...)`
call with the attribute two or three arguments down). An AST walk finds the
call regardless of formatting.

Baseline, not a blanket ban: an independent enumeration during this same
audit found ~96 pre-existing instances of this exact pattern outside the
11 confirmed leaks (mostly `teacher.email`/`user.email` at INFO in
billing/*, logging the school-side actor rather than a student). Fixing
all of them was out of scope for the PR that introduced this check — see
docs/HARDENING_BACKLOG.md — so pre-existing files are grandfathered in
scripts/pii_log_baseline.txt, matching this repo's existing flake8-eradicate
(E800) per-file burn-down convention. A file only needs to leave the
baseline once every violation in it is fixed; this script does not
auto-detect that, so remove a file by hand once it is clean, the same way
E800's per-file exemptions were burned down (docs/evidence/
ITEM9_E800_BURNDOWN_EVIDENCE.md).

Since 2026-10-05 (the bundle 7 merge-down; note added by H-122): the
baseline has no entry, because H-91 fixed the last listed files. The same
rule also runs as a test, AutoGrader/tests_no_pii_in_logs.py. What reaches
Sentry is scrubbed by the three hooks of AutoGrader/sentry_scrubbing.py
(H-89), which replaced the single before_send function this check was
first paired with; what log handlers print is scrubbed by the record
factory in AutoGrader/log_scrubbing.py.
"""
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_FILE = REPO_ROOT / "scripts" / "pii_log_baseline.txt"

BANNED_ATTRS = {"email", "first_name", "last_name", "get_full_name"}
LOGGER_METHODS = {
    "debug",
    "info",
    "warning",
    "warn",
    "error",
    "exception",
    "critical",
    "log",
}
EXCLUDE_DIR_PARTS = {"migrations", "node_modules", ".venv", "venv", ".git"}


def load_baseline() -> set[str]:
    if not BASELINE_FILE.exists():
        return set()
    lines = BASELINE_FILE.read_text().splitlines()
    return {
        line.strip()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    }


def is_excluded(path: Path) -> bool:
    parts = set(path.parts)
    if parts & EXCLUDE_DIR_PARTS:
        return True
    name = path.name
    return name.startswith("test_") or name.startswith("tests_") or name == "tests.py"


def _is_banned_attr_access(node: ast.AST) -> bool:
    if isinstance(node, ast.Call):
        node = node.func
    return isinstance(node, ast.Attribute) and node.attr in BANNED_ATTRS


def _contains_banned(node: ast.AST) -> bool:
    return any(_is_banned_attr_access(n) for n in ast.walk(node))


def _is_logger_or_print_call(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name) and func.id == "print":
        return True
    return isinstance(func, ast.Attribute) and func.attr in LOGGER_METHODS


def find_violations(path: Path) -> list[int]:
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except SyntaxError as exc:
        print(f"error: could not parse {path}: {exc}", file=sys.stderr)
        return []

    lines = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_logger_or_print_call(node)):
            continue
        args = list(node.args) + [kw.value for kw in node.keywords]
        if any(_contains_banned(arg) for arg in args):
            lines.append(node.lineno)
    return lines


def main() -> int:
    baseline = load_baseline()
    new_violations: dict[str, list[int]] = {}

    for path in sorted(REPO_ROOT.rglob("*.py")):
        if is_excluded(path.relative_to(REPO_ROOT)):
            continue
        violations = find_violations(path)
        if not violations:
            continue
        rel = str(path.relative_to(REPO_ROOT))
        if rel in baseline:
            continue
        new_violations[rel] = violations

    if new_violations:
        print(
            "FAIL: found logger.*/print call(s) passing .email/.first_name/"
            ".last_name/.get_full_name() directly as an argument.\n"
            "Log an identifier (e.g. user.id) instead — see plan "
            "docs/phase2/architecture/04_epic_a_implementation_plan.md §0.5a.\n"
        )
        for rel, lines in sorted(new_violations.items()):
            for lineno in lines:
                print(f"  {rel}:{lineno}")
        print(
            "\nIf this is genuinely pre-existing and out of scope for your "
            f"change, add the file to {BASELINE_FILE.relative_to(REPO_ROOT)} "
            "as a tracked exception — do not add a blanket exclusion for a "
            "directory or a new attribute name."
        )
        return 1

    print("OK: no new PII-in-logs violations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
