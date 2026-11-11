"""Vendor-neutral normalized network model.

Every connector (Cisco ACI today; NX-OS, FortiGate, Juniper, Palo Alto, VMware later)
translates its native data into these objects. The UI, the AI assistant and the change
engine only ever work on this model, never on vendor payloads directly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class FabricNode(BaseModel):
    id: str
    name: str
    role: Literal["spine", "leaf", "controller", "border-leaf"]
    model: str = ""
    serial: str = ""
    version: str = ""
    oob_ip: str = ""
    pod: str = "1"
    status: Literal["up", "down", "degraded"] = "up"


class FabricLink(BaseModel):
    a: str  # node id
    b: str  # node id
    a_port: str = ""
    b_port: str = ""
    speed: str = ""
    status: Literal["up", "down"] = "up"


class Subnet(BaseModel):
    gateway: str  # e.g. 10.10.1.1/24
    scope: str = "private"


class VRF(BaseModel):
    name: str
    tenant: str
    policy_enforcement: str = "enforced"
    descr: str = ""


class BridgeDomain(BaseModel):
    name: str
    tenant: str
    vrf: str = ""
    subnets: list[Subnet] = Field(default_factory=list)
    unicast_route: bool = True
    arp_flood: bool = False
    l2_unknown_unicast: str = "proxy"
    descr: str = ""


class EPG(BaseModel):
    name: str
    tenant: str
    app_profile: str
    bridge_domain: str = ""
    descr: str = ""


class AppProfile(BaseModel):
    name: str
    tenant: str
    descr: str = ""
    epgs: list[EPG] = Field(default_factory=list)


class L3Out(BaseModel):
    name: str
    tenant: str
    vrf: str = ""
    descr: str = ""


class Tenant(BaseModel):
    name: str
    descr: str = ""
    vrfs: list[VRF] = Field(default_factory=list)
    bridge_domains: list[BridgeDomain] = Field(default_factory=list)
    app_profiles: list[AppProfile] = Field(default_factory=list)
    l3outs: list[L3Out] = Field(default_factory=list)

    def vrf(self, name: str) -> VRF | None:
        return next((v for v in self.vrfs if v.name == name), None)

    def bd(self, name: str) -> BridgeDomain | None:
        return next((b for b in self.bridge_domains if b.name == name), None)

    def ap(self, name: str) -> AppProfile | None:
        return next((a for a in self.app_profiles if a.name == name), None)

    def epgs(self) -> list[EPG]:
        return [e for ap in self.app_profiles for e in ap.epgs]


class Snapshot(BaseModel):
    """A point-in-time, normalized view of one target (e.g. one ACI fabric)."""

    target: str
    vendor: str = "cisco-aci"
    controller_version: str = ""
    source: str = "mock"
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    nodes: list[FabricNode] = Field(default_factory=list)
    links: list[FabricLink] = Field(default_factory=list)
    tenants: list[Tenant] = Field(default_factory=list)

    def tenant(self, name: str) -> Tenant | None:
        return next((t for t in self.tenants if t.name == name), None)

    def stats(self) -> dict[str, int]:
        return {
            "spines": sum(1 for n in self.nodes if n.role == "spine"),
            "leaves": sum(1 for n in self.nodes if n.role in ("leaf", "border-leaf")),
            "controllers": sum(1 for n in self.nodes if n.role == "controller"),
            "links": len(self.links),
            "tenants": len(self.tenants),
            "vrfs": sum(len(t.vrfs) for t in self.tenants),
            "bridge_domains": sum(len(t.bridge_domains) for t in self.tenants),
            "app_profiles": sum(len(t.app_profiles) for t in self.tenants),
            "epgs": sum(len(t.epgs()) for t in self.tenants),
            "l3outs": sum(len(t.l3outs) for t in self.tenants),
        }
