// dread gui: one page, no framework, no build step. The server (dread/gui.py) answers /api/*;
// everything here is the view. Changes go through the server, which makes them as the command line does.
// The force simulation both graphs are laid out with is sim.js.

import { Sim } from "./sim.js";

const TOKEN = document.querySelector('meta[name="dread-token"]').content;
const IMAGE = new URLSearchParams(location.search).get("image") || "";
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const hex = (n) => "0x" + n.toString(16);
const hex8 = (n) => n.toString(16).padStart(8, "0");
const fnLink = (name, at) => "#/f/" + encodeURIComponent(name) + (at != null ? "@" + hex(at) : "");
const relLink = (name) => "#/r/" + encodeURIComponent(name);
const modeLink = (name) => (S.view === "program" ? progLink(name) : S.mode === "relations" ? relLink(name) : fnLink(name));
const modeHash = (mode, name) => (mode === "relations" ? relLink(name) : mode === "program" ? progLink(name) : fnLink(name));

const ORIGINS = {
  hand: { short: "hand", long: "written by hand" },
  pilot: { short: "pilot", long: "written by dread pilot" },
  lifted: { short: "lifted", long: "lifted C" },
  refused: { short: "refused", long: "refused by the lifter" },
  dropped: { short: "dropped", long: "dropped by the lift check" },
  original: { short: "original", long: "original machine code" },
};
const ANALYSES = { callers: "callers", endless: "returns", flow: "registers", texts: "texts" };

const S = {
  overview: null, functions: [], byName: new Map(), order: [], current: null, sel: -1,
  filter: "", origins: new Set(), sort: "address", showBytes: false, showC: true, by: "bytes",
  generation: 0, pending: [], view: null, tab: null, mode: "code",
};

// ---- small helpers ----

const prefs = {
  get(key, fallback) {
    try { const v = localStorage.getItem("dread-" + key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem("dread-" + key, JSON.stringify(value)); } catch { /* private window: per page only */ }
  },
};

async function api(path, params = {}, body) {
  const query = new URLSearchParams(params);
  if (IMAGE) query.set("image", IMAGE);
  const options = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json", "X-Dread-Token": TOKEN }, body: JSON.stringify(body),
  };
  const response = await fetch(path + (query.toString() ? "?" + query : ""), options);
  const data = await response.json().catch(() => ({ error: response.statusText }));
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

function size(n) {
  if (n < 1024) return n + " B";
  if (n < 1 << 20) return (n / 1024).toFixed(1) + " KiB";
  return (n / (1 << 20)).toFixed(2) + " MiB";
}

function ago(t) {
  if (!t) return "never";
  const s = Date.now() / 1000 - t;
  if (s < 60) return "just now";
  if (s < 3600) return Math.round(s / 60) + " min ago";
  if (s < 86400) return Math.round(s / 3600) + " h ago";
  return Math.round(s / 86400) + " days ago";
}

function when(t) {
  return t ? new Date(t * 1000).toLocaleString() : "";
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

let toastTimer = null;
function toast(text, error = false) {
  const box = $("#toast");
  box.textContent = text;
  box.className = "toast" + (error ? " error" : "");
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, error ? 9000 : 4500);
}

function dot(origin) {
  return `<span class="dot o-${origin}"></span>`;
}

// ---- theme ----

function applyTheme() {
  const theme = prefs.get("theme", "auto");
  if (theme === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  $("#theme").title = "Theme: " + (theme === "auto" ? "as the system" : theme) + " (t)";
  if (S.view === "overview") drawMap();
  if (S.view === "program") { P.colors = progColors(); drawProgram(); }
}

function cycleTheme() {
  const order = ["auto", "light", "dark"];
  prefs.set("theme", order[(order.indexOf(prefs.get("theme", "auto")) + 1) % 3]);
  applyTheme();
}

// ---- the function list ----

function renderChips() {
  $("#chips").innerHTML = Object.entries(ORIGINS).map(([key, o]) =>
    `<span class="chip${S.origins.has(key) ? " on" : ""}" data-origin="${key}" title="${esc(o.long)}">${dot(key)}${o.short}</span>`).join("");
}

function applyFilter() {
  const f = S.filter.trim().toLowerCase();
  const list = S.functions.filter((fn) => (!S.origins.size || S.origins.has(fn.origin)) &&
    (!f || fn.name.toLowerCase().includes(f) || hex8(fn.entry).includes(f.replace(/^0x/, ""))));
  if (S.sort === "name") list.sort((a, b) => a.name.localeCompare(b.name));
  else if (S.sort === "size") list.sort((a, b) => b.size - a.size || a.entry - b.entry);
  else list.sort((a, b) => a.entry - b.entry);
  S.order = list;
  $("#count").textContent = list.length === S.functions.length ? `${list.length} functions` : `${list.length} of ${S.functions.length}`;
  renderList();
}

const ROW = 24;
function renderList() {
  const box = $("#list"), spacer = $("#spacer");
  spacer.style.height = S.order.length * ROW + "px";
  const from = Math.max(0, Math.floor(box.scrollTop / ROW) - 12);
  const to = Math.min(S.order.length, Math.ceil((box.scrollTop + box.clientHeight) / ROW) + 12);
  const current = S.current && S.current.header.name;
  let html = "";
  for (let i = from; i < to; i++) {
    const fn = S.order[i];
    const flags = fn.failed ? " failed" : "";
    const title = `${fn.name} · ${hex(fn.entry)} · ${fn.size} bytes · ${ORIGINS[fn.origin].long}` +
      (fn.failed ? " · cases fail" : "") + (fn.missed ? " · instructions never ran" : "");
    html += `<div class="fnrow${fn.name === current ? " current" : ""}" data-i="${i}" data-name="${esc(fn.name)}" title="${esc(title)}">` +
      `${dot(fn.origin)}<span class="nm${flags}">${esc(fn.name)}</span><span class="size">${fn.size}</span></div>`;
  }
  spacer.innerHTML = html;
  for (const row of spacer.children) row.style.transform = `translateY(${row.dataset.i * ROW}px)`;
}

function revealInList(name) {
  const i = S.order.findIndex((fn) => fn.name === name);
  if (i < 0) return;
  const box = $("#list");
  if (i * ROW < box.scrollTop || (i + 1) * ROW > box.scrollTop + box.clientHeight) {
    box.scrollTop = Math.max(0, i * ROW - box.clientHeight / 2);
  }
  renderList();
}

async function loadFunctions() {
  S.functions = await api("/api/functions");
  S.byName = new Map(S.functions.map((fn) => [fn.name, fn]));
  applyFilter();
}

// ---- routing ----

// Every render takes a number; an answer that arrives after a newer render started is dropped, so
// the page always shows what the address bar says, whatever order the server's answers come in.
let renders = 0;

async function route(keep = false) {
  const hash = decodeURIComponent(location.hash.slice(1));
  const m = hash.match(/^\/([fr])\/([^@]+)(?:@(0x[0-9a-f]+))?$/i);
  const p = hash.match(/^\/p(?:\/(.+))?$/);
  if (!keep) $("#side").classList.remove("open");
  if (m) await showFunction(m[2], m[3] ? parseInt(m[3], 16) : null, keep, m[1] === "r" ? "relations" : "code");
  else if (p) await showProgram(p[1] || null, keep);
  else await showOverview(keep);
}

// ---- the overview ----

async function showOverview(keep = false) {
  const mine = ++renders;
  let o;
  try {
    o = await api("/api/overview");
  } catch (e) {
    if (mine === renders) showError(e.message);
    return;
  }
  if (mine !== renders) return;
  stopRelations();
  const scroll = $("#main").scrollTop, again = keep && S.view === "overview";
  S.view = "overview";
  S.overview = o;
  S.current = null;
  renderList();
  document.title = "DREAD · " + o.target.title;
  const img = o.image;
  const main = $("#main");
  main.innerHTML = `
    <div class="ov">
      <h1>${esc(o.target.title)}</h1>
      <div class="sub">image <b>${esc(img.name)}</b> · ${esc(img.arch)}${img.isa ? " " + esc(img.isa) : ""} ·
        <span class="mono">${hex(img.base)}–${hex(img.end)}</span> · ${size(img.end - img.base)} · ${img.functions} functions</div>
      <section class="section">
        <div class="section-head"><h2>Origins</h2><span class="grow"></span>
          <div class="seg" id="by"><button data-by="bytes">bytes</button><button data-by="functions">functions</button></div></div>
        <div class="stack" id="stack"></div>
        <div class="legend" id="legend"></div>
      </section>
      <section class="section">
        <div class="section-head"><h2>Whole program</h2><span class="grow"></span>
          <a class="button" href="#/p" title="The whole program (p)">Open the graph</a></div>
        <div class="legend-line">its functions in the groups dread groups finds from their code (shared memory, texts, devices,
          neighbours), joined by the calls between them; a group opens into its functions</div>
      </section>
      <section class="section">
        <div class="section-head"><h2>Image map</h2><span class="grow"></span>
          <span class="legend-line">point at a function to name it, click to open it</span></div>
        <canvas class="map" id="map"></canvas>
        <div class="map-scale"><span>${hex(img.base)}</span><span>${hex(img.end)}</span></div>
      </section>
      <div class="cards">${refusedCard(o)}${testsCard(o)}${droppedCard(o)}${runsCard(o)}</div>
    </div>`;
  main.scrollTop = again ? scroll : 0;
  renderOrigins();
  drawMap();
  watch(o.status);
}

function renderOrigins() {
  const o = S.overview, by = S.by;
  const total = Object.values(o.counts).reduce((t, c) => t + c[by], 0) || 1;
  const stack = $("#stack"), legend = $("#legend");
  stack.innerHTML = "";
  for (const key of Object.keys(ORIGINS)) {
    const part = document.createElement("div");
    part.style.width = (100 * o.counts[key][by] / total) + "%";
    part.style.background = `var(--o-${key})`;
    part.title = `${ORIGINS[key].long}: ${o.counts[key].functions} functions, ${size(o.counts[key].bytes)}`;
    stack.appendChild(part);
  }
  legend.innerHTML = Object.entries(ORIGINS).map(([key, info]) => {
    const c = o.counts[key];
    const pct = (100 * c[by] / total).toFixed(c[by] && 100 * c[by] / total < 1 ? 1 : 0);
    const hint = key === "pilot" ? "checked by the tools, read by nobody yet; " : "";
    return `<div class="item" data-origin="${key}" title="${hint}show only these in the list">${dot(key)}<span class="label">${esc(info.long)}</span>` +
      `<span class="num">${c.functions} · ${size(c.bytes)} · ${pct}%</span></div>`;
  }).join("");
  for (const b of $("#by").children) b.classList.toggle("on", b.dataset.by === by);
}

function refusedCard(o) {
  const count = o.refused.reduce((t, g) => t + g.functions.length, 0);
  const body = o.refused.length ? o.refused.map((g, i) => `
    <div class="group${i < 2 ? " open" : ""}">
      <div class="head"><span class="r">${esc(g.reason)}</span><span class="num">${g.functions.length}</span></div>
      <div class="names"${i < 2 ? "" : " hidden"}>${g.functions.slice(0, 60).map((n) => `<a href="${fnLink(n)}">${esc(n)}</a>`).join("")}${g.functions.length > 60 ? `<span class="legend-line">and ${g.functions.length - 60} more</span>` : ""}</div>
    </div>`).join("") : `<div class="none">${o.runs.lift_check ? "The lifter refused nothing." : "No lift check recorded yet (dread lift --check writes it)."}</div>`;
  return `<div class="card"><h2>Refused by the lifter${count ? ` · ${count}` : ""}</h2>${body}</div>`;
}

function droppedCard(o) {
  const body = o.dropped.length ? o.dropped.map((d) =>
    `<div class="rowline"><a href="${fnLink(d.name)}">${esc(d.name)}</a><span class="r" title="${esc(d.reason)}">${esc(d.reason)}</span></div>`).join("")
    : `<div class="none">${o.runs.lift_check ? "Nothing dropped: every lifted function agreed and was covered." : "No lift check recorded yet."}</div>`;
  return `<div class="card"><h2>Dropped by the lift check${o.dropped.length ? ` · ${o.dropped.length}` : ""}</h2>${body}</div>`;
}

function testsCard(o) {
  let body;
  if (!o.runs.test) body = `<div class="none">No test run recorded yet (dread test writes it).</div>`;
  else if (!o.failing.length && !o.uncovered.length) body = `<div class="none good">Every tested function agrees with the original, and its cases run every instruction.</div>`;
  else {
    body = o.failing.map((f) => `<div class="rowline"><a href="${fnLink(f.name)}">${esc(f.name)}</a><span class="r bad">${f.failed} of ${f.cases} cases fail</span></div>`).join("") +
      o.uncovered.map((u) => `<div class="rowline"><a href="${fnLink(u.name)}">${esc(u.name)}</a><span class="r warn">${u.missed} instructions never ran</span></div>`).join("");
  }
  return `<div class="card"><h2>Last test run</h2>${body}</div>`;
}

function runsCard(o) {
  const rows = [["dread recon", o.runs.recon], ["dread test", o.runs.test], ["dread lift --check", o.runs.lift_check]];
  return `<div class="card"><h2>When the tools ran</h2><div class="runs">${rows.map(([k, t]) =>
    `<span class="k">${k}</span><span class="v" title="${esc(when(t))}">${ago(t)}</span>`).join("")}</div></div>`;
}

let mapLayout = null;
function drawMap() {
  const canvas = $("#map");
  if (!canvas || !S.overview) return;
  const img = S.overview.image;
  const span = Math.max(1, img.end - img.base);
  const width = canvas.clientWidth || 800;
  const rowHeight = 12, gap = 3, maxRows = 28;
  const perPixel = Math.max(1, Math.ceil(span / (width * maxRows)));
  const rows = Math.max(1, Math.ceil(span / (perPixel * width)));
  const height = rows * (rowHeight + gap) - gap;
  const ratio = window.devicePixelRatio || 1;
  canvas.style.height = height + "px";
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const g = canvas.getContext("2d");
  g.setTransform(ratio, 0, 0, ratio, 0, 0);
  g.clearRect(0, 0, width, height);
  g.fillStyle = cssVar("--panel-2");
  for (let r = 0; r < rows; r++) g.fillRect(0, r * (rowHeight + gap), r === rows - 1 ? Math.ceil((span - r * perPixel * width) / perPixel) : width, rowHeight);
  const colors = Object.fromEntries(Object.keys(ORIGINS).map((k) => [k, cssVar("--o-" + k)]));
  const sorted = [...S.functions].sort((a, b) => a.entry - b.entry);
  for (const fn of sorted) {
    g.fillStyle = colors[fn.origin];
    let from = (fn.entry - img.base) / perPixel, to = (fn.entry + fn.size - img.base) / perPixel;
    const wide = to - from >= 4;
    while (from < to) {
      const r = Math.floor(from / width), x = from - r * width;
      const w = Math.min(to - from, width - x);
      g.fillRect(Math.floor(x), r * (rowHeight + gap), Math.max(1, Math.round(w) - (wide && w === to - from ? 1 : 0)), rowHeight);
      from += w;
    }
  }
  mapLayout = { width, rowHeight, gap, perPixel, base: img.base, sorted };
}

function mapHit(event) {
  if (!mapLayout) return null;
  const canvas = $("#map"), box = canvas.getBoundingClientRect();
  const { width, rowHeight, gap, perPixel, base, sorted } = mapLayout;
  const x = event.clientX - box.left, y = event.clientY - box.top;
  const r = Math.floor(y / (rowHeight + gap));
  if (y - r * (rowHeight + gap) > rowHeight) return null;
  const address = base + Math.floor((r * width + x) * perPixel);
  let lo = 0, hi = sorted.length - 1, found = null;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (sorted[mid].entry <= address) { found = sorted[mid]; lo = mid + 1; } else hi = mid - 1;
  }
  if (found && address < found.entry + Math.max(found.size, perPixel)) return found;
  return null;
}

// ---- one function ----

async function showFunction(name, at, keep = false, mode = "code") {
  const mine = ++renders;
  let d;
  try {
    d = await api("/api/function", { name });
  } catch (e) {
    if (mine === renders) showError(e.message);
    return;
  }
  if (mine !== renders) return;
  const main = $("#main");
  const same = S.current && S.current.header.name === name && S.view === "function" && S.mode === mode;
  const scroll = main.scrollTop, sel = S.sel;
  S.view = "function";
  S.mode = mode;
  S.current = d;
  S.sel = -1;
  document.title = name + " · DREAD";
  if (!same) S.tab = null;
  if (mode === "relations") {
    main.innerHTML = headerHTML(d, true) + relationsHTML();
    main.scrollTop = 0;
    revealInList(name);
    watch(d.status);
    return showRelations(name, mine);
  }
  stopRelations();
  main.innerHTML = headerHTML(d) + `
    <div class="split${S.showC ? "" : " no-c"}" id="split">
      <section class="pane">
        <div class="pane-head"><span class="title">Listing</span>${coverageLegend(d)}</div>
        <div class="listing${S.showBytes ? " show-bytes" : ""}" id="listing">${listingHTML(d)}</div>
      </section>
      <section class="pane cpane" id="cpane"></section>
    </div>`;
  renderC();
  revealInList(name);
  if (keep && same) { if (sel >= 0) selectRow(sel, false); main.scrollTop = scroll; }
  else if (at != null) selectAddress(at, true);
  else main.scrollTop = 0;
  watch(d.status);
}

function headerHTML(d, compact = false) {
  const h = d.header;
  const meta = [`<span class="mono">${hex8(h.entry)}–${hex8(h.end)}</span>`, `${h.count} instructions`, `${d.size} bytes`, esc(h.isa)];
  if (d.placement) {
    meta.push(`${esc(d.placement.placement)}: ${d.placement.c_bytes} bytes of C for ${d.placement.original_bytes}`);
  }
  if (h.endless) meta.push("never returns");
  const facts = [];
  const callers = new Map();
  for (const c of h.callers) callers.set(c.name, [...(callers.get(c.name) || []), c.address]);
  if (S.pending.includes("callers") && !h.callers.length) facts.push(["called by", "<span class=\"legend-line\">being found</span>"]);
  else facts.push(["called by", callers.size ? [...callers].map(([n, sites]) =>
    `<a href="${fnLink(n, sites[0])}" title="${sites.map(hex).join(", ")}">${esc(n)}</a>${sites.length > 1 ? `<span class="times">×${sites.length}</span>` : ""}`).join("")
    : "<span class=\"legend-line\">no direct calls</span>"]);
  if (h.calls.length) facts.push(["calls", h.calls.map((n) => `<a href="${fnLink(n)}">${esc(n)}</a>`).join("")]);
  if (h.reads) facts.push(["registers", `reads ${h.reads.length ? esc(h.reads.join(", ")) : "nothing"} at its entry; its callers read ${h.kept.length ? esc(h.kept.join(", ")) : "nothing"} after it`]);
  const v = d.verdict;
  if (d.origin === "hand") facts.push(["origin", "written by hand" + (v && v.verdict !== "by hand" ? "" : "")]);
  else if (d.origin === "pilot") facts.push(["origin", "<span class=\"warn\">written by dread pilot and checked by the tools; read by nobody yet</span>"]);
  if (v && v.verdict === "refused") facts.push(["lift check", `<span class="warn">refused:</span> ${esc(v.reason)}`]);
  else if (v && v.verdict === "dropped") facts.push(["lift check", `<span class="bad">dropped:</span> ${esc(v.reason)}`]);
  else if (v && v.verdict === "accepted") facts.push(["lift check", `<span class="good">accepted</span>${v.reason ? " " + esc(v.reason) : ""}: the cases agree and run every instruction a path reaches`]);
  if (d.test) {
    const failed = +d.test.failed, cases = +d.test.cases, missed = d.test.missed ? d.test.missed.split(" ").length : 0;
    facts.push(["last test", (failed ? `<span class="bad">${failed} of ${cases} cases fail</span>` : `${cases} cases agree`) +
      (missed ? `; <span class="warn">${missed} instructions never ran</span>` : "")]);
  }
  const cases = Object.entries(d.cases).filter(([k]) => k !== "auto").map(([k, n]) => `${n} ${k}`);
  if (cases.length || d.cases.auto) facts.push(["cases", (cases.join(" · ") || "") + (d.cases.auto ? `${cases.length ? " · " : ""}generated for it (auto)` : "")]);
  const notes = h.notes.length ? `<div class="notes">${esc(h.notes.join("\n"))}</div>` : "";
  const tips = { code: "The listing and the C (r)", relations: "The functions, memory, texts and structures around it (r)",
    program: "The whole program, this function lit in its group (p)" };
  const modes = `<div class="seg modes" id="modes">${[["code", "Code"], ["relations", "Relations"], ["program", "Program"]].map(([m, label]) =>
    `<button data-mode="${m}"${m === S.mode ? ' class="on"' : ""} title="${tips[m]}">${label}</button>`).join("")}</div>`;
  return `
    <div class="fn-head${compact ? " compact" : ""}">
      <div class="fn-title"><h1>${esc(h.name)}</h1>
        <span class="badge">${dot(d.origin)}${esc(ORIGINS[d.origin].long)}</span>${d.system ? "<span class=\"badge\">tested by the system run</span>" : ""}
        <span class="grow"></span>
        ${modes}
        <button id="note-btn" title="Note on the selected line (;)">Note</button>
        <button id="rename-btn" title="Rename (n)">Rename</button></div>
      <div class="meta">${meta.map((m) => `<span>${m}</span>`).join("")}</div>
      ${compact ? "" : `<div class="facts">${facts.map(([k, val]) => `<span class="k">${k}</span><span class="v">${val}</span>`).join("")}</div>
      ${notes}`}
    </div>`;
}

function coverageLegend(d) {
  const c = d.coverage;
  if (!c) return `<span class="legend-line">no record of which instructions the cases run</span>`;
  return `<span class="legend-line" title="${esc(c.from + (c.when ? ", " + when(c.when) : ""))}"><span><i class="ran"></i>ran</span>` +
    `${c.missed.length ? `<span><i class="missed"></i>${c.missed.length} never ran</span>` : ""}` +
    `${c.unreached.length ? `<span><i class="unreached"></i>${c.unreached.length} no path reaches</span>` : ""}` +
    `<span>${esc(c.from)}${c.when ? ", " + ago(c.when) : ""}</span></span>`;
}

function listingHTML(d) {
  const missed = new Set(d.coverage ? d.coverage.missed : []);
  const unreached = new Set(d.coverage ? d.coverage.unreached : []);
  return d.rows.map((r, i) => {
    if (r.kind === "label") {
      return `<div class="ln label" id="label-${r.address}" data-i="${i}" data-a="${r.address}"><span class="m"></span><span class="i">${esc(r.text)}:</span></div>`;
    }
    if (r.kind === "note") {
      return `<div class="ln note" data-i="${i}" data-a="${r.address}"><span class="m"></span><span class="i">; ${esc(r.text)}</span></div>`;
    }
    let mark = "";
    if (r.kind === "insn" && d.coverage) mark = missed.has(r.address) ? " missed" : unreached.has(r.address) ? " unreached" : " ran";
    const space = r.text.indexOf(" ");
    const text = r.kind === "insn" && space > 0
      ? `<span class="mn">${esc(r.text.slice(0, space))}</span>${esc(r.text.slice(space))}` : esc(r.text);
    const comment = r.comment && r.comment.length ? "; " + r.comment.map((c) =>
      c.name ? `<a href="${fnLink(c.name)}">${esc(c.text)}</a>`
        : c.label != null ? `<a data-label="${c.label}">${esc(c.text)}</a>` : esc(c.text)).join("; ") : "";
    return `<div class="ln ${r.kind}${mark}" data-i="${i}" data-a="${r.address}"><span class="m"></span>` +
      `<span class="a">${hex8(r.address)}</span><span class="b">${r.bytes || ""}</span><span class="i">${text}</span><span class="c">${comment}</span></div>`;
  }).join("");
}

function renderC() {
  const d = S.current, pane = $("#cpane");
  if (!pane) return;
  const sources = d.sources;
  if (!S.tab || !sources.some((s) => s.kind === S.tab)) S.tab = sources.length ? sources[0].kind : null;
  const tabs = sources.map((s) => `<button class="tab${s.kind === S.tab ? " on" : ""}" data-tab="${s.kind}">${s.kind === "pilot" ? "pilot" : s.kind}</button>`).join("");
  const s = sources.find((x) => x.kind === S.tab);
  let body;
  if (!s) body = `<div class="placeholder">No C for this function.</div>`;
  else if (s.pending) body = `<div class="placeholder">The lifted C follows when the register analysis is done.</div>`;
  else if (s.refused) body = `<div class="placeholder"><span class="warn">The lifter refuses it:</span> ${esc(s.refused)}</div>`;
  else {
    const where = s.path ? `${esc(s.path)}${s.first > 1 ? ":" + s.first : ""}` : "";
    const how = s.how || (s.kind === "source" ? "written by hand" : s.kind === "pilot" ? "written by dread pilot, read by nobody" : "");
    body = `<div class="pane-sub">${[where, esc(how)].filter(Boolean).join(" · ")}</div><div class="code">${codeHTML(s.text, s.first)}</div>`;
  }
  pane.innerHTML = `<div class="pane-head"><span class="title">C</span>${tabs}</div>${body}`;
}

const KEYWORDS = new Set(("if else for while do switch case default break continue return goto sizeof typedef struct union " +
  "enum static inline extern register volatile const asm __asm__ __attribute__ __volatile__").split(" "));
const TYPES = new Set(("void char short int long unsigned signed float double bool _Bool u8 u16 u32 u64 s8 s16 s32 s64 " +
  "i8 i16 i32 i64 uint8_t uint16_t uint32_t uint64_t int8_t int16_t int32_t int64_t size_t uintptr_t").split(" "));

function codeHTML(text, first = 1) {
  const pattern = /(\/\/[^\n]*|\/\*[\s\S]*?\*\/)|("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')|(^[ \t]*#[^\n]*)|(\b0[xX][0-9a-fA-F]+[uUlL]*\b|\b\d+[uUlL]*\b)|(\b[A-Za-z_]\w*\b)/gm;
  const own = S.current && S.current.header.name;
  let html = "", last = 0, m;
  const wrap = (cls, t) => t.split("\n").map((p) => `<span class="${cls}">${esc(p)}</span>`).join("\n");
  while ((m = pattern.exec(text))) {
    html += esc(text.slice(last, m.index));
    last = pattern.lastIndex;
    const [t, comment, string, pre, number, id] = m;
    if (comment) html += wrap("cm", t);
    else if (string) html += wrap("s", t);
    else if (pre) html += wrap("pp", t);
    else if (number) html += `<span class="n">${t}</span>`;
    else if (KEYWORDS.has(id)) html += `<span class="k">${id}</span>`;
    else if (TYPES.has(id)) html += `<span class="t">${id}</span>`;
    else if (S.byName.has(id) && id !== own) html += `<a class="f" href="${fnLink(id)}">${id}</a>`;
    else html += id;
  }
  html += esc(text.slice(last));
  return html.replace(/\n$/, "").split("\n").map((line, i) =>
    `<div class="cl"><span class="no">${first + i}</span><span>${line || " "}</span></div>`).join("");
}

function rowsOf(kinds = ["insn", "word"]) {
  return [...document.querySelectorAll("#listing .ln")].filter((e) => kinds.some((k) => e.classList.contains(k)));
}

function selectRow(i, scroll = true, center = false) {
  const listing = $("#listing");
  if (!listing) return;
  const old = listing.querySelector(".ln.sel");
  if (old) old.classList.remove("sel");
  const row = listing.querySelector(`.ln[data-i="${i}"]`);
  if (!row) { S.sel = -1; return; }
  row.classList.add("sel");
  S.sel = i;
  if (scroll) row.scrollIntoView({ block: center ? "center" : "nearest" });
}

function selectAddress(address, center = false) {
  const d = S.current;
  let best = -1;
  d.rows.forEach((r, i) => {
    if ((r.kind === "insn" || r.kind === "word") && r.address <= address && (best < 0 || r.address >= d.rows[best].address)) best = i;
  });
  if (best >= 0) {
    selectRow(best, true, center);
    const row = $(`#listing .ln[data-i="${best}"]`);
    row.classList.remove("flash");
    void row.offsetWidth;
    row.classList.add("flash");
  }
}

function moveSelection(step) {
  const rows = rowsOf();
  if (!rows.length) return;
  const at = rows.findIndex((r) => +r.dataset.i === S.sel);
  const next = at < 0 ? (step > 0 ? 0 : rows.length - 1) : Math.max(0, Math.min(rows.length - 1, at + step));
  selectRow(+rows[next].dataset.i);
}

function followSelection() {
  const row = $(`#listing .ln[data-i="${S.sel}"]`);
  const link = row && row.querySelector(".c a");
  if (!link) return;
  if (link.dataset.label) goLabel(+link.dataset.label);
  else location.hash = link.getAttribute("href");
}

function goLabel(address) {
  selectAddress(address, true);
}

// ---- relations: the graph around a function (dread relations, /api/graph) ----
//
// The server says what is related (calls, memory read and written, texts, structures) and how sure
// each name is; this lays it out with the force simulation of sim.js (callers held above their line,
// callees below theirs, the function fixed in the middle) and draws it as SVG, frame by frame while the
// simulation moves: nodes overshoot a little and settle; a change of the graph (depth, filters, the C
// read meanwhile, a walk to another function) starts from the positions and velocities they had, so
// the picture morphs instead of jumping; a dragged node pulls its neighbours along and settles again
// when released (the function in the middle glides back to it). At rest nothing runs. With
// prefers-reduced-motion the layout goes to its rest at once and is drawn once, and a dragged node
// stays where it is dropped. No randomness: the same graph reached the same way settles the same way,
// unless it changed while it moved or was dragged.

const KIND_GROUP = { function: "function", global: "memory", register: "memory", rom: "memory", table: "memory",
  text: "text", structure: "structure" };
const KIND_LONG = { function: "function", global: "memory in RAM", register: "device register", rom: "data in rom",
  table: "table of pointers", text: "text", structure: "structure (readable_types)" };
const GROUPS = [["function", "functions"], ["memory", "memory"], ["text", "texts"], ["structure", "structures"]];
const GROUP_TITLE = { function: "the callers, callees and functions whose address is taken",
  memory: "globals, device registers, data in rom and tables of pointers", text: "the texts the code points to",
  structure: "the structures its variables point to (readable_types)" };
const NAMING = { named: "named", proposed: "name proposed", unnamed: "no name yet" };
const LABELS_ALL = 60;          // up to this many nodes every label shows; beyond, LABELS_SOME and on hover
const LABELS_SOME = 28;         // the labels a larger graph shows: the function, then the best-joined near pieces
const LABEL_ZOOM = 1.35;        // and every label from this zoom on
const SIM = { layer: 120, band: 0.8, spread: 90, repel: 9000, spring: 0.16, pull: 0.12, gravity: 0.004, reach: 420,
  anchor: 0.25 };
const WOBBLE = { friction: 0.86, decay: 0.028, maxSpeed: 20 };   // the relations' motion (sim.js)
const REST = { calls: 95, "refers-to-function": 85, access: 52, "refers-to-text": 44, "uses-structure": 60, "points-to": 60 };
const DRAG_ALPHA = 0.3;         // the energy held while a node is dragged: its neighbours follow it
const FOLLOW = 0.16;            // the share of the way to the fitted view the camera goes in a frame
const SETTLE_TICKS = 1500;      // the most ticks a layout without motion takes to its rest

const R = {
  center: null, depth: 1, graph: null, pos: new Map(), view: null, sticky: null, timer: null,
  groups: new Set(GROUPS.map(([k]) => k)), shown: null, drag: null, touched: false,
  sim: null, index: new Map(), slots: [], els: [], paths: [], ends: [], frame: 0, last: 0, acc: 0, home: true,
};
const KEEP_POSITIONS = 4000;    // positions remembered across the walk at most

const MOTION = matchMedia("(prefers-reduced-motion: reduce)");
const still = () => MOTION.matches;

// Ticks for this animation frame: one per 60th of a second whatever the display's rate, at most two.
function steps(state, now) {
  const dt = state.last ? Math.min(50, now - state.last) : 1000 / 60;
  state.last = now;
  state.acc = Math.min(2, state.acc + dt * 60 / 1000);
  const n = Math.floor(state.acc);
  state.acc -= n;
  return n;
}

function shapePath(kind, center = false) {
  if (kind === "function") return `<circle class="shape" r="${center ? 10 : 6.5}"/>`;
  if (kind === "register") return `<path class="shape" d="M0,-7.5L7.5,0L0,7.5L-7.5,0Z"/>`;
  if (kind === "table") return `<rect class="shape" x="-8" y="-5" width="16" height="10" rx="1.5"/><path class="inner" d="M-8,0H8M0,-5V5"/>`;
  if (kind === "text") return `<rect class="shape" x="-8" y="-4.5" width="16" height="9" rx="4.5"/>`;
  if (kind === "structure") return `<path class="shape" d="M0,-7.5L7.5,5.5H-7.5Z"/>`;
  return `<rect class="shape" x="-5.5" y="-5.5" width="11" height="11" rx="${kind === "rom" ? 0 : 2}"/>`;
}

function radius(d) {
  return d.kind === "function" ? (d.id === "f:" + R.center ? 11 : 7) : d.kind === "text" || d.kind === "table" ? 8 : 7;
}

function relationsHTML() {
  const icon = { function: "function", memory: "global", text: "text", structure: "structure" };
  return `
    <section class="rel" id="rel">
      <div class="rel-bar">
        <span class="rel-k">depth</span>
        <div class="seg" id="rel-depth">${[1, 2, 3].map((n) =>
          `<button data-depth="${n}"${n === R.depth ? ' class="on"' : ""} title="callers and callees ${n} call${n > 1 ? "s" : ""} away">${n}</button>`).join("")}</div>
        <div class="chips" id="rel-kinds">${GROUPS.map(([k, label]) =>
          `<span class="chip${R.groups.has(k) ? " on" : ""}" data-group="${k}" title="${GROUP_TITLE[k]}"><svg viewBox="-9 -9 18 18" class="glyph k-${icon[k]}">${shapePath(icon[k])}</svg>${label}</span>`).join("")}</div>
        <span class="grow"></span>
        <span class="rel-note" id="rel-note"></span>
        <button id="rel-fit" title="Fit the graph (f)">Fit</button>
      </div>
      <div class="rel-stage" id="rel-stage">
        <svg class="graph" id="rel-svg" role="img" aria-label="Relations"></svg>
        <div class="rel-legend" id="rel-legend">${legendHTML()}</div>
      </div>
    </section>`;
}

function legendHTML() {
  const sw = (cls, text) => `<span class="lg"><svg viewBox="-9 -9 18 18" class="glyph ${cls}">${shapePath("function")}</svg>${text}</span>`;
  const sh = (kind, text) => `<span class="lg"><svg viewBox="-9 -9 18 18" class="glyph k-${kind} n-unnamed">${shapePath(kind)}</svg>${text}</span>`;
  const ln = (cls, text) => `<span class="lg"><svg viewBox="0 0 22 8" class="line"><path class="edge ${cls}" d="M1,4H21"/></svg>${text}</span>`;
  return `<div class="lg-row">${sw("n-named", "named")}${sw("n-proposed", "name proposed")}${sw("n-unnamed", "no name yet")}</div>` +
    `<div class="lg-row">${sh("global", "RAM")}${sh("register", "register")}${sh("rom", "rom")}${sh("table", "table")}${sh("text", "text")}${sh("structure", "structure")}</div>` +
    `<div class="lg-row">${ln("e-calls", "calls")}${ln("e-refers-to-function", "address taken")}${ln("e-access", "reads")}${ln("e-access w", "writes")}</div>` +
    `<div class="lg-row">${ln("e-uses", "uses")}${ln("e-refers-to-text", "refers to")}${ln("e-uses-structure", "structure")}</div>`;
}

// Leaving the relations: no more asking the server, no more frames.
function stopRelations() {
  clearTimeout(R.timer);
  R.timer = null;
  if (R.frame) cancelAnimationFrame(R.frame);
  R.frame = 0;
}

async function showRelations(name, mine) {
  clearTimeout(R.timer);
  R.timer = null;
  if (R.center !== name) { R.sticky = null; }
  let g;
  try {
    g = await api("/api/graph", { name, depth: R.depth });
  } catch (e) {
    if (mine === renders) relNote(e.message);
    return;
  }
  if (mine !== renders || S.mode !== "relations" || !$("#rel-svg")) return;
  const moved = R.center !== name;
  if (moved) R.touched = false;
  R.center = name;
  R.graph = g;
  sizeStage("rel-stage");
  layoutGraph(moved);
  drawGraph();
  if (!R.view || (still() && !R.touched)) fitGraph(); else applyView();   // (else the camera follows the motion)
  relNote();
  kick();
  if (g.waiting || g.pending.length) {
    R.timer = setTimeout(() => { if (mine === renders) showRelations(name, mine); }, g.waiting ? 500 : 900);
  }
}

function relNote(error) {
  const box = $("#rel-note");
  if (!box) return;
  if (error) { box.innerHTML = `<span class="bad">${esc(error)}</span>`; return; }
  const g = R.graph, shown = R.shown;
  const parts = [];
  if (g.waiting) parts.push("finding the callers");
  else {
    parts.push(`${shown.nodes.length} of ${g.nodes.length} nodes`);
    if (g.pending.length) parts.push(`<span class="busy-dot"></span>reading the C of ${g.pending.length} function${g.pending.length > 1 ? "s" : ""}`);
    if (g.left_out && (g.left_out.functions || g.left_out.nodes)) {
      parts.push(`<span class="warn" title="dread relations' limits: MAX_FUNCTIONS, MAX_NODES">${g.left_out.functions + g.left_out.nodes} left out</span>`);
    }
  }
  box.innerHTML = parts.join(" · ");
}

// What the filters let through: the center and what joins it through nodes of the kinds shown.
function visibleGraph() {
  const g = R.graph, center = "f:" + g.center;
  const adj = new Map();
  for (const e of g.edges) {
    if (!adj.has(e.from)) adj.set(e.from, []);
    if (!adj.has(e.to)) adj.set(e.to, []);
    adj.get(e.from).push(e.to);
    adj.get(e.to).push(e.from);
  }
  const visible = new Set(g.nodes.some((n) => n.id === center) ? [center] : []);
  for (let grew = true; grew;) {
    grew = false;
    for (const n of g.nodes) {
      if (visible.has(n.id) || !R.groups.has(KIND_GROUP[n.kind])) continue;
      if ((adj.get(n.id) || []).some((m) => visible.has(m))) { visible.add(n.id); grew = true; }
    }
  }
  const nodes = g.nodes.filter((n) => visible.has(n.id));
  const links = [], pairs = new Map();
  for (const e of g.edges) {
    if (!visible.has(e.from) || !visible.has(e.to)) continue;
    if (e.kind === "reads" || e.kind === "writes" || e.kind === "uses") {
      const key = e.from + "\u0000" + e.to;
      let l = pairs.get(key);
      if (!l) {
        l = { source: e.from, target: e.to, kind: "access", read: false, write: false, uses: false, edges: [] };
        pairs.set(key, l);
        links.push(l);
      }
      l[e.kind === "reads" ? "read" : e.kind === "writes" ? "write" : "uses"] = true;
      l.edges.push(e);
    } else {
      links.push({ source: e.from, target: e.to, kind: e.kind, edges: [e] });
    }
  }
  return { nodes, links, byId: new Map(nodes.map((n) => [n.id, n])) };
}

// A new simulation for the graph shown, seeded with the positions and velocities its nodes had (in the
// frame of the function now in the middle); new nodes start where they belong (functions on their
// layer by address, the rest around their first neighbour on the golden angle). Without motion it is
// run to its rest here.
function layoutGraph(moved) {
  const shown = R.shown = visibleGraph();
  const { nodes, links } = shown;
  const n = nodes.length, center = "f:" + R.graph.center;
  const index = new Map(nodes.map((d, i) => [d.id, i]));
  const sim = new Sim(Object.assign({ repel: SIM.repel, power: 2, reach: SIM.reach, gravity: SIM.gravity }, WOBBLE)).size(n);
  const { x, y, vx, vy } = sim;
  const ty = new Float64Array(n), layered = new Uint8Array(n), known = new Uint8Array(n);
  const neighbours = nodes.map(() => []);
  const ends = links.map((l) => {
    const s = index.get(l.source), t = index.get(l.target);
    neighbours[s].push(t);
    neighbours[t].push(s);
    return [s, t];
  });
  const old = R.pos, base = moved ? old.get(center) || { x: 0, y: 0 } : { x: 0, y: 0 };
  // functions on layers: callers above, callees and taken addresses below, by address along a layer
  const layers = new Map();
  nodes.forEach((d, i) => {
    if (d.kind !== "function") return;
    const dist = d.distance || 0;
    ty[i] = d.side === "caller" ? -dist * SIM.layer : d.side === "callee" || d.side === "referred" ? dist * SIM.layer : 0;
    layered[i] = 1;
    if (!layers.has(ty[i])) layers.set(ty[i], []);
    layers.get(ty[i]).push(i);
  });
  for (const [level, members] of layers) {
    members.sort((a, b) => nodes[a].address - nodes[b].address);
    members.forEach((i, j) => { x[i] = (j - (members.length - 1) / 2) * SIM.spread; y[i] = level; });
  }
  const place = (i) => {
    const p = old.get(nodes[i].id);
    if (p) { x[i] = p.x - base.x; y[i] = p.y - base.y; vx[i] = p.vx || 0; vy[i] = p.vy || 0; known[i] = 1; }
  };
  nodes.forEach((d, i) => { if (d.kind === "function") place(i); });
  let k = 0;
  nodes.forEach((d, i) => {
    if (d.kind === "function") return;
    const at = neighbours[i].find((j) => nodes[j].kind === "function") ?? neighbours[i][0];
    const angle = k * 2.399963, r = 48 + 5 * Math.sqrt(k);
    k += 1;
    x[i] = (at != null ? x[at] : 0) + r * Math.cos(angle);
    y[i] = (at != null ? y[at] : 0) + r * Math.sin(angle);
    place(i);
  });
  const c = index.get(center);
  if (c != null && (moved || R.home)) {
    x[c] = 0; y[c] = 0; vx[c] = 0; vy[c] = 0;
    sim.fix(c, 0, 0);
    R.home = true;
  }
  sim.links(ends.map((e) => e[0]), ends.map((e) => e[1]), links.map((l) => REST[l.kind] || 60),
    ends.map(([s, t]) => SIM.spring / Math.sqrt(Math.max(1, Math.min(neighbours[s].length, neighbours[t].length)))));
  sim.forces.push((s) => {               // callers kept above their line, callees below theirs, free beyond it
    for (let i = 0; i < n; i++) {
      if (!layered[i]) continue;
      const line = ty[i] * SIM.band, yi = s.y[i];
      if (ty[i] < 0 ? yi > line : ty[i] > 0 ? yi < line : true) s.fy[i] += (line - yi) * SIM.pull;
    }
    if (c != null && !s.fixed[c]) { s.fx[c] -= s.x[c] * SIM.anchor; s.fy[c] -= s.y[c] * SIM.anchor; }
  });
  const share = n ? known.reduce((t, v) => t + v, 0) / n : 0;
  // fresh: the whole energy; a step to another function: most of it; more of the same graph: a settling
  sim.heat(share < 0.25 ? 1 : moved ? 0.6 : share > 0.6 ? 0.3 : 0.6);
  const drag = R.drag;
  if (drag && drag.moved && drag.id && index.has(drag.id) && !still()) {
    const i = index.get(drag.id);
    sim.fix(i, x[i], y[i]);
    sim.target = DRAG_ALPHA;
  }
  if (still()) sim.run(SETTLE_TICKS);
  R.pos = new Map();                    // (every position in the frame of the function now in the middle)
  if (old.size < KEEP_POSITIONS) for (const [id, p] of old) R.pos.set(id, { x: p.x - base.x, y: p.y - base.y, vx: 0, vy: 0 });
  R.slots = nodes.map((d, i) => {
    const p = { x: x[i], y: y[i], vx: vx[i], vy: vy[i] };
    R.pos.set(d.id, p);
    return p;
  });
  if (moved && R.view && !still()) {    // the picture stays where it was; the camera then glides to the middle
    R.view.tx += base.x * R.view.k;
    R.view.ty += base.y * R.view.k;
  }
  R.sim = sim;
  R.index = index;
}

function syncSlots() {
  const { x, y, vx, vy } = R.sim;
  R.slots.forEach((p, i) => { p.x = x[i]; p.y = y[i]; p.vx = vx[i]; p.vy = vy[i]; });
}

function kick() {
  if (!R.frame && R.sim && $("#rel-svg")) {
    R.last = 0;
    R.frame = requestAnimationFrame(relFrame);
  }
}

// One animation frame of the relations: the simulation's ticks, the nodes and edges moved, the camera.
function relFrame(now) {
  R.frame = 0;
  const sim = R.sim;
  if (!sim || !$("#rel-svg") || S.view !== "function" || S.mode !== "relations") return;
  if (still()) sim.run(SETTLE_TICKS);
  else for (let t = steps(R, now); t > 0 && !sim.rested; t--) sim.tick();
  if (sim.rested && !R.home) homeCenter();
  syncSlots();
  placeNodes();
  placeEdges();
  const easing = !R.touched && !still() && followView();
  if (still() && !R.touched) fitGraph();
  if (!sim.rested || (R.drag && R.drag.moved && R.drag.id) || easing) R.frame = requestAnimationFrame(relFrame);
}

// At rest after a drag: the function in the middle again, exactly.
function homeCenter() {
  const c = R.index.get("f:" + R.graph.center);
  if (c != null) {
    R.sim.fix(c, 0, 0);
    R.sim.x[c] = 0;
    R.sim.y[c] = 0;
  }
  R.home = true;
}

function nodeLabel(d) {
  const name = d.kind === "text" ? (d.name.length > 30 ? d.name.slice(0, 28) + "…\"" : d.name) : d.name;
  const more = d.more ? d.more.callers + d.more.callees : 0;
  return esc(name) + (more ? `<tspan class="more" dx="4">+${more}</tspan>` : "");
}

function drawGraph() {
  const svg = $("#rel-svg");
  if (!svg || !R.shown) return;
  const { nodes, links, byId } = R.shown, center = "f:" + R.graph.center;
  const shared = new Map();             // how many functions each node is joined to: a shared piece is labelled
  for (const l of links) {
    shared.set(l.target, (shared.get(l.target) || 0) + 1);
    shared.set(l.source, (shared.get(l.source) || 0) + 1);
  }
  const marker = (id, cls) => `<marker id="${id}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" ` +
    `markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path class="${cls}" d="M0,1.5L9,5L0,8.5Z"/></marker>`;
  const defs = `<defs>${marker("ah-call", "mk-call")}${marker("ah-read", "mk-read")}${marker("ah-write", "mk-write")}${marker("ah-hl", "mk-hl")}</defs>`;
  const edges = links.map((l, i) => {
    const cls = l.kind === "access" ? `e-access${l.write ? " w" : ""}${l.uses && !l.read && !l.write ? " e-uses" : ""}` : "e-" + l.kind;
    let start = "", end = "";           // data flows: a read towards the function, a write towards the memory
    if (l.kind === "calls" || l.kind === "refers-to-function" || l.kind === "points-to" || l.kind === "holds") end = "ah-call";
    else if (l.kind === "access") { if (l.read) start = "ah-read"; if (l.write) end = "ah-write"; }
    return `<path class="edge ${cls}" data-i="${i}"${start ? ` marker-start="url(#${start})"` : ""}${end ? ` marker-end="url(#${end})"` : ""}/>`;
  }).join("");
  const many = nodes.length > LABELS_ALL;
  const ranked = nodes.filter((d) => d.id !== center && ((d.kind === "function" && (d.distance || 0) <= 1) || (shared.get(d.id) || 0) > 2))
    .sort((a, b) => (shared.get(b.id) || 0) - (shared.get(a.id) || 0) || (a.distance || 0) - (b.distance || 0) || (a.address || 0) - (b.address || 0));
  const labelled = new Set([center, ...ranked.slice(0, LABELS_SOME).map((d) => d.id)]);
  const body = nodes.map((d) => {
    const major = !many || labelled.has(d.id);
    const naming = d.kind === "text" ? "n-none" : "n-" + (d.naming || "unnamed");
    const r = radius(d);
    return `<g class="node k-${d.kind} ${naming}${d.id === center ? " center" : ""}${major ? " major" : ""}" data-id="${esc(d.id)}">` +
      `${shapePath(d.kind, d.id === center)}<text x="${r + 3}" y="4">${nodeLabel(d)}</text></g>`;
  }).join("");
  svg.innerHTML = `${defs}<g id="rel-view"><g class="edges">${edges}</g><g class="nodes">${body}</g></g>`;
  svg.classList.remove("focus");        // (a pointer still on a node lights it again when it moves)
  svg.classList.toggle("labels", !many);
  svg.classList.toggle("dense", links.length > 120);
  R.els = [...svg.querySelectorAll(".nodes .node")];
  R.paths = [...svg.querySelectorAll(".edges .edge")];
  R.ends = links.map((l) => [R.index.get(l.source), R.index.get(l.target),
    radius(byId.get(l.source)) + 1.5, radius(byId.get(l.target)) + 1.5]);
  placeNodes();
  placeEdges();
  if (R.sticky && R.shown.byId.has(R.sticky)) focusNode(R.sticky);
}

function placeNodes(only) {
  R.els.forEach((g, i) => {
    if (only != null && i !== only) return;
    const p = R.slots[i];
    g.setAttribute("transform", `translate(${p.x.toFixed(1)},${p.y.toFixed(1)})`);
  });
}

function placeEdges(only) {
  const slots = R.slots;
  R.ends.forEach(([s, t, ra, rb], i) => {
    if (only != null && s !== only && t !== only) return;
    const a = slots[s], b = slots[t], path = R.paths[i];
    if (!a || !b || !path) return;
    const dx = b.x - a.x, dy = b.y - a.y, d = Math.sqrt(dx * dx + dy * dy) || 1;
    path.setAttribute("d", `M${(a.x + dx / d * ra).toFixed(1)},${(a.y + dy / d * ra).toFixed(1)}L${(b.x - dx / d * rb).toFixed(1)},${(b.y - dy / d * rb).toFixed(1)}`);
  });
}

function sizeStage(id) {
  const stage = $("#" + id);
  if (!stage) return;
  const top = stage.getBoundingClientRect().top;
  stage.style.height = Math.max(360, Math.floor(window.innerHeight - top - 10)) + "px";
}

// The view that fits: the function in the middle, everything else in view (the zoom that fits the
// farthest node on each side).
function fitTarget() {
  const stage = $("#rel-stage");
  if (!stage || !R.shown) return null;
  const w = stage.clientWidth, h = stage.clientHeight;
  let ex = 40, ey = 40;
  R.shown.nodes.forEach((d, i) => {
    const p = R.slots[i];
    const label = Math.min(d.name.length, 30) * 6.6 + 16;
    ex = Math.max(ex, Math.abs(p.x) + (p.x >= 0 ? label : 14));
    ey = Math.max(ey, Math.abs(p.y) + 14);
  });
  return { k: Math.max(0.15, Math.min(1.4, (w / 2 - 12) / ex, (h / 2 - 12) / ey)), tx: w / 2, ty: h / 2 };
}

function fitGraph() {
  const t = fitTarget();
  if (!t) return;
  R.view = t;
  applyView();
}

// The camera a little nearer the fitted view; whether it still has a way to go.
function followView() {
  const t = fitTarget(), v = R.view;
  if (!t) return false;
  if (!v) { R.view = t; applyView(); return false; }
  const dk = t.k - v.k, dx = t.tx - v.tx, dy = t.ty - v.ty;
  v.k += dk * FOLLOW; v.tx += dx * FOLLOW; v.ty += dy * FOLLOW;
  applyView();
  return Math.abs(dk) > 0.002 || Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5;
}

function applyView() {
  const g = $("#rel-view"), svg = $("#rel-svg");
  if (!g || !R.view) return;
  g.setAttribute("transform", `translate(${R.view.tx.toFixed(1)},${R.view.ty.toFixed(1)}) scale(${R.view.k.toFixed(4)})`);
  svg.classList.toggle("zoomed", R.view.k >= LABEL_ZOOM);
}

function focusNode(id) {
  const svg = $("#rel-svg");
  if (!svg || !R.shown) return;
  const near = new Set([id]);
  R.shown.links.forEach((l, i) => {
    const on = l.source === id || l.target === id;
    if (on) { near.add(l.source); near.add(l.target); }
    const path = R.paths[i];
    if (path) path.classList.toggle("hl", on);
  });
  for (const g of R.els) g.classList.toggle("hl", near.has(g.dataset.id));
  svg.classList.add("focus");
}

function unfocus() {
  const svg = $("#rel-svg");
  if (!svg) return;
  svg.classList.remove("focus");
  for (const e of svg.querySelectorAll(".hl")) e.classList.remove("hl");
  if (R.sticky && R.shown && R.shown.byId.has(R.sticky)) focusNode(R.sticky);
}

function nodeTip(d) {
  const { links, byId } = R.shown;
  const rows = [];
  const line = (k, v) => rows.push(`<div class="tk">${k}</div><div>${v}</div>`);
  const others = (pred, pick) => links.filter(pred).map((l) => esc(byId.get(pick(l)).name));
  if (d.address != null) line("address", `<span class="mono">${hex(d.address)}</span>${d.size ? ` · ${d.size} bytes` : ""}`);
  if (d.kind === "function") {
    line("where", d.side === "center" ? "the function shown" : d.side === "referred" ? "its address is taken here"
      : `${d.side === "both" ? "caller and callee" : d.side} ${d.distance} call${d.distance > 1 ? "s" : ""} away`);
    if (d.origin) line("origin", `${dot(d.origin)} ${esc(ORIGINS[d.origin].long)}${d.readable ? ", readable C accepted" : ""}`);
    if (d.more && (d.more.callers || d.more.callees)) line("beyond", `${d.more.callers} more callers, ${d.more.callees} more callees`);
    if (d.facts === "pending") line("read from", "its C is being read");
    else if (d.facts === "readable C") line("read from", "its readable C");
    else if (d.facts === "listing") line("read from", `its listing only (each address "uses"): ${esc(d.why || "")}`);
    else if (d.facts === null) line("read from", "not read: outside the depth");
    else line("read from", `<span class="bad">${esc(d.why || "nothing")}</span>`);
  } else if (d.kind === "text") {
    line("text", `<span class="mono">${esc(d.text)}</span>`);
    line("used by", others((l) => l.target === d.id, (l) => l.source).join(", "));
  } else if (d.kind === "structure") {
    line("fields", d.fields.slice(0, 10).map(([off, type, field]) => `<span class="mono">${field} ${type} +0x${off.toString(16)}</span>`).join("<br>") +
      (d.fields.length > 10 ? `<br>and ${d.fields.length - 10} more` : ""));
    line("used by", links.filter((l) => l.target === d.id && l.kind === "uses-structure")
      .map((l) => `${esc(byId.get(l.source).name)} (${esc(l.edges[0].variables.join(", "))})`).join(", "));
  } else {
    if (d.types && d.types.length) line("accessed as", esc(d.types.join(", ")));
    if (d.default && d.default !== d.name) line("default name", `<span class="mono">${esc(d.default)}</span>`);
    const read = others((l) => l.target === d.id && l.read, (l) => l.source);
    const written = others((l) => l.target === d.id && l.write, (l) => l.source);
    const used = others((l) => l.target === d.id && l.uses && !l.read && !l.write, (l) => l.source);
    if (read.length) line("read by", read.join(", "));
    if (written.length) line("written by", written.join(", "));
    if (used.length) line("named by", used.join(", "));
    if (d.kind === "table") line("holds", others((l) => l.source === d.id, (l) => l.target).join(", ") + (d.words ? ` (its first ${d.words} words read)` : ""));
  }
  if (d.naming) {
    let v = NAMING[d.naming];
    if (d.proposed) v += `: <span class="mono">${esc(d.proposed.name)}</span>${d.proposed.what ? " — " + esc(d.proposed.what) : ""}`;
    line("name", v);
  }
  const hint = d.kind !== "function" ? "click: keep it lit" : d.id === "f:" + R.graph.center
    ? "double-click or Shift+click: its code · drag: move it" : "click: its relations · Shift+click: its code · drag: move it";
  return `<div class="tip-h">${esc(d.name)} <span class="sub">${esc(KIND_LONG[d.kind] || d.kind)}</span></div>` +
    `<div class="tip-g">${rows.join("")}</div><div class="tip-hint">${hint}</div>`;
}

function showTip(event, html) {
  const tip = $("#tip");
  tip.innerHTML = html;
  tip.classList.add("wide");
  tip.hidden = false;
  const x = Math.min(event.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
  const y = event.clientY + 16 + tip.offsetHeight > window.innerHeight ? event.clientY - tip.offsetHeight - 12 : event.clientY + 16;
  tip.style.transform = `translate(${Math.max(8, x)}px, ${Math.max(8, y)}px)`;
}

function hideTip() {
  const tip = $("#tip");
  tip.hidden = true;
  tip.classList.remove("wide");
}

function graphPoint(event, svg, v) {
  const box = svg.getBoundingClientRect();
  return { x: (event.clientX - box.left - v.tx) / v.k, y: (event.clientY - box.top - v.ty) / v.k };
}

// Zooming a view {k, tx, ty} by a factor about a point of its element.
function zoomAt(v, factor, px, py, min = 0.1, max = 4) {
  const k = Math.max(min, Math.min(max, v.k * factor));
  v.tx = px - (px - v.tx) * (k / v.k);
  v.ty = py - (py - v.ty) * (k / v.k);
  v.k = k;
}

// Two fingers on a graph zoom it: the pointers down, and the distance between them when the second came.
function pinch(state, event, element, apply) {
  const pointers = state.pointers || (state.pointers = new Map());
  if (event.type === "pointerdown") pointers.set(event.pointerId, [event.clientX, event.clientY]);
  else if (event.type === "pointerup" || event.type === "pointercancel") { pointers.delete(event.pointerId); state.pinch = null; return false; }
  else if (pointers.has(event.pointerId)) pointers.set(event.pointerId, [event.clientX, event.clientY]);
  if (pointers.size !== 2) return false;
  const [[ax, ay], [bx, by]] = [...pointers.values()];
  const d = Math.hypot(bx - ax, by - ay) || 1, box = element.getBoundingClientRect();
  const mx = (ax + bx) / 2 - box.left, my = (ay + by) / 2 - box.top;
  if (!state.pinch) { state.pinch = { d }; state.drag = null; return true; }
  apply(d / state.pinch.d, mx, my);
  state.pinch.d = d;
  return true;
}

function wireRelations() {
  const main = $("#main");
  main.addEventListener("pointerdown", (e) => {
    const svg = e.target.closest("#rel-svg");
    if (!svg || !R.view) return;
    svg.setPointerCapture(e.pointerId);
    if (pinch(R, e, svg) || e.button !== 0) return;
    const node = e.target.closest(".node");
    R.drag = { id: node ? node.dataset.id : null, x: e.clientX, y: e.clientY, moved: false, tx: R.view.tx, ty: R.view.ty };
  });
  main.addEventListener("pointermove", (e) => {
    const svg = e.target.closest("#rel-svg") || (R.drag || R.pinch ? $("#rel-svg") : null);
    if (svg && R.view && pinch(R, e, svg, (f, px, py) => { zoomAt(R.view, f, px, py); R.touched = true; applyView(); })) return;
    if (R.drag && R.view && svg) {
      const dx = e.clientX - R.drag.x, dy = e.clientY - R.drag.y;
      if (!R.drag.moved && dx * dx + dy * dy < 16) return;
      R.drag.moved = true;
      hideTip();
      R.touched = true;
      if (R.drag.id) {
        const p = graphPoint(e, svg, R.view), i = R.index.get(R.drag.id);
        if (i == null) return;
        if (still()) {                  // (no motion: the node alone, left where it is dropped)
          Object.assign(R.slots[i], p);
          R.sim.x[i] = p.x; R.sim.y[i] = p.y;
          placeNodes(i);
          placeEdges(i);
        } else {
          R.sim.fix(i, p.x, p.y);
          R.sim.target = DRAG_ALPHA;
          R.sim.heat(DRAG_ALPHA);
          if (R.drag.id === "f:" + R.graph.center) R.home = false;
          kick();
        }
      } else {
        R.view.tx = R.drag.tx + dx;
        R.view.ty = R.drag.ty + dy;
        svg.classList.add("panning");
        applyView();
      }
      return;
    }
    if (!svg || !R.shown || e.target.closest("#rel-svg") !== svg) return;
    const node = e.target.closest(".node");
    if (!node) { if (!$("#tip").hidden) { hideTip(); unfocus(); } return; }
    const d = R.shown.byId.get(node.dataset.id);
    if (!d) return;
    focusNode(d.id);
    showTip(e, nodeTip(d));
  });
  const up = (e) => {
    const svg = $("#rel-svg");
    if (svg) pinch(R, e, svg);
    const drag = R.drag;
    R.drag = null;
    if (svg) svg.classList.remove("panning");
    if (!drag || !R.shown) return;
    if (drag.moved) {
      const i = drag.id ? R.index.get(drag.id) : null;
      if (i != null && !still()) {      // released: it settles again with the others
        R.sim.free(i);
        R.sim.target = 0;
        R.sim.heat(DRAG_ALPHA);
        kick();
      }
      return;
    }
    if (e.type === "pointercancel") return;
    if (!drag.id) { R.sticky = null; unfocus(); return; }
    const d = R.shown.byId.get(drag.id);
    if (d && d.kind === "function" && e.shiftKey) {
      hideTip();
      location.hash = fnLink(d.name);                   // (Shift: its code)
    } else if (d && d.kind === "function" && d.id !== "f:" + R.graph.center) {
      hideTip();
      location.hash = relLink(d.name);
    } else if (d) {
      R.sticky = R.sticky === d.id ? null : d.id;
      unfocus();
    }
  };
  main.addEventListener("pointerup", up);
  main.addEventListener("pointercancel", up);
  main.addEventListener("dblclick", (e) => {
    const node = e.target.closest("#rel-svg .node");
    if (!node || !R.shown) return;
    const d = R.shown.byId.get(node.dataset.id);
    if (d && d.kind === "function") location.hash = fnLink(d.name);
  });
  main.addEventListener("wheel", (e) => {
    if (!e.target.closest("#rel-svg") || !R.view) return;
    e.preventDefault();
    const box = $("#rel-svg").getBoundingClientRect();
    zoomAt(R.view, Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0015)), e.clientX - box.left, e.clientY - box.top);
    R.touched = true;
    applyView();
  }, { passive: false });
  main.addEventListener("click", (e) => {
    const depth = e.target.closest("#rel-depth button");
    if (depth) {
      R.depth = +depth.dataset.depth;
      prefs.set("rel-depth", R.depth);
      for (const b of $("#rel-depth").children) b.classList.toggle("on", +b.dataset.depth === R.depth);
      if (S.current) showRelations(S.current.header.name, renders);
      return;
    }
    const chip = e.target.closest("#rel-kinds .chip");
    if (chip) {
      const k = chip.dataset.group;
      if (R.groups.has(k)) R.groups.delete(k); else R.groups.add(k);
      prefs.set("rel-groups", [...R.groups]);
      chip.classList.toggle("on", R.groups.has(k));
      if (R.graph) { layoutGraph(false); drawGraph(); applyView(); relNote(); kick(); }
      return;
    }
    if (e.target.closest("#rel-fit")) { refit(); return; }
    const mode = e.target.closest("#modes button");
    if (mode && S.current) location.hash = modeHash(mode.dataset.mode, S.current.header.name);
  });
  window.addEventListener("resize", () => {
    if (S.view === "function" && S.mode === "relations" && $("#rel-stage")) { sizeStage("rel-stage"); if (!R.touched) refit(); }
  });
}

// The camera back to the fitted view: at once without motion, else gliding there.
function refit() {
  R.touched = false;
  if (still()) fitGraph(); else kick();
}

// ---- the whole program: dread groups' record (/api/program) on a canvas ----
//
// Each group dread groups finds is a node, sized by its functions, its ring the share of them named
// and named by a proposal; groups are joined by the calls between their functions (an arrow at the
// callee's end), the addresses taken and the memory and texts both touch. A click opens a group into
// its functions in place (they burst from where it was); the label above them closes it again. A
// small program opens with all its functions (PROGRAM.atOnce). The same simulation as the relations,
// with a repulsion that falls as 1/d, Barnes-Hut's beyond sim.js EXACT_MAX nodes, an open group's
// functions pulled towards their centre; drawn on a canvas, which keeps thousands of nodes moving
// where SVG would not. Labels are drawn in screen pixels; a function's label shows when few are
// shown, when zoomed in, or when it is lit.

const PROGRAM = {
  atOnce: 500,          // up to this many functions the program opens with all of them
  repel: 18,            // the repulsion of two functions (as 1/d)
  gravity: 0.02,        // every node pulled towards the middle, per unit of distance
  cohesion: 0.12,       // an open group's functions pulled towards their centre
  apart: 0.3,           // how hard two groups' discs that overlap push apart, per unit of overlap
  gap: 26,              // and the space they keep between them
  spring: 0.7,          // a link's strength, over the smaller number of links of its ends
  across: 0.06,         // ... times this between functions of two groups (the groups hold together, not the calls)
  labels: 70,           // up to this many nodes every label shows; beyond, zoomed in or lit
  labelZoom: 1.1,
  maxLabels: 400,       // labels drawn in one frame at most
  heavy: 400,           // a group's links drawn by weight, with arrows: the heaviest this many; the rest as hairlines
};
const P = {
  data: null, stamp: null, open: new Set(), sim: null, nodes: [], links: [], index: new Map(), adj: [], pos: new Map(),
  slots: [], home: [], members: [], groupOf: new Int32Array(0), fnIndex: new Map(), calls: null, view: null,
  touched: false, hover: -1, sticky: null, lit: null, drag: null, frame: 0, last: 0, acc: 0, timer: null,
  colors: null, labels: [], hullOf: new Map(), settling: false,
};

const progLink = (name) => "#/p" + (name ? "/" + encodeURIComponent(name) : "");

function programHTML() {
  return `
    <div class="fn-head compact">
      <div class="fn-title"><h1>Whole program</h1><span class="badge" id="prog-badge">dread groups</span>
        <span class="grow"></span>
        <div class="seg" id="prog-mode"><button data-open="none" title="Each group one node">Groups</button><button data-open="all" title="Every function, in its group">Functions</button></div>
        <button id="prog-fit" title="Fit the graph (f)">Fit</button></div>
    </div>
    <section class="rel" id="prog">
      <div class="rel-bar"><span class="rel-note" id="prog-note"></span></div>
      <div class="rel-stage" id="prog-stage">
        <canvas class="graph" id="prog-canvas" role="img" aria-label="The whole program: its groups of functions and the calls between them"></canvas>
        <div class="rel-legend" id="prog-legend">${progLegendHTML()}</div>
      </div>
    </section>`;
}

function progLegendHTML() {
  const sw = (cls, text) => `<span class="lg"><svg viewBox="-9 -9 18 18" class="glyph ${cls}">${shapePath("function")}</svg>${text}</span>`;
  const ln = (cls, text) => `<span class="lg"><svg viewBox="0 0 22 8" class="line"><path class="edge ${cls}" d="M1,4H21"/></svg>${text}</span>`;
  return `<div class="lg-row"><span class="lg"><svg viewBox="-9 -9 18 18" class="glyph"><circle class="grp" r="7.5"/><path class="grp-ring" d="M0,-6A6,6 0 1,1 -5.2,3"/></svg>a group: its size the functions, its ring how many are named</span></div>` +
    `<div class="lg-row">${sw("n-named", "named")}${sw("n-proposed", "name proposed")}${sw("n-unnamed", "no name yet")}</div>` +
    `<div class="lg-row">${ln("e-calls", "calls")}${ln("e-refers-to-function", "address taken")}${ln("e-uses-structure", "memory or texts both use")}</div>` +
    `<div class="lg-row"><span>click a group: its functions · its label: back to one node</span></div>`;
}

function progNote(html) {
  const box = $("#prog-note");
  if (!box) return;
  if (html != null) { box.innerHTML = html; return; }
  const d = P.data;
  if (!d) return;
  const open = P.open.size, functions = P.nodes.filter((n) => n.kind === "function").length;
  box.innerHTML = `${d.counts.functions} functions in ${d.counts.groups} groups · ${d.counts.calls} calls` +
    ` · showing ${P.nodes.length - functions} group${P.nodes.length - functions === 1 ? "" : "s"}` +
    (open ? ` and ${functions} function${functions === 1 ? "" : "s"} of ${open} open` : "") +
    (P.settling ? ` · <span class="busy-dot"></span>settling` : "");
}

async function showProgram(name, keep = false) {
  const mine = ++renders;
  stopRelations();
  const main = $("#main");
  if (S.view !== "program" || !$("#prog-canvas")) {
    if (P.frame) cancelAnimationFrame(P.frame);
    P.frame = 0;
    main.innerHTML = programHTML();
    main.scrollTop = 0;
  }
  S.view = "program";
  S.current = null;
  document.title = "Whole program · DREAD";
  renderList();
  sizeStage("prog-stage");
  if (!keep || name !== P.focus) P.focus = name;
  await loadProgram(mine);
}

async function loadProgram(mine) {
  clearTimeout(P.timer);
  let d;
  try {
    d = await api("/api/program");
  } catch (e) {
    if (mine === renders) progNote(`<span class="bad">${esc(e.message)}</span>`);
    return;
  }
  if (mine !== renders || S.view !== "program") return;
  watch(d.status);
  if (d.waiting || d.error) {
    progNote(d.error ? `<span class="bad">${esc(d.error)}</span>` : d.waiting === "callers" ? `<span class="busy-dot"></span>finding the callers`
      : `<span class="busy-dot"></span>grouping the functions (dread groups)${d.seconds >= 2 ? `, ${Math.round(d.seconds)} s` : ""}`);
    if (d.waiting) P.timer = setTimeout(() => { if (mine === renders) loadProgram(mine); }, 800);
    return;
  }
  const stamp = d.groups.map((g) => g.members).join(" ") + "|" + d.functions.map((f) => f.name).join(" ");
  const fresh = stamp !== P.stamp;
  if (fresh) {
    if (P.stamp === null) P.open = new Set(d.functions.length <= PROGRAM.atOnce ? d.groups.map((g) => g.group) : []);
    else P.open = new Set([...P.open].filter((g) => g < d.groups.length));
    prepareProgram(d);
    P.stamp = stamp;
  } else {
    P.data = d;                         // (the names may have changed, not the groups)
  }
  P.colors = progColors();
  P.sticky = null;
  if (P.focus && P.fnIndex.has(P.focus)) {
    const g = P.groupOf[P.fnIndex.get(P.focus)];
    if (g >= 0 && !P.open.has(g)) { P.open.add(g); P.sim = null; }
    P.sticky = "f:" + P.focus;
  }
  const badge = $("#prog-badge");
  badge.textContent = d.groups.some((g) => g.from === "pilot") ? "dread groups, named by dread pilot" : "dread groups";
  badge.title = d.groups_from === "kept" ? "the groups dread groups kept in BUILD/groups for these tables"
    : "the groups dread groups found now (kept in BUILD/groups for the next look)";
  if (fresh || !P.sim) buildProgram(P.pos.size ? 0.6 : 1);
  else if (!P.sim.rested) progKick();
  progMarkMode();
  litFrom(stickyIndex());
  if (!P.view) progFit();
  if (still()) progSettle();
  else progKick();
  progNote();
}

function prepareProgram(d) {
  P.data = d;
  P.members = d.groups.map(() => []);
  P.groupOf = new Int32Array(d.functions.length).fill(-1);
  d.functions.forEach((f, i) => {
    if (f.group != null && P.members[f.group]) { P.groupOf[i] = f.group; P.members[f.group].push(i); }
  });
  P.fnIndex = new Map(d.functions.map((f, i) => [f.name, i]));
  const calls = { out: new Int32Array(d.functions.length), in: new Int32Array(d.functions.length) };
  for (const [i, j] of d.calls) { calls.out[i] += 1; calls.in[j] += 1; }
  P.calls = calls;
  P.home = homeOfGroups(d);
}

const groupRadius = (g) => 7 + 2.7 * Math.sqrt(g.functions);
const fnRadius = (f) => 3.2 + Math.min(4.5, Math.sqrt(f.size) / 7);
const groupCharge = () => 0.6;          // (a group is kept apart by its disc: the push only spreads them)
const spiral = (i, step) => {             // the golden angle: points spread evenly round a middle
  const r = step * Math.sqrt(i + 0.5), a = i * 2.399963;
  return [r * Math.cos(a), r * Math.sin(a)];
};

function progOptions() {
  return { repel: PROGRAM.repel, power: 1, gravity: PROGRAM.gravity, theta: 0.9, near: 2, friction: 0.85, decay: 0.024,
    maxSpeed: 30, restSpeed: 0.03 };
}

function linkWeight(l) {
  return l.calls[0] + l.calls[1] + l.refers[0] + l.refers[1] + l.shared;
}

// Where each group would rest with every group one node: the start of the groups and of the functions
// burst from them (the same physics, run to rest at once; a few hundred groups at most).
function homeOfGroups(d) {
  const n = d.groups.length, sim = new Sim(progOptions()).size(n), deg = new Float64Array(n);
  d.groups.forEach((g, i) => { [sim.x[i], sim.y[i]] = spiral(i, 26); sim.q[i] = groupCharge(g); });
  for (const e of d.between) { deg[e.a] += 1; deg[e.b] += 1; }
  const w = (e) => e.calls[0] + e.calls[1] + e.refers[0] + e.refers[1] + Object.values(e.shared).reduce((t, v) => t + v, 0);
  sim.links(d.between.map((e) => e.a), d.between.map((e) => e.b),
    d.between.map((e) => groupRadius(d.groups[e.a]) + groupRadius(d.groups[e.b]) + 36),
    d.between.map((e) => PROGRAM.spring * Math.min(1, 0.35 + 0.25 * Math.log2(1 + w(e))) / Math.max(1, Math.min(deg[e.a], deg[e.b]))));
  sim.heat(1).run(SETTLE_TICKS);
  return d.groups.map((g, i) => ({ x: sim.x[i], y: sim.y[i] }));
}

// The nodes shown (a closed group one node, an open one its functions) and the links between them
// (dread groups' calls, taken addresses and shared things summed per pair of nodes), and a new
// simulation seeded with where each node was: a group that closes starts at its functions' centre,
// the functions of a group that opens around where it was.
function buildProgram(alpha) {
  const d = P.data, nodes = [], index = new Map();
  for (const g of d.groups) {
    if (P.open.has(g.group)) {
      for (const fi of P.members[g.group]) {
        const f = d.functions[fi];
        index.set("f:" + f.name, nodes.length);
        nodes.push({ id: "f:" + f.name, kind: "function", fi, g: g.group, r: fnRadius(f) });
      }
    } else {
      index.set("g:" + g.group, nodes.length);
      nodes.push({ id: "g:" + g.group, kind: "group", g: g.group, r: groupRadius(g) });
    }
  }
  const n = nodes.length;
  const at = (fi) => {
    const g = P.groupOf[fi];
    return g < 0 ? undefined : P.open.has(g) ? index.get("f:" + d.functions[fi].name) : index.get("g:" + g);
  };
  const pairs = new Map(), links = [];
  const link = (a, b) => {
    const s = Math.min(a, b), t = Math.max(a, b), key = s * n + t;
    let l = pairs.get(key);
    if (!l) { l = { s, t, calls: [0, 0], refers: [0, 0], shared: 0, kinds: null }; pairs.set(key, l); links.push(l); }
    return l;
  };
  for (const [i, j] of d.calls) {
    const a = at(i), b = at(j);
    if (a !== undefined && b !== undefined && a !== b) link(a, b).calls[a < b ? 0 : 1] += 1;
  }
  for (const [i, j] of d.refers) {
    const a = at(i), b = at(j);
    if (a !== undefined && b !== undefined && a !== b) link(a, b).refers[a < b ? 0 : 1] += 1;
  }
  for (const e of d.between) {
    if (P.open.has(e.a) || P.open.has(e.b)) continue;
    const total = Object.values(e.shared).reduce((t, v) => t + v, 0);
    if (!total) continue;
    const l = link(index.get("g:" + e.a), index.get("g:" + e.b));
    l.shared = total;
    l.kinds = e.shared;
  }
  P.adj = nodes.map(() => []);
  links.forEach((l, i) => { P.adj[l.s].push(i); P.adj[l.t].push(i); l.w = linkWeight(l); });
  const grouped = links.filter((l) => nodes[l.s].kind === "group" || nodes[l.t].kind === "group");
  grouped.sort((a, b) => b.w - a.w || a.s - b.s || a.t - b.t).forEach((l, rank) => { l.heavy = rank < PROGRAM.heavy; });
  const sim = new Sim(progOptions()).size(n), old = P.pos, spread = old.size ? 1 : 0.3;
  const centre = (g) => {
    let sx = 0, sy = 0, m = 0;
    for (const fi of P.members[g]) {
      const p = old.get("f:" + d.functions[fi].name);
      if (p) { sx += p.x; sy += p.y; m += 1; }
    }
    return m ? { x: sx / m, y: sy / m } : null;
  };
  const homeOf = (g) => ({ x: P.home[g].x * spread, y: P.home[g].y * spread });
  const bases = new Map(), order = new Map();
  nodes.forEach((node, i) => {
    let p = old.get(node.id);
    if (!p && node.kind === "group") p = centre(node.g) || homeOf(node.g);
    else if (!p) {
      if (!bases.has(node.g)) bases.set(node.g, old.get("g:" + node.g) || centre(node.g) || homeOf(node.g));
      const j = order.get(node.g) || 0, [ox, oy] = spiral(j, 4), base = bases.get(node.g);
      order.set(node.g, j + 1);
      p = { x: base.x + ox, y: base.y + oy };
    }
    sim.x[i] = p.x; sim.y[i] = p.y; sim.vx[i] = p.vx || 0; sim.vy[i] = p.vy || 0;
    sim.q[i] = node.kind === "group" ? groupCharge(d.groups[node.g]) : 1;
  });
  const deg = new Float64Array(n);
  for (const l of links) { deg[l.s] += 1; deg[l.t] += 1; }
  const inside = (l) => nodes[l.s].g === nodes[l.t].g;
  sim.links(links.map((l) => l.s), links.map((l) => l.t),
    links.map((l) => nodes[l.s].r + nodes[l.t].r + (inside(l) ? 16 : 40)),
    links.map((l) => PROGRAM.spring * (inside(l) ? 1 : PROGRAM.across) * Math.min(1, 0.35 + 0.25 * Math.log2(1 + l.w)) /
      Math.max(1, Math.min(deg[l.s], deg[l.t]))));
  // the groups apart: each a disc (a closed one its node, an open one its functions' centre and spread)
  // that pushes the discs it overlaps, all its nodes alike
  const discs = [...nodes.reduce((m, node, i) => m.set(node.g, [...(m.get(node.g) || []), i]), new Map()).values()];
  const E = discs.length, cx = new Float64Array(E), cy = new Float64Array(E), cr = new Float64Array(E);
  const ex = new Float64Array(E), ey = new Float64Array(E);
  sim.forces.push((s) => {
    for (let e = 0; e < E; e++) {
      const m = discs[e];
      let sx = 0, sy = 0;
      for (const i of m) { sx += s.x[i]; sy += s.y[i]; }
      cx[e] = sx / m.length; cy[e] = sy / m.length;
      if (m.length === 1) { cr[e] = nodes[m[0]].kind === "group" ? 1.35 * nodes[m[0]].r + 12 : nodes[m[0]].r; continue; }
      let s2 = 0;
      for (const i of m) s2 += (s.x[i] - cx[e]) ** 2 + (s.y[i] - cy[e]) ** 2;
      cr[e] = 1.5 * Math.sqrt(s2 / m.length) + 8;
    }
    ex.fill(0); ey.fill(0);
    for (let a = 0; a < E; a++) {
      for (let b = a + 1; b < E; b++) {
        const dx = cx[b] - cx[a], dy = cy[b] - cy[a], d = Math.sqrt(dx * dx + dy * dy) || 0.01;
        const overlap = cr[a] + cr[b] + PROGRAM.gap - d;
        if (overlap <= 0) continue;
        const f = overlap * PROGRAM.apart / d, wa = discs[b].length / (discs[a].length + discs[b].length);
        ex[a] -= dx * f * wa; ey[a] -= dy * f * wa;
        ex[b] += dx * f * (1 - wa); ey[b] += dy * f * (1 - wa);
      }
    }
    for (let e = 0; e < E; e++) for (const i of discs[e]) { s.fx[i] += ex[e]; s.fy[i] += ey[e]; }
  });
  const member = Int32Array.from(nodes, (node) => node.kind === "function" ? node.g : -1);
  if (member.some((g) => g >= 0)) {
    const gx = new Float64Array(d.groups.length), gy = new Float64Array(d.groups.length), gm = new Float64Array(d.groups.length);
    sim.forces.push((s) => {             // an open group's functions pulled towards their centre
      gx.fill(0); gy.fill(0); gm.fill(0);
      for (let i = 0; i < n; i++) { const g = member[i]; if (g >= 0) { gx[g] += s.x[i]; gy[g] += s.y[i]; gm[g] += 1; } }
      for (let i = 0; i < n; i++) {
        const g = member[i];
        if (g >= 0) { s.fx[i] += (gx[g] / gm[g] - s.x[i]) * PROGRAM.cohesion; s.fy[i] += (gy[g] / gm[g] - s.y[i]) * PROGRAM.cohesion; }
      }
    });
  }
  sim.heat(alpha);
  P.sim = sim;
  P.nodes = nodes;
  P.links = links;
  P.index = index;
  P.hover = -1;
  P.hullOf = new Map();
  nodes.forEach((node, i) => {
    if (node.kind !== "function") return;
    if (!P.hullOf.has(node.g)) P.hullOf.set(node.g, []);
    P.hullOf.get(node.g).push(i);
  });
  P.pos = new Map();
  P.slots = nodes.map((node, i) => {
    const p = { x: sim.x[i], y: sim.y[i], vx: sim.vx[i], vy: sim.vy[i] };
    P.pos.set(node.id, p);
    return p;
  });
  const drag = P.drag;
  if (drag && drag.moved && drag.id && index.has(drag.id)) {
    const i = index.get(drag.id);
    sim.fix(i, sim.x[i], sim.y[i]);
    sim.target = DRAG_ALPHA;
  }
}

function syncProgram() {
  const { x, y, vx, vy } = P.sim;
  P.slots.forEach((p, i) => { p.x = x[i]; p.y = y[i]; p.vx = vx[i]; p.vy = vy[i]; });
}

function progKick() {
  if (!P.frame && P.sim && $("#prog-canvas")) {
    P.last = 0;
    P.frame = requestAnimationFrame(progFrame);
  }
}

function progFrame(now) {
  P.frame = 0;
  const sim = P.sim;
  if (!sim || S.view !== "program" || !$("#prog-canvas")) return;
  if (still()) { progSettle(); return; }
  for (let t = steps(P, now); t > 0 && !sim.rested; t--) sim.tick();
  syncProgram();
  const easing = !P.touched && progFollow();
  drawProgram();
  if (!sim.rested || (P.drag && P.drag.moved && P.drag.id) || easing) P.frame = requestAnimationFrame(progFrame);
  else if (P.settling) { P.settling = false; progNote(); }
}

// Without motion: the layout to its rest, a slice of the main thread's time at a time, then drawn.
function progSettle() {
  const sim = P.sim;
  if (!sim) return;
  const start = performance.now();
  while (!sim.rested && performance.now() - start < 40) sim.tick();
  if (!sim.rested) {
    if (!P.settling) { P.settling = true; progNote(); }
    setTimeout(() => { if (P.sim === sim && S.view === "program") progSettle(); }, 0);
    return;
  }
  syncProgram();
  if (!P.touched) progFit(); else drawProgram();
  if (P.settling) { P.settling = false; progNote(); }
}

function progFitTarget() {
  const canvas = $("#prog-canvas");
  if (!canvas || !P.sim || !P.nodes.length) return null;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  P.nodes.forEach((node, i) => {
    const p = P.slots[i], r = node.r + (node.kind === "group" ? 18 : 6);
    x0 = Math.min(x0, p.x - r); x1 = Math.max(x1, p.x + r);
    y0 = Math.min(y0, p.y - r); y1 = Math.max(y1, p.y + r + (node.kind === "group" ? 14 : 0));
  });
  // (the labels are drawn in screen pixels: room for the functions' names on the right, the open groups' on top)
  const named = P.nodes.length - P.nodes.filter((n) => n.kind === "group").length <= PROGRAM.labels;
  const right = named ? Math.min(120, w * 0.22) : 12, left = P.open.size ? Math.min(40, w * 0.08) : 12;
  const above = P.open.size ? 26 : 12;
  const legend = $("#prog-legend"), below = legend && legend.offsetHeight ? legend.offsetHeight + 8 : 0;
  const k = Math.max(0.02, Math.min(2, (w - 12 - left - right) / Math.max(1, x1 - x0),
    (h - 24 - above - below) / Math.max(1, y1 - y0)));
  return { k, tx: (w + left - right) / 2 - (x0 + x1) / 2 * k, ty: (h + above - below) / 2 - (y0 + y1) / 2 * k };
}

function progFit() {
  const t = progFitTarget();
  if (!t) return;
  P.view = t;
  drawProgram();
}

function progFollow() {
  const t = progFitTarget(), v = P.view;
  if (!t) return false;
  if (!v) { P.view = t; return false; }
  const dk = t.k - v.k, dx = t.tx - v.tx, dy = t.ty - v.ty;
  v.k += dk * FOLLOW; v.tx += dx * FOLLOW; v.ty += dy * FOLLOW;
  return Math.abs(dk) > 0.0005 * t.k || Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5;
}

function progColors() {
  const v = (name) => cssVar(name);
  return { named: v("--g-named"), namedLine: v("--g-named-line"), proposed: v("--g-proposed"), proposedLine: v("--g-proposed-line"),
    unnamed: v("--g-unnamed"), unnamedLine: v("--g-unnamed-line"), call: v("--g-call"), shared: v("--g-struct"),
    refer: v("--g-weak"), text: v("--text"), muted: v("--muted"), faint: v("--faint"), panel: v("--panel"),
    group: v("--accent-bg"), groupLine: v("--accent-soft"), hull: v("--panel-2"), hullLine: v("--line-strong"),
    strong: v("--accent-strong"), mono: v("--mono") };
}

// The points' convex hull (Andrew's monotone chain) over node numbers, sorted in place by position.
function convexHull(idx, x, y) {
  idx.sort((a, b) => x[a] - x[b] || y[a] - y[b]);
  if (idx.length < 3) return idx.slice();
  const cross = (o, a, b) => (x[a] - x[o]) * (y[b] - y[o]) - (y[a] - y[o]) * (x[b] - x[o]);
  const lower = [], upper = [];
  for (const p of idx) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
    lower.push(p);
  }
  for (let j = idx.length - 1; j >= 0; j--) {
    const p = idx[j];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
    upper.push(p);
  }
  lower.pop();
  upper.pop();
  return lower.concat(upper);
}

// The canvas, drawn in as few paths as there are styles (a path of thousands of pieces costs about
// what one piece does; thousands of paths do not): the open groups' hulls, the links (inside a group,
// fainter across, a group's by weight with arrows at the callee's end), the functions by naming, the
// groups with their rings; what is not lit drawn dimmed first.
function drawProgram() {
  const canvas = $("#prog-canvas");
  if (!canvas || !P.sim || !P.view || !P.colors) return;
  const ratio = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== Math.round(w * ratio) || canvas.height !== Math.round(h * ratio)) {
    canvas.width = Math.round(w * ratio);
    canvas.height = Math.round(h * ratio);
  }
  const g = canvas.getContext("2d"), C = P.colors, v = P.view, { x, y } = P.sim, nodes = P.nodes, links = P.links;
  const d = P.data, px = 1 / v.k, lit = P.lit;
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.clearRect(0, 0, canvas.width, canvas.height);
  g.setTransform(ratio * v.k, 0, 0, ratio * v.k, ratio * v.tx, ratio * v.ty);
  const left = -v.tx / v.k, top = -v.ty / v.k, right = left + w / v.k, bottom = top + h / v.k;
  const seen = (i, r) => x[i] + r >= left && x[i] - r <= right && y[i] + r >= top && y[i] - r <= bottom;
  const passes = lit ? [false, true] : [true];          // (the dimmed first, the lit over them)
  const shown = (on, yes) => !lit || yes === on;
  const pad = 13;
  g.lineJoin = "round";
  g.lineCap = "round";
  // the open groups: one rounded hull behind their functions
  const hulls = [];
  for (const [gid, members] of P.hullOf) hulls.push([gid, convexHull(members, x, y), members]);
  for (const on of passes) {
    g.beginPath();
    let any = false;
    for (const [, hull, members] of hulls) {
      if (!shown(on, members.some((i) => lit && lit.has(i)))) continue;
      hull.forEach((i, j) => (j ? g.lineTo(x[i], y[i]) : g.moveTo(x[i], y[i])));
      if (hull.length === 1) g.lineTo(x[hull[0]] + 0.01, y[hull[0]]);
      g.closePath();
      any = true;
    }
    if (!any) continue;
    g.globalAlpha = on ? 1 : 0.35;
    g.lineWidth = 2 * pad + 2 * px;
    g.strokeStyle = C.hullLine;
    g.stroke();
    g.lineWidth = 2 * pad;
    g.strokeStyle = C.hull;
    g.stroke();
    g.fillStyle = C.hull;
    g.fill();
  }
  // the links
  const near = (L) => lit && lit.has(L.s) && lit.has(L.t) && (L.s === P.litFrom || L.t === P.litFrom);
  const ofGroup = (L) => nodes[L.s].kind === "group" || nodes[L.t].kind === "group";
  const faint = nodes.length > 1500 ? 0.35 : 0.55;
  for (const on of passes) {
    const alpha = lit ? (on ? 1 : 0.12) : faint;
    for (const inside of [false, true]) {
      g.beginPath();
      for (const L of links) {
        if (ofGroup(L) || (nodes[L.s].g === nodes[L.t].g) !== inside || (lit && near(L) !== on)) continue;
        g.moveTo(x[L.s], y[L.s]);
        g.lineTo(x[L.t], y[L.t]);
      }
      g.globalAlpha = inside ? alpha : alpha * 0.45;
      g.lineWidth = 0.9 * px;
      g.strokeStyle = C.call;
      g.stroke();
    }
    g.beginPath();                      // a group's lighter links, beyond the heaviest: hairlines
    for (const L of links) {
      if (!ofGroup(L) || L.heavy || (lit && near(L) !== on) || (lit && on)) continue;
      g.moveTo(x[L.s], y[L.s]);
      g.lineTo(x[L.t], y[L.t]);
    }
    g.globalAlpha = alpha * 0.5;
    g.lineWidth = 0.8 * px;
    g.strokeStyle = C.call;
    g.stroke();
    const buckets = new Map();          // the heaviest, and the lit: by width (in halves of a pixel) and kind
    for (const L of links) {
      if (!ofGroup(L) || (lit && near(L) !== on) || !(L.heavy || (lit && on))) continue;
      const calls = L.calls[0] + L.calls[1] + L.refers[0] + L.refers[1] > 0;
      const key = (calls ? "c" : "s") + Math.round(2 * Math.min(7, 0.8 + 1.1 * Math.log2(1 + L.w))) / 2;
      if (!buckets.has(key)) buckets.set(key, []);
      buckets.get(key).push(L);
    }
    g.globalAlpha = alpha;
    for (const [key, list] of buckets) {
      g.beginPath();
      for (const L of list) { g.moveTo(x[L.s], y[L.s]); g.lineTo(x[L.t], y[L.t]); }
      g.lineWidth = +key.slice(1) * px;
      g.strokeStyle = key[0] === "c" ? C.call : C.shared;
      g.setLineDash(key[0] === "c" ? [] : [5 * px, 3 * px]);
      g.stroke();
    }
    g.setLineDash([]);
    g.beginPath();
    for (const list of buckets.values()) {
      for (const L of list) {
        if (L.calls[0] || L.refers[0]) arrow(g, L.s, L.t, px);
        if (L.calls[1] || L.refers[1]) arrow(g, L.t, L.s, px);
      }
    }
    g.fillStyle = C.call;
    g.fill();
  }
  // the functions, a path for each naming
  for (const on of passes) {
    g.globalAlpha = on ? 1 : 0.2;
    for (const status of ["unnamed", "proposed", "named"]) {
      g.beginPath();
      let any = false;
      for (let i = 0; i < nodes.length; i++) {
        const node = nodes[i];
        if (node.kind !== "function" || d.functions[node.fi].naming !== status || !shown(on, lit && lit.has(i))) continue;
        if (!seen(i, node.r)) continue;
        g.moveTo(x[i] + node.r, y[i]);
        g.arc(x[i], y[i], node.r, 0, 2 * Math.PI);
        any = true;
      }
      if (!any) continue;
      g.fillStyle = C[status];
      g.fill();
      g.lineWidth = 1.1 * px;
      g.strokeStyle = C[status + "Line"];
      g.stroke();
    }
  }
  // the groups: their discs, then a ring of the share of their functions named and proposed
  for (const on of passes) {
    const mine = [];
    for (let i = 0; i < nodes.length; i++) {
      if (nodes[i].kind === "group" && seen(i, nodes[i].r) && shown(on, lit && lit.has(i))) mine.push(i);
    }
    if (!mine.length) continue;
    g.globalAlpha = on ? 1 : 0.25;
    g.beginPath();
    for (const i of mine) { g.moveTo(x[i] + nodes[i].r, y[i]); g.arc(x[i], y[i], nodes[i].r, 0, 2 * Math.PI); }
    g.fillStyle = C.group;
    g.fill();
    g.lineWidth = 1.2 * px;
    g.strokeStyle = C.groupLine;
    g.stroke();
    for (const status of ["named", "proposed"]) {
      g.beginPath();
      for (const i of mine) {
        const grp = d.groups[nodes[i].g], r = nodes[i].r, ring = Math.min(r * 0.45, Math.max(2.5 * px, r * 0.22));
        const share = grp.naming[status] / Math.max(1, grp.functions);
        if (!share) continue;
        const a0 = -Math.PI / 2 + (status === "named" ? 0 : 2 * Math.PI * grp.naming.named / Math.max(1, grp.functions));
        const a1 = a0 + share * 2 * Math.PI, outer = r - 0.6 * px, inner = Math.max(0, outer - ring);
        g.moveTo(x[i] + outer * Math.cos(a0), y[i] + outer * Math.sin(a0));
        g.arc(x[i], y[i], outer, a0, a1);
        g.arc(x[i], y[i], inner, a1, a0, true);
        g.closePath();
      }
      g.fillStyle = C[status];
      g.fill();
    }
  }
  g.globalAlpha = 1;
  if (P.hover >= 0 && P.nodes[P.hover]) {
    const i = P.hover, r = P.nodes[i].r;
    g.beginPath();
    g.arc(x[i], y[i], r + (P.nodes[i].kind === "group" ? 1 : 2.5) * px, 0, 2 * Math.PI);
    g.lineWidth = 1.6 * px;
    g.strokeStyle = C.strong;
    g.stroke();
  }
  drawLabels(g, ratio, hulls, seen, pad);
}

// An arrowhead at the edge of a node, as a piece of the path being made.
function arrow(g, from, to, px) {
  const { x, y } = P.sim, r = P.nodes[to].r, size = 7 * px;
  const dx = x[to] - x[from], dy = y[to] - y[from], d = Math.sqrt(dx * dx + dy * dy) || 1;
  const ux = dx / d, uy = dy / d, tipx = x[to] - ux * (r + 1.5 * px), tipy = y[to] - uy * (r + 1.5 * px);
  g.moveTo(tipx, tipy);
  g.lineTo(tipx - ux * size - uy * size * 0.45, tipy - uy * size + ux * size * 0.45);
  g.lineTo(tipx - ux * size + uy * size * 0.45, tipy - uy * size - ux * size * 0.45);
}

// The labels in screen pixels: the groups' (all of them when few, else the larger ones on the screen),
// above each open group its name (a click there closes it), and the functions' when few are shown,
// when zoomed in, or lit.
function drawLabels(g, ratio, hulls, seen, pad) {
  const C = P.colors, v = P.view, { x, y } = P.sim, nodes = P.nodes, d = P.data, lit = P.lit;
  g.setTransform(ratio, 0, 0, ratio, 0, 0);
  g.textBaseline = "middle";
  g.lineJoin = "round";
  const sx = (i) => x[i] * v.k + v.tx, sy = (i) => y[i] * v.k + v.ty;
  const text = (t, tx, ty, color, font) => {
    g.font = font;
    g.lineWidth = 3.5;
    g.strokeStyle = C.panel;
    g.strokeText(t, tx, ty);
    g.fillStyle = color;
    g.fillText(t, tx, ty);
  };
  const small = `11px ${C.mono}`, bold = `600 11.5px ${C.mono}`;
  const many = hulls.length + nodes.filter((n) => n.kind === "group").length > 80;
  const taken = [];                     // the boxes of the labels drawn: a label that would overlap one is left out
  const free = (x0, y0, x1, y1) => {
    for (const [a, b, c2, d2] of taken) if (x0 < c2 && x1 > a && y0 < d2 && y1 > b) return false;
    taken.push([x0, y0, x1, y1]);
    return true;
  };
  P.labels = [];
  const bySize = hulls.slice().sort((a, b) => b[2].length - a[2].length || a[0] - b[0]);
  for (const [gid, hull, members] of bySize) {
    let top = Infinity, cx = 0, x0 = Infinity, x1 = -Infinity;
    for (const i of hull) { x0 = Math.min(x0, x[i]); x1 = Math.max(x1, x[i]); }
    for (const i of members) { top = Math.min(top, y[i] - nodes[i].r); cx += x[i]; }
    cx /= members.length;
    const on = lit && members.some((i) => lit.has(i));
    if (many && (x1 - x0 + 2 * pad) * v.k < 70 && !on) continue;
    const grp = d.groups[gid], label = `${grp.label}  ${grp.functions}  ×`;
    const tx = cx * v.k + v.tx, ty = (top - pad) * v.k + v.ty - 9;
    if (tx < -200 || tx > g.canvas.width / ratio + 200 || ty < -20 || ty > g.canvas.height / ratio + 20) continue;
    g.font = bold;
    const width = g.measureText(label).width;
    if (!free(tx - width / 2 - 2, ty - 8, tx + width / 2 + 2, ty + 8) && !on) continue;
    g.globalAlpha = lit && !on ? 0.4 : 1;
    text(label, tx - width / 2, ty, C.text, bold);
    P.labels.push({ g: gid, x0: tx - width / 2 - 4, x1: tx + width / 2 + 4, y0: ty - 9, y1: ty + 9 });
  }
  g.globalAlpha = 1;
  const closed = [];
  for (let i = 0; i < nodes.length; i++) if (nodes[i].kind === "group" && seen(i, nodes[i].r)) closed.push(i);
  closed.sort((a, b) => (lit && lit.has(b)) - (lit && lit.has(a)) || nodes[b].r - nodes[a].r || a - b);
  for (const i of closed) {
    const node = nodes[i];
    if (many && node.r * v.k < 9 && !(lit && lit.has(i)) && i !== P.hover) continue;
    const grp = d.groups[node.g], label = grp.label.length > 26 ? grp.label.slice(0, 25) + "…" : grp.label;
    g.font = bold;
    const width = g.measureText(label).width, ty = sy(i) + node.r * v.k + 10;
    if (!free(sx(i) - width / 2 - 2, ty - 8, sx(i) + width / 2 + 2, ty + 20) && i !== P.hover) continue;
    g.globalAlpha = lit && !lit.has(i) ? 0.3 : 1;
    text(label, sx(i) - width / 2, ty, C.text, bold);
    const count = String(grp.functions);
    g.font = small;
    text(count, sx(i) - g.measureText(count).width / 2, ty + 13, C.muted, small);
  }
  const functions = nodes.length - nodes.filter((n) => n.kind === "group").length;
  const every = functions <= PROGRAM.labels || v.k >= PROGRAM.labelZoom;
  const first = [P.litFrom, P.hover].filter((i) => i >= 0 && nodes[i] && nodes[i].kind === "function");
  const order = [...new Set([...first, ...(lit ? [...lit] : []), ...(every ? nodes.keys() : [])])];
  let drawn = 0;
  for (const i of order) {                  // (the lit node's own label first, then its neighbours', then the rest)
    const node = nodes[i];
    if (drawn >= PROGRAM.maxLabels) break;
    if (node.kind !== "function" || !seen(i, node.r)) continue;
    const on = lit && lit.has(i);
    const name = d.functions[node.fi].name, lx = sx(i) + node.r * v.k + 4;
    g.font = small;
    if (!free(lx, sy(i) - 7, lx + g.measureText(name).width, sy(i) + 7) && !first.includes(i)) continue;
    g.globalAlpha = lit && !on ? 0.25 : 1;
    text(name, lx, sy(i), i === P.litFrom ? C.strong : C.text, small);
    drawn += 1;
  }
  g.globalAlpha = 1;
}

// What a pointer is on: an open group's label, a node, or nothing.
function progHit(event) {
  const canvas = $("#prog-canvas");
  if (!canvas || !P.sim || !P.view) return {};
  const box = canvas.getBoundingClientRect(), sx = event.clientX - box.left, sy = event.clientY - box.top;
  for (const L of P.labels) if (sx >= L.x0 && sx <= L.x1 && sy >= L.y0 && sy <= L.y1) return { label: L.g };
  const v = P.view, gx = (sx - v.tx) / v.k, gy = (sy - v.ty) / v.k, slack = 4 / v.k, { x, y } = P.sim;
  let best = -1, bd = Infinity;
  for (let i = 0; i < P.nodes.length; i++) {
    const dx = x[i] - gx, dy = y[i] - gy, d2 = dx * dx + dy * dy, r = P.nodes[i].r + slack;
    if (d2 <= r * r && d2 < bd) { best = i; bd = d2; }
  }
  return { node: best, x: gx, y: gy };
}

const stickyIndex = () => (P.sticky && P.index.has(P.sticky) ? P.index.get(P.sticky) : -1);

// A node and its neighbours lit, the rest dimmed (-1: none).
function litFrom(i) {
  P.litFrom = i;
  if (i < 0 || !P.nodes[i]) { P.lit = null; return; }
  const lit = new Set([i]);
  for (const l of P.adj[i]) { lit.add(P.links[l].s); lit.add(P.links[l].t); }
  P.lit = lit;
}

function progTip(i) {
  const node = P.nodes[i], d = P.data, rows = [];
  const line = (k, v) => rows.push(`<div class="tk">${k}</div><div>${v}</div>`);
  const joined = P.adj[i].map((l) => P.links[l]).sort((a, b) => b.w - a.w).slice(0, 5).map((L) => {
    const other = L.s === i ? L.t : L.s, o = P.nodes[other];
    const name = o.kind === "group" ? d.groups[o.g].label : d.functions[o.fi].name;
    const out = L.s === i ? L.calls[0] : L.calls[1], back = L.s === i ? L.calls[1] : L.calls[0];
    const parts = [];
    if (out) parts.push(`calls ${out}`);
    if (back) parts.push(`called ${back}`);
    if (L.refers[0] + L.refers[1]) parts.push(`${L.refers[0] + L.refers[1]} taken`);
    if (L.shared) parts.push(`${L.shared} shared`);
    return `<span class="mono">${esc(name)}</span> (${parts.join(", ")})`;
  });
  if (node.kind === "group") {
    const g = d.groups[node.g];
    const largest = P.members[node.g].slice().sort((a, b) => d.functions[b].size - d.functions[a].size).slice(0, 6)
      .map((fi) => esc(d.functions[fi].name));
    line("functions", `${g.functions} · ${size(g.bytes)} · <span class="mono">${hex(g.first)}–${hex(g.last)}</span>`);
    line("names", `${g.naming.named} named, ${g.naming.proposed} proposed, ${g.naming.unnamed} without`);
    if (g.from === "pilot") line("named", `by dread pilot${g.prefix ? `, prefix <span class="mono">${esc(g.prefix)}</span>` : ""}${g.what ? ": " + esc(g.what) : ""}`);
    else if (g.from === "prefix") line("named", `by the prefix ${g.common.functions} of its functions share`);
    else if (g.from === "function") line("named", "by its only function");
    else line("named", "not yet: no prefix most of its functions share, no name from dread pilot");
    line("largest", `<span class="mono">${largest.join(", ")}</span>`);
    if (joined.length) line("joined to", joined.join("<br>"));
    return `<div class="tip-h">${esc(g.label)} <span class="sub">group ${g.group}</span></div><div class="tip-g">${rows.join("")}</div>` +
      `<div class="tip-hint">click: its functions · drag: move it</div>`;
  }
  const f = d.functions[node.fi], g = d.groups[node.g];
  line("address", `<span class="mono">${hex(f.entry)}</span> · ${f.size} bytes`);
  line("group", `${esc(g.label)} <span class="sub">(group ${g.group}, ${g.functions} functions)</span>`);
  if (f.origin) line("origin", `${dot(f.origin)} ${esc(ORIGINS[f.origin].long)}`);
  line("name", NAMING[f.naming] + (f.proposed ? `: <span class="mono">${esc(f.proposed)}</span>` : ""));
  line("calls", `${P.calls.out[node.fi]} functions; called by ${P.calls.in[node.fi]}`);
  if (joined.length) line("joined to", joined.join("<br>"));
  return `<div class="tip-h">${esc(f.name)} <span class="sub">function</span></div><div class="tip-g">${rows.join("")}</div>` +
    `<div class="tip-hint">click: its relations · Shift+click: its code · drag: move it</div>`;
}

function progMarkMode() {
  const all = P.data && P.open.size === P.data.groups.length, none = !P.open.size;
  for (const b of document.querySelectorAll("#prog-mode button")) {
    b.classList.toggle("on", b.dataset.open === "all" ? all : none);
  }
}

// Groups opened or closed: the graph built anew from where its nodes are, moving on from there.
function progRegroup() {
  buildProgram(0.55);
  progMarkMode();
  litFrom(stickyIndex());
  progNote();
  if (still()) progSettle(); else progKick();
}

function progRedraw() {
  if (!P.frame) drawProgram();
}

function wireProgram() {
  const main = $("#main");
  const canvasOf = (e) => (e.target.id === "prog-canvas" ? e.target : null);
  main.addEventListener("pointerdown", (e) => {
    const canvas = canvasOf(e);
    if (!canvas || !P.view) return;
    canvas.setPointerCapture(e.pointerId);
    if (pinch(P, e, canvas) || e.button !== 0) return;
    const hit = progHit(e);
    P.drag = { id: hit.node >= 0 ? P.nodes[hit.node].id : null, label: hit.label ?? null, x: e.clientX, y: e.clientY,
      moved: false, tx: P.view.tx, ty: P.view.ty };
  });
  main.addEventListener("pointermove", (e) => {
    const canvas = canvasOf(e) || (P.drag || P.pinch ? $("#prog-canvas") : null);
    if (!canvas || !P.view) {
      if (S.view === "program" && P.hover >= 0) { P.hover = -1; litFrom(stickyIndex()); progRedraw(); }
      return;
    }
    if (pinch(P, e, canvas, (f, px, py) => { zoomAt(P.view, f, px, py, 0.02, 6); P.touched = true; progRedraw(); })) return;
    if (P.drag) {
      const dx = e.clientX - P.drag.x, dy = e.clientY - P.drag.y;
      if (!P.drag.moved && dx * dx + dy * dy < 16) return;
      P.drag.moved = true;
      hideTip();
      P.touched = true;
      if (P.drag.id) {
        const i = P.index.get(P.drag.id);
        if (i == null) return;
        const box = canvas.getBoundingClientRect(), v = P.view;
        const gx = (e.clientX - box.left - v.tx) / v.k, gy = (e.clientY - box.top - v.ty) / v.k;
        if (still()) {
          P.sim.x[i] = gx; P.sim.y[i] = gy;
          syncProgram();
          drawProgram();
        } else {
          P.sim.fix(i, gx, gy);
          P.sim.target = DRAG_ALPHA;
          P.sim.heat(DRAG_ALPHA);
          progKick();
        }
      } else {
        P.view.tx = P.drag.tx + dx;
        P.view.ty = P.drag.ty + dy;
        canvas.classList.add("panning");
        progRedraw();
      }
      return;
    }
    const hit = progHit(e);
    const i = hit.node ?? -1;
    canvas.classList.toggle("pointing", i >= 0 || hit.label != null);
    if (hit.label != null) {
      showTip(e, `<div class="tip-h">${esc(P.data.groups[hit.label].label)}</div><div class="tip-hint">click: back to one node</div>`);
    } else if (i >= 0) showTip(e, progTip(i));
    else hideTip();
    if (i !== P.hover) {
      P.hover = i;
      litFrom(i >= 0 ? i : stickyIndex());
      progRedraw();
    }
  });
  const up = (e) => {
    const canvas = $("#prog-canvas");
    if (canvas) { pinch(P, e, canvas); canvas.classList.remove("panning"); }
    const drag = P.drag;
    P.drag = null;
    if (!drag || !P.sim) return;
    if (drag.moved) {
      const i = drag.id ? P.index.get(drag.id) : null;
      if (i != null && !still()) {      // released: it settles again with the others
        P.sim.free(i);
        P.sim.target = 0;
        P.sim.heat(DRAG_ALPHA);
        progKick();
      }
      return;
    }
    if (e.type === "pointercancel") return;
    if (drag.label != null) {
      P.open.delete(drag.label);
      hideTip();
      progRegroup();
      return;
    }
    if (!drag.id) { P.sticky = null; litFrom(-1); progRedraw(); return; }
    const node = P.nodes[P.index.get(drag.id)];
    if (!node) return;
    hideTip();
    if (node.kind === "group") {
      P.open.add(node.g);
      progRegroup();
    } else {
      const name = P.data.functions[node.fi].name;
      location.hash = e.shiftKey ? fnLink(name) : relLink(name);
    }
  };
  main.addEventListener("pointerup", up);
  main.addEventListener("pointercancel", up);
  main.addEventListener("dblclick", (e) => {
    if (!canvasOf(e)) return;
    const hit = progHit(e);
    const node = hit.node >= 0 ? P.nodes[hit.node] : null;
    if (node && node.kind === "function") location.hash = fnLink(P.data.functions[node.fi].name);
  });
  main.addEventListener("wheel", (e) => {
    const canvas = canvasOf(e);
    if (!canvas || !P.view) return;
    e.preventDefault();
    const box = canvas.getBoundingClientRect();
    zoomAt(P.view, Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0015)), e.clientX - box.left, e.clientY - box.top, 0.02, 6);
    P.touched = true;
    progRedraw();
  }, { passive: false });
  main.addEventListener("mouseleave", () => {
    if (S.view === "program" && P.hover >= 0) { P.hover = -1; litFrom(stickyIndex()); progRedraw(); }
  });
  main.addEventListener("click", (e) => {
    const mode = e.target.closest("#prog-mode button");
    if (mode && P.data) {
      P.open = new Set(mode.dataset.open === "all" ? P.data.groups.map((g) => g.group) : []);
      P.touched = false;
      progRegroup();
      return;
    }
    if (e.target.closest("#prog-fit")) progRefit();
  });
  window.addEventListener("resize", () => {
    if (S.view !== "program" || !$("#prog-stage")) return;
    sizeStage("prog-stage");
    if (!P.touched) progRefit(); else progRedraw();
  });
}

function progRefit() {
  P.touched = false;
  if (still()) progFit(); else progKick();
}

// ---- search ----

let searchTimer = null, results = [], active = -1, searched = "";
async function search(q) {
  if (!q.trim()) { results = []; active = -1; searched = q; $("#results").hidden = true; return; }
  let found = [];
  try {
    found = await api("/api/search", { q });
  } catch (e) {
    found = [];
  }
  if (q !== $("#search").value) return;    // the text changed meanwhile: its own search follows
  results = found;
  searched = q;
  active = results.length ? 0 : -1;
  renderResults();
}

function renderResults() {
  const box = $("#results");
  box.hidden = false;
  if (!results.length) {
    box.innerHTML = `<div class="empty">Nothing found${S.pending.includes("texts") ? " yet (the text index is being built)" : ""}.</div>`;
    return;
  }
  box.innerHTML = results.map((r, i) => {
    const sub = r.kind === "text" ? `"${esc(r.text)}" at ${hex(r.address)}` : r.kind === "address" ? `${hex(r.address)} is in it` : hex(r.entry);
    return `<div class="result${i === active ? " active" : ""}" data-i="${i}">${dot(r.origin || "original")}<span class="nm">${esc(r.name)}</span>` +
      `<span class="kind">${r.kind}</span><span class="sub sub-line">${sub}</span></div>`;
  }).join("");
}

function openResult(i) {
  const r = results[i];
  if (!r) return;
  $("#results").hidden = true;
  $("#search").blur();
  location.hash = r.kind === "name" ? modeLink(r.name) : fnLink(r.name, r.address);
}

// ---- changes ----

function dialog({ title, hint, value = "", multiline = false, okLabel = "Save", ok }) {
  const box = $("#dialog"), form = $("#dialog-form");
  const input = $("#dialog-input"), area = $("#dialog-text"), error = $("#dialog-error");
  const field = multiline ? area : input;
  $("#dialog-title").textContent = title;
  $("#dialog-hint").textContent = hint;
  $("#dialog-ok").textContent = okLabel;
  input.hidden = multiline;
  area.hidden = !multiline;
  field.value = value;
  error.textContent = "";
  box.hidden = false;
  field.focus();
  field.select();
  return new Promise((resolve) => {
    const close = (result) => {
      box.hidden = true;
      form.onsubmit = null;
      box.onkeydown = null;
      resolve(result);
    };
    form.onsubmit = async (event) => {
      event.preventDefault();
      error.textContent = "";
      $("#dialog-ok").disabled = true;
      try {
        close(await ok(field.value));
      } catch (e) {
        error.textContent = e.message;
      } finally {
        $("#dialog-ok").disabled = false;
      }
    };
    $("#dialog-cancel").onclick = () => close(null);
    box.onkeydown = (event) => {
      if (event.key === "Escape") { event.preventDefault(); close(null); }
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && multiline) form.requestSubmit();
    };
  });
}

async function renameCurrent() {
  if (!S.current) return;
  const old = S.current.header.name;
  await dialog({
    title: `Rename ${old}`,
    hint: "Every table, case file and C file that names it changes (dread rename); then the C is compiled and the cases are loaded. If anything still uses the old name, nothing is changed.",
    value: old, okLabel: "Rename",
    ok: async (value) => {
      const name = value.trim();
      if (!name || name === old) throw new Error("Give it a new name.");
      const r = await api("/api/rename", {}, { old, new: name });
      await loadFunctions();
      history.replaceState(null, "", fnLink(name));
      await route();
      toast(`Renamed ${old} to ${name}: ${r.changed.length} file${r.changed.length === 1 ? "" : "s"} changed.`);
    },
  });
}

async function noteSelected() {
  const d = S.current;
  if (!d) return;
  const row = S.sel >= 0 ? d.rows[S.sel] : null;
  const address = row ? row.address : d.header.entry;
  const existing = address === d.header.entry ? d.header.notes
    : d.rows.filter((r) => r.kind === "note" && r.address === address).map((r) => r.text);
  await dialog({
    title: `Note at ${hex8(address)}${address === d.header.entry ? " (the entry)" : ""}`,
    hint: "Kept in annotations.txt, shown here and by dread flat. Several lines are fine; empty removes the note. Ctrl+Enter saves.",
    value: existing.join("\n"), multiline: true, okLabel: "Save",
    ok: async (text) => {
      await api("/api/annotate", {}, { address, text });
      await route(true);
      selectAddress(address);
      toast(text.trim() ? `Note at ${hex(address)} saved.` : `Note at ${hex(address)} removed.`);
    },
  });
}

// ---- keeping up with the server ----

function watch(status) {
  if (!status) return;
  S.pending = status.pending;
  if (!S.generation) S.generation = status.generation;
  const busy = $("#busy");
  busy.hidden = !S.pending.length;
  busy.textContent = S.pending.length ? "finding " + S.pending.map((k) => ANALYSES[k]).join(", ") : "";
}

async function poll() {
  let delay = 4000;
  try {
    const status = await api("/api/status");
    const waited = S.pending.length && !status.pending.length;
    if (status.generation !== S.generation) {
      S.generation = status.generation;
      await loadFunctions();
      await refreshView();
    } else if (waited) {
      await refreshView();
    }
    watch(status);
    if (S.pending.length) delay = 1200;
  } catch {
    delay = 8000;                       // the server went away; keep trying quietly
  }
  setTimeout(poll, delay);
}

async function refreshView() {
  await route(true);                    // what the address bar says, keeping selection and scroll
}

function showError(message) {
  S.view = "error";
  $("#main").innerHTML = `<div class="error-view"><b>${esc(message)}</b><p><a href="#/">Overview</a></p></div>`;
}

// ---- wiring ----

function wire() {
  $("#filter").addEventListener("input", (e) => { S.filter = e.target.value; applyFilter(); });
  $("#sort").addEventListener("change", (e) => { S.sort = e.target.value; prefs.set("sort", S.sort); applyFilter(); });
  $("#chips").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    const o = chip.dataset.origin;
    if (S.origins.has(o)) S.origins.delete(o); else S.origins.add(o);
    renderChips();
    applyFilter();
  });
  let ticking = false;
  $("#list").addEventListener("scroll", () => {
    if (!ticking) { ticking = true; requestAnimationFrame(() => { renderList(); ticking = false; }); }
  });
  $("#list").addEventListener("click", (e) => {
    const row = e.target.closest(".fnrow");
    if (row) location.hash = modeLink(row.dataset.name);
  });
  $("#menu").addEventListener("click", () => $("#side").classList.toggle("open"));
  $("#theme").addEventListener("click", cycleTheme);
  $("#help-button").addEventListener("click", () => { $("#help").hidden = false; });
  $("#help-close").addEventListener("click", () => { $("#help").hidden = true; });
  $("#help").addEventListener("click", (e) => { if (e.target.id === "help") $("#help").hidden = true; });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (S.view === "overview") drawMap();
    if (S.view === "program") { P.colors = progColors(); drawProgram(); }
  });
  window.addEventListener("resize", () => { renderList(); if (S.view === "overview") drawMap(); });
  window.addEventListener("hashchange", route);

  const searchBox = $("#search");
  searchBox.addEventListener("input", () => {
    clearTimeout(searchTimer);
    results = [];                       // Enter must never open a result of an older text
    active = -1;
    searchTimer = setTimeout(() => search(searchBox.value), 110);
  });
  searchBox.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (results.length) { active = (active + (e.key === "ArrowDown" ? 1 : results.length - 1)) % results.length; renderResults(); }
    } else if (e.key === "Enter") {
      e.preventDefault();
      clearTimeout(searchTimer);
      if (searched !== searchBox.value) search(searchBox.value).then(() => openResult(active));
      else openResult(active);
    } else if (e.key === "Escape") {
      searchBox.value = "";
      $("#results").hidden = true;
      searchBox.blur();
    }
  });
  searchBox.addEventListener("focus", () => { if (searchBox.value.trim() && results.length) renderResults(); });
  document.addEventListener("mousedown", (e) => {
    if (!e.target.closest(".search")) $("#results").hidden = true;
  });
  $("#results").addEventListener("mousedown", (e) => {
    const r = e.target.closest(".result");
    if (r) { e.preventDefault(); openResult(+r.dataset.i); }
  });

  const main = $("#main");
  main.addEventListener("click", (e) => {
    const label = e.target.closest("a[data-label]");
    if (label) { e.preventDefault(); goLabel(+label.dataset.label); return; }
    if (e.target.closest("#rename-btn")) return renameCurrent();
    if (e.target.closest("#note-btn")) return noteSelected();
    const tab = e.target.closest(".tab");
    if (tab) { S.tab = tab.dataset.tab; renderC(); return; }
    const by = e.target.closest("#by button");
    if (by) { S.by = by.dataset.by; prefs.set("by", S.by); renderOrigins(); return; }
    const item = e.target.closest(".legend .item");
    if (item) { S.origins = new Set([item.dataset.origin]); renderChips(); applyFilter(); $("#side").classList.add("open"); return; }
    const head = e.target.closest(".group .head");
    if (head) { const names = head.parentElement.querySelector(".names"); names.hidden = !names.hidden; return; }
    const row = e.target.closest(".ln");
    if (row && !e.target.closest("a") && (row.classList.contains("insn") || row.classList.contains("word"))) selectRow(+row.dataset.i, false);
  });
  main.addEventListener("mousemove", (e) => {
    const tip = $("#tip");
    if (e.target.closest("#rel-svg, #prog-canvas")) return;    // (the graphs have tips of their own)
    if (e.target.id !== "map") { tip.hidden = true; return; }
    const fn = mapHit(e);
    if (!fn) { tip.hidden = true; return; }
    tip.innerHTML = `${esc(fn.name)} <span class="sub">${hex(fn.entry)} · ${fn.size} bytes · ${esc(ORIGINS[fn.origin].long)}</span>`;
    tip.classList.remove("wide");
    tip.hidden = false;
    const x = Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
    tip.style.transform = `translate(${x}px, ${e.clientY + 16}px)`;
  });
  main.addEventListener("mouseleave", () => { $("#tip").hidden = true; });
  main.addEventListener("click", (e) => {
    if (e.target.id !== "map") return;
    const fn = mapHit(e);
    if (fn) location.hash = fnLink(fn.name);
  });

  document.addEventListener("keydown", (e) => {
    if (!$("#dialog").hidden) return;
    if (!$("#help").hidden) { if (e.key === "Escape" || e.key === "?") $("#help").hidden = true; return; }
    const typing = e.target.closest("input, textarea, select");
    if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
    const keys = {
      "/": () => $("#search").select(), g: () => $("#search").select(),
      j: () => moveSelection(1), ArrowDown: () => moveSelection(1), k: () => moveSelection(-1), ArrowUp: () => moveSelection(-1),
      Enter: followSelection, n: renameCurrent, ";": noteSelected,
      b: () => { S.showBytes = !S.showBytes; prefs.set("bytes", S.showBytes); const l = $("#listing"); if (l) l.classList.toggle("show-bytes", S.showBytes); },
      c: () => { S.showC = !S.showC; prefs.set("c", S.showC); const s = $("#split"); if (s) s.classList.toggle("no-c", !S.showC); },
      o: () => { location.hash = "#/"; }, t: cycleTheme, "?": () => { $("#help").hidden = false; },
      r: () => { const n = S.current.header.name; location.hash = S.mode === "relations" ? fnLink(n) : relLink(n); },
      f: () => { if (S.view === "program") progRefit(); else if (S.mode === "relations") refit(); },
      p: () => { location.hash = progLink(S.view === "function" && S.current ? S.current.header.name : null); },
      "[": () => stepFunction(-1), "]": () => stepFunction(1),
      Escape: () => { selectRow(-1); },
    };
    const action = keys[e.key];
    if (!action) return;
    if (S.view !== "function" && ["j", "k", "ArrowDown", "ArrowUp", "Enter", "n", ";", "Escape", "r"].includes(e.key)) return;
    if (e.key === "f" && S.view !== "program" && !(S.view === "function" && S.mode === "relations")) return;
    e.preventDefault();
    action();
  });
}

function stepFunction(step) {
  if (!S.order.length) return;
  const at = S.current ? S.order.findIndex((fn) => fn.name === S.current.header.name) : -1;
  const next = at < 0 ? 0 : Math.max(0, Math.min(S.order.length - 1, at + step));
  location.hash = modeLink(S.order[next].name);
}

async function start() {
  S.sort = prefs.get("sort", "address");
  S.showBytes = prefs.get("bytes", false);
  S.showC = prefs.get("c", true);
  S.by = prefs.get("by", "bytes");
  R.depth = [1, 2, 3].includes(prefs.get("rel-depth", 1)) ? prefs.get("rel-depth", 1) : 1;
  const groups = prefs.get("rel-groups", null);
  if (Array.isArray(groups)) R.groups = new Set(groups.filter((k) => GROUPS.some(([g]) => g === k)));
  $("#sort").value = S.sort;
  applyTheme();
  renderChips();
  wire();
  wireRelations();
  wireProgram();
  try {
    const o = await api("/api/overview");
    const images = o.image.images;
    $("#where").innerHTML = `<b>${esc(o.target.title)}</b>` + (images.length > 1
      ? `<select id="image-select" aria-label="Image">${images.map((n) => `<option${n === o.image.name ? " selected" : ""}>${esc(n)}</option>`).join("")}</select>`
      : ` · ${esc(o.image.name)}`);
    const select = $("#image-select");
    if (select) select.addEventListener("change", () => { location.search = "?image=" + encodeURIComponent(select.value); });
    S.generation = o.status.generation;
    await loadFunctions();
  } catch (e) {
    return showError(e.message);
  }
  await route();
  setTimeout(poll, 1500);
}

start();
