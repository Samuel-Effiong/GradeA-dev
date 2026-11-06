"""Epic B1a step 3a: nothing may write back a stale reserved_credits
(SM ruling Q7, 2026-11-06).

(a) An AST guard over production code: every CreditWallet .save() names its
    update_fields, and no .update() sets reserved_credits outside
    billing/holds.py.
(b) Interleaving: a wallet loaded BEFORE a hold is taken, then saved by the
    existing paths, leaves the hold intact.

These protect behaviour that is already correct, so they pass at red; the
3a gate proves them with planted bugs.
"""

import ast
from pathlib import Path

from django.conf import settings
from django.test import TestCase

from billing.models import CreditWallet
from billing.testing.holds import fresh, funded_wallet, holds, make_job

ROOT = Path(settings.BASE_DIR)
SKIP_PARTS = {"tests", "migrations", "testing", "evidence", "docs", "node_modules"}
APPS = (
    "billing",
    "users",
    "students",
    "assignments",
    "ai_processor",
    "classrooms",
    "AutoGrader",
)


def production_files():
    for app in APPS:
        for path in (ROOT / app).rglob("*.py"):
            parts = set(path.relative_to(ROOT).parts)
            if parts & SKIP_PARTS:
                continue
            if path.name.startswith(("test_", "tests_")) or path.name == "tests.py":
                continue
            yield path


def receiver_text(node):
    if node is None:
        return ""
    try:
        return ast.unparse(node).lower()
    except Exception:  # noqa: BLE001
        return ""


class WalletWriteGuardTests(TestCase):
    def test_every_wallet_save_names_its_fields(self):
        offenders = []
        for path in production_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Call)
                    and getattr(node.func, "attr", None) == "save"
                ):
                    continue
                receiver = receiver_text(getattr(node.func, "value", None))
                if "wallet" not in receiver:
                    continue
                if not any(k.arg == "update_fields" for k in node.keywords):
                    offenders.append(
                        f"{path.relative_to(ROOT)}:{node.lineno} {receiver}.save()"
                    )
        self.assertEqual(offenders, [])

    def test_only_the_hold_service_updates_reserved_credits(self):
        offenders = []
        for path in production_files():
            if path.relative_to(ROOT).as_posix() == "billing/holds.py":
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and getattr(node.func, "attr", None)
                    in ("update", "save", "bulk_update")
                    and any(
                        k.arg == "reserved_credits"
                        or (
                            isinstance(k.value, ast.AST)
                            and "reserved_credits" in receiver_text(k.value)
                        )
                        for k in node.keywords
                    )
                ):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(offenders, [])


class InterleavingTests(TestCase):
    def test_a_charge_through_a_stale_wallet_keeps_the_hold(self):
        wallet = funded_wallet("g.consume@example.com", 500)
        stale = CreditWallet.objects.get(pk=wallet.pk)  # loaded before the hold
        holds().reserve(wallet, 30, make_job(wallet))
        stale.consume_credits(10)
        self.assertEqual(fresh(wallet).reserved_credits, 30)

    def test_the_consumption_block_save_keeps_the_hold(self):
        # consume_credits' own self.save(update_fields=[...]) path.
        wallet = funded_wallet("g.block@example.com", 500)
        stale = CreditWallet.objects.get(pk=wallet.pk)
        holds().reserve(wallet, 30, make_job(wallet))
        stale.is_consumption_blocked = True
        stale.save(update_fields=["is_consumption_blocked", "updated_at"])
        self.assertEqual(fresh(wallet).reserved_credits, 30)
