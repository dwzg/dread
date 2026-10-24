// dread gui: one page, no framework, no build step. The server (dread/gui.py) answers /api/*;
// everything here is the view. Changes go through the server, which makes them as the command line does.
// The force simulation both graphs are laid out with is sim.js. Pages of their own (the Run page and
// those after it) are modules of dread/guipages on the server and scripts beside this one, which import
// what they need from here and register() themselves (dread/guipages/__init__.py says how).

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
// How sure a name is (dread.relations certainty, sent by the server), from sure to none: the order of
// the legends and of the overview's bar.
const CERTAINTY = {
  named: { short: "named", long: "a name in the tables (a person's or the ELF's)" },
  sure: { short: "sure", long: "proposed by dread pilot; every answer of its vote agreed" },
  likely: { short: "likely", long: "proposed by dread pilot; most answers of its vote agreed" },
  unsure: { short: "unsure", long: "proposed by dread pilot; half of its vote's answers or fewer agreed (the review challenges it for that alone)" },
  unvoted: { short: "no vote", long: "proposed without a vote (one answer)" },
  none: { short: "no name", long: "no name yet" },
};
// The review's mark on a name (dread.namereview standing, sent by the server).
const MARKS = {
  flagged: { glyph: "!", short: "flagged", long: "a person's name the review's decision went against: left for a person to look at" },
  suspect: { glyph: "?", short: "suspect", long: "challenged by the review and kept without being confirmed" },
  changed: { glyph: "↻", short: "changed", long: "a name the review gave in place of an older one" },
  confirmed: { glyph: "✓", short: "confirmed", long: "challenged by the review and confirmed" },
};

const S = {
  overview: null, functions: [], byName: new Map(), order: [], current: null, sel: -1,
  filter: "", origins: new Set(), certainty: new Set(), sort: "address", showBytes: false, showC: true, by: "bytes",
  generation: 0, names: null, runs: null, pending: [], view: null, tab: null, mode: "code", mapBy: "origin",
  reading: null,
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

// The share of a name's disc that is filled: the vote's agreement for a proposal, all of it for a
// name in the tables or a proposal without a vote (its outline is dashed), none without a name.
function filled(certainty, confidence) {
  if (certainty === "none") return 0;
  if (certainty === "named" || certainty === "unvoted" || confidence == null) return 1;
  return Math.max(0, Math.min(1, confidence));
}

function certaintyWords(certainty, confidence) {
  const c = CERTAINTY[certainty] || CERTAINTY.none;
  return confidence != null && certainty !== "named" ? `${c.short}: its vote agreed ${Math.round(100 * confidence)} %` : c.short;
}

// A name's disc in the page's text (the list, the legends, the tips): an SVG, since the page's
// Content-Security-Policy keeps style attributes out; the same disc as the graphs draw.
function pie(certainty, confidence, title = true) {
  const level = certainty || "none", f = filled(level, confidence);
  const tip = title ? `<title>${esc(certaintyWords(level, confidence))}</title>` : "";
  return `<svg class="disc c-${level}" viewBox="-6 -6 12 12"${title ? "" : ' aria-hidden="true"'}>${tip}<circle class="disc-bg" r="5.2"/>${piePath(4.55, f)}</svg>`;
}

function markHTML(review, title = true) {
  if (!review || !review.mark || !MARKS[review.mark]) return "";
  const m = MARKS[review.mark];
  const tip = title ? ` title="${esc(m.short + ": " + m.long + (review.old ? ` (before: ${review.old})` : ""))}"` : "";
  return `<span class="mk mk-${review.mark}"${tip}>${m.glyph}</span>`;
}

// The name a function goes by on the page: its name in the tables, or the name dread pilot proposed
// (in italics: a proposal, not a name of the tables) where it has none.
function shownName(fn) {
  return fn.proposed ? fn.proposed : fn.name;
}

function nameHTML(fn) {
  return fn.proposed ? `<span class="prop">${esc(fn.proposed)}</span>` : esc(fn.name);
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
  // how sure the names are, and the review's marks where a review left any
  const marks = new Set(S.functions.map((fn) => fn.review && fn.review.mark).filter(Boolean));
  const sample = { likely: 0.6, unsure: 0.4 };
  $("#name-chips").innerHTML = Object.entries(CERTAINTY).map(([key, c]) =>
    `<span class="chip${S.certainty.has(key) ? " on" : ""}" data-certainty="${key}" title="${esc(c.long)}">${pie(key, sample[key], false)}${c.short}</span>`).join("") +
    Object.entries(MARKS).filter(([key]) => marks.has(key)).map(([key, m]) =>
      `<span class="chip${S.certainty.has(key) ? " on" : ""}" data-certainty="${key}" title="${esc(m.long)}">${markHTML({ mark: key }, false)}${m.short}</span>`).join("");
}

// Whether a function passes the names filter: one of the certainties or marks chosen (any of them).
function namesPass(fn) {
  return !S.certainty.size || S.certainty.has(fn.certainty) || (fn.review && S.certainty.has(fn.review.mark));
}

function applyFilter() {
  const f = S.filter.trim().toLowerCase();
  const list = S.functions.filter((fn) => (!S.origins.size || S.origins.has(fn.origin)) && namesPass(fn) &&
    (!f || fn.name.toLowerCase().includes(f) || (fn.proposed && fn.proposed.toLowerCase().includes(f)) ||
      hex8(fn.entry).includes(f.replace(/^0x/, ""))));
  if (S.sort === "name") list.sort((a, b) => shownName(a).localeCompare(shownName(b)));
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
    const flags = (fn.failed ? " failed" : "") + (fn.proposed ? " prop" : "");
    const review = fn.review ? ` · ${MARKS[fn.review.mark].short} by the review` + (fn.review.old ? ` (before: ${fn.review.old})` : "") : "";
    const title = `${fn.name}${fn.proposed ? ` (proposed: ${fn.proposed})` : ""} · ${hex(fn.entry)} · ${fn.size} bytes · ${ORIGINS[fn.origin].long}` +
      (fn.failed ? " · cases fail" : "") + (fn.missed ? " · instructions never ran" : "") +
      ` · name ${certaintyWords(fn.certainty, fn.confidence)}` + review;
    html += `<div class="fnrow${fn.name === current ? " current" : ""}" data-i="${i}" data-name="${esc(fn.name)}" title="${esc(title)}">` +
      `${dot(fn.origin)}${pie(fn.certainty, fn.confidence, false)}<span class="nm${flags}">${esc(shownName(fn))}</span>` +
      `${markHTML(fn.review, false) || "<span></span>"}<span class="size">${fn.size}</span></div>`;
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
  renderChips();
  applyFilter();
}

// ---- routing ----

// Every render takes a number; an answer that arrives after a newer render started is dropped, so
// the page always shows what the address bar says, whatever order the server's answers come in.
let renders = 0;

// ---- pages of their own (a module of dread/guipages on the server, a script beside this one) ----

// What /api/gui says: the pages' scripts, whether the server is read-only (no change, no job).
const GUI = { pages: [], readOnly: false, version: "", target: null, local: true, home: "", uploadLimit: 0 };
const PAGES = [];               // registered: {name, match, show, leave, status, keys, help, nav}
const pageKeys = {};
let shownPage = null;           // the registered page on screen, or null

// A page script registers its view: the addresses it shows (match: a RegExp on the part after #), how
// (show(match, keep): keep is a reload of the same address, which must keep what the reader has typed),
// what it stops when another view is shown (leave), its share of /api/status (status), its keys and
// their help, and a button in the bar (nav: {title, href, icon}).
function register(page) {
  PAGES.push(page);
  for (const [key, action] of Object.entries(page.keys || {})) if (!(key in pageKeys)) pageKeys[key] = action;
  const table = $("#help .keys");
  for (const [keys, text] of page.help || []) {
    const row = document.createElement("tr");
    row.innerHTML = `<td>${keys.split(" ").map((k) => `<kbd>${esc(k)}</kbd>`).join(" ")}</td><td>${esc(text)}</td>`;
    table.tBodies[0].insertBefore(row, table.tBodies[0].lastElementChild);     // (before Alt ← back)
  }
  if (page.nav) {
    const a = document.createElement("a");
    a.className = "icon-btn nav-btn";
    a.href = page.nav.href;
    a.title = page.nav.title;
    a.setAttribute("aria-label", page.nav.title);
    a.dataset.page = page.name;
    a.innerHTML = (page.nav.icon || esc(page.name[0])) + `<span class="nav-badge" hidden></span>`;
    $(".tools").insertBefore(a, $("#theme"));
  }
}

// The main area for a registered page's view, the other views' motion stopped, no function current.
function enter(view, title) {
  stopRelations();
  if (P.frame) cancelAnimationFrame(P.frame);
  P.frame = 0;
  S.view = view;
  S.current = null;
  renderList();
  document.title = title + " · DREAD";
  return $("#main");
}

// A page's own render numbers, for dropping answers a newer render overtook (as the views here do).
const nextRender = () => ++renders;
const isCurrent = (mine) => mine === renders;

function pageStatus(pages) {
  if (!pages) return;
  for (const p of PAGES) {
    if (p.status && pages[p.name] !== undefined) {
      try { p.status(pages[p.name]); } catch (e) { console.error(p.name, e); }
    }
  }
}

// The pages' scripts and stylesheets, imported before the first view (an older server has none).
async function loadPages(known) {
  let gui = known;
  try {
    gui = gui || await api("/api/gui");
  } catch {
    return;
  }
  Object.assign(GUI, { pages: gui.pages || [], readOnly: !!gui.read_only, version: gui.version || "",
    target: gui.target || null, local: gui.local !== false, home: gui.home || "", uploadLimit: gui.upload_limit || 0 });
  document.documentElement.classList.toggle("read-only", GUI.readOnly);
  for (const p of GUI.pages) {
    if (!p.style) continue;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "/" + p.style;
    document.head.appendChild(link);
  }
  for (const p of GUI.pages) {            // (one after another: the bar's buttons and the keys' help in their order)
    if (!p.script) continue;
    try {
      await import("/" + p.script);
    } catch (e) {
      console.error(p.script, e);
      toast(`The page ${p.name} did not load: ${e.message}`, true);
    }
  }
  pageStatus(gui.status);
}

export { $, esc, api, hex, size, ago, when, toast, showError, watch, prefs, S, GUI, register, enter, nextRender, isCurrent };

async function route(keep = false) {
  const hash = decodeURIComponent(location.hash.slice(1));
  const m = hash.match(/^\/([fr])\/([^@]+)(?:@(0x[0-9a-f]+))?$/i);
  const p = hash.match(/^\/p(?:\/(.+))?$/);
  const t = hash.match(/^\/s(?:\/(.+))?$/);
  if (!keep) $("#side").classList.remove("open");
  const page = PAGES.find((x) => x.match.test(hash)) || null;
  if (shownPage && shownPage !== page && shownPage.leave) {
    try { shownPage.leave(); } catch (e) { console.error(shownPage.name, e); }
  }
  shownPage = page;
  for (const b of document.querySelectorAll(".nav-btn")) b.classList.toggle("on", !!page && b.dataset.page === page.name);
  if (page) await page.show(hash.match(page.match), keep);
  else if (!GUI.target) showNoTarget();
  else if (m) await showFunction(m[2], m[3] ? parseInt(m[3], 16) : null, keep, m[1] === "r" ? "relations" : "code");
  else if (p) await showProgram(p[1] || null, keep);
  else if (t) await showTimeline(t[1] || null, keep);
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
        <div class="section-head"><h2>Names</h2><span class="grow"></span>
          <span class="legend-line">how sure each function's name is: filled as far as its vote agreed</span></div>
        <div class="stack" id="name-stack"></div>
        <div class="legend" id="name-legend"></div>
      </section>
      <section class="section">
        <div class="section-head"><h2>Whole program</h2><span class="grow"></span>
          <a class="button" href="#/p" title="The whole program (p)">Open the graph</a></div>
        <div class="legend-line">its functions in the groups dread groups finds from their code (shared memory, texts, devices,
          neighbours), joined by the calls between them; a group opens into its functions</div>
      </section>
      <section class="section">
        <div class="section-head"><h2>System run</h2><span class="grow"></span>
          <a class="button" href="#/s" title="The device logs of both images side by side (s)">Open the timeline</a></div>
        ${sysrunLines(o)}
      </section>
      <section class="section">
        <div class="section-head"><h2>Image map</h2><span class="grow"></span>
          <span class="legend-line">point at a function to name it, click to open it</span>
          <div class="seg" id="map-by"><button data-map="origin" title="Coloured by origin, as the Origins above">origin</button><button data-map="names" title="Coloured by how sure the names are, as the Names above">names</button></div></div>
        <canvas class="map" id="map"></canvas>
        <div class="map-scale"><span>${hex(img.base)}</span><span>${hex(img.end)}</span></div>
      </section>
      <div class="cards">${reviewCard(o)}${refusedCard(o)}${testsCard(o)}${droppedCard(o)}${runsCard(o)}</div>
    </div>`;
  main.scrollTop = again ? scroll : 0;
  renderOrigins();
  renderNames();
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

// How sure the names are: a bar of the functions by certainty, a legend whose items filter the list.
function renderNames() {
  const counts = S.overview.names.counts;
  const total = Object.values(counts).reduce((t, c) => t + c.functions, 0) || 1;
  const stack = $("#name-stack"), legend = $("#name-legend");
  stack.innerHTML = "";
  for (const key of Object.keys(CERTAINTY)) {
    const part = document.createElement("div");
    part.className = "c-" + key;
    part.style.width = (100 * counts[key].functions / total) + "%";
    part.title = `${CERTAINTY[key].long}: ${counts[key].functions} functions`;
    stack.appendChild(part);
  }
  const sample = { likely: 0.6, unsure: 0.4 };
  legend.innerHTML = Object.entries(CERTAINTY).map(([key, c]) => {
    const n = counts[key].functions, pct = (100 * n / total).toFixed(n && 100 * n / total < 1 ? 1 : 0);
    return `<div class="item" data-certainty="${key}" title="${esc(c.long)}; show only these in the list">${pie(key, sample[key], false)}<span class="label">${esc(c.short)}</span>` +
      `<span class="num">${n} · ${size(counts[key].bytes)} · ${pct}%</span></div>`;
  }).join("");
  for (const b of $("#map-by").children) b.classList.toggle("on", b.dataset.map === S.mapBy);
}

const REVIEW_SHOWN = 30;        // the names of each kind the review card lists at most

// What the reviews said (dread pilot --task review, review.tsv): the names they flagged, the suspects
// they left, the names they changed, with the reasons; the groups' names; how many they confirmed.
function reviewCard(o) {
  const r = o.names.review;
  if (!r) return `<div class="card"><h2>Name review</h2><div class="none">No review recorded yet (dread pilot --task review writes it).</div></div>`;
  const reasons = (last) => (last.reasons || []).map((x) => x.text).join("; ");
  const fnLine = (item) => {
    const last = item.last, made = item.changed || last;
    const what = item.review && item.review.mark === "changed"
      ? `was <span class="mono">${esc(made.old || "")}</span>: ${esc(reasons(made))}${made.agreement ? ` · candidate ${esc(made.agreement)}` : ""}${made.votes ? `, decided ${esc(made.votes)}` : ""}`
      : `${esc(reasons(last))}${last.agreement ? ` · candidate ${esc(last.candidate || "")} ${esc(last.agreement)}` : ""}${last.votes ? `, decided ${esc(last.votes)}` : ""}`;
    return `<div class="rowline">${markHTML(item.review)}${pie(item.certainty, item.confidence)}<a href="${fnLink(item.name)}" title="${esc(item.name)}">${nameHTML(item)}</a>` +
      `<span class="r" title="${esc(reasons(last))}">${what}</span></div>`;
  };
  const groupLine = (g) => `<div class="rowline">${markHTML(g)}<span class="mono">${esc(g.label || g.members)}${g.prefix ? ` (${esc(g.prefix)})` : ""}</span>` +
    `<span class="r" title="${esc(reasons(g.last))}">${esc(g.last.outcome || "")}: ${esc(reasons(g.last))}</span></div>`;
  const parts = [];
  const head = (text) => `<div class="sub-head">${text}</div>`;
  const some = (list, line) => list.slice(0, REVIEW_SHOWN).map(line).join("") +
    (list.length > REVIEW_SHOWN ? `<div class="legend-line">and ${list.length - REVIEW_SHOWN} more</div>` : "");
  if (r.flagged.length) parts.push(head(`flagged: names people gave that lost · ${r.flagged.length}`) + some(r.flagged, fnLine));
  if (r.suspect.length) parts.push(head(`suspects: challenged, kept without a confirmation · ${r.suspect.length}`) + some(r.suspect, fnLine));
  if (r.changed.length) parts.push(head(`changed by the review · ${r.changed.length}`) + some(r.changed, fnLine));
  if (r.groups.length) parts.push(head(`groups' names · ${r.groups.length}`) + some(r.groups, groupLine));
  const foot = [`${r.confirmed.length} confirmed`, `${r.challenges} challenges in ${r.reviews} review${r.reviews === 1 ? "" : "s"}`];
  if (r.stale) foot.push(`${r.stale} about names no longer in use`);
  return `<div class="card review-card"><h2>Name review · ${r.flagged.length + r.suspect.length} to look at</h2>${parts.join("")}` +
    `<div class="legend-line">${foot.join(" · ")}</div></div>`;
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

// What the system run's records hold, a line a scenario: whether the images agree, the entries, the calls
// through pointers it saw, which tool kept it and when.
function sysrunLines(o) {
  const runs = o.sysrun || [];
  if (!runs.length) return `<div class="legend-line">No run recorded yet: dread sysrun keeps each scenario's runs, dread lift --check its whole image's.</div>`;
  return runs.map((r) => {
    const state = r.runs.length < 2 ? "the original alone" : r.differ
      ? `<span class="bad">the images differ${r.differ.at != null ? " from entry " + commas(r.differ.at) : ""}</span>` : `<span class="good">the images agree</span>`;
    return `<div class="rowline"><a href="${tlLink(r.name)}">${esc(recordLabel(r))}</a><span class="r">${state} · ${commas(r.runs[0].entries)} log entries · ` +
      `${commas(r.pointers)} call${r.pointers === 1 ? "" : "s"} through pointers · ${esc(r.by || "")}, ${ago(r.when)}</span></div>`;
  }).join("");
}

function runsCard(o) {
  const rows = [["dread recon", o.runs.recon], ["dread test", o.runs.test], ["dread lift --check", o.runs.lift_check],
    ["dread sysrun", o.runs.sysrun]];
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
  const byNames = S.mapBy === "names";
  const colors = byNames ? Object.fromEntries(Object.keys(CERTAINTY).map((k) => [k, cssVar(k === "none" ? "--c-empty" : "--c-" + k)]))
    : Object.fromEntries(Object.keys(ORIGINS).map((k) => [k, cssVar("--o-" + k)]));
  const sorted = [...S.functions].sort((a, b) => a.entry - b.entry);
  for (const fn of sorted) {
    g.fillStyle = colors[byNames ? fn.certainty : fn.origin];
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
    `<a href="${fnLink(n, sites[0])}" title="${esc(n)}: ${sites.map(hex).join(", ")}">${nameHTML(S.byName.get(n) || { name: n })}</a>${sites.length > 1 ? `<span class="times">×${sites.length}</span>` : ""}`).join("")
    : "<span class=\"legend-line\">no direct calls</span>"]);
  if (h.calls.length) facts.push(["calls", h.calls.map((n) => `<a href="${fnLink(n)}" title="${esc(n)}">${nameHTML(S.byName.get(n) || { name: n })}</a>`).join("")]);
  const ran = ranFacts(d.ran);
  if (ran) facts.push(["system run", ran]);
  if (h.reads) facts.push(["registers", `reads ${h.reads.length ? esc(h.reads.join(", ")) : "nothing"} at its entry; its callers read ${h.kept.length ? esc(h.kept.join(", ")) : "nothing"} after it`]);
  facts.push(...nameFacts(d.name));
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
        <span class="badge">${dot(d.origin)}${esc(ORIGINS[d.origin].long)}</span>${nameBadge(d.name)}${d.system ? "<span class=\"badge\">tested by the system run</span>" : ""}
        <span class="grow"></span>
        ${modes}
        <button id="note-btn" title="Note on the selected line (;)">Note</button>
        <button id="rename-btn" title="Rename (n)">Rename</button></div>
      <div class="meta">${meta.map((m) => `<span>${m}</span>`).join("")}</div>
      ${compact ? "" : `<div class="facts">${facts.map(([k, val]) => `<span class="k">${k}</span><span class="v">${val}</span>`).join("")}</div>
      ${notes}`}
    </div>`;
}

// What the system run's records saw of a function beyond its calls: who called it through a pointer
// or started it by a return (a task from its scheduler), what it called so, how the core entered it.
const RAN_WORDS = { pointer: "through a pointer", "pointer-jump": "jumping through a pointer", return: "by a return", falls: "running into it" };
function ranFacts(r) {
  if (!r || !(r.callers.length || r.callees.length || r.entered_by.length)) return "";
  const link = (x) => `<a href="${fnLink(x.name, x.site != null && x.how !== "return" ? x.site : null)}" title="${esc(x.name)} (${esc(RAN_WORDS[x.how] || x.how)}${x.site != null ? " at " + hex(x.site) : ""}; ${esc((x.scenarios || []).join(", "))})">` +
    `${nameHTML(S.byName.get(x.name) || { name: x.name })}</a><span class="times">×${commas(x.times)}</span>`;
  const lines = [];
  for (const [how, words] of [["pointer", "called through a pointer by"], ["pointer-jump", "jumped to through a pointer by"], ["return", "started by a return from"], ["falls", "run into from"]]) {
    const got = r.callers.filter((x) => x.how === how);
    if (got.length) lines.push(`${words} ${got.map(link).join("")}`);
  }
  for (const [how, words] of [["pointer", "calls through a pointer"], ["pointer-jump", "jumps through a pointer to"], ["return", "starts by a return"], ["falls", "runs into"]]) {
    const got = r.callees.filter((x) => x.how === how);
    if (got.length) lines.push(`${words} ${got.map(link).join("")}`);
  }
  if (r.entered_by.length) {
    lines.push("entered by " + r.entered_by.map((x) => `${x.how === "exception" ? "exception " + x.exception : x.how === "reset" ? "the reset" : "a system call"} <span class="times">×${commas(x.times)}</span>`).join(" "));
  }
  return lines.join("<br>") + ` <span class="legend-line inline">(<a href="#/s" title="the records: ${esc(r.scenarios.join(", "))}">its timeline</a>)</span>`;
}

// The name's certainty (and the review's mark) beside the function's name: the proposal it goes by too.
function nameBadge(n) {
  if (!n) return "";
  const shown = n.naming === "proposed" && n.proposed ? ` <span class="prop">${esc(n.proposed)}</span>` : "";
  const review = n.review && n.review.mark ? { mark: n.review.mark } : null;
  return `<span class="badge" title="${esc(CERTAINTY[n.certainty].long)}">${pie(n.certainty, n.confidence, false)}${esc(certaintyWords(n.certainty, n.confidence))}${shown}${review ? " " + markHTML(review) : ""}</span>`;
}

// What names.tsv and review.tsv say of the function's name, for the facts under its title: the
// proposal with its vote, and every challenge of the review with its reasons and outcome.
function nameFacts(n) {
  if (!n) return [];
  const out = [];
  if (n.proposed) {
    const answers = n.samples ? ` <span class="legend-line inline" title="the answers of its vote, in the order asked">answers: ${esc(n.samples)}</span>` : "";
    const vote = n.proposal_confidence != null ? `its vote agreed ${Math.round(100 * n.proposal_confidence)} %` : "one answer, no vote";
    const who = n.naming === "named" ? "dread pilot proposed" : "proposed by dread pilot:";
    out.push(["name", `${who} <span class="mono prop">${esc(n.proposed)}</span>${n.what ? " — " + esc(n.what) : ""} <span class="legend-line inline">${vote}${n.provider ? ", " + esc(n.provider) : ""}</span>${answers}`]);
  }
  const r = n.review;
  if (r && r.rows) {
    for (const row of r.rows) {
      const reasons = (row.reasons || []).map((x) => `<li>${esc(x.text)} <span class="legend-line inline">${esc(x.kind)} ${x.weight.toFixed(2)}</span></li>`).join("");
      let outcome = esc(row.outcome || "");
      if (row.outcome === "changed" || row.outcome === "general") outcome = `changed from <span class="mono">${esc(row.old || "")}</span> to <span class="mono">${esc(row.name || "")}</span>${row.what ? " — " + esc(row.what) : ""}`;
      else if (row.outcome === "flagged") outcome = `flagged: the decision preferred <span class="mono">${esc(row.candidate || "")}</span>${row.candidate_what ? " — " + esc(row.candidate_what) : ""}; a person's name, never changed`;
      else if (row.outcome === "kept") outcome = `kept: <span class="mono">${esc(row.candidate || "")}</span> did not win by the margin`;
      else if (row.outcome === "confirmed") outcome = `confirmed: the candidate had its words`;
      else if (row.outcome === "no candidate") outcome = `no candidate came back: the reasons stand`;
      const votes = [row.agreement ? `candidate ${esc(row.agreement)}` : "", row.votes ? `decided ${esc(row.votes)}` : ""].filter(Boolean).join(", ");
      out.push([`review ${row.review ?? ""}`, `${row.mark && !row.stale ? markHTML({ mark: row.mark }) + " " : ""}` +
        `${row.stale ? `<span class="legend-line inline">about an earlier name, ${esc(row.name || "")}:</span> ` : ""}${outcome}${votes ? ` <span class="legend-line inline">${votes}</span>` : ""}` +
        `<ul class="reasons" title="round ${row.round ?? ""}, score ${row.score ?? ""}">${reasons}</ul>`]);
    }
  }
  return out;
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
  else if (s.kind === "readable") body = readingBody(d);
  else if (s.pending) body = `<div class="placeholder">The lifted C follows when the register analysis is done.</div>`;
  else if (s.refused) body = `<div class="placeholder"><span class="warn">The lifter refuses it:</span> ${esc(s.refused)}</div>`;
  else {
    const where = s.path ? `${esc(s.path)}${s.first > 1 ? ":" + s.first : ""}` : "";
    const how = s.how || (s.kind === "source" ? "written by hand" : s.kind === "pilot" ? "written by dread pilot, read by nobody" : "");
    body = `<div class="pane-sub">${[where, esc(how)].filter(Boolean).join(" · ")}</div><div class="code">${codeHTML(s.text, s.first)}</div>`;
  }
  pane.innerHTML = `<div class="pane-head"><span class="title">C</span>${tabs}</div>${body}`;
}

// The function's readable C in the reading form (dread readable --plain, /api/reading): without the
// instructions' comments, each statement's line with the address of its first instruction in the
// margin (a click selects that instruction in the listing). The server makes it on the first request
// (the page shows a placeholder meanwhile) and keeps it for the load and the readable C's tables.
function readingBody(d) {
  const name = d.header.name, r = S.reading;
  if (!r || r.name !== name || r.generation !== S.generation) {
    loadReading(name);
    return `<div class="placeholder">Making its readable C…</div>`;
  }
  const a = r.data;
  if (a.pending) return `<div class="placeholder">The readable C follows when the register analysis is done.</div>`;
  if (!a.lines) return `<div class="placeholder"><span class="warn">No readable C:</span> ${esc(a.why || "")}</div>`;
  const how = a.accepted ? "in the readable table: built from this C, its cases agree"
    : "not in the readable table: made now, not built or tested";
  return `<div class="pane-sub">dread readable --plain · ${how} · margin: each statement's first instruction</div>` +
    `<div class="code reading">${codeHTML(a.lines.map((l) => l[1]).join("\n"), 1, a.lines.map((l) => l[0]))}</div>`;
}

let readingAsked = null;
async function loadReading(name) {
  if (readingAsked === name) return;
  readingAsked = name;
  const generation = S.generation;
  let data;
  try {
    data = await api("/api/reading", { name });
  } catch (e) {
    data = { name, lines: null, why: e.message };
  }
  if (readingAsked === name) readingAsked = null;
  S.reading = { name, generation, data };
  if (data.pending) {                     // (asked again when the register analysis may be done)
    setTimeout(() => { if (S.reading && S.reading.data === data) { S.reading = null; renderC(); } }, 1500);
  }
  if (S.current && S.current.header.name === name && S.tab === "readable") renderC();
}

const KEYWORDS = new Set(("if else for while do switch case default break continue return goto sizeof typedef struct union " +
  "enum static inline extern register volatile const asm __asm__ __attribute__ __volatile__").split(" "));
const TYPES = new Set(("void char short int long unsigned signed float double bool _Bool u8 u16 u32 u64 s8 s16 s32 s64 " +
  "i8 i16 i32 i64 uint8_t uint16_t uint32_t uint64_t int8_t int16_t int32_t int64_t size_t uintptr_t").split(" "));

// C as highlighted lines, each with its number (first: the first line's), or with margins (an
// address or null for each line: the reading form's) a link to that address in the listing.
function codeHTML(text, first = 1, margins = null) {
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
  return html.replace(/\n$/, "").split("\n").map((line, i) => {
    const at = margins ? margins[i] : null;
    const no = !margins ? first + i
      : at == null ? "" : `<a class="at" data-at="${at}" title="${hex(at)}: select it in the listing">${hex8(at)}</a>`;
    return `<div class="cl"><span class="no">${no}</span><span>${line || " "}</span></div>`;
  }).join("");
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
const LABELS_ALL = 60;          // up to this many nodes every label shows; beyond, LABELS_SOME and on hover
const LABELS_SOME = 28;         // the labels a larger graph shows: the function, then the best-joined near pieces
const LABEL_ZOOM = 1.35;        // and every label from this zoom on
const SIM = { layer: 120, band: 0.8, spread: 90, repel: 9000, spring: 0.16, pull: 0.12, gravity: 0.004, reach: 420,
  anchor: 0.25 };
const WOBBLE = { friction: 0.86, decay: 0.028, maxSpeed: 20 };   // the relations' motion (sim.js)
const REST = { calls: 95, "refers-to-function": 85, access: 52, "refers-to-text": 44, "uses-structure": 60, "points-to": 60,
  "calls-pointer": 95, enters: 95 };
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

// A function's disc in the relations: its outline, and inside it the share filled that its name's
// vote agreed (filled()), a wedge from twelve o'clock clockwise.
function piePath(r, f) {
  if (f <= 0) return "";
  if (f >= 0.999) return `<circle class="pie" r="${r}"/>`;
  const a = 2 * Math.PI * f, x = (r * Math.sin(a)).toFixed(2), y = (-r * Math.cos(a)).toFixed(2);
  return `<path class="pie" d="M0,0V${-r}A${r},${r} 0 ${f > 0.5 ? 1 : 0},1 ${x},${y}Z"/>`;
}

function functionShape(d, center) {
  const r = center ? 10 : 6.5;
  return `<circle class="shape" r="${r}"/>${piePath(center ? 8.5 : 5.85, filled(d.certainty, d.confidence))}`;
}

// The review's mark on a node: a glyph in a small disc at its upper right (not for a confirmed name).
function markBadge(review, r) {
  if (!review || !review.mark || review.mark === "confirmed" || !MARKS[review.mark]) return "";
  const at = (r * 0.78 + 1).toFixed(1);
  return `<g class="mk-g mk-${review.mark}" transform="translate(${at},-${at})"><circle r="4.8"/><text>${MARKS[review.mark].glyph}</text></g>`;
}

// What a node is called on the page: a function without a name in the tables by its proposal.
function labelOf(d) {
  return d.kind === "function" && d.naming === "proposed" && d.proposed ? d.proposed.name : d.name;
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

// The scale of how sure names are, and the review's marks: the legends of both graphs.
function certaintyLegend() {
  const sample = { likely: 0.6, unsure: 0.4 };
  return `<div class="lg-row">${Object.entries(CERTAINTY).map(([k, c]) =>
    `<span class="lg" title="${esc(c.long)}">${pie(k, sample[k], false)}${esc(c.short)}</span>`).join("")}</div>` +
    `<div class="lg-row">${["flagged", "suspect", "changed"].map((k) =>
      `<span class="lg" title="${esc(MARKS[k].long)}">${markHTML({ mark: k }, false)}${esc(MARKS[k].short)}</span>`).join("")}<span class="lg">filled as far as the vote agreed</span></div>`;
}

function legendHTML() {
  const sh = (kind, text) => `<span class="lg"><svg viewBox="-9 -9 18 18" class="glyph k-${kind} c-none">${shapePath(kind)}</svg>${text}</span>`;
  const ln = (cls, text) => `<span class="lg"><svg viewBox="0 0 22 8" class="line"><path class="edge ${cls}" d="M1,4H21"/></svg>${text}</span>`;
  return certaintyLegend() +
    `<div class="lg-row">${sh("global", "RAM")}${sh("register", "register")}${sh("rom", "rom")}${sh("table", "table")}${sh("text", "text")}${sh("structure", "structure")}</div>` +
    `<div class="lg-row">${ln("e-calls", "calls")}${ln("e-refers-to-function", "address taken")}${ln("e-access", "reads")}${ln("e-access w", "writes")}</div>` +
    `<div class="lg-row">${ln("e-uses", "uses")}${ln("e-refers-to-text", "refers to")}${ln("e-uses-structure", "structure")}</div>` +
    `<div class="lg-row">${ln("e-calls-pointer", "through a pointer")}${ln("e-enters", "started by a return")}<span class="lg">(the system run)</span></div>`;
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
  const name = d.kind === "text" ? (d.name.length > 30 ? d.name.slice(0, 28) + "…\"" : d.name) : labelOf(d);
  const more = d.more ? d.more.callers + d.more.callees : 0;
  const shown = d.kind === "function" && d.naming === "proposed" && d.proposed ? `<tspan class="prop">${esc(name)}</tspan>` : esc(name);
  return shown + (more ? `<tspan class="more" dx="4">+${more}</tspan>` : "");
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
    if (l.kind === "calls" || l.kind === "refers-to-function" || l.kind === "points-to" || l.kind === "holds" ||
        l.kind === "calls-pointer" || l.kind === "enters") end = "ah-call";
    else if (l.kind === "access") { if (l.read) start = "ah-read"; if (l.write) end = "ah-write"; }
    return `<path class="edge ${cls}" data-i="${i}"${start ? ` marker-start="url(#${start})"` : ""}${end ? ` marker-end="url(#${end})"` : ""}/>`;
  }).join("");
  const many = nodes.length > LABELS_ALL;
  const ranked = nodes.filter((d) => d.id !== center && ((d.kind === "function" && (d.distance || 0) <= 1) || (shared.get(d.id) || 0) > 2))
    .sort((a, b) => (shared.get(b.id) || 0) - (shared.get(a.id) || 0) || (a.distance || 0) - (b.distance || 0) || (a.address || 0) - (b.address || 0));
  const labelled = new Set([center, ...ranked.slice(0, LABELS_SOME).map((d) => d.id)]);
  const body = nodes.map((d) => {
    const major = !many || labelled.has(d.id);
    const sure = d.kind === "text" ? "" : " c-" + (d.certainty || "none");
    const r = radius(d), mid = d.id === center;
    const shape = d.kind === "function" ? functionShape(d, mid) : shapePath(d.kind, mid);
    return `<g class="node k-${d.kind}${sure}${mid ? " center" : ""}${major ? " major" : ""}" data-id="${esc(d.id)}">` +
      `${shape}${markBadge(d.review, mid ? 10 : 6.5)}<text x="${r + 3}" y="4">${nodeLabel(d)}</text></g>`;
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
    const label = Math.min(labelOf(d).length, 30) * 6.6 + 16;
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
    const ranLinks = links.filter((l) => (l.kind === "calls-pointer" || l.kind === "enters") && (l.source === d.id || l.target === d.id));
    for (const l of ranLinks) {
      const e = l.edges[0], other = byId.get(l.source === d.id ? l.target : l.source);
      const how = e.how.map((h) => RAN_WORDS[h] || h).join(", ");
      const words = l.source === d.id ? (l.kind === "enters" ? `starts ${esc(other.name)} (${how})` : `calls ${esc(other.name)} ${how}`)
        : (l.kind === "enters" ? `started by ${esc(other.name)} (${how})` : `called by ${esc(other.name)} ${how}`);
      line("system run", `${words}: ${commas(e.times)} times in ${esc(e.scenarios.join(", "))}`);
    }
    for (const e of d.entered_by || []) {
      line("system run", `entered by ${e.how === "exception" ? "exception " + e.exception : e.how === "reset" ? "the reset" : "a system call"}: ${commas(e.times)} times`);
    }
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
    let v = `${pie(d.certainty, d.confidence, false)} ${esc(certaintyWords(d.certainty, d.confidence))}`;
    if (d.proposed) v += `: <span class="mono prop">${esc(d.proposed.name)}</span>${d.proposed.what ? " — " + esc(d.proposed.what) : ""}`;
    line("name", v);
  }
  reviewTip(d.review, line);
  const hint = d.kind !== "function" ? "click: keep it lit" : d.id === "f:" + R.graph.center
    ? "double-click or Shift+click: its code · drag: move it" : "click: its relations · Shift+click: its code · drag: move it";
  return `<div class="tip-h">${esc(d.name)} <span class="sub">${esc(KIND_LONG[d.kind] || d.kind)}</span></div>` +
    `<div class="tip-g">${rows.join("")}</div><div class="tip-hint">${hint}</div>`;
}

// The review's word on a name in a tooltip: its mark, the outcome, the old name where it changed it,
// and the reasons of its last challenge.
function reviewTip(review, line) {
  if (!review || !review.mark || !review.last) return;
  const last = review.last, made = review.changed || last;
  let v = `${markHTML(review, false)} ${esc(MARKS[review.mark].short)}`;
  if (review.mark === "changed") v += `: was <span class="mono">${esc(made.old || "")}</span>`;
  else if (review.mark === "flagged") v += `: the decision preferred <span class="mono">${esc(last.candidate || "")}</span>`;
  else if (review.mark === "suspect") v += `: ${esc(last.outcome || "")}${last.candidate ? `, against <span class="mono">${esc(last.candidate)}</span>` : ""}`;
  line(`review ${last.review ?? ""}`, v);
  const reasons = (review.mark === "changed" ? made : last).reasons || [];
  if (reasons.length) line("why", reasons.map((x) => esc(x.text)).join("<br>"));
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
  sharedPairs: 4000,    // links from memory and texts two groups share down to an open group's functions, at most
};
const P = {
  data: null, stamp: null, open: new Set(), sim: null, nodes: [], links: [], index: new Map(), adj: [], pos: new Map(),
  slots: [], home: [], members: [], groupOf: new Int32Array(0), fnIndex: new Map(), calls: null, view: null,
  touched: false, hover: -1, sticky: null, lit: null, drag: null, frame: 0, last: 0, acc: 0, timer: null,
  colors: null, labels: [], hullOf: new Map(), settling: false, sharedLeft: 0,
  query: "", found: null, foundGroups: new Map(), foundNodes: null, searchTimer: null,
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
      <div class="rel-bar">
        <input id="prog-search" class="light-search" type="search" placeholder="Light: name, address or text" autocomplete="off"
          spellcheck="false" aria-label="Light the functions a search finds" value="${esc(P.query)}">
        <span class="rel-note" id="prog-found"></span><span class="grow"></span><span class="rel-note" id="prog-note"></span></div>
      <div class="rel-stage" id="prog-stage">
        <canvas class="graph" id="prog-canvas" role="img" aria-label="The whole program: its groups of functions and the calls between them"></canvas>
        <div class="rel-legend" id="prog-legend">${progLegendHTML()}</div>
      </div>
    </section>`;
}

function progLegendHTML() {
  const ln = (cls, text) => `<span class="lg"><svg viewBox="0 0 22 8" class="line"><path class="edge ${cls}" d="M1,4H21"/></svg>${text}</span>`;
  return `<div class="lg-row"><span class="lg"><svg viewBox="-9 -9 18 18" class="glyph"><circle class="grp" r="7.5"/><path class="grp-ring" d="M0,-6A6,6 0 1,1 -5.2,3"/></svg>a group: its size the functions, its ring how sure their names are; <span class="mk mk-suspect">?</span>3: suspects it holds</span></div>` +
    certaintyLegend() +
    `<div class="lg-row">${ln("e-calls", "calls")}${ln("e-refers-to-function", "address taken")}${ln("e-uses-structure", "memory or texts both use")}${ln("e-calls-pointer", "through a pointer, started (system run)")}</div>` +
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
    (P.sharedLeft ? ` · <span class="warn" title="PROGRAM.sharedPairs: links from shared memory and texts down to the functions">${P.sharedLeft} shared links left out</span>` : "") +
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
    P.marked = d.functions.some((f) => f.review && f.review.mark && f.review.mark !== "confirmed");
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
  markFound();
  if (P.query) progSearch(P.query);     // (the names it finds by may have changed)
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
  P.marked = d.functions.some((f) => f.review && f.review.mark && f.review.mark !== "confirmed");
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
  return l.calls[0] + l.calls[1] + l.refers[0] + l.refers[1] + l.ran[0] + l.ran[1] + l.shared;
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
    if (!l) { l = { s, t, calls: [0, 0], refers: [0, 0], ran: [0, 0], shared: 0, kinds: null }; pairs.set(key, l); links.push(l); }
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
  for (const [i, j] of d.ran || []) {      // the system run's: through a pointer, a task's start (dashed)
    const a = at(i), b = at(j);
    if (a !== undefined && b !== undefined && a !== b) link(a, b).ran[a < b ? 0 : 1] += 1;
  }
  for (const e of d.between) {         // memory and texts both touch: between closed groups the server's count
    if (P.open.has(e.a) || P.open.has(e.b)) continue;
    const total = Object.values(e.shared).reduce((t, v) => t + v, 0);
    if (!total) continue;
    const l = link(index.get("g:" + e.a), index.get("g:" + e.b));
    l.shared = total;
    l.kinds = Object.assign({}, e.shared);
  }
  // ... and down to the functions of an open group: each thing functions of two groups touch (the
  // server's "shared"), between the nodes its functions are now in, one more for each pair of them in
  // two groups; things touched by fewer functions first, PROGRAM.sharedPairs pairs at most
  let budget = PROGRAM.sharedPairs;
  P.sharedLeft = 0;
  const things = (d.shared || []).map((t, k) => [t, k]).sort((a, b) => a[0][2].length - b[0][2].length || a[1] - b[1]);
  for (const [[kind, , users]] of things) {
    const ends = [...new Set(users.map(at).filter((a) => a !== undefined))];
    if (!ends.some((a) => nodes[a].kind === "function")) continue;      // (closed groups only: counted above)
    for (let x = 0; x < ends.length; x++) {
      for (let y = x + 1; y < ends.length; y++) {
        const a = ends[x], b = ends[y];
        if (nodes[a].g === nodes[b].g || (nodes[a].kind === "group" && nodes[b].kind === "group")) continue;
        if (budget <= 0) { P.sharedLeft += 1; continue; }
        budget -= 1;
        const l = link(a, b);
        l.shared += 1;
        l.kinds = l.kinds || {};
        l.kinds[kind] = (l.kinds[kind] || 0) + 1;
      }
    }
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
  const out = { call: v("--g-call"), shared: v("--g-struct"), refer: v("--g-weak"), text: v("--text"), muted: v("--muted"),
    faint: v("--faint"), panel: v("--panel"), group: v("--accent-bg"), groupLine: v("--accent-soft"), hull: v("--panel-2"),
    hullLine: v("--line-strong"), strong: v("--accent-strong"), mono: v("--mono"), sans: v("--sans"), empty: v("--c-empty"),
    mk: { suspect: v("--mk-suspect"), flagged: v("--mk-flagged"), changed: v("--mk-changed") },
    mkInk: { suspect: v("--mk-ink"), flagged: v("--mk-ink-on"), changed: v("--mk-ink-on") }, fill: {}, line: {} };
  for (const k of Object.keys(CERTAINTY)) {
    out.fill[k] = k === "none" ? out.empty : v("--c-" + k);
    out.line[k] = v("--c-" + k + "-line");
  }
  return out;
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
  const called = (L) => L.calls[0] + L.calls[1] + L.refers[0] + L.refers[1] > 0;
  const ranOnly = (L) => !called(L) && L.ran[0] + L.ran[1] > 0;
  const faint = nodes.length > 1500 ? 0.35 : 0.55;
  for (const on of passes) {
    const alpha = lit ? (on ? 1 : 0.12) : faint;
    for (const inside of [false, true]) {
      g.beginPath();
      for (const L of links) {
        if (ofGroup(L) || !called(L) || (nodes[L.s].g === nodes[L.t].g) !== inside || (lit && near(L) !== on)) continue;
        g.moveTo(x[L.s], y[L.s]);
        g.lineTo(x[L.t], y[L.t]);
      }
      g.globalAlpha = inside ? alpha : alpha * 0.45;
      g.lineWidth = 0.9 * px;
      g.strokeStyle = C.call;
      g.stroke();
    }
    g.beginPath();                      // the system run's transfers no call shows: through a pointer, a task's start
    for (const L of links) {
      if (ofGroup(L) || !ranOnly(L) || (lit && near(L) !== on)) continue;
      g.moveTo(x[L.s], y[L.s]);
      g.lineTo(x[L.t], y[L.t]);
    }
    g.globalAlpha = alpha;
    g.lineWidth = 1.1 * px;
    g.strokeStyle = C.call;
    g.setLineDash([2.5 * px, 2.5 * px]);
    g.stroke();
    g.setLineDash([]);
    g.beginPath();                      // ... and their arrows
    for (const L of links) {
      if (ofGroup(L) || !ranOnly(L) || (lit && near(L) !== on)) continue;
      if (L.ran[0]) arrow(g, L.s, L.t, px);
      if (L.ran[1]) arrow(g, L.t, L.s, px);
    }
    g.fillStyle = C.call;
    g.fill();
    g.beginPath();                      // functions of two open groups that touch the same memory or text
    for (const L of links) {
      if (ofGroup(L) || called(L) || ranOnly(L) || (lit && near(L) !== on)) continue;
      g.moveTo(x[L.s], y[L.s]);
      g.lineTo(x[L.t], y[L.t]);
    }
    g.globalAlpha = alpha * 0.6;
    g.lineWidth = 0.8 * px;
    g.strokeStyle = C.shared;
    g.setLineDash([4 * px, 3 * px]);
    g.stroke();
    g.setLineDash([]);
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
      const key = (calls ? "c" : ranOnly(L) ? "r" : "s") + Math.round(2 * Math.min(7, 0.8 + 1.1 * Math.log2(1 + L.w))) / 2;
      if (!buckets.has(key)) buckets.set(key, []);
      buckets.get(key).push(L);
    }
    g.globalAlpha = alpha;
    for (const [key, list] of buckets) {
      g.beginPath();
      for (const L of list) { g.moveTo(x[L.s], y[L.s]); g.lineTo(x[L.t], y[L.t]); }
      g.lineWidth = +key.slice(1) * px;
      g.strokeStyle = key[0] === "s" ? C.shared : C.call;
      g.setLineDash(key[0] === "c" ? [] : key[0] === "r" ? [2.5 * px, 2.5 * px] : [5 * px, 3 * px]);
      g.stroke();
    }
    g.setLineDash([]);
    g.beginPath();
    for (const list of buckets.values()) {
      for (const L of list) {
        if (L.calls[0] || L.refers[0] || L.ran[0]) arrow(g, L.s, L.t, px);
        if (L.calls[1] || L.refers[1] || L.ran[1]) arrow(g, L.t, L.s, px);
      }
    }
    g.fillStyle = C.call;
    g.fill();
  }
  // the functions by how sure their names are, bucketed in one pass: a disc filled whole (a name in the
  // tables, a vote every answer agreed with, a proposal without a vote) is one path a level, filled and
  // outlined; one filled in part is its empty disc outlined, then the wedge its vote agreed (from twelve
  // o'clock, inside the outline); outlines dashed without a vote
  const levels = Object.keys(CERTAINTY);
  for (const on of passes) {
    g.globalAlpha = on ? 1 : 0.2;
    const whole = levels.map(() => []), part = levels.map(() => []);
    for (let i = 0; i < nodes.length; i++) {
      const node = nodes[i];
      if (node.kind !== "function" || !shown(on, lit && lit.has(i)) || !seen(i, node.r)) continue;
      const f = d.functions[node.fi], level = levels.indexOf(f.certainty || "none");
      const share = filled(levels[level], f.confidence);
      (share >= 0.999 ? whole : part)[level].push(i);
    }
    const discs = (list) => {
      g.beginPath();
      for (const i of list) { g.moveTo(x[i] + nodes[i].r, y[i]); g.arc(x[i], y[i], nodes[i].r, 0, 2 * Math.PI); }
    };
    g.lineWidth = 1.1 * px;
    levels.forEach((level, k) => {
      if (!whole[k].length && !part[k].length) return;
      g.setLineDash(level === "unvoted" ? [2.2 * px, 1.6 * px] : []);
      g.strokeStyle = C.line[level];
      if (whole[k].length) {
        discs(whole[k]);
        g.fillStyle = C.fill[level];
        g.fill();
        g.stroke();
      }
      if (part[k].length) {
        discs(part[k]);
        g.fillStyle = C.empty;
        g.fill();
        g.stroke();
        g.beginPath();
        for (const i of part[k]) {
          const f = d.functions[nodes[i].fi], share = filled(level, f.confidence), r = nodes[i].r - 0.55 * px;
          if (share <= 0 || r <= 0) continue;
          g.moveTo(x[i], y[i]);
          g.arc(x[i], y[i], r, -Math.PI / 2, -Math.PI / 2 + 2 * Math.PI * share);
          g.closePath();
        }
        g.fillStyle = C.fill[level];
        g.fill();
      }
    });
    g.setLineDash([]);
  }
  // the groups: their discs, then a ring of their functions by how sure their names are
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
    const arcs = mine.map((i) => {         // (each ring's pieces, the surest first, from twelve o'clock)
      const grp = d.groups[nodes[i].g], counts = groupCertainty(grp), total = Math.max(1, grp.functions);
      let at = -Math.PI / 2;
      return RING.map((level) => {
        const n = counts[level] || 0, a0 = at;
        at += 2 * Math.PI * n / total;
        return n ? [a0, at] : null;
      });
    });
    for (const [k, level] of RING.entries()) {
      g.beginPath();
      mine.forEach((i, j) => {
        const arc = arcs[j][k];
        if (!arc) return;
        const r = nodes[i].r, ring = Math.min(r * 0.45, Math.max(2.5 * px, r * 0.22));
        const outer = r - 0.6 * px, inner = Math.max(0, outer - ring);
        g.moveTo(x[i] + outer * Math.cos(arc[0]), y[i] + outer * Math.sin(arc[0]));
        g.arc(x[i], y[i], outer, arc[0], arc[1]);
        g.arc(x[i], y[i], inner, arc[1], arc[0], true);
        g.closePath();
      });
      g.fillStyle = C.fill[level];
      g.fill();
    }
  }
  g.globalAlpha = 1;
  if (P.foundNodes) {                   // what the search lights: a ring round each
    g.beginPath();
    for (const i of P.foundNodes) {
      if (!nodes[i] || !seen(i, nodes[i].r + 4 * px)) continue;
      const r = nodes[i].r + (nodes[i].kind === "group" ? 3 : 2.5) * px;
      g.moveTo(x[i] + r, y[i]);
      g.arc(x[i], y[i], r, 0, 2 * Math.PI);
    }
    g.lineWidth = 2 * px;
    g.strokeStyle = C.strong;
    g.stroke();
  }
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

// A group's ring: its functions by how sure their names are, the surest first (an older record without
// certainties: its named and proposed functions).
const RING = ["named", "sure", "likely", "unsure", "unvoted"];
function groupCertainty(grp) {
  return grp.certainty || { named: grp.naming.named, unvoted: grp.naming.proposed };
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
    const grp = d.groups[gid], label = `${grp.label}${groupMarkText(grp)}  ${grp.functions}  ×`;
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
    const grp = d.groups[node.g], label = (grp.label.length > 26 ? grp.label.slice(0, 25) + "…" : grp.label) + groupMarkText(grp);
    g.font = bold;
    const width = g.measureText(label).width, ty = sy(i) + node.r * v.k + 10;
    if (!free(sx(i) - width / 2 - 2, ty - 8, sx(i) + width / 2 + 2, ty + 20) && i !== P.hover) continue;
    g.globalAlpha = lit && !lit.has(i) ? 0.3 : 1;
    text(label, sx(i) - width / 2, ty, C.text, bold);
    const hits = P.found && P.foundGroups.get(node.g);
    const count = hits ? `${grp.functions} · ${hits} found` : String(grp.functions);
    g.font = small;
    text(count, sx(i) - g.measureText(count).width / 2, ty + 13, hits ? C.strong : C.muted, small);
  }
  const functions = nodes.length - nodes.filter((n) => n.kind === "group").length;
  const every = functions <= PROGRAM.labels || v.k >= PROGRAM.labelZoom;
  const first = [P.litFrom, P.hover].filter((i) => i >= 0 && nodes[i] && nodes[i].kind === "function");
  const order = [...new Set([...first, ...(lit ? [...lit] : []), ...(every ? nodes.keys() : [])])];
  let drawn = 0;
  const italic = `italic 11px ${C.mono}`;
  for (const i of order) {                  // (the lit node's own label first, then its neighbours', then the rest)
    const node = nodes[i];
    if (drawn >= PROGRAM.maxLabels) break;
    if (node.kind !== "function" || !seen(i, node.r)) continue;
    const on = lit && lit.has(i);
    const f = d.functions[node.fi], proposed = f.naming === "proposed" && f.proposed;
    const name = proposed ? f.proposed : f.name, lx = sx(i) + node.r * v.k + 4, font = proposed ? italic : small;
    g.font = font;
    if (!free(lx, sy(i) - 7, lx + g.measureText(name).width, sy(i) + 7) && !first.includes(i)) continue;
    g.globalAlpha = lit && !on ? 0.25 : 1;
    text(name, lx, sy(i), i === P.litFrom ? C.strong : C.text, font);
    drawn += 1;
  }
  g.globalAlpha = 1;
  if (P.marked) drawMarks(g, seen);
}

// A group's own name's mark from the review, after its label (its name, not its functions').
function groupMarkText(grp) {
  const m = grp.review && grp.review.mark;
  return m && m !== "confirmed" && MARKS[m] ? " " + MARKS[m].glyph : "";
}

// The review's marks in screen pixels: on each function shown that has one (not a confirmed name), at
// its upper right; on a closed group, the suspects and flagged names it holds, counted.
function drawMarks(g, seen) {
  const C = P.colors, v = P.view, { x, y } = P.sim, nodes = P.nodes, d = P.data, lit = P.lit;
  g.textAlign = "center";
  g.textBaseline = "middle";
  const badge = (cx, cy, r, kind, label) => {
    g.font = `700 ${Math.round(r * 1.55)}px ${C.sans}`;
    const w = label.length > 1 ? Math.max(2 * r, g.measureText(label).width + r) : 2 * r;
    g.beginPath();
    g.roundRect(cx - w / 2, cy - r, w, 2 * r, r);
    g.fillStyle = C.mk[kind];
    g.fill();
    g.lineWidth = 1.2;
    g.strokeStyle = C.panel;
    g.stroke();
    g.fillStyle = C.mkInk[kind];
    g.fillText(label, cx, cy + 0.5);
  };
  for (let i = 0; i < nodes.length; i++) {
    const node = nodes[i], rs = node.r * v.k;
    if (!seen(i, node.r)) continue;
    g.globalAlpha = lit && !lit.has(i) ? 0.3 : 1;
    if (node.kind === "function") {
      const m = (d.functions[node.fi].review || {}).mark;
      if (!m || m === "confirmed" || !MARKS[m]) continue;
      const r = Math.max(3.5, Math.min(5.5, rs * 0.75)), at = rs * 0.72 + r * 0.45;
      badge(x[i] * v.k + v.tx + at, y[i] * v.k + v.ty - at, r, m, MARKS[m].glyph);
    } else {
      const marks = d.groups[node.g].marks || {};
      let dx = rs * 0.72;
      for (const m of ["flagged", "suspect"]) {
        if (!marks[m]) continue;
        const label = MARKS[m].glyph + marks[m];
        badge(x[i] * v.k + v.tx + dx + 6, y[i] * v.k + v.ty - rs * 0.72, 6, m, label);
        dx += 18;
      }
    }
  }
  g.globalAlpha = 1;
  g.textAlign = "start";
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

// A node and its neighbours lit, the rest dimmed (-1: none, or what the search found).
function litFrom(i) {
  P.litFrom = i;
  if ((i < 0 || !P.nodes[i]) && P.foundNodes) { P.lit = P.foundNodes; return; }
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
    if (L.ran[0] + L.ran[1]) parts.push(`${L.ran[0] + L.ran[1]} through pointers or started (system run)`);
    if (L.shared) parts.push(`${L.shared} shared`);
    return `<span class="mono">${esc(name)}</span> (${parts.join(", ")})`;
  });
  if (node.kind === "group") {
    const g = d.groups[node.g];
    const largest = P.members[node.g].slice().sort((a, b) => d.functions[b].size - d.functions[a].size).slice(0, 6)
      .map((fi) => esc(d.functions[fi].name));
    line("functions", `${g.functions} · ${size(g.bytes)} · <span class="mono">${hex(g.first)}–${hex(g.last)}</span>`);
    const counts = groupCertainty(g);
    line("names", Object.keys(CERTAINTY).filter((k) => counts[k]).map((k) => `${pie(k, { likely: 0.6, unsure: 0.4 }[k], false)} ${counts[k]} ${esc(CERTAINTY[k].short)}`).join(", ") || "none");
    const marks = g.marks || {};
    const held = ["flagged", "suspect", "changed"].filter((k) => marks[k]).map((k) => `${markHTML({ mark: k }, false)} ${marks[k]} ${MARKS[k].short}`);
    if (held.length) line("review", held.join(", "));
    if (g.from === "pilot") line("named", `by dread pilot${g.prefix ? `, prefix <span class="mono">${esc(g.prefix)}</span>` : ""}${g.what ? ": " + esc(g.what) : ""}` +
      (g.name_certainty ? ` <span class="sub">(${esc(certaintyWords(g.name_certainty, g.confidence != null ? +g.confidence : null))})</span>` : ""));
    else if (g.from === "prefix") line("named", `by the prefix ${g.common.functions} of its functions share`);
    else if (g.from === "function") line("named", "by its only function");
    else line("named", "not yet: no prefix most of its functions share, no name from dread pilot");
    if (g.review) reviewTip(g.review, line);
    line("largest", `<span class="mono">${largest.join(", ")}</span>`);
    if (joined.length) line("joined to", joined.join("<br>"));
    const hits = P.found && P.foundGroups.get(node.g);
    if (hits) line("found", `${hits} of its functions`);
    return `<div class="tip-h">${esc(g.label)} <span class="sub">group ${g.group}</span></div><div class="tip-g">${rows.join("")}</div>` +
      `<div class="tip-hint">click: its functions · drag: move it</div>`;
  }
  const f = d.functions[node.fi], g = d.groups[node.g];
  line("address", `<span class="mono">${hex(f.entry)}</span> · ${f.size} bytes`);
  line("group", `${esc(g.label)} <span class="sub">(group ${g.group}, ${g.functions} functions)</span>`);
  if (f.origin) line("origin", `${dot(f.origin)} ${esc(ORIGINS[f.origin].long)}`);
  line("name", `${pie(f.certainty || "none", f.confidence, false)} ${esc(certaintyWords(f.certainty || "none", f.confidence))}` +
    (f.proposed ? `: <span class="mono prop">${esc(f.proposed)}</span>` : ""));
  reviewTip(f.review, line);
  line("calls", `${P.calls.out[node.fi]} functions; called by ${P.calls.in[node.fi]}`);
  const ranBy = (d.ran || []).filter((r) => r[1] === node.fi), ranTo = (d.ran || []).filter((r) => r[0] === node.fi);
  const ranName = (i) => esc(d.functions[i].name);
  if (ranBy.length) line("system run", ranBy.map((r) => `${r[2] === "return" ? "started by" : "called through a pointer by"} <span class="mono">${ranName(r[0])}</span> ×${commas(r[3])}`).join("<br>"));
  if (ranTo.length) line(ranBy.length ? "" : "system run", ranTo.map((r) => `${r[2] === "return" ? "starts" : "calls through a pointer"} <span class="mono">${ranName(r[1])}</span> ×${commas(r[3])}`).join("<br>"));
  for (const e of f.entered_by || []) line("entered by", `${e.how === "exception" ? "exception " + e.exception : e.how === "reset" ? "the reset" : "a system call"} ×${commas(e.times)}`);
  if (joined.length) line("joined to", joined.join("<br>"));
  const found = P.found && P.found.functions.find((x) => x.name === f.name);
  if (found) line("found", `by its ${found.how.join(", ")}${found.text ? `: <span class="mono">${esc(found.text)}</span>` : ""}`);
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
  if (P.found) progFound();
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
  main.addEventListener("input", (e) => {
    if (e.target.id !== "prog-search") return;
    clearTimeout(P.searchTimer);
    const q = e.target.value;
    P.searchTimer = setTimeout(() => progSearch(q), 160);
  });
  main.addEventListener("keydown", (e) => {
    if (e.target.id !== "prog-search") return;
    if (e.key === "Escape") { e.target.value = ""; progSearch(""); e.target.blur(); }
    else if (e.key === "Enter" && P.found && P.found.groups.length) {
      e.preventDefault();
      for (const g of P.found.groups) P.open.add(g.group);
      P.touched = false;
      progRegroup();
    }
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

// The search of the whole program (/api/program-search: the server finds the functions by name, the
// name proposed, address or text, and the groups that hold them): the functions shown lit, and each
// closed group holding some, with how many; Enter opens those groups, Escape ends it.
async function progSearch(q) {
  P.query = q;
  if (!q.trim()) { P.found = null; progFound(); return; }
  let found;
  try {
    found = await api("/api/program-search", { q });
  } catch (e) {
    $("#prog-found").innerHTML = `<span class="bad">${esc(e.message)}</span>`;
    return;
  }
  const box = $("#prog-search");
  if (S.view !== "program" || !box || box.value !== q || found.waiting) return;
  P.found = found;
  progFound();
}

// What the search found, on the nodes shown now (a function of an open group, else its group).
function markFound() {
  P.foundGroups = new Map();
  P.foundNodes = null;
  if (!P.found) return;
  const lit = new Set();
  for (const g of P.found.groups) P.foundGroups.set(g.group, g.functions);
  for (const f of P.found.functions) {
    const i = P.index.has("f:" + f.name) ? P.index.get("f:" + f.name) : P.index.get("g:" + f.group);
    if (i !== undefined) lit.add(i);
  }
  P.foundNodes = lit;
}

function progFound() {
  markFound();
  const box = $("#prog-found"), f = P.found;
  if (box) {
    if (!f) box.textContent = "";
    else if (!f.functions.length) box.textContent = "nothing found";
    else {
      const closed = f.groups.filter((g) => !P.open.has(g.group)).length;
      box.innerHTML = `${f.functions.length}${f.more ? "+" : ""} function${f.functions.length === 1 ? "" : "s"} in ${f.groups.length} group${f.groups.length === 1 ? "" : "s"}` +
        (closed ? ` · <span class="legend-line inline">Enter: open ${closed === 1 ? "it" : "them"}</span>` : "");
    }
  }
  litFrom(P.hover >= 0 ? P.hover : stickyIndex());
  progRedraw();
}

// ---- the system run: both images' device logs side by side (/api/timeline) ----
//
// What the system run's records hold (dread sysrun, dread lift --check's whole image; BUILD/sysrun): a
// scenario's runs on the original and the rebuilt image, their device logs entry by entry, where they
// first differ, and the function running at each entry where the run kept it. A virtual list: only the
// rows on screen are drawn, and the server is asked for the entries around them in chunks.

const TL_CHUNK = 400;           // entries one answer brings
const T = { name: null, data: null, chunks: new Map(), asked: new Set(), first: 0, kept: 0, labels: [], stamp: null };
const tlLink = (name) => "#/s" + (name ? "/" + encodeURIComponent(name) : "");
// A record by its scenario, and the tool where it is not dread sysrun's own (the lift check's whole image).
const recordLabel = (x) => (x.name === x.scenario ? x.scenario
  : x.scenario + " · " + (x.by === "dread lift --check" ? "lift check" : x.name.slice(x.scenario.length + 1)));
const NARROW = matchMedia("(max-width: 700px)");
const tlRowHeight = () => (NARROW.matches && T.labels.length > 1 ? 44 : 23);   // (app.css: .tlrow)
const commas = (n) => (n == null ? "" : Number(n).toLocaleString("en-US"));

async function showTimeline(name, keep = false) {
  const mine = ++renders;
  let d;
  try {
    d = await api("/api/timeline", Object.assign({ count: 1 }, name ? { name } : {}));
  } catch (e) {
    if (mine === renders) showError(e.message);
    return;
  }
  if (mine !== renders) return;
  stopRelations();
  if (P.frame) cancelAnimationFrame(P.frame);
  P.frame = 0;
  const main = $("#main");
  const picked = d.scenarios.find((x) => x.name === d.name);
  const stamp = picked ? picked.name + "|" + picked.when : null;
  const again = keep && S.view === "timeline" && T.stamp === stamp && $("#tl-list");
  const scroll = again ? $("#tl-list").scrollTop : null;
  S.view = "timeline";
  S.current = null;
  renderList();
  document.title = "System run · DREAD";
  watch(d.status);
  if (!d.scenarios.length) {
    main.innerHTML = `<div class="ov"><h1>System run</h1><div class="none tl-none">No run recorded yet. <span class="mono">dread sysrun</span> keeps
      each scenario's runs (original and rebuilt image) in BUILD/sysrun, and <span class="mono">dread lift --check</span> its whole image's.</div></div>`;
    return;
  }
  if (!again) { T.chunks = new Map(); T.asked = new Set(); }
  Object.assign(T, { name: d.name, data: d, first: d.first, kept: d.kept, labels: d.runs.map((r) => r.label), stamp });
  main.innerHTML = timelineHTML(d);
  main.scrollTop = 0;
  sizeTimeline();
  const list = $("#tl-list");
  $("#tl-spacer").style.height = T.kept * tlRowHeight() + "px";     // (before a scroll can go there)
  if (scroll != null) list.scrollTop = scroll;
  else if (d.differ && d.differ.at != null) tlGo(d.differ.at, false);
  renderTimeline();
}

function timelineHTML(d) {
  const pick = d.scenarios.length > 1
    ? `<div class="seg" id="tl-pick">${d.scenarios.map((x) => `<button data-s="${esc(x.name)}"${x.name === d.name ? ' class="on"' : ""}` +
      ` title="${esc(x.scenario)}, ${esc(x.by || "")}: ${x.runs.length < 2 ? "the original alone" : x.differ ? "the logs differ" : "the images agree"}">${esc(recordLabel(x))}${x.differ ? " ≠" : ""}</button>`).join("")}</div>`
    : `<span class="badge">${esc(recordLabel(d))}</span>`;
  const picked = d.scenarios.find((x) => x.name === d.name) || {};
  const texts = d.runs.map((r) => (r.reports && r.reports.uart && r.reports.uart.sent) || "");
  const same = texts.length > 1 && texts.every((t) => t === texts[0]);
  const run = (r, k) => {
    const sent = same && k > 0 ? "" : tail(texts[k], 4, 240);
    const ints = Object.entries(r.interrupts || {}).map(([n, k]) => `${n}: ${commas(k)}`).join(", ");
    return `<div class="tl-run"><span class="tl-label">${esc(r.label)}</span><span class="tl-how">${esc(r.how)}</span>` +
      `<span class="legend-line inline">clock ${commas(r.clock)} · ${commas(r.entries)} entries${ints ? ` · interrupts ${esc(ints)}` : ""}</span>` +
      (sent ? `<span class="tl-sent" title="what the serial port model was sent${same ? " (the same in both runs)" : ""}">${esc(sent)}</span>` : "") + `</div>`;
  };
  let state;
  if (d.runs.length < 2) state = `<span class="legend-line inline">the original alone (dread sysrun --original-only)</span>`;
  else if (d.differ) {
    state = `<span class="bad">The images differ${d.differ.at != null ? ` from entry ${commas(d.differ.at)}` : ""}</span>` +
      (d.differ.at != null ? ` <button id="tl-go" title="Scroll to the first difference (d)">Show it</button>` : "") +
      `<pre class="tl-lines">${esc(d.differ.lines.join("\n"))}</pre>`;
  } else state = `<span class="good">The images agree: every entry of the device logs is the same.</span>`;
  const window = d.kept < Math.max(...d.runs.map((r) => r.entries))
    ? `<span class="legend-line inline">the record keeps entries ${commas(d.first)}–${commas(d.first + d.kept - 1)} (${commas(d.keep)} at most)</span>` : "";
  const clock = d.built ? `<span class="legend-line inline" title="the shared clock counts the code both images share">the original's clock leaves out the code of the ${commas(d.built)} functions the rebuilt image has from C</span>` : "";
  const cols = d.runs.map((r) => `<span>${esc(r.label)}<span class="sub"> · function · access</span></span>`).join("");
  return `
    <div class="fn-head compact">
      <div class="fn-title"><h1>System run</h1>${pick}<span class="badge" title="${esc(when(picked.when))}">${esc(d.by || "")}, ${ago(picked.when)}</span>
        <span class="grow"></span></div>
      <div class="tl-sum">${d.runs.map(run).join("")}<div class="tl-state">${state} ${window} ${clock}</div></div>
    </div>
    <section class="tl${d.runs.length < 2 ? " one" : ""}" id="tl">
      <div class="tl-cols"><span class="ix">entry</span>${cols}</div>
      <div class="tl-list" id="tl-list"><div class="spacer" id="tl-spacer"></div></div>
    </section>`;
}

// The last lines of a text, at most chars of it ("…" where it was cut).
function tail(text, lines, chars) {
  const parts = text.replace(/\r/g, "").split("\n");
  let out = parts.slice(-lines).join("\n");
  if (out.length > chars) out = out.slice(-chars);
  return out.length < text.replace(/\r/g, "").length ? "…" + out : out;
}

function sizeTimeline() {
  const list = $("#tl-list");
  if (!list) return;
  const top = list.getBoundingClientRect().top + $("#main").scrollTop;
  list.style.height = Math.max(240, Math.floor(window.innerHeight - top - 8)) + "px";
}

// The rows on screen, from the chunks there are; the missing chunks asked for.
function renderTimeline() {
  const list = $("#tl-list"), spacer = $("#tl-spacer");
  if (!list || !T.data) return;
  const row = tlRowHeight();
  spacer.style.height = T.kept * row + "px";
  const from = Math.max(0, Math.floor(list.scrollTop / row) - 8);
  const to = Math.min(T.kept, Math.ceil((list.scrollTop + list.clientHeight) / row) + 8);
  for (let k = Math.floor(from / TL_CHUNK); k <= Math.floor(Math.max(from, to - 1) / TL_CHUNK); k++) askChunk(k);
  let html = "";
  for (let i = from; i < to; i++) html += tlRowHTML(i);
  spacer.innerHTML = html;
  for (const el of spacer.children) el.style.transform = `translateY(${el.dataset.i * row}px)`;
}

async function askChunk(k) {
  if (T.chunks.has(k) || T.asked.has(k) || k * TL_CHUNK >= T.kept) return;
  T.asked.add(k);
  const { name, stamp } = T;
  try {
    const d = await api("/api/timeline", { name, from: T.first + k * TL_CHUNK, count: TL_CHUNK });
    if (T.name !== name || T.stamp !== stamp) return;
    T.chunks.set(k, d.rows);
    if (S.view === "timeline") renderTimeline();
  } catch {
    T.asked.delete(k);                  // (asked again when the rows come back on screen)
  }
}

function tlRowHTML(i) {
  const n = T.first + i, rows = T.chunks.get(Math.floor(i / TL_CHUNK)), at = i % TL_CHUNK;
  if (!rows) return `<div class="tlrow wait" data-i="${i}"><span class="ix">${commas(n)}</span><span class="cell"><span class="ev">…</span></span></div>`;
  const entries = T.labels.map((label) => (rows[label] && at < rows[label].log.length ? rows[label].log[at] : null));
  const differs = T.labels.length === 2 && JSON.stringify(entries[0]) !== JSON.stringify(entries[1]);
  const first = T.data.differ && T.data.differ.at === n;
  const cells = T.labels.map((label, k) => tlCell(rows[label], at, entries[k], label, T.labels.length === 2));
  return `<div class="tlrow${differs ? " differ" : ""}${first ? " first" : ""}" data-i="${i}"><span class="ix">${commas(n)}</span>${cells.join("")}</div>`;
}

// One image's entry: the function its pc lies in (a link to it, at that instruction where the pc is the
// listing's: the original's), and the entry.
function tlCell(r, at, e, label, both) {
  const tag = both ? `<span class="tl-tag">${esc(label[0])}</span>` : "";
  if (!e) return `<span class="cell end">${tag}<span class="ev">(its log has ended)</span></span>`;
  const fn = r.in ? r.in[at] : null, pc = r.at ? r.at[at] : null;
  const known = fn ? S.byName.get(fn) : null;
  const listed = pc != null && known && label === "original";
  const where = fn ? `<a class="fn" href="${fnLink(fn, listed ? pc : null)}" title="${esc(fn)}${pc != null ? " at " + hex(pc) : ""}">${nameHTML(known || { name: fn })}</a>`
    : pc != null ? `<span class="fn none" title="in no function of the tables">${hex(pc)}</span>` : "";
  return `<span class="cell">${tag}${where}<span class="ev" title="${esc(JSON.stringify(e))}">${entryHTML(e)}</span></span>`;
}

// One entry of a device log (dread sysrun's, as compared): a read with its value and how often it was
// read in a row, a write, an interrupt taken, a system call, what a model noted (a byte sent).
function entryHTML(e) {
  const v = (x) => (typeof x === "number" ? hex(x) : esc(String(x)));
  if (e[0] === "read") return `<b class="ek r">read</b> ${esc(e[1])} <span class="ad">${v(e[2])}</span> → ${v(e[4])}${e[5] > 1 ? ` <span class="ct">×${commas(e[5])}</span>` : ""}`;
  if (e[0] === "write") return `<b class="ek w">write</b> ${esc(e[1])} <span class="ad">${v(e[2])}</span> ← ${v(e[4])}`;
  if (e[0] === "interrupt") return `<b class="ek i">interrupt</b> ${esc(e[1])} ${esc(String(e[2]))}`;
  if (e[0] === "syscall") return `<b class="ek i">system call</b>`;
  const byte = e[1] === "sent" && typeof e[2] === "number" && e[2] >= 32 && e[2] < 127 ? ` <span class="ch">'${esc(String.fromCharCode(e[2]))}'</span>` : "";
  return `<b class="ek n">${esc(String(e[0]))} ${esc(String(e[1]))}</b> ${e.slice(2).map(v).join(" ")}${byte}`;
}

// The list scrolled to an entry (absolute), it in the upper third.
function tlGo(n, smooth = true) {
  const list = $("#tl-list");
  if (!list || n == null) return;
  const row = tlRowHeight(), i = n - T.first;
  list.scrollTo({ top: Math.max(0, i * row - list.clientHeight / 3), behavior: smooth && !still() ? "smooth" : "auto" });
}

function wireTimeline() {
  const main = $("#main");
  let ticking = false;                  // (one render a frame while it scrolls)
  main.addEventListener("scroll", (e) => {
    if (e.target.id !== "tl-list" || S.view !== "timeline" || ticking) return;
    ticking = true;
    requestAnimationFrame(() => { ticking = false; renderTimeline(); });
  }, true);
  main.addEventListener("click", (e) => {
    const b = e.target.closest("#tl-pick button");
    if (b) { location.hash = tlLink(b.dataset.s); return; }
    if (e.target.closest("#tl-go") && T.data && T.data.differ) tlGo(T.data.differ.at);
  });
  window.addEventListener("resize", () => { if (S.view === "timeline") { sizeTimeline(); renderTimeline(); } });
  NARROW.addEventListener("change", () => { if (S.view === "timeline") renderTimeline(); });
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
    const sub = r.kind === "text" ? `"${esc(r.text)}" at ${hex(r.address)}` : r.kind === "address" ? `${hex(r.address)} is in it`
      : r.kind === "proposal" ? `${esc(r.name)} · ${hex(r.entry)}` : hex(r.entry);
    const name = r.kind === "proposal" ? `<span class="prop">${esc(r.proposed)}</span>` : esc(r.name);
    return `<div class="result${i === active ? " active" : ""}" data-i="${i}">${dot(r.origin || "original")}<span class="nm">${name}</span>` +
      `<span class="kind">${r.kind === "proposal" ? "proposed" : r.kind}</span><span class="sub sub-line">${sub}</span></div>`;
  }).join("");
}

function openResult(i) {
  const r = results[i];
  if (!r) return;
  $("#results").hidden = true;
  $("#search").blur();
  location.hash = r.kind === "name" || r.kind === "proposal" ? modeLink(r.name) : fnLink(r.name, r.address);
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
  if (GUI.readOnly) return toast("This page is read-only (dread gui --read-only): it renames nothing.", true);
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
  if (GUI.readOnly) return toast("This page is read-only (dread gui --read-only): it keeps no notes.", true);
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
  if (status.names != null && S.names == null) S.names = status.names;
  if (status.runs != null && S.runs == null) S.runs = status.runs;
  const busy = $("#busy");
  busy.hidden = !S.pending.length;
  busy.textContent = S.pending.length ? "finding " + S.pending.map((k) => ANALYSES[k]).join(", ") : "";
}

async function poll() {
  let delay = 4000;
  try {
    const status = await api("/api/status");
    if ((status.target || null) !== (GUI.target ? GUI.target.root : null)) {
      location.reload();                // (the server opened another target: from the Setup page, here or elsewhere)
      return;
    }
    const waited = S.pending.length && !status.pending.length;
    if (status.generation !== S.generation || (status.names != null && status.names !== S.names)) {
      S.generation = status.generation;    // (the tables, or only the names: names.tsv, review.tsv)
      S.names = status.names;
      S.runs = status.runs;
      await loadFunctions();
      await refreshView();
    } else if (waited || (status.runs != null && status.runs !== S.runs)) {
      S.runs = status.runs;              // (a new record of the system run: the graphs and the timeline anew)
      await refreshView();
    }
    pageStatus(status.pages);           // (the registered pages' shares: the jobs, ...)
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

// No target yet (dread gui started where there is none): the browsing views wait for the Setup page.
function showNoTarget() {
  S.view = "error";
  document.title = "DREAD";
  $("#main").innerHTML = `<div class="error-view"><b>No target yet</b><p>dread gui started where there is no dread.toml.
    The <a href="#/setup">Setup page</a> makes a target from an ELF or a raw image, or opens one.</p></div>`;
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
  $("#name-chips").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    const c = chip.dataset.certainty;
    if (S.certainty.has(c)) S.certainty.delete(c); else S.certainty.add(c);
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
    const at = e.target.closest("a.at[data-at]");
    if (at) { e.preventDefault(); selectAddress(+at.dataset.at, true); return; }
    if (e.target.closest("#rename-btn")) return renameCurrent();
    if (e.target.closest("#note-btn")) return noteSelected();
    const tab = e.target.closest(".tab");
    if (tab) { S.tab = tab.dataset.tab; renderC(); return; }
    const by = e.target.closest("#by button");
    if (by) { S.by = by.dataset.by; prefs.set("by", S.by); renderOrigins(); return; }
    const item = e.target.closest(".legend .item");
    if (item && item.dataset.certainty) { S.certainty = new Set([item.dataset.certainty]); renderChips(); applyFilter(); $("#side").classList.add("open"); return; }
    if (item) { S.origins = new Set([item.dataset.origin]); renderChips(); applyFilter(); $("#side").classList.add("open"); return; }
    const mapBy = e.target.closest("#map-by button");
    if (mapBy) { S.mapBy = mapBy.dataset.map; prefs.set("map-by", S.mapBy); renderNames(); drawMap(); return; }
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
    tip.innerHTML = `${esc(fn.name)}${fn.proposed ? ` <span class="prop">${esc(fn.proposed)}</span>` : ""} <span class="sub">${hex(fn.entry)} · ${fn.size} bytes · ${esc(ORIGINS[fn.origin].long)} · name ${esc(certaintyWords(fn.certainty, fn.confidence))}</span>`;
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
      s: () => { location.hash = tlLink(S.view === "timeline" ? T.name : null); },
      d: () => { if (S.view === "timeline" && T.data && T.data.differ) tlGo(T.data.differ.at); },
      "[": () => stepFunction(-1), "]": () => stepFunction(1),
      Escape: () => { selectRow(-1); },
    };
    const action = keys[e.key];
    if (!action) {
      const own = pageKeys[e.key];          // (a registered page's key)
      if (own) { e.preventDefault(); own(e); }
      return;
    }
    if (S.view !== "function" && ["j", "k", "ArrowDown", "ArrowUp", "Enter", "n", ";", "Escape", "r"].includes(e.key)) return;
    if (e.key === "f" && S.view !== "program" && !(S.view === "function" && S.mode === "relations")) return;
    if (e.key === "d" && S.view !== "timeline") return;
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
  S.mapBy = prefs.get("map-by", "origin") === "names" ? "names" : "origin";
  R.depth = [1, 2, 3].includes(prefs.get("rel-depth", 1)) ? prefs.get("rel-depth", 1) : 1;
  const groups = prefs.get("rel-groups", null);
  if (Array.isArray(groups)) R.groups = new Set(groups.filter((k) => GROUPS.some(([g]) => g === k)));
  $("#sort").value = S.sort;
  applyTheme();
  renderChips();
  wire();
  wireRelations();
  wireProgram();
  wireTimeline();
  let gui = null;
  try {
    gui = await api("/api/gui");
  } catch { /* an older server: as before */ }
  if (gui && !gui.target) {             // (no target yet: the Setup page makes or opens one)
    $("#where").innerHTML = `<b>no target yet</b>`;
    document.documentElement.classList.add("no-target");
    await loadPages(gui);
    if (!location.hash || location.hash === "#/" || location.hash === "#") history.replaceState(null, "", "#/setup");
    await route();                      // (replaceState: no hashchange, so one route, not two)
    setTimeout(poll, 1500);
    return;
  }
  try {
    const o = await api("/api/overview");
    const images = o.image.images;
    $("#where").innerHTML = `<b>${esc(o.target.title)}</b>` + (images.length > 1
      ? `<select id="image-select" aria-label="Image">${images.map((n) => `<option${n === o.image.name ? " selected" : ""}>${esc(n)}</option>`).join("")}</select>`
      : ` · ${esc(o.image.name)}`);
    const select = $("#image-select");
    if (select) select.addEventListener("change", () => { location.search = "?image=" + encodeURIComponent(select.value); });
    S.generation = o.status.generation;
    S.names = o.status.names;
    S.runs = o.status.runs;
    await loadFunctions();
  } catch (e) {
    return showError(e.message);
  }
  await loadPages(gui);
  await route();
  setTimeout(poll, 1500);
}

start();
