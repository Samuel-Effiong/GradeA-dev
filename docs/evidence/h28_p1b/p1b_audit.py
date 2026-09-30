#!/usr/bin/env python3
"""H-28 (P1b) audit: irreversible Stripe mutations executed inside a
transaction.atomic and/or while holding a select_for_update.

Two passes, because a transaction can wrap a Stripe call ACROSS function
boundaries (billing/views.py:726 opens atomic, then calls
release_schedule() defined in billing/stripe_service.py, which is where
the irreversible stripe.SubscriptionSchedule.release actually happens).

  Pass 1 (lexical): for every Stripe MUTATION call, record the enclosing
          function and whether an atomic block / @atomic decorator /
          select_for_update lexically encloses it.
  Pass 2 (transitive): build a name-based call graph, propagate "reaches a
          Stripe mutation" up through callers, then report every atomic
          block that calls (directly or transitively) into one.

Name-based resolution is deliberately OVER-inclusive: it is a funnel that
narrows ~30k lines to a reviewable set. EVERY hit is then confirmed by
hand against the source. Nothing here goes into evidence unconfirmed.
"""

import ast
import json
import pathlib
import sys
from collections import defaultdict

# Stripe calls that CHANGE remote state and that no DB rollback can undo.
# Deliberately excludes retrieve/list/construct_event (read-only).
MUTATING = {
    ("Subscription", "modify"),
    ("Subscription", "delete"),
    ("SubscriptionSchedule", "release"),
    ("SubscriptionSchedule", "modify"),
    ("SubscriptionSchedule", "create"),
    ("Refund", "create"),
    ("Invoice", "void_invoice"),
    ("Invoice", "pay"),
    ("Invoice", "finalize_invoice"),
    ("Customer", "modify"),
    ("Customer", "create"),
    ("Customer", "delete"),
    ("SetupIntent", "create"),
    ("PaymentMethod", "detach"),
    ("PaymentMethod", "attach"),
    ("PaymentIntent", "cancel"),
    ("PaymentIntent", "capture"),
    ("Price", "create"),
    ("Price", "modify"),
    ("Product", "create"),
    ("Product", "modify"),
}


def is_atomic_call(node):
    """with transaction.atomic(...) / with atomic(...)"""
    f = node.func if isinstance(node, ast.Call) else node
    if isinstance(f, ast.Attribute) and f.attr == "atomic":
        return True
    if isinstance(f, ast.Name) and f.id == "atomic":
        return True
    return False


def is_atomic_decorator(dec):
    if isinstance(dec, ast.Call):
        dec = dec.func
    if isinstance(dec, ast.Attribute) and dec.attr == "atomic":
        return True
    if isinstance(dec, ast.Name) and dec.id == "atomic":
        return True
    return False


def call_name(node):
    """stripe.Subscription.modify(...) -> ('Subscription', 'modify')"""
    f = node.func
    if not isinstance(f, ast.Attribute):
        return None
    method = f.attr
    owner = f.value
    if not isinstance(owner, ast.Attribute):
        return None
    obj = owner.attr
    root = owner.value
    if isinstance(root, ast.Name) and root.id == "stripe":
        return (obj, method)
    return None


def simple_callee(node):
    """Best-effort callee name for the crude call graph."""
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


class Walker(ast.NodeVisitor):
    def __init__(self, path):
        self.path = path
        self.stack = []  # (funcname, atomic_depth_at_entry)
        self.atomic_stack = []  # line numbers of open atomic blocks
        self.sfu_stack = []  # select_for_update seen inside current atomic
        self.mutations = []  # records
        self.func_calls = defaultdict(set)  # func -> callees
        self.func_mutates = set()  # funcs with a DIRECT mutation
        self.func_def_line = {}
        self.atomic_blocks = []  # (line, func, callees_inside, sfu)

    # -- functions -------------------------------------------------------
    def visit_FunctionDef(self, node):  # noqa: B906 - _func() calls generic_visit
        self._func(node)

    def visit_AsyncFunctionDef(self, node):  # noqa: B906 - _func() calls generic_visit
        self._func(node)

    def _func(self, node):
        deco_atomic = any(is_atomic_decorator(d) for d in node.decorator_list)
        self.stack.append(node.name)
        self.func_def_line[node.name] = node.lineno
        if deco_atomic:
            self.atomic_stack.append(("decorator", node.lineno))
        self.generic_visit(node)
        if deco_atomic:
            self.atomic_stack.pop()
        self.stack.pop()

    # -- with blocks -----------------------------------------------------
    def visit_With(self, node):  # noqa: B906 - _with() calls generic_visit
        self._with(node)

    def visit_AsyncWith(self, node):  # noqa: B906 - _with() calls generic_visit
        self._with(node)

    def _with(self, node):
        opened = False
        for item in node.items:
            if isinstance(item.context_expr, ast.Call) and is_atomic_call(
                item.context_expr
            ):
                opened = True
        if opened:
            self.atomic_stack.append(("with", node.lineno))
            inner_calls = set()
            sfu = False
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    c = simple_callee(sub)
                    if c:
                        inner_calls.add(c)
                    if c == "select_for_update":
                        sfu = True
            self.atomic_blocks.append(
                {
                    "file": self.path,
                    "line": node.lineno,
                    "func": self.stack[-1] if self.stack else "<module>",
                    "callees": sorted(inner_calls),
                    "select_for_update": sfu,
                }
            )
        self.generic_visit(node)
        if opened:
            self.atomic_stack.pop()

    # -- calls -----------------------------------------------------------
    def visit_Call(self, node):
        cur = self.stack[-1] if self.stack else "<module>"
        c = simple_callee(node)
        if c:
            self.func_calls[cur].add(c)
        sn = call_name(node)
        if sn and sn in MUTATING:
            self.func_mutates.add(cur)
            self.mutations.append(
                {
                    "file": self.path,
                    "line": node.lineno,
                    "call": f"stripe.{sn[0]}.{sn[1]}",
                    "func": cur,
                    "lexically_atomic": bool(self.atomic_stack),
                    "atomic_opened_at": (
                        self.atomic_stack[-1][1] if self.atomic_stack else None
                    ),
                }
            )
        self.generic_visit(node)


def main(root):
    root = pathlib.Path(root)
    files = [
        p
        for p in root.glob("billing/**/*.py")
        if "/tests/" not in str(p)
        and not p.name.startswith("test_")
        and "/migrations/" not in str(p)
        and "live_qa" not in str(p)
        and not p.name.startswith("qa_")
    ]

    all_mut, all_blocks = [], []
    calls = defaultdict(set)
    mutating_funcs = set()
    def_file = {}

    for p in files:
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError as e:
            print(f"SKIP {p}: {e}", file=sys.stderr)
            continue
        rel = str(p.relative_to(root))
        w = Walker(rel)
        w.visit(tree)
        all_mut.extend(w.mutations)
        all_blocks.extend(w.atomic_blocks)
        for k, v in w.func_calls.items():
            calls[k] |= v
        mutating_funcs |= w.func_mutates
        for fn, ln in w.func_def_line.items():
            def_file.setdefault(fn, f"{rel}:{ln}")

    # transitive closure: which functions can REACH a stripe mutation
    reaches = set(mutating_funcs)
    changed = True
    while changed:
        changed = False
        for fn, callees in calls.items():
            if fn in reaches:
                continue
            if callees & reaches:
                reaches.add(fn)
                changed = True

    # atomic blocks that reach a mutation, directly or transitively
    risky = []
    for b in all_blocks:
        hits = sorted(set(b["callees"]) & reaches)
        if hits:
            b = dict(b)
            b["reaching_callees"] = hits
            b["callee_defs"] = {h: def_file.get(h, "?") for h in hits}
            risky.append(b)

    print(
        json.dumps(
            {
                "mutations": all_mut,
                "atomic_blocks_total": len(all_blocks),
                "risky_atomic_blocks": risky,
                "mutating_funcs": sorted(mutating_funcs),
                "reaching_funcs": sorted(reaches),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
