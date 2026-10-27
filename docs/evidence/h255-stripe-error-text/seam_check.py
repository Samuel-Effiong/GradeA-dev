"""H-255 seam, by program: between the base and the tip, the four production files
differ only in the named units, and inside most of those only in what is raised.

Comments do not reach the syntax tree. Each file's top-level statements are keyed
(a function or class by its name, a class's methods as "Class.method", an
assignment by its targets, an import by its text); the keys that were added,
removed or changed must be exactly the allowed set for that file. Then:
- in `stripe_overage_unit_price`, `new_customer_test_clock_kwargs`,
  `StripeSubscriptionScheduleService.schedule_plan_change_on_stripe` and
  `StripePriceService.create_custom_price`, the function with every `raise`
  statement's exception (and every comment) removed dumps the same: only what
  is raised changed;
- in `overage_price_drift`, the only change is `str(exc)` -> `exc.detail`;
- `describe_stripe_error` holds one `logger.warning` with no `exc_info`, whose
  arguments are the format, `type(error).__name__` and `error.request_id` only,
  and its one early `return` sits under an isinstance check on CardError.
Usage: python -I seam_check.py <base dir> <tip dir>; exit 0 = as packed.
"""

import ast
import copy
import pathlib
import sys

ALLOWED = {
    "AutoGrader/error_messages.py": {
        "import logging",
        "logger",
        "describe_stripe_error",
    },
    "billing/overage_pricing.py": {
        "OVERAGE_PRICE_UNAVAILABLE_MESSAGE",
        "OveragePriceUnavailable",
        "OveragePriceUnavailable.__init__",
        "OVERAGE_PRICE_CHANGED_MESSAGE",
        "OveragePriceMismatch",
        "OveragePriceMismatch.__init__",
        "stripe_overage_unit_price",
        "overage_price_drift",
    },
    "billing/qa_time_travel.py": {
        "from AutoGrader.error_messages import describe_stripe_error",
        "new_customer_test_clock_kwargs",
    },
    "billing/stripe_service.py": {
        "StripeSubscriptionScheduleService.schedule_plan_change_on_stripe",
        "StripePriceService.create_custom_price",
    },
}
RAISES_ONLY = {
    "billing/overage_pricing.py": ["stripe_overage_unit_price"],
    "billing/qa_time_travel.py": ["new_customer_test_clock_kwargs"],
    "billing/stripe_service.py": [
        "StripeSubscriptionScheduleService.schedule_plan_change_on_stripe",
        "StripePriceService.create_custom_price",
    ],
}


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


def _blank_raises(fn):
    """A copy of `fn` with every raise statement's exception and cause removed."""
    fn = copy.deepcopy(fn)
    for node in ast.walk(fn):
        if isinstance(node, ast.Raise):
            node.exc = None
            node.cause = None
    return fn


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
        for name in RAISES_ONLY.get(path, []):
            blank = [ast.dump(_blank_raises(u[name])) for u in (base, tip)]
            if blank[0] != blank[1]:
                print(f"SEAM: {path} {name} changed beyond what it raises")
                ok = False
        print(f"{path}: {sorted(changed)}")
    drift = [
        _units(
            ast.parse((d / "billing/overage_pricing.py").read_text(encoding="utf-8"))
        )["overage_price_drift"]
        for d in (base_dir, tip_dir)
    ]
    if ast.unparse(drift[0]).replace("str(exc)", "exc.detail") != ast.unparse(drift[1]):
        print("SEAM: overage_price_drift changed beyond str(exc) -> exc.detail")
        ok = False
    fn = _units(
        ast.parse(
            (tip_dir / "AutoGrader/error_messages.py").read_text(encoding="utf-8")
        )
    )["describe_stripe_error"]
    logs = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "logger"
    ]
    want = [
        "'Stripe call failed: %s (request %s)'",
        "type(error).__name__",
        "error.request_id",
    ]
    if (
        len(logs) != 1
        or logs[0].func.attr != "warning"
        or logs[0].keywords
        or [ast.unparse(a) for a in logs[0].args] != want
    ):
        print(
            "SEAM: describe_stripe_error's log is not the one WARNING, class and request id only"
        )
        ok = False
    early = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.If) and any(isinstance(s, ast.Return) for s in n.body)
    ]
    if len(early) != 1 or "stripe.error.CardError" not in ast.unparse(early[0].test):
        print(
            "SEAM: describe_stripe_error's pass-through is not under a CardError check"
        )
        ok = False
    if not ok:
        return 2
    print("log:", ast.unparse(logs[0]))
    print("pass-through:", ast.unparse(early[0].test))
    print("SEAM OK: only the named units changed, and only as packed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
