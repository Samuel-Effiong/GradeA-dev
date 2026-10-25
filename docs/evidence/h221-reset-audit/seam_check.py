"""H-221 seam, by program: users/views.py at the tip differs from the base only inside
reset_password, and only by the two new sign_in_failed calls.

Comments do not reach the syntax tree, so the comment edits pass. Every other
top-level statement, every other class body item and every other method must
dump the same at the base and at the tip, and so must reset_password's
decorators and arguments. Inside reset_password, the tip with the two new
calls taken out must dump the same as the base. The two calls are named by
their place: the sign_in_failed call just before the raise in the If whose
test reads _holds_admin_power (H-164, reason INVALID_CODE), and the one just
before the raise in the If whose test is `not user.is_active` (H-202, reason
ACCOUNT_DEACTIVATED, denied=True). Prints the two calls.
Usage: python -I seam_check.py <base source file> <tip source file>; exit 0 = as packed.
"""

import ast
import sys

METHOD = "reset_password"
WANTED = {
    "_holds_admin_power": ("INVALID_CODE", False),
    "not user.is_active": ("ACCOUNT_DEACTIVATED", True),
}


def _is_sign_in_failed(stmt):
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Call)
        and isinstance(stmt.value.func, ast.Name)
        and stmt.value.func.id == "sign_in_failed"
    )


def _which(if_node):
    test = ast.unparse(if_node.test)
    if "_holds_admin_power" in test:
        return "_holds_admin_power"
    if test == "not user.is_active":
        return "not user.is_active"
    return None


def _strip(stmts, found):
    """The statements with the two new calls taken out, recursively."""
    out = []
    for stmt in stmts:
        if isinstance(stmt, ast.If):
            key = _which(stmt)
            body = list(stmt.body)
            if key is not None and len(body) >= 2 and isinstance(body[-1], ast.Raise):
                call = body[-2]
                if _is_sign_in_failed(call):
                    args = call.value.args
                    denied = any(
                        k.arg == "denied" and ast.unparse(k.value) == "True"
                        for k in call.value.keywords
                    )
                    reason = ast.literal_eval(args[3]) if len(args) == 4 else None
                    if (
                        reason,
                        denied,
                    ) == WANTED[
                        key
                    ] and ast.unparse(args[1]) == "user":
                        found.append(ast.unparse(call))
                        body = body[:-2] + body[-1:]
            stmt = ast.If(
                test=stmt.test,
                body=_strip(body, found),
                orelse=_strip(stmt.orelse, found),
            )
        out.append(stmt)
    return out


def _method(tree):
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == METHOD:
                    return node, item
    raise SystemExit(f"SEAM: no {METHOD}")


def _rest(tree, cls_name):
    rest = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            rest.append(("class", node.name, ast.dump(ast.Tuple(node.bases))))
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == METHOD:
                    rest.append(
                        (
                            "method",
                            ast.dump(ast.Tuple(item.decorator_list)),
                            ast.dump(item.args),
                        )
                    )
                else:
                    rest.append(ast.dump(item))
        else:
            rest.append(ast.dump(node))
    return rest


def main():
    base = ast.parse(open(sys.argv[1], encoding="utf-8").read())
    tip = ast.parse(open(sys.argv[2], encoding="utf-8").read())
    base_cls, base_fn = _method(base)
    tip_cls, tip_fn = _method(tip)
    if base_cls.name != tip_cls.name or _rest(base, base_cls.name) != _rest(
        tip, tip_cls.name
    ):
        print("SEAM: something outside reset_password's body changed")
        return 2
    found = []
    stripped = _strip(tip_fn.body, found)
    base_found = []
    _strip(base_fn.body, base_found)
    if base_found:
        print("SEAM: the base already has a call this check would take out")
        return 2
    if len(found) != 2:
        print("SEAM: not exactly the two new calls ->", found)
        return 2
    if ast.dump(ast.Module(stripped, [])) != ast.dump(ast.Module(base_fn.body, [])):
        print("SEAM: reset_password differs by more than the two calls")
        return 2
    for call in found:
        print(call)
    print("SEAM OK: only reset_password changed, by the two sign_in_failed calls")
    return 0


if __name__ == "__main__":
    sys.exit(main())
