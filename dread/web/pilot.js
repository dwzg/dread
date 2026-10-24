// dread gui's pilot page (the server's dread/guipages/pilot.py): dread pilot started from a short form, a
// run followed live and stopped, the runs before it, what the answers cost a month, and the pilot's
// comparisons of names. The page computes nothing: the command line is the Run page's (/api/command-line),
// the estimate the pilot's dry run and an earlier run's tokens (dread.pilotlog estimate), a run's state the
// pilot's events folded on the server (dread.pilotlog Run), the costs dread.llm's usage ledger.
// Addresses: #/pilot (start, runs, cost, comparisons), #/pilot/ID (a run, followed while it goes on, frozen
// after its end), #/pilot/job/N (a job of dread pilot until its run begins).

import { $, esc, api, ago, toast, showError, S, GUI, prefs, register, enter, nextRender, isCurrent } from "./app.js";

const POLL = 1000;              // ms between looks at a run going on
const POLL_ELSEWHERE = 3000;    // ... at a run started elsewhere (no job of this page)
const ROWS = 500;               // rows of a run's list on screen (the newest)
const ITEMS = 4000;             // events of a run the page keeps for its filters
const LINE_WAIT = 150;          // ms after the last change before the command line is asked for
const ESTIMATE_WAIT = 700;      // ... and the estimate
const NAMED = ["proposed", "named", "accepted", "inputs"];
const ICON = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2.2 11.8a5.8 5.8 0 1 1 11.6 0"/><path d="M8 11.8 10.9 6.9"/><circle cx="8" cy="11.8" r="1" fill="currentColor" stroke="none"/></svg>`;

const P = { data: null, task: null, provider: null, budget: "", fn: "all", limit: "", names: "", batches: true,
  batchN: "", extra: {}, line: null, args: null, error: null, field: null, seq: 0, lineTimer: 0, estTimer: 0,
  estSeq: 0, estimate: null, runs: null, picked: [], index: undefined, month: null, usage: null,
  compare: null, cmp: { mode: "score", truth: "", judge: "", provider: "", against: "", vs: "" },
  cmpJob: null, cmpTimer: 0, cmpNext: null, cmpLog: "" };
const R = { id: null, next: null, items: [], dropped: 0, timer: 0, busy: false, data: null, final: false,
  outcomes: new Map(), fetched: 0, show: { refusals: true, events: true, every: false }, touched: false };
const J = { id: null, timer: 0 };

// ---- small helpers ----

function money(x, exact = false) {
  if (x == null || !isFinite(x)) return "–";
  if (exact) return Math.abs(x * 100 - Math.round(x * 100)) < 1e-6 ? "$" + x.toFixed(2) : "$" + x.toFixed(4);
  if (x >= 10) return "$" + x.toFixed(2);
  return "$" + x.toFixed(4);
}

function count(n) {
  return n == null ? "–" : Number(n).toLocaleString("en-US");
}

function pct(x) {
  return x == null || !isFinite(x) ? "–" : Math.round(100 * x) + " %";
}

function seconds(s) {
  if (s == null || !isFinite(s)) return "";
  s = Math.max(0, s);
  if (s < 10) return s.toFixed(1) + " s";
  if (s < 60) return Math.round(s) + " s";
  if (s < 3600) return Math.floor(s / 60) + " min " + Math.round(s % 60) + " s";
  return Math.floor(s / 3600) + " h " + Math.round((s % 3600) / 60) + " min";
}

function clock(t) {
  return t ? new Date(t * 1000).toLocaleTimeString() : "";
}

function day(t) {
  if (!t) return "";
  const d = new Date(t * 1000);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) + " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

// A bar of a share (0 to 1): an SVG (the page's Content-Security-Policy keeps style attributes out).
function bar(frac, cls = "") {
  const w = Math.max(0, Math.min(1, frac || 0)) * 100;
  return `<svg class="pl-bar ${cls}" viewBox="0 0 100 6" preserveAspectRatio="none" aria-hidden="true"><rect class="bg" width="100" height="6"/><rect class="fg" width="${w.toFixed(2)}" height="6"/></svg>`;
}

// The spend's bar: its colour changes as the spend nears the budget.
function spendClass(frac) {
  if (frac == null) return "plain";
  return frac >= 0.9 ? "over" : frac >= 0.75 ? "near" : "ok";
}

// How a run stands, in one word: running, gone, or how it ended.
function standing(r) {
  if (!r) return "";
  return r.status === "ended" ? r.how || "ended" : r.status;
}

function standingHTML(word) {
  const cls = { running: "s-running", done: "s-done", stopped: "s-stopped", failed: "s-failed", "budget reached": "s-budget", gone: "s-gone" }[word] || "";
  return `<span class="pl-state ${cls}">${esc(word || "?")}</span>`;
}

function fnLink(name) {
  return S.byName && S.byName.has(name) ? `<a class="fn" href="#/f/${encodeURIComponent(name)}" title="Its page">${esc(name)}</a>` : `<span class="fn">${esc(name || "?")}</span>`;
}

function setupLink() {
  return (GUI.pages || []).some((p) => p.name === "setup")
    ? `<a href="#/setup">the Setup page</a>`
    : `<span class="mono">llm.toml</span> (dread llm set, dread llm key)`;
}

// ---- #/pilot: start a run ----

function provider() {
  return P.data && P.data.providers.providers.find((p) => p.name === P.provider) || null;
}

function task() {
  return P.data && P.data.tasks.find((t) => t.name === P.task) || null;
}

// Whether a field of COMMON shows for the task: its help ties it to some tasks (dread.cliforms "when").
function applies(field) {
  return !!field && (!field.when || field.when.field !== "--task" || field.when.in.includes(P.task));
}

function providerFacts(p) {
  if (!p) return `no provider: ${setupLink()}`;
  const parts = [`<span class="mono">${esc(p.kind || "?")}</span> · <span class="mono">${esc(p.model || "?")}</span>`];
  if (p.prices) {
    parts.push(`$${p.prices.in} in, $${p.prices.out} out a million tokens`);
    if (p.batches) parts.push(`Message Batches at ${Math.round(100 * p.prices.batch)} %`);
    if (p.cache) parts.push(`prompt cache: read at $${+p.prices.cache_read.toFixed(4)}, written at $${+p.prices.cache_write.toFixed(4)}`);
  } else parts.push(`<span class="warn">no prices in llm.toml: no estimate, no budget</span>`);
  const k = p.key;                                  // (dread.llm key_status: never the key)
  if (k.needed || k.set) {
    parts.push(k.set ? `key: there (${esc(k.how || "")})`
      : `<span class="warn">key: not set${k.problem ? " (" + esc(k.problem) + ")" : ""}</span>`);
  }
  return parts.join(" · ") + ` · providers and keys: ${setupLink()}`;
}

function fieldWidget(f, v) {
  const at = `data-x="${esc(f.id)}"`;
  if (f.kind === "flag") return `<input type="checkbox" ${at}${v === true ? " checked" : ""} aria-label="${esc(f.id)}">`;
  if (f.kind === "bool") {
    const cur = v === true ? "true" : v === false ? "false" : "";
    const off = f.flags[1] || "--no-" + f.flags[0].replace(/^--/, "");
    return `<select ${at} aria-label="${esc(f.id)}"><option value=""${cur === "" ? " selected" : ""}>default (${esc(f.default || "per task")})</option>` +
      `<option value="true"${cur === "true" ? " selected" : ""}>${esc(f.flags[0])}</option><option value="false"${cur === "false" ? " selected" : ""}>${esc(off)}</option></select>`;
  }
  if (f.choices) return `<select ${at} aria-label="${esc(f.id)}"><option value="">default${f.default != null ? " (" + esc(f.default) + ")" : ""}</option>${f.choices.map((c) => `<option${String(v) === c ? " selected" : ""}>${esc(c)}</option>`).join("")}</select>`;
  const mode = f.type === "int" || f.type === "float" ? ' inputmode="decimal"' : "";
  const cls = f.type === "int" || f.type === "float" ? ' class="num"' : "";
  return `<input type="text" ${at}${cls}${mode} value="${esc(v == null ? "" : v)}" placeholder="${esc(f.default != null ? f.default : f.metavar || "")}" spellcheck="false" autocomplete="off" aria-label="${esc(f.id)}">`;
}

function taskFieldsHTML() {
  const t = task(), d = P.data;
  if (!t) return "";
  const ids = [...t.fields, ...["--jobs", "--thinking"].filter((id) => applies(d.fields[id]))];
  return ids.map((id) => {
    const f = d.fields[id];
    if (!f) return "";
    return `<div class="pl-row pl-opt"><label class="pl-k"><code>${esc(f.flags[0] || f.id)}</code></label>
      <div class="pl-v">${fieldWidget(f, P.extra[id])}</div><div class="pl-help">${esc(f.help || "")}</div></div>`;
  }).join("");
}

function startHTML() {
  const d = P.data;
  const tasks = d.tasks.map((t) => `<button type="button" data-task="${esc(t.name)}"${t.name === P.task ? ' class="on"' : ""}>${esc(t.name)}</button>`).join("");
  const options = d.providers.providers.map((p) => `<option value="${esc(p.name)}"${p.name === P.provider ? " selected" : ""}>${esc(p.name)} · ${esc(p.model || "?")}${p.prices ? ` · $${p.prices.in}/$${p.prices.out}` : " · no prices"}</option>`).join("");
  const fnHidden = P.task === "device";
  return `
    <section class="section pl-start" id="pl-start">
      <div class="section-head"><h2>Start a run</h2><span class="grow"></span>
        <a class="button" id="pl-every" href="#/run/pilot" title="The Run page's form of dread pilot, filled in with these: every option">Every option</a></div>
      ${d.providers.error ? `<p class="warn pl-note">${esc(d.providers.error)}: ${setupLink()}</p>` : ""}
      <div class="pl-form">
        <div class="pl-row"><span class="pl-k">Task</span><div class="pl-v"><div class="seg pl-tasks" id="pl-tasks" role="group" aria-label="Task">${tasks}</div></div>
          <div class="pl-help" id="pl-task-help"></div></div>
        <div class="pl-row"><label class="pl-k" for="pl-provider">Provider</label><div class="pl-v"><select id="pl-provider">${options || "<option value=''>none</option>"}</select></div>
          <div class="pl-help" id="pl-provider-facts"></div></div>
        <div class="pl-row"><label class="pl-k" for="pl-budget">Budget</label><div class="pl-v"><span class="pl-money">$<input id="pl-budget" inputmode="decimal" value="${esc(P.budget)}" placeholder="none" spellcheck="false" autocomplete="off"></span></div>
          <div class="pl-help" id="pl-budget-help"></div></div>
        <div class="pl-row" id="pl-fn-row"${fnHidden ? " hidden" : ""}><span class="pl-k">Functions</span><div class="pl-v pl-fns" role="radiogroup" aria-label="Functions">
            <label><input type="radio" name="pl-fn" value="all"${P.fn === "all" ? " checked" : ""}> all</label>
            <label><input type="radio" name="pl-fn" value="first"${P.fn === "first" ? " checked" : ""}> the first <input id="pl-limit" class="num" inputmode="numeric" value="${esc(P.limit)}" placeholder="N" aria-label="--limit"></label>
            <label class="pl-these"><input type="radio" name="pl-fn" value="these"${P.fn === "these" ? " checked" : ""}> these: <input id="pl-names" value="${esc(P.names)}" placeholder="names, separated by spaces" spellcheck="false" autocomplete="off" aria-label="the functions"></label></div>
          <div class="pl-help" id="pl-fn-help"></div></div>
        <div class="pl-row" id="pl-batch-row"><span class="pl-k">Batches</span><div class="pl-v"><label class="pl-check"><input type="checkbox" id="pl-batches"${P.batches ? " checked" : ""}> Message Batches for rounds of
            <input id="pl-batch-n" class="num" inputmode="numeric" value="${esc(P.batchN)}" placeholder="${esc((d.fields["--batch"] || {}).default || "100")}" aria-label="--batch"> questions or more</label></div>
          <div class="pl-help" id="pl-batch-help"></div></div>
        <div id="pl-task-fields">${taskFieldsHTML()}</div>
      </div>
      <div class="pl-line-box"><pre class="pl-line" id="pl-line" aria-live="polite"></pre><button type="button" id="pl-copy" title="Copy the command line">Copy</button></div>
      <div class="pl-error" id="pl-error"></div>
      <div class="pl-estimate" id="pl-estimate"></div>
      <div class="pl-actions">${startActions()}</div>
    </section>`;
}

function startActions() {
  const d = P.data;
  if (GUI.readOnly) return `<span class="pl-muted">read-only (dread gui --read-only): copy the command line and run it in a terminal</span>`;
  if (!d.runnable) return `<span class="warn">${esc(d.why || "this page starts no jobs")}</span>`;
  return `<span class="pl-muted">runs in <span class="mono">${esc(d.cwd)}</span> as a job; Stop gives it ${Math.round(d.grace)} s to cancel its batches and end</span>
    <button type="button" class="primary" id="pl-start-btn" disabled>Start</button>`;
}

function renderStartParts() {
  const t = task(), p = provider(), d = P.data;
  if (!t) return;
  for (const b of document.querySelectorAll("#pl-tasks button")) b.classList.toggle("on", b.dataset.task === P.task);
  const help = d.fields["--task"].help || "";
  $("#pl-task-help").textContent = taskWords(P.task) || help;
  $("#pl-provider-facts").innerHTML = providerFacts(p);
  const budget = $("#pl-budget");
  const noBudget = !t.budget ? t.why : p && !p.prices ? `${p.name} has no prices in llm.toml: a budget needs them` : null;
  budget.disabled = !!noBudget;
  $("#pl-budget-help").innerHTML = noBudget ? `<span class="warn">${esc(noBudget)}</span>`
    : "in US dollars; the run asks no question whose estimated cost would take its spend past it, keeps every answer and ends with “budget reached” (started again, it goes on where it stopped)";
  $("#pl-fn-row").hidden = P.task === "device";
  $("#pl-fn-help").textContent = P.task === "name" ? "all: the functions without a name (FUN_), callees first; a run started again asks only those without a proposal"
    : P.task === "readable" ? "all: the readable table's functions (dread readable --check made it); a run started again asks only those without names"
    : P.task === "review" ? "all: every name the image has, where code finds reasons against it" : "";
  const batchField = d.fields["--batch"];
  const batchRow = $("#pl-batch-row");
  batchRow.hidden = !applies(batchField);
  const canBatch = p && p.batches;
  $("#pl-batches").disabled = !canBatch;
  $("#pl-batch-n").disabled = !canBatch || !P.batches;
  $("#pl-batch-help").innerHTML = canBatch ? "half the price, answered within the hour mostly; off: one question at a time (--batch 0)"
    : `${esc(p ? p.name : "the provider")} has no Message Batches: it asks one at a time`;
  $("#pl-task-fields").innerHTML = taskFieldsHTML();
}

function taskWords(name) {
  return {
    name: "names for the functions without one (FUN_): each asked several times, callees first, the groups named first",
    readable: "names for the variables of the readable C (and the globals and structures they share)",
    globals: "names for the globals and structures alone, from every function's readable C",
    subsystems: "names for the groups of functions dread groups finds",
    device: "what kind of device the image is for, from its texts",
    input: "inputs that run the instructions the cases leave unrun; the emulator decides",
    write: "C for lifted functions, checked by the cases, the callers and the mutation check",
    review: "a no-confidence vote on existing names where code finds reasons against them",
  }[name] || "";
}

// The values of the pilot's form (the Run page's field ids) that this short form sets.
function formValues() {
  const d = P.data, t = task();
  const v = { "--task": P.task };
  if (P.provider) v["--provider"] = P.provider;
  if (P.budget.trim() && t && t.budget) v["--budget"] = P.budget.trim();
  if (P.task !== "device") {
    if (P.fn === "first" && P.limit.trim()) v["--limit"] = P.limit.trim();
    if (P.fn === "these" && P.names.trim()) v.names = P.names.trim();
  }
  if (applies(d.fields["--batch"])) {
    if (!P.batches) v["--batch"] = "0";
    else if (P.batchN.trim()) v["--batch"] = P.batchN.trim();
  }
  const ids = [...(t ? t.fields : []), ...["--jobs", "--thinking"].filter((id) => applies(d.fields[id]))];
  for (const id of ids) {
    const x = P.extra[id];
    if (x !== undefined && x !== "" && x !== null) v[id] = x;
  }
  return v;
}

function changed(now = false) {
  clearTimeout(P.lineTimer);
  P.lineTimer = setTimeout(askLine, now ? 0 : LINE_WAIT);
  clearTimeout(P.estTimer);
  P.estTimer = setTimeout(askEstimate, now ? 0 : ESTIMATE_WAIT);
  const box = $("#pl-estimate");
  if (box) box.classList.add("stale");             // (the estimate of other values, until the new one comes)
  prefs.set("pilot-form", { task: P.task, provider: P.provider, batches: P.batches });
}

async function askLine() {
  const seq = ++P.seq;
  let d;
  try {
    d = await api("/api/command-line", { name: "pilot", values: JSON.stringify(formValues()) });
  } catch (e) {
    d = { line: null, args: null, error: e.message, field: null };
  }
  if (seq !== P.seq || !$("#pl-line")) return;
  Object.assign(P, { line: d.line, args: d.args, error: d.error, field: d.field });
  $("#pl-line").textContent = d.line || "";
  $("#pl-error").textContent = d.error || "";
  const btn = $("#pl-start-btn");
  if (btn) btn.disabled = !!d.error || !d.args;
  const every = $("#pl-every");
  if (every) every.href = "#/run/pilot" + (d.args ? "@" + encodeURIComponent(JSON.stringify(d.args)) : "");
}

async function askEstimate() {
  const seq = ++P.estSeq, box = $("#pl-estimate");
  if (!box) return;
  if (!box.textContent.trim()) box.innerHTML = `<span class="pl-muted">estimating…</span>`;
  let e;
  try {
    e = await api("/api/pilot/estimate", {}, { values: formValues() });
  } catch (err) {
    if (seq === P.estSeq && $("#pl-estimate")) {
      $("#pl-estimate").innerHTML = `<span class="pl-muted">no estimate: ${esc(err.message)}</span>`;
      $("#pl-estimate").classList.remove("stale");
    }
    return;
  }
  if (seq !== P.estSeq || !$("#pl-estimate")) return;
  P.estimate = e;
  $("#pl-estimate").innerHTML = estimateHTML(e);
  $("#pl-estimate").classList.remove("stale");
}

// The estimate: the dry run's count, and the earlier run its price a function comes from (or why none).
function estimateHTML(e) {
  const same = `<div class="pl-est-why">The pilot prints the same: <span class="mono">${esc(e.line || "")}</span></div>`;
  if (e.functions != null && !e.asked) {
    return `<div class="pl-est-head"><b>nothing to ask</b> · ${e.functions ? `all ${count(e.functions)} functions have ${e.approximate ? "names already (asked again only where their C changed since)" : "an answer already"}: a run started again asks only what has none` : "no function to take"}</div>${same}`;
  }
  const facts = e.functions == null ? "" : ` · ${count(e.asked)} function${e.asked === 1 ? "" : "s"} to ask` +
    (e.already ? ` (${count(e.already)} with ${e.approximate ? "names already, asked again only where their C changed" : "an answer already"})`
      : e.already == null && e.task === "globals" ? " (their globals and structures)" : "");
  if (e.cost == null) {
    return `<div class="pl-est-head"><b>no estimate</b>${esc(facts)}</div><div class="pl-est-why">${esc((e.words || "").replace(/^no estimate( here| before the run)?: /, ""))}</div>${same}`;
  }
  const b = e.basis;
  const differ = (e.differ || []).map(([k, was, now]) => `${k} ${JSON.stringify(was)} there, ${JSON.stringify(now)} now`).join(", ");
  return `<div class="pl-est-head"><b>about ${money(e.cost)}</b>${esc(facts)}</div>
    <div class="pl-est-why">${money(e.per_function)} a function: the tokens a function of <a href="#/pilot/${encodeURIComponent(b.run)}">run ${esc(b.run)}</a>
      (${esc(b.provider || "?")}, ${count(b.asked)} functions asked, ${money(b.cost)}) at ${esc(P.provider || "the provider")}'s prices${e.batch_share ? `, ${pct(e.batch_share)} in Message Batches as there` : ""}.
      ${differ ? `<span class="warn">Its settings differ: ${esc(differ)}.</span> ` : ""}Rough: the shared stages (groups, globals and structures) are spread over its functions; --budget is what holds a run to a sum.</div>${same}`;
}

async function startRun() {
  const btn = $("#pl-start-btn");
  if (!btn || btn.disabled || GUI.readOnly) return;
  btn.disabled = true;
  try {
    const r = await api("/api/pilot/start", {}, { values: formValues() });
    toast(`Job ${r.id}: ${r.line}`);
    location.hash = "#/pilot/job/" + r.id;
  } catch (e) {
    $("#pl-error").textContent = e.message;
    btn.disabled = false;
  }
}

// ---- #/pilot: the runs before ----

function runsHTML(list) {
  if (!list) return `<div class="pl-none">…</div>`;
  if (!list.length) return `<div class="pl-none">No run on this target yet: start one above. Runs started in a terminal show here too (their events: BUILD/pilot/runs).</div>`;
  return `<div class="pl-runs-head" aria-hidden="true"><span></span><span>started</span><span>task</span><span>provider</span><span>how</span><span class="r">answers</span><span class="r">cost</span><span class="r">named</span><span class="r">took</span></div>` +
    list.map((r) => {
      const named = r.named || 0;
      const settled = Object.entries(r.settled || {}).map(([k, n]) => `${k} ${n}`).join(", ");
      return `<div class="pl-runrow${r.status === "running" ? " live" : ""}">
        <input type="checkbox" data-pick="${esc(r.run)}"${P.picked.includes(r.run) ? " checked" : ""} aria-label="pick ${esc(r.run)} to compare">
        <a href="#/pilot/${encodeURIComponent(r.run)}" class="pl-when" title="${esc(r.run)}">${esc(day(r.started))}</a>
        <span class="pl-task">${esc(r.task || "?")}${r.image ? ` <span class="pl-muted">${esc(r.image)}</span>` : ""}</span>
        <span class="pl-prov" title="${esc((r.provider || "?") + " (" + (r.model || "?") + ")")}">${esc(r.provider || "?")}</span>
        <span>${standingHTML(standing(r))}</span>
        <span class="r">${count(r.answers)}<span class="pl-lab"> answers</span></span>
        <span class="r" title="${r.budget != null ? "of a budget of " + money(r.budget, true) : "no budget"}">${money(r.cost)}${r.budget != null ? `<span class="pl-muted"> / ${money(r.budget, true)}</span>` : ""}</span>
        <span class="r" title="${esc(settled || "nothing settled")}">${count(named)}<span class="pl-lab"> named</span></span>
        <span class="r pl-muted">${esc(seconds(r.seconds))}</span>
      </div>`;
    }).join("");
}

async function refreshRuns() {
  let d;
  try {
    d = await api("/api/pilot/runs");
  } catch {
    return;
  }
  P.runs = d.runs;
  const box = $("#pl-runs");
  if (!box) return;
  box.innerHTML = runsHTML(P.runs) + (d.more ? `<div class="pl-none">${count(d.more)} older runs not listed (all of them: dread pilot --list)</div>` : "");
  renderLive();
  renderPair();
}

function renderLive() {
  const box = $("#pl-live");
  if (!box || !P.runs) return;
  const live = P.runs.filter((r) => r.status === "running");
  box.hidden = !live.length;
  box.innerHTML = live.map((r) => `<a class="pl-live-row" href="#/pilot/${encodeURIComponent(r.run)}">${standingHTML("running")}
      <b>${esc(r.task || "?")}</b> with ${esc(r.provider || "?")}, started ${esc(ago(r.started))}${r.job ? ` (job ${r.job.id})` : " (started elsewhere)"}: follow it</a>`).join("");
}

// ---- #/pilot: the cost per month ----

function usageHTML(u) {
  if (!u) return `<div class="pl-none">…</div>`;
  const options = (u.months.length ? u.months.map((m) => m.month) : [u.month]);
  if (!options.includes(u.month)) options.push(u.month);
  const select = `<select id="pl-month" aria-label="Month">${options.sort().reverse().map((m) => `<option${m === u.month ? " selected" : ""}>${esc(m)}</option>`).join("")}</select>`;
  const rows = u.providers.map((p) => `<tr>
      <td><b>${esc(p.name)}</b> <span class="pl-muted mono">${esc(p.model || "")}</span>${p.priced_as ? ` <span class="pl-muted pl-small" title="not in llm.toml: priced as the provider of the same model">priced as ${esc(p.priced_as)}</span>` : ""}</td>
      <td class="r">${count(p.answers)}</td>
      <td class="r" title="${count(p.batch)} answers of Message Batches">${pct(p.batch_share)}</td>
      <td class="r">${count(p.input)}</td>
      <td class="r" title="${count(p.cache_read)} tokens read from the prompt cache, ${count(p.cache_write)} written">${pct(p.cache_read_share)} / ${pct(p.cache_write_share)}</td>
      <td class="r">${count(p.output)}</td>
      <td class="r"><b>${p.priced ? money(p.cost) : "no prices"}</b></td>
      <td class="r pl-muted">${p.saved != null ? money(p.saved) : "–"}</td></tr>`).join("");
  const most = Math.max(0, ...u.months.map((m) => m.cost || 0));
  const months = u.months.length > 1 ? `<div class="pl-months">${u.months.slice().reverse().map((m) => `<button type="button" class="pl-month-row${m.month === u.month ? " on" : ""}" data-month="${esc(m.month)}">
      <span class="mono">${esc(m.month)}</span>${bar(most ? m.cost / most : 0, "plain")}<span class="r">${money(m.cost)}</span><span class="r pl-muted">${count(m.answers)} answers</span></button>`).join("")}</div>` : "";
  return `<div class="section-head"><h2>Cost per month</h2><span class="grow"></span>${select}</div>
    ${u.providers.length ? `<div class="pl-scroll"><table class="pl-usage">
      <thead><tr><th>provider</th><th class="r">answers</th><th class="r" title="the share of the answers that came from Message Batches (half price)">in batches</th>
        <th class="r">tokens in</th><th class="r" title="of the tokens in: read from the prompt cache (a tenth of the price) / written to it (a quarter more)">cache read / written</th>
        <th class="r">tokens out</th><th class="r">cost</th><th class="r" title="what the batches and the prompt cache saved against the standard prices">saved</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
      <div class="pl-total">${esc(u.month)}: <b>${money(u.total)}</b> where prices are known${u.saved ? `, ${money(u.saved)} saved by Message Batches and the prompt cache` : ""}${u.unpriced.length ? ` · without prices: ${esc(u.unpriced.join(", "))}` : ""}</div>`
    : `<div class="pl-none">No answer recorded in ${esc(u.month)}.</div>`}
    ${months}
    <div class="pl-muted pl-small">From the usage ledger <span class="mono">${esc(u.ledger)}</span> (every answer of every run on this machine, all targets), priced with today's <span class="mono">llm.toml</span>; the same: <span class="mono">dread pilot --usage ${esc(u.month)}</span></div>`;
}

async function refreshUsage(month = P.month) {
  let u;
  try {
    u = await api("/api/pilot/usage", month ? { month } : {});
  } catch (e) {
    const box = $("#pl-cost");
    if (box) box.innerHTML = `<div class="pl-none">The cost per month: ${esc(e.message)}</div>`;
    return;
  }
  P.usage = u;
  P.month = u.month;
  const box = $("#pl-cost");
  if (box) box.innerHTML = usageHTML(u);
}

// ---- #/pilot: comparisons ----

function compareHTML(c) {
  if (!c) return `<div class="pl-none">…</div>`;
  const m = P.cmp, names = c.names;
  const modes = [["score", "with the true names"], ["against", "with another copy's names"], ["replicates", "two settings, over copies"]];
  const judges = c.judges.map((j) => `<option value="${esc(j.name)}"${j.name === m.provider ? " selected" : ""}>${esc(j.name)} · ${esc(j.model || "?")}${j.prices ? ` · $${j.prices.in}/$${j.prices.out}` : ""}${j.key.needed && !j.key.set ? " (no key)" : ""}</option>`).join("");
  const copies = c.copies.length ? `<datalist id="pl-copies">${c.copies.map((p) => `<option value="${esc(p)}">`).join("")}</datalist>` : "";
  const truths = c.truths.length ? `<datalist id="pl-truths">${c.truths.map((p) => `<option value="${esc(p)}">`).join("")}</datalist>` : "";
  return `<div class="section-head"><h2>Compare names</h2></div>
    <p class="pl-note">dread pilot compares function names, the proposals of <span class="mono">--task name</span>: this image's
      <span class="mono">names.tsv</span> ${names.exists ? `has ${count(names.proposals)} proposals` : "does not exist yet (no naming run)"}.
      Pick two runs above to see whether they can be compared.</p>
    <div class="pl-pair" id="pl-pair"></div>
    <div class="pl-form">
      <div class="pl-row"><span class="pl-k">Compare</span><div class="pl-v"><div class="seg" id="pl-cmp-mode">${modes.map(([k, w]) => `<button type="button" data-mode="${k}"${k === m.mode ? ' class="on"' : ""}>${esc(w)}</button>`).join("")}</div></div>
        <div class="pl-help">${esc({ score: "--score TRUTH: by their words (asks nothing), and with a judge by its verdicts", against: "--against OTHER: function by function where the two differ, judged (one run against one shows a direction, not which setting is better)", replicates: "--against RUN... --vs RUN... with --grounded: each run judged once against the source, the means compared by a permutation test over runs (docs/decisions.md)" }[m.mode])}</div></div>
      <div class="pl-row"><label class="pl-k" for="pl-truth">True names</label><div class="pl-v"><input id="pl-truth" class="wide" list="pl-truths" value="${esc(m.truth)}" placeholder="an ELF with symbols, or a functions.tsv of a copy that has them" spellcheck="false" autocomplete="off">${truths}</div>
        <div class="pl-help">--score TRUTH</div></div>
      <div class="pl-row"><span class="pl-k">Judge</span><div class="pl-v pl-judge">
          <select id="pl-judge" aria-label="judge"><option value=""${m.judge === "" ? " selected" : ""}>none (asks nothing)</option><option value="grounded"${m.judge === "grounded" ? " selected" : ""}>against the source (--grounded)</option><option value="judge"${m.judge === "judge" ? " selected" : ""}>against the true name (--judge)</option></select>
          <select id="pl-judge-provider" aria-label="the judge's provider"${m.judge ? "" : " disabled"}>${judges}</select></div>
        <div class="pl-help">a judge asks a provider: it costs, and the run shows in the runs above (its answers kept: a run judged before costs nothing again)</div></div>
      <div class="pl-row"${m.mode === "score" ? " hidden" : ""}><label class="pl-k" for="pl-against">${m.mode === "replicates" ? "Runs of one setting" : "Another run"}</label><div class="pl-v"><input id="pl-against" class="wide" list="pl-copies" value="${esc(m.against)}" placeholder="its names.tsv${m.mode === "replicates" ? " (several: separated by spaces)" : ""}" spellcheck="false" autocomplete="off">${copies}</div>
        <div class="pl-help">--against${c.copies.length ? `: ${c.copies.length} other cop${c.copies.length === 1 ? "y" : "ies"} beside this target offered` : ""}</div></div>
      <div class="pl-row"${m.mode === "replicates" ? "" : " hidden"}><label class="pl-k" for="pl-vs">Runs of the other</label><div class="pl-v"><input id="pl-vs" class="wide" list="pl-copies" value="${esc(m.vs)}" placeholder="their names.tsv, separated by spaces" spellcheck="false" autocomplete="off"></div>
        <div class="pl-help">--vs; five runs a side settle a setting, four at least</div></div>
    </div>
    <div class="pl-error" id="pl-cmp-error"></div>
    <div class="pl-actions">${GUI.readOnly ? `<span class="pl-muted">read-only: nothing is started here</span>` : `<button type="button" class="primary" id="pl-cmp-btn">Compare</button>`}</div>
    <div class="pl-cmp-result" id="pl-cmp-result"${P.cmpJob ? "" : " hidden"}></div>`;
}

async function refreshCompare() {
  let c;
  try {
    c = await api("/api/pilot/compare");
  } catch {
    return;
  }
  P.compare = c;
  if (!P.cmp.provider && c.judges.length) {        // (the start form's provider where it can judge, else the cheapest)
    P.cmp.provider = (c.judges.find((j) => j.name === P.provider) || c.judges[0]).name;
  }
  if (!P.cmp.truth && c.truths.length) P.cmp.truth = c.truths[0];
  renderCompare();
}

function renderCompare() {
  const box = $("#pl-compare");
  if (!box) return;
  box.innerHTML = compareHTML(P.compare);
  renderPair();
  if (P.cmpJob) renderCmpResult();
}

async function renderPair() {
  const box = $("#pl-pair");
  if (!box) return;
  if (P.picked.length !== 2) {
    box.innerHTML = P.picked.length ? `<span class="pl-muted">one more run to pick</span>` : "";
    return;
  }
  try {
    const c = await api("/api/pilot/compare", { a: P.picked[0], b: P.picked[1] });
    if (!$("#pl-pair") || P.picked.length !== 2) return;
    $("#pl-pair").innerHTML = `<div class="pl-pair-box"><b>${c.pair.can ? "Can be compared" : "Not comparable as they are"}:</b> ${esc(c.pair.why)}</div>`;
  } catch (e) {
    box.textContent = e.message;
  }
}

async function startCompare() {
  const m = P.cmp;
  const body = { mode: m.mode, truth: m.truth, judge: m.judge, provider: m.judge ? m.provider : "",
    against: m.against.split(/\s+/).filter(Boolean), vs: m.vs.split(/\s+/).filter(Boolean) };
  $("#pl-cmp-error").textContent = "";
  try {
    const r = await api("/api/pilot/compare/start", {}, body);
    P.cmpJob = r;
    P.cmpNext = null;
    P.cmpLog = "";
    toast(`Job ${r.id}: ${r.line}`);
    renderCmpResult();
    pollCompare();
  } catch (e) {
    $("#pl-cmp-error").textContent = e.message;
  }
}

function renderCmpResult() {
  const box = $("#pl-cmp-result"), r = P.cmpJob;
  if (!box || !r) return;
  box.hidden = false;
  const lines = P.cmpLog.split("\n");
  // (the pilot's result lines: its summary, not the run's id or the questions' progress)
  const result = lines.filter((l) => l.startsWith("dread pilot --score") || (l.startsWith("dread pilot:") && !l.startsWith("dread pilot: run ")));
  box.innerHTML = `<div class="pl-cmp-head"><a href="#/jobs/${r.id}">Job ${r.id}</a> <span class="pl-state s-${esc(r.state)}">${esc(r.state)}</span>
      <span class="mono pl-small">${esc(r.line || "")}</span></div>
    ${result.length ? `<pre class="pl-result">${esc(result.join("\n"))}</pre>` : ""}
    <details${result.length ? "" : " open"}><summary>its log</summary><pre class="pl-log">${esc(lines.slice(-60).join("\n"))}</pre></details>`;
}

async function pollCompare() {
  clearTimeout(P.cmpTimer);
  const r = P.cmpJob;
  if (!r || S.view !== "pilot") return;
  try {
    const log = await api("/api/job-log", P.cmpNext == null ? { id: r.id, from: 0 } : { id: r.id, from: P.cmpNext });
    P.cmpLog += log.text;
    P.cmpNext = log.next;
    if (log.state !== r.state) P.cmpJob = await api("/api/job", { id: r.id });
  } catch { /* the next look */ }
  renderCmpResult();
  if (["queued", "running", "stopping"].includes(P.cmpJob.state)) P.cmpTimer = setTimeout(pollCompare, POLL);
  else refreshCompare();
}

// ---- #/pilot ----

function pilotHTML() {
  const head = `<div class="pl-head"><h1>Pilot</h1><span class="pl-muted">dread pilot: questions to a language model, each answer checked by the tools; a run started here is a job, followed live</span></div>`;
  if (!P.data.target) {                           // (a server without a target: the Setup page makes or opens one)
    return `<div class="pl" id="pl">${head}
      <section class="section pl-start"><div class="section-head"><h2>No target yet</h2></div>
        <p class="pl-note">A pilot run asks about a target's functions: ${(GUI.pages || []).some((p) => p.name === "setup") ? `the <a href="#/setup">Setup page</a>` : "dread new"} makes a target
          from an ELF or a raw image, or opens one; its runs, their estimates and their names show here then.</p></section>
      <section class="section pl-cost" id="pl-cost">${usageHTML(P.usage)}</section>
    </div>`;
  }
  return `<div class="pl" id="pl">${head}
      <div class="pl-live" id="pl-live" hidden></div>
      <div id="pl-start-box"></div>
      <section class="section" id="pl-runs-box"><div class="section-head"><h2>Runs</h2><span class="grow"></span><span class="pl-muted pl-small">newest first; pick two to compare</span></div><div class="pl-runs" id="pl-runs">${runsHTML(P.runs)}</div></section>
      <section class="section pl-cost" id="pl-cost">${usageHTML(P.usage)}</section>
      <section class="section pl-compare" id="pl-compare">${compareHTML(P.compare)}</section>
    </div>`;
}

async function showPilot(match, keep) {
  const again = S.view === "pilot" && $("#pl");
  if (keep && again) { refreshRuns(); return; }       // (a reload: the form stays as typed)
  const mine = nextRender();
  let d;
  try {
    d = await api("/api/pilot");
  } catch (e) {
    if (isCurrent(mine)) showError(e.message);
    return;
  }
  if (!isCurrent(mine)) return;
  const main = enter("pilot", "Pilot");
  P.data = d;
  const saved = prefs.get("pilot-form", {}) || {};
  const names = d.tasks.map((t) => t.name);
  if (!P.task) P.task = names.includes(saved.task) ? saved.task : d.fields["--task"].default || names[0];
  const provs = d.providers.providers.map((p) => p.name);
  if (!P.provider || !provs.includes(P.provider)) P.provider = provs.includes(saved.provider) ? saved.provider : d.providers.default || provs[0] || null;
  if (saved.batches === false) P.batches = false;
  main.innerHTML = pilotHTML();
  main.scrollTop = 0;
  refreshUsage();
  if (!d.target) return;
  $("#pl-start-box").outerHTML = startHTML();
  renderStartParts();
  changed(true);
  refreshRuns();
  refreshCompare();
  if (P.cmpJob && ["queued", "running", "stopping"].includes(P.cmpJob.state)) pollCompare();    // (one started before)
}

// ---- #/pilot/ID: a run ----

function runHTML(id) {
  return `<div class="pl-run" id="pl-run">
      <div class="fn-head compact pl-run-head" id="pl-run-head"><div class="fn-title"><h1 class="pl-run-id">${esc(id)}</h1></div></div>
      <div class="pl-run-body">
        <div class="pl-cards" id="pl-cards"></div>
        <div class="pl-banner" id="pl-banner" hidden></div>
        <section class="section pl-batches" id="pl-batches" hidden></section>
        <section class="section pl-warnings" id="pl-warnings" hidden></section>
        <section class="section pl-names">
          <div class="section-head"><h2>As it goes</h2><span class="grow"></span>
            <label class="pl-check"><input type="checkbox" data-show="refusals"${R.show.refusals ? " checked" : ""}> refusals</label>
            <label class="pl-check"><input type="checkbox" data-show="events"${R.show.events ? " checked" : ""}> stages and batches</label>
            <label class="pl-check"><input type="checkbox" data-show="every"${R.show.every ? " checked" : ""}> every answer</label></div>
          <div class="pl-items" id="pl-items"></div>
          <div class="pl-muted pl-small" id="pl-items-note"></div>
        </section>
      </div>
    </div>`;
}

function headHTML(d) {
  const st = d.state, job = d.job, stop = d.stop || {};
  const word = standing(st);
  const buttons = [];
  if (!GUI.readOnly && st.status === "running") {
    if (stop.can) buttons.push(`<button type="button" id="pl-stop" title="SIGTERM to its job: it cancels its Message Batches, waits for the questions being asked and ends (“stopped”)${job ? `; SIGKILL after ${Math.round(job.grace || 60)} s` : ""}">Stop</button>`);
    else if (job && job.state === "stopping") buttons.push(`<button type="button" disabled>Stopping…</button>`);
  }
  if (!GUI.readOnly && st.status === "gone" && st.waiting && st.waiting.length) buttons.push(`<button type="button" id="pl-cancel-batches" title="dread llm batches --cancel, as a job">Cancel its batches</button>`);
  if (job) buttons.push(`<a class="button" href="#/run/pilot@${job.id}" title="The Run page's form of dread pilot, filled in from this run's command line">Change and run</a>`);
  const facts = [`<b>${esc(st.task || "?")}</b>${st.image ? " on " + esc(st.image) : ""}`, `${esc(st.provider || "?")} (<span class="mono">${esc(st.model || "?")}</span>)`];
  if (st.started) facts.push(`started ${esc(clock(st.started))} (${esc(ago(st.started))})`);
  const took = st.status === "ended" ? st.seconds : st.started ? Date.now() / 1000 - st.started : null;
  if (took != null) facts.push((st.status === "ended" ? "took " : "for ") + esc(seconds(took)));
  if (job) facts.push(`<a href="#/jobs/${job.id}">job ${job.id}</a> (its log)`);
  else facts.push(`not started from this page${st.pid ? ` (pid ${st.pid})` : ""}`);
  if (st.functions != null) facts.push(`${count(st.functions)} functions planned`);
  if (st.budget != null) facts.push(`budget ${money(st.budget, true)}`);
  const why = !stop.can && st.status === "running" && stop.why ? `<div class="pl-muted pl-small">${esc(stop.why)}</div>` : "";
  return `<div class="fn-title"><h1 class="pl-run-id">${esc(st.run || R.id)}</h1>${standingHTML(word)}<span class="grow"></span>${buttons.join("")}</div>
    <div class="pl-facts">${facts.join(" · ")}</div>${why}`;
}

function cardsHTML(st) {
  const stage = st.stages && st.stages.length ? st.stages[st.stages.length - 1] : null;
  const stages = (st.stages || []).map((x) => `<span class="pl-chip${"ended" in x ? "" : " on"}" title="${esc(x.stage)}: ${x.answered} of ${x.questions == null ? "?" : (x.at_most ? "at most " : "") + x.questions} questions answered${"ended" in x ? ", ended" : ""}">${esc(x.stage)} ${x.answered}${x.questions != null ? "/" + (x.at_most ? "≤" : "") + x.questions : ""}</span>`).join("");
  const stageWord = st.stage ? st.stage + (st.status === "running" ? "" : " (not finished)")
    : st.status === "running" ? (stage ? "between stages" : "preparing") : stage ? "all ended" : "none begun";
  const planned = st.planned || 0, answered = st.answered || 0;
  const qFrac = planned ? answered / planned : 0;
  const budget = st.budget;
  const sFrac = budget ? st.spent / budget : null;
  const waiting = st.waiting && st.waiting.length;
  return `
    <div class="card pl-card"><h2>Stage</h2><div class="pl-big">${esc(stageWord)}</div><div class="pl-chips">${stages || `<span class="pl-muted">no stage begun yet</span>`}</div></div>
    <div class="card pl-card"><h2>Questions</h2><div class="pl-big">${count(answered)} <span class="pl-of">of ${count(planned)}</span></div>${bar(qFrac, "plain")}
      <div class="pl-small pl-muted">answered of those planned so far; ${count(st.answers)} answers${st.batch_answers ? `, ${count(st.batch_answers)} from Message Batches` : ""}</div></div>
    <div class="card pl-card"><h2>Spend</h2><div class="pl-big">${money(st.spent)}${budget != null ? ` <span class="pl-of">of ${money(budget, true)}</span>` : ""}</div>${bar(budget ? sFrac : 0, spendClass(sFrac))}
      <div class="pl-small pl-muted">${budget != null ? `${pct(sFrac)} of the budget` : "no budget"}${st.budget_reached ? " · <b>budget reached</b>" : ""}</div></div>
    <div class="card pl-card"><h2>Pace</h2><div class="pl-big">${st.rate ? st.rate.toFixed(2) + ' <span class="pl-of">questions/s</span>' : "–"}</div>
      <div class="pl-small pl-muted">${st.left != null ? `about ${esc(seconds(st.left))} left${waiting ? " (rough while a batch is out: its answers come at once)" : ""}` : st.status === "running" ? "time left: not known yet" : ""}${st.last_event != null && st.status === "running" ? ` · last event ${esc(seconds(st.last_event + (Date.now() - R.fetched) / 1000))} ago` : ""}</div></div>`;
}

function batchesHTML(st) {
  const list = st.waiting || [];
  if (!list.length) return "";
  const late = (Date.now() - R.fetched) / 1000;
  return `<div class="section-head"><h2>Batches ${st.status === "running" ? "waiting" : "not seen to end"}</h2></div>
    ${list.map((b) => `<div class="pl-batch"><span class="mono pl-batch-id" title="${esc(b.id)}">${esc(b.id)}</span>
      <span>${count(b.requests)} questions</span><span class="pl-muted">${esc(b.status || "")}</span>
      <span>${esc(seconds(b.age + (st.status === "running" ? late : 0)))} out</span>
      <span class="pl-muted">${b.answered == null ? "none answered yet" : count(b.answered) + " answered"}</span></div>`).join("")}
    ${st.status === "gone" ? `<div class="warn pl-small">Its process is gone without an end: these batches may still run at the provider, billed for every answer (cancel them above, or dread llm batches --provider ${esc(st.provider || "NAME")} --cancel ID).</div>` : ""}`;
}

function bannerHTML(st) {
  const parts = [];
  if (st.budget_reached) parts.push(`<div><b>Budget reached</b> at ${esc(clock(st.budget_reached.t))}: ${esc(st.budget_reached.words || "")}</div>`);
  if (st.status === "ended") {
    const e = st.end || {};
    const totals = [e.answers != null ? `${count(e.answers)} answers` : "", e.batch ? `${count(e.batch)} from Message Batches` : "",
      e.input != null ? `${count(e.input)} tokens in, ${count(e.output)} out` : "", e.cost != null ? money(e.cost) : "",
      e.asked != null ? `${count(e.asked)} questions asked` : "", e.held ? `${count(e.held)} held back by the budget` : ""].filter(Boolean);
    const settled = Object.entries(e.settled || {}).map(([k, n]) => `${n} ${k}`).join(", ");
    parts.push(`<div><b>Ended: ${esc(st.how || "?")}</b>${st.error ? ` (${esc(st.error)})` : ""} at ${esc(clock(st.ended))} after ${esc(seconds(st.seconds))}${totals.length ? ": " + esc(totals.join(", ")) : ""}${settled ? `; functions: ${esc(settled)}` : ""}.</div>`);
    if (st.how === "budget reached" || st.how === "stopped") parts.push(`<div class="pl-muted pl-small">The tables are as for a run that ends: a run started again goes on where this one stopped.</div>`);
  } else if (st.status === "gone") {
    parts.push(`<div><b>Gone</b>: no end event, and its process (pid ${esc(st.pid)}) is no more (killed outright?); its last event ${esc(seconds(st.last_event))} ago.</div>`);
  }
  return parts.join("");
}

function warningsHTML(st) {
  const list = st.warnings || [];
  if (!list.length) return "";
  return `<div class="section-head"><h2>Warnings and errors</h2></div>${list.map((w) => `<div class="pl-warn-row"><span class="t">${esc(clock(w.t))}</span> ${esc(w.text)}</div>`).join("")}`;
}

// Whether the run asks each function several times and keeps the name most answers agree with (a vote):
// then an answer is one sample, and the list shows the function settled; else each answer is the function's.
function voting(st) {
  const samples = (st.settings || {}).samples;
  return ["name", "subsystems", "device", "review"].includes(st.task) && (samples == null || samples > 1);
}

// What an event is on the list: null for one the filters leave out.
function itemKind(e) {
  if (e.event === "settled") return "settled";
  if (e.event === "batch" && e.state === "looked" && e.status === "ended") return null;    // (its "ended" follows)
  if (e.event === "outcome") return ["accepted", "answered"].includes(e.result) ? (R.show.every ? "answer" : null) : (R.show.refusals || R.show.every ? "refusal" : null);
  if (["warning", "error", "budget", "end"].includes(e.event)) return "event";
  return R.show.events ? "event" : null;
}

function namesText(names, most = 6) {
  if (!names || typeof names !== "object") return names == null ? "" : String(names);
  const pairs = Object.entries(names);
  return pairs.slice(0, most).map(([k, v]) => `${k} → ${v}`).join(", ") + (pairs.length > most ? `, … ${pairs.length - most} more` : "");
}

function eventText(e) {
  if (e.event === "stage") {
    if (e.state === "start") return `stage ${e.stage}: ${e.at_most ? "at most " : ""}${e.questions != null ? count(e.questions) : "?"} questions${e.functions != null ? `, ${count(e.functions)} functions` : ""}`;
    const rest = Object.entries(e).filter(([k]) => !["t", "event", "state", "stage"].includes(k)).map(([k, v]) => `${k} ${typeof v === "object" ? JSON.stringify(v) : v}`).join(", ");
    return `stage ${e.stage} ended${rest ? ": " + rest : ""}`;
  }
  if (e.event === "batch") {
    if (e.state === "submitted") return `batch ${e.id} submitted: ${count(e.requests)} questions (estimated ${money(e.estimate)})`;
    if (e.state === "looked") return `batch ${e.id} ${e.status || ""} after ${seconds(e.age)}`;
    if (e.state === "ended") return `batch ${e.id} ended after ${seconds(e.seconds)}: ${count(e.succeeded)} succeeded, ${count(e.errored)} errored${e.canceled ? `, ${count(e.canceled)} cancelled` : ""}`;
    if (e.state === "cancelled") return `batch ${e.id} cancelled (${e.why || "?"})`;
    return `a batch of ${count(e.requests)} questions failed: ${e.error || "?"}`;
  }
  if (e.event === "budget") return `budget reached: ${e.words || ""}`;
  if (e.event === "end") return `the run ended: ${e.how}${e.error ? " (" + e.error + ")" : ""}${e.cost != null ? ", " + money(e.cost) : ""}`;
  if (e.event === "warning" || e.event === "error") return `${e.event}: ${e.text || ""}`;
  return e.event;
}

function itemHTML(e, kind) {
  const t = `<span class="t">${esc(clock(e.t))}</span>`;
  if (kind === "event") return `<div class="pl-item ev ev-${esc(e.event)}">${t}<span class="ev-text">${esc(eventText(e))}</span></div>`;
  if (kind === "settled") {
    const got = R.outcomes.get(e.function);
    let what = "";
    if (e.name) what = `<b>${esc(e.name)}</b>${e.confidence ? ` <span class="pl-muted">(${esc(e.confidence)})</span>` : ""}`;
    else if (e.names != null && got && got.names && typeof got.names === "object") what = `<span class="mono">${esc(namesText(got.names))}</span>`;
    else if (e.names != null) what = `${count(e.names)} names`;
    else if (e.named != null) what = `${count(e.named)} of ${count(e.of)} named`;
    else if (e.ran != null) what = `runs ${count(e.ran)} of ${count(e.of)} instructions`;
    const sentence = e.what ? `<span class="pl-what">${esc(e.what)}</span>` : "";
    const why = e.why && !NAMED.includes(e.result) ? `<span class="pl-why">${esc(e.why)}</span>` : "";
    const cost = got && got.cost != null && !voting(R.data ? R.data.state : {}) ? `<span class="pl-muted pl-small">${money(got.cost)}</span>` : "";
    return `<div class="pl-item st r-${esc(e.result)}">${t}${fnLink(e.function)}<span class="res res-${esc(e.result)}">${esc(e.result)}</span><span class="pl-got">${what}${sentence}${why}${cost}</span></div>`;
  }
  const detail = e.name ? `<b>${esc(e.name)}</b>` : e.names != null ? `<span class="mono">${esc(namesText(e.names))}</span>` : "";
  const cost = e.cost != null ? `<span class="pl-muted pl-small">${money(e.cost)}</span>` : "";
  return `<div class="pl-item oc r-${esc(e.result)}" data-fn="${esc(e.function || "")}">${t}${fnLink(e.function)}<span class="res res-${esc(e.result)}">${esc(e.result)}</span><span class="pl-got">${detail}${e.why ? `<span class="pl-why">${esc(e.why)}</span>` : ""}${cost}</span></div>`;
}

// Where a function is asked once, its answer's row gives way to its row once it is settled (one row a
// function, its final state); a vote's answers are samples and stay when every answer is asked for.
function superseded(e, settled) {
  return e.event === "outcome" && ["accepted", "answered"].includes(e.result) && settled.has(e.function) &&
    !(R.data && voting(R.data.state));
}

function remember(events) {
  for (const e of events) {
    if (e.event === "outcome" && e.function && (e.names != null || e.name)) R.outcomes.set(e.function, e);
    R.items.push(e);
  }
  if (R.items.length > ITEMS) {
    R.dropped += R.items.length - ITEMS;
    R.items.splice(0, R.items.length - ITEMS);
  }
}

// The list anew (a filter changed, the first look).
function renderItems() {
  const box = $("#pl-items");
  if (!box) return;
  let html = "", shown = 0;
  const settled = new Set();
  for (let i = R.items.length - 1; i >= 0 && shown < ROWS; i--) {
    const e = R.items[i];
    if (e.event === "settled") settled.add(e.function);
    const kind = itemKind(e);
    if (!kind || superseded(e, settled)) continue;
    html += itemHTML(e, kind);
    shown++;
  }
  box.innerHTML = html || `<div class="pl-none">${R.data && R.data.state.status === "running" ? "Nothing yet: the questions are being prepared or asked." : "Nothing to show with these filters."}</div>`;
  itemsNote(shown);
}

// New events at the top of the list, the oldest rows beyond ROWS taken off.
function prependItems(events) {
  const box = $("#pl-items");
  if (!box) return;
  let html = "";
  const settled = new Set();
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.event === "settled") settled.add(e.function);
    const kind = itemKind(e);
    if (kind && !superseded(e, settled)) html += itemHTML(e, kind);
  }
  for (const row of box.querySelectorAll(".pl-item.oc.r-accepted, .pl-item.oc.r-answered")) {
    if (superseded({ event: "outcome", result: "accepted", function: row.dataset.fn }, settled)) row.remove();
  }
  if (!html) return;
  const empty = box.querySelector(".pl-none");
  if (empty) empty.remove();
  box.insertAdjacentHTML("afterbegin", html);
  while (box.children.length > ROWS) box.lastElementChild.remove();
  itemsNote(box.children.length);
}

function itemsNote(shown) {
  const note = $("#pl-items-note");
  if (!note) return;
  const older = R.dropped + (R.data ? R.data.dropped || 0 : 0);
  note.textContent = (shown >= ROWS ? `the newest ${ROWS} rows; ` : "") + (older ? `${count(older)} older events not on the page (all of them: dread pilot --status, or BUILD/pilot/runs/${R.id}/events.jsonl)` : "");
}

function renderRun() {
  const d = R.data;
  if (!d || !$("#pl-run")) return;
  $("#pl-run-head").innerHTML = headHTML(d);
  $("#pl-cards").innerHTML = cardsHTML(d.state);
  const banner = bannerHTML(d.state);
  $("#pl-banner").hidden = !banner;
  $("#pl-banner").innerHTML = banner;
  const batches = batchesHTML(d.state);
  $("#pl-batches").hidden = !batches;
  $("#pl-batches").innerHTML = batches;
  const warnings = warningsHTML(d.state);
  $("#pl-warnings").hidden = !warnings;
  $("#pl-warnings").innerHTML = warnings;
  document.title = `${d.state.task || "run"} ${standing(d.state)} · Pilot · DREAD`;
}

async function pollRun() {
  clearTimeout(R.timer);
  const id = R.id;
  if (R.busy || id == null) return;
  R.busy = true;
  let d = null;
  try {
    d = await api("/api/pilot/follow", R.next == null ? { run: id } : { run: id, from: R.next });
  } catch (e) {
    if (R.next == null && R.id === id && S.view === "pilot-run") showError(e.message);
  } finally {
    R.busy = false;
  }
  if (R.id !== id || S.view !== "pilot-run") return;
  if (d) {
    const first = R.next == null;
    R.fetched = Date.now();
    R.next = d.next;
    remember(d.events);
    R.data = d;
    if (first && !R.touched) {                // (every answer by default where a function is asked once)
      R.show.every = !voting(d.state);
      const box = document.querySelector('#pl-run input[data-show="every"]');
      if (box) box.checked = R.show.every;
    }
    renderRun();
    if (first) renderItems(); else prependItems(d.events);
  }
  const live = !d || d.state.status === "running";
  if (live || !R.final) {                     // (one more look after the end: the last events)
    if (!live) R.final = true;
    R.timer = setTimeout(pollRun, !live ? 0 : d && d.job ? POLL : POLL_ELSEWHERE);
  }
}

async function showRun(match) {
  const id = match[1];
  const same = S.view === "pilot-run" && R.id === id && $("#pl-run");
  enter("pilot-run", "Pilot run");
  if (same) {
    if (!R.busy && !R.timer) pollRun();
    return;
  }
  stopRun();
  Object.assign(R, { id, next: null, items: [], dropped: 0, data: null, final: false, outcomes: new Map(), fetched: 0,
    touched: false });
  $("#main").innerHTML = runHTML(id);
  $("#main").scrollTop = 0;
  pollRun();
}

function stopRun() {
  clearTimeout(R.timer);
  R.timer = 0;
  R.id = null;
}

async function stopIt() {
  const btn = $("#pl-stop");
  if (btn) btn.disabled = true;
  try {
    const r = await api("/api/pilot/stop", {}, { run: R.id });
    toast(`Job ${r.id}: SIGTERM sent; the run cancels its batches and ends within ${Math.round(r.grace || 60)} s`);
    pollRun();
  } catch (e) {
    toast(e.message, true);
    if (btn) btn.disabled = false;
  }
}

async function cancelBatches() {
  try {
    const r = await api("/api/pilot/cancel-batches", {}, { run: R.id });
    toast(`Job ${r.id}: ${r.line}`);
    location.hash = "#/jobs/" + r.id;
  } catch (e) {
    toast(e.message, true);
  }
}

// ---- #/pilot/job/N: a job until its run begins ----

async function pollJob() {
  clearTimeout(J.timer);
  const id = J.id;
  if (id == null) return;
  let d;
  try {
    d = await api("/api/pilot/job", { id });
  } catch (e) {
    if (J.id === id && S.view === "pilot-job") showError(e.message);
    return;
  }
  if (J.id !== id || S.view !== "pilot-job") return;
  if (d.run) {                                // (its run has begun: follow it; Back skips the waiting)
    history.replaceState(null, "", "#/pilot/" + encodeURIComponent(d.run));
    J.id = null;
    showRun([null, d.run]);
    return;
  }
  const r = d.job, live = ["queued", "running", "stopping"].includes(r.state);
  $("#main").innerHTML = `<div class="pl-run"><div class="fn-head compact pl-run-head"><div class="fn-title"><h1>Job ${r.id}</h1>
      <span class="pl-state s-${esc(r.state)}">${esc(r.state)}</span><span class="grow"></span>
      ${live && !GUI.readOnly && r.state !== "stopping" ? `<button type="button" id="pl-stop-job">${r.state === "queued" ? "Cancel" : "Stop"}</button>` : ""}
      <a class="button" href="#/jobs/${r.id}">Its log</a></div>
      <pre class="pl-line">${esc(r.line || "")}</pre>
      <div class="pl-facts">${live ? (r.state === "queued" ? "waiting for its turn (dread gui --jobs N runs more at a time)" : "preparing the run: the image, its tables and the questions; its events begin with the run")
        : `it ended ${esc(r.state)}${r.exit != null ? ", exit status " + r.exit : ""} before its run began${r.why ? ": " + esc(r.why) : ""}`}</div></div>
      <div class="pl-run-body"><pre class="pl-log">${esc(d.tail || "")}</pre></div></div>`;
  if (live) J.timer = setTimeout(pollJob, POLL);
}

function showJob(match, keep) {
  const id = Number(match[1]);
  if (keep && S.view === "pilot-job" && J.id === id) return;       // (a reload: its poll goes on)
  enter("pilot-job", "Pilot job");
  clearTimeout(J.timer);
  J.id = id;
  $("#main").innerHTML = `<div class="pl-run"><div class="pl-none">…</div></div>`;
  pollJob();
}

async function stopJob() {
  try {
    await api("/api/pilot/stop", {}, { job: J.id });
    pollJob();
  } catch (e) {
    toast(e.message, true);
  }
}

// ---- the bar, the status, the wiring ----

function onStatus(s) {
  if (!s) return;
  const n = (s.live || []).length;
  const badge = document.querySelector('.nav-btn[data-page="pilot"] .nav-badge');
  if (badge) {
    badge.hidden = !n;
    badge.textContent = n;
    badge.parentElement.title = "The pilot (l)" + (n ? ` · ${n} run${n === 1 ? "" : "s"} going on` : "");
  }
  const first = P.index === undefined;
  if (s.index !== P.index) {
    P.index = s.index;
    if (!first && S.view === "pilot") { refreshRuns(); refreshUsage(); }
  }
}

function wire() {
  const main = $("#main");
  main.addEventListener("input", (e) => {
    if (S.view !== "pilot") return;
    const el = e.target;
    if (el.id === "pl-budget") { P.budget = el.value; changed(); return; }
    if (el.id === "pl-limit") { P.limit = el.value; if (el.value) { P.fn = "first"; main.querySelector('input[name="pl-fn"][value="first"]').checked = true; } changed(); return; }
    if (el.id === "pl-names") { P.names = el.value; if (el.value) { P.fn = "these"; main.querySelector('input[name="pl-fn"][value="these"]').checked = true; } changed(); return; }
    if (el.id === "pl-batch-n") { P.batchN = el.value; changed(); return; }
    if (el.dataset.x && el.type === "text") { P.extra[el.dataset.x] = el.value; changed(); return; }
    if (el.id === "pl-truth") { P.cmp.truth = el.value; return; }
    if (el.id === "pl-against") { P.cmp.against = el.value; return; }
    if (el.id === "pl-vs") { P.cmp.vs = el.value; }
  });
  main.addEventListener("change", (e) => {
    const el = e.target;
    if (S.view === "pilot-run" && el.dataset.show) { R.show[el.dataset.show] = el.checked; R.touched = true; renderItems(); return; }
    if (S.view !== "pilot") return;
    if (el.id === "pl-provider") { P.provider = el.value; renderStartParts(); changed(); return; }
    if (el.name === "pl-fn") { P.fn = el.value; changed(); return; }
    if (el.id === "pl-batches") { P.batches = el.checked; renderStartParts(); changed(); return; }
    if (el.dataset.x) {
      const f = P.data.fields[el.dataset.x];
      P.extra[el.dataset.x] = f.kind === "flag" ? (el.checked || undefined) : f.kind === "bool" ? (el.value === "" ? undefined : el.value === "true") : el.value;
      changed();
      return;
    }
    if (el.dataset.pick) {
      P.picked = P.picked.filter((x) => x !== el.dataset.pick);
      if (el.checked) P.picked.push(el.dataset.pick);
      while (P.picked.length > 2) P.picked.shift();
      for (const box of main.querySelectorAll("input[data-pick]")) box.checked = P.picked.includes(box.dataset.pick);
      renderPair();
      return;
    }
    if (el.id === "pl-month") { refreshUsage(el.value); return; }
    if (el.id === "pl-judge") { P.cmp.judge = el.value; renderCompare(); return; }
    if (el.id === "pl-judge-provider") { P.cmp.provider = el.value; }
  });
  main.addEventListener("click", (e) => {
    const t = e.target;
    if (t.closest("#pl-stop")) return stopIt();
    if (t.closest("#pl-cancel-batches")) return cancelBatches();
    if (t.closest("#pl-stop-job")) return stopJob();
    if (S.view !== "pilot") return;
    const b = t.closest("#pl-tasks button");
    if (b) { P.task = b.dataset.task; renderStartParts(); changed(); return; }
    if (t.closest("#pl-start-btn")) return startRun();
    if (t.closest("#pl-copy")) {
      if (!P.line) return;
      (navigator.clipboard ? navigator.clipboard.writeText(P.line) : Promise.reject(new Error("no clipboard here")))
        .then(() => toast("The command line is copied."), (err) => toast("Not copied: " + err.message, true));
      return;
    }
    const month = t.closest(".pl-month-row");
    if (month) { refreshUsage(month.dataset.month); return; }
    const mode = t.closest("#pl-cmp-mode button");
    if (mode) { P.cmp.mode = mode.dataset.mode; renderCompare(); return; }
    if (t.closest("#pl-cmp-btn")) startCompare();
  });
  main.addEventListener("keydown", (e) => {
    if (S.view === "pilot" && e.key === "Enter" && (e.ctrlKey || e.metaKey) && e.target.closest("#pl-start")) { e.preventDefault(); startRun(); }
  });
}

wire();

register({
  name: "pilot", match: /^\/pilot$/, show: showPilot,
  leave: () => { clearTimeout(P.lineTimer); clearTimeout(P.estTimer); clearTimeout(P.cmpTimer); },
  status: onStatus,
  keys: { l: () => { location.hash = "#/pilot"; } },
  help: [["l", "the pilot: start a run of dread pilot (task, provider, budget, functions), follow it live and stop it; the runs before, what they cost a month, comparisons of names"]],
  nav: { title: "The pilot (l)", href: "#/pilot", icon: ICON },
});
// (the same name: the bar's button stays lit on a run's page and a job's)
register({ name: "pilot", match: /^\/pilot\/job\/(\d+)$/, show: showJob, leave: () => { clearTimeout(J.timer); J.id = null; } });
register({ name: "pilot", match: /^\/pilot\/([0-9]{8}-[0-9]{6}-[A-Za-z0-9-]+-[0-9a-f]{4})$/, show: showRun, leave: stopRun });
