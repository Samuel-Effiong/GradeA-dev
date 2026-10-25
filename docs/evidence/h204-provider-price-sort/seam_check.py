"""H-204 seam, by program: ai_processor/services.py at the tip differs from the base only in
_provider_block and the two new constants PROVIDER_SORT and PROVIDER_QUANTIZATIONS.

Comments do not reach the syntax tree, so a comment edit (the one beside the request in
__ai_model) passes. Every other top-level statement, every class and every method must dump
the same at the base and at the tip. Prints _provider_block's code (docstring left out).
Usage: python -I seam_check.py <base source file> <tip source file>; exit 0 = as packed.
"""

import ast
import sys

CHANGED = {"_provider_block", "PROVIDER_SORT", "PROVIDER_QUANTIZATIONS"}


def _name(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
        if isinstance(target, ast.Name):
            return target.id
    return None


def _rest(tree):
    return [ast.dump(n) for n in tree.body if _name(n) not in CHANGED]


def main():
    base = ast.parse(open(sys.argv[1], encoding="utf-8").read())
    tip = ast.parse(open(sys.argv[2], encoding="utf-8").read())
    if _rest(base) != _rest(tip):
        print(
            "SEAM: something other than _provider_block and the two constants changed"
        )
        return 2
    added = sorted(_name(n) for n in tip.body if _name(n) in CHANGED)
    if added != sorted(CHANGED):
        print("SEAM: the tip does not define exactly", sorted(CHANGED), "->", added)
        return 2
    for node in tip.body:
        if _name(node) in ("PROVIDER_SORT", "PROVIDER_QUANTIZATIONS"):
            print(ast.unparse(node))
        if _name(node) == "_provider_block":
            body = node.body[1:] if isinstance(node.body[0], ast.Expr) else node.body
            node.body = body
            print(ast.unparse(node))
    print("SEAM OK: only _provider_block and the two constants changed as code")
    return 0


if __name__ == "__main__":
    sys.exit(main())
