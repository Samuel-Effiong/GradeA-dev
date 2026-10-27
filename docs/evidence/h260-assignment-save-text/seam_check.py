"""H-260 seam, by program: between the base and the tip, `assignments/serializers.py`
differs only in the named units, and `AssignmentSerializer.create` only in its except
clause.

Comments do not reach the syntax tree. The file's top-level statements are keyed
(a function or class by its name, a class's methods as "Class.method", an
assignment by its targets, an import by its text); the keys that were added,
removed or changed must be exactly the allowed set. Then:
- `create` with its one `except` clause's body blanked dumps the same at the base
  and the tip;
- that clause at the tip is two statements: a `logger.warning` call whose
  arguments name the error only as `type(e).__name__` and that passes no
  `exc_info`, then `raise serializers.ValidationError(ASSIGNMENT_SAVE_FAILED) from e`;
- `logger` is `logging.getLogger(__name__)`, and `ASSIGNMENT_SAVE_FAILED` is one
  string constant.
Usage: python -I seam_check.py <base dir> <tip dir>; exit 0 = as packed.
"""

import ast
import copy
import pathlib
import sys

PATH = "assignments/serializers.py"
ALLOWED = {
    "import logging",
    "logger",
    "ASSIGNMENT_SAVE_FAILED",
    "AssignmentSerializer.create",
}
CREATE = "AssignmentSerializer.create"
RAISE = "raise serializers.ValidationError(ASSIGNMENT_SAVE_FAILED) from e"


def _key(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    if isinstance(node, ast.Assign):
        return ",".join(ast.unparse(t) for t in node.targets)
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return ast.unparse(node)
    return "stmt:" + ast.dump(node)


def _units(tree):
    units = {}
    for node in tree.body:
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
            units[_key(node)] = node
    return units


def _dump(x):
    return x if isinstance(x, str) else ast.dump(x)


def _handlers(fn):
    return [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)]


def _without_handler_body(fn):
    fn = copy.deepcopy(fn)
    for handler in _handlers(fn):
        handler.body = [ast.Pass()]
    return fn


def _names_e_outside_class(node):
    """Uses of `e` in `node` that are not inside `type(e).__name__`."""
    if (
        isinstance(node, ast.Attribute)
        and node.attr == "__name__"
        and ast.unparse(node.value) == "type(e)"
    ):
        return 0
    found = int(isinstance(node, ast.Name) and node.id == "e")
    return found + sum(_names_e_outside_class(c) for c in ast.iter_child_nodes(node))


def main():
    base_dir, tip_dir = map(pathlib.Path, sys.argv[1:3])
    base = _units(ast.parse((base_dir / PATH).read_text(encoding="utf-8")))
    tip = _units(ast.parse((tip_dir / PATH).read_text(encoding="utf-8")))
    changed = {
        k
        for k in set(base) | set(tip)
        if k not in base or k not in tip or _dump(base[k]) != _dump(tip[k])
    }
    if changed != ALLOWED:
        print(f"SEAM: {PATH} changed {sorted(changed)}; allowed {sorted(ALLOWED)}")
        return 2
    print(f"{PATH}: {sorted(changed)}")
    ok = True
    if ast.dump(_without_handler_body(base[CREATE])) != ast.dump(
        _without_handler_body(tip[CREATE])
    ):
        print(f"SEAM: {CREATE} changed outside its except clause")
        ok = False
    handlers = _handlers(tip[CREATE])
    if len(handlers) != 1:
        print(f"SEAM: {CREATE} has {len(handlers)} except clauses, not one")
        return 2
    body = handlers[0].body
    if len(body) != 2:
        print(f"SEAM: the except clause has {len(body)} statements, not two")
        return 2
    log, raise_ = body
    call = log.value if isinstance(log, ast.Expr) else None
    if not (
        isinstance(call, ast.Call)
        and ast.unparse(call.func) == "logger.warning"
        and not call.keywords
        and not _names_e_outside_class(call)
    ):
        print("SEAM: the first statement is not a logger.warning naming the class only")
        ok = False
    if ast.unparse(raise_) != RAISE:
        print(f"SEAM: the raise is {ast.unparse(raise_)!r}")
        ok = False
    if ast.unparse(tip["logger"]) != "logger = logging.getLogger(__name__)":
        print("SEAM: logger is not logging.getLogger(__name__)")
        ok = False
    sentence = tip["ASSIGNMENT_SAVE_FAILED"].value
    if not (isinstance(sentence, ast.Constant) and isinstance(sentence.value, str)):
        print("SEAM: ASSIGNMENT_SAVE_FAILED is not one string constant")
        ok = False
    if not ok:
        return 2
    print("except clause:", ast.unparse(handlers[0]).replace("\n", " | "))
    print("sentence:", sentence.value)
    print("SEAM OK: only the named units changed, and create only in its except clause")
    return 0


if __name__ == "__main__":
    sys.exit(main())
