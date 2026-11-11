"""Mock Cisco APIC: an in-memory ACI fabric that behaves like the APIC REST API.

State is seeded from ``data/mock/aci_fabric.json`` (native APIC JSON) and persisted to the
runtime directory, so changes made in the demo survive restarts until the lab is reset.
"""

from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Any

from autohero.config import PACKAGE_DIR
from autohero.connectors import aci_mo
from autohero.connectors.base import Connector, ConnectorError
from autohero.core.model import Snapshot

SEED_FILE = PACKAGE_DIR / "data" / "mock" / "aci_fabric.json"


class MockAciConnector(Connector):
    vendor = "cisco-aci"
    live = False

    def __init__(self, state_file: Path, seed_file: Path = SEED_FILE):
        self._state_file = state_file
        self._seed_file = seed_file
        self._lock = threading.RLock()
        self._doc: dict[str, Any] = {}
        self._load()

    # -- persistence -------------------------------------------------------
    def _load(self) -> None:
        src = self._state_file if self._state_file.is_file() else self._seed_file
        self._doc = json.loads(src.read_text(encoding="utf-8"))

    def _save(self) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._doc, indent=1), encoding="utf-8")
        tmp.replace(self._state_file)

    def reset(self) -> None:
        with self._lock:
            self._doc = json.loads(self._seed_file.read_text(encoding="utf-8"))
            self._save()

    # -- Connector API -----------------------------------------------------
    @property
    def target(self) -> str:
        return self._doc.get("fabric", "mock-aci")

    def fetch_native(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._doc["imdata"])

    def fetch_snapshot(self) -> Snapshot:
        with self._lock:
            return aci_mo.normalize(
                self._doc["imdata"], target=self.target,
                controller_version=self._doc.get("controllerVersion", ""), source="mock",
            )

    def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            tenants = self._doc["imdata"]["fvTenant"]
            before = copy.deepcopy(tenants)
            try:
                aci_mo.apply_payload(tenants, payload)
            except Exception as exc:  # roll the in-memory tree back on any error
                self._doc["imdata"]["fvTenant"] = before
                raise ConnectorError(f"Mock APIC rejected payload: {exc}") from exc
            self._save()
        return {"status": "ok", "target": self.target, "mode": "mock", "imdata": []}

    def health(self) -> dict[str, Any]:
        return super().health() | {"mode": "mock-lab", "controller_version": self._doc.get("controllerVersion", "")}
