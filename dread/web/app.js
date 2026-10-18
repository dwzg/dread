// dread gui: one page, no framework, no build step. The server (dread/gui.py) answers /api/*;
// everything here is the view. Changes go through the server, which makes them as the command line does.

const TOKEN = document.querySelector('meta[name="dread-token"]').content;
const IMAGE = new URLSearchParams(location.search).get("image") || "";
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const hex = (n) => "0x" + n.toString(16);
const hex8 = (n) => n.toString(16).padStart(8, "0");
const fnLink = (name, at) => "#/f/" + encodeURIComponent(name) + (at != null ? "@" + hex(at) : "");

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
  generation: 0, pending: [], view: null, tab: null,
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
  const m = hash.match(/^\/f\/([^@]+)(?:@(0x[0-9a-f]+))?$/i);
  if (!keep) $("#side").classList.remove("open");
  if (m) await showFunction(m[1], m[2] ? parseInt(m[2], 16) : null, keep);
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

async function showFunction(name, at, keep = false) {
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
  const same = S.current && S.current.header.name === name && S.view === "function";
  const scroll = main.scrollTop, sel = S.sel;
  S.view = "function";
  S.current = d;
  S.sel = -1;
  document.title = name + " · DREAD";
  if (!same) S.tab = null;
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

function headerHTML(d) {
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
  return `
    <div class="fn-head">
      <div class="fn-title"><h1>${esc(h.name)}</h1>
        <span class="badge">${dot(d.origin)}${esc(ORIGINS[d.origin].long)}</span>${d.system ? "<span class=\"badge\">tested by the system run</span>" : ""}
        <span class="grow"></span>
        <button id="note-btn" title="Note on the selected line (;)">Note</button>
        <button id="rename-btn" title="Rename (n)">Rename</button></div>
      <div class="meta">${meta.map((m) => `<span>${m}</span>`).join("")}</div>
      <div class="facts">${facts.map(([k, val]) => `<span class="k">${k}</span><span class="v">${val}</span>`).join("")}</div>
      ${notes}
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
  location.hash = fnLink(r.name, r.kind === "name" ? null : r.address);
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
    if (row) location.hash = fnLink(row.dataset.name);
  });
  $("#menu").addEventListener("click", () => $("#side").classList.toggle("open"));
  $("#theme").addEventListener("click", cycleTheme);
  $("#help-button").addEventListener("click", () => { $("#help").hidden = false; });
  $("#help-close").addEventListener("click", () => { $("#help").hidden = true; });
  $("#help").addEventListener("click", (e) => { if (e.target.id === "help") $("#help").hidden = true; });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (S.view === "overview") drawMap(); });
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
    if (e.target.id !== "map") { tip.hidden = true; return; }
    const fn = mapHit(e);
    if (!fn) { tip.hidden = true; return; }
    tip.innerHTML = `${esc(fn.name)} <span class="sub">${hex(fn.entry)} · ${fn.size} bytes · ${esc(ORIGINS[fn.origin].long)}</span>`;
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
      "[": () => stepFunction(-1), "]": () => stepFunction(1),
      Escape: () => { selectRow(-1); },
    };
    const action = keys[e.key];
    if (!action) return;
    if (S.view !== "function" && ["j", "k", "ArrowDown", "ArrowUp", "Enter", "n", ";", "Escape"].includes(e.key)) return;
    e.preventDefault();
    action();
  });
}

function stepFunction(step) {
  if (!S.order.length) return;
  const at = S.current ? S.order.findIndex((fn) => fn.name === S.current.header.name) : -1;
  const next = at < 0 ? 0 : Math.max(0, Math.min(S.order.length - 1, at + step));
  location.hash = fnLink(S.order[next].name);
}

async function start() {
  S.sort = prefs.get("sort", "address");
  S.showBytes = prefs.get("bytes", false);
  S.showC = prefs.get("c", true);
  S.by = prefs.get("by", "bytes");
  $("#sort").value = S.sort;
  applyTheme();
  renderChips();
  wire();
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
