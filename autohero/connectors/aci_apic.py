"""Real Cisco APIC REST connector.

Uses the documented APIC REST API:
  * ``POST /api/aaaLogin.json``                       - authenticate, returns a token cookie
  * ``GET  /api/class/<class>.json?rsp-subtree=full`` - class queries for collection
  * ``POST /api/mo/uni.json``                         - configuration changes

The rest of AutoHero is unchanged when you switch from the mock lab to this connector:
both return the same native JSON, which goes through the same normalizer.

Safety: :meth:`apply` refuses to run unless ``AUTOHERO_ALLOW_LIVE_EXECUTION=true``.
"""

from __future__ import annotations

from typing import Any

import httpx

from autohero.connectors import aci_mo
from autohero.connectors.base import Connector, ConnectorError
from autohero.core.model import Snapshot

COLLECT_CLASSES = {
    "fvTenant": "rsp-subtree=full&rsp-subtree-class=fvCtx,fvBD,fvRsCtx,fvSubnet,fvAp,fvAEPg,fvRsBd,l3extOut,l3extRsEctx",
    "fabricNode": "",
    "topSystem": "",
    "fabricLink": "",
}


class ApicConnector(Connector):
    vendor = "cisco-aci"
    live = True

    def __init__(self, url: str, username: str, password: str, verify_tls: bool = True,
                 allow_execution: bool = False, timeout: float = 30.0):
        if not url:
            raise ConnectorError("AUTOHERO_APIC_URL is not set")
        self._url = url.rstrip("/")
        self._username = username
        self._password = password
        self._allow_execution = allow_execution
        self._client = httpx.Client(base_url=self._url, verify=verify_tls, timeout=timeout)
        self._fabric_name = self._url.split("//")[-1]
        self._version = ""
        self._logged_in = False

    @property
    def target(self) -> str:
        return self._fabric_name

    def _login(self) -> None:
        body = {"aaaUser": {"attributes": {"name": self._username, "pwd": self._password}}}
        r = self._client.post("/api/aaaLogin.json", json=body)
        if r.status_code != 200:
            raise ConnectorError(f"APIC login failed: HTTP {r.status_code}")
        attrs = r.json()["imdata"][0]["aaaLogin"]["attributes"]
        self._version = attrs.get("version", "")
        self._client.cookies.set("APIC-cookie", attrs["token"])
        self._logged_in = True

    def _get(self, path: str) -> dict[str, Any]:
        if not self._logged_in:
            self._login()
        r = self._client.get(path)
        if r.status_code in (401, 403):  # token expired - log in again once
            self._login()
            r = self._client.get(path)
        if r.status_code != 200:
            raise ConnectorError(f"APIC GET {path} failed: HTTP {r.status_code}")
        return r.json()

    def fetch_native(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for cls, query in COLLECT_CLASSES.items():
            suffix = f"?{query}" if query else ""
            out[cls] = self._get(f"/api/class/{cls}.json{suffix}").get("imdata", [])
        return out

    def fetch_snapshot(self) -> Snapshot:
        native = self.fetch_native()
        return aci_mo.normalize(native, target=self.target, controller_version=self._version, source="apic")

    def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._allow_execution:
            raise ConnectorError(
                "Live execution is disabled. Set AUTOHERO_ALLOW_LIVE_EXECUTION=true to allow changes on a real APIC."
            )
        if not self._logged_in:
            self._login()
        r = self._client.post("/api/mo/uni.json", json=payload)
        if r.status_code != 200:
            raise ConnectorError(f"APIC rejected change: HTTP {r.status_code} {r.text[:300]}")
        return {"status": "ok", "target": self.target, "mode": "live", "imdata": r.json().get("imdata", [])}

    def health(self) -> dict[str, Any]:
        try:
            self._login()
            return super().health() | {"mode": "live", "controller_version": self._version}
        except Exception as exc:  # noqa: BLE001
            return super().health() | {"mode": "live", "reachable": False, "error": str(exc)}
