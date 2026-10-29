"""H-259 seam, by program: between the base and the tip, the eight production files
differ only in the named units, and inside the billing functions only in what they log.

Comments do not reach the syntax tree. Each file's top-level statements are keyed
(a function or class by its name, a class's methods as "Class.method", an
assignment by its targets, an import by its text, the module docstring as
"__doc__"); the keys that were added, removed or changed must be exactly the
allowed set for that file. Then:
- in every changed billing function, with every logger call blanked, every
  `except` clause's name dropped, and the H-28 alert's reason blanked, the
  function dumps the same: only what is logged changed. Its logger and alert
  calls are as many as before, and each one that differs from the base's
  carries `stripe_error_label`;
- in `scrub`, apart from its docstring, the only change is one statement added
  before the H-167 check;
- at the tip, no logger call inside an `except` that catches a Stripe error in
  billing/ (23 of them) is `logger.exception` or passes `exc_info`, or names
  the caught exception outside `stripe_error_label(...)`; eighteen name it
  inside one, and the other five do not use it at all. The alert's reason
  names its two exceptions the same way.
Usage: python -I seam_check.py <base dir> <tip dir>; exit 0 = as packed.
"""

import ast
import copy
import pathlib
import sys

IMPORT = "from AutoGrader.error_messages import "
ALLOWED = {
    "AutoGrader/error_messages.py": {"stripe_error_label"},
    "AutoGrader/log_scrubbing.py": {
        "__doc__",
        "_STRIPE_SECRET",
        "scrub",
        "_prefix_kept",
    },
    "billing/license_stripe_mutation.py": {
        IMPORT + "stripe_error_label",
        "apply_at_stripe",
    },
    "billing/license_views.py": {
        IMPORT + "describe_stripe_error, describe_user_error",
        IMPORT + "describe_stripe_error, describe_user_error, stripe_error_label",
        "LicenseSubscriptionViewSet.purchase_overage",
        "LicenseSubscriptionViewSet.setup_payment_method",
    },
    "billing/payment_method_views.py": {
        IMPORT + "describe_stripe_error",
        IMPORT + "describe_stripe_error, stripe_error_label",
        "PaymentMethodViewSet.list",
        "PaymentMethodViewSet.create",
        "PaymentMethodViewSet.portal_session",
        "PaymentMethodViewSet.destroy",
        "PaymentMethodViewSet.set_default",
    },
    "billing/receipts.py": {
        IMPORT + "stripe_error_label",
        "lookup_receipt_url",
    },
    "billing/stripe_service.py": {
        IMPORT + "describe_stripe_error",
        IMPORT + "describe_stripe_error, stripe_error_label",
        "SubscriptionReactivationService.reactivate_if_cancelling",
        "StripeSubscriptionMutationService._void_or_refund_side_effect_invoice",
        "StripeSubscriptionMutationService._revert_to_previous_price",
        "StripeSubscriptionScheduleService.release_schedule",
        "StripeWebhookHandler.handle_setup_intent_succeeded",
    },
    "billing/tasks.py": {
        IMPORT + "stripe_error_label",
        "_find_new_period_paid_invoice",
        "process_license_renewals",
        "reconcile_subscription_prices",
    },
}
LOG_LEVELS = {"debug", "info", "warning", "error", "exception", "critical"}
SITES = 23
LABELLED = 18


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


def _is_log_call(node):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in LOG_LEVELS
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "logger"
    )


def _is_alert(node):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_reconciliation_needed"
    )


def _blank_logging(fn):
    """`fn` with every logger call and alert call replaced by a constant, and
    every except clause's name dropped."""
    fn = copy.deepcopy(fn)
    for node in ast.walk(fn):
        if isinstance(node, ast.ExceptHandler):
            node.name = None
        for field, value in ast.iter_fields(node):
            if isinstance(value, list):
                for i, item in enumerate(value):
                    if _is_log_call(item) or _is_alert(item):
                        value[i] = ast.Constant(None)
            elif _is_log_call(value) or _is_alert(value):
                setattr(node, field, ast.Constant(None))
    return fn


def _calls(fn):
    return [n for n in ast.walk(fn) if _is_log_call(n) or _is_alert(n)]


def _changed_calls_without_label(base_fn, tip_fn):
    """The tip's logging calls that differ from the base's and do not carry
    stripe_error_label; None when their number changed."""
    base_calls, tip_calls = _calls(base_fn), _calls(tip_fn)
    if len(base_calls) != len(tip_calls):
        return None
    return [
        tip.lineno
        for base, tip in zip(base_calls, tip_calls, strict=True)
        if ast.dump(base) != ast.dump(tip)
        and "stripe_error_label" not in ast.unparse(tip)
    ]


def _names_outside_label(node, name):
    """Uses of `name` in `node` that are not inside stripe_error_label(...)."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "stripe_error_label"
    ):
        return 0
    found = int(isinstance(node, ast.Name) and node.id == name)
    return found + sum(
        _names_outside_label(c, name) for c in ast.iter_child_nodes(node)
    )


def _catches_stripe(handler):
    text = ast.unparse(handler.type) if handler.type is not None else ""
    return "stripe" in text.lower()


def _sites(tip_dir):
    """Every logger call in a Stripe except clause in billing/, and what is wrong
    with each."""
    sites, labelled, problems = [], [], []
    for path in sorted((tip_dir / "billing").rglob("*.py")):
        if "tests" in path.parts or "migrations" in path.parts:
            continue
        rel = path.relative_to(tip_dir)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for handler in ast.walk(tree):
            if not isinstance(handler, ast.ExceptHandler) or not _catches_stripe(
                handler
            ):
                continue
            for call in ast.walk(handler):
                if not _is_log_call(call):
                    continue
                where = f"{rel}:{call.lineno}"
                sites.append(where)
                if call.func.attr == "exception":
                    problems.append(f"{where} is logger.exception")
                if any(k.arg == "exc_info" for k in call.keywords):
                    problems.append(f"{where} passes exc_info")
                if handler.name is not None:
                    if _names_outside_label(call, handler.name):
                        problems.append(
                            f"{where} names {handler.name} outside stripe_error_label"
                        )
                    if f"stripe_error_label({handler.name})" in ast.unparse(call):
                        labelled.append(where)
    return sites, labelled, problems


def main():
    base_dir, tip_dir = map(pathlib.Path, sys.argv[1:3])
    ok = True
    trees = {}
    for path, allowed in ALLOWED.items():
        base_tree = ast.parse((base_dir / path).read_text(encoding="utf-8"))
        tip_tree = ast.parse((tip_dir / path).read_text(encoding="utf-8"))
        base, tip = _units(base_tree), _units(tip_tree)
        trees[path] = (base, tip)
        changed = {
            k
            for k in set(base) | set(tip)
            if k not in base or k not in tip or _dump(base[k]) != _dump(tip[k])
        }
        if changed != allowed:
            print(f"SEAM: {path} changed {sorted(changed)}; allowed {sorted(allowed)}")
            ok = False
            continue
        print(f"{path}: {sorted(changed)}")
        if not path.startswith("billing/"):
            continue
        for name in sorted(k for k in changed if not k.startswith(IMPORT)):
            blank = [ast.dump(_blank_logging(u[name])) for u in (base, tip)]
            if blank[0] != blank[1]:
                print(f"SEAM: {path} {name} changed beyond what it logs")
                ok = False
            unlabelled = _changed_calls_without_label(base[name], tip[name])
            if unlabelled is None:
                print(f"SEAM: {path} {name} has a different number of logging calls")
                ok = False
            elif unlabelled:
                print(
                    f"SEAM: {path} {name} changed logging calls without the label: {unlabelled}"
                )
                ok = False
    if not ok:
        return 2
    base, tip = trees["AutoGrader/log_scrubbing.py"]
    tip_scrub, base_scrub = copy.deepcopy(tip["scrub"]), copy.deepcopy(base["scrub"])
    tip_scrub.body = tip_scrub.body[2:]
    base_scrub.body = base_scrub.body[1:]
    if ast.dump(tip_scrub) != ast.dump(base_scrub):
        print(
            "SEAM: scrub changed beyond the one statement added before the H-167 check"
        )
        ok = False
    print("scrub adds:", ast.unparse(tip["scrub"].body[1]).replace("\n", " "))
    pattern = tip["_STRIPE_SECRET"].value.args[0].value
    print("pattern:", pattern)
    sites, labelled, problems = _sites(tip_dir)
    if len(sites) != SITES or len(labelled) != LABELLED:
        print(
            f"SEAM: {len(sites)} log calls in Stripe except clauses ({len(labelled)} "
            f"labelled), not {SITES} ({LABELLED})"
        )
        ok = False
    for problem in problems:
        print("SEAM:", problem)
        ok = False
    lsm = trees["billing/license_stripe_mutation.py"][1]["apply_at_stripe"]
    [alert] = [
        n
        for n in ast.walk(lsm)
        if _is_alert(n) and "read-back also failed" in ast.unparse(n)
    ]
    for name in ("exc", "read_exc"):
        if _names_outside_label(alert, name):
            print(f"SEAM: the alert names {name} outside stripe_error_label")
            ok = False
    if not ok:
        return 2
    print(f"{len(sites)} log calls in Stripe except clauses; labelled: {len(labelled)}")
    for where in labelled:
        print("  ", where)
    print("SEAM OK: only the named units changed, and only what they log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
