"""Planner / dry-run engine.

For an intent it produces, without touching the target:
  * vendor payload (APIC JSON) and the matching rollback payload
  * pre-checks (pass / warn / fail) with plain-language explanations
  * a simulated "after" state, built by applying the payload to a copy of the live config
  * a unified diff of the normalized tenant (before vs after)
  * a risk rating and blast-radius summary
"""

from __future__ import annotations

import copy
import difflib
import ipaddress
import json
import re
from datetime import datetime, timezone
from typing import Any

from autohero.changes.intents import CreateAppProfile, CreateBridgeDomain, DeleteObject
from autohero.connectors import aci_mo
from autohero.core.model import Snapshot, Tenant

NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
PROTECTED_TENANTS = {"common", "infra", "mgmt"}
SUFFIX = {"app_profile": "_AP", "bridge_domain": "_BD", "epg": "_EPG"}


class Check:
    def __init__(self) -> None:
        self.items: list[dict[str, str]] = []

    def add(self, cid: str, name: str, status: str, detail: str) -> None:
        self.items.append({"id": cid, "name": name, "status": status, "detail": detail})

    def ok(self, cid: str, name: str, detail: str) -> None:
        self.add(cid, name, "pass", detail)

    def warn(self, cid: str, name: str, detail: str) -> None:
        self.add(cid, name, "warn", detail)

    def fail(self, cid: str, name: str, detail: str) -> None:
        self.add(cid, name, "fail", detail)

    @property
    def failed(self) -> bool:
        return any(i["status"] == "fail" for i in self.items)

    @property
    def warnings(self) -> int:
        return sum(1 for i in self.items if i["status"] == "warn")


def _tenant_dump(t: Tenant | None) -> str:
    if t is None:
        return ""
    return json.dumps(t.model_dump(mode="json"), indent=2, sort_keys=False)


def _name_checks(chk: Check, object_type: str, name: str) -> None:
    if NAME_RE.match(name):
        chk.ok("name.syntax", "Name is valid for ACI", f"'{name}' uses only allowed characters (A-Z, 0-9, _ . : -) and is at most 64 long.")
    else:
        chk.fail("name.syntax", "Name is valid for ACI", f"'{name}' contains characters ACI does not allow, or is longer than 64.")
    suffix = SUFFIX[object_type]
    if name.endswith(suffix):
        chk.ok("name.convention", "Naming convention", f"Ends with '{suffix}' as per the team convention.")
    else:
        chk.warn("name.convention", "Naming convention", f"Team convention is to end {object_type.replace('_', ' ')} names with '{suffix}'.")


def _all_subnets_in_vrf(snapshot: Snapshot, tenant: str, vrf: str) -> list[tuple[str, str]]:
    out = []
    for t in snapshot.tenants:
        for bd in t.bridge_domains:
            if bd.vrf == vrf and (t.name == tenant or vrf == "default"):
                out += [(f"{t.name}/{bd.name}", s.gateway) for s in bd.subnets]
    return out


def _plan_create_bd(intent: CreateBridgeDomain, snap: Snapshot, chk: Check) -> tuple[dict, dict, list[str]]:
    t = snap.tenant(intent.tenant)
    _name_checks(chk, "bridge_domain", intent.name)
    if t and t.bd(intent.name):
        chk.fail("object.unique", "Bridge Domain does not exist yet", f"{intent.name} already exists in {intent.tenant}.")
    elif t:
        chk.ok("object.unique", "Bridge Domain does not exist yet", f"No Bridge Domain called {intent.name} in {intent.tenant}.")
    common = snap.tenant("common")
    if t and (t.vrf(intent.vrf) or (common and common.vrf(intent.vrf))):
        chk.ok("vrf.exists", "VRF exists", f"VRF {intent.vrf} found.")
    elif t:
        chk.fail("vrf.exists", "VRF exists", f"VRF {intent.vrf} not found in {intent.tenant} or common.")
    if not intent.subnets:
        chk.warn("subnet.present", "Gateway subnet", "No gateway subnet given. Hosts in this BD will have no default gateway in the fabric.")
    existing = _all_subnets_in_vrf(snap, intent.tenant, intent.vrf)
    for s in intent.subnets:
        try:
            iface = ipaddress.ip_interface(s)
        except ValueError:
            chk.fail("subnet.syntax", f"Subnet {s}", "Not a valid gateway address. Use the form 10.20.30.1/24.")
            continue
        net = iface.network
        if iface.ip == net.network_address or (net.version == 4 and net.prefixlen < 31 and iface.ip == net.broadcast_address):
            chk.fail("subnet.syntax", f"Subnet {s}", "The gateway must be a host address, not the network or broadcast address.")
            continue
        chk.ok("subnet.syntax", f"Subnet {s}", f"Gateway {iface.ip} in network {net}.")
        clash = [(owner, g) for owner, g in existing if ipaddress.ip_interface(g).network.overlaps(net)]
        if clash:
            chk.fail("subnet.overlap", f"No overlap for {s}", "Overlaps with " + ", ".join(f"{g} ({o})" for o, g in clash) + f" in VRF {intent.vrf}.")
        else:
            chk.ok("subnet.overlap", f"No overlap for {s}", f"No other Bridge Domain in VRF {intent.vrf} uses this range.")
    if intent.l2_unknown_unicast == "flood" and not intent.arp_flood:
        chk.warn("bd.flooding", "Flooding settings", "L2 unknown unicast is 'flood' but ARP flooding is off. This mix is unusual.")
    kids = [aci_mo.mo("fvRsCtx", {"tnFvCtxName": intent.vrf})]
    kids += [aci_mo.mo("fvSubnet", {"ip": s, "scope": "private"}) for s in intent.subnets]
    bd = aci_mo.mo("fvBD", {
        "name": intent.name, "descr": intent.descr, "status": "created",
        "unicastRoute": "yes" if intent.unicast_route else "no",
        "arpFlood": "yes" if intent.arp_flood else "no",
        "unkMacUcastAct": intent.l2_unknown_unicast,
    }, kids)
    rb = aci_mo.mo("fvBD", {"name": intent.name, "status": "deleted"})
    return bd, rb, [f"+ Bridge Domain {intent.name}"] + [f"+ Subnet {s}" for s in intent.subnets]


def _plan_create_ap(intent: CreateAppProfile, snap: Snapshot, chk: Check) -> tuple[dict, dict, list[str]]:
    t = snap.tenant(intent.tenant)
    _name_checks(chk, "app_profile", intent.name)
    if t and t.ap(intent.name):
        chk.fail("object.unique", "Application Profile does not exist yet", f"{intent.name} already exists in {intent.tenant}.")
    elif t:
        chk.ok("object.unique", "Application Profile does not exist yet", f"No Application Profile called {intent.name} in {intent.tenant}.")
    names = [e.name for e in intent.epgs]
    if len(names) != len(set(names)):
        chk.fail("epg.unique", "EPG names are unique", "The same EPG name is used twice.")
    for e in intent.epgs:
        if not NAME_RE.match(e.name):
            chk.fail("epg.syntax", f"EPG {e.name}", "EPG name contains characters ACI does not allow.")
        elif not e.name.endswith("_EPG"):
            chk.warn("epg.convention", f"EPG {e.name}", "Team convention is to end EPG names with '_EPG'.")
        if t and t.bd(e.bridge_domain):
            chk.ok("epg.bd", f"EPG {e.name} → BD", f"Bridge Domain {e.bridge_domain} exists.")
        elif t:
            chk.fail("epg.bd", f"EPG {e.name} → BD", f"Bridge Domain {e.bridge_domain} not found in {intent.tenant}.")
    if not intent.epgs:
        chk.warn("ap.empty", "Application Profile has EPGs", "No EPGs defined. The profile will be created empty.")
    epgs = [aci_mo.mo("fvAEPg", {"name": e.name, "descr": e.descr}, [aci_mo.mo("fvRsBd", {"tnFvBDName": e.bridge_domain})]) for e in intent.epgs]
    ap = aci_mo.mo("fvAp", {"name": intent.name, "descr": intent.descr, "status": "created"}, epgs)
    rb = aci_mo.mo("fvAp", {"name": intent.name, "status": "deleted"})
    return ap, rb, [f"+ Application Profile {intent.name}"] + [f"+ EPG {e.name} (BD {e.bridge_domain})" for e in intent.epgs]


def _plan_delete(intent: DeleteObject, snap: Snapshot, native_tenants: list[dict], chk: Check) -> tuple[dict, dict | None, list[str], int]:
    t = snap.tenant(intent.tenant)
    impact: list[str] = []
    blast = 0
    if intent.tenant in PROTECTED_TENANTS:
        chk.fail("tenant.protected", "Tenant is not protected", f"Deleting from the system tenant '{intent.tenant}' is blocked by policy.")
    else:
        chk.ok("tenant.protected", "Tenant is not protected", f"{intent.tenant} is not a system tenant.")
    tn_path = [("fvTenant", f"tn-{intent.tenant}")]
    if intent.object_type == "bridge_domain":
        path = tn_path + [("fvBD", f"BD-{intent.name}")]
        bd = t.bd(intent.name) if t else None
        if bd is None and t:
            chk.fail("object.exists", "Object exists", f"Bridge Domain {intent.name} not found in {intent.tenant}.")
        elif bd:
            chk.ok("object.exists", "Object exists", f"Bridge Domain {intent.name} found ({len(bd.subnets)} subnet(s)).")
            users = [f"{e.app_profile}/{e.name}" for e in t.epgs() if e.bridge_domain == intent.name]
            if users:
                chk.fail("bd.in_use", "Bridge Domain is not in use", "Still used by EPG(s): " + ", ".join(users) + ". Move or delete them first.")
            else:
                chk.ok("bd.in_use", "Bridge Domain is not in use", "No EPG references this Bridge Domain.")
            impact = [f"- Bridge Domain {intent.name}"] + [f"- Subnet {s.gateway}" for s in bd.subnets]
            blast = 1 + len(bd.subnets)
        payload = aci_mo.mo("fvBD", {"name": intent.name, "status": "deleted"})
    elif intent.object_type == "app_profile":
        path = tn_path + [("fvAp", f"ap-{intent.name}")]
        ap = t.ap(intent.name) if t else None
        if ap is None and t:
            chk.fail("object.exists", "Object exists", f"Application Profile {intent.name} not found in {intent.tenant}.")
        elif ap:
            chk.ok("object.exists", "Object exists", f"Application Profile {intent.name} found.")
            if ap.epgs:
                chk.warn("ap.epgs", "Dependent EPGs", f"{len(ap.epgs)} EPG(s) will also be deleted: " + ", ".join(e.name for e in ap.epgs) + ". Endpoints in them lose connectivity.")
            impact = [f"- Application Profile {intent.name}"] + [f"- EPG {e.name}" for e in ap.epgs]
            blast = 1 + 3 * len(ap.epgs)
        payload = aci_mo.mo("fvAp", {"name": intent.name, "status": "deleted"})
    else:  # epg
        if not intent.app_profile:
            chk.fail("epg.ap", "Application Profile given", "Deleting an EPG needs its Application Profile.")
        path = tn_path + [("fvAp", f"ap-{intent.app_profile}"), ("fvAEPg", f"epg-{intent.name}")]
        ap = t.ap(intent.app_profile) if t else None
        epg = next((e for e in ap.epgs if e.name == intent.name), None) if ap else None
        if epg is None and t:
            chk.fail("object.exists", "Object exists", f"EPG {intent.app_profile}/{intent.name} not found in {intent.tenant}.")
        elif epg:
            chk.ok("object.exists", "Object exists", f"EPG {intent.name} found (BD {epg.bridge_domain}).")
            chk.warn("epg.endpoints", "Endpoint impact", "Endpoints learned in this EPG will lose connectivity.")
            impact = [f"- EPG {intent.name}"]
            blast = 3
        payload = aci_mo.mo("fvAp", {"name": intent.app_profile}, [aci_mo.mo("fvAEPg", {"name": intent.name, "status": "deleted"})])
    # Rollback = recreate the exact subtree that exists today
    existing = aci_mo.find(native_tenants, path)
    rollback = None
    if existing is not None:
        restored = copy.deepcopy(existing)
        aci_mo.unpack(restored)[1]["status"] = "created"
        rollback = restored if intent.object_type != "epg" else aci_mo.mo("fvAp", {"name": intent.app_profile}, [restored])
    return payload, rollback, impact, blast


def _wrap(tenant: str, child: dict) -> dict:
    return aci_mo.mo("fvTenant", {"dn": f"uni/tn-{tenant}", "name": tenant}, [child])


def plan(intent: CreateAppProfile | CreateBridgeDomain | DeleteObject, snapshot: Snapshot,
         native: dict[str, Any]) -> dict[str, Any]:
    chk = Check()
    t = snapshot.tenant(intent.tenant)
    if t:
        chk.ok("tenant.exists", "Tenant exists", f"Tenant {intent.tenant} found on {snapshot.target}.")
    else:
        chk.fail("tenant.exists", "Tenant exists", f"Tenant {intent.tenant} does not exist on {snapshot.target}.")

    native_tenants = copy.deepcopy(native.get("fvTenant", []))
    blast = 0
    if isinstance(intent, CreateBridgeDomain):
        child, rb_child, impact = _plan_create_bd(intent, snapshot, chk)
        blast = 1 + len(intent.subnets)
    elif isinstance(intent, CreateAppProfile):
        child, rb_child, impact = _plan_create_ap(intent, snapshot, chk)
        blast = 1 + len(intent.epgs)
    else:
        child, rb_child, impact, blast = _plan_delete(intent, snapshot, native_tenants, chk)

    payload = _wrap(intent.tenant, child)
    rollback = _wrap(intent.tenant, rb_child) if rb_child else None
    if rollback is None:
        chk.fail("rollback.ready", "Rollback plan ready", "Could not build a rollback payload for this change.")
    else:
        chk.ok("rollback.ready", "Rollback plan ready", "A payload that restores the current state has been prepared.")

    # Simulate on a sandbox copy of the live config
    after_tenant = None
    sim_imdata = copy.deepcopy(native)
    if not chk.failed:
        try:
            aci_mo.apply_payload(sim_imdata["fvTenant"], payload)
            sim = aci_mo.normalize(sim_imdata, target=snapshot.target)
            after_tenant = sim.tenant(intent.tenant)
            chk.ok("simulation", "Simulated on a copy of the fabric", "Payload applied cleanly to a sandbox copy of the current configuration.")
        except Exception as exc:  # noqa: BLE001
            chk.fail("simulation", "Simulated on a copy of the fabric", f"Simulation failed: {exc}")

    before_txt = _tenant_dump(t)
    after_txt = _tenant_dump(after_tenant) if after_tenant else before_txt
    diff = "\n".join(difflib.unified_diff(
        before_txt.splitlines(), after_txt.splitlines(),
        fromfile=f"{intent.tenant} (current)", tofile=f"{intent.tenant} (after change)", lineterm="", n=3,
    ))

    if chk.failed:
        risk = "high"
    elif isinstance(intent, DeleteObject):
        risk = "high" if blast > 3 else "medium"
    else:
        risk = "medium" if chk.warnings else "low"

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target": snapshot.target,
        "passed": not chk.failed,
        "risk": risk,
        "checks": chk.items,
        "impact": {"summary": impact, "objects": blast},
        "payload": payload,
        "rollback_payload": rollback,
        "endpoint": "POST /api/mo/uni.json",
        "diff": diff,
    }


def verify(intent: CreateAppProfile | CreateBridgeDomain | DeleteObject, snapshot: Snapshot, rolled_back: bool = False) -> list[dict[str, str]]:
    """Post-checks against a fresh snapshot after execution (or after rollback)."""
    chk = Check()
    t = snapshot.tenant(intent.tenant)
    present: bool
    label: str
    if isinstance(intent, CreateBridgeDomain):
        bd = t.bd(intent.name) if t else None
        present, label = bd is not None, f"Bridge Domain {intent.name}"
        if bd and not rolled_back:
            if bd.vrf == intent.vrf:
                chk.ok("post.vrf", "VRF binding", f"Bound to {bd.vrf}.")
            else:
                chk.fail("post.vrf", "VRF binding", f"Expected {intent.vrf}, found {bd.vrf or 'none'}.")
            got = sorted(s.gateway for s in bd.subnets)
            if got == sorted(intent.subnets):
                chk.ok("post.subnets", "Subnets", ", ".join(got) or "none")
            else:
                chk.fail("post.subnets", "Subnets", f"Expected {sorted(intent.subnets)}, found {got}.")
    elif isinstance(intent, CreateAppProfile):
        ap = t.ap(intent.name) if t else None
        present, label = ap is not None, f"Application Profile {intent.name}"
        if ap and not rolled_back:
            got = sorted(e.name for e in ap.epgs)
            want = sorted(e.name for e in intent.epgs)
            (chk.ok if got == want else chk.fail)("post.epgs", "EPGs", ", ".join(got) or "none")
    else:
        if intent.object_type == "bridge_domain":
            present = bool(t and t.bd(intent.name))
        elif intent.object_type == "app_profile":
            present = bool(t and t.ap(intent.name))
        else:
            ap = t.ap(intent.app_profile) if t else None
            present = bool(ap and any(e.name == intent.name for e in ap.epgs))
        label = f"{intent.object_type.replace('_', ' ').title()} {intent.name}"
    should_exist = isinstance(intent, DeleteObject) == rolled_back
    if present == should_exist:
        chk.ok("post.state", "Object state", f"{label} is {'present' if present else 'absent'}, as expected.")
    else:
        chk.fail("post.state", "Object state", f"{label} is {'present' if present else 'absent'}, which is not expected.")
    return chk.items
