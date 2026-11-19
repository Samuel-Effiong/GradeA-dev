"""The `dpt` cache scope (plan 10): bumps on every department or membership
change, and the key of the cached "my departments" payload.

The cache holds payload only. The permission decision (is this teacher live
in this school) is never answered from it. A bump that cannot be made is
logged and never fails the change: staleness is bounded by the TTL, unlike a
reap, which fails closed."""

import logging

from AutoGrader.cache_generation import (
    SCOPE_DEPARTMENT,
    SCOPE_USER,
    bump_many,
    versioned_key,
)

logger = logging.getLogger(__name__)

MY_DEPARTMENTS_TTL = 300


def bump(department_ids=(), user_ids=()):
    pairs = [(SCOPE_DEPARTMENT, d) for d in department_ids] + [
        (SCOPE_USER, u) for u in user_ids
    ]
    try:
        bump_many(pairs)
    except Exception as error:  # a stale entry ages out; the write stands
        logger.warning("department cache bump failed: %s", type(error).__name__)


def my_departments_key(user_id, department_ids):
    return versioned_key(
        f"departments:list:user={user_id}",
        [(SCOPE_USER, user_id), *[(SCOPE_DEPARTMENT, d) for d in department_ids]],
        batched=True,
    )
