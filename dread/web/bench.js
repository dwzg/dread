// dread gui's bench page (the server's dread/guipages/bench.py): what DREAD made of a real project built in
// many ways (dread bench), read from the report's own data (dread bench report --json). The builds of a
// recipe as a table that sorts and filters (compiler, release, options, project), each build's trend of one
// figure over its measurements, a build's whole measurement with the build opened as the page's target, and
// dread bench's report and measurements run as jobs. The page computes nothing the tool does not: figures,
// releases and categories come from the record; the estimate of a measurement from the server.
// Addresses: #/bench (the first recipe), #/bench/RECIPE, #/bench/RECIPE/BUILD.

import { $, esc, api, ago, when, toast, size, S, prefs, register, enter, nextRender, isCurrent } from "./app.js";

const POLL = 2000;              // ms between looks while a bench job is queued or running
const LIVE = new Set(["queued", "running", "stopping"]);
const ICON = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 13.5h11"/><path d="M4.5 11V8"/><path d="M8 11V4.5"/><path d="M11.5 11V6.5"/></svg>`;
const SHOWN_REASONS = 15;       // reasons a build's page lists before "all"

const B = { recipe: null, data: null, trend: null, sort: null, filters: null, timer: 0, started: new Set(),
  build: null, detail: null, chartKey: null, steps: null, picked: null, lineSeq: 0, lineTimer: 0, live: null };

// ---- small helpers ----

function count(n) { return n == null ? "–" : Number(n).toLocaleString("en-US"); }
function plural(n, one, many) { return `${count(n)} ${n === 1 ? one : many || one + "s"}`; }
function seconds(s) {
  if (s == null || !isFinite(s)) return "–";
  if (s < 10) return s.toFixed(1) + " s";
  if (s < 120) return Math.round(s) + " s";
  if (s < 7200) return Math.round(s / 60) + " min";
  return (s / 3600).toFixed(1) + " h";
}
// A figure's value as the page writes it, by its unit (dread.bench FIGURES).
function fmt(value, unit) {
  if (value == null) return "–";
  if (unit === "flag") return value ? "yes" : "no";
  if (unit === "percent") return value.toFixed(1) + " %";
  if (unit === "ratio") return Number(value).toFixed(3);
  if (unit === "seconds") return seconds(value);
  if (unit === "bytes") return size(value);
  return count(value);
}
// An axis tick's label: a figure's unit, rounded as a tick is.
function tick(value, unit) {
  if (unit === "percent") return Number(value.toPrecision(4)) + " %";
  if (unit === "seconds") return seconds(value);
  if (unit === "bytes") return size(value);
  if (unit === "ratio") return String(Number(value.toPrecision(3)));
  return count(Number(value.toPrecision(12)));
}
function delta(now, before, unit) {
  if (now == null || before == null || unit === "flag" || now === before) return "";
  const d = now - before;
  const text = unit === "percent" ? Math.abs(d).toFixed(1) + " pt" : unit === "ratio" ? Math.abs(d).toFixed(3) :
    unit === "seconds" ? seconds(Math.abs(d)) : unit === "bytes" ? size(Math.abs(d)) : count(Math.abs(d));
  return (d > 0 ? "+" : "−") + text;
}
function day(t) { return t ? new Date(t * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : ""; }
function stamp(t) { return t ? new Date(t * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""; }
function releaseKey(r) { return (r || "").split(".").map((x) => x.padStart(6, "0")).join("."); }
function units() { return Object.fromEntries(((B.data && B.data.record && B.data.record.figures) || []).map((f) => [f.key, f])); }
function figureOf(key) { return units()[key] || { key, title: key, unit: "count" }; }
function jobLine(job, what) {
  if (!job) return "";
  const words = job.state === "failed" ? `failed${job.exit != null ? " (exit status " + job.exit + ")" : ""}` : job.state;
  return `<a href="#/jobs/${job.id}" title="${esc(job.line || "")}">job ${job.id}</a> ${esc(what || "")} ${esc(words)}`;
}
function disabledWhy(why) { return why ? ` disabled title="${esc(why)}"` : ""; }

// ---- the recipe's builds ----

const FILTERS = { text: "", kind: "", release: "", opt: "", cpu: "", project: "" };

// The table's columns: what each shows of a build and how it sorts (a figure's value, or the build's); a
// column of a figure no build has is left out.
const COLUMNS = [
  { key: "build", title: "build", sort: (b) => b.build, cell: (b) => `<a class="bn-name" href="#/bench/${encodeURIComponent(B.recipe)}/${encodeURIComponent(b.build)}">${esc(b.build)}</a>${b.current === false ? ` <span class="bn-flag warn" title="the recipe changed since it was built: a measurement builds it again first">changed</span>` : ""}` },
  { key: "compiler", title: "compiler", help: "the compiler's kind and release (its version line on hover), and the toolchain image it ran in", sort: (b) => (b.kind || "") + " " + releaseKey(b.release), cell: (b) => `<span title="${esc(b.compiler || "")}">${esc(b.kind || "?")} ${esc(b.release || "")}</span>${b.toolchain ? ` <span class="bn-muted" title="built in the toolchain image ${esc(b.toolchain)}">${esc(b.toolchain)}</span>` : ""}` },
  { key: "options", title: "options", help: "the optimisation and the core of its flags (all of them on hover)", sort: (b) => (b.optimisation || "") + " " + (b.cpu || ""), cell: (b) => `<span class="mono" title="${esc(b.cflags || "")}">${esc([b.optimisation, b.cpu].filter(Boolean).join(" ") || "–")}</span>` },
  { key: "functions", title: "functions", num: true, fig: "functions" },
  { key: "lifted", title: "lifted", help: "the share of the functions the lift check decided that it lifted, and how many (sorts by the share)", num: true, fig: "lifted_share", cell: (b) => b.figures.lifted == null ? "–" : `${b.figures.lifted_share.toFixed(1)} % <span class="bn-muted">${count(b.figures.lifted)}/${count(b.figures.checked)}</span>` },
  { key: "trend", title: "trend", help: "the chosen figure over the build's measurements, the last one marked, and its change against the one before (sorts by that change)", sort: (b) => trendChange(b) ?? -Infinity, cell: (b) => sparkline(b) },
  { key: "readable", title: "readable", num: true, fig: "readable", help: "functions dread readable --check accepted" },
  { key: "ghidra", title: "Ghidra", help: "the function starts Ghidra found without the symbols: found · missed · extra", num: true, fig: "ghidra_found", cell: (b) => b.figures.ghidra_found == null ? "–" : `${count(b.figures.ghidra_found)} · ${count(b.figures.ghidra_missed)} · ${count(b.figures.ghidra_extra)}` },
  { key: "noreturn", title: "never return", num: true, fig: "noreturn" },
  { key: "registers", title: "registers", help: "functions whose registers dread callconv measured that disagree with dread.regflow, of those measured", num: true, fig: "registers_disagree", cell: (b) => b.figures.registers_measured == null ? "–" : `${count(b.figures.registers_disagree)} <span class="bn-muted">of ${count(b.figures.registers_measured)}</span>` },
  { key: "whatcc", title: "libraries", help: "whether dread whatcc told the libraries' toolchain right; of the library functions it named as certain, how many have the function's own name", num: true, fig: "whatcc_sure", cell: (b) => b.figures.whatcc_right == null ? "–" : `${b.figures.whatcc_right ? "right" : `<span class="bn-bad">wrong</span>`} <span class="bn-muted">${count(b.figures.whatcc_agree)}/${count(b.figures.whatcc_sure)}</span>` },
  { key: "seconds", title: "seconds", help: "all steps of its last measurement", num: true, fig: "seconds", cell: (b) => seconds(b.figures.seconds) },
  { key: "when", title: "measured", sort: (b) => b.when || 0, cell: (b) => `<span title="${esc(when(b.when))}">${esc(ago(b.when))}</span>` },
];

function shownColumns(builds) {
  return COLUMNS.filter((c) => !c.fig || builds.some((b) => b.figures[c.fig] != null));
}

function sortValue(col, b) {
  if (col.sort) return col.sort(b);
  const v = b.figures[col.fig];
  return v == null ? -Infinity : typeof v === "boolean" ? Number(v) : v;
}
function trendChange(b) {
  const pts = (b.trend || []).filter((p) => p[1] != null && typeof p[1] !== "boolean");
  return pts.length > 1 ? pts[pts.length - 1][1] - pts[pts.length - 2][1] : null;
}

function filtered() {
  const builds = (B.data.record && B.data.record.builds) || [];
  const f = B.filters, text = f.text.trim().toLowerCase();
  return builds.filter((b) => (!f.kind || b.kind === f.kind) && (!f.release || b.release === f.release) &&
    (!f.opt || b.optimisation === f.opt) && (!f.cpu || b.cpu === f.cpu) && (!f.project || b.project === f.project) &&
    (!text || [b.build, b.compiler, b.cflags, b.project].some((s) => (s || "").toLowerCase().includes(text))));
}

function sorted(list) {
  const s = B.sort;
  const col = COLUMNS.find((c) => c.key === s.key) || COLUMNS[0];
  const dir = s.dir === "desc" ? -1 : 1;
  return list.slice().sort((a, b) => {
    const x = sortValue(col, a), y = sortValue(col, b);
    const c = typeof x === "string" || typeof y === "string" ? String(x).localeCompare(String(y)) : x - y;
    return c * dir || a.build.localeCompare(b.build);
  });
}

// A build's trend of the chosen figure: a small line of its measurements, the last one marked; its values
// in the title (and on the build's page as a chart with its table).
function sparkline(b) {
  const pts = (b.trend || []).filter((p) => p[0] != null && p[1] != null && typeof p[1] !== "boolean");
  if (!pts.length) return `<span class="bn-muted">–</span>`;
  const W = 96, H = 22, P = 3;
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const X = (x) => x1 === x0 ? W - P : P + (x - x0) / (x1 - x0) * (W - 2 * P);
  const Y = (y) => y1 === y0 ? H / 2 : H - P - (y - y0) / (y1 - y0) * (H - 2 * P);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)} ${Y(p[1]).toFixed(1)}`).join(" ");
  const last = pts[pts.length - 1], unit = figureOf(B.trend).unit;
  const change = trendChange(b);
  const title = `${figureOf(B.trend).title}: ` + pts.map((p) => `${day(p[0])} ${fmt(p[1], unit)}`).join(", ");
  return `<span class="bn-spark"><svg viewBox="0 0 ${W} ${H}" class="bn-spark-svg" role="img" aria-label="${esc(title)}"><title>${esc(title)}</title>` +
    `<path d="${d}" class="bn-spark-line"/><circle cx="${X(last[0]).toFixed(1)}" cy="${Y(last[1]).toFixed(1)}" r="2.6" class="bn-spark-dot"/></svg>` +
    `<span class="bn-spark-val">${esc(fmt(last[1], unit))}${change ? ` <span class="bn-muted">${esc(delta(last[1], pts[pts.length - 2][1], unit))}</span>` : ""}</span></span>`;
}

function options(values, chosen, all) {
  return `<option value="">${esc(all)}</option>` + values.map((v) => `<option value="${esc(v)}"${v === chosen ? " selected" : ""}>${esc(v)}</option>`).join("");
}

function distinct(builds, key, order) {
  const seen = [...new Set(builds.map((b) => b[key]).filter((v) => v != null && v !== ""))];
  return order ? seen.sort(order) : seen.sort();
}

// What a job of dread bench does, from its command line: measures (and then makes the report), or the report.
function jobWhat(job) { return / bench measure /.test(job.line || "") ? "measures:" : "makes the report:"; }

function reportState() {
  const d = B.data;
  const live = d.job && LIVE.has(d.job.state);
  if (live) return `<span class="bn-state busy">${jobLine(d.job, jobWhat(d.job))}</span>`;
  if (!d.record) return `<span class="bn-state">no report yet${d.job ? " · " + jobLine(d.job, "") : ""}</span>`;
  const parts = [`report of ${esc(ago(d.when))}`];
  if (d.stale_count) parts.push(`<span class="warn" title="${esc(d.stale.join("\n"))}">${plural(d.stale_count, "newer file")}</span>`);
  if (d.job && d.job.state === "failed") parts.push(`<span class="bad">${jobLine(d.job, "")}</span>`);
  return `<span class="bn-state">${parts.join(" · ")}</span>`;
}

function headHTML(title, back) {
  const d = B.data;
  const recipes = B.recipes && B.recipes.length > 1 ? `<select id="bn-recipe" aria-label="Recipe">${B.recipes.map((r) => `<option${r.name === B.recipe ? " selected" : ""}>${esc(r.name)}</option>`).join("")}</select>` : "";
  return `<div class="bn-head">${back || ""}<h1>${title}</h1>${recipes}
    <span class="bn-muted bn-where" title="${esc(d.bench)}">${esc(d.bench)}</span></div>`;
}

// The bench jobs of this recipe queued or running (all of them; on the recipe's page not the report's job,
// which its state line shows).
function liveHTML(all = false) {
  const live = (B.data.live || []).filter((j) => all || !(B.data.job && j.id === B.data.job.id));
  return live.length ? `<div class="bn-live">${live.map((j) => `<div>${jobLine(j, jobWhat(j))} · <span class="mono">${esc(j.line || "")}</span></div>`).join("")}</div>` : "";
}

function renderRecipe() {
  const d = B.data, main = $("#main");
  const rec = d.record;
  const builds = (rec && rec.builds) || [];
  const f = B.filters;
  const why = d.why || d.changes;
  let html = `<div class="bn">` + headHTML("Bench: " + esc(d.recipe));
  html += `<div class="bn-sub">${rec ? `${plural(builds.length, "build")} measured${d.builds ? ` of ${count(d.builds.length)} in the recipe` : ""}` +
      (rec.missing && rec.missing.length ? ` · not measured: ${esc(rec.missing.join(", "))}` : "") : "no report of this recipe yet"}
    · ${reportState()}
    <button type="button" id="bn-report"${disabledWhy(why)}>${rec ? "Make the report again" : "Make the report"}</button></div>`;
  html += liveHTML();
  if (!rec) {
    html += `<div class="bn-none">${d.why ? esc(d.why) : d.changes ? esc(d.changes) + `: run <span class="mono">dread bench report ${esc(d.recipe)} --json ${esc(d.path)}</span> in a terminal` : "The report is being made: it reads each build's metrics.json and takes a few seconds."}</div></div>`;
    main.innerHTML = html;
    return;
  }
  const trendFigures = rec.figures.filter((x) => x.unit !== "flag");
  html += `<div class="bn-filters" role="group" aria-label="Filters">
    <input type="search" id="bn-text" placeholder="Filter: build, compiler, flags" value="${esc(f.text)}" spellcheck="false" autocomplete="off" aria-label="Filter the builds">
    <select data-filter="kind" aria-label="Compiler">${options(distinct(builds, "kind"), f.kind, "every compiler")}</select>
    <select data-filter="release" aria-label="Release">${options(distinct(builds.filter((b) => !f.kind || b.kind === f.kind), "release", (a, b) => releaseKey(a).localeCompare(releaseKey(b))), f.release, "every release")}</select>
    <select data-filter="opt" aria-label="Optimisation">${options(distinct(builds, "optimisation"), f.opt, "every -O")}</select>
    <select data-filter="cpu" aria-label="Core">${options(distinct(builds, "cpu"), f.cpu, "every core")}</select>
    <select data-filter="project" aria-label="Project">${options(distinct(builds, "project"), f.project, "every project")}</select>
    <label class="bn-trend-pick">trend of <select id="bn-trend" aria-label="Trend of">${trendFigures.map((x) => `<option value="${esc(x.key)}"${x.key === B.trend ? " selected" : ""}>${esc(x.title)}</option>`).join("")}</select></label>
  </div>`;
  html += `<div class="bn-count" id="bn-count"></div><div class="bn-scroll"><table class="bn-table" id="bn-table"></table></div>`;
  html += `<div class="bn-foot bn-muted">Figures as <span class="mono">dread bench report</span> shows them, from each build's metrics.json; a build's page has all of them. A trend's points are the build's measurements (its history/), the last marked.</div></div>`;
  main.innerHTML = html;
  renderTable();
}

function renderTable() {
  const table = $("#bn-table");
  if (!table) return;
  const list = sorted(filtered());
  const all = (B.data.record.builds || []).length;
  $("#bn-count").textContent = list.length === all ? `${plural(all, "build")}` : `${count(list.length)} of ${plural(all, "build")}`;
  const s = B.sort;
  const columns = shownColumns(B.data.record.builds || []);
  const head = columns.map((c) => {
    const on = s.key === c.key;
    const title = c.key === "trend" ? "trend: " + figureOf(B.trend).title : c.title;
    return `<th class="${c.num ? "r" : ""}${on ? " on" : ""} c-${c.key}" aria-sort="${on ? (s.dir === "desc" ? "descending" : "ascending") : "none"}"${c.help ? ` title="${esc(c.help)}"` : ""}><button type="button" class="bn-sort" data-sort="${c.key}">${esc(title)}${on ? (s.dir === "desc" ? " ↓" : " ↑") : ""}</button></th>`;
  }).join("");
  const rows = list.map((b) => `<tr>${columns.map((c) => `<td class="${c.num ? "r" : ""} c-${c.key}">${c.cell ? c.cell(b) : esc(fmt(b.figures[c.fig], figureOf(c.fig).unit))}</td>`).join("")}</tr>`).join("");
  table.innerHTML = `<thead><tr>${head}</tr></thead><tbody>${rows || `<tr><td colspan="${columns.length}" class="bn-none">No build matches the filters.</td></tr>`}</tbody>`;
}

// ---- one build ----

// The trend chart of one figure, drawn at the width it has on screen (so that its text is the page's size).
function chartHTML(key, width) {
  const f = figureOf(key);
  const hist = (B.detail.history || []).map((h) => [h.when, (h.figures || {})[key]]).filter((p) => p[0] != null && p[1] != null);
  if (!hist.length) return `<div class="bn-none">No measurement of this build has this figure.</div>`;
  const W = Math.max(300, Math.round(width || 720)), H = 220, L = 54, R = 18, T = 24, Bm = 30;
  const xs = hist.map((p) => p[0]), ys = hist.map((p) => Number(p[1]));
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  const ticks = niceTicks(y0, y1, 4, ["count", "bytes"].includes(f.unit));
  y0 = ticks[0]; y1 = ticks[ticks.length - 1];
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const X = (x) => x1 === x0 ? (L + W - R) / 2 : L + (x - x0) / (x1 - x0) * (W - L - R);
  const Y = (y) => y1 === y0 ? (T + H - Bm) / 2 : H - Bm - (y - y0) / (y1 - y0) * (H - T - Bm);
  const grid = ticks.map((t) => `<line x1="${L}" x2="${W - R}" y1="${Y(t).toFixed(1)}" y2="${Y(t).toFixed(1)}" class="bn-grid"/>` +
    `<text x="${L - 8}" y="${(Y(t) + 4).toFixed(1)}" class="bn-axis" text-anchor="end">${esc(tick(t, f.unit))}</text>`).join("");
  const days = [...new Set([x0, x1, x0 + (x1 - x0) / 2].map((x) => x))];
  const labels = (x1 === x0 ? [x0] : days).map((x, i) => `<text x="${X(x).toFixed(1)}" y="${H - 9}" class="bn-axis" text-anchor="${x1 === x0 ? "middle" : i === 0 ? "start" : i === 1 ? "end" : "middle"}">${esc(day(x))}</text>`).join("");
  const d = hist.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)} ${Y(Number(p[1])).toFixed(1)}`).join(" ");
  const last = hist[hist.length - 1];
  const dots = hist.map((p, i) => `<circle cx="${X(p[0]).toFixed(1)}" cy="${Y(Number(p[1])).toFixed(1)}" r="${i === hist.length - 1 ? 4 : 2.2}" class="${i === hist.length - 1 ? "bn-end" : "bn-pt"}"/>`).join("");
  B.chart = { hist, X, Y, W, H, L, R, T, Bm, unit: f.unit };
  return `<div class="bn-chart-box"><svg viewBox="0 0 ${W} ${H}" class="bn-chart" id="bn-chart" role="img" aria-label="${esc(f.title)} over ${hist.length} measurements">
    ${grid}${labels}<path d="${d}" class="bn-line"/>${dots}
    <text x="${Math.min(X(last[0]) + 0, W - R).toFixed(1)}" y="${(Y(Number(last[1])) - 10).toFixed(1)}" class="bn-endlabel" text-anchor="end">${esc(fmt(last[1], f.unit))}</text>
    <line id="bn-cross" class="bn-cross" x1="0" x2="0" y1="${T}" y2="${H - Bm}" visibility="hidden"/>
    <circle id="bn-focus" class="bn-focus" r="5" cx="0" cy="0" visibility="hidden"/>
    <rect x="${L}" y="${T}" width="${W - L - R}" height="${H - T - Bm}" class="bn-hit" id="bn-hit"/></svg>
    <div class="bn-tip" id="bn-tip" hidden></div></div>`;
}

// Round ticks around [lo, hi] (n intervals or about), for an axis that starts where the values do; whole
// steps for counts.
function niceTicks(lo, hi, n, whole) {
  if (lo === hi) { const pad = Math.max(whole ? 1 : 0, Math.abs(lo) > 1 ? Math.abs(lo) * 0.05 : 1); lo -= pad; hi += pad; }
  const raw = (hi - lo) / n, mag = 10 ** Math.floor(Math.log10(raw));
  let step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || 10 * mag;
  if (whole) step = Math.max(1, Math.round(step));
  const first = Math.floor(lo / step) * step, out = [];
  for (let v = first; v <= hi + step * 0.001; v += step) out.push(Number(v.toPrecision(12)));
  if (out[out.length - 1] < hi) out.push(Number((out[out.length - 1] + step).toPrecision(12)));
  return out;
}

function historyTable(key) {
  const f = figureOf(key);
  const hist = (B.detail.history || []).map((h) => ({ when: h.when, v: (h.figures || {})[key], file: h.file }));
  const rows = hist.map((h, i) => ({ ...h, d: i ? delta(h.v, hist[i - 1].v, f.unit) : "" })).reverse();
  return `<details class="bn-more"><summary>The measurements as a table (${hist.length})</summary><div class="bn-scroll"><table class="bn-mini">
    <thead><tr><th>measured</th><th class="r">${esc(f.title)}</th><th class="r">change</th><th>file</th></tr></thead><tbody>
    ${rows.map((h) => `<tr><td>${esc(stamp(h.when))}</td><td class="r">${esc(fmt(h.v, f.unit))}</td><td class="r bn-muted">${esc(h.d)}</td><td class="mono bn-muted">${esc(h.file)}</td></tr>`).join("")}
    </tbody></table></div></details>`;
}

function figuresTable() {
  const b = B.detail, hist = b.history || [];
  const before = hist.length > 1 ? hist[hist.length - 2].figures || {} : {};
  const known = b.figures_known || [];
  const rows = known.filter((f) => b.figures[f.key] != null || before[f.key] != null).map((f) => {
    const now = b.figures[f.key], was = before[f.key];
    const d = delta(now, was, f.unit);
    const pick = f.unit !== "flag" ? ` <button type="button" class="bn-link" data-chart="${esc(f.key)}" title="its trend">trend</button>` : "";
    return `<tr${f.key === B.chartKey ? ' class="on"' : ""}><td>${esc(f.title)}${pick}</td><td class="r">${esc(fmt(now, f.unit))}</td><td class="r bn-muted">${esc(hist.length > 1 ? fmt(was, f.unit) : "")}</td><td class="r">${esc(d)}</td></tr>`;
  }).join("");
  return `<div class="bn-scroll"><table class="bn-mini bn-figs"><thead><tr><th>figure</th><th class="r">now</th><th class="r">before</th><th class="r">change</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function stopsHTML() {
  const b = B.detail, cats = b.categories || Object.keys(b.stops || {});
  if (b.not_lifted == null || !b.figures.checked) return "";
  const rows = cats.map((c) => `<tr><td>${esc(c)}</td><td class="r">${count((b.stops || {})[c] || 0)}</td></tr>`).join("");
  return `<section class="bn-card"><h2>What stops the rest</h2><p class="bn-note">${plural(b.not_lifted, "function")} not lifted: the drops by what stopped them, and the lifter's refusals${b.refused_most ? ` (most for: <span class="mono">${esc(b.refused_most.category)}</span>, ${count(b.refused_most.count)})` : ""}.</p>
    <table class="bn-mini"><tbody>${rows}<tr><td>refused by the lifter</td><td class="r">${count(b.refused || 0)}</td></tr></tbody></table></section>`;
}

function reasonsHTML() {
  const reasons = Object.entries(((B.detail.metrics || {}).lift || {}).reasons || {}).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  if (!reasons.length) return "";
  const shown = B.allReasons ? reasons : reasons.slice(0, SHOWN_REASONS);
  return `<section class="bn-card"><h2>Why the rest is not lifted</h2><p class="bn-note">The lift check's verdicts by reason, particulars (registers, addresses, numbers) taken out.</p>
    <ul class="bn-reasons">${shown.map(([r, n]) => `<li><span class="bn-n">${count(n)}</span> <span class="mono">${esc(r)}</span></li>`).join("")}</ul>
    ${reasons.length > shown.length ? `<button type="button" class="bn-link" id="bn-all-reasons">all ${count(reasons.length)}</button>` : ""}</section>`;
}

function namesHTML() {
  const m = B.detail.metrics || {};
  const parts = [];
  for (const [step, title] of [["lift", "lifted"], ["readable", "readable"]]) {
    const a = (m[step] || {}).against_last;
    if (!a) continue;
    parts.push(`<div><b>${title}</b>: gained ${a.gained.length ? a.gained.map((n) => `<span class="mono">${esc(n)}</span>`).join(", ") : "none"}; lost ${a.lost.length ? a.lost.map((n) => `<span class="mono bn-bad">${esc(n)}</span>`).join(", ") : "none"}</div>`);
  }
  return parts.length ? `<section class="bn-card"><h2>Against the measurement before</h2>${parts.join("")}</section>` : "";
}

function registersHTML() {
  const c = (B.detail.metrics || {}).callconv || {};
  const dis = Object.entries(c.disagree || {});
  if (!c.ran) return "";
  return `<section class="bn-card"><h2>Registers measured against regflow</h2><p class="bn-note">dread callconv: of ${plural(c.measured, "function")} measured by their cases, ${count(c.measured - c.agree)} disagree with dread.regflow's answers.</p>
    ${dis.length ? `<ul class="bn-reasons">${dis.map(([n, found]) => `<li><span class="mono">${esc(n)}</span>: ${esc(Object.entries(found).map(([k, v]) => k + " " + v.join(", ")).join("; "))}</li>`).join("")}</ul>` : ""}</section>`;
}

function readableHTML() {
  const rd = (B.detail.metrics || {}).readable;
  if (!rd) return "";
  if (!rd.ran) return `<section class="bn-card"><h2>Readable C</h2><p class="bn-note">Not measured: ${esc(rd.why || "the readable step did not run")}.</p></section>`;
  const fig = (k) => rd[k] ? `${count(rd[k].sum)}${rd[k].median != null ? ` <span class="bn-muted">(median ${rd[k].median})</span>` : ""}` : "–";
  const stay = Object.entries(rd.stay_lifted || {});
  const src = rd.source || {};
  const worst = (list, title) => list && list.length ? `<h3>${title}</h3><ul class="bn-reasons">${list.map((w) => `<li><span class="mono">${esc(w.name)}</span>${w.name !== w.source ? ` = <span class="mono">${esc(w.source)}</span>` : ""} <span class="bn-muted">(${esc(w.unit)})</span>: ${count(w.statements)} statements against ${count(w.src_statements)}, depth ${w.depth} against ${w.src_depth}</li>`).join("")}</ul>` : "";
  return `<section class="bn-card"><h2>Readable C</h2>
    <table class="bn-mini"><tbody>
      <tr><td>lifted, turned readable, readable</td><td class="r">${count(rd.lifted)} · ${count(rd.turned)} · ${count(rd.measured)}</td></tr>
      <tr><td>statements</td><td class="r">${fig("statements")}</td></tr><tr><td>gotos left (functions)</td><td class="r">${count((rd.gotos || {}).sum)} (${count((rd.gotos || {}).functions)})</td></tr>
      <tr><td>casts</td><td class="r">${fig("casts")}</td></tr><tr><td>members of structures</td><td class="r">${fig("members")}</td></tr>
      ${src.ratio ? `<tr><td>statements against the source's (median, quartiles)</td><td class="r">${src.ratio[1]} (${src.ratio[0]}–${src.ratio[2]})</td></tr>` : ""}
    </tbody></table>
    ${stay.length ? `<h3>Why the rest stays lifted</h3><ul class="bn-reasons">${stay.map(([k, n]) => `<li><span class="bn-n">${count(n)}</span> ${esc(k)}</li>`).join("")}</ul>` : ""}
    ${worst(src.worst_larger, "Much larger than the source")}${worst(src.worst_deeper, "Deeper than the source")}</section>`;
}

function measureHTML() {
  const b = B.detail;
  const why = b.why || b.changes;
  const steps = b.steps || [];
  const measured = steps.filter((s) => b.figures["seconds_" + s] != null);     // (by default the steps it had)
  const picked = B.steps || new Set(measured.length ? measured : steps);
  return `<section class="bn-card"><h2>Measure again</h2>
    <p class="bn-note">dread bench measure as a job, the steps chosen; it ends with the report, which this page then reads. ${b.current === false ? `<b>The recipe changed since this build was made: the measurement builds it again first.</b>` : ""}</p>
    <div class="bn-steps" role="group" aria-label="Steps">${steps.map((s) => `<label><input type="checkbox" data-step="${esc(s)}"${picked.has(s) ? " checked" : ""}> ${esc(s)} <span class="bn-muted">${esc(seconds(b.figures["seconds_" + s]))}</span></label>`).join("")}</div>
    <pre class="bn-cmd" id="bn-measure-line"></pre><div class="bn-estimate" id="bn-estimate"></div>
    <div class="bn-actions"><a class="bn-link" id="bn-every" href="#/run/bench">Every option</a><button type="button" class="primary" id="bn-measure"${disabledWhy(why)}>Measure</button></div></section>`;
}

function renderBuild() {
  const b = B.detail, main = $("#main");
  const back = `<a class="bn-back" href="#/bench/${encodeURIComponent(B.recipe)}" title="the recipe's builds">← ${esc(B.recipe)}</a>`;
  const hist = b.history || [];
  const before = hist.length > 1 ? hist[hist.length - 2].figures || {} : {};
  const tiles = [["functions", "functions"], ["lifted", "lifted"], ["readable", "readable"], ["seconds", "seconds, all steps"]]
    .filter(([k]) => b.figures[k] != null).map(([k, label]) => {
      const f = figureOf(k), d = delta(b.figures[k], before[k], f.unit);
      return `<div class="bn-tile"><div class="bn-tile-k">${esc(label)}</div><div class="bn-tile-v">${esc(fmt(b.figures[k], f.unit))}${k === "lifted" && b.figures.checked ? ` <span class="bn-muted">of ${count(b.figures.checked)}</span>` : ""}</div><div class="bn-tile-d">${d ? esc(d) + " against the measurement before" : hist.length > 1 ? "as before" : "the first measurement"}</div></div>`;
    }).join("");
  const trendFigures = (b.figures_known || []).filter((x) => x.unit !== "flag" && hist.some((h) => (h.figures || {})[x.key] != null));
  let html = `<div class="bn bn-build">` + headHTML(esc(b.build), back);
  html += `<div class="bn-facts"><span title="the compiler's version line">${esc(b.compiler || b.cc || "")}</span> · <span class="mono">${esc(b.cflags || "")}</span>${b.project ? ` · project ${esc(b.project)}` : ""}
    · measured ${esc(ago(b.when))} <span class="bn-muted">(${esc(when(b.when))})</span>${b.current === true ? " · built from the recipe as it is" : b.current === false ? ` · <span class="warn">the recipe changed since it was built</span>` : ""}</div>`;
  html += `<div class="bn-summary">${esc(b.summary || "")}</div>`;
  html += `<div class="bn-actions bn-top"><button type="button" id="bn-open"${disabledWhy(b.open)}>Open as the target</button><span class="bn-muted bn-open-why">${b.open ? esc(b.open) : `<span class="mono">${esc(b.directory)}</span>: its listing, C, verdicts and system run, as dread gui --target shows them`}</span></div>`;
  html += liveHTML(true);
  html += `<div class="bn-tiles">${tiles}</div>`;
  html += `<section class="bn-card"><div class="bn-card-head"><h2>Trend</h2><select id="bn-chart-key" aria-label="Figure">${trendFigures.map((x) => `<option value="${esc(x.key)}"${x.key === B.chartKey ? " selected" : ""}>${esc(x.title)}</option>`).join("")}</select>
    <span class="bn-muted">${plural(hist.length, "measurement")}</span></div><div id="bn-chart-wrap"></div><div id="bn-hist">${historyTable(B.chartKey)}</div></section>`;
  html += `<div class="bn-cols"><section class="bn-card"><h2>Every figure</h2>${figuresTable()}</section><div class="bn-col">${stopsHTML()}${namesHTML()}${measureHTML()}</div></div>`;
  html += reasonsHTML() + registersHTML() + readableHTML();
  html += `</div>`;
  main.innerHTML = html;
  drawChart();
  scheduleLine(true);
}

function drawChart() {
  const wrap = $("#bn-chart-wrap");
  if (wrap) wrap.innerHTML = chartHTML(B.chartKey, wrap.clientWidth);
}

// ---- the measurement's command line and estimate (the server's) ----

function stepsChosen() {
  return [...document.querySelectorAll("#main input[data-step]")].filter((x) => x.checked).map((x) => x.dataset.step);
}

function scheduleLine(now = false) {
  clearTimeout(B.lineTimer);
  B.lineTimer = setTimeout(askLine, now ? 0 : 150);
}

async function askLine() {
  const mine = ++B.lineSeq;
  const steps = stepsChosen();
  B.steps = new Set(steps);
  let r;
  try {
    r = await api("/api/bench/measure-line", { recipe: B.recipe, builds: JSON.stringify([B.build]), steps: JSON.stringify(steps) });
  } catch (e) {
    r = { error: e.message };
  }
  if (mine !== B.lineSeq || !$("#bn-measure-line")) return;
  $("#bn-measure-line").textContent = r.line || "";
  const est = $("#bn-estimate");
  const never = r.unknown && r.unknown.length ? `${r.unknown.join(", ")} never measured here: longer` : "";
  est.innerHTML = r.error ? `<span class="bad">${esc(r.error)}</span>` : r.estimate != null ?
    `About ${esc(seconds(r.estimate))}: the chosen steps' seconds in its last measurement${never ? ` (${esc(never)})` : ""}.` :
    "No estimate: none of these steps was measured here before.";
  const button = $("#bn-measure");
  if (button && !(B.detail.why || B.detail.changes)) button.disabled = !!r.error;
  const every = $("#bn-every");
  if (every && r.args) every.href = "#/run/bench@" + encodeURIComponent(JSON.stringify(r.args));
}

// ---- loading, polling, jobs ----

async function loadRecipes() {
  try {
    const o = await api("/api/bench");
    B.recipes = o.recipes;
    return o;
  } catch (e) {
    return null;
  }
}

function schedule() {
  clearTimeout(B.timer);
  const d = B.data, busy = d && ((d.job && LIVE.has(d.job.state)) || (d.live && d.live.length));
  if (busy) B.timer = setTimeout(() => refresh(true), POLL);
}

async function refresh(keep) {
  if (B.build) return showBuild([null, B.recipe, B.build], keep);
  return showRecipe([null, B.recipe], keep);
}

async function maybeStart() {
  const d = B.data;
  const key = d.recipe + "@" + (d.inputs || 0);      // (once for each state of the files it reads)
  if (!d.auto || B.started.has(key)) return false;
  B.started.add(key);
  try {
    await api("/api/bench/report", {}, { recipe: d.recipe });
    return true;
  } catch (e) {
    toast("The report did not start: " + e.message, true);
    return false;
  }
}

async function showRecipe(match, keep = false) {
  const mine = nextRender();
  clearTimeout(B.timer);
  if (!B.recipes) await loadRecipes();
  let name = match[1] || null;           // (the router has decoded the address)
  if (!name) {
    const remembered = prefs.get("bench-recipe", null);
    const list = B.recipes || [];
    name = (list.find((r) => r.name === remembered) || list.find((r) => r.measured) || list[0] || {}).name;
  }
  if (!name) {
    if (!isCurrent(mine)) return;
    enter("bench", "Bench");
    $("#main").innerHTML = `<div class="bn"><div class="bn-head"><h1>Bench</h1></div><div class="bn-none">No recipe in DREAD's bench/ and no measured build in the bench directory. <span class="mono">dread bench all freertos</span> builds and measures the FreeRTOS recipe (hours).</div></div>`;
    return;
  }
  B.recipe = name;
  B.build = null;
  prefs.set("bench-recipe", name);
  B.trend = B.trend || prefs.get("bench-trend", "lifted");
  B.sort = B.sort || prefs.get("bench-sort", { key: "build", dir: "asc" });
  B.filters = B.filters || Object.assign({}, FILTERS, prefs.get("bench-filters", {}));
  let d;
  try {
    d = await api("/api/bench/report", { recipe: name, trend: B.trend });
  } catch (e) {
    if (!isCurrent(mine)) return;
    enter("bench", "Bench");
    $("#main").innerHTML = `<div class="bn"><div class="bn-head"><h1>Bench</h1></div><div class="bn-none bad">${esc(e.message)}</div></div>`;
    return;
  }
  if (!isCurrent(mine)) return;
  B.data = d;
  const scroll = $("#main").scrollTop;
  enter("bench", "Bench: " + name);
  if (keep && $("#bn-table") && d.record) {
    const focus = document.activeElement && document.activeElement.id;
    renderRecipe();
    if (focus === "bn-text") { const t = $("#bn-text"); t.focus(); t.setSelectionRange(t.value.length, t.value.length); }
  } else renderRecipe();
  $("#main").scrollTop = keep ? scroll : 0;
  if (await maybeStart()) return refresh(true);
  schedule();
}

async function showBuild(match, keep = false) {
  const mine = nextRender();
  clearTimeout(B.timer);
  if (!B.recipes) await loadRecipes();
  const recipe = match[1], build = match[2];
  if (B.build !== build) { B.steps = null; B.allReasons = false; }
  B.recipe = recipe;
  B.build = build;
  B.chartKey = B.chartKey || prefs.get("bench-chart", "lifted");
  let d, b;
  try {
    [d, b] = await Promise.all([api("/api/bench/report", { recipe, trend: B.chartKey }), api("/api/bench/build", { recipe, build })]);
  } catch (e) {
    if (!isCurrent(mine)) return;
    enter("bench", "Bench");
    $("#main").innerHTML = `<div class="bn"><div class="bn-head"><a class="bn-back" href="#/bench/${encodeURIComponent(recipe)}">← ${esc(recipe)}</a><h1>${esc(build)}</h1></div><div class="bn-none bad">${esc(e.message)}</div></div>`;
    return;
  }
  if (!isCurrent(mine)) return;
  B.data = d;
  B.detail = b;
  if (!(b.history || []).some((h) => (h.figures || {})[B.chartKey] != null)) B.chartKey = "lifted";
  const scroll = $("#main").scrollTop;
  enter("bench", build + " · Bench");
  renderBuild();
  $("#main").scrollTop = keep ? scroll : 0;
  if (await maybeStart()) return refresh(true);
  schedule();
}

function onStatus(s) {
  if (!s) return;
  const live = s.live || [];
  const n = live.length;
  const badge = document.querySelector('.nav-btn[data-page="bench"] .nav-badge');
  if (badge) {
    badge.hidden = !n;
    badge.textContent = n;
    badge.parentElement.title = "Bench (m)" + (n ? ` · ${n} job${n === 1 ? "" : "s"} of dread bench` : "");
  }
  const key = live.join(",");
  if (B.live !== null && key !== B.live && S.view === "bench") refresh(true);
  B.live = key;
}

async function startReport() {
  try {
    const r = await api("/api/bench/report", {}, { recipe: B.recipe });
    toast(`Job ${r.id}: the report.`);
  } catch (e) {
    toast(e.message, true);
  }
  refresh(true);
}

async function startMeasure() {
  const steps = stepsChosen();
  try {
    const r = await api("/api/bench/measure", {}, { recipe: B.recipe, builds: [B.build], steps });
    toast(`Job ${r.id}: dread bench measure ${B.build}.`);
  } catch (e) {
    toast(e.message, true);
  }
  refresh(true);
}

async function openBuild() {
  try {
    await api("/api/bench/open", {}, { recipe: B.recipe, build: B.build });
    toast(`${B.build} is the target now.`);
    location.hash = "#/";
    location.reload();
  } catch (e) {
    toast(e.message, true);
  }
}

// ---- the trend chart's pointer ----

function chartPointer(e) {
  const c = B.chart, svg = $("#bn-chart"), tip = $("#bn-tip");
  if (!c || !svg || !tip) return;
  const box = svg.getBoundingClientRect();
  const x = (e.clientX - box.left) / box.width * c.W;
  let best = null;
  for (const p of c.hist) if (!best || Math.abs(c.X(p[0]) - x) < Math.abs(c.X(best[0]) - x)) best = p;
  if (!best) return;
  const cx = c.X(best[0]), cy = c.Y(Number(best[1]));
  const cross = $("#bn-cross"), focus = $("#bn-focus");
  cross.setAttribute("x1", cx); cross.setAttribute("x2", cx); cross.setAttribute("visibility", "visible");
  focus.setAttribute("cx", cx); focus.setAttribute("cy", cy); focus.setAttribute("visibility", "visible");
  tip.hidden = false;
  tip.textContent = "";
  const v = document.createElement("b");
  v.textContent = fmt(best[1], c.unit);
  const t = document.createElement("span");
  t.textContent = stamp(best[0]);
  tip.append(v, t);
  const px = cx / c.W * box.width, py = cy / c.H * box.height;
  tip.style.left = Math.max(0, Math.min(box.width - tip.offsetWidth, px - tip.offsetWidth / 2)) + "px";
  tip.style.top = Math.max(0, py - tip.offsetHeight - 12) + "px";
}

function chartLeave() {
  for (const id of ["bn-cross", "bn-focus"]) { const el = $("#" + id); if (el) el.setAttribute("visibility", "hidden"); }
  const tip = $("#bn-tip");
  if (tip) tip.hidden = true;
}

function wire() {
  const main = $("#main");
  main.addEventListener("input", (e) => {
    if (e.target.id === "bn-text") {
      B.filters.text = e.target.value;
      prefs.set("bench-filters", B.filters);
      renderTable();
    }
  });
  main.addEventListener("change", (e) => {
    const el = e.target;
    if (el.dataset.filter && B.filters) {
      B.filters[el.dataset.filter] = el.value;
      if (el.dataset.filter === "kind") B.filters.release = "";
      prefs.set("bench-filters", B.filters);
      renderRecipe();
      return;
    }
    if (el.id === "bn-trend") { B.trend = el.value; prefs.set("bench-trend", el.value); refresh(true); return; }
    if (el.id === "bn-recipe") { location.hash = "#/bench/" + encodeURIComponent(el.value); return; }
    if (el.id === "bn-chart-key") { setChart(el.value); return; }
    if (el.dataset.step) scheduleLine();
  });
  main.addEventListener("click", (e) => {
    const t = e.target;
    const sort = t.closest(".bn-sort");
    if (sort) {
      const key = sort.dataset.sort;
      B.sort = B.sort.key === key ? { key, dir: B.sort.dir === "asc" ? "desc" : "asc" } : { key, dir: ["build", "compiler", "options"].includes(key) ? "asc" : "desc" };
      prefs.set("bench-sort", B.sort);
      renderTable();
      return;
    }
    if (t.closest("#bn-report")) return startReport();
    if (t.closest("#bn-measure")) return startMeasure();
    if (t.closest("#bn-open")) return openBuild();
    if (t.closest("#bn-all-reasons")) { B.allReasons = true; renderBuild(); return; }
    const chart = t.closest("[data-chart]");
    if (chart) { setChart(chart.dataset.chart); const sel = $("#bn-chart-key"); if (sel) sel.value = chart.dataset.chart; }
  });
  main.addEventListener("pointermove", (e) => { if (e.target.id === "bn-hit") chartPointer(e); });
  main.addEventListener("pointerleave", (e) => { if (e.target.id === "bn-hit") chartLeave(); }, true);
}

function setChart(key) {
  B.chartKey = key;
  prefs.set("bench-chart", key);
  drawChart();
  const hist = $("#bn-hist");
  if (hist) hist.innerHTML = historyTable(key);
  for (const row of document.querySelectorAll(".bn-figs tbody tr")) {
    const b = row.querySelector("[data-chart]");
    row.classList.toggle("on", !!b && b.dataset.chart === key);
  }
}

wire();
let resized = 0;
window.addEventListener("resize", () => {
  clearTimeout(resized);
  resized = setTimeout(() => { if (S.view === "bench" && B.build && B.detail) drawChart(); }, 150);
});

register({
  name: "bench", match: /^\/bench(?:\/([^/]+))?$/, show: showRecipe,
  leave: () => { clearTimeout(B.timer); clearTimeout(B.lineTimer); },
  status: onStatus,
  keys: { m: () => { location.hash = "#/bench"; } },
  help: [["m", "the bench: what DREAD made of a project built in many ways (dread bench), each build's figures and their trend; a build opened as the target; a measurement or the report run as a job"]],
  nav: { title: "Bench (m)", href: "#/bench", icon: ICON },
});
// (the same name: the bar's button stays lit on a build's page)
register({ name: "bench", match: /^\/bench\/([^/]+)\/([^/]+)$/, show: showBuild, leave: () => { clearTimeout(B.timer); clearTimeout(B.lineTimer); } });
