"""Short-lived server-side state, because callback_data is capped at 64 bytes."""

from __future__ import annotations

import secrets
import time
from typing import Any

_TTL = 60 * 60 * 3  # 3 hours
_store: dict[str, dict[str, Any]] = {}


def put(data: dict[str, Any]) -> str:
    _gc()
    token = secrets.token_urlsafe(6)
    _store[token] = {"_at": time.time(), **data}
    return token


def get(token: str) -> dict[str, Any] | None:
    item = _store.get(token)
    if item is None:
        return None
    if time.time() - item["_at"] > _TTL:
        _store.pop(token, None)
        return None
    return item


def update(token: str, **kwargs: Any) -> None:
    item = _store.get(token)
    if item is not None:
        item.update(kwargs)


def drop(token: str) -> None:
    _store.pop(token, None)


def _gc() -> None:
    now = time.time()
    for key in [k for k, v in _store.items() if now - v["_at"] > _TTL]:
        _store.pop(key, None)
