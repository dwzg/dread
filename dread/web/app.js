// dread gui: one page, no framework, no build step. The server (dread/gui.py) answers /api/*;
// everything here is the view. Changes go through the server, which makes them as the command line does.

const TOKEN = document.querySelector('meta[name="dread-token"]').content;
const IMAGE = new URLSearchParams(location.search).get("image") || "";
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const hex = (n) => "0x" + n.toString(16);
const hex8 = (n) => n.toString(16).padStart(8, "0");
const fnLink = (name, at) => "#/f/" + encodeURIComponent(name) + (at != null ? "@" + hex(at) : "");
const relLink = (name) => "#/r/" + encodeURIComponent(name);
const modeLink = (name) => (S.mode === "relations" ? relLink(name) : fnLink(name));    // the view it is in

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
  if (!keep) $("#side").classList.remove("open");
  if (m) await showFunction(m[2], m[3] ? parseInt(m[3], 16) : null, keep, m[1] === "r" ? "relations" : "code");
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
  const modes = `<div class="seg modes" id="modes">${[["code", "Code"], ["relations", "Relations"]].map(([m, label]) =>
    `<button data-mode="${m}"${m === S.mode ? ' class="on"' : ""} title="${m === "code" ? "The listing and the C" : "The functions, memory, texts and structures around it"} (r)">${label}</button>`).join("")}</div>`;
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
// each name is; this lays it out (a small force simulation: callers pulled up, callees down, the
// function fixed in the middle, seeded from the last layout so that a step through the graph keeps
// its shape) and draws it as SVG. No randomness: the same graph reached the same way gives the same
// picture.

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
const SIM = { layer: 120, band: 0.8, spread: 90, repel: 9000, spring: 0.08, pull: 0.12, gravity: 0.004, step: 30, reach: 420 };
const REST = { calls: 95, "refers-to-function": 85, access: 52, "refers-to-text": 44, "uses-structure": 60, "points-to": 60 };

const R = {
  center: null, depth: 1, graph: null, pos: new Map(), view: null, sticky: null, timer: null,
  groups: new Set(GROUPS.map(([k]) => k)), shown: null, drag: null, touched: false,
};
const KEEP_POSITIONS = 4000;    // positions remembered across the walk at most

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

function stopRelations() {
  clearTimeout(R.timer);
  R.timer = null;
}

async function showRelations(name, mine) {
  stopRelations();
  if (R.center !== name) { R.sticky = null; }
  let g;
  try {
    g = await api("/api/graph", { name, depth: R.depth });
  } catch (e) {
    if (mine === renders) relNote(e.message);
    return;
  }
  if (mine !== renders || S.mode !== "relations") return;
  const moved = R.center !== name;
  if (moved) R.touched = false;
  R.center = name;
  R.graph = g;
  sizeStage();
  layoutGraph(moved);
  drawGraph();
  if (moved || !R.view || !R.touched) fitGraph(); else applyView();      // (it grows as the C is read)
  relNote();
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

function layoutGraph(moved) {
  const shown = R.shown = visibleGraph();
  const { nodes, links } = shown;
  const n = nodes.length, center = "f:" + R.graph.center;
  const index = new Map(nodes.map((d, i) => [d.id, i]));
  const x = new Float64Array(n), y = new Float64Array(n), ty = new Float64Array(n);
  const layered = new Uint8Array(n), known = new Uint8Array(n);
  const neighbours = nodes.map(() => []);
  const L = links.map((l) => {
    const s = index.get(l.source), t = index.get(l.target);
    neighbours[s].push(t);
    neighbours[t].push(s);
    return { s, t, len: REST[l.kind] || 60 };
  });
  for (const l of L) {                  // (a hub's links pull softly: its forty callers do not fall into it)
    l.k = SIM.spring / Math.sqrt(Math.max(1, Math.min(neighbours[l.s].length, neighbours[l.t].length)));
  }
  const old = R.pos, base = old.get(center) || { x: 0, y: 0 };
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
    if (p) { x[i] = p.x - base.x; y[i] = p.y - base.y; known[i] = 1; }
  };
  nodes.forEach((d, i) => { if (d.kind === "function") place(i); });
  let k = 0;                            // the rest around their first neighbour already placed (golden angle)
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
  if (c != null) { x[c] = 0; y[c] = 0; }
  const share = n ? known.reduce((t, v) => t + v, 0) / n : 0;
  // fresh: the whole cooling; a step to another function: most of it; more of the same graph: a settling
  const [iterations, start] = share < 0.25 ? [320, 1] : moved ? [240, 0.6] : share > 0.6 ? [90, 0.3] : [200, 0.6];
  let temp = start;
  const cool = Math.pow(0.02 / temp, 1 / iterations);
  const fx = new Float64Array(n), fy = new Float64Array(n);
  const reach2 = SIM.reach * SIM.reach;
  for (let it = 0; it < iterations; it++) {
    fx.fill(0);
    fy.fill(0);
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let dx = x[i] - x[j], dy = y[i] - y[j], d2 = dx * dx + dy * dy;
        if (d2 > reach2) continue;
        if (d2 < 0.01) { dx = 0.1 * (1 + (i % 7)); dy = 0.1 * (1 + (j % 5)); d2 = dx * dx + dy * dy; }
        const f = SIM.repel / (d2 * Math.sqrt(d2));          // (inverse square: near nodes part, far ones barely)
        fx[i] += dx * f; fy[i] += dy * f; fx[j] -= dx * f; fy[j] -= dy * f;
      }
    }
    for (const l of L) {
      const dx = x[l.t] - x[l.s], dy = y[l.t] - y[l.s], d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const f = (d - l.len) * l.k / d;
      fx[l.s] += dx * f; fy[l.s] += dy * f; fx[l.t] -= dx * f; fy[l.t] -= dy * f;
    }
    const max = SIM.step * temp;
    for (let i = 0; i < n; i++) {
      if (i === c) continue;
      let mx = fx[i] - x[i] * SIM.gravity, my = fy[i] - y[i] * SIM.gravity;
      if (layered[i]) {                 // callers kept above their line, callees below theirs, free beyond it
        const line = ty[i] * SIM.band;
        if (ty[i] < 0 ? y[i] > line : ty[i] > 0 ? y[i] < line : true) my += (line - y[i]) * SIM.pull;
      }
      const m = Math.sqrt(mx * mx + my * my);
      if (m > max) { mx *= max / m; my *= max / m; }
      x[i] += mx;
      y[i] += my;
    }
    temp *= cool;
  }
  R.pos = new Map();                    // (every position in the frame of the function now in the middle)
  if (old.size < KEEP_POSITIONS) for (const [id, p] of old) R.pos.set(id, { x: p.x - base.x, y: p.y - base.y });
  nodes.forEach((d, i) => R.pos.set(d.id, { x: x[i], y: y[i] }));
}

function nodeLabel(d) {
  const name = d.kind === "text" ? (d.name.length > 30 ? d.name.slice(0, 28) + "…\"" : d.name) : d.name;
  const more = d.more ? d.more.callers + d.more.callees : 0;
  return esc(name) + (more ? `<tspan class="more" dx="4">+${more}</tspan>` : "");
}

function drawGraph() {
  const svg = $("#rel-svg");
  if (!svg || !R.shown) return;
  const { nodes, links } = R.shown, center = "f:" + R.graph.center;
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
    return `<path class="edge ${cls}" data-i="${i}"/>`;
  }).join("");
  const many = nodes.length > LABELS_ALL;
  const ranked = nodes.filter((d) => d.id !== center && ((d.kind === "function" && (d.distance || 0) <= 1) || (shared.get(d.id) || 0) > 2))
    .sort((a, b) => (shared.get(b.id) || 0) - (shared.get(a.id) || 0) || (a.distance || 0) - (b.distance || 0) || (a.address || 0) - (b.address || 0));
  const labelled = new Set([center, ...ranked.slice(0, LABELS_SOME).map((d) => d.id)]);
  const body = nodes.map((d) => {
    const p = R.pos.get(d.id);
    const major = !many || labelled.has(d.id);
    const naming = d.kind === "text" ? "n-none" : "n-" + (d.naming || "unnamed");
    const r = radius(d);
    return `<g class="node k-${d.kind} ${naming}${d.id === center ? " center" : ""}${major ? " major" : ""}" data-id="${esc(d.id)}" ` +
      `transform="translate(${p.x.toFixed(1)},${p.y.toFixed(1)})">${shapePath(d.kind, d.id === center)}` +
      `<text x="${r + 3}" y="4">${nodeLabel(d)}</text></g>`;
  }).join("");
  svg.innerHTML = `${defs}<g id="rel-view"><g class="edges">${edges}</g><g class="nodes">${body}</g></g>`;
  svg.classList.toggle("labels", !many);
  svg.classList.toggle("dense", links.length > 120);
  placeEdges();
  if (R.sticky && R.shown.byId.has(R.sticky)) focusNode(R.sticky);
}

function placeEdges(only) {
  const svg = $("#rel-svg");
  if (!svg || !R.shown) return;
  const { links, byId } = R.shown;
  const paths = svg.querySelectorAll(".edges .edge");
  links.forEach((l, i) => {
    if (only && l.source !== only && l.target !== only) return;
    const a = R.pos.get(l.source), b = R.pos.get(l.target), path = paths[i];
    if (!a || !b || !path) return;
    const dx = b.x - a.x, dy = b.y - a.y, d = Math.sqrt(dx * dx + dy * dy) || 1;
    const ra = radius(byId.get(l.source)) + 1.5, rb = radius(byId.get(l.target)) + 1.5;
    const x1 = a.x + dx / d * ra, y1 = a.y + dy / d * ra, x2 = b.x - dx / d * rb, y2 = b.y - dy / d * rb;
    path.setAttribute("d", `M${x1.toFixed(1)},${y1.toFixed(1)}L${x2.toFixed(1)},${y2.toFixed(1)}`);
    let start = null, end = null;      // data flows: a read towards the function, a write towards the memory
    if (l.kind === "calls" || l.kind === "refers-to-function" || l.kind === "points-to" || l.kind === "holds") end = "ah-call";
    else if (l.kind === "access") { if (l.read) start = "ah-read"; if (l.write) end = "ah-write"; }
    if (start) path.setAttribute("marker-start", `url(#${start})`); else path.removeAttribute("marker-start");
    if (end) path.setAttribute("marker-end", `url(#${end})`); else path.removeAttribute("marker-end");
  });
}

function sizeStage() {
  const stage = $("#rel-stage");
  if (!stage) return;
  const top = stage.getBoundingClientRect().top;
  stage.style.height = Math.max(360, Math.floor(window.innerHeight - top - 10)) + "px";
}

// The function in the middle, everything else in view: the zoom that fits the farthest node on each side.
function fitGraph() {
  const stage = $("#rel-stage");
  if (!stage || !R.shown) return;
  const w = stage.clientWidth, h = stage.clientHeight;
  let ex = 40, ey = 40;
  for (const d of R.shown.nodes) {
    const p = R.pos.get(d.id);
    const label = Math.min(d.name.length, 30) * 6.6 + 16;
    ex = Math.max(ex, Math.abs(p.x) + (p.x >= 0 ? label : 14));
    ey = Math.max(ey, Math.abs(p.y) + 14);
  }
  const k = Math.max(0.15, Math.min(1.4, (w / 2 - 12) / ex, (h / 2 - 12) / ey));
  R.view = { k, tx: w / 2, ty: h / 2 };
  applyView();
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
    const path = svg.querySelector(`.edges .edge[data-i="${i}"]`);
    if (path) path.classList.toggle("hl", on);
  });
  for (const g of svg.querySelectorAll(".node")) g.classList.toggle("hl", near.has(g.dataset.id));
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
    ? "double-click or Shift+click: its code" : "click: its relations · Shift+click: its code";
  return `<div class="tip-h">${esc(d.name)} <span class="sub">${esc(KIND_LONG[d.kind] || d.kind)}</span></div>` +
    `<div class="tip-g">${rows.join("")}</div><div class="tip-hint">${hint}</div>`;
}

function showTip(event, d) {
  const tip = $("#tip");
  tip.innerHTML = nodeTip(d);
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

function graphPoint(event) {
  const box = $("#rel-svg").getBoundingClientRect(), v = R.view;
  return { x: (event.clientX - box.left - v.tx) / v.k, y: (event.clientY - box.top - v.ty) / v.k };
}

function wireRelations() {
  const main = $("#main");
  main.addEventListener("pointerdown", (e) => {
    const svg = e.target.closest("#rel-svg");
    if (!svg || e.button !== 0 || !R.view) return;
    const node = e.target.closest(".node");
    R.drag = { id: node ? node.dataset.id : null, x: e.clientX, y: e.clientY, moved: false, tx: R.view.tx, ty: R.view.ty };
    svg.setPointerCapture(e.pointerId);
  });
  main.addEventListener("pointermove", (e) => {
    const svg = e.target.closest("#rel-svg");
    if (R.drag && R.view) {
      const dx = e.clientX - R.drag.x, dy = e.clientY - R.drag.y;
      if (!R.drag.moved && dx * dx + dy * dy < 16) return;
      R.drag.moved = true;
      hideTip();
      R.touched = true;
      if (R.drag.id) {
        const p = graphPoint(e);
        R.pos.set(R.drag.id, p);
        const g = $(`#rel-svg .node[data-id="${CSS.escape(R.drag.id)}"]`);
        if (g) g.setAttribute("transform", `translate(${p.x.toFixed(1)},${p.y.toFixed(1)})`);
        placeEdges(R.drag.id);
      } else {
        R.view.tx = R.drag.tx + dx;
        R.view.ty = R.drag.ty + dy;
        $("#rel-svg").classList.add("panning");
        applyView();
      }
      return;
    }
    if (!svg || !R.shown) return;
    const node = e.target.closest(".node");
    if (!node) { if (!$("#tip").hidden) { hideTip(); unfocus(); } return; }
    const d = R.shown.byId.get(node.dataset.id);
    if (!d) return;
    focusNode(d.id);
    showTip(e, d);
  });
  main.addEventListener("pointerup", (e) => {
    const drag = R.drag;
    R.drag = null;
    const svg = $("#rel-svg");
    if (svg) svg.classList.remove("panning");
    if (!drag || drag.moved || !R.shown) return;
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
  });
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
    const k = Math.max(0.1, Math.min(4, R.view.k * Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0015))));
    const px = e.clientX - box.left, py = e.clientY - box.top;
    R.view.tx = px - (px - R.view.tx) * (k / R.view.k);
    R.view.ty = py - (py - R.view.ty) * (k / R.view.k);
    R.view.k = k;
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
      if (R.graph) { layoutGraph(false); drawGraph(); applyView(); relNote(); }
      return;
    }
    if (e.target.closest("#rel-fit")) { R.touched = false; fitGraph(); return; }
    const mode = e.target.closest("#modes button");
    if (mode && S.current) location.hash = mode.dataset.mode === "relations" ? relLink(S.current.header.name) : fnLink(S.current.header.name);
  });
  window.addEventListener("resize", () => { if (S.mode === "relations" && $("#rel-stage")) { sizeStage(); applyView(); } });
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
    if (e.target.closest("#rel-svg")) return;              // (the graph has tips of its own)
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
      f: () => { if (S.mode === "relations") { R.touched = false; fitGraph(); } },
      "[": () => stepFunction(-1), "]": () => stepFunction(1),
      Escape: () => { selectRow(-1); },
    };
    const action = keys[e.key];
    if (!action) return;
    if (S.view !== "function" && ["j", "k", "ArrowDown", "ArrowUp", "Enter", "n", ";", "Escape", "r", "f"].includes(e.key)) return;
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
