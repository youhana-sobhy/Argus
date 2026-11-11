"""Inventory service: caches the latest normalized snapshot of the target."""

from __future__ import annotations

import threading
import time
from typing import Any

from autohero.connectors.base import Connector
from autohero.connectors.registry import get_connector
from autohero.core.model import Snapshot


class Inventory:
    def __init__(self, connector: Connector, ttl_seconds: int = 300):
        self.connector = connector
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._snapshot: Snapshot | None = None
        self._fetched_at = 0.0

    def snapshot(self, refresh: bool = False) -> Snapshot:
        with self._lock:
            if refresh or self._snapshot is None or time.time() - self._fetched_at > self._ttl:
                self._snapshot = self.connector.fetch_snapshot()
                self._fetched_at = time.time()
            return self._snapshot

    def invalidate(self) -> None:
        with self._lock:
            self._snapshot = None

    def native(self) -> dict[str, Any]:
        return self.connector.fetch_native()


_inventory: Inventory | None = None


def get_inventory() -> Inventory:
    global _inventory
    if _inventory is None:
        _inventory = Inventory(get_connector())
    return _inventory
