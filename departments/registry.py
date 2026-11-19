"""Department content registry (plan 7.3, 8).

A later epic (lessons, library) registers each kind of content that belongs
to a department with three functions. F1 registers nothing, so a delete of an
empty or member-only department just works; the choice flow is built and
tested against a test-only kind so those epics inherit working behavior.

`count(department)` -> int, `move(department, target)` -> int moved,
`delete(department)` -> int deleted. All run inside the delete's transaction.
"""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ContentKind:
    key: str
    count: Callable
    move: Callable
    delete: Callable


_KINDS: dict[str, ContentKind] = {}


def register(kind: ContentKind) -> None:
    if kind.key in _KINDS:
        raise ValueError(f"content kind {kind.key!r} is already registered")
    _KINDS[kind.key] = kind


def unregister(key: str) -> None:
    _KINDS.pop(key, None)


def kinds() -> list[ContentKind]:
    return [_KINDS[key] for key in sorted(_KINDS)]


def counts(department) -> dict[str, int]:
    """Non-zero counts per kind."""
    found = {kind.key: kind.count(department) for kind in kinds()}
    return {key: n for key, n in found.items() if n}
