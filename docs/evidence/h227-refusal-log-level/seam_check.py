"""H-227 seam, by program: users/exceptions.py at the tip differs from the base only
inside `custom_exception_handler`, and there only in its logging.

Comments do not reach the syntax tree. Every other top-level statement must dump
the same at the base and at the tip, and so must the function's arguments. Inside
the function, the statements with every `logger.<level>(...)` call removed must
dump the same: the change moves and splits the log, nothing else. At the tip the
function holds exactly one `logger.warning` (no `exc_info`) and one
`logger.error("API Exception", exc_info=...)`.
Usage: python -I seam_check.py <base source file> <tip source file>; exit 0 = as packed.
"""

import ast
import copy
import sys

FUNCTION = "custom_exception_handler"


def _function(tree):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == FUNCTION:
            return node
    raise SystemExit(f"SEAM: no {FUNCTION}")


def _rest(tree):
    return [
        (
            ("function", ast.dump(n.args))
            if isinstance(n, ast.FunctionDef) and n.name == FUNCTION
            else ast.dump(n)
        )
        for n in tree.body
    ]


def _is_log(stmt):
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Call)
        and isinstance(stmt.value.func, ast.Attribute)
        and isinstance(stmt.value.func.value, ast.Name)
        and stmt.value.func.value.id == "logger"
    )


class _StripLogs(ast.NodeTransformer):
    def generic_visit(self, node):
        super().generic_visit(node)
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(node, field, None)
            if isinstance(stmts, list):
                kept = [s for s in stmts if not _is_log(s)]
                setattr(node, field, kept or [ast.Pass()])
        return node


def _logs(fn):
    return [s.value for s in ast.walk(fn) if isinstance(s, ast.stmt) and _is_log(s)]


def main():
    base = ast.parse(open(sys.argv[1], encoding="utf-8").read())
    tip = ast.parse(open(sys.argv[2], encoding="utf-8").read())
    if _rest(base) != _rest(tip):
        print(f"SEAM: something outside {FUNCTION} changed")
        return 2
    stripped = [
        ast.dump(_StripLogs().visit(copy.deepcopy(_function(t)))) for t in (base, tip)
    ]
    if stripped[0] != stripped[1]:
        print(f"SEAM: {FUNCTION} changed beyond its log calls")
        return 2
    calls = _logs(_function(tip))
    warnings = [c for c in calls if c.func.attr == "warning"]
    crashes = [
        c
        for c in calls
        if c.func.attr == "error"
        and c.args
        and isinstance(c.args[0], ast.Constant)
        and c.args[0].value == "API Exception"
    ]
    refusal = [w for w in warnings if "API request refused" in ast.unparse(w)]
    if len(refusal) != 1 or any(k.arg == "exc_info" for k in refusal[0].keywords):
        print("SEAM: not exactly one refusal WARNING without exc_info")
        return 2
    if len(crashes) != 1 or not any(k.arg == "exc_info" for k in crashes[0].keywords):
        print('SEAM: not exactly one ERROR "API Exception" with exc_info')
        return 2
    print("refusal:", ast.unparse(refusal[0]))
    print("crash:  ", ast.unparse(crashes[0]))
    print(f"SEAM OK: only {FUNCTION}'s logging changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
