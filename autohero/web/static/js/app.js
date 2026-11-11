/* AutoHero web client - vanilla JS, no build step. */
(() => {
  "use strict";
  const AH = window.AH || {};
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const can = (p) => (AH.user?.permissions || []).includes(p);

  // ------------------------------------------------------------------ helpers
  async function api(path, opts = {}) {
    const init = { headers: { "Content-Type": "application/json" }, ...opts };
    if (init.body && typeof init.body !== "string") init.body = JSON.stringify(init.body);
    const r = await fetch(path, init);
    if (r.status === 401) { location.href = "/login"; throw new Error("Signed out"); }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : `Request failed (${r.status})`);
    return j;
  }
  function toast(msg, kind = "ok") {
    const t = document.createElement("div");
    t.className = `toast ${kind}`; t.textContent = msg;
    $("#toasts").appendChild(t);
    setTimeout(() => t.remove(), 4200);
  }
  function md(text) {
    const lines = esc(text).split(/\n/);
    let html = "", inList = false;
    const inline = (s) => s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|[^*])\*(?!\s)(.+?)\*/g, "$1<i>$2</i>")
      .replace(/`(.+?)`/g, "<code>$1</code>"); // no _italic_: ACI names use underscores
    for (const ln of lines) {
      const m = ln.match(/^\s*[-•]\s+(.*)/);
      if (m) { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${inline(m[1])}</li>`; continue; }
      if (inList) { html += "</ul>"; inList = false; }
      if (ln.trim()) html += `<p>${inline(ln)}</p>`;
    }
    if (inList) html += "</ul>";
    return html;
  }
  const ago = (iso) => {
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 60) return "just now"; if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`; return new Date(iso).toLocaleDateString();
  };
  const fmt = (iso) => new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });

  const SVG = {
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
    x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>',
    warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M12 6v7M12 17.5h.01"/></svg>',
    play: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M7 5v14l12-7z"/></svg>',
    undo: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/></svg>',
    flask: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 3h6M10 3v6L4.5 19a1.5 1.5 0 0 0 1.3 2h12.4a1.5 1.5 0 0 0 1.3-2L14 9V3"/></svg>',
    send: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12 20 4l-6 16-3-7z"/></svg>',
    trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h16M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>',
  };

  const STATUS = {
    draft: ["Draft", "neutral"], validated: ["Dry-run passed", "blue"], validation_failed: ["Dry-run blocked", "red"],
    pending_approval: ["Awaiting approval", "amber"], approved: ["Approved", "violet"], rejected: ["Rejected", "dark"],
    executing: ["Executing", "blue"], completed: ["Completed", "green"], failed: ["Failed", "red"], rolled_back: ["Rolled back", "dark"],
  };
  const statusPill = (s) => { const [l, c] = STATUS[s] || [s, "neutral"]; return `<span class="pill ${c}"><span class="dot"></span>${l}</span>`; };
  const riskPill = (r) => `<span class="pill ${{ low: "green", medium: "amber", high: "red" }[r] || "neutral"}">${esc(r)} risk</span>`;
  const btnBusy = (b, on, label) => { b.disabled = on; if (on) { b.dataset.html = b.innerHTML; b.innerHTML = `<span class="spin"></span>${label || "Working…"}`; } else if (b.dataset.html) b.innerHTML = b.dataset.html; };

  function modal({ title, body, confirm, danger, withNote }) {
    return new Promise((resolve) => {
      const bg = document.createElement("div");
      bg.className = "modal-bg";
      bg.innerHTML = `<div class="modal"><div class="card-h"><h2>${esc(title)}</h2></div><div class="card-b">${body}
        ${withNote ? '<div><label class="f">Note (optional)</label><textarea class="input" rows="2" id="m-note"></textarea></div>' : ""}</div>
        <div class="foot"><button class="btn ghost" data-a="no">Cancel</button><button class="btn ${danger ? "dark" : "primary"}" data-a="yes">${esc(confirm)}</button></div></div>`;
      document.body.appendChild(bg);
      bg.addEventListener("click", (e) => {
        const a = e.target.closest("[data-a]")?.dataset.a;
        if (e.target === bg || a === "no") { bg.remove(); resolve(null); }
        if (a === "yes") { const note = $("#m-note", bg)?.value || ""; bg.remove(); resolve({ note }); }
      });
    });
  }

  // ------------------------------------------------------------------ shell
  $("#logout")?.addEventListener("click", async () => { await api("/api/auth/logout", { method: "POST" }).catch(() => {}); location.href = "/login"; });
  async function navCount() {
    try {
      const rows = await api("/api/changes");
      let n = 0;
      if (can("change.approve")) n += rows.filter((r) => r.status === "pending_approval" && r.requested_by !== AH.user.username).length;
      if (can("change.execute")) n += rows.filter((r) => r.status === "approved").length;
      const el = $("#nav-pending");
      if (el) { el.textContent = n; el.classList.toggle("hidden", !n); }
      return rows;
    } catch { return []; }
  }

  // ------------------------------------------------------------------ overview
  async function overview() {
    const h = new Date().getHours();
    $("#greet-time").textContent = h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
    const [snap, rows] = await Promise.all([api("/api/inventory/snapshot"), navCount()]);
    const st = snap.stats;
    const down = snap.links.filter((l) => l.status !== "up").length;
    const open = rows.filter((r) => ["draft", "validated", "pending_approval", "approved", "executing"].includes(r.status)).length;
    const kpi = (label, value, hint, alert) => `<div class="card kpi ${alert ? "alert" : ""}"><div class="label">${label}</div><div class="value">${value}</div><div class="hint">${hint}</div></div>`;
    $("#kpis").innerHTML = [
      kpi("Fabric nodes", st.spines + st.leaves + st.controllers, `${st.spines} spines · ${st.leaves} leaves · ${st.controllers} APIC`),
      kpi("Tenants", st.tenants, `${st.vrfs} VRFs`),
      kpi("Bridge domains", st.bridge_domains, "L2 domains with gateways"),
      kpi("EPGs", st.epgs, `${st.app_profiles} application profiles`),
      kpi("Open changes", open, `${rows.length} total`),
      kpi("Links down", down, down ? "Needs attention" : "All fabric links up", down > 0),
    ].join("");
    $("#recent-changes").innerHTML = rows.slice(0, 6).map((r) => `<tr class="click" data-id="${r.id}"><td class="ref">${r.ref}</td><td>${esc(r.title)}</td><td>${statusPill(r.status)}</td><td>${riskPill(r.risk)}</td><td>${esc(r.requested_by)}</td></tr>`).join("")
      || `<tr><td colspan="5" class="empty">No changes yet. ${can("change.create") ? '<a href="/changes/new">Create the first one</a>.' : ""}</td></tr>`;
    $$("#recent-changes tr.click").forEach((tr) => (tr.onclick = () => (location.href = `/changes/${tr.dataset.id}`)));

    const items = [];
    snap.links.filter((l) => l.status !== "up").forEach((l) => {
      const n = (id) => snap.nodes.find((x) => x.id === id)?.name || id;
      items.push(`<div class="row"><div class="icon-tile">${SVG.warn}</div><div><b>Fabric link down</b><div class="small muted">${esc(n(l.a))} ${l.a_port} ↔ ${esc(n(l.b))} ${l.b_port}</div></div><a class="btn sm" style="margin-left:auto" href="/topology">View</a></div>`);
    });
    rows.filter((r) => r.status === "pending_approval").forEach((r) => items.push(`<div class="row"><div class="icon-tile amber">${SVG.check}</div><div><b>${r.ref} awaits approval</b><div class="small muted">${esc(r.title)}</div></div><a class="btn sm" style="margin-left:auto" href="/changes/${r.id}">Open</a></div>`));
    rows.filter((r) => r.status === "approved").forEach((r) => items.push(`<div class="row"><div class="icon-tile blue">${SVG.play}</div><div><b>${r.ref} ready to execute</b><div class="small muted">${esc(r.title)}</div></div><a class="btn sm" style="margin-left:auto" href="/changes/${r.id}">Open</a></div>`));
    const unused = snap.tenants.flatMap((t) => t.name === "common" ? [] : t.bridge_domains.filter((b) => !t.app_profiles.some((a) => a.epgs.some((e) => e.bridge_domain === b.name))).map((b) => [t.name, b]));
    unused.forEach(([tn, b]) => items.push(`<div class="row"><div class="icon-tile dark">${SVG.trash}</div><div><b>Unused bridge domain ${esc(b.name)}</b><div class="small muted">${esc(tn)} · no EPGs · clean-up candidate</div></div><a class="btn sm" style="margin-left:auto" href="/assistant?q=${encodeURIComponent("Tell me about " + b.name)}">Ask</a></div>`));
    $("#attention").innerHTML = items.join("") || '<div class="empty">All clear.</div>';
  }

  // ------------------------------------------------------------------ topology
  const ROLE = { spine: ["#E3051B", "Spine"], leaf: ["#2E3338", "Leaf"], "border-leaf": ["#5E656C", "Border leaf"], controller: ["#1F63C9", "APIC"] };
  let TOPO = null;
  async function topology() {
    TOPO = await api("/api/topology");
    const draw = (mode) => {
      $("#legend-phys").classList.toggle("hidden", mode !== "physical");
      $("#tenant-pick").classList.toggle("hidden", mode !== "logical");
      mode === "physical" ? drawPhysical() : drawLogical(TOPO.logical.find((t) => t.tenant !== "common")?.tenant);
    };
    $$("#topo-mode button").forEach((b) => (b.onclick = () => { $$("#topo-mode button").forEach((x) => x.classList.toggle("on", x === b)); draw(b.dataset.mode); }));
    $("#topo-refresh").onclick = async () => { await api("/api/inventory/sync", { method: "POST" }).catch(() => {}); TOPO = await api("/api/topology"); draw($("#topo-mode .on").dataset.mode); toast("Topology refreshed"); };
    $("#tenant-pick").innerHTML = TOPO.logical.map((t) => `<button data-t="${esc(t.tenant)}">${esc(t.tenant)}</button>`).join("");
    $$("#tenant-pick button").forEach((b) => (b.onclick = () => drawLogical(b.dataset.t)));
    draw("physical");
  }
  function drawPhysical() {
    const nodes = TOPO.nodes, links = TOPO.links;
    const rows = { spine: nodes.filter((n) => n.role === "spine"), leaf: nodes.filter((n) => n.role === "leaf" || n.role === "border-leaf"), controller: nodes.filter((n) => n.role === "controller") };
    const BW = 132, BH = 54, GAP = 18;
    const W = Math.max(940, rows.leaf.length * (BW + GAP) + 200);
    const Y = { spine: 60, leaf: 250, controller: 440 }, pos = {};
    for (const [r, list] of Object.entries(rows)) {
      const total = list.length * BW + (list.length - 1) * GAP, x0 = 120 + (W - 120 - total) / 2;
      list.forEach((n, i) => (pos[n.id] = { x: x0 + i * (BW + GAP), y: Y[r] }));
    }
    let s = `<svg width="100%" viewBox="0 0 ${W} 530" style="min-width:720px" font-family="Inter,Segoe UI,sans-serif">`;
    s += `<text x="24" y="${Y.spine + 32}" font-size="11" fill="#9AA1A8" font-weight="700" letter-spacing="1.5">SPINES</text><text x="24" y="${Y.leaf + 32}" font-size="11" fill="#9AA1A8" font-weight="700" letter-spacing="1.5">LEAVES</text><text x="24" y="${Y.controller + 32}" font-size="11" fill="#9AA1A8" font-weight="700" letter-spacing="1.5">CONTROLLERS</text>`;
    links.forEach((l, i) => {
      const a = pos[l.a], b = pos[l.b]; if (!a || !b) return;
      const [top, bot] = a.y < b.y ? [a, b] : [b, a];
      const x1 = top.x + BW / 2, y1 = top.y + BH, x2 = bot.x + BW / 2, y2 = bot.y, my = (y1 + y2) / 2;
      const down = l.status !== "up";
      s += `<path class="lnk" data-a="${l.a}" data-b="${l.b}" d="M${x1} ${y1} C ${x1} ${my}, ${x2} ${my}, ${x2} ${y2}" fill="none" stroke="${down ? "#E3051B" : "#B9BFC5"}" stroke-width="${down ? 2.5 : 1.6}" ${down ? 'stroke-dasharray="6 5"' : ""} opacity=".9"><title>${l.a} ${l.a_port} ↔ ${l.b} ${l.b_port} · ${l.speed} · ${l.status}</title></path>`;
    });
    nodes.forEach((n) => {
      const p = pos[n.id]; if (!p) return;
      const [color, label] = ROLE[n.role] || ["#999", n.role];
      const stc = n.status === "up" ? "#0E8A5F" : n.status === "degraded" ? "#B86E00" : "#E3051B";
      s += `<g class="node-g" data-id="${n.id}" transform="translate(${p.x},${p.y})">
        <rect class="box" width="${BW}" height="${BH}" rx="12" fill="#fff" stroke="#DADDE1"/>
        <rect width="6" height="${BH}" rx="3" fill="${color}"/>
        <text x="16" y="22" font-size="12.5" font-weight="700" fill="#1B1F23">${esc(n.name.replace("vf-dc1-", ""))}</text>
        <text x="16" y="40" font-size="11" fill="#5E656C">${label} · ${n.id}</text>
        <circle cx="${BW - 14}" cy="16" r="5" fill="${stc}"/></g>`;
    });
    $("#topo-canvas").innerHTML = s + "</svg>";
    $$("#topo-canvas .node-g").forEach((g) => (g.onclick = () => selectNode(g.dataset.id)));
  }
  function selectNode(id) {
    const n = TOPO.nodes.find((x) => x.id === id);
    $$("#topo-canvas .node-g").forEach((g) => g.classList.toggle("sel", g.dataset.id === id));
    $$("#topo-canvas .lnk").forEach((p) => { const on = p.dataset.a === id || p.dataset.b === id; p.setAttribute("opacity", on ? "1" : ".25"); if (on && p.getAttribute("stroke") !== "#E3051B") p.setAttribute("stroke", "#5E656C"); else if (p.getAttribute("stroke") === "#5E656C") p.setAttribute("stroke", "#B9BFC5"); });
    const links = TOPO.links.filter((l) => l.a === id || l.b === id);
    const name = (x) => TOPO.nodes.find((k) => k.id === x)?.name || x;
    $("#topo-panel").innerHTML = `<div style="display:flex;align-items:center;gap:10px"><div class="icon-tile" style="background:${(ROLE[n.role] || ["#999"])[0]};color:#fff">${SVG.check.replace('stroke-width="3"', 'stroke-width="2"')}</div><div><h3>${esc(n.name)}</h3><div class="small muted">${(ROLE[n.role] || [0, n.role])[1]} · node ${n.id}</div></div></div>
      <dl><dt>Status</dt><dd>${statusDot(n.status)}</dd><dt>Model</dt><dd>${esc(n.model)}</dd><dt>Software</dt><dd>${esc(n.version)}</dd><dt>OOB mgmt</dt><dd class="mono">${esc(n.oob_ip)}</dd><dt>Serial</dt><dd class="mono">${esc(n.serial)}</dd><dt>Pod</dt><dd>${esc(n.pod)}</dd></dl>
      <h3 style="margin-top:18px">Links (${links.length})</h3>
      <div style="margin-top:8px;display:flex;flex-direction:column;gap:6px">${links.map((l) => { const other = l.a === id ? l.b : l.a; const lp = l.a === id ? l.a_port : l.b_port, rp = l.a === id ? l.b_port : l.a_port;
        return `<div class="small" style="display:flex;justify-content:space-between;gap:8px;padding:7px 10px;border:1px solid var(--mist);border-radius:8px"><span><span class="mono">${lp}</span> → ${esc(name(other).replace("vf-dc1-", ""))} <span class="mono">${rp}</span></span>${l.status === "up" ? '<span class="pill green">up</span>' : '<span class="pill red">down</span>'}</div>`; }).join("")}</div>
      <a class="btn sm" style="margin-top:14px" href="/assistant?q=${encodeURIComponent("Tell me about " + n.name)}">Ask the assistant</a>`;
  }
  const statusDot = (s) => `<span class="pill ${s === "up" ? "green" : s === "degraded" ? "amber" : "red"}"><span class="dot"></span>${s}</span>`;
  function drawLogical(tenant) {
    $$("#tenant-pick button").forEach((b) => b.classList.toggle("on", b.dataset.t === tenant));
    const t = TOPO.logical.find((x) => x.tenant === tenant); if (!t) return;
    const X = { tn: 30, vrf: 250, bd: 480, epg: 760 }, BW = 190, BH = 46, RG = 14;
    let y = 30, s = "", boxes = [], edges = [];
    const box = (x, yy, title, sub, color, kind, data, dashed) => {
      boxes.push(`<g class="node-g" data-kind="${kind}" data-name="${esc(data)}" transform="translate(${x},${yy})"><rect class="box" width="${BW}" height="${BH}" rx="11" fill="#fff" stroke="${dashed ? "#B86E00" : "#DADDE1"}" ${dashed ? 'stroke-dasharray="5 4"' : ""}/><rect width="6" height="${BH}" rx="3" fill="${color}"/><text x="16" y="20" font-size="12.5" font-weight="700" fill="#1B1F23">${esc(title)}</text><text x="16" y="36" font-size="11" fill="#5E656C">${esc(sub)}</text></g>`);
      return { x, y: yy };
    };
    const curve = (a, b, color = "#C3C8CE") => edges.push(`<path d="M${a.x + BW} ${a.y + BH / 2} C ${a.x + BW + 40} ${a.y + BH / 2}, ${b.x - 40} ${b.y + BH / 2}, ${b.x} ${b.y + BH / 2}" fill="none" stroke="${color}" stroke-width="1.6"/>`);
    const vrfPos = [];
    for (const v of t.vrfs) {
      const startY = y, bdPos = [];
      for (const bd of v.bds) {
        const bdY = y;
        const epgs = bd.epgs.length ? bd.epgs : [];
        epgs.forEach((e, i) => { const p = box(X.epg, y, e.name, `EPG · ${e.ap}`, "#6A43C9", "epg", e.name); if (i < epgs.length - 1) y += BH + RG; e._p = p; });
        const bp = box(X.bd, epgs.length ? bdY + ((y - bdY) / 2) : bdY, bd.name, bd.subnets.join(", ") || "no subnet", "#2E3338", "bd", bd.name, !epgs.length);
        epgs.forEach((e) => curve(bp, e._p, "#CBB9F0"));
        bdPos.push(bp); y += BH + RG * 1.6;
      }
      for (const o of t.l3outs.filter((o) => o.vrf === v.name)) { bdPos.push(box(X.bd, y, o.name, "L3Out · external routing", "#0E8A5F", "l3out", o.name)); y += BH + RG * 1.6; }
      if (!bdPos.length) y += BH + RG;
      const vp = box(X.vrf, bdPos.length ? (startY + y - BH - RG * 1.6) / 2 : startY, v.name, `VRF · ${v.bds.length} BD`, "#E3051B", "vrf", v.name);
      bdPos.forEach((b) => curve(vp, b, "#F5A3AC"));
      vrfPos.push(vp); y += RG;
    }
    const tp = box(X.tn, Math.max(30, (30 + y - BH) / 2 - 10), t.tenant, t.descr || "Tenant", "#17191C", "tenant", t.tenant);
    vrfPos.forEach((v) => curve(tp, v, "#9AA1A8"));
    const W = X.epg + BW + 40, H = Math.max(y + 20, 300);
    s = `<svg width="100%" viewBox="0 0 ${W} ${H}" style="min-width:820px" font-family="Inter,Segoe UI,sans-serif">
      ${["TENANT", "VRF", "BRIDGE DOMAIN / L3OUT", "EPG"].map((l, i) => `<text x="${Object.values(X)[i]}" y="18" font-size="10.5" fill="#9AA1A8" font-weight="700" letter-spacing="1.4">${l}</text>`).join("")}
      ${edges.join("")}${boxes.join("")}</svg>`;
    $("#topo-canvas").innerHTML = s;
    $$("#topo-canvas .node-g").forEach((g) => (g.onclick = () => {
      $$("#topo-canvas .node-g").forEach((x) => x.classList.toggle("sel", x === g));
      const kind = g.dataset.kind, name = g.dataset.name;
      const label = { tenant: "Tenant", vrf: "VRF", bd: "Bridge Domain", epg: "EPG", l3out: "L3Out" }[kind];
      let dl = "";
      if (kind === "bd") { const bd = t.vrfs.flatMap((v) => v.bds.map((b) => ({ ...b, vrf: v.name }))).find((b) => b.name === name);
        dl = `<dt>VRF</dt><dd>${esc(bd.vrf)}</dd><dt>Subnets</dt><dd class="mono">${bd.subnets.map(esc).join("<br>") || "none"}</dd><dt>EPGs</dt><dd>${bd.epgs.map((e) => esc(e.name)).join(", ") || '<span class="pill amber">unused</span>'}</dd>`; }
      if (kind === "vrf") { const v = t.vrfs.find((x) => x.name === name); dl = `<dt>Bridge domains</dt><dd>${v.bds.map((b) => esc(b.name)).join(", ") || "none"}</dd>`; }
      if (kind === "epg") { const bd = t.vrfs.flatMap((v) => v.bds).find((b) => b.epgs.some((e) => e.name === name)); const e = bd.epgs.find((x) => x.name === name);
        dl = `<dt>App profile</dt><dd>${esc(e.ap)}</dd><dt>Bridge domain</dt><dd>${esc(bd.name)}</dd><dt>Gateway</dt><dd class="mono">${bd.subnets.map(esc).join("<br>")}</dd>`; }
      $("#topo-panel").innerHTML = `<h3>${esc(name)}</h3><div class="small muted">${label} in ${esc(t.tenant)}</div><dl>${dl}</dl>
        <a class="btn sm" style="margin-top:14px" href="/assistant?q=${encodeURIComponent("Tell me about " + name)}">Ask the assistant</a>
        ${can("change.create") && ["bd", "epg"].includes(kind) ? `<a class="btn sm danger-outline" style="margin-top:14px" href="/changes/new?kind=delete_object&tenant=${encodeURIComponent(t.tenant)}&otype=${kind === "bd" ? "bridge_domain" : "epg"}&name=${encodeURIComponent(name)}">Plan deletion</a>` : ""}`;
    }));
  }

  // ------------------------------------------------------------------ inventory
  async function inventory() {
    const snap = await api("/api/inventory/snapshot");
    $("#inv-meta").textContent = `Collected ${ago(snap.collected_at)} · source ${snap.source} · controller ${snap.controller_version}`;
    const render = (q = "") => {
      q = q.toLowerCase();
      const match = (...xs) => !q || xs.some((x) => String(x).toLowerCase().includes(q));
      $("#tenants").innerHTML = snap.tenants.map((t, i) => {
        const vrfs = t.vrfs.filter((v) => match(v.name, t.name)), bds = t.bridge_domains.filter((b) => match(b.name, b.vrf, t.name, ...b.subnets.map((s) => s.gateway)));
        const aps = t.app_profiles.filter((a) => match(a.name, t.name, ...a.epgs.map((e) => e.name + e.bridge_domain)));
        if (q && !vrfs.length && !bds.length && !aps.length) return "";
        const used = new Set(t.app_profiles.flatMap((a) => a.epgs.map((e) => e.bridge_domain)));
        return `<div class="tenant ${q || i === 1 ? "open" : ""}"><div class="tenant-h"><div class="icon-tile dark"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 21V8l9-5 9 5v13"/><path d="M9 21v-6h6v6"/></svg></div>
          <div><b>${esc(t.name)}</b><div class="small muted">${esc(t.descr)}</div></div>
          <div style="display:flex;gap:6px;margin-left:16px"><span class="pill neutral">${t.vrfs.length} VRF</span><span class="pill neutral">${t.bridge_domains.length} BD</span><span class="pill neutral">${t.app_profiles.reduce((n, a) => n + a.epgs.length, 0)} EPG</span></div>
          <svg class="chev" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 6l6 6-6 6"/></svg></div>
          <div class="tenant-b"><div class="obj-grid">
            <div class="obj-col"><h4>VRFs &amp; L3Outs</h4>${vrfs.map((v) => `<div class="obj"><div class="on">${esc(v.name)}</div><div class="os">enforcement ${esc(v.policy_enforcement)}</div></div>`).join("")}${t.l3outs.map((o) => `<div class="obj"><div class="on">${esc(o.name)}</div><div class="os">L3Out · VRF ${esc(o.vrf)}</div></div>`).join("")}</div>
            <div class="obj-col"><h4>Bridge domains</h4>${bds.map((b) => `<div class="obj"><div class="on">${esc(b.name)} ${used.has(b.name) || t.name === "common" ? "" : '<span class="pill amber" style="padding:1px 7px;font-size:10.5px">unused</span>'}</div><div class="os mono">${b.subnets.map((s) => esc(s.gateway)).join(", ") || "no subnet"}</div><div class="os">VRF ${esc(b.vrf)} · ${b.unicast_route ? "routing on" : "L2 only"} · ARP ${b.arp_flood ? "flood" : "unicast"}</div></div>`).join("") || '<div class="muted small">None</div>'}</div>
            <div class="obj-col"><h4>Application profiles</h4>${aps.map((a) => `<div class="obj"><div class="on">${esc(a.name)}</div>${a.epgs.map((e) => `<div class="os">• ${esc(e.name)} → ${esc(e.bridge_domain)}</div>`).join("") || '<div class="os">no EPGs</div>'}</div>`).join("") || '<div class="muted small">None</div>'}</div>
          </div></div></div>`;
      }).join("") || '<div class="empty">No match.</div>';
      $$(".tenant-h").forEach((h) => (h.onclick = () => h.parentElement.classList.toggle("open")));
    };
    render();
    $("#inv-search").oninput = (e) => render(e.target.value);
    $("#nodes").innerHTML = snap.nodes.map((n) => `<tr><td><b>${esc(n.name.replace("vf-dc1-", ""))}</b><div class="small muted mono">${esc(n.oob_ip)}</div></td><td>${(ROLE[n.role] || [0, n.role])[1]}</td><td class="small nw">${esc(n.model.replace("APIC-SERVER-", "APIC-"))}</td><td>${statusDot(n.status)}</td></tr>`).join("");
    $("#sync")?.addEventListener("click", async (e) => { btnBusy(e.currentTarget, true, "Collecting…"); try { await api("/api/inventory/sync", { method: "POST" }); toast("Configuration re-collected"); setTimeout(() => location.reload(), 500); } catch (x) { toast(x.message, "err"); btnBusy(e.currentTarget, false); } });
    $("#lab-reset")?.addEventListener("click", async () => {
      if (!(await modal({ title: "Reset mock lab?", body: "<p>This restores the mock fabric to its original seed configuration. Change history is kept.</p>", confirm: "Reset lab", danger: true }))) return;
      await api("/api/admin/lab-reset", { method: "POST" }); toast("Mock lab restored"); setTimeout(() => location.reload(), 500);
    });
  }

  function proposalSummary(p) {
    const label = { create_bridge_domain: "Create Bridge Domain", create_app_profile: "Create Application Profile", delete_object: "Delete object" }[p.kind];
    const rows = [["Action", label], ["Tenant", p.tenant], ["Name", p.name]];
    if (p.vrf) rows.push(["VRF", p.vrf]);
    if (p.subnets?.length) rows.push(["Subnets", p.subnets.join(", ")]);
    if (p.epgs?.length) rows.push(["EPGs", p.epgs.map((e) => `${e.name} → ${e.bridge_domain}`).join(", ")]);
    if (p.object_type) rows.push(["Object type", p.object_type.replace("_", " ")]);
    if (p.app_profile) rows.push(["App profile", p.app_profile]);
    return `<div style="display:grid;grid-template-columns:110px 1fr;gap:4px 10px;margin:8px 0 12px;font-size:13px">${rows.map(([k, v]) => `<span class="muted">${esc(k)}</span><b>${esc(v)}</b>`).join("")}</div>`;
  }

  // ------------------------------------------------------------------ assistant
  async function assistant() {
    const msgs = $("#msgs"), q = $("#q"), history = [];
    const prov = AH.system.llm_provider === "anthropic" ? "Claude" : AH.system.llm_provider === "azure" ? "Azure OpenAI" : "Offline engine";
    const add = (role, html, meta = "") => {
      const d = document.createElement("div");
      d.className = `msg ${role}`;
      d.innerHTML = `<div class="av">${role === "bot" ? '<img src="/static/img/logo-mark.svg" alt="">' : esc(AH.user.full_name[0])}</div><div style="min-width:0"><div class="bubble-t">${html}</div>${meta ? `<div class="meta">${meta}</div>` : ""}</div>`;
      msgs.appendChild(d); msgs.scrollTop = msgs.scrollHeight; return d;
    };
    add("bot", md(`Hi ${AH.user.full_name.split(" ")[0]}, I'm the AutoHero assistant. I know the current configuration of **${AH.system.target}**.\n\nAsk me about tenants, bridge domains, EPGs, subnets or fabric health. I can also **prepare a draft change**, which then goes through dry-run and approval.`), prov);
    const ask = async (text) => {
      text = text.trim(); if (!text) return;
      add("me", esc(text)); q.value = "";
      const wait = add("bot", '<span class="typing"><i></i><i></i><i></i></span>');
      $("#send").disabled = true;
      try {
        const r = await api("/api/chat", { method: "POST", body: { question: text, history } });
        wait.remove();
        history.push({ role: "user", content: text }, { role: "assistant", content: r.answer });
        let html = md(r.answer);
        if (r.proposal) {
          html += `<div class="proposal"><div class="k">Draft change proposal</div>${proposalSummary(r.proposal)}
            ${can("change.create") ? '<button class="btn primary sm" data-create>Create draft &amp; run dry-run</button> <button class="btn sm" data-edit>Edit in form</button>' : '<div class="small muted">Your role cannot create changes. Ask an engineer.</div>'}</div>`;
        }
        const meta = `${r.provider === "offline" ? "Offline engine" : esc(r.model)}${r.masked ? ` · ${r.masked} value(s) masked before sending` : ""}`;
        const el = add("bot", html, meta);
        el.querySelector("[data-create]")?.addEventListener("click", async (e) => {
          btnBusy(e.currentTarget, true, "Running dry-run…");
          try { const cr = await api("/api/changes", { method: "POST", body: { intent: r.proposal, justification: `Drafted with the assistant: "${text}"` } }); location.href = `/changes/${cr.id}`; }
          catch (x) { toast(x.message, "err"); btnBusy(e.currentTarget, false); }
        });
        el.querySelector("[data-edit]")?.addEventListener("click", () => { sessionStorage.setItem("ah-intent", JSON.stringify(r.proposal)); location.href = "/changes/new?from=assistant"; });
      } catch (x) { wait.remove(); add("bot", `<span style="color:var(--red-700)">${esc(x.message)}</span>`); }
      $("#send").disabled = false; q.focus();
    };
    $("#composer").onsubmit = (e) => { e.preventDefault(); ask(q.value); };
    q.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(q.value); } });
    $$("#suggest button").forEach((b) => (b.onclick = () => ask(b.textContent)));
    const pre = new URLSearchParams(location.search).get("q"); if (pre) ask(pre);
  }

  // ------------------------------------------------------------------ changes list
  async function changesList() {
    const rows = await navCount();
    const groups = { all: () => true, approval: (r) => r.status === "pending_approval", ready: (r) => ["approved", "validated"].includes(r.status),
      done: (r) => ["completed", "rolled_back", "rejected"].includes(r.status), problem: (r) => ["failed", "validation_failed"].includes(r.status) };
    const render = (f) => {
      const list = rows.filter(groups[f]);
      $("#ch-rows").innerHTML = list.map((r) => `<tr class="click" data-id="${r.id}"><td class="ref">${r.ref}</td><td><b>${esc(r.title)}</b><div class="small muted">${esc(r.target)}</div></td><td>${statusPill(r.status)}</td><td>${riskPill(r.risk)}</td><td>${esc(r.requested_by)}</td><td>${esc(r.approved_by || "–")}</td><td class="small muted">${ago(r.updated_at)}</td></tr>`).join("")
        || `<tr><td colspan="7" class="empty">Nothing here.</td></tr>`;
      $$("#ch-rows tr.click").forEach((tr) => (tr.onclick = () => (location.href = `/changes/${tr.dataset.id}`)));
    };
    $$("#ch-filter button").forEach((b) => (b.onclick = () => { $$("#ch-filter button").forEach((x) => x.classList.toggle("on", x === b)); render(b.dataset.f); }));
    render("all");
  }

  // ------------------------------------------------------------------ new change
  async function changeNew() {
    if (!$("#types")) return;
    navCount();
    const snap = await api("/api/inventory/snapshot");
    const params = new URLSearchParams(location.search);
    let prefill = null;
    if (params.get("from") === "assistant") { try { prefill = JSON.parse(sessionStorage.getItem("ah-intent")); } catch { /* ignore */ } }
    if (params.get("kind")) prefill = { kind: params.get("kind"), tenant: params.get("tenant"), object_type: params.get("otype"), name: params.get("name") };
    let kind = prefill?.kind || "create_bridge_domain";
    const tenants = snap.tenants.filter((t) => t.name !== "common");
    const T = () => snap.tenants.find((t) => t.name === $("#f-tenant").value);
    $("#f-tenant").innerHTML = tenants.map((t) => `<option>${esc(t.name)}</option>`).join("");
    const fillTenant = () => {
      const t = T(), common = snap.tenants.find((x) => x.name === "common");
      $("#f-vrf").innerHTML = t.vrfs.map((v) => `<option>${esc(v.name)}</option>`).join("") + (common ? common.vrfs.map((v) => `<option value="${esc(v.name)}">common/${esc(v.name)}</option>`).join("") : "");
      $$("#epg-rows select").forEach((s) => (s.innerHTML = bdOptions()));
      fillObjects();
    };
    const bdOptions = () => T().bridge_domains.map((b) => `<option>${esc(b.name)}</option>`).join("");
    const fillObjects = () => {
      const t = T(), ot = $("#f-otype").value;
      const opts = ot === "bridge_domain" ? t.bridge_domains.map((b) => [b.name, b.name, ""]) : ot === "app_profile" ? t.app_profiles.map((a) => [a.name, `${a.name} (${a.epgs.length} EPGs)`, ""]) : t.app_profiles.flatMap((a) => a.epgs.map((e) => [e.name, `${a.name} / ${e.name}`, a.name]));
      $("#f-obj").innerHTML = opts.map(([v, l, ap]) => `<option value="${esc(v)}" data-ap="${esc(ap)}">${esc(l)}</option>`).join("");
    };
    const addEpg = (name = "", bd = "") => {
      const row = document.createElement("div"); row.className = "epg-row";
      row.innerHTML = `<input class="input" placeholder="EPG name, e.g. NRF_EPG" value="${esc(name)}"><select class="input">${bdOptions()}</select><button class="btn ghost" type="button" title="Remove">✕</button>`;
      if (bd) row.querySelector("select").value = bd;
      row.querySelector("button").onclick = () => row.remove();
      $("#epg-rows").appendChild(row);
    };
    const setKind = (k) => {
      kind = k;
      $$("#types .type-card").forEach((c) => c.classList.toggle("on", c.dataset.kind === k));
      $$("[data-for]").forEach((el) => el.classList.toggle("hidden", !el.dataset.for.split(" ").includes(k)));
      $("#w-name").classList.toggle("hidden", k === "delete_object");
      $("#f-name").placeholder = k === "create_app_profile" ? "e.g. NRF_AP" : "e.g. CACHE_BD";
      $("#name-help").textContent = k === "create_app_profile" ? "Convention: end with _AP" : "Convention: end with _BD";
      $("#create").innerHTML = (k === "delete_object" ? SVG.trash : SVG.flask) + (k === "delete_object" ? "Plan deletion &amp; run dry-run" : "Create &amp; run dry-run");
    };
    $$("#types .type-card").forEach((c) => (c.onclick = () => setKind(c.dataset.kind)));
    $("#f-tenant").onchange = fillTenant; $("#f-otype").onchange = fillObjects;
    $("#add-epg").onclick = () => addEpg();
    if (prefill?.tenant) $("#f-tenant").value = prefill.tenant;
    fillTenant(); setKind(kind);
    if (prefill) {
      if (prefill.name) $("#f-name").value = prefill.name;
      if (prefill.vrf) $("#f-vrf").value = prefill.vrf;
      if (prefill.subnets) $("#f-subnets").value = prefill.subnets.join(", ");
      if (prefill.descr) $("#f-descr").value = prefill.descr;
      (prefill.epgs || []).forEach((e) => addEpg(e.name, e.bridge_domain));
      if (prefill.object_type) { $("#f-otype").value = prefill.object_type; fillObjects(); $("#f-obj").value = prefill.name; }
    }
    if (kind === "create_app_profile" && !$("#epg-rows").children.length) addEpg();
    if (!$("#epg-rows").children.length) addEpg();
    $("#create").onclick = async (e) => {
      const tenant = $("#f-tenant").value, descr = $("#f-descr").value.trim();
      let intent;
      if (kind === "create_bridge_domain") intent = { kind, tenant, name: $("#f-name").value.trim(), vrf: $("#f-vrf").value, descr,
        subnets: $("#f-subnets").value.split(",").map((s) => s.trim()).filter(Boolean), unicast_route: $("#f-ucast").checked, arp_flood: $("#f-arp").checked, l2_unknown_unicast: $("#f-l2").value };
      else if (kind === "create_app_profile") intent = { kind, tenant, name: $("#f-name").value.trim(), descr,
        epgs: $$("#epg-rows .epg-row").map((r) => ({ name: r.querySelector("input").value.trim(), bridge_domain: r.querySelector("select").value })).filter((x) => x.name) };
      else { const o = $("#f-obj").selectedOptions[0]; if (!o) return toast("Pick an object to delete", "err");
        intent = { kind, tenant, object_type: $("#f-otype").value, name: o.value, app_profile: o.dataset.ap || "" }; }
      if (kind !== "delete_object" && !intent.name) return toast("Give the object a name", "err");
      btnBusy(e.currentTarget, true, "Running dry-run…");
      try { const cr = await api("/api/changes", { method: "POST", body: { intent, justification: $("#f-just").value.trim() } }); sessionStorage.removeItem("ah-intent"); location.href = `/changes/${cr.id}`; }
      catch (x) { toast(x.message, "err"); btnBusy(e.currentTarget, false); }
    };
  }

  // ------------------------------------------------------------------ change detail
  async function changeDetail(id) {
    navCount();
    let cr = await api(`/api/changes/${id}`);
    let view = "diff";
    const render = () => {
      document.title = `${cr.ref} · AutoHero`;
      $("#crumb-ref").textContent = cr.ref; $("#cr-title").textContent = cr.title; $("#cr-ref").textContent = cr.ref;
      $("#cr-status").innerHTML = statusPill(cr.status); $("#cr-risk").innerHTML = riskPill(cr.risk);
      $("#cr-meta").textContent = `Requested by ${cr.requested_by} · ${fmt(cr.created_at)}${cr.approved_by ? ` · approved by ${cr.approved_by}` : ""}`;
      stepper(); actions(); body(); checks(); impact(); timeline(); post();
    };
    const stepper = () => {
      const s = cr.status;
      const idx = { draft: 1, validation_failed: 1, validated: 2, pending_approval: 2, rejected: 2, approved: 3, executing: 3, failed: 3, completed: 5, rolled_back: 5 }[s];
      const bad = { validation_failed: 1, rejected: 2, failed: 3 }[s];
      const steps = [["Draft", "created"], ["Dry-run", s === "validation_failed" ? "blocked" : "pre-checks"], ["Approval", s === "rejected" ? "rejected" : "four-eyes"],
        ["Execute", s === "failed" ? "failed" : cr.result?.mode === "live" ? "live fabric" : "push"], [s === "rolled_back" ? "Rolled back" : "Verified", s === "rolled_back" ? "restored" : "post-checks"]];
      $("#stepper").innerHTML = steps.map(([l, sub], i) => {
        const cls = bad === i ? "bad" : i < idx ? "done" : i === idx ? "current" : "";
        const inner = cls === "bad" ? SVG.x : cls === "done" ? SVG.check : i + 1;
        return `<div class="step ${cls}"><div class="bubble">${inner}</div><div class="lbl">${l}</div><div class="sub">${sub}</div></div>`;
      }).join("");
    };
    const act = async (action, opts) => {
      const ok = opts ? await modal(opts) : { note: "" };
      if (!ok) return;
      const btn = $(`[data-act="${action}"]`); if (btn) btnBusy(btn, true, opts?.busy);
      try { cr = await api(`/api/changes/${id}/${action}`, { method: "POST", body: { note: ok.note } }); toast(opts?.done || "Done"); render(); navCount(); }
      catch (x) { toast(x.message, "err"); if (btn) btnBusy(btn, false); }
    };
    const actions = () => {
      const me = AH.user.username, s = cr.status, out = [];
      if (["draft", "validated", "validation_failed"].includes(s) && can("change.create")) out.push(`<button class="btn" data-act="dry-run">${SVG.flask}Re-run dry-run</button>`);
      if (s === "validated" && can("change.create") && (cr.requested_by === me || AH.user.role === "admin")) out.push(`<button class="btn primary" data-act="submit">${SVG.send}Submit for approval</button>`);
      if (s === "pending_approval") {
        if (can("change.approve") && cr.requested_by !== me) out.push(`<button class="btn danger-outline" data-act="reject">${SVG.x}Reject</button><button class="btn success" data-act="approve">${SVG.check}Approve</button>`);
        else out.push(`<span class="small muted">Waiting for a second person with the approver role.</span>`);
      }
      if (s === "approved" && can("change.execute")) out.push(`<button class="btn primary" data-act="execute">${SVG.play}Execute on ${AH.system.live ? "LIVE fabric" : "mock lab"}</button>`);
      if (s === "completed" && can("change.rollback")) out.push(`<button class="btn danger-outline" data-act="rollback">${SVG.undo}Roll back</button>`);
      $("#cr-actions").innerHTML = out.join("");
      const live = AH.system.live;
      const map = {
        "dry-run": null,
        submit: { title: "Submit for approval?", body: "<p>A second person with the <b>approver</b> role will review the dry-run before anything is executed.</p>", confirm: "Submit", withNote: true, done: "Submitted for approval", busy: "Submitting…" },
        approve: { title: `Approve ${cr.ref}?`, body: `<p>You confirm you reviewed the diff and pre-checks for <b>${esc(cr.title)}</b>.</p>`, confirm: "Approve", withNote: true, done: "Change approved" },
        reject: { title: `Reject ${cr.ref}?`, body: "<p>The requester will see your note.</p>", confirm: "Reject", danger: true, withNote: true, done: "Change rejected" },
        execute: { title: live ? "Execute on the LIVE fabric?" : "Execute on the mock lab?", body: `<p>AutoHero will re-check the current state (drift check), push the payload, then verify with post-checks. If post-checks fail it rolls back automatically.</p>${live ? '<p class="err-box">This changes production configuration.</p>' : ""}`, confirm: "Execute now", done: "Change executed", busy: "Executing…" },
        rollback: { title: `Roll back ${cr.ref}?`, body: "<p>The prepared rollback payload restores the exact previous state, then post-checks confirm it.</p>", confirm: "Roll back", danger: true, withNote: true, done: "Rolled back", busy: "Rolling back…" },
      };
      $$("#cr-actions [data-act]").forEach((b) => (b.onclick = () => act(b.dataset.act, map[b.dataset.act] ? map[b.dataset.act] : { title: "Re-run dry-run?", body: "<p>Re-plan against the current fabric state.</p>", confirm: "Run dry-run", done: "Dry-run complete", busy: "Running…" })));
    };
    const body = () => {
      const p = cr.plan || {};
      $$("#view-seg button").forEach((b) => (b.onclick = () => { view = b.dataset.v; $$("#view-seg button").forEach((x) => x.classList.toggle("on", x === b)); body(); }));
      let html = "";
      if (view === "diff") {
        html = p.diff ? `<div class="diff">${p.diff.split("\n").map((l) => `<div class="${l.startsWith("+++") || l.startsWith("---") ? "meta" : l.startsWith("@@") ? "hunk" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : ""}">${esc(l) || " "}</div>`).join("")}</div>`
          : `<div class="empty">No diff: the dry-run was blocked before simulation. See the failed pre-checks.</div>`;
      } else if (view === "payload") html = `<div class="small muted" style="padding:10px 20px">${esc(p.endpoint || "")} on ${esc(cr.target)}</div><pre class="code">${esc(JSON.stringify(p.payload, null, 2))}</pre>`;
      else if (view === "rollback") html = `<div class="small muted" style="padding:10px 20px">Applied automatically if post-checks fail, or on demand.</div><pre class="code">${esc(JSON.stringify(p.rollback_payload, null, 2))}</pre>`;
      else html = `<div class="small muted" style="padding:10px 20px">Vendor-neutral intent${cr.justification ? ` · <i>${esc(cr.justification)}</i>` : ""}</div><pre class="code">${esc(JSON.stringify(cr.intent, null, 2))}</pre>`;
      $("#view-body").innerHTML = html;
    };
    const checkRows = (items) => items.map((c) => `<div class="check ${c.status}"><div class="ic">${c.status === "pass" ? SVG.check : c.status === "warn" ? SVG.warn : SVG.x}</div><div><div class="nm">${esc(c.name)}</div><div class="dt">${esc(c.detail)}</div></div></div>`).join("");
    const checks = () => {
      const items = cr.plan?.checks || [];
      const n = (s) => items.filter((c) => c.status === s).length;
      $("#chk-sum").innerHTML = `${n("pass")} passed · ${n("warn")} warnings · ${n("fail")} failed`;
      const order = { fail: 0, warn: 1, pass: 2 };
      $("#checks").innerHTML = checkRows([...items].sort((a, b) => order[a.status] - order[b.status]));
    };
    const impact = () => {
      const im = cr.plan?.impact || { summary: [], objects: 0 };
      $("#impact").innerHTML = `<div style="display:flex;gap:10px;align-items:center;margin-bottom:10px">${riskPill(cr.risk)}<span class="small muted">${im.objects} managed object(s) touched on ${esc(cr.target)}</span></div>` +
        (im.summary.map((s) => `<div class="mono small" style="color:${s.startsWith("+") ? "var(--green)" : "var(--red-700)"}">${esc(s)}</div>`).join("") || '<div class="muted small">No objects affected.</div>');
    };
    const timeline = () => {
      $("#timeline").innerHTML = [...cr.events].reverse().map((e) => `<div class="tl"><div class="d"></div><div><div class="w">${(STATUS[e.to] || [e.to])[0]}</div><div class="m">${esc(e.note)}</div><div class="m">${esc(e.actor)} · ${fmt(e.ts)}</div></div></div>`).join("");
    };
    const post = () => {
      const r = cr.result || {};
      const items = [...(r.post_checks || []), ...(r.rollback_checks || []).map((c) => ({ ...c, name: "After rollback: " + c.name }))];
      if (r.stage === "drift-check") items.push(...r.checks.filter((c) => c.status === "fail"));
      if (r.error) items.push({ status: "fail", name: "Target rejected the change", detail: r.error });
      $("#post-card").classList.toggle("hidden", !items.length);
      $("#post-checks").innerHTML = checkRows(items);
    };
    render();
  }

  // ------------------------------------------------------------------ audit
  async function auditPage() {
    navCount();
    const load = async () => {
      const a = $("#a-action").value;
      const rows = await api(`/api/audit${a ? `?action=${encodeURIComponent(a)}` : ""}`);
      $("#a-rows").innerHTML = rows.map((e) => `<tr><td class="small">${fmt(e.ts)}</td><td><b>${esc(e.actor)}</b></td><td class="mono small">${esc(e.action)}</td><td class="small">${esc(e.target)}</td>
        <td><span class="pill ${e.outcome === "success" ? "green" : e.outcome === "denied" || e.outcome === "blocked" ? "amber" : "red"}">${esc(e.outcome)}</span></td>
        <td class="small muted" style="max-width:420px">${esc(Object.entries(e.details || {}).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join(" · "))}</td></tr>`).join("")
        || '<tr><td colspan="6" class="empty">No events.</td></tr>';
    };
    $("#a-action").onchange = load; load();
  }

  // ------------------------------------------------------------------ router
  const path = location.pathname;
  const run = (fn) => fn().catch((e) => toast(e.message, "err"));
  if (path === "/") run(overview);
  else if (path === "/topology") run(topology);
  else if (path === "/inventory") run(inventory);
  else if (path === "/assistant") { navCount(); run(assistant); }
  else if (path === "/changes") run(changesList);
  else if (path === "/changes/new") run(changeNew);
  else if (/^\/changes\/\d+$/.test(path)) run(() => changeDetail(path.split("/").pop()));
  else if (path === "/audit") run(auditPage);
  else navCount();
})();
