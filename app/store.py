"""In-memory, thread-safe stateful store backing the bot.

Holds versioned context (idempotent by scope + context_id), pending
outreach actions awaiting a reply, and suppression state so the same
message is never re-sent for a resolved trigger.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Optional


class Store:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # (scope, context_id) -> {"version": int, "payload": dict, "delivered_at": str}
        self._contexts: dict[tuple[str, str], dict[str, Any]] = {}
        # suppression_key -> stored_at epoch seconds
        self._suppressed: dict[str, float] = {}
        # merchant_id -> most recent pending action (awaiting reply)
        self._pending: dict[str, dict[str, Any]] = {}
        self._tick_count = 0

    def upsert_context(self, scope: str, context_id: str, version: int, payload: dict, delivered_at: str) -> dict:
        with self._lock:
            key = (scope, context_id)
            existing = self._contexts.get(key)
            if existing is not None and version <= existing["version"]:
                return {"accepted": True, "ack_id": f"ack_{uuid.uuid4().hex[:12]}", "stored_at": existing["delivered_at"], "no_op": True}
            self._contexts[key] = {"version": version, "payload": payload, "delivered_at": delivered_at}
            return {"accepted": True, "ack_id": f"ack_{uuid.uuid4().hex[:12]}", "stored_at": delivered_at, "no_op": False}

    def get_context(self, scope: str, context_id: str) -> Optional[dict]:
        with self._lock:
            entry = self._contexts.get((scope, context_id))
            return dict(entry) if entry else None

    def all_contexts(self, scope: str) -> dict[str, dict]:
        with self._lock:
            return {cid: dict(v) for (s, cid), v in self._contexts.items() if s == scope}

    def is_suppressed(self, suppression_key: str) -> bool:
        with self._lock:
            return suppression_key in self._suppressed

    def suppress(self, suppression_key: str) -> None:
        with self._lock:
            self._suppressed[suppression_key] = time.time()

    def set_pending(self, merchant_id: str, action: dict) -> None:
        with self._lock:
            self._pending[merchant_id] = action

    def get_pending(self, merchant_id: str) -> Optional[dict]:
        with self._lock:
            return self._pending.get(merchant_id)

    def clear_pending(self, merchant_id: str) -> None:
        with self._lock:
            self._pending.pop(merchant_id, None)

    def next_tick(self) -> int:
        with self._lock:
            self._tick_count += 1
            return self._tick_count


store = Store()
