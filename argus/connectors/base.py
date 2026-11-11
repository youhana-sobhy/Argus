"""Connector interface. One implementation per vendor/target type.

A connector knows how to (1) collect the full config + state of a target and return it as
a normalized :class:`Snapshot`, and (2) push a vendor-native change payload. Everything
else (validation, diffing, approvals, AI) is vendor-neutral and lives outside connectors.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from argus.core.model import Snapshot


class ConnectorError(RuntimeError):
    pass


class Connector(ABC):
    vendor: str = "generic"
    #: True when the connector talks to a real device/controller
    live: bool = False

    @property
    @abstractmethod
    def target(self) -> str: ...

    @abstractmethod
    def fetch_snapshot(self) -> Snapshot:
        """Collect config + state and return it normalized."""

    @abstractmethod
    def fetch_native(self) -> dict[str, Any]:
        """Return the raw vendor-native export (for 'export' and troubleshooting)."""

    @abstractmethod
    def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Push a vendor-native change payload. Returns a result dict."""

    def health(self) -> dict[str, Any]:
        return {"target": self.target, "vendor": self.vendor, "live": self.live, "reachable": True}
