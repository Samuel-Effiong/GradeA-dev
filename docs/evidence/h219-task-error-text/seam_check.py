"""H-219 seam, by program: assignments/tasks.py at the tip differs from the base only
inside the five functions that hold the six sites, and each of the five changed.

Comments do not reach the syntax tree. Every other top-level statement must dump
the same at the base and at the tip, and so must the five functions' decorators
and arguments. At the tip the five hold no `str(...)` stored as a FAILED `update_result` error (a
CANCELLED one keeps the cancellation's own fixed text), no
`logger.exception` and no f-string log call.
Usage: python -I seam_check.py <base source file> <tip source file>; exit 0 = as packed.
"""

import ast
import sys

FUNCTIONS = (
    "upload_assignment_async",
    "grade_batch_async",
    "send_assignment_due_reminder",
    "send_new_assignment_posted_notification",
    "prerender_assignment_pdfs",
)


def _rest(tree):
    rest = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS:
            rest.append(
                (
                    node.name,
                    ast.dump(node.args),
                    [ast.dump(d) for d in node.decorator_list],
                )
            )
        else:
            rest.append(ast.dump(node))
    return rest


def _functions(tree):
    return {
        n.name: n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name in FUNCTIONS
    }


def _faults(fn):
    faults = []
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        name = node.func.attr
        if name == "exception" and ast.unparse(node.func.value) == "logger":
            faults.append(f"logger.exception at line {node.lineno}")
        if ast.unparse(node.func.value) == "logger" and any(
            isinstance(a, ast.JoinedStr) for a in node.args
        ):
            faults.append(f"an f-string log call at line {node.lineno}")
        failed = (
            len(node.args) > 1
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "FAILED"
        )
        if (
            name == "update_result"
            and failed
            and any(
                k.arg == "error" and ast.unparse(k.value).startswith("str(")
                for k in node.keywords
            )
        ):
            faults.append(f"update_result(error=str(...)) at line {node.lineno}")
    return faults


def main():
    base = ast.parse(open(sys.argv[1], encoding="utf-8").read())
    tip = ast.parse(open(sys.argv[2], encoding="utf-8").read())
    if _rest(base) != _rest(tip):
        print("SEAM: something outside the five functions changed")
        return 2
    base_fns, tip_fns = _functions(base), _functions(tip)
    unchanged = [n for n in FUNCTIONS if ast.dump(base_fns[n]) == ast.dump(tip_fns[n])]
    if unchanged:
        print("SEAM: these did not change:", ", ".join(unchanged))
        return 2
    faults = [f"{n}: {f}" for n in FUNCTIONS for f in _faults(tip_fns[n])]
    if faults:
        print("SEAM: text or traceback left:", "; ".join(faults))
        return 2
    print(
        "SEAM OK: only the five task functions changed, and none keeps text or a traceback"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
