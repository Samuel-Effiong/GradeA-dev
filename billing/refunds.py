"""
Refund accumulation for multi-call AI pipelines.

Some AI features (grading, in particular) make several billed calls to
execute_graded_task in the course of one logical operation. If the
operation ultimately fails, every credit charge made along the way should
be refunded — but the pipeline itself must NOT run inside one long-lived DB
transaction to get that behaviour, because that holds the CreditWallet row
locked (via CreditWallet.consume_credits' select_for_update()) across every
network call in the run. See ai_processor/services.py::grade_student_submission
for the pipeline this was built for.

billing_refund_scope() replaces that transaction: each execute_graded_task
call commits its charge independently and registers its billing task_id
with the innermost open scope. If the scope's block raises, every
registered task_id is refunded via SubscriptionService.refund_credits.
"""

import contextlib
import contextvars
import logging

logger = logging.getLogger(__name__)

# Holds the list of billing task_ids spent inside the innermost active
# scope. A ContextVar (not an attribute on the module-level `ai_processor`
# singleton in ai_processor/services.py) because that singleton is
# instantiated once per process and shared by every thread — instance state
# would leak across unrelated requests/tasks. ContextVars are per-thread AND
# per-async-context, and a child context gets a *copy* rather than sharing
# the parent's, so nothing leaks sideways either.
_active_task_ids: contextvars.ContextVar = contextvars.ContextVar(
    "billing_refund_task_ids", default=None
)


def record_billing_task_id(task_id):
    """
    Register a already-committed charge with the innermost active refund
    scope. A no-op when no scope is open, so every existing caller of
    execute_graded_task keeps today's charge-and-keep behaviour untouched.
    """
    ids = _active_task_ids.get()
    if ids is not None and task_id:
        ids.append(task_id)


@contextlib.contextmanager
def billing_refund_scope(*, reason="task failed"):
    """
    Refund every credit charge registered inside this block if the block
    raises.

    This is the direct replacement for wrapping a multi-call AI pipeline in
    @transaction.atomic: that pattern refunded a failed run implicitly, by
    rolling back, at the cost of holding the teacher's wallet row locked
    across every AI call in the run. This restores the same user-visible
    outcome (no charge for a failed run) without holding any lock or
    transaction open across network I/O.

    Nesting: if this scope is opened inside another already-open scope, a
    successful exit hands its ids up to the outer scope, so an outer
    failure can still reclaim them.
    """
    outer = _active_task_ids.get()
    ids = []
    token = _active_task_ids.set(ids)
    try:
        yield ids
    except BaseException:
        _refund_all(ids, reason=reason)
        raise
    else:
        if outer is not None:
            outer.extend(ids)
    finally:
        _active_task_ids.reset(token)


def _refund_all(task_ids, *, reason):
    """Refund a batch of billing task_ids. Never masks the exception that
    triggered the refund — a failure here is logged, not raised, since the
    caller is already unwinding from the real error."""
    if not task_ids:
        return

    # Lazy import: billing.services imports from billing.models, and this
    # module needs to stay importable from ai_processor without pulling in
    # the rest of the billing service layer at import time.
    from billing.services import SubscriptionService

    for task_id in task_ids:
        try:
            SubscriptionService.refund_credits(task_id, reason=reason)
        except Exception:
            logger.exception(
                "Failed to refund credits for billing task %s (%s). "
                "Credits remain consumed — manual reconciliation required.",
                task_id,
                reason,
            )
