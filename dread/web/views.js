// dread gui's views of one function beyond its listing and C (the server's dread/guipages/views.py): where
// a failing case parts (dread trace), why code went unrun (dread uncovered), the changes to its C no case
// notices (dread mutate), its registers (dread.regflow, dread callconv). A strip under the function's
// header says what each view knows; one view opens beside the listing and the C (below 1600 px: above
// them) and marks its instructions in the listing and its lines in the C. Each view shows its tool's
// own record and runs the tool as a job where the record is missing or stale; the page computes nothing.
// Addresses: #/f/NAME/trace, #/f/NAME/trace/MODE/LABEL (a case's trace), #/f/NAME/uncovered,
// #/f/NAME/mutation, #/f/NAME/registers; #/f/NAME keeps the view that is open.

import { $, esc, api, hex, hex8, ago, when, toast, prefs, S, GUI, register, functionHook, fnLink, selectAddress } from "./app.js";

const VIEWS = {
  trace: { title: "Trace", about: "where a failing case parts: the original and the rebuilt image side by side to the first point where they differ (dread trace)" },
  uncovered: { title: "Uncovered", about: "why the cases leave code unrun: each region's way in, what decides it and where that comes from (dread uncovered)" },
  mutation: { title: "Mutation", about: "small changes to its C that no case notices (dread mutate)" },
  registers: { title: "Registers", about: "the registers it reads at its entry and those its callers read after it (dread regflow), measured by its cases (dread callconv)" },
};
const MODES = { test: "the image dread test runs", lifted: "the image dread lift --check builds (--lifted)",
  readable: "the image dread readable --check builds (--readable)" };
const LIVE = new Set(["queued", "running", "stopping"]);
const CASES_SHOWN = 8;          // failing cases the trace view lists before "all N"
const BESIDE = matchMedia("(min-width: 1600px)");     // the panel beside the listing and the C (views.css)
const FORCED = {    // how the run sent the missed way ended, in dread uncovered's words (its docstring)
  returns: "it returns: the condition alone stands in the way",
  faults: "it faults: the region needs more of its inputs than the condition",
  limit: "it runs into the instruction limit: the region needs more of its inputs than the condition",
  spins: "it spins: the region needs more of its inputs than the condition",
  stops: "a stub ends it",
  "touches the image": "it touches the image, which the generator drops",
};

const V = { name: null, open: null, summary: null, data: null, dataKey: null, drawn: null, seq: 0, trace: { mode: null, label: null },
  changed: null, scrolled: null, lmarks: [], kill: null, other: "", allCases: false, auto: false, started: new Set() };

// ---- small helpers ----

function plural(n, one, many) { return `${n} ${n === 1 ? one : many || one + "s"}`; }
function regs(list) { return list && list.length ? list.map((r) => `<code class="reg">${esc(r)}</code>`).join(" ") : `<span class="muted">none</span>`; }
// An address as a link: to its row in the listing where it lies in the function shown (name: the function
// shown), to the named function's page at it otherwise; a plain number where neither is known.
function addrLink(name, a) {
  if (a == null) return "";
  const h = S.current && S.current.header;
  const own = name === V.name && h && h.entry <= a && a < h.end;
  if (name === V.name && !own) return `<span class="mono">${hex8(a)}</span>`;
  return `<a class="mono${own ? " vw-goto" : ""}" href="${fnLink(name, a)}" data-at="${a}" title="${own ? "select it in the listing" : "in " + esc(name)}">${hex8(a)}</a>`;
}
function fnRef(name, a) {
  if (!name) return `<span class="muted">outside every function</span>`;
  return `<a class="mono" href="${fnLink(name, a != null && name !== V.name ? a : null)}">${esc(name)}</a>`;
}
function bitsText(mask) {    // the bits a read uses (a mask), as ranges: 0xff -> bits 0-7
  const out = [];
  let i = 0;
  while (i < 64) {
    if (mask / 2 ** i % 2 >= 1) {
      let j = i;
      while (j + 1 < 64 && mask / 2 ** (j + 1) % 2 >= 1) j++;
      out.push(i === j ? `${i}` : `${i}–${j}`);
      i = j + 1;
    } else i++;
  }
  return "bits " + out.join(", ");
}
function stateLine(job) {
  if (!job) return "";
  const words = job.state === "done" ? "done" : job.state === "failed" ? `failed${job.exit != null ? " (exit status " + job.exit + ")" : ""}` : job.state;
  return `<a href="#/jobs/${job.id}" title="${esc(job.line || "")}">job ${job.id}</a> ${esc(words)}`;
}
function live(job) { return !!job && LIVE.has(job.state); }
function runButton(label, view, extra = {}, title = "") {
  const data = Object.entries(extra).map(([k, v]) => ` data-${k}="${esc(v)}"`).join("");
  const off = GUI.readOnly ? ' disabled title="read-only (dread gui --read-only): it runs no jobs"' : title ? ` title="${esc(title)}"` : "";
  return `<button type="button" class="vw-run" data-view="${view}"${data}${off}>${esc(label)}</button>`;
}

// ---- the strip ----

function summaryWords(view, s) {
  if (!s) return ["", ""];
  if (view === "trace") {
    if (live(s.job)) return ["tracing…", "busy"];
    const parts = [];
    let tone = "";
    if (s.failing) { parts.push(`${s.failing} failing`); tone = "bad"; }
    if (s.traced) parts.push(`${s.traced} traced` + (!s.parts ? ", all agree" : s.agrees ? ` (${s.agrees} agree)` : ""));
    return [parts.join(" · ") || "no failing case", tone || (s.traced ? "good" : "")];
  }
  if (view === "uncovered") {
    if (live(s.job)) return ["finding why…", "busy"];
    if (s.missed == null) return ["no record of what ran", ""];
    const regions = s.regions != null ? ` · ${plural(s.regions, "region")}` : "";
    return s.missed ? [`${s.missed} never ran${regions}`, "warn"] : [`every instruction ran${regions}`, "good"];
  }
  if (view === "mutation") {
    if (!s.rewritten) return ["not C of src", ""];
    if (live(s.job)) return ["mutating…", "busy"];
    if (s.error) return ["failed", "bad"];
    if (s.survived == null) return ["not run", ""];
    return s.survived ? [`${s.survived} survive`, "warn"] : [`none survive, ${s.caught} caught`, "good"];
  }
  if (s.pending) return ["finding…", "busy"];
  if (!s.reads) return ["", ""];
  const disagree = s.measured && s.disagree ? ` · ${plural(s.disagree, "disagreement")}` : "";
  return [`reads ${s.reads.join(" ") || "nothing"} · kept ${s.kept.join(" ") || "nothing"}${disagree}`, s.disagree ? "warn" : ""];
}

function renderStrip() {
  const box = $("#fn-strip");
  if (!box) return;
  const s = V.summary;
  box.innerHTML = `<div class="vw-tabs">${Object.entries(VIEWS).map(([key, v]) => {
    const [words, tone] = summaryWords(key, s && s[key]);
    return `<button type="button" class="vw-tab${V.open === key ? " on" : ""}" data-view="${key}" aria-pressed="${V.open === key}" title="${esc(v.about)}">` +
      `<span class="vw-name">${v.title}</span><span class="vw-sum ${tone}">${esc(words)}</span></button>`;
  }).join("")}</div>`;
}

// ---- the panel ----

function renderPanel() {
  const panel = $("#fn-view"), body = $("#fn-body");
  if (!panel) return;
  panel.hidden = !V.open;
  body.classList.toggle("viewing", !!V.open);
  if (!V.open) { panel.innerHTML = ""; clearMarks(); return; }
  const v = VIEWS[V.open];
  const d = V.data && V.dataKey === key() ? V.data : null;
  let html = `<div class="vw-head"><h2>${v.title}</h2><button type="button" class="vw-close icon-btn" aria-label="Close the view" title="Close">×</button></div>
    <div class="vw-about">${esc(v.about)}</div>`;
  if (!d) html += `<div class="vw-wait">Reading…</div>`;
  else if (V.open === "trace") html += traceHTML(d);
  else if (V.open === "uncovered") html += uncoveredHTML(d);
  else if (V.open === "mutation") html += mutationHTML(d);
  else html += registersHTML(d);
  const top = panel.scrollTop;
  panel.innerHTML = html;
  panel.scrollTop = top;
  applyMarks(d);
}

// A record's state: when it was written, which inputs are newer, the job that writes it (while it runs,
// and where it ended without writing the record: the tools' exit status says more than how a run went,
// dread trace's 1 is a case that parts, dread mutate's a survivor).
function recordState(d, what, button) {
  const parts = [];
  if (d.job && live(d.job)) parts.push(stateLine(d.job));
  if (d.when) parts.push(`${what} <span title="${esc(when(d.when))}">${esc(ago(d.when))}</span>`);
  else if (!live(d.job)) parts.push(`no record yet`);
  if (d.job && !live(d.job) && d.job.started && (!d.when || d.when < d.job.started)) {
    parts.push(`${stateLine(d.job)}: it wrote no record (its log says why)`);
  }
  const stale = d.stale && d.stale.length ? `<div class="vw-stale">older than what it read: ${d.stale.slice(0, 4).map((f) => `<span class="mono">${esc(f)}</span>`).join(", ")}${d.stale.length > 4 ? ` and ${d.stale.length - 4} more` : ""} changed since</div>` : "";
  return `<div class="vw-state"><span>${parts.join(" · ")}</span><span class="grow"></span>${live(d.job) ? "" : button}</div>${stale}`;
}

// ---- trace ----

function traceHTML(d) {
  let html = "";
  const chosen = d.chosen || {};
  const cut = !V.allCases && d.cases.length > CASES_SHOWN;
  const shown = cut ? d.cases.filter((c, k) => k < CASES_SHOWN || (c.mode === chosen.mode && c.label === chosen.label)) : d.cases;
  const rows = shown.map((c) => {
    const r = c.record;
    const on = c.mode === chosen.mode && c.label === chosen.label;
    const state = live(c.job) ? `<span class="busy-word">${esc(c.job.state)}</span>`
      : !r ? `<span class="muted">not traced</span>`
      : r.error ? `<span class="bad">cannot be traced</span>`
      : r.agrees ? `<span class="good">agrees</span>` : `<span class="bad">parts</span>`;
    const stale = r && r.stale && r.stale.length ? ` <span class="warn" title="older than ${esc(r.stale.join(", "))}">stale</span>` : "";
    return `<button type="button" class="vw-case${on ? " on" : ""}" data-mode="${c.mode}" data-label="${esc(c.label)}" title="${esc(c.from ? "fails in " + c.from : "traced before")}: ${esc(MODES[c.mode])}">` +
      `<span class="mono">${esc(c.label)}</span><span class="vw-mode">${esc(c.mode)}</span>${state}${stale}</button>`;
  }).join("");
  const named = [...new Set(d.cases.map((c) => c.from).filter(Boolean))].join(", ");
  html += `<div class="vw-sub">${named ? `The failing cases (${esc(named)})` : d.cases.length ? "Cases traced before (no record says one fails)" : "The failing cases"}</div>`;
  html += rows ? `<div class="vw-cases">${rows}${cut ? `<button type="button" class="vw-more" title="every failing case the records name">all ${d.cases.length}</button>` : ""}</div>` : `<div class="muted vw-none">No record says a case of ${esc(d.name)} fails: dread test's tests.tsv, the lift check's and the readable check's verdicts.</div>`;
  for (const n of d.notes || []) html += `<div class="warn vw-note">${esc(n)}</div>`;
  html += `<form class="vw-other" id="vw-other"><input id="vw-label" type="text" placeholder="another case: its label (generated 0)" value="${esc(V.other)}" spellcheck="false" autocomplete="off" aria-label="Case label">` +
    `<select id="vw-mode" aria-label="Rebuilt image">${Object.keys(MODES).map((m) => `<option value="${m}"${m === (chosen.mode || "test") ? " selected" : ""}${m === "test" && d.modes && !d.modes.test ? ' disabled title="no rebuilt image: dread recon makes it"' : ""}>${m}</option>`).join("")}</select>` +
    `${GUI.readOnly ? "" : `<button type="submit">Trace</button>`}</form>`;
  if (!chosen.label) return html;
  const r = d.record;
  const button = runButton(r ? "Trace again" : "Trace", "trace", { mode: chosen.mode, label: chosen.label }, `dread trace ${d.name} '${chosen.label}'${chosen.mode === "test" ? "" : " --" + chosen.mode}`);
  html += `<div class="vw-chosen"><span class="mono">${esc(chosen.label)}</span> in ${esc(MODES[chosen.mode] || chosen.mode)}</div>`;
  html += recordState(Object.assign({}, r || {}, { job: d.job, when: r && r.when, stale: r && r.stale }), "traced", button);
  if (!r) return html;
  if (r.error) return html + `<div class="vw-block"><b class="bad">It cannot be traced:</b> ${esc(r.error)}</div>`;
  html += `<div class="vw-meta">${esc(d.name)} is built from ${esc(r.built || "the original's code")}${r.moved ? `, moved to ${hex(r.address)} behind a branch at ${hex(r.entry)}` : ""} · ${esc(r.what || "")} · <span class="mono">${esc(r.image || "")}</span>${r.note ? `<br><span class="muted">${esc(r.note)}</span>` : ""}</div>`;
  if (r.truncated) html += `<div class="warn vw-note">The original's run made more shared events than the trace keeps: it compared those.</div>`;
  if (r.agrees) {
    const a = r.agreed || {};
    return html + `<div class="vw-block good">No divergence: ${plural(a.calls || 0, "call")}, ${plural(a.log || 0, "log entry", "log entries")} and ${plural(a.stretches || 0, "stretch of stores", "stretches of stores")} agree, and so does the end.</div>`;
  }
  html += divergenceHTML(r.first, r);
  if (!r.hard && !r.same) html += `<div class="vw-block">Nothing dread test compares differs after it; dread test: the case agrees.</div>`;
  if (r.hard) {
    if (r.then && r.then.length) html += `<div class="vw-then">${r.then.map((t) => `<div>then: ${esc(t.what)} (${addrLink(V.name, t.original)})</div>`).join("")}</div>`;
    html += divergenceHTML(r.hard, r);
  }
  return html;
}

function instructionHTML(i, label) {
  if (!i) return "";
  const c = i.statement ? `<div class="vw-cline"><span class="muted">its statement in the C (${esc(i.statement.how)}),</span> <a class="mono vw-cref" data-path="${esc(i.statement.path)}" data-line="${i.statement.line}">${esc(i.statement.path)}:${i.statement.line}</a><pre>${esc(i.statement.text)}</pre></div>`
    : i.line ? `<div class="vw-cline"><span class="muted">from the C line</span> <a class="mono vw-cref" data-path="${esc(i.line.path)}" data-line="${i.line.line}">${esc(i.line.path)}:${i.line.line}</a>${i.line.text != null ? `<pre>${esc(i.line.text)}</pre>` : ""}</div>` : "";
  const goto = i.which === "original" && i.function ? addrLink(i.function, i.address) : `<span class="mono">${hex8(i.address)}</span>`;
  return `<div class="vw-insn"><div class="vw-insn-head"><span class="vw-which">${esc(label || i.which)}</span>${goto}<code>${esc(i.instruction)}</code></div>` +
    `<div class="vw-where">${esc(i.where)}</div>${c}</div>`;
}

function stackHTML(frames, which) {
  if (!frames || !frames.length) return `<span class="muted">-</span>`;
  return frames.map((f, k) => {
    const caller = k > 0 ? frames[k - 1].function : null;
    const from = f.from != null ? ` <span class="muted">from</span> ${which === "original" && caller ? addrLink(caller, f.from) : `<span class="mono">${hex(f.from)}</span>`}` : "";
    return `<span class="vw-frame">${fnRef(f.function)}${f.built ? ` <span class="muted">[${esc(f.built)}]</span>` : ""}${f.helper ? ` <span class="muted">(no shared point)</span>` : ""}${from}</span>`;
  }).join(`<span class="vw-sep">›</span>`);
}

function divergenceHTML(g, r) {
  let html = `<section class="vw-div"><h3>${esc(g.title)}</h3><div class="vw-what">${esc(g.what)}</div>`;
  if (g.stretch) html += `<div class="muted">in the stretch of stores after ${esc(g.stretch)}</div>`;
  html += `<div class="vw-pair">${instructionHTML(g.original, "original") || `<div class="vw-insn"><span class="vw-which">original</span> ${esc(g.original_none || "")}</div>`}` +
    `${instructionHTML(g.rebuilt, "rebuilt") || `<div class="vw-insn"><span class="vw-which">rebuilt</span> ${esc(g.rebuilt_none || "")}</div>`}</div>`;
  if (g.recent) html += `<div class="muted">the blocks the rebuilt image ran last: ${g.recent.map((a) => hex(a)).join(", ")}</div>`;
  if (g.writers.length) html += `<div class="vw-sub">where each run set it last</div><div class="vw-pair">${g.writers.map((w) => instructionHTML(w, w.which)).join("")}</div>`;
  for (const l of g.leaves) html += `<div class="vw-sub">where the ${esc(l.which)} run's path leaves the other's: it returns from ${fnRef(l.function)} (${hex(l.result)}), which the other run has not left</div>${instructionHTML(l.instruction, l.which)}`;
  if (g.stores) {
    html += `<div class="vw-sub">what differs at the stretch's end (each with the store that left it)</div><div class="vw-scroll"><table class="vw-table"><thead><tr><th>address</th><th>original</th><th>rebuilt</th></tr></thead><tbody>` +
      g.stores.map((s) => `<tr><td class="mono">${hex(s.address)}</td><td class="mono">${s.original == null ? "-" : `${esc(s.original)} <span class="muted">at</span> ${addrLink(V.name, s.original_site)}`}</td>` +
        `<td class="mono">${s.rebuilt == null ? "-" : `${esc(s.rebuilt)} <span class="muted">at ${hex(s.rebuilt_site)}</span>`}</td></tr>`).join("") +
      `</tbody></table></div>${g.stores_total > g.stores.length ? `<div class="muted">… ${g.stores_total} runs of bytes in all</div>` : ""}`;
  }
  for (const n of g.notes) html += `<div class="vw-note">${esc(n)}</div>`;
  html += `<div class="vw-sub">call stacks</div><div class="vw-stacks"><div><span class="vw-which">original</span> ${stackHTML(g.stacks.original, "original")}</div>` +
    `<div><span class="vw-which">rebuilt</span> ${stackHTML(g.stacks.rebuilt, "rebuilt")}</div></div>`;
  if (g.events_total) {
    html += `<div class="vw-sub">the last shared events before it (${g.events_shown} of ${g.events_total})</div><div class="vw-scroll"><table class="vw-table vw-events"><thead><tr><th>#</th><th>event</th><th>original</th><th>rebuilt</th></tr></thead><tbody>` +
      g.events.map((e) => `<tr><td class="num">${e.index}</td><td class="mono vw-ev">${esc(e.text)}</td><td>${addrLink(V.name, e.original)}</td><td class="mono">${hex(e.rebuilt)}</td></tr>`).join("") + `</tbody></table></div>`;
  }
  if (g.frame) html += frameHTML(g.frame);
  if (g.verdict) html += `<div class="vw-sub">dread test</div><div class="vw-verdict">${g.verdict.length ? g.verdict.map((v) => `<div class="mono">${esc(v)}</div>`).join("") : "the case agrees"}</div>`;
  return html + `</section>`;
}

function frameHTML(f) {
  if (f.error) return `<div class="vw-sub">frame</div><div class="muted">not shown (${esc(f.error)})</div>`;
  const head = `frame of ${esc(f.function)} (${esc(f.built || "the original's code")}) at that point: original sp0 ${hex(f.sp0)}, in use down to ${hex(f.inuse)}` +
    (f.rebuilt_sp0 != null ? `; rebuilt sp0 ${hex(f.rebuilt_sp0)}` : "; not on the rebuilt image's stack");
  const unset = ` <span class="muted">(not set)</span>`;
  return `<div class="vw-sub">${head}</div>${f.objects ? "" : `<div class="muted">no objects of its C; the words from sp0 down</div>`}` +
    `<div class="vw-scroll"><table class="vw-table vw-frame-t"><thead><tr><th>sp0−</th><th>object</th><th>original</th><th>rebuilt</th></tr></thead><tbody>` +
    f.rows.map((row) => {
      const differ = row.rebuilt != null && row.rebuilt !== row.original;
      return `<tr${differ ? ' class="differ"' : ""}><td class="mono">${hex(row.depth)}</td><td class="mono">${esc(row.label)}</td><td class="mono">${esc(row.original)}${row.original_unset ? unset : ""}</td>` +
        `<td class="mono">${row.rebuilt != null ? esc(row.rebuilt) + (row.rebuilt_unset ? unset : "") : row.rebuilt_missing ? `<span class="muted">(not in memory)</span>` : ""}</td></tr>`;
    }).join("") + `</tbody></table></div>${f.more ? `<div class="muted">…</div>` : ""}`;
}

// ---- uncovered ----

function uncoveredHTML(d) {
  let html = "";
  const c = d.coverage;
  if (c) {
    html += `<div class="vw-meta">${c.missed.length ? `<b class="warn">${plural(c.missed.length, "instruction")} never ran</b>` : "every instruction a path reaches ran"}` +
      `${c.unreached.length ? `, ${c.unreached.length} no path reaches` : ""} · ${esc(c.from)}${c.when ? `, <span title="${esc(when(c.when))}">${esc(ago(c.when))}</span>` : ""}</div>`;
  } else html += `<div class="vw-meta muted">No record of which instructions its cases run (dread test, dread lift --check).</div>`;
  const button = !d.verdicts ? `<span class="muted">dread uncovered reads a lift check's verdicts: run dread lift --check first</span>`
    : runButton(d.record ? "Find why again" : "Find why", "uncovered", {}, `dread uncovered ${d.name}`);
  html += recordState(d, "found", button);
  const r = d.record;
  if (!r) return html;
  if (r.absent) return html + `<div class="vw-block muted">The record holds no entry for ${esc(d.name)}.</div>`;
  html += `<div class="vw-meta">${esc(r.kind)}: ${r.missed} of ${r.instructions} instructions missed${r.reason ? ` · <span class="muted">${esc(r.reason)}</span>` : ""}</div>`;
  const n = r.cases;
  if (n) {
    const said = [];
    if (n.cases != null) said.push(`${plural(n.cases, "case")} read${n.stale ? " (cached under an older key)" : ""}`);
    if (n.failed) said.push(`generation: ${esc(n.failed)}`);
    for (const [k, words] of [["stubbed", "stubbed"], ["stops", "the cases end at"]]) {
      const got = Object.entries(n[k] || {});
      if (got.length) said.push(`${words} ${got.map(([f, m]) => `${fnRef(f)} <span class="muted">(${m})</span>`).join(", ")}`);
    }
    if ((n.polled || []).length) said.push(`polled ${n.polled.map(esc).join(", ")}`);
    if (n.hang) said.push(`${plural(n.hang, "hang case")}`);
    if (said.length) html += `<div class="vw-notes">${said.map((s) => `<div>${s}</div>`).join("")}</div>`;
    if ((n.refused || []).length) html += `<div class="vw-sub">the lift check's words</div><div class="vw-verdict">${n.refused.map((l) => `<div class="mono">${esc(l)}</div>`).join("")}</div>`;
  }
  if (r.runs) html += `<div class="muted">${plural(r.runs.runs, "run")} on the original: ${Object.entries(r.runs.ends || {}).map(([k, m]) => `${esc(k)} ${m}`).join(", ")}</div>`;
  if (!r.regions.length) return html + `<div class="vw-block good">No region: its cases run every instruction.</div>`;
  html += `<div class="vw-sub">${plural(r.regions.length, "region")} (click one to see it in the listing)</div>`;
  html += r.regions.map((g, k) => regionHTML(g, k)).join("");
  return html;
}

function regionHTML(g, k) {
  const w = g.ways[0];
  const key = g.key ? g.key[1] : g.source;
  let html = `<section class="vw-region" data-region="${k}" data-start="${parseInt(g.start, 16)}"><div class="vw-region-head"><span class="vw-n">${k + 1}</span>` +
    `${addrLink(V.name, parseInt(g.start, 16))}<span>${plural(g.instructions, "instruction")}</span><span class="vw-cause">${esc(g.cause)}</span>${key && key !== "-" ? `<span class="muted">${esc(key)}</span>` : ""}</div>` +
    `<div class="vw-what">${esc(g.text)}</div>`;
  if (w) {
    html += `<div class="vw-way"><span class="muted">way in at</span> ${addrLink(V.name, parseInt(w.at, 16))} <code>${esc(w.insn || "")}</code> <span class="muted">to</span> ${addrLink(V.name, parseInt(w.to, 16))}</div>`;
    if (w.deciders && w.deciders.length) html += `<div><span class="muted">decided by</span> ${w.deciders.map((a) => addrLink(V.name, parseInt(a, 16))).join(" ")}</div>`;
    if (w.sources && w.sources.length) html += `<ul class="vw-sources">${w.sources.map(([cls, text], i) => `<li${i ? "" : ' class="main"'}><span class="vw-cls">${esc(cls)}</span> ${esc(text)}</li>`).join("")}</ul>`;
    if (w.values) {
      const vals = Object.entries(w.values.registers || {}).map(([reg, vs]) => `<code class="reg">${esc(reg)}</code> ${vs.map(([v, m]) => esc(v) + (m > 1 && v !== "..." ? ` <span class="muted">×${m}</span>` : "")).join(", ")}`);
      html += `<div><span class="muted">runs there:</span> ${w.values.reached}${vals.length ? ` · ${vals.join("; ")}` : ""}</div>`;
    } else if (w.reached != null) html += `<div><span class="muted">runs there:</span> ${w.reached}</div>`;
    if (w.ends && Object.keys(w.ends).length) html += `<div><span class="muted">the runs at the call ended:</span> ${Object.entries(w.ends).map(([e, m]) => `${esc(e)} ${m}`).join(", ")}</div>`;
    if (w.forced) {
      const said = FORCED[g.forced] || "";
      html += `<div class="vw-forced"><span class="muted">sent the missed way:</span> ends ${esc(w.forced.end)}, ${w.forced.ran} of its instructions run${said ? `<div class="vw-hint">${esc(said)}</div>` : ""}</div>`;
    }
  }
  if (g.ways.length > 1) html += `<div class="muted">${plural(g.ways.length - 1, "more way")} in: ${g.ways.slice(1, 4).map((x) => esc(x.text)).join("; ")}</div>`;
  for (const n of g.notes || []) html += `<div class="vw-note">${esc(n)}</div>`;
  return html + `</section>`;
}

// ---- mutation ----

function mutationHTML(d) {
  if (!d.rewritten) return `<div class="vw-block muted">${esc(d.why)}</div>`;
  const killing = V.kill ?? !!(d.record && d.record.kill);
  const kill = `<label class="vw-check"><input type="checkbox" id="vw-kill"${killing ? " checked" : ""}${GUI.readOnly ? " disabled" : ""}> search inputs that catch the survivors (--kill)</label>`;
  let html = recordState(d, "mutated", kill + runButton(d.record ? "Run again" : "Run", "mutation", {}, `dread mutate ${d.name}`));
  const r = d.record;
  if (!r) return html + `<div class="vw-block muted">dread mutate changes ${esc(d.name)}'s C in ${esc(d.source.path)} one small change at a time and runs its cases against each: the changes no case notices are listed here.</div>`;
  if (r.error) return html + `<div class="vw-block"><b class="bad">dread mutate failed:</b> ${esc(r.error)}</div>`;
  const counts = [`${r.caught} caught${r.killed.length ? ` (${r.killed.length} by new written cases)` : ""}`, `<b class="${r.survived ? "warn" : "good"}">${r.survived} survived</b>`];
  if (r.unbuilt) counts.push(`${r.unbuilt} did not build`);
  if (r.argued.length) counts.push(`${r.argued.length} argued`);
  if (r.same_code) counts.push(`${r.same_code} compiled to the same code`);
  if (r.ran_away) counts.push(`${r.ran_away} of the caught ran away (over ${r.seconds} s)`);
  html += `<div class="vw-meta">${plural(r.changes, "change")} to <span class="mono">${esc(r.file)}:${r.first}–${r.last}</span>: ${counts.join(", ")}${r.static ? ` · a static helper: the cases of ${r.cases_of.map((f) => fnRef(f)).join(", ")} ran` : ""}</div>`;
  if (r.survivors.length) {
    html += `<div class="vw-sub">survivors: no case's result, memory, calls or log changes with them (each needs a case or an argument in argued.tsv)</div>`;
    html += r.survivors.map((s) => changeHTML(r, s, s.search === "none" ? "the --kill search found no input that tells the original from it"
      : s.search === "suspect" ? "the input the search found separates the original from the unchanged C too: a real difference to trace"
      : s.search === "refused" ? "the input the search found touches the image, which counts for nothing"
      : r.kill ? "not searched: the change does not build alone" : "no search (--kill not given)")).join("");
  }
  if (r.argued.length) html += `<div class="vw-sub">argued (argued.tsv)</div>` + r.argued.map((s) => changeHTML(r, s, s.argument)).join("");
  if (r.killed.length) html += `<div class="vw-sub">caught by new written cases</div>` + r.killed.map((s) => changeHTML(r, s, "a written case the search kept tells them apart")).join("");
  return html;
}

function changeHTML(r, s, why) {
  const removed = s.new === "";
  return `<section class="vw-change" data-line="${s.line}"><div class="vw-change-head"><a class="mono vw-cref" data-path="${esc(r.file)}" data-line="${s.line}">${esc(r.file)}:${s.line}</a><code>${esc(s.what)}</code></div>` +
    `<pre class="vw-diff"><span class="del">- ${esc(s.old)}</span>\n<span class="add">+ ${removed ? "" : esc(s.new)}</span>${removed ? `<span class="muted"> (the line removed)</span>` : ""}</pre>` +
    `<div class="vw-why">${esc(why)}</div>${s.input ? `<details><summary>the input</summary><pre class="vw-input">${esc(JSON.stringify(s.input))}</pre></details>` : ""}</section>`;
}

// ---- registers ----

function registersHTML(d) {
  const f = d.regflow;
  let html = "";
  if (!f || f.pending) html += `<div class="vw-block muted">The register analysis is running.</div>`;
  else if (f.error) html += `<div class="vw-block bad">dread.regflow failed: ${esc(f.error)}</div>`;
  else {
    const bits = (kind, r) => f.bits && f.bits[kind] && f.bits[kind][r] != null ? ` <span class="muted">${esc(bitsText(f.bits[kind][r]))}</span>` : "";
    const list = (kind, rs) => rs.length ? rs.map((r) => `<code class="reg">${esc(r)}</code>${bits(kind, r)}`).join(", ") : `<span class="muted">none</span>`;
    const yes = (v, words) => v == null ? `<span class="muted">not known (an older analysis)</span>` : v ? words : "no";
    html += `<div class="vw-sub">dread.regflow, from the code</div><dl class="vw-facts">
      <dt>reads at its entry</dt><dd>${list("reads", f.reads)}</dd>
      <dt>its callers read after it</dt><dd>${list("kept", f.kept)}</dd>
      <dt>leaves as it found them</dt><dd>${regs(f.preserved)}</dd>
      <dt>its own on every path</dt><dd>${f.overwritten == null ? `<span class="muted">not known</span>` : regs(f.overwritten)}</dd>
      <dt>second word a leftover</dt><dd>${yes(f.leftover, "yes: it holds only a constant or what a callee left there (no result of the source's)")}</dd>
      <dt>result computed for the return</dt><dd>${yes(f.computed, "yes: its code writes the result for the return alone")}</dd>
      <dt>variadic</dt><dd>${yes(f.variadic, "yes: it reads unnamed arguments (a save area of its argument registers, or a printf format it takes)")}</dd></dl>`;
    if (f.formats && f.formats.length) {
      html += `<div class="vw-sub">its calls of variadic functions: the argument registers each passes (dread.varargs, from a constant format)</div>`;
      html += `<div class="vw-scroll"><table class="vw-table"><thead><tr><th>at</th><th>callee</th><th>arguments</th><th>format at</th></tr></thead><tbody>` +
        f.formats.map((c) => `<tr><td>${addrLink(V.name, c.address)}</td><td>${fnRef(c.callee)}</td><td>${c.arguments == null ? `<span class="muted">not known: all of them</span>` : c.arguments}</td><td>${c.format == null ? "" : `<span class="mono">${hex8(c.format)}</span>`}</td></tr>`).join("") +
        `</tbody></table></div>`;
    }
    html += `<div class="vw-sub">its callers: each call site and what it reads after the call</div>`;
    html += f.callers.length ? `<div class="vw-scroll"><table class="vw-table"><thead><tr><th>caller</th><th>at</th><th>reads after it</th></tr></thead><tbody>` +
      f.callers.map((c) => `<tr><td>${c.address ? fnRef(c.caller, c.address) : `<span class="muted">${esc(c.caller)}</span>`}</td><td>${c.address ? `<a class="mono" href="${fnLink(c.caller, c.address)}">${hex8(c.address)}</a>` : ""}</td><td>${siteRegs(c.registers)}</td></tr>`).join("") +
      `</tbody></table></div>` : `<div class="muted vw-none">No caller reads a register of it after the call.</div>`;
    html += `<div class="vw-sub">its calls: what it reads of each callee after the call</div>`;
    html += f.calls.length ? `<div class="vw-scroll"><table class="vw-table"><thead><tr><th>at</th><th>callee</th><th>reads after it</th></tr></thead><tbody>` +
      f.calls.map((c) => `<tr><td>${addrLink(V.name, c.address)}</td><td>${fnRef(c.callee)}</td><td>${siteRegs(c.registers)}</td></tr>`).join("") +
      `</tbody></table></div>` : `<div class="muted vw-none">It reads no callee's register after a call.</div>`;
  }
  html += `<div class="vw-sub">dread callconv, measured by its cases</div>`;
  html += recordState(d, "measured", runButton(d.callconv ? "Measure again" : "Measure", "callconv", {}, `dread callconv ${d.name}`));
  const m = d.callconv;
  if (!m) return html + `<div class="muted vw-none">Each of its cases runs on the original once as it is and once more for each register with another value at the entry: what changes its behaviour is read, what differs at the return is changed.</div>`;
  if (m.absent) return html + `<div class="vw-block muted">No measurement: it has no cached cases for the image as it is now (dread lift --check makes them).</div>`;
  const found = Object.entries(m.found || {});
  html += `<dl class="vw-facts"><dt>cases</dt><dd>${m.cases}${m.touched ? ` <span class="muted">(${m.touched} left out: they touch a saved register's slot)</span>` : ""}</dd>
    <dt>reads</dt><dd>${regs(m.reads)}</dd><dt>changes</dt><dd>${regs(m.changes)}</dd>
    <dt>against regflow</dt><dd>${found.length ? found.map(([k, rs]) => `<div class="warn">${esc(k)} ${rs.map((r) => `<code class="reg">${esc(r)}</code>`).join(" ")}</div>`).join("") : `<span class="good">agrees</span>`}</dd>
    ${(m.variadic || []).length ? `<dt>variadic</dt><dd>${regs(m.variadic)} <span class="muted">(unnamed arguments of a variadic function: no use to regflow by design)</span></dd>` : ""}
    ${(m.unshown || []).length ? `<dt>no case showed</dt><dd>${regs(m.unshown)} <span class="muted">(regflow's reads: a path the cases do not take, or the analysis' caution)</span></dd>` : ""}</dl>`;
  return html;
}

function siteRegs(list) {
  return list.map((r) => `<code class="reg${r.always ? "" : " some"}" title="${r.always ? "read on every path after the call" : "read on some paths only"}">${esc(r.register)}</code>`).join(" ");
}

// ---- marks in the listing and the C ----

function clearMarks() {
  for (const [el, cls, titled] of V.lmarks) {
    el.classList.remove(...cls);
    if (titled) el.removeAttribute("title");
  }
  V.lmarks = [];
}

function markRow(address, cls, title) {
  const row = document.querySelector(`#listing .ln[data-a="${address}"]`);
  if (!row) return null;
  row.classList.add(...cls);
  const titled = !!title && !row.title;
  if (titled) row.title = title;
  V.lmarks.push([row, cls, titled]);
  return row;
}

// The instructions the open view is about, marked in the listing; on a view's first look, the first of
// them scrolled to.
function applyMarks(d) {
  clearMarks();
  if (!d || !V.open || !$("#listing")) return;
  let first = null;
  if (V.open === "trace" && d.record && !d.record.agrees && d.record.first) {
    for (const g of [d.record.first, d.record.hard]) {
      if (!g) continue;
      if (g.original && g.original.function === V.name) {
        if (first == null) first = g.original.address;
        markRow(g.original.address, ["vw-at"], g.title);
      }
      for (const w of g.writers || []) if (w.which === "original" && w.function === V.name) markRow(w.address, ["vw-set"], "set it last");
    }
  } else if (V.open === "uncovered" && d.record && d.record.regions) {
    d.record.regions.forEach((g, k) => {
      for (const a of g.missed) markRow(parseInt(a, 16), ["vw-unc"], `region ${k + 1}: ${g.cause}`);
      const w = g.ways[0];
      if (w) {
        markRow(parseInt(w.at, 16), ["vw-way"], `the way into region ${k + 1}`);
        for (const a of w.deciders || []) markRow(parseInt(a, 16), ["vw-decider"], `decides the way into region ${k + 1}`);
      }
      if (first == null) first = parseInt(g.start, 16);
    });
  } else if (V.open === "registers" && d.regflow && d.regflow.calls) {
    for (const c of d.regflow.calls) markRow(c.address, ["vw-call"], "reads " + c.registers.map((r) => r.register).join(", ") + " after the call");
  }
  const seen = V.open + ":" + V.name + ":" + (d.record && d.record.when);
  if (first != null && V.scrolled !== seen) {    // (beside the listing only: above it, the panel would go)
    V.scrolled = seen;
    if (BESIDE.matches) selectAddress(first, true);
  }
  markC();
}

// The C lines the open view names, in the C pane: a trace's C line where the pane shows that file, and
// the statements of the original's instruction where it shows this function's lifted C (the comment that
// names the instruction) or its readable C (the margin's address of a statement's first instruction);
// the mutation's survivors in its source.
function markC(pane = $("#cpane"), source = null) {
  if (!pane || !V.open || !V.data || V.dataKey !== key() || !S.current || S.current.header.name !== V.name) return;
  const s = source || (S.current && S.current.sources.find((x) => x.kind === S.tab));
  if (!s) return;
  const rows = [...pane.querySelectorAll(".code .cl")];
  const want = [];
  const d = V.data;
  if (V.open === "trace" && d.record && d.record.first) {
    for (const g of [d.record.first, d.record.hard]) {
      if (!g) continue;
      for (const i of [g.original, g.rebuilt]) {
        const c = i && (i.line || i.statement);
        if (c && s.path && samePath(c.path, s.path, d.record.image)) want.push([rows[c.line - (s.first || 1)], "vw-cl-at"]);
      }
      const a = g.original && g.original.function === V.name ? g.original.address : null;
      if (a == null) continue;
      if (s.kind === "lifted") {
        const note = `/* ${a.toString(16)}: `;
        for (const row of rows) if (row.textContent.includes(note)) want.push([row, "vw-cl-at"]);
      } else if (s.kind === "readable") {
        for (const row of rows) if (row.querySelector(`a.at[data-at="${a}"]`)) want.push([row, "vw-cl-at"]);
      }
    }
  } else if (V.open === "mutation" && d.record && d.record.survivors && s.path) {
    for (const x of d.record.survivors) if (samePath(d.record.file, s.path)) want.push([rows[x.line - (s.first || 1)], "vw-cl-mut"]);
  }
  for (const [row, cls] of want) if (row) row.classList.add(cls);
}

// Whether a path a record names (relative to the target, or to the rebuilt image's directory) is the
// file the C pane shows (relative to the target).
function samePath(path, shown, image = null) {
  if (path === shown) return true;
  if (image && image.includes("/")) return image.slice(0, image.lastIndexOf("/") + 1) + path === shown;
  return false;
}

// ---- loading ----

function key() {
  return V.name + "|" + V.open + (V.open === "trace" ? "|" + V.trace.mode + "|" + V.trace.label : "");
}

async function fetchView() {
  const name = V.name;
  if (V.open === "trace") {
    const params = { name };
    if (V.trace.mode && V.trace.label) Object.assign(params, { mode: V.trace.mode, label: V.trace.label });
    const d = await api("/api/views/trace", params);
    if (d.chosen && d.chosen.label) Object.assign(V.trace, { mode: d.chosen.mode, label: d.chosen.label });
    return d;
  }
  return api("/api/views/" + V.open, { name });
}

async function refresh() {
  const mine = ++V.seq, name = V.name;
  if (!name) return;
  let summary, data = null;
  try {
    [summary, data] = await Promise.all([api("/api/views", { name }), V.open ? fetchView() : Promise.resolve(null)]);
  } catch (e) {
    if (mine === V.seq) toast("The function's views: " + e.message, true);
    return;
  }
  if (mine !== V.seq || name !== V.name || S.view !== "function" || !$("#fn-strip")) return;
  const same = JSON.stringify([summary, data, key()]) === V.drawn && $("#fn-strip").firstChild;
  V.summary = summary;
  if (V.open) { V.data = data; V.dataKey = key(); }
  if (same) return;                     // (nothing new: the panel stays as it is, a click on it not lost)
  V.drawn = JSON.stringify([summary, data, key()]);
  renderStrip();
  renderPanel();
  autoTrace(data);
}

// A case chosen on purpose (its chip, the trace view opened by its tab or by an address) whose trace is
// missing is traced at once, once a case for each page: walking from function to function with the view
// open starts nothing.
function autoTrace(d) {
  const asked = V.auto;
  V.auto = false;
  if (!asked || V.open !== "trace" || !d || !d.chosen || !d.chosen.label || d.record || GUI.readOnly) return;
  if (live(d.job) || (d.job && d.job.started)) return;      // (a job ran for it: its own state says why)
  const id = [V.name, d.chosen.mode, d.chosen.label].join("|");
  if (V.started.has(id)) return;
  V.started.add(id);
  startJob({ view: "trace", mode: d.chosen.mode, label: d.chosen.label });
}

function show(d, ctx) {
  const name = d.header.name;
  V.drawn = null;                       // (the page drew the strip and the panel anew)
  if (V.name !== name) {
    V.summary = null;
    V.data = null;
    V.dataKey = null;
    V.trace = { mode: null, label: null };
    V.other = "";
    V.allCases = false;
    V.kill = null;
  }
  if (ctx.view && VIEWS[ctx.view]) {
    V.open = ctx.view;
    V.auto = ctx.view === "trace" && !ctx.keep;
    if (ctx.view === "trace" && ctx.arg) {
      const m = ctx.arg.match(/^(test|lifted|readable)\/(.+)$/);
      V.trace = m ? { mode: m[1], label: m[2] } : { mode: V.trace.mode || "test", label: ctx.arg };
    }
  }
  V.name = name;
  prefs.set("view-open", V.open);
  renderStrip();
  renderPanel();
  refresh();
}

function open(view) {
  V.open = V.open === view ? null : view;
  V.auto = V.open === "trace";
  prefs.set("view-open", V.open);
  const hash = V.open ? fnLink(V.name).replace(/^#\/f\/[^@]+/, (p) => p + "/" + V.open) : fnLink(V.name);
  history.replaceState(null, "", hash);
  renderStrip();
  renderPanel();
  if (V.open) refresh();
}

async function startJob(body) {
  try {
    const r = await api("/api/views/run", {}, Object.assign({ name: V.name }, body));
    toast(`Job ${r.id}: ${r.line}`);
  } catch (e) {
    toast(e.message, true);
  }
  refresh();
}

// ---- wiring ----

function wire() {
  const main = $("#main");
  main.addEventListener("click", (e) => {
    if (S.view !== "function") return;
    const tab = e.target.closest(".vw-tab");
    if (tab) { open(tab.dataset.view); return; }
    if (e.target.closest(".vw-close")) { open(V.open); return; }
    const goto = e.target.closest("a.vw-goto");
    if (goto) { e.preventDefault(); selectAddress(+goto.dataset.at, true); return; }
    const cref = e.target.closest("a.vw-cref");
    if (cref) { e.preventDefault(); showLine(cref.dataset.path, +cref.dataset.line); return; }
    const kase = e.target.closest(".vw-case");
    if (kase) {
      V.trace = { mode: kase.dataset.mode, label: kase.dataset.label };
      V.auto = true;
      history.replaceState(null, "", `#/f/${encodeURIComponent(V.name)}/trace/${encodeURIComponent(V.trace.mode)}/${encodeURIComponent(V.trace.label)}`);
      V.data = null;
      renderPanel();
      refresh();
      return;
    }
    if (e.target.closest(".vw-more")) { V.allCases = true; renderPanel(); return; }
    const region = e.target.closest(".vw-region");
    if (region && !e.target.closest("a")) { selectAddress(+region.dataset.start, true); return; }
    const run = e.target.closest(".vw-run");
    if (run && !run.disabled) {
      const view = run.dataset.view;
      if (view === "trace") startJob({ view, mode: run.dataset.mode, label: run.dataset.label });
      else if (view === "mutation") startJob({ view, kill: !!(V.kill ?? (V.data && V.data.record && V.data.record.kill)) });
      else startJob({ view });
    }
  });
  main.addEventListener("change", (e) => {
    if (e.target.id === "vw-kill") V.kill = e.target.checked;
  });
  main.addEventListener("input", (e) => {
    if (e.target.id === "vw-label") V.other = e.target.value;
  });
  main.addEventListener("submit", (e) => {
    if (e.target.id !== "vw-other") return;
    e.preventDefault();
    const label = $("#vw-label").value.trim(), mode = $("#vw-mode").value;
    if (!label) return toast("Which case: its label, as dread test prints it (generated 0).", true);
    V.trace = { mode, label };
    startJob({ view: "trace", mode, label });
  });
}

// A C line a record names: in the C pane where it shows that file (its tab chosen), else said.
function showLine(path, line) {
  const d = S.current;
  const image = V.data && V.data.record && V.data.record.image;
  const s = d && d.sources.find((x) => x.path && samePath(path, x.path, image));
  if (!s) return toast(`${path}:${line} is not the C the pane shows for ${V.name}: its text is above.`);
  if (S.tab !== s.kind) {
    S.tab = s.kind;
    const tab = document.querySelector(`#cpane .tab[data-tab="${s.kind}"]`);
    if (tab) tab.click();
  }
  const row = document.querySelectorAll("#cpane .code .cl")[line - (s.first || 1)];
  if (row) {
    row.scrollIntoView({ block: "center" });
    row.classList.remove("vw-flash");
    void row.offsetWidth;
    row.classList.add("vw-flash");
  }
}

wire();
V.open = VIEWS[prefs.get("view-open", null)] ? prefs.get("view-open", null) : null;

functionHook({
  show,
  c: (pane, source) => markC(pane, source),
  leave: () => { V.seq++; clearMarks(); },
});

// The views are part of the function page: registered for their share of /api/status alone (the jobs'
// stamp: a job that ends brings its record in), with an address that never matches.
register({
  name: "views", match: /(?!)/, show: () => {},
  status: (s) => {
    if (!s || s.changed === V.changed) return;
    const first = V.changed == null;
    V.changed = s.changed;
    if (!first && S.view === "function" && S.mode === "code" && V.name) refresh();
  },
});
