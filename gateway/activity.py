"""Process-wide accounting for user-visible agent executions.

Gateway chat turns, native cron jobs, and API runs use different worker
registries for their own control semantics.  This registry deliberately tracks
only the shared lifecycle question: is this process executing agent work?
"""

from __future__ import annotations

import threading
import time
import uuid
from contextlib import contextmanager
from typing import Iterator, Optional


_lock = threading.Lock()
_active: dict[str, dict[str, object]] = {}


def begin_activity(kind: str, execution_id: Optional[str] = None) -> str:
    """Register an execution and return an opaque, idempotently releasable token."""
    token = uuid.uuid4().hex
    with _lock:
        _active[token] = {
            "kind": str(kind),
            "execution_id": execution_id,
            "started_at": time.time(),
        }
    return token


def end_activity(token: Optional[str]) -> None:
    """Release a previously registered execution token."""
    if not token:
        return
    with _lock:
        _active.pop(token, None)


def active_count() -> int:
    """Return the number of active executions across all worker types."""
    with _lock:
        return len(_active)


@contextmanager
def activity(kind: str, execution_id: Optional[str] = None) -> Iterator[str]:
    """Context manager that guarantees activity is released on every exit."""
    token = begin_activity(kind, execution_id)
    try:
        yield token
    finally:
        end_activity(token)
