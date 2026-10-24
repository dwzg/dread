// dread gui's leak check page (the server's dread/guipages/leakcheck.py): dread leakcheck run as a job
// against a tree, the private project's tables, words and allow list given by their paths, and its hits
// listed with their file, lines and the string each matched. The page never sees the private files: only
// what the check's record (dread leakcheck --json) and its log hold, the strings that matched. Its jobs are
// the page's own, kept in the user's state directory, not the target's.
// Addresses: #/leakcheck (the newest check), #/leakcheck/KEY (one check).

import { $, esc, api, ago, when, toast, S, register, enter, nextRender, isCurrent } from "./app.js";

const POLL = 1000;              // ms between looks at a check that runs
const LIVE = new Set(["queued", "running", "stopping"]);
const SHOWN = 400;              // hits a table lists before "all"
const ICON = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 1.8 13.2 4v3.7c0 3.1-2.2 5.6-5.2 6.5-3-.9-5.2-3.4-5.2-6.5V4z"/><path d="M5.8 8.1 7.4 9.7 10.4 6.4"/></svg>`;

const L = { data: null, check: null, key: null, values: null, line: null, seq: 0, timer: 0, lineTimer: 0, job: null,
  log: "", next: null, onlyBad: true, filter: "", all: false, live: null };

function count(n) { return n == null ? "–" : Number(n).toLocaleString("en-US"); }
function plural(n, one, many) { return `${count(n)} ${n === 1 ? one : many || one + "s"}`; }
function disabledWhy(why) { return why ? ` disabled title="${esc(why)}"` : ""; }
function stateWords(job) {
  if (!job) return "";
  return job.state === "failed" ? `failed${job.exit != null ? " (exit status " + job.exit + ")" : ""}` : job.state;
}

// ---- the form ----

function formValues() {
  const v = (id) => ($(id) ? $(id).value : "");
  return { tree: v("#lk-tree"), tables: v("#lk-tables").split("\n").map((s) => s.trim()).filter(Boolean),
    words: v("#lk-words"), allow: v("#lk-allow"), history: !!($("#lk-history") && $("#lk-history").checked), min: v("#lk-min") };
}

function formHTML(d) {
  const v = L.values || d.last || { tree: "", tables: [], words: "", allow: "", history: false, min: d.min };
  const why = d.changes;
  return `<section class="lk-card" id="lk-form"><h2>Check a tree</h2>
    <p class="lk-note">The private files are read by the check alone; this page shows only what matched. Paths as typed, from <span class="mono">${esc(d.home)}</span>; nothing here is stored in a repository (the page's jobs and records: <span class="mono">${esc(d.where)}</span>).</p>
    <div class="lk-row"><label class="lk-k" for="lk-tree">Tree</label><div class="lk-v"><input type="text" id="lk-tree" value="${esc(v.tree || "")}" placeholder="${esc(d.tree)}" spellcheck="false" autocomplete="off"><span class="lk-help">the tree to check (empty: the DREAD this server runs, <span class="mono">${esc(d.tree)}</span>)</span></div></div>
    <div class="lk-row"><label class="lk-k" for="lk-tables">Tables</label><div class="lk-v"><textarea id="lk-tables" rows="3" spellcheck="false" placeholder="one path a line: functions.tsv, symbols.txt">${esc((v.tables || []).join("\n"))}</textarea><span class="lk-help">the private project's function tables or symbol files: their names of ${esc(String(v.min || d.min))} characters and more that are not generic, their addresses of five hex digits and more</span></div></div>
    <div class="lk-row"><label class="lk-k" for="lk-words">Words</label><div class="lk-v"><input type="text" id="lk-words" value="${esc(v.words || "")}" placeholder="optional: one word or expression a line" spellcheck="false" autocomplete="off"></div></div>
    <div class="lk-row"><label class="lk-k" for="lk-allow">Allow list</label><div class="lk-v"><input type="text" id="lk-allow" value="${esc(v.allow || "")}" placeholder="optional: the hits that are coincidences" spellcheck="false" autocomplete="off"></div></div>
    <div class="lk-row"><span class="lk-k">Also</span><div class="lk-v"><label class="lk-check"><input type="checkbox" id="lk-history"${v.history ? " checked" : ""}> every commit of the tree's history and its authors (<span class="mono">--history</span>)</label>
      <label class="lk-check">shortest name <input type="number" id="lk-min" min="1" value="${esc(String(v.min || d.min))}" class="lk-num"></label></div></div>
    <pre class="lk-cmd" id="lk-line"></pre><div class="lk-error" id="lk-error"></div>
    <div class="lk-actions"><span class="lk-muted" id="lk-job-state"></span><button type="button" id="lk-stop" hidden>Stop</button><button type="button" class="primary" id="lk-run"${disabledWhy(why)}>Check</button></div>
    <pre class="lk-log" id="lk-log" hidden></pre></section>`;
}

function scheduleLine() {
  clearTimeout(L.lineTimer);
  L.lineTimer = setTimeout(askLine, 200);
}

async function askLine() {
  const mine = ++L.seq;
  L.values = formValues();
  let r;
  try {
    r = await api("/api/leakcheck/line", { values: JSON.stringify(L.values) });
  } catch (e) {
    r = { error: e.message };
  }
  if (mine !== L.seq || !$("#lk-line")) return;
  L.line = r;
  $("#lk-line").textContent = r.line || "";
  $("#lk-error").textContent = r.error || (r.missing && r.missing.length ? "No such file or directory: " + r.missing.join(", ") : "");
  const run = $("#lk-run");
  if (run && !L.data.changes) run.disabled = !!(r.error || (r.missing && r.missing.length) || (L.job && LIVE.has(L.job.state)));
}

// ---- a check's hits ----

function summaryHTML(c) {
  const bad = c.not_allowed || 0;
  const looked = c.looked_for || {};
  return `<div class="lk-tiles">
    <div class="lk-tile ${bad ? "lk-bad" : "lk-good"}"><div class="lk-tile-k">hits not allowed</div><div class="lk-tile-v">${count(bad)}</div><div class="lk-tile-d">${bad ? "each one stops a push until it is taken out or allowed" : "none: the check passes (exit status 0)"}</div></div>
    <div class="lk-tile"><div class="lk-tile-k">allowed</div><div class="lk-tile-v">${count(c.allowed)}</div><div class="lk-tile-d">coincidences on the allow list</div></div>
    <div class="lk-tile"><div class="lk-tile-k">looked for</div><div class="lk-tile-v lk-small">${plural(looked.names, "name")}, ${plural(looked.addresses, "address", "addresses")}, ${plural(looked.words, "word")}</div><div class="lk-tile-d">${looked.names || looked.addresses || looked.words ? "from the private files" : "<b>nothing</b>: are the tables the right ones?"}</div></div>
    ${c.history ? `<div class="lk-tile"><div class="lk-tile-k">in the history</div><div class="lk-tile-v">${count((c.history_hits || []).length)}</div><div class="lk-tile-d">hits in commits' messages and diffs</div></div>` : ""}
  </div>`;
}

function hitsHTML(c) {
  const text = L.filter.trim().toLowerCase();
  const hits = (c.hits || []).filter((h) => (!L.onlyBad || !h.allowed) && (!text || h.file.toLowerCase().includes(text) || h.hit.toLowerCase().includes(text)));
  const shown = L.all ? hits : hits.slice(0, SHOWN);
  const rows = shown.map((h) => `<tr class="${h.allowed ? "lk-allowed" : "lk-hit"}"><td class="mono lk-file">${esc(h.file)}</td><td class="mono lk-lines">${esc(h.lines.join(", "))}</td><td class="mono lk-match">${esc(h.hit)}</td><td>${h.allowed ? `<span class="lk-muted">allowed</span>` : `<span class="bad">not allowed</span>`}</td></tr>`).join("");
  return `<section class="lk-card" id="lk-hits"><div class="lk-card-head"><h2>Hits in the tree</h2><span class="lk-muted">${plural((c.hits || []).length, "hit")} (a hit: a file and the string it matched; its lines)</span></div>
    <div class="lk-filters"><input type="search" id="lk-filter" placeholder="Filter: file or string" value="${esc(L.filter)}" spellcheck="false" autocomplete="off" aria-label="Filter the hits">
      <label class="lk-check"><input type="checkbox" id="lk-only-bad"${L.onlyBad ? " checked" : ""}> only those not allowed</label></div>
    ${hits.length ? `<div class="lk-scroll"><table class="lk-table"><thead><tr><th>file</th><th>lines</th><th>matched</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>` : `<div class="lk-none">${(c.hits || []).length ? "None of them matches the filter." : "No hit in the tree."}</div>`}
    ${hits.length > shown.length ? `<button type="button" class="lk-link" id="lk-all">all ${count(hits.length)}</button>` : ""}</section>`;
}

function historyHTML(c) {
  if (!c.history) return "";
  const hits = c.history_hits || [];
  return `<section class="lk-card"><div class="lk-card-head"><h2>Hits in the history</h2><span class="lk-muted">not allowed ones only, once a commit</span></div>
    ${hits.length ? `<div class="lk-scroll"><table class="lk-table"><thead><tr><th>commit</th><th>file</th><th>matched</th></tr></thead><tbody>
      ${hits.map((h) => `<tr class="lk-hit"><td class="mono">${esc(h.commit)}</td><td class="mono lk-file">${esc(h.file || "(message or author)")}</td><td class="mono lk-match">${esc(h.hit)}</td></tr>`).join("")}</tbody></table></div>` : `<div class="lk-none">No hit in the history.</div>`}
    ${c.authors ? `<h3>Commit authors</h3><div class="lk-authors">${c.authors.map((a) => `<span class="lk-chip">${esc(a)}</span>`).join("")}</div>` : ""}</section>`;
}

function checksHTML(d) {
  if (!d.checks.length) return "";
  return `<section class="lk-card"><h2>Checks made</h2><div class="lk-scroll"><table class="lk-table lk-checks"><thead><tr><th>when</th><th>tree</th><th class="r">tables</th><th>history</th><th class="r">not allowed</th><th class="r">allowed</th><th></th></tr></thead><tbody>
    ${d.checks.map((c) => `<tr class="${c.key === L.key ? "on" : ""}"><td><a href="#/leakcheck/${esc(c.key)}" title="${esc(when(c.when))}">${esc(ago(c.when))}</a></td><td class="mono lk-file" title="${esc(c.tree || "")}">${esc(c.tree || "")}</td><td class="r">${count((c.tables || []).length)}</td><td>${c.history ? "yes" : "no"}</td><td class="r ${c.not_allowed ? "bad" : ""}">${count(c.not_allowed)}</td><td class="r">${count(c.allowed)}</td><td>${c.job && LIVE.has(c.job.state) ? `<span class="lk-busy">${esc(c.job.state)}</span>` : ""}</td></tr>`).join("")}
    </tbody></table></div></section>`;
}

function render() {
  const d = L.data, c = L.check, main = $("#main");
  let html = `<div class="lk"><div class="lk-head"><h1>Leak check</h1><span class="lk-muted">dread leakcheck: a private project's names, addresses and words looked for in a tree before it is published</span></div>`;
  if (d.changes) html += `<div class="lk-blocked">${esc(d.changes)}: this page shows the checks and starts none.</div>`;
  html += formHTML(d);
  if (c) {
    html += `<div class="lk-result-head"><h2 class="lk-h">${c.key === (d.checks[0] || {}).key ? "The newest check" : "A check"}</h2><span class="lk-muted">of <span class="mono">${esc(c.tree)}</span>, ${esc(ago(c.when))} (${esc(when(c.when))})${c.job ? ` · ${esc(jobWords(c.job))}` : ""}</span></div>`;
    html += summaryHTML(c) + hitsHTML(c) + historyHTML(c);
  } else if (!d.checks.length) html += `<div class="lk-none">No check yet: give the tables above and press Check.</div>`;
  html += checksHTML(d) + `</div>`;
  main.innerHTML = html;
  askLine();
  showJobState();
}

// ---- the job that runs ----

// Whether the check shown is what job j wrote (its record is newer than the job's start).
function wrote(j) {
  return !!(j && L.check && L.check.key === L.key && L.check.when && j.started && L.check.when >= j.started - 1);
}

// A job's state in words; dread leakcheck's exit status 1 is a hit not allowed (the job's record calls it
// failed), where the job wrote the check shown.
function jobWords(j) {
  if (!j) return "";
  const found = !LIVE.has(j.state) && wrote(j);
  return found && j.exit === 1 ? `job ${j.id} ended: hits not allowed (exit status 1)` :
    found && j.exit === 0 ? `job ${j.id} ended: no hit that is not allowed` : `job ${j.id} ${stateWords(j)}`;
}

function showJobState() {
  const box = $("#lk-job-state"), stopBtn = $("#lk-stop"), log = $("#lk-log");
  if (!box) return;
  const j = L.job;
  const found = j && !LIVE.has(j.state) && wrote(j);
  box.textContent = jobWords(j);
  stopBtn.hidden = !(j && LIVE.has(j.state)) || !!L.data.changes;
  log.hidden = !j || found || j.state === "done";     // (while it runs, or where it left no record: its log says why)
  log.textContent = L.log;
  log.scrollTop = log.scrollHeight;
  const run = $("#lk-run");
  if (run && !L.data.changes) run.disabled = !!(j && LIVE.has(j.state)) || !!(L.line && (L.line.error || (L.line.missing && L.line.missing.length)));
}

async function followJob() {
  clearTimeout(L.timer);
  if (!L.job) return;
  let r;
  try {
    r = await api("/api/leakcheck/job", { id: L.job.id, from: L.next == null ? "" : L.next });
  } catch (e) {
    return;
  }
  if (L.job && r.job.id !== L.job.id) return;
  const was = LIVE.has(L.job.state);       // (it ran when this look began: its end reloads the check)
  L.job = r.job;
  L.log = (L.next == null ? "" : L.log) + r.log.text;
  L.next = r.log.next;
  if (r.log.more) { L.timer = setTimeout(followJob, 50); showJobState(); return; }
  showJobState();
  if (LIVE.has(r.job.state)) L.timer = setTimeout(followJob, POLL);
  else if (was && S.view === "leakcheck") show([null, L.key], true);
}

async function runCheck() {
  try {
    const r = await api("/api/leakcheck/run", {}, { values: formValues() });
    L.job = r;
    L.key = r.key;
    L.log = "";
    L.next = null;
    history.replaceState(null, "", "#/leakcheck/" + r.key);
    showJobState();
    followJob();
  } catch (e) {
    toast(e.message, true);
  }
}

async function stopCheck() {
  if (!L.job) return;
  try {
    L.job = await api("/api/leakcheck/stop", {}, { id: L.job.id });
    showJobState();
  } catch (e) {
    toast(e.message, true);
  }
}

// ---- loading ----

async function show(match, keep = false) {
  const mine = nextRender();
  clearTimeout(L.timer);
  let d, c = null;
  const key = match[1] || null;
  try {
    d = await api("/api/leakcheck");
    const pick = key || (d.checks[0] || {}).key;
    if (pick) c = await api("/api/leakcheck/check", { key: pick }).catch((e) => (key ? Promise.reject(e) : null));
  } catch (e) {
    if (!isCurrent(mine)) return;
    enter("leakcheck", "Leak check");
    $("#main").innerHTML = `<div class="lk"><div class="lk-head"><h1>Leak check</h1></div><div class="lk-none bad">${esc(e.message)}</div></div>`;
    return;
  }
  if (!isCurrent(mine)) return;
  const formKept = keep && $("#lk-form") ? formValues() : null;
  L.data = d;
  L.check = c;
  L.key = c ? c.key : null;
  if (!keep || !formKept) L.values = c ? c.values : (d.last || null);
  else L.values = formKept;
  if (!L.job || !LIVE.has(L.job.state)) {
    L.job = (c && c.job) || d.live[0] || null;
    if (L.job && !LIVE.has(L.job.state) && L.job.state === "done") { L.log = ""; L.next = null; }
  }
  const scroll = $("#main").scrollTop;
  enter("leakcheck", "Leak check");
  render();
  $("#main").scrollTop = keep ? scroll : 0;
  if (L.job && (LIVE.has(L.job.state) || (L.job.state !== "done" && !wrote(L.job)))) { L.next = null; L.log = ""; followJob(); }
}

function onStatus(s) {
  if (!s) return;
  const live = s.live || [];
  const badge = document.querySelector('.nav-btn[data-page="leakcheck"] .nav-badge');
  if (badge) {
    badge.hidden = !live.length;
    badge.textContent = live.length;
    badge.parentElement.title = "Leak check (w)" + (live.length ? " · a check runs" : "");
  }
  L.live = live.join(",");
}

function wire() {
  const main = $("#main");
  main.addEventListener("input", (e) => {
    if (S.view !== "leakcheck") return;
    const el = e.target;
    if (el.closest("#lk-form") && el.id !== "lk-log") { scheduleLine(); return; }
    if (el.id === "lk-filter") { L.filter = el.value; rerenderHits(); }
  });
  main.addEventListener("change", (e) => {
    if (S.view !== "leakcheck") return;
    const el = e.target;
    if (el.id === "lk-history" || el.id === "lk-min") { scheduleLine(); return; }
    if (el.id === "lk-only-bad") { L.onlyBad = el.checked; rerenderHits(); }
  });
  main.addEventListener("click", (e) => {
    if (S.view !== "leakcheck") return;
    const t = e.target;
    if (t.closest("#lk-run")) return runCheck();
    if (t.closest("#lk-stop")) return stopCheck();
    if (t.closest("#lk-all")) { L.all = true; rerenderHits(); }
  });
}

function rerenderHits() {
  const box = $("#lk-hits");
  if (!box || !L.check) return;
  const focus = document.activeElement && document.activeElement.id;
  box.outerHTML = hitsHTML(L.check);
  if (focus === "lk-filter") { const f = $("#lk-filter"); f.focus(); f.setSelectionRange(f.value.length, f.value.length); }
}

wire();

register({
  name: "leakcheck", match: /^\/leakcheck(?:\/([0-9a-f]+))?$/, show,
  leave: () => { clearTimeout(L.timer); clearTimeout(L.lineTimer); },
  status: onStatus,
  keys: { w: () => { location.hash = "#/leakcheck"; } },
  help: [["w", "the leak check: a private project's names, addresses and words looked for in a tree (dread leakcheck as a job), each hit with its file and lines"]],
  nav: { title: "Leak check (w)", href: "#/leakcheck", icon: ICON },
});
