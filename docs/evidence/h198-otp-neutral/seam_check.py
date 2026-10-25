"""H-198 seam, by program: users/views.py at the tip differs from the base only in
the `otp` method (its body and its OpenAPI decorator), and inside that body only
in the two branches the user ruled on.

Comments do not reach the syntax tree. Every other top-level statement, every
other class body item and every other method must dump the same at the base
and at the tip, and so must `otp`'s arguments. Inside `otp`, every statement
must dump the same except the two If statements whose tests read
`user.email_verified_at and user.is_active` (C) and
`not user.email_verified_at and (not user.is_active or _holds_admin_power(user))`
(G); those must now end in the neutral 202 and raise nothing.
Usage: python -I seam_check.py <base source file> <tip source file>; exit 0 = as packed.
"""

import ast
import sys

METHOD = "otp"
C_TEST = "user.email_verified_at and user.is_active"
G_TEST = (
    "not user.email_verified_at and (not user.is_active or _holds_admin_power(user))"
)


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
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            rest.append(("class", node.name, ast.dump(ast.Tuple(node.bases))))
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == METHOD:
                    rest.append(("method", ast.dump(item.args)))
                else:
                    rest.append(ast.dump(item))
        else:
            rest.append(ast.dump(node))
    return rest


def _flatten(stmts, out):
    """Every statement in order, with the two ruled If statements named, not
    dumped (their bodies are checked on their own)."""
    for stmt in stmts:
        if isinstance(stmt, ast.If) and ast.unparse(stmt.test) in (C_TEST, G_TEST):
            out.append(("ruled", ast.unparse(stmt.test)))
            continue
        if isinstance(stmt, ast.If):
            out.append(("if", ast.unparse(stmt.test)))
            _flatten(stmt.body, out)
            out.append("else")
            _flatten(stmt.orelse, out)
            out.append("end")
            continue
        out.append(ast.dump(stmt))
    return out


def _ruled(fn):
    found = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.If) and ast.unparse(node.test) in (C_TEST, G_TEST):
            found[ast.unparse(node.test)] = node
    return found


def main():
    base = ast.parse(open(sys.argv[1], encoding="utf-8").read())
    tip = ast.parse(open(sys.argv[2], encoding="utf-8").read())
    base_cls, base_fn = _method_class(base)
    tip_cls, tip_fn = _method_class(tip)
    if base_cls.name != tip_cls.name or _rest(base, base_cls.name) != _rest(
        tip, tip_cls.name
    ):
        print("SEAM: something outside otp changed")
        return 2
    if _flatten(base_fn.body, []) != _flatten(tip_fn.body, []):
        print("SEAM: otp changed outside the two ruled branches")
        return 2
    ruled = _ruled(tip_fn)
    if sorted(ruled) != sorted([C_TEST, G_TEST]):
        print("SEAM: the two ruled branches are not both there")
        return 2
    for test, node in sorted(ruled.items()):
        last = node.body[-1]
        if not (
            isinstance(last, ast.Return) and "OTP_SENT_DETAIL" in ast.unparse(last)
        ):
            print("SEAM: a ruled branch does not end in the neutral reply:", test)
            return 2
        if any(isinstance(n, ast.Raise) for n in ast.walk(node)):
            print("SEAM: a ruled branch still raises:", test)
            return 2
        print(f"if {test}:")
        for stmt in node.body:
            print("   ", ast.unparse(stmt).replace("\n", "\n    "))
    print("SEAM OK: only otp changed, in the two ruled branches (and its OpenAPI text)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
