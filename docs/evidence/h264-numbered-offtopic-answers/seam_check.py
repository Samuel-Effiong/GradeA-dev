"""H-264 seam, by program: between the base and the tip, only the named units
changed, and inside the four changed methods only the named statements.

Comments do not reach the syntax tree. Each file's top-level statements are
keyed (a function or class by its name, a class's methods as "Class.method", an
assignment by its targets, an import by its text, the module docstring as
"__doc__"); the keys that were added, removed or changed must be exactly the
allowed set for that file. Two of the changed files hold only a comment change,
so their allowed set is empty (settings) or just the docstring (schemas).

Then, for each changed function or method:
- its argument list is exactly the one written here, at the base and at the tip;
- the statements of its body that only the base has, and the ones only the tip
  has, each match one written marker, one for one. A docstring change is
  reported and allowed; nothing else is.

The prompt (rule: a suffixed version, the old file archived):
- the archived ANSWERS_EXTRACTION_PROMPT_HTML_4.txt is byte for byte the base's;
- every line of it is a line of ANSWERS_EXTRACTION_PROMPT_HTML_5.txt, in order
  (nothing was removed or rewritten), and the lines _5 adds are the written
  count and hold the written phrases.

Usage: python -I seam_check.py <base dir> <tip dir>; exit 0 = as packed.
"""

import ast
import copy
import difflib
import pathlib
import sys

SERVICES = "ai_processor/services.py"
SETTINGS = "AutoGrader/settings.py"
SCHEMAS = "ai_processor/extraction_schemas.py"
PROMPT_4 = "ai_processor/ANSWERS_EXTRACTION_PROMPT_HTML_4.txt"
ARCHIVED_4 = "ai_processor/prompts_archive/ANSWERS_EXTRACTION_PROMPT_HTML_4.txt"
PROMPT_5 = "ai_processor/ANSWERS_EXTRACTION_PROMPT_HTML_5.txt"

#: The units that may differ, per file. Empty = nothing in the tree may differ.
ALLOWED = {
    SERVICES: {
        "from html import unescape as unescape_html",
        "ANSWERS_EXTRACTION_PROMPT",
        "_retranscription_evidence",
        "_BLOCK_BREAK",
        "_LABELLED_SECTION",
        "_BARE_SECTION",
        "PILE_QUOTE_LENGTH",
        "_in_order",
        "_numbered_sections",
        "_quoted_start",
        "_piled_answers",
        "_recovered_answer",
        "SPLIT_ANSWER_NOTE",
        "AIProcessor._verify_blank_answers",
        "AIProcessor._retranscribe_found_answers",
        "AIProcessor._second_reading",
    },
    SETTINGS: set(),
    SCHEMAS: {"__doc__"},
}

#: Added whole, so there is nothing to compare statement by statement.
NEW_UNITS = {
    "_BLOCK_BREAK",
    "_LABELLED_SECTION",
    "_BARE_SECTION",
    "PILE_QUOTE_LENGTH",
    "_in_order",
    "_numbered_sections",
    "_quoted_start",
    "_piled_answers",
    "SPLIT_ANSWER_NOTE",
}

#: unit -> (base arguments, tip arguments), spelled out.
SIGNATURES = {
    "_retranscription_evidence": ("entry", "entry, pile_line=None"),
    "_recovered_answer": ("entry, replacement", "entry, replacement, note=None"),
    "AIProcessor._verify_blank_answers": None,  # unchanged; printed, not pinned
    "AIProcessor._retranscribe_found_answers": None,
    "AIProcessor._second_reading": (
        "self, user, content, assignment, targets, *, assignment_model, "
        "processing_task_id, run, config",
        "self, user, content, assignment, targets, pile_lines, *, "
        "assignment_model, processing_task_id, run, config",
    ),
}

#: unit -> {"out": markers of the statements only the base has,
#:          "in": markers of the statements only the tip has}, one for one.
BODY = {
    "_retranscription_evidence": {
        "out": [],
        "in": ["if pile_line is not None"],
    },
    "_recovered_answer": {
        "out": ["_joined_detail(detail, RECOVERED_ANSWER_NOTE)"],
        "in": ["note if note is not None else RECOVERED_ANSWER_NOTE"],
    },
    "AIProcessor._verify_blank_answers": {
        "out": [
            "skipping the re-read",
            "found_by_key[self._question_number_key(finding.get('question_number'))]",
        ],
        "in": [
            "if cap <= 0",
            "re-checking the lowest-numbered",
            "asked = {self._question_number_key(number) for number in numbers}",
            "if key in asked",
        ],
    },
    "AIProcessor._retranscribe_found_answers": {
        "out": [
            "targets = [entry for entry in answers if isinstance(entry, dict) and "
            "entry.get('answer_status') == NOT_FOUND_IN_DOCUMENT",
            "found = self._second_reading(",
            "for entry in answers:",
        ],
        "in": [
            "pile_lines, named_by_pile = _piled_answers(answers)",
            "id(entry) in pile_lines",
            "self._second_reading(",
            "returned_ids = {",
            "SPLIT_ANSWER_NOTE",
        ],
    },
    "AIProcessor._second_reading": {
        "out": ["_retranscription_evidence(entry) for entry in targets"],
        "in": ["pile_lines.get(id(entry))"],
    },
}

#: The prompt: _5 is _4 plus this many lines, holding these phrases.
PROMPT_ADDED_LINES = 100
PROMPT_PHRASES = (
    "it is NOT one piece of work",
    "Example 4: A Numbered Paper for Another Assignment",
    '"question_count": 75',
    "Questions 7 to 75: one entry each, like question 7.",
)


def _key(node, first):
    if (
        first
        and isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ):
        return "__doc__"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    if isinstance(node, ast.Assign):
        return ",".join(ast.unparse(t) for t in node.targets)
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return ast.unparse(node)
    return "stmt:" + ast.dump(node)


def _units(tree):
    units = {}
    for i, node in enumerate(tree.body):
        if isinstance(node, ast.ClassDef):
            shell = copy.deepcopy(node)
            shell.body = [
                n
                for n in shell.body
                if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            units[node.name] = ast.dump(shell)
            for n in node.body:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    units[f"{node.name}.{n.name}"] = n
        else:
            units[_key(node, i == 0)] = node
    return units


def _dump(x):
    return x if isinstance(x, str) else ast.dump(x)


def _is_docstring(node):
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _body_changes(base_fn, tip_fn):
    """(statements only the base has, statements only the tip has, docstring
    changed), each statement as its one-line source."""
    base_body, tip_body = list(base_fn.body), list(tip_fn.body)
    doc_changed = False
    if base_body and tip_body and _is_docstring(base_body[0]):
        doc_changed = ast.dump(base_body[0]) != ast.dump(tip_body[0])
        base_body, tip_body = base_body[1:], tip_body[1:]
    base_dumps = [ast.dump(s) for s in base_body]
    tip_dumps = [ast.dump(s) for s in tip_body]
    matcher = difflib.SequenceMatcher(None, base_dumps, tip_dumps, autojunk=False)
    out, into = [], []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        out += [_one_line(s) for s in base_body[i1:i2]]
        into += [_one_line(s) for s in tip_body[j1:j2]]
    return out, into, doc_changed


def _one_line(node):
    return " ".join(ast.unparse(node).split())


def _match_markers(name, side, statements, markers):
    """Every statement matches one marker and every marker one statement."""
    ok = True
    left = list(statements)
    for marker in markers:
        hits = [s for s in left if marker in s]
        if len(hits) != 1:
            print(f"SEAM: {name} {side}: {len(hits)} statements hold {marker!r}")
            ok = False
            continue
        left.remove(hits[0])
    for extra in left:
        print(f"SEAM: {name} {side}: a statement with no written marker: {extra[:160]}")
        ok = False
    return ok


def _args(fn):
    args = copy.deepcopy(fn.args)
    for node in ast.walk(args):
        if hasattr(node, "annotation"):
            node.annotation = None
    return " ".join(ast.unparse(args).split())


def check_prompt(base_dir, tip_dir):
    ok = True
    base_4 = (base_dir / PROMPT_4).read_bytes()
    if (tip_dir / ARCHIVED_4).read_bytes() != base_4:
        print("SEAM: the archived _4 is not byte for byte the base's _4")
        ok = False
    if (tip_dir / PROMPT_4).exists():
        print("SEAM: _4 is still beside the live prompts")
        ok = False
    lines_4 = base_4.decode("utf-8").splitlines()
    lines_5 = (tip_dir / PROMPT_5).read_text(encoding="utf-8").splitlines()
    added = [
        line[2:]
        for line in difflib.ndiff(lines_4, lines_5)
        if line.startswith(("+ ", "- ", "? "))
    ]
    removed = [
        line for line in difflib.ndiff(lines_4, lines_5) if line.startswith("- ")
    ]
    if removed:
        print(f"SEAM: _5 drops or rewrites {len(removed)} of _4's lines")
        ok = False
    if len(added) != PROMPT_ADDED_LINES:
        print(f"SEAM: _5 adds {len(added)} lines, not {PROMPT_ADDED_LINES}")
        ok = False
    text = " ".join(" ".join(added).split())
    for phrase in PROMPT_PHRASES:
        if phrase not in text:
            print(f"SEAM: the added lines do not hold {phrase!r}")
            ok = False
    print(f"prompt: _4 archived unchanged; _5 = _4 + {len(added)} lines")
    return ok


def main():
    base_dir, tip_dir = map(pathlib.Path, sys.argv[1:3])
    ok = True
    for path, allowed in ALLOWED.items():
        base = _units(ast.parse((base_dir / path).read_text(encoding="utf-8")))
        tip = _units(ast.parse((tip_dir / path).read_text(encoding="utf-8")))
        changed = {
            k
            for k in set(base) | set(tip)
            if k not in base or k not in tip or _dump(base[k]) != _dump(tip[k])
        }
        if changed != allowed:
            print(f"SEAM: {path} changed {sorted(changed)}; allowed {sorted(allowed)}")
            ok = False
            continue
        print(f"{path}: {sorted(changed) or 'nothing in the syntax tree'}")
        for name in sorted(changed):
            if name in NEW_UNITS or name not in BODY:
                continue
            want = SIGNATURES[name]
            got = (_args(base[name]), _args(tip[name]))
            if want is not None and got != tuple(want):
                print(f"SEAM: {name} arguments are {got}, not {tuple(want)}")
                ok = False
            else:
                print(f"  {name}({got[1]})")
            out, into, doc = _body_changes(base[name], tip[name])
            ok = _match_markers(name, "base-only", out, BODY[name]["out"]) and ok
            ok = _match_markers(name, "tip-only", into, BODY[name]["in"]) and ok
            print(
                f"  {name}: {len(out)} statement(s) replaced by {len(into)}"
                f"{'; docstring changed' if doc else ''}"
            )
    for name in sorted(set(ALLOWED[SERVICES]) & NEW_UNITS):
        print(f"  new: {name}")
    ok = check_prompt(base_dir, tip_dir) and ok
    if not ok:
        return 2
    print("SEAM OK: only the named units changed, and only the named statements")
    return 0


if __name__ == "__main__":
    sys.exit(main())
