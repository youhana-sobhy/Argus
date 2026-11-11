"""Argus assistant: chat about topology and config, and draft change requests.

Flow for an external LLM (Claude / Azure OpenAI):
  1. Build a compact text context from the normalized snapshot.
  2. Redact IPs, serials and secrets (consistent tokens).
  3. Send system prompt + context + conversation to the provider.
  4. Restore real values in the answer; extract any ```argus-change``` proposal.

The assistant never executes anything. A proposal only pre-fills a *draft* change
request, which still goes through dry-run, four-eyes approval and execution.

The offline engine (provider ``mock``) answers common questions deterministically from
the same snapshot, so the demo works with no key and no data leaving the host.
"""

from __future__ import annotations

import ipaddress
import json
import re
from typing import Any

from argus.ai.providers import LLMProvider, ProviderError
from argus.ai.redaction import Redactor
from argus.changes.intents import parse_intent
from argus.core.model import Snapshot

SYSTEM_PROMPT = """You are Argus, the network assistant of a cloud network automation team.
You help network engineers understand a Cisco ACI fabric and prepare change requests.

Ground rules:
- Answer only from the INVENTORY below. If something is not in it, say you don't see it.
- Be concise and practical. Use short paragraphs or bullet lists, and name exact objects
  (tenant / VRF / bridge domain / application profile / EPG / node).
- Values like <IP_3> or <SERIAL_2> are masked for privacy; use them as-is.
- You cannot execute changes. When the user asks for a change you may propose ONE draft,
  using exactly one fenced block tagged argus-change containing JSON for one of:
  {"kind":"create_bridge_domain","tenant":"","name":"","vrf":"","subnets":["10.0.0.1/24"],"descr":""}
  {"kind":"create_app_profile","tenant":"","name":"","descr":"","epgs":[{"name":"","bridge_domain":""}]}
  {"kind":"delete_object","tenant":"","object_type":"app_profile|bridge_domain|epg","name":"","app_profile":""}
  Explain that the draft will be dry-run, then needs approval by a second person before execution.
- Team naming convention: *_BD, *_AP, *_EPG, *_VRF.
"""

PROPOSAL_RE = re.compile(r"```argus-change\s*(\{.*?\})\s*```", re.S)


def build_context(s: Snapshot) -> str:
    lines = [f"Fabric {s.target} (Cisco ACI, controller {s.controller_version or 'n/a'}, source {s.source})"]
    st = s.stats()
    lines.append(f"Totals: {st['controllers']} APIC, {st['spines']} spines, {st['leaves']} leaves, {st['tenants']} tenants, "
                 f"{st['vrfs']} VRFs, {st['bridge_domains']} BDs, {st['app_profiles']} APs, {st['epgs']} EPGs")
    lines.append("Nodes:")
    for n in s.nodes:
        lines.append(f"  - node {n.id} {n.name} role={n.role} model={n.model} version={n.version} oob={n.oob_ip} serial={n.serial} status={n.status}")
    down = [l for l in s.links if l.status != "up"]
    lines.append(f"Fabric links: {len(s.links)} total, {len(down)} down")
    for l in down:
        lines.append(f"  - DOWN: node {l.a} {l.a_port} <-> node {l.b} {l.b_port}")
    for t in s.tenants:
        lines.append(f"Tenant {t.name}: {t.descr}")
        for v in t.vrfs:
            lines.append(f"  VRF {v.name} enforcement={v.policy_enforcement}")
        used = {e.bridge_domain for e in t.epgs()}
        for b in t.bridge_domains:
            subnets = ", ".join(x.gateway for x in b.subnets) or "none"
            lines.append(f"  BD {b.name} vrf={b.vrf} subnets={subnets} unicast_route={b.unicast_route} arp_flood={b.arp_flood} "
                         f"l2_unknown={b.l2_unknown_unicast} used_by_epg={'yes' if b.name in used else 'no'} descr={b.descr}")
        for a in t.app_profiles:
            lines.append(f"  AP {a.name}: " + ", ".join(f"EPG {e.name} (BD {e.bridge_domain})" for e in a.epgs))
        for o in t.l3outs:
            lines.append(f"  L3Out {o.name} vrf={o.vrf}")
    return "\n".join(lines)


def extract_proposal(text: str) -> tuple[str, dict[str, Any] | None]:
    m = PROPOSAL_RE.search(text)
    if not m:
        return text, None
    try:
        intent = parse_intent(json.loads(m.group(1))).model_dump(mode="json")
    except Exception:  # noqa: BLE001 - invalid proposal: just show the text
        return text, None
    return PROPOSAL_RE.sub("", text).strip(), intent


class Assistant:
    def __init__(self, provider: LLMProvider | None, redact: bool = True):
        self.provider = provider
        self.redact = redact

    @property
    def provider_name(self) -> str:
        return self.provider.name if self.provider else "offline"

    def ask(self, question: str, history: list[dict[str, str]], snapshot: Snapshot) -> dict[str, Any]:
        if self.provider is None:
            return offline_answer(question, snapshot) | {"provider": "offline", "model": "argus-rules", "masked": 0}
        red = Redactor(self.redact)
        context = red.redact(build_context(snapshot))
        msgs = [{"role": m["role"], "content": red.redact(m["content"])} for m in history[-10:] if m.get("role") in ("user", "assistant")]
        msgs.append({"role": "user", "content": red.redact(question)})
        system = f"{SYSTEM_PROMPT}\nINVENTORY (normalized, collected {snapshot.collected_at:%Y-%m-%d %H:%M} UTC):\n{context}"
        try:
            reply = self.provider.complete(system, msgs)
        except ProviderError as exc:
            fallback = offline_answer(question, snapshot)
            fallback["answer"] = f"*{exc} Answering offline instead.*\n\n" + fallback["answer"]
            return fallback | {"provider": "offline", "model": "argus-rules", "masked": 0}
        text, proposal = extract_proposal(red.restore(reply.text))
        return {"answer": text, "proposal": proposal, "provider": reply.provider, "model": reply.model,
                "masked": red.masked_count, "masked_summary": red.summary()}


# ---------------------------------------------------------------------------
# Offline engine
# ---------------------------------------------------------------------------

def _find_names(snapshot: Snapshot, text: str) -> list[tuple[str, Any, Any]]:
    """Return (type, object, tenant) for every object name mentioned in the text."""
    low = text.lower()
    hits: list[tuple[str, Any, Any]] = []
    for t in snapshot.tenants:
        if re.search(rf"\b{re.escape(t.name.lower())}\b", low):
            hits.append(("tenant", t, t))
        for v in t.vrfs:
            if v.name.lower() in low and v.name != "default":
                hits.append(("vrf", v, t))
        for b in t.bridge_domains:
            if b.name.lower() in low and b.name != "default":
                hits.append(("bd", b, t))
        for a in t.app_profiles:
            if a.name.lower() in low:
                hits.append(("ap", a, t))
            for e in a.epgs:
                if e.name.lower() in low:
                    hits.append(("epg", e, t))
    for n in snapshot.nodes:
        if n.name.lower() in low or re.search(rf"\bnode[- ]?{n.id}\b", low) or re.search(rf"\b(leaf|spine)[- ]?{n.id}\b", low):
            hits.append(("node", n, None))
    return hits


def _describe(kind: str, obj: Any, t: Any, s: Snapshot) -> str:
    if kind == "tenant":
        return (f"**Tenant {obj.name}** ({obj.descr or 'no description'})\n"
                f"- VRFs: {', '.join(v.name for v in obj.vrfs) or 'none'}\n"
                f"- Bridge Domains: {', '.join(b.name for b in obj.bridge_domains) or 'none'}\n"
                f"- Application Profiles: {', '.join(f'{a.name} ({len(a.epgs)} EPGs)' for a in obj.app_profiles) or 'none'}\n"
                f"- L3Outs: {', '.join(o.name for o in obj.l3outs) or 'none'}")
    if kind == "vrf":
        bds = [b for b in t.bridge_domains if b.vrf == obj.name]
        bd_txt = ", ".join(b.name + " (" + (", ".join(x.gateway for x in b.subnets) or "no subnet") + ")" for b in bds)
        return (f"**VRF {obj.name}** in tenant {t.name}, policy enforcement *{obj.policy_enforcement}*.\n"
                f"- Bridge Domains: {bd_txt or 'none'}\n"
                f"- L3Outs: {', '.join(o.name for o in t.l3outs if o.vrf == obj.name) or 'none'}")
    if kind == "bd":
        epgs = [f"{e.app_profile}/{e.name}" for e in t.epgs() if e.bridge_domain == obj.name]
        return (f"**Bridge Domain {obj.name}** in tenant {t.name} ({obj.descr or 'no description'})\n"
                f"- VRF: {obj.vrf}\n- Subnets: {', '.join(x.gateway for x in obj.subnets) or 'none'}\n"
                f"- Unicast routing: {'on' if obj.unicast_route else 'off'}, ARP flooding: {'on' if obj.arp_flood else 'off'}, "
                f"L2 unknown unicast: {obj.l2_unknown_unicast}\n"
                f"- Used by EPGs: {', '.join(epgs) or '**none** (candidate for clean-up)'}")
    if kind == "ap":
        return (f"**Application Profile {obj.name}** in tenant {t.name} ({obj.descr or 'no description'})\n"
                + "\n".join(f"- EPG {e.name} → BD {e.bridge_domain}" for e in obj.epgs))
    if kind == "epg":
        bd = t.bd(obj.bridge_domain)
        return (f"**EPG {obj.name}** in {t.name}/{obj.app_profile} ({obj.descr or 'no description'})\n"
                f"- Bridge Domain: {obj.bridge_domain}\n- VRF: {bd.vrf if bd else 'unknown'}\n"
                f"- Gateway: {', '.join(x.gateway for x in bd.subnets) if bd else 'unknown'}")
    if kind == "node":
        links = [l for l in s.links if obj.id in (l.a, l.b)]
        down = [l for l in links if l.status != "up"]
        return (f"**{obj.name}** (node {obj.id}, {obj.role})\n- Model {obj.model}, version {obj.version}\n"
                f"- OOB management {obj.oob_ip}, serial {obj.serial}\n- Status: **{obj.status}**, "
                f"{len(links)} fabric links ({len(down)} down)")
    return ""


def _proposal_from_text(q: str, s: Snapshot) -> tuple[str, dict[str, Any]] | None:
    low = q.lower()
    if not re.search(r"\b(create|add|new|build|delete|remove|decommission)\b", low):
        return None
    tenant = next((t for t in s.tenants if re.search(rf"\b{re.escape(t.name.lower())}\b", low)), None)
    tname = tenant.name if tenant else "VF-CORE"
    m_name = re.search(r"\b(?:called|named|name)\s+([A-Za-z0-9_.:-]+)", q)
    if re.search(r"\b(delete|remove|decommission)\b", low):
        for kind, obj, t in _find_names(s, q):
            if kind in ("bd", "ap", "epg"):
                otype = {"bd": "bridge_domain", "ap": "app_profile", "epg": "epg"}[kind]
                intent = {"kind": "delete_object", "tenant": t.name, "object_type": otype, "name": obj.name,
                          "app_profile": getattr(obj, "app_profile", "") if kind == "epg" else ""}
                return f"I've prepared a draft to delete {otype.replace('_', ' ')} **{obj.name}** from {t.name}.", intent
        return None
    if re.search(r"bridge[- ]?domain|\bbd\b", low):
        name = m_name.group(1) if m_name else (re.search(r"\b([A-Za-z0-9]+_BD)\b", q).group(1) if re.search(r"\b([A-Za-z0-9]+_BD)\b", q) else "NEW_BD")
        subnets = [x for x in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}/\d{1,2}\b", q)]
        vrf = next((v.name for t in s.tenants if t.name == tname for v in t.vrfs if v.name.lower() in low), None)
        if vrf is None and tenant and tenant.vrfs:
            vrf = tenant.vrfs[0].name
        intent = {"kind": "create_bridge_domain", "tenant": tname, "name": name, "vrf": vrf or "default", "subnets": subnets, "descr": ""}
        return f"I've prepared a draft Bridge Domain **{name}** in {tname} (VRF {intent['vrf']}).", intent
    if re.search(r"app(lication)?[- ]?profile|\bap\b", low):
        name = m_name.group(1) if m_name else (re.search(r"\b([A-Za-z0-9]+_AP)\b", q).group(1) if re.search(r"\b([A-Za-z0-9]+_AP)\b", q) else "NEW_AP")
        epgs = []
        for epg_name, bd_name in re.findall(r"\b([A-Za-z0-9]+_EPG)\b\s+(?:on|in|using|with)\s+(?:bd\s+)?([A-Za-z0-9]+_BD)\b", q, re.I):
            epgs.append({"name": epg_name, "bridge_domain": bd_name})
        intent = {"kind": "create_app_profile", "tenant": tname, "name": name, "descr": "", "epgs": epgs}
        return f"I've prepared a draft Application Profile **{name}** in {tname} with {len(epgs)} EPG(s).", intent
    return None


def offline_answer(question: str, s: Snapshot) -> dict[str, Any]:
    q = question.strip()
    low = q.lower()
    st = s.stats()

    prop = _proposal_from_text(q, s)
    if prop:
        msg, intent = prop
        try:
            intent = parse_intent(intent).model_dump(mode="json")
        except Exception:  # noqa: BLE001
            return {"answer": msg + " Some fields are missing, so open **New change** to complete it.", "proposal": None}
        return {"answer": msg + "\n\nReview it below. Creating it runs a **dry-run** with pre-checks; it then needs "
                "**approval by a second person** before anything is pushed to the fabric.", "proposal": intent}

    ips = re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", q)
    if ips:
        ip = ipaddress.ip_address(ips[0])
        for t in s.tenants:
            for b in t.bridge_domains:
                for sn in b.subnets:
                    if ip in ipaddress.ip_interface(sn.gateway).network:
                        epgs = [e.name for e in t.epgs() if e.bridge_domain == b.name]
                        return {"answer": f"**{ip}** belongs to subnet {sn.gateway} on Bridge Domain **{b.name}** "
                                f"(tenant {t.name}, VRF {b.vrf}). EPGs on this BD: {', '.join(epgs) or 'none'}.", "proposal": None}
        for n in s.nodes:
            if n.oob_ip == str(ip):
                return {"answer": _describe("node", n, None, s), "proposal": None}
        return {"answer": f"I don't see {ip} in any Bridge Domain subnet or node address on {s.target}.", "proposal": None}

    if re.search(r"\b(unused|orphan|clean ?up|empty)\b", low):
        unused = [(t.name, b) for t in s.tenants for b in t.bridge_domains
                  if b.name not in {e.bridge_domain for e in t.epgs()} and t.name != "common"]
        if not unused:
            return {"answer": "Every Bridge Domain is used by at least one EPG.", "proposal": None}
        lines = "\n".join(f"- **{b.name}** in {tn} ({', '.join(x.gateway for x in b.subnets) or 'no subnet'}) {b.descr}" for tn, b in unused)
        return {"answer": f"These Bridge Domains are not used by any EPG:\n{lines}\n\nYou can ask me to *delete* one, "
                "and I'll prepare a draft change.", "proposal": None}

    if re.search(r"\b(down|health|issue|problem|degraded|fault|alarm|status)\b", low):
        down = [l for l in s.links if l.status != "up"]
        deg = [n for n in s.nodes if n.status != "up"]
        if not down and not deg:
            return {"answer": f"All {len(s.nodes)} nodes and {len(s.links)} fabric links on {s.target} are up.", "proposal": None}
        names = {n.id: n.name for n in s.nodes}
        lines = [f"- Link **{names.get(l.a, l.a)} {l.a_port} ↔ {names.get(l.b, l.b)} {l.b_port}** is **down**" for l in down]
        lines += [f"- Node **{n.name}** is **{n.status}**" for n in deg]
        return {"answer": f"I found {len(down)} link(s) down on {s.target}:\n" + "\n".join(lines) +
                "\n\nThe fabric is still redundant (every leaf has two spine uplinks), but the affected leaf runs on one uplink.", "proposal": None}

    hits = _find_names(s, q)
    if hits:
        # Prefer the most specific object mentioned
        order = {"epg": 0, "bd": 1, "ap": 2, "vrf": 3, "node": 4, "tenant": 5}
        hits.sort(key=lambda h: order[h[0]])
        seen, parts = set(), []
        for kind, obj, t in hits[:3]:
            key = (kind, obj.name)
            if key not in seen:
                seen.add(key)
                parts.append(_describe(kind, obj, t, s))
        return {"answer": "\n\n".join(parts), "proposal": None}

    if re.search(r"\b(spine|leaf|leaves|node|nodes|switch|topology|fabric|apic|controller)\b", low):
        sp = [n for n in s.nodes if n.role == "spine"]
        lf = [n for n in s.nodes if n.role in ("leaf", "border-leaf")]
        ap = [n for n in s.nodes if n.role == "controller"]
        return {"answer": f"**{s.target}** is a spine-leaf ACI fabric (controller {s.controller_version}):\n"
                f"- {len(ap)} APIC controllers: {', '.join(n.name for n in ap)}\n"
                f"- {len(sp)} spines: {', '.join(n.name for n in sp)}\n"
                f"- {len(lf)} leaves: {', '.join(n.name + (' (border)' if n.role == 'border-leaf' else '') for n in lf)}\n"
                f"- {len(s.links)} fabric links, {sum(1 for l in s.links if l.status != 'up')} down\n\n"
                "Open **Topology** to see the graph.", "proposal": None}

    if re.search(r"\b(tenant|tenants|vrf|vrfs|bridge|bd|bds|epg|epgs|summary|overview|how many|inventory)\b", low):
        lines = [f"- **{t.name}**: {len(t.vrfs)} VRF, {len(t.bridge_domains)} BD, {len(t.app_profiles)} AP, {len(t.epgs())} EPG" for t in s.tenants]
        return {"answer": f"{s.target} has {st['tenants']} tenants, {st['vrfs']} VRFs, {st['bridge_domains']} Bridge Domains, "
                f"{st['app_profiles']} Application Profiles and {st['epgs']} EPGs:\n" + "\n".join(lines), "proposal": None}

    return {"answer": "I can answer questions about the fabric and prepare change drafts. Try:\n"
            "- *Give me an overview of the fabric*\n- *Are any links down?*\n- *Which bridge domains are unused?*\n"
            "- *Where is 10.50.3.20?*\n- *Tell me about UPF_BD*\n"
            "- *Create a bridge domain called CACHE_BD in VF-CORE on PROD_VRF with subnet 10.10.40.1/24*\n"
            "- *Delete LEGACY_BD*", "proposal": None}
