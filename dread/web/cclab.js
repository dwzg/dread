// dread gui's compiler lab page (the server's dread/guipages/cclab.py): what dread cclab found out about a
// compiler by running its probes in the emulator, read from its report's own data (dread cclab report
// --json): the compilers measured in a work directory, a compiler's profile (the calling convention, types,
// what each operation calls or does inline, what a call keeps, where the functions lie, its runtime helpers,
// what DREAD lifts), the probes and builds that failed, and dread cclab's run, doctor and report as jobs.
// The page computes nothing: results, builds and the command lines come from the server.
// Addresses: #/cclab (the work directory, its compilers, a run), #/cclab/ADAPTER (a compiler's profile),
// #/cclab/compare/ORIGINAL/REBUILD (two compilers side by side: dread cclab compare --json, made as a job).

import { $, esc, api, ago, when, toast, S, prefs, register, enter, nextRender, isCurrent } from "./app.js";

const POLL = 1500;              // ms between looks while a cclab job is queued or running
const LIVE = new Set(["queued", "running", "stopping"]);
const ICON = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2.5h4"/><path d="M6.8 2.5v4L3.2 12.6a.9.9 0 0 0 .8 1.4h8a.9.9 0 0 0 .8-1.4L9.2 6.5v-4"/><path d="M4.8 10h6.4"/></svg>`;
const KINDS = { arg: "the calling convention: where each argument arrives", result: "where each result comes back",
  value: "sizes, alignments, signs and bit fields", op: "a runtime helper, or inline code and the instructions that mark its idiom",
  keeps: "what a function keeps across a call, what its frame saves, its frame at calls" };

const C = { work: null, data: null, adapter: null, record: null, filter: "", differ: false, timer: 0, started: new Set(),
  compare: null, compareData: null, cmp: { original: "", rebuild: "" },
  pick: { adapter: "gcc-arm", other: "", builds: null, own: "", lift: true, guard: false }, lineSeq: 0, lineTimer: 0, line: null, live: null };

// ---- small helpers ----

function count(n) { return n == null ? "–" : Number(n).toLocaleString("en-US"); }
function plural(n, one, many) { return `${count(n)} ${n === 1 ? one : many || one + "s"}`; }
function jobLine(job, what) {
  if (!job) return "";
  const words = job.state === "failed" ? `failed${job.exit != null ? " (exit status " + job.exit + ")" : ""}` : job.state;
  return `<a href="#/jobs/${job.id}" title="${esc(job.line || "")}">job ${job.id}</a> ${esc(what || "")} ${esc(words)}`;
}
function disabledWhy(why) { return why ? ` disabled title="${esc(why)}"` : ""; }
// A result's builds in a few words: "all 16", or by the values of one dimension of the matrix whose builds
// they are exactly ("cpu cortex-m4f (4)"), or all but such ("all but cpu cortex-m4f (12)"), whichever names
// fewer values; else the list.
// The full list is the title (and the report's). C.picked: {build: {dimension: value}} of the profile shown.
function buildsText(builds, all) {
  if (builds.length === all.length) return `all ${all.length}`;
  const picked = C.picked || {};
  const groups = (set) => {
    const dims = [...new Set(all.flatMap((b) => Object.keys(picked[b] || {})))];
    let best = null;
    for (const dim of dims) {
      const values = [...new Set(all.map((b) => (picked[b] || {})[dim]))];
      const inside = values.filter((v) => all.filter((b) => (picked[b] || {})[dim] === v).every((b) => set.has(b)));
      const covered = all.filter((b) => inside.includes((picked[b] || {})[dim]));
      if (inside.length && covered.length === set.size && (!best || inside.length < best.values.length)) best = { dim, values: inside };
    }
    return best;
  };
  const set = new Set(builds);
  const exact = builds.length > 1 ? groups(set) : null;
  const rest = new Set(all.filter((b) => !set.has(b)));
  const but = builds.length > 2 && rest.size ? groups(rest) : null;
  if (but && (!exact || but.values.length < exact.values.length)) return `all but ${but.dim} ${but.values.join(", ")} (${builds.length})`;
  if (exact) return `${exact.dim} ${exact.values.join(", ")} (${builds.length})`;
  return builds.join(", ");
}
function buildsHTML(builds, all) {
  const text = buildsText(builds, all);
  return text === builds.join(", ") ? esc(text) : `<span title="${esc(builds.join(", "))}">${esc(text)}</span>`;
}
function workParam() { return { work: C.work || "" }; }

// ---- the work directory ----

function reportState(d) {
  const live = d.job && LIVE.has(d.job.state);
  if (live) return `<span class="lab-state busy">${jobLine(d.job, "makes the report:")}</span>`;
  if (!d.when) return `<span class="lab-state">no report yet${d.job ? " · " + jobLine(d.job, "") : ""}</span>`;
  const parts = [`report of ${esc(ago(d.when))}`];
  if (d.stale_count) parts.push(`<span class="warn" title="${esc((d.stale || []).join("\n"))}">${plural(d.stale_count, "newer file")}</span>`);
  if (d.job && d.job.state === "failed") parts.push(`<span class="bad">${jobLine(d.job, "")}</span>`);
  return `<span class="lab-state">${parts.join(" · ")}</span>`;
}

function liveHTML(d) {
  const live = (d.live || []).filter((j) => !(d.job && j.id === d.job.id));
  return live.length ? `<div class="lab-live">${live.map((j) => `<div>${jobLine(j, "")} · <span class="mono">${esc(j.line || "")}</span></div>`).join("")}</div>` : "";
}

function adaptersHTML(d) {
  if (!d.exists) return `<div class="lab-none">No such directory yet: a run below makes it.</div>`;
  if (!d.adapters.length) return `<div class="lab-none">Nothing measured here yet: run a compiler's probes below.</div>`;
  return `<div class="lab-adapters">${d.adapters.map((a) => `<a class="lab-adapter" href="#/cclab/${encodeURIComponent(a.name)}">
      <span class="lab-a-name">${esc(a.name)}</span>
      <span class="lab-a-version">${esc(a.version || "version not recorded")}</span>
      <span class="lab-a-counts">${plural(a.measured, "build")} measured${a.failed ? ` · <span class="bad">${count(a.failed)} failed</span>` : ""}${a.reported ? "" : ` · <span class="warn">not in the report yet</span>`}</span></a>`).join("")}</div>`;
}

function compareHref(a, b) { return `#/cclab/compare/${encodeURIComponent(a)}/${encodeURIComponent(b)}`; }

// Two compilers measured here, side by side: the one that made the image and the one that rebuilds its
// functions (dread cclab compare), and the comparisons made before.
function compareCardHTML(d) {
  const names = (d.adapters || []).filter((a) => a.measured).map((a) => a.name);
  if (names.length < 2) return "";
  if (!names.includes(C.cmp.original)) C.cmp.original = names[0];
  if (!names.includes(C.cmp.rebuild) || C.cmp.rebuild === C.cmp.original) C.cmp.rebuild = names.find((n) => n !== C.cmp.original);
  const opts = (chosen) => names.map((n) => `<option value="${esc(n)}"${n === chosen ? " selected" : ""}>${esc(n)}</option>`).join("");
  const made = (d.compares || []).map((c) => `<a class="lab-chip" href="${compareHref(c.original, c.rebuild)}">${esc(c.original)} against ${esc(c.rebuild)} <span class="lab-muted">${esc(ago(c.when))}</span></a>`).join("");
  return `<section class="lab-card" id="lab-compare"><h2>Compare two compilers</h2>
    <p class="lab-note">Where the compiler that made an image and the one that rebuilds its functions differ: in what linking depends on (the calling convention, the types, what a call keeps), with the options that close a difference, and in their code.</p>
    <div class="lab-row"><label class="lab-k" for="lab-cmp-a">Made the image</label><div class="lab-v"><select id="lab-cmp-a">${opts(C.cmp.original)}</select></div></div>
    <div class="lab-row"><label class="lab-k" for="lab-cmp-b">Rebuilds</label><div class="lab-v"><select id="lab-cmp-b">${opts(C.cmp.rebuild)}</select></div></div>
    <div class="lab-actions">${made ? `<span class="lab-made">${made}</span>` : ""}<a class="button lab-primary" id="lab-cmp-go" href="${compareHref(C.cmp.original, C.cmp.rebuild)}">Compare</a></div></section>`;
}

function ownDirs() { return C.pick.own.split("\n").map((s) => s.trim()).filter(Boolean); }

function chosenAdapter() {
  return C.pick.adapter === "other" ? C.pick.other.trim() : C.pick.adapter;
}

function matrixHTML(brief) {
  if (!brief) return "";
  const dims = Object.entries(brief.matrix || {});
  const chosen = C.pick.builds || new Set(brief.builds);
  if (dims.length === 2) {          // (a grid: rows by the first dimension, columns by the second)
    const [rows, cols] = dims;
    return `<table class="lab-matrix"><thead><tr><th></th>${cols[1].map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>
      ${rows[1].map((r) => `<tr><th>${esc(r)}</th>${cols[1].map((c) => {
        const key = brief.builds.find((k) => k === `${r}-${c}`) || brief.builds.find((k) => k.startsWith(r + "-") && k.endsWith("-" + c));
        return key ? `<td><label title="${esc(key)}"><input type="checkbox" data-build="${esc(key)}"${chosen.has(key) ? " checked" : ""} aria-label="${esc(key)}"></label></td>` : "<td></td>";
      }).join("")}</tr>`).join("")}</tbody></table>`;
  }
  return `<div class="lab-builds">${brief.builds.map((k) => `<label><input type="checkbox" data-build="${esc(k)}"${chosen.has(k) ? " checked" : ""}> ${esc(k)}</label>`).join("")}</div>`;
}

function runHTML(d) {
  const why = d.changes;
  const opts = d.builtin.map((a) => `<option value="${esc(a.name)}"${a.name === C.pick.adapter ? " selected" : ""}>${esc(a.name)} (${esc(a.arch)}, ${a.builds.length} builds)</option>`).join("");
  return `<section class="lab-card" id="lab-run"><h2>Measure a compiler</h2>
    <p class="lab-note">dread cclab run builds the probes for each build of the adapter's matrix, runs them in the emulator and records what they show; the report then compares the builds. A built-in adapter, a compiler of Compiler Explorer (<span class="mono">ce:ID</span>), a bundle's objects (<span class="mono">relink:DIR</span>) or an adapter file of your own.</p>
    <div class="lab-row"><label class="lab-k" for="lab-adapter">Adapter</label><div class="lab-v">
      <select id="lab-adapter">${opts}<option value="other"${C.pick.adapter === "other" ? " selected" : ""}>other…</option></select>
      <input type="text" id="lab-other" placeholder="ce:ID, relink:DIR or a TOML file" value="${esc(C.pick.other)}" spellcheck="false" autocomplete="off"${C.pick.adapter === "other" ? "" : " hidden"}></div></div>
    <div class="lab-row"><span class="lab-k">Builds</span><div class="lab-v lab-v-col"><div id="lab-matrix"></div>
      <div class="lab-quick"><button type="button" class="lab-link" id="lab-all">all</button> <button type="button" class="lab-link" id="lab-none">none</button></div></div></div>
    <div class="lab-row"><label class="lab-k" for="lab-own">Own probes</label><div class="lab-v lab-v-col"><textarea id="lab-own" rows="1" placeholder="optional: directories of one's own probes, one a line (--probe-dir)" spellcheck="false">${esc(C.pick.own)}</textarea></div></div>
    <div class="lab-row"><span class="lab-k">Also</span><div class="lab-v">
      <label class="lab-check"><input type="checkbox" id="lab-lift"${C.pick.lift ? " checked" : ""}> <span class="mono">--lift</span>: then dread lift --check on each build's probe functions</label>
      <label class="lab-check"><input type="checkbox" id="lab-guard"${C.pick.guard ? " checked" : ""}> <span class="mono">--guard</span>: guard the work directory as for a proprietary toolchain</label></div></div>
    <pre class="lab-cmd" id="lab-line"></pre><div class="lab-error" id="lab-error"></div>
    <div class="lab-actions"><a class="lab-link" id="lab-every" href="#/run/cclab">Every option</a>
      <button type="button" id="lab-doctor"${disabledWhy(why)} title="dread cclab doctor: its tools, its version, one probe built and run">Check the adapter</button>
      <button type="button" class="primary" id="lab-go"${disabledWhy(why)}>Run</button></div></section>`;
}

function renderWork() {
  const d = C.data, main = $("#main");
  let html = `<div class="lab"><div class="lab-head"><h1>Compiler lab</h1>
    <label class="lab-work"><span class="lab-muted">work directory</span><input type="text" id="lab-work" value="${esc(C.work || "")}" spellcheck="false" autocomplete="off" aria-label="Work directory" placeholder="cclab-work"></label></div>
    <div class="lab-sub"><span class="mono" title="typed paths start in ${esc(d.home)}">${esc(d.work)}</span> · ${reportState(d)}
      <button type="button" id="lab-report"${disabledWhy(d.changes || (!d.adapters.length ? "nothing measured here yet" : ""))}>${d.when ? "Make the report again" : "Make the report"}</button></div>`;
  html += liveHTML(d);
  html += `<section class="lab-card"><h2>Compilers measured here</h2>${adaptersHTML(d)}</section>`;
  html += compareCardHTML(d);
  html += runHTML(d) + `</div>`;
  main.innerHTML = html;
  askLine(true);
}

// ---- the run's command line (the server's) ----

function scheduleLine() {
  clearTimeout(C.lineTimer);
  C.lineTimer = setTimeout(() => askLine(false), 200);
}

async function askLine(redraw) {
  const mine = ++C.lineSeq;
  const adapter = chosenAdapter();
  const params = Object.assign(workParam(), { adapter, builds: JSON.stringify(C.pick.builds ? [...C.pick.builds] : []),
    probe_dirs: JSON.stringify(ownDirs()), lift: C.pick.lift ? "1" : "", guard: C.pick.guard ? "1" : "" });
  let r;
  try {
    r = await api("/api/cclab/line", params);
  } catch (e) {
    r = { error: e.message };
  }
  if (mine !== C.lineSeq || !$("#lab-line")) return;
  if (r.adapter && (redraw || !C.line || !C.line.adapter || C.line.adapter.name !== r.adapter.name)) {
    if (C.pick.builds) C.pick.builds = new Set([...C.pick.builds].filter((k) => r.adapter.builds.includes(k)));
    $("#lab-matrix").innerHTML = matrixHTML(r.adapter);
  } else if (!r.adapter) $("#lab-matrix").innerHTML = "";
  if (!r.error && C.pick.builds && !C.pick.builds.size) r = Object.assign({}, r, { line: null, args: null, error: "Choose at least one build (all: every build of the matrix)." });
  C.line = r;
  $("#lab-line").textContent = r.line || "";
  $("#lab-error").textContent = r.error || "";
  const blocked = !!(C.data && C.data.changes);
  for (const id of ["#lab-go", "#lab-doctor"]) { const b = $(id); if (b && !blocked) b.disabled = !!r.error; }
  const every = $("#lab-every");
  if (every && r.args) every.href = "#/run/cclab@" + encodeURIComponent(JSON.stringify(r.args));
}

// ---- a compiler's profile ----

function differs(row) { return (row.results || []).length > 1; }
function rowMatches(row, text) {
  if (!text) return true;
  return [row.probe, row.what, row.helper, ...(row.results || []).map((r) => r.text || r.error)].some((s) => (s || "").toLowerCase().includes(text));
}

function resultsHTML(results, all) {
  return results.map((r) => `<div class="lab-res"><span class="lab-res-t">${esc(r.text != null ? r.text : r.error)}</span> <span class="lab-builds-of">${buildsHTML(r.builds, all)}</span></div>`).join("");
}

function sectionHTML(s, all) {
  const text = C.filter.trim().toLowerCase();
  const rows = (s.rows || []).filter((r) => rowMatches(r, text) && (!C.differ || differs(r)));
  if (!rows.length && (text || C.differ)) return "";
  const n = (s.rows || []).filter(differs).length;
  return `<section class="lab-card" id="lab-s-${esc(s.kind)}"><div class="lab-card-head"><h2>${esc(s.title)}</h2><span class="lab-muted">${esc(KINDS[s.kind] || "")}</span><span class="lab-grow"></span><span class="lab-muted">${plural((s.rows || []).length, "probe")}${n ? ` · ${count(n)} differ between builds` : ""}</span></div>
    <div class="lab-scroll"><table class="lab-table"><thead><tr><th>probe</th><th>what it asks</th><th>result (builds)</th></tr></thead><tbody>
    ${rows.map((r) => `<tr class="${differs(r) ? "differ" : ""}"><td class="mono lab-probe">${esc(r.probe)}</td><td>${esc(r.what || "")}</td><td>${resultsHTML(r.results, all)}</td></tr>`).join("")}
    </tbody></table></div>${s.aligned ? `<p class="lab-note lab-after">Every frame at a call is a multiple of <b>${count(s.aligned)}</b> bytes over all builds: the stack alignment the compiler keeps at calls (given an aligned entry).</p>` : ""}</section>`;
}

function helpersHTML(rec, all) {
  const helpers = rec.helpers || [];
  const prof = (rec.profile && rec.profile.helpers) || {};
  const byName = {};
  for (const [key, info] of Object.entries(prof)) for (const n of info.names || [key]) byName[n] = Object.assign({ key }, info);
  const text = C.filter.trim().toLowerCase();
  const rows = helpers.filter((h) => !text || [h.helper, ...(h.called_by || [])].some((s) => s.toLowerCase().includes(text)));
  if (!helpers.length || (!rows.length && text)) return "";
  return `<section class="lab-card" id="lab-s-helpers"><div class="lab-card-head"><h2>Runtime helpers the probes call</h2><span class="lab-muted">registers changed over every call in every build (results included); the results the compiler's own code reads after a call (the profile, dread.regflow on each build's image)</span></div>
    <div class="lab-scroll"><table class="lab-table"><thead><tr><th>helper (its names)</th><th>called by</th><th>results read</th><th>registers changed</th><th>builds</th></tr></thead><tbody>
    ${rows.map((h) => {
      const names = h.helper.split(" = ");
      const p = names.map((n) => byName[n]).find(Boolean);
      return `<tr><td class="mono lab-probe">${names.map(esc).join("<br>")}</td><td class="mono">${(h.called_by || []).map(esc).join(", ")}</td><td class="mono">${p ? esc((p.results || []).join(" ") || "–") : `<span class="lab-muted">–</span>`}</td><td class="mono">${esc((h.changed || []).join(" ") || "–")}</td><td class="lab-muted">${buildsHTML(h.builds, all)}</td></tr>`;
    }).join("")}</tbody></table></div></section>`;
}

function layoutHTML(rec, all) {
  if (!(rec.layout || []).length || C.filter.trim() || C.differ && !rec.layout.some(differs)) return "";
  const rows = C.differ ? rec.layout.filter(differs) : rec.layout;
  return `<section class="lab-card" id="lab-s-layout"><div class="lab-card-head"><h2>Where the functions lie</h2><span class="lab-muted">what tells a function's start in a stripped image</span></div>
    <div class="lab-scroll"><table class="lab-table"><thead><tr><th>what</th><th>result (builds)</th></tr></thead><tbody>
    ${rows.map((r) => `<tr class="${differs(r) ? "differ" : ""}"><td>${esc(r.what)}</td><td>${resultsHTML(r.results, all)}</td></tr>`).join("")}</tbody></table></div></section>`;
}

function liftHTML(rec) {
  const lift = rec.lift;
  if (!lift) return "";
  const lifted = Object.keys(lift.builds || {});
  const text = C.filter.trim().toLowerCase();
  const rows = (lift.rows || []).filter((r) => rowMatches(r, text));
  return `<section class="lab-card" id="lab-s-lift"><div class="lab-card-head"><h2>What DREAD lifts</h2><span class="lab-muted">dread lift --check on each build's probe functions (run --lift)</span></div>
    <div class="lab-lift-builds">${Object.entries(lift.builds || {}).map(([k, v]) => `<span class="lab-chip"><b>${esc(k)}</b> ${count(v.accepted)} of ${count(v.checked)}</span>`).join("")}
    ${Object.entries(lift.errors || {}).map(([k, e]) => `<span class="lab-chip bad" title="${esc(e)}"><b>${esc(k)}</b> not checked</span>`).join("")}</div>
    ${rows.length ? `<div class="lab-scroll"><table class="lab-table"><thead><tr><th>probe</th><th>not lifted (builds)</th></tr></thead><tbody>
      ${rows.map((r) => `<tr><td class="mono lab-probe">${esc(r.probe)}</td><td>${resultsHTML(r.results, lifted)}</td></tr>`).join("")}</tbody></table></div>` : `<p class="lab-note">Every probe function lifted in every build checked.</p>`}</section>`;
}

function failedHTML(rec, all) {
  const probes = rec.failed_probes || [];
  const builds = Object.entries(rec.failed || {});
  if (!probes.length && !builds.length) return `<div class="lab-ok">No probe and no build failed.</div>`;
  let html = `<section class="lab-card lab-failed" id="lab-s-failed"><div class="lab-card-head"><h2>What failed</h2></div>`;
  if (builds.length) html += `<h3>Builds (${count(builds.length)})</h3><ul class="lab-list">${builds.map(([k, e]) => `<li><b class="mono">${esc(k)}</b>: ${esc(e)}</li>`).join("")}</ul>`;
  if (probes.length) html += `<h3>Probes whose measurement failed (${count(probes.length)})</h3><div class="lab-scroll"><table class="lab-table"><thead><tr><th>probe</th><th>what it asks</th><th>error (builds)</th></tr></thead><tbody>
    ${probes.map((p) => `<tr><td class="mono lab-probe">${esc(p.probe)}</td><td>${esc(p.what || "")} <span class="lab-muted">(${esc(p.kind)})</span></td><td>${resultsHTML(p.results, all)}</td></tr>`).join("")}</tbody></table></div>`;
  return html + `</section>`;
}

function renderAdapter() {
  const a = C.adapterData, rec = a.record, main = $("#main");
  C.picked = rec ? Object.fromEntries((rec.builds || []).map((b) => [b.build, b.picked || {}])) : {};
  const back = `<a class="lab-back" href="#/cclab">← compiler lab</a>`;
  let html = `<div class="lab lab-profile"><div class="lab-head">${back}<h1>${esc(a.adapter)}</h1>${rec ? `<span class="lab-muted">${esc(rec.arch || "")}</span>` : ""}</div>`;
  if (!rec) {
    html += `<div class="lab-sub">${reportState(a)}</div><div class="lab-none">${a.changes ? esc(a.changes) : "Not in the report yet: the report is being made (it reads each build's results)."}</div></div>`;
    main.innerHTML = html;
    return;
  }
  const all = rec.measured || [];
  html += `<div class="lab-sub"><span class="mono">${esc(rec.compiler || "version not recorded")}</span> · ${reportState(a)}</div>`;
  html += `<div class="lab-builds-row">${(rec.builds || []).map((b) => `<span class="lab-chip${b.measured ? "" : " bad"}" title="${esc((b.flags || []).join(" ") + (b.error ? "\n" + b.error : ""))}">${esc(b.build)}</span>`).join("")}</div>`;
  html += failedHTML(rec, all);
  html += `<div class="lab-filters"><input type="search" id="lab-filter" placeholder="Filter: probe, result, helper" value="${esc(C.filter)}" spellcheck="false" autocomplete="off" aria-label="Filter the probes">
    <label class="lab-check"><input type="checkbox" id="lab-differ"${C.differ ? " checked" : ""}> only where the builds differ</label>
    <span class="lab-muted lab-jump">${(rec.sections || []).filter((s) => s.rows && s.rows.length).map((s) => `<a href="#lab-s-${esc(s.kind)}" data-jump="lab-s-${esc(s.kind)}">${esc(s.title.split(":")[0])}</a>`).join(" · ")}</span></div>`;
  html += `<div id="lab-sections">${sectionsHTML(rec, all)}</div></div>`;
  main.innerHTML = html;
}

function sectionsHTML(rec, all) {
  const parts = (rec.sections || []).map((s) => sectionHTML(s, all));
  parts.push(layoutHTML(rec, all), helpersHTML(rec, all), liftHTML(rec));
  const html = parts.join("");
  return html || `<div class="lab-none">Nothing matches.</div>`;
}

// ---- two compilers side by side ----

function cmpCell(cell) {
  if (cell == null) return `<span class="lab-muted">not measured</span>`;
  return cell.map((r) => `<div class="lab-res"><span class="lab-res-t">${esc(r.text)}</span> <span class="lab-builds-of" title="${esc(r.builds.join(", "))}">${esc(r.shown)}</span></div>`).join("");
}

// (data-k: the compiler's name, which a phone's stacked rows show in front of its results)
function cmpRows(rec, rows) {
  return rows.map((r) => `<tr><td class="mono lab-probe">${esc(r.probe)}</td><td>${esc(r.note || "")}</td><td data-k="${esc(rec.original)}">${cmpCell(r.original)}</td><td data-k="${esc(rec.rebuild)}">${cmpCell(r.rebuild)}</td></tr>`).join("");
}

function cmpTable(rec, rows, extra = "") {
  return `<div class="lab-scroll"><table class="lab-table lab-cmp"><thead><tr><th>probe</th><th>what it asks</th><th>${esc(rec.original)}</th><th>${esc(rec.rebuild)}</th></tr></thead>
    <tbody>${cmpRows(rec, rows)}${extra}</tbody></table></div>`;
}

function compareState(c) {
  const live = c.job && LIVE.has(c.job.state);
  if (live) return `<span class="lab-state busy">${jobLine(c.job, "compares them:")}</span>`;
  if (!c.when) return `<span class="lab-state">not compared yet${c.job ? " · " + jobLine(c.job, "") : ""}</span>`;
  const parts = [`compared ${esc(ago(c.when))}`];
  if (c.stale_count) parts.push(`<span class="warn" title="${esc((c.stale || []).join("\n"))}">${plural(c.stale_count, "newer result")}</span>`);
  if (c.job && c.job.state === "failed") parts.push(`<span class="bad">${jobLine(c.job, "")}</span>`);
  return `<span class="lab-state">${parts.join(" · ")}</span>`;
}

function renderCompare() {
  const c = C.compareData, rec = c.record, main = $("#main");
  const busy = c.job && LIVE.has(c.job.state);
  let html = `<div class="lab lab-compare"><div class="lab-head"><a class="lab-back" href="#/cclab">← compiler lab</a><h1>${esc(c.original)} against ${esc(c.rebuild)}</h1></div>
    <div class="lab-sub">${compareState(c)} <button type="button" id="lab-compare-again"${disabledWhy(c.changes || (busy ? "a comparison is being made" : ""))}>${c.when ? "Compare again" : "Compare"}</button></div>`;
  if (!rec) {
    html += `<div class="lab-none">${c.changes ? esc(c.changes) : "The comparison is being made: it reads the two compilers' results."}</div></div>`;
    main.innerHTML = html;
    return;
  }
  html += `<p class="lab-note"><b>${esc(rec.original)}</b> made the image (${plural(rec.builds.original.length, "build")}, ${esc(rec.arch.original)}); <b>${esc(rec.rebuild)}</b> rebuilds its functions (${plural(rec.builds.rebuild.length, "build")}, ${esc(rec.arch.rebuild)}). A probe is listed where the two differ, each result with the builds that gave it.</p>`;
  if (rec.arch.original !== rec.arch.rebuild) html += `<div class="lab-none bad">Different architectures: their code cannot be linked together.</div>`;
  const al = rec.alignment || {};
  const alignRow = al.original !== al.rebuild ? `<tr><td class="lab-probe">stack alignment at calls</td><td>the largest power of two dividing every frame at a call</td><td data-k="${esc(rec.original)}">${esc(String(al.original ?? "–"))}</td><td data-k="${esc(rec.rebuild)}">${esc(String(al.rebuild ?? "–"))}</td></tr>` : "";
  const abi = rec.abi.length || alignRow;
  html += `<section class="lab-card" id="lab-c-abi"><div class="lab-card-head"><h2>What linking depends on</h2><span class="lab-muted">the calling convention, the types, what a call keeps</span><span class="lab-grow"></span><span class="lab-muted">${plural(rec.abi.length + (alignRow ? 1 : 0), "difference")} · ${plural(rec.same, "probe")} agree</span></div>
    ${abi ? cmpTable(rec, rec.abi, alignRow) : `<div class="lab-ok">They agree on every probe.</div>`}</section>`;
  if (rec.hints.length) {
    html += `<section class="lab-card" id="lab-c-hints"><h2>Options of ${esc(rec.rebuild)} that close a difference</h2>
      <ul class="lab-list">${rec.hints.map((h) => `<li><span class="mono">${esc(h.option)}</span>: ${h.probes.map((f) => `<span class="mono">${esc(f)}</span>`).join(", ")}</li>`).join("")}</ul></section>`;
  }
  if (rec.code.length) {
    html += `<section class="lab-card" id="lab-c-code"><div class="lab-card-head"><h2>Where their code differs</h2><span class="lab-muted">helpers, inline code, saves and frames: they matter to a reader, not to linking</span></div>
      ${cmpTable(rec, rec.code)}</section>`;
  }
  if (rec.lift) {
    const [aa, an, ab] = rec.lift.original, [ba, bn, bb] = rec.lift.rebuild;
    html += `<section class="lab-card" id="lab-c-lift"><div class="lab-card-head"><h2>What DREAD lifts</h2><span class="lab-muted">run --lift's verdicts</span></div>
      <p class="lab-note">${esc(rec.original)}: ${count(aa)} of ${count(an)} probe functions over ${plural(ab, "build")}; ${esc(rec.rebuild)}: ${count(ba)} of ${count(bn)} over ${plural(bb, "build")}.</p>
      ${rec.lift.rows.length ? `<div class="lab-scroll"><table class="lab-table lab-cmp"><thead><tr><th>probe</th><th>${esc(rec.original)}</th><th>${esc(rec.rebuild)}</th></tr></thead><tbody>
        ${rec.lift.rows.map((r) => `<tr><td class="mono lab-probe">${esc(r.probe)}</td><td data-k="${esc(rec.original)}">${r.original.length ? cmpCell(r.original) : "–"}</td><td data-k="${esc(rec.rebuild)}">${r.rebuild.length ? cmpCell(r.rebuild) : "–"}</td></tr>`).join("")}</tbody></table></div>` : ""}</section>`;
  }
  html += `<p class="lab-note lab-after">The same as Markdown: <span class="mono">${esc(rec.markdown || "")}</span></p></div>`;
  main.innerHTML = html;
}

async function maybeCompare(c) {
  const key = "compare:" + c.path + "@" + (c.inputs || 0);
  if (!c.auto || C.started.has(key)) return false;
  C.started.add(key);
  try {
    await api("/api/cclab/compare", {}, Object.assign(workParam(), { original: c.original, rebuild: c.rebuild }));
    return true;
  } catch (e) {
    toast("The comparison did not start: " + e.message, true);
    return false;
  }
}

async function showCompare(match, keep = false) {
  const mine = nextRender();
  clearTimeout(C.timer);
  if (C.work === null) C.work = prefs.get("cclab-work", "cclab-work");
  const [original, rebuild] = [match[1], match[2]];
  C.adapter = null;
  C.compare = [original, rebuild];
  let c;
  try {
    c = await api("/api/cclab/compare", Object.assign(workParam(), { original, rebuild }));
  } catch (e) {
    if (!isCurrent(mine)) return;
    enter("cclab", "Compiler lab");
    $("#main").innerHTML = `<div class="lab"><div class="lab-head"><a class="lab-back" href="#/cclab">← compiler lab</a><h1>${esc(original)} against ${esc(rebuild)}</h1></div><div class="lab-none bad">${esc(e.message)}</div></div>`;
    return;
  }
  if (!isCurrent(mine)) return;
  C.compareData = c;
  C.cmp = { original, rebuild };
  const scroll = $("#main").scrollTop;
  enter("cclab", `${original} against ${rebuild} · Compiler lab`);
  renderCompare();
  $("#main").scrollTop = keep ? scroll : 0;
  if (await maybeCompare(c)) return refresh(true);
  schedule(c);
}

async function compareAgain() {
  const c = C.compareData;
  if (!c) return;
  try {
    const r = await api("/api/cclab/compare", {}, Object.assign(workParam(), { original: c.original, rebuild: c.rebuild }));
    toast(`Job ${r.id}: the comparison.`);
  } catch (e) {
    toast(e.message, true);
  }
  refresh(true);
}

// ---- loading, polling ----

function schedule(d) {
  clearTimeout(C.timer);
  const busy = d && ((d.job && LIVE.has(d.job.state)) || (d.live && d.live.length));
  if (busy) C.timer = setTimeout(() => refresh(true), POLL);
}

async function refresh(keep) {
  if (C.compare) return showCompare([null, ...C.compare], keep);
  if (C.adapter) return showAdapter([null, C.adapter], keep);
  return showWork([null], keep);
}

async function maybeStart(d) {
  const key = d.work + "@" + (d.inputs || 0);        // (once for each state of the files it reads)
  if (!d.auto || C.started.has(key)) return false;
  C.started.add(key);
  try {
    await api("/api/cclab/report", {}, { work: d.work });
    return true;
  } catch (e) {
    toast("The report did not start: " + e.message, true);
    return false;
  }
}

async function showWork(match, keep = false) {
  const mine = nextRender();
  clearTimeout(C.timer);
  C.adapter = null;
  C.compare = null;
  if (C.work === null) C.work = prefs.get("cclab-work", "cclab-work");
  let d;
  try {
    d = await api("/api/cclab", workParam());
  } catch (e) {
    if (!isCurrent(mine)) return;
    enter("cclab", "Compiler lab");
    $("#main").innerHTML = `<div class="lab"><div class="lab-head"><h1>Compiler lab</h1></div><div class="lab-none bad">${esc(e.message)}</div></div>`;
    return;
  }
  if (!isCurrent(mine)) return;
  C.data = d;
  if (!d.builtin.some((a) => a.name === C.pick.adapter) && C.pick.adapter !== "other") C.pick.adapter = (d.builtin[0] || {}).name || "other";
  const scroll = $("#main").scrollTop;
  const typing = keep && document.activeElement && document.activeElement.id;
  enter("cclab", "Compiler lab");
  if (keep && $("#lab-run")) {           // (a reload while the form is open: only what changed)
    const sub = $(".lab-sub"), list = $(".lab-adapters, .lab-card .lab-none");
    if (sub) sub.outerHTML = `<div class="lab-sub"><span class="mono" title="typed paths start in ${esc(d.home)}">${esc(d.work)}</span> · ${reportState(d)}
      <button type="button" id="lab-report"${disabledWhy(d.changes || (!d.adapters.length ? "nothing measured here yet" : ""))}>${d.when ? "Make the report again" : "Make the report"}</button></div>`;
    const card = list && list.closest(".lab-card");
    if (card) card.innerHTML = `<h2>Compilers measured here</h2>${adaptersHTML(d)}`;
    const oldLive = $(".lab-live");
    if (oldLive) oldLive.remove();
    const live = liveHTML(d);
    if (live) $(".lab-sub").insertAdjacentHTML("afterend", live);
  } else {
    renderWork();
    $("#main").scrollTop = keep ? scroll : 0;
  }
  if (typing === "lab-work") { const w = $("#lab-work"); if (w) w.focus(); }
  if (await maybeStart(d)) return refresh(true);
  schedule(d);
}

async function showAdapter(match, keep = false) {
  const mine = nextRender();
  clearTimeout(C.timer);
  if (C.work === null) C.work = prefs.get("cclab-work", "cclab-work");
  const name = match[1];                // (the router has decoded the address)
  if (C.adapter !== name) { C.filter = ""; }
  C.adapter = name;
  C.compare = null;
  let a, d;
  try {
    [a, d] = await Promise.all([api("/api/cclab/adapter", Object.assign(workParam(), { adapter: name })), api("/api/cclab", workParam())]);
  } catch (e) {
    if (!isCurrent(mine)) return;
    enter("cclab", "Compiler lab");
    $("#main").innerHTML = `<div class="lab"><div class="lab-head"><a class="lab-back" href="#/cclab">← compiler lab</a><h1>${esc(name)}</h1></div><div class="lab-none bad">${esc(e.message)}</div></div>`;
    return;
  }
  if (!isCurrent(mine)) return;
  C.adapterData = a;
  C.data = d;
  const scroll = $("#main").scrollTop;
  enter("cclab", name + " · Compiler lab");
  renderAdapter();
  $("#main").scrollTop = keep ? scroll : 0;
  if (await maybeStart(d)) return refresh(true);
  schedule(d);
}

function onStatus(s) {
  if (!s) return;
  const live = s.live || [];
  const badge = document.querySelector('.nav-btn[data-page="cclab"] .nav-badge');
  if (badge) {
    badge.hidden = !live.length;
    badge.textContent = live.length;
    badge.parentElement.title = "Compiler lab (i)" + (live.length ? ` · ${live.length} job${live.length === 1 ? "" : "s"} of dread cclab` : "");
  }
  const key = live.join(",");
  if (C.live !== null && key !== C.live && S.view === "cclab") refresh(true);
  C.live = key;
}

async function start(action) {
  const adapter = chosenAdapter();
  try {
    const r = await api("/api/cclab/run", {}, Object.assign(workParam(), { action, adapter, builds: C.pick.builds ? [...C.pick.builds] : [],
      probe_dirs: ownDirs(), lift: C.pick.lift, guard: C.pick.guard }));
    toast(`Job ${r.id}: dread cclab ${action} ${adapter}.`);
  } catch (e) {
    toast(e.message, true);
  }
  refresh(true);
}

async function startReport() {
  try {
    const r = await api("/api/cclab/report", {}, workParam());
    toast(`Job ${r.id}: the report.`);
  } catch (e) {
    toast(e.message, true);
  }
  refresh(true);
}

function wire() {
  const main = $("#main");
  main.addEventListener("input", (e) => {
    if (S.view !== "cclab") return;
    const el = e.target;
    if (el.id === "lab-filter") { C.filter = el.value; const box = $("#lab-sections"); if (box && C.adapterData && C.adapterData.record) box.innerHTML = sectionsHTML(C.adapterData.record, C.adapterData.record.measured || []); return; }
    if (el.id === "lab-other") { C.pick.other = el.value; scheduleLine(); return; }
    if (el.id === "lab-own") { C.pick.own = el.value; scheduleLine(); }
  });
  main.addEventListener("change", (e) => {
    if (S.view !== "cclab") return;
    const el = e.target;
    if (el.id === "lab-work") { C.work = el.value.trim(); prefs.set("cclab-work", C.work); refresh(false); return; }
    if (el.id === "lab-adapter") {
      C.pick.adapter = el.value;
      C.pick.builds = null;
      const other = $("#lab-other");
      if (other) { other.hidden = el.value !== "other"; if (!other.hidden) other.focus(); }
      askLine(true);
      return;
    }
    if (el.dataset.build) {
      const brief = C.line && C.line.adapter;
      if (!brief) return;
      C.pick.builds = new Set([...document.querySelectorAll("#lab-matrix input[data-build]")].filter((x) => x.checked).map((x) => x.dataset.build));
      scheduleLine();
      return;
    }
    if (el.id === "lab-cmp-a" || el.id === "lab-cmp-b") {
      C.cmp[el.id === "lab-cmp-a" ? "original" : "rebuild"] = el.value;
      const go = $("#lab-cmp-go");
      if (go) go.setAttribute("href", compareHref(C.cmp.original, C.cmp.rebuild));
      return;
    }
    if (el.id === "lab-lift") { C.pick.lift = el.checked; scheduleLine(); return; }
    if (el.id === "lab-guard") { C.pick.guard = el.checked; scheduleLine(); return; }
    if (el.id === "lab-differ") { C.differ = el.checked; const box = $("#lab-sections"); if (box) box.innerHTML = sectionsHTML(C.adapterData.record, C.adapterData.record.measured || []); }
  });
  main.addEventListener("keydown", (e) => {
    if (e.target.id === "lab-work" && e.key === "Enter") { e.preventDefault(); e.target.blur(); }
  });
  main.addEventListener("click", (e) => {
    if (S.view !== "cclab") return;
    const t = e.target;
    if (t.closest("#lab-go")) return start("run");
    if (t.closest("#lab-doctor")) return start("doctor");
    if (t.closest("#lab-report")) return startReport();
    if (t.closest("#lab-compare-again")) return compareAgain();
    if (t.closest("#lab-all") || t.closest("#lab-none")) {
      const on = !!t.closest("#lab-all");
      for (const box of document.querySelectorAll("#lab-matrix input[data-build]")) box.checked = on;
      C.pick.builds = on ? null : new Set();
      scheduleLine();
      return;
    }
    const jump = t.closest("[data-jump]");
    if (jump) {
      e.preventDefault();
      const target = document.getElementById(jump.dataset.jump);
      if (target) target.scrollIntoView({ block: "start", behavior: "smooth" });
    }
  });
}

wire();

register({
  name: "cclab", match: /^\/cclab$/, show: showWork,
  leave: () => { clearTimeout(C.timer); clearTimeout(C.lineTimer); },
  status: onStatus,
  keys: { i: () => { location.hash = "#/cclab"; } },
  help: [["i", "the compiler lab: what dread cclab found about a compiler (calling convention, idioms, helpers, what fails), a compiler's probes run as a job"]],
  nav: { title: "Compiler lab (i)", href: "#/cclab", icon: ICON },
});
// (the same name: the bar's button stays lit on a compiler's profile)
register({ name: "cclab", match: /^\/cclab\/([^/]+)$/, show: showAdapter, leave: () => { clearTimeout(C.timer); } });
register({ name: "cclab", match: /^\/cclab\/compare\/([^/]+)\/([^/]+)$/, show: showCompare,
  leave: () => { clearTimeout(C.timer); C.compare = null; } });
