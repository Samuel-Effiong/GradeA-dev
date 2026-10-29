"""H-210 seam, by program: users/views.py at the tip differs from the base only in
register_school_admin (its body and its OpenAPI decorator) and in the
`from rest_framework.exceptions import ...` line, which gains exactly `Throttled`.

Comments do not reach the syntax tree. Every other top-level statement, every
other class body item and every other method must dump the same at the base
and at the tip. Prints the import's names and the method's line count.
Usage: python -I seam_check.py <base source file> <tip source file>; exit 0 = as packed.
"""

import ast
import sys

METHOD = "register_school_admin"
MODULE = "rest_framework.exceptions"


def _is_the_import(node):
    return isinstance(node, ast.ImportFrom) and node.module == MODULE


def _method_class(tree):
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == METHOD:
                    return node, item
    raise SystemExit(f"SEAM: no {METHOD}")


def _rest(tree, cls_name):
    rest = []
    for node in tree.body:
        if _is_the_import(node):
            rest.append("the import")
        elif isinstance(node, ast.ClassDef) and node.name == cls_name:
            rest.append(("class", node.name, ast.dump(ast.Tuple(node.bases))))
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == METHOD:
                    rest.append(("method", ast.dump(item.args)))
                else:
                    rest.append(ast.dump(item))
        else:
            rest.append(ast.dump(node))
    return rest


def _names(tree):
    found = [n for n in tree.body if _is_the_import(n)]
    if len(found) != 1:
        raise SystemExit(f"SEAM: {len(found)} imports from {MODULE}")
    return [a.name for a in found[0].names]


def main():
    base = ast.parse(open(sys.argv[1], encoding="utf-8").read())
    tip = ast.parse(open(sys.argv[2], encoding="utf-8").read())
    base_cls, base_fn = _method_class(base)
    tip_cls, tip_fn = _method_class(tip)
    if base_cls.name != tip_cls.name or _rest(base, base_cls.name) != _rest(
        tip, tip_cls.name
    ):
        print("SEAM: something outside register_school_admin and the import changed")
        return 2
    base_names, tip_names = _names(base), _names(tip)
    if sorted(tip_names) != sorted(base_names + ["Throttled"]):
        print("SEAM: the import does not gain exactly Throttled ->", tip_names)
        return 2
    if ast.dump(base_fn) == ast.dump(tip_fn):
        print("SEAM: register_school_admin did not change")
        return 2
    print(f"from {MODULE} import", ", ".join(tip_names))
    print(
        f"{METHOD}: {base_fn.end_lineno - base_fn.lineno + 1} lines at the base, "
        f"{tip_fn.end_lineno - tip_fn.lineno + 1} at the tip"
    )
    print("SEAM OK: only register_school_admin and the Throttled import changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
