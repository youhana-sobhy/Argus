"""Cisco ACI managed-object (MO) helpers.

Handles the native APIC JSON shape: ``{"<class>": {"attributes": {...}, "children": [...]}}``.
Used by the mock APIC (to apply payloads to its in-memory tree), by the normalizer
(APIC JSON -> AutoHero model) and by the exporter (AutoHero model -> APIC JSON).
"""

from __future__ import annotations

import copy
from typing import Any

from autohero.core.model import (
    EPG,
    VRF,
    AppProfile,
    BridgeDomain,
    FabricLink,
    FabricNode,
    L3Out,
    Snapshot,
    Subnet,
    Tenant,
)

MO = dict[str, Any]

RN_RULES: dict[str, str] = {
    "fvTenant": "tn-{name}",
    "fvCtx": "ctx-{name}",
    "fvBD": "BD-{name}",
    "fvAp": "ap-{name}",
    "fvAEPg": "epg-{name}",
    "fvSubnet": "subnet-[{ip}]",
    "fvRsCtx": "rsctx",
    "fvRsBd": "rsbd",
    "l3extOut": "out-{name}",
    "l3extRsEctx": "rsectx",
}


def mo(cls: str, attrs: dict[str, Any], children: list[MO] | None = None) -> MO:
    body: dict[str, Any] = {"attributes": dict(attrs)}
    if children:
        body["children"] = children
    return {cls: body}


def unpack(obj: MO) -> tuple[str, dict[str, Any], list[MO]]:
    cls = next(iter(obj))
    body = obj[cls]
    return cls, body.setdefault("attributes", {}), body.setdefault("children", [])


def rn_of(cls: str, attrs: dict[str, Any]) -> str:
    if "dn" in attrs and attrs["dn"]:
        return attrs["dn"].rsplit("/", 1)[-1] if cls != "fvSubnet" else "subnet-" + attrs["dn"].split("subnet-", 1)[1]
    rule = RN_RULES.get(cls)
    if rule is None:
        raise ValueError(f"Unsupported ACI class: {cls}")
    return rule.format(**attrs)


def _merge_children(existing: list[MO], incoming: list[MO]) -> None:
    for child in incoming:
        cls, attrs, kids = unpack(copy.deepcopy(child))
        rn = rn_of(cls, attrs)
        idx = next(
            (i for i, e in enumerate(existing) if next(iter(e)) == cls and rn_of(cls, unpack(e)[1]) == rn),
            None,
        )
        status = attrs.pop("status", "")
        if "deleted" in status:
            if idx is not None:
                existing.pop(idx)
            continue
        if status == "created" and idx is not None:
            # APIC semantics: status=created (without modified) fails if the object exists
            raise ValueError(f"{cls} {rn} already exists")
        attrs.pop("dn", None)
        if idx is None:
            existing.append(mo(cls, attrs, kids) if kids else mo(cls, attrs))
            if kids:
                # normalise nested statuses (e.g. created) away
                _strip_status(existing[-1])
        else:
            _, e_attrs, e_kids = unpack(existing[idx])
            e_attrs.update(attrs)
            _merge_children(e_kids, kids)


def _strip_status(obj: MO) -> None:
    _, attrs, kids = unpack(obj)
    attrs.pop("status", None)
    for k in kids:
        _strip_status(k)


def apply_payload(tenants: list[MO], payload: MO) -> None:
    """Apply an APIC-style POST payload (rooted at fvTenant) to an fvTenant list in place."""
    cls, attrs, kids = unpack(copy.deepcopy(payload))
    if cls == "polUni":
        for k in kids:
            apply_payload(tenants, k)
        return
    if cls != "fvTenant":
        raise ValueError("Mock APIC only accepts payloads rooted at polUni or fvTenant")
    root = {"polUni": {"attributes": {}, "children": tenants}}
    wrapped = mo("fvTenant", {k: v for k, v in attrs.items() if k != "dn"} | {"name": attrs.get("name") or rn_of(cls, attrs)[3:]}, kids)
    _merge_children(unpack(root)[2], [wrapped])


def find(tenants: list[MO], path: list[tuple[str, str]]) -> MO | None:
    """Find an MO by a path of (class, rn) pairs starting at a tenant."""
    level = tenants
    found: MO | None = None
    for cls, rn in path:
        found = next(
            (e for e in level if next(iter(e)) == cls and rn_of(cls, unpack(e)[1]) == rn),
            None,
        )
        if found is None:
            return None
        level = unpack(found)[2]
    return found


def tree_to_dn(obj: MO, parent_dn: str) -> MO:
    """Return a copy of an MO tree with a dn on the root (for display/export)."""
    out = copy.deepcopy(obj)
    cls, attrs, _ = unpack(out)
    attrs["dn"] = f"{parent_dn}/{rn_of(cls, attrs)}"
    return out


# ---------------------------------------------------------------------------
# Normalizer: APIC JSON -> AutoHero model
# ---------------------------------------------------------------------------

def _yes(v: Any) -> bool:
    return str(v).lower() in {"yes", "true", "1"}


def normalize(imdata: dict[str, list[MO]], target: str, controller_version: str = "", source: str = "mock") -> Snapshot:
    systems = {unpack(s)[1]["id"]: unpack(s)[1] for s in imdata.get("topSystem", [])}
    nodes: list[FabricNode] = []
    for raw in imdata.get("fabricNode", []):
        _, a, _ = unpack(raw)
        role = a.get("nodeType") if a.get("nodeType") == "border-leaf" else a.get("role", "leaf")
        st = a.get("fabricSt", "active")
        nodes.append(FabricNode(
            id=a["id"], name=a.get("name", a["id"]), role=role, model=a.get("model", ""),
            serial=a.get("serial", ""), version=a.get("version", ""),
            oob_ip=systems.get(a["id"], {}).get("oobMgmtAddr", ""),
            pod=a.get("dn", "topology/pod-1").split("/")[1].replace("pod-", "") if "/" in a.get("dn", "") else "1",
            status="up" if st in ("active", "") else "down",
        ))
    links: list[FabricLink] = []
    for raw in imdata.get("fabricLink", []):
        _, a, _ = unpack(raw)
        links.append(FabricLink(
            a=a["n1"], b=a["n2"], a_port=f"eth{a.get('s1', '1')}/{a.get('p1', '')}",
            b_port=f"eth{a.get('s2', '1')}/{a.get('p2', '')}", speed=a.get("speed", ""),
            status="up" if a.get("linkState", "ok") == "ok" else "down",
        ))
    # Mark nodes with a down link as degraded
    down_nodes = {n for l in links if l.status == "down" for n in (l.a, l.b)}
    for n in nodes:
        if n.id in down_nodes and n.status == "up":
            n.status = "degraded"

    tenants: list[Tenant] = []
    for raw in imdata.get("fvTenant", []):
        _, ta, tkids = unpack(raw)
        tname = ta["name"]
        t = Tenant(name=tname, descr=ta.get("descr", ""))
        for child in tkids:
            cls, a, kids = unpack(child)
            if cls == "fvCtx":
                t.vrfs.append(VRF(name=a["name"], tenant=tname, policy_enforcement=a.get("pcEnfPref", "enforced"), descr=a.get("descr", "")))
            elif cls == "fvBD":
                bd = BridgeDomain(
                    name=a["name"], tenant=tname, descr=a.get("descr", ""),
                    unicast_route=_yes(a.get("unicastRoute", "yes")), arp_flood=_yes(a.get("arpFlood", "no")),
                    l2_unknown_unicast=a.get("unkMacUcastAct", "proxy"),
                )
                for k in kids:
                    kc, ka, _ = unpack(k)
                    if kc == "fvRsCtx":
                        bd.vrf = ka.get("tnFvCtxName", "")
                    elif kc == "fvSubnet":
                        bd.subnets.append(Subnet(gateway=ka["ip"], scope=ka.get("scope", "private")))
                t.bridge_domains.append(bd)
            elif cls == "fvAp":
                ap = AppProfile(name=a["name"], tenant=tname, descr=a.get("descr", ""))
                for k in kids:
                    kc, ka, kk = unpack(k)
                    if kc == "fvAEPg":
                        bd_name = next((unpack(x)[1].get("tnFvBDName", "") for x in kk if next(iter(x)) == "fvRsBd"), "")
                        ap.epgs.append(EPG(name=ka["name"], tenant=tname, app_profile=ap.name, bridge_domain=bd_name, descr=ka.get("descr", "")))
                t.app_profiles.append(ap)
            elif cls == "l3extOut":
                vrf = next((unpack(x)[1].get("tnFvCtxName", "") for x in kids if next(iter(x)) == "l3extRsEctx"), "")
                t.l3outs.append(L3Out(name=a["name"], tenant=tname, vrf=vrf, descr=a.get("descr", "")))
        tenants.append(t)
    return Snapshot(target=target, controller_version=controller_version, source=source, nodes=nodes, links=links, tenants=tenants)


# ---------------------------------------------------------------------------
# Exporter: AutoHero model -> APIC JSON
# ---------------------------------------------------------------------------

def bd_to_mo(bd: BridgeDomain) -> MO:
    kids = [mo("fvRsCtx", {"tnFvCtxName": bd.vrf})] if bd.vrf else []
    kids += [mo("fvSubnet", {"ip": s.gateway, "scope": s.scope}) for s in bd.subnets]
    return mo("fvBD", {
        "name": bd.name, "descr": bd.descr,
        "unicastRoute": "yes" if bd.unicast_route else "no",
        "arpFlood": "yes" if bd.arp_flood else "no",
        "unkMacUcastAct": bd.l2_unknown_unicast,
    }, kids)


def epg_to_mo(epg: EPG) -> MO:
    kids = [mo("fvRsBd", {"tnFvBDName": epg.bridge_domain})] if epg.bridge_domain else []
    return mo("fvAEPg", {"name": epg.name, "descr": epg.descr}, kids)


def ap_to_mo(ap: AppProfile) -> MO:
    return mo("fvAp", {"name": ap.name, "descr": ap.descr}, [epg_to_mo(e) for e in ap.epgs])


def tenant_to_mo(t: Tenant) -> MO:
    kids: list[MO] = [mo("fvCtx", {"name": v.name, "pcEnfPref": v.policy_enforcement, "descr": v.descr}) for v in t.vrfs]
    kids += [bd_to_mo(b) for b in t.bridge_domains]
    kids += [ap_to_mo(a) for a in t.app_profiles]
    kids += [mo("l3extOut", {"name": o.name, "descr": o.descr}, [mo("l3extRsEctx", {"tnFvCtxName": o.vrf})]) for o in t.l3outs]
    return mo("fvTenant", {"dn": f"uni/tn-{t.name}", "name": t.name, "descr": t.descr}, kids)


def export_apic(snapshot: Snapshot) -> dict[str, Any]:
    return {"totalCount": str(len(snapshot.tenants)), "imdata": [tenant_to_mo(t) for t in snapshot.tenants]}
