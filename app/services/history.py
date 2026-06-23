"""In-memory query history (last 5 entries)."""

from __future__ import annotations

import collections

from app.models.response import HistoryItem

_store: collections.deque[HistoryItem] = collections.deque(maxlen=5)


def append(item: HistoryItem) -> None:
    """Add an item; oldest entry is dropped when the deque is full."""
    _store.append(item)


def get_all() -> list[HistoryItem]:
    """Return history oldest-first."""
    return list(_store)
