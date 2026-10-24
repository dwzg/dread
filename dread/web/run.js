// dread gui's Run page: every command of DREAD as a form made from its own parser (the server's
// dread/guipages/run.py with dread/cliforms.py), the command line the form makes, and the jobs that run
// it, with their logs and their history (dread/jobs.py). The page computes nothing: it sends the form's
// values and shows the command line the server makes of them, with the same code that starts the job.
// Addresses: #/run (the commands), #/run/NAME (one command's form), #/run/NAME@N (the form filled from
// job N's command line), #/run/NAME@["--x", ...] (filled from those words, a JSON list: another page's
// "every option"), #/jobs (every job), #/jobs/N (one job and its log, followed while it runs).

import { $, esc, api, ago, when, toast, showError, S, GUI, register, enter, nextRender, isCurrent } from "./app.js";

const LIVE = new Set(["queued", "running", "stopping"]);
const RECENT = 8;               // jobs the Run page lists
const LISTED = 500;             // jobs the list of jobs shows
const LOG_POLL = 1000;          // ms between looks at a running job's log
const LOG_LINES = 20000;        // lines of a log the page keeps (the file keeps them all)
const PREVIEW_WAIT = 120;       // ms after the last change before the command line is asked for
const ICON = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 4.5 6.5 8 3 11.5M8.5 11.5H13"/></svg>`;

const RUN = { commands: null, name: null, command: null, values: {}, showAll: false, line: null, args: null,
  error: null, field: null, seq: 0, timer: 0, status: null, seen: null, filter: "", kept: {} };
const JOB = { id: null, record: null, lines: [""], cr: false, rendered: 0, next: null, timer: 0, busy: false,
  final: false, cut: 0 };

// ---- small helpers ----

function seconds(s) {
  if (s == null || !isFinite(s)) return "";
  if (s < 10) return s.toFixed(1) + " s";
  if (s < 60) return Math.round(s) + " s";
  if (s < 3600) return Math.floor(s / 60) + " min " + Math.round(s % 60) + " s";
  return Math.floor(s / 3600) + " h " + Math.round((s % 3600) / 60) + " min";
}

// How long a job ran (or runs, or waits).
function duration(r) {
  const now = Date.now() / 1000;
  if (r.state === "queued") return "waiting " + seconds(now - r.queued);
  if (!r.started) return "";
  return seconds((r.ended || now) - r.started);
}

function stateHTML(state) {
  return `<span class="state s-${esc(state)}">${esc(state)}</span>`;
}

function exitWords(r) {
  if (r.exit == null) return r.state === "lost" || (r.ended && r.adopted) ? "exit status unknown" : "";
  if (r.exit < 0) return "ended by signal " + -r.exit;
  return "exit status " + r.exit;
}

// ---- the commands and their forms ----

function runHTML() {
  return `
    <div class="run" id="run">
      <div class="run-head"><h1>Run</h1>
        <span class="legend-line inline">every command of DREAD, its form made from its own options (--help); the command
          line it makes runs as a job</span></div>
      <div class="run-grid">
        <nav class="cmds" aria-label="Commands">
          <input id="cmd-filter" type="search" placeholder="Filter commands" autocomplete="off" spellcheck="false" aria-label="Filter commands">
          <select id="cmd-select" aria-label="Command"></select>
          <div class="cmd-list" id="cmd-list"></div>
        </nav>
        <section class="cmd-pane" id="cmd-pane"></section>
      </div>
      <section class="section run-jobs">
        <div class="section-head"><h2>Jobs</h2><span class="grow"></span><span class="legend-line inline" id="run-jobs-state"></span>
          <a class="button" href="#/jobs">All jobs</a></div>
        <div id="run-jobs"></div>
      </section>
    </div>`;
}

function renderCommands() {
  const list = $("#cmd-list"), select = $("#cmd-select");
  if (!list || !RUN.commands) return;
  const f = RUN.filter.trim().toLowerCase();
  const shown = RUN.commands.filter((c) => !f || c.name.includes(f) || c.about.toLowerCase().includes(f));
  list.innerHTML = shown.map((c) => `<a class="cmd${c.name === RUN.name ? " on" : ""}${c.runnable ? "" : " off"}" href="#/run/${encodeURIComponent(c.name)}"
      title="${esc(c.about + (c.why ? " (" + c.why + ")" : ""))}"><span class="nm">${esc(c.name)}</span><span class="about">${esc(c.about)}</span></a>`).join("") ||
    `<div class="none">No command has "${esc(RUN.filter)}" in its name or what it does.</div>`;
  select.innerHTML = `<option value=""${RUN.name ? "" : " selected"}>Choose a command</option>` +
    RUN.commands.map((c) => `<option value="${esc(c.name)}"${c.name === RUN.name ? " selected" : ""}>${esc(c.name)} · ${esc(c.about)}</option>`).join("");
}

function pickHTML() {
  const s = RUN.status;
  const limit = s ? s.limit : 1;
  return `<div class="cmd-pick">
    <h2>Choose a command</h2>
    <p>Each command's form is made from its own options, as <span class="mono">dread COMMAND --help</span> lists them.
      Whatever you set becomes the command line below the form, the same words you would type in a terminal in the
      target's directory; <b>Run</b> runs it there as a job.</p>
    <ul>
      <li>${limit} job${limit === 1 ? "" : "s"} at a time (<span class="mono">dread gui --jobs N</span>); the others wait in a queue.</li>
      <li>Polite, as DREAD's bulk runs are: the lowest priority.</li>
      <li>Its output and its record are kept in BUILD/gui/jobs: the history survives the server.</li>
      <li>Stop sends SIGTERM to the job's processes, SIGKILL if they are still there after a grace period.</li>
      <li>Where a job changes the tables, the other views reload by themselves.</li>
    </ul>
    ${GUI.readOnly ? `<p class="warn">This page is read-only (dread gui --read-only): it shows the command lines and runs nothing.</p>` : ""}
    ${s && s.owner && !GUI.readOnly ? `<p class="warn">${esc(s.owner)}: this page starts no jobs.</p>` : ""}
  </div>`;
}

// The values at a path of subcommands (their objects made on the way): where a nested field keeps its value.
function container(path) {
  let box = RUN.values;
  for (const id of path) {
    if (!box[id] || typeof box[id] !== "object" || Array.isArray(box[id])) box[id] = { command: null, values: {} };
    if (!box[id].values) box[id].values = {};
    box = box[id].values;
  }
  return box;
}

function textOf(v) {
  if (v == null || v === false || v === true) return "";
  return Array.isArray(v) ? v.map((w) => (/[\s'"\\]/.test(w) || w === "" ? "'" + w.replace(/'/g, `'"'"'`) + "'" : w)).join(" ") : String(v);
}

// Whether a field shows: its help ties it to choices of another field (dread.cliforms condition) and
// that field has one of them, or every option is asked for.
function visible(field, form, values) {
  if (field.hidden) return false;
  if (RUN.showAll || !field.when) return true;
  const ctl = form.fields.find((f) => f.id === field.when.field);
  if (!ctl) return true;
  const v = values[ctl.id] != null && values[ctl.id] !== "" ? values[ctl.id] : ctl.default;
  return field.when.in.includes(String(v));
}

// The values the server gets: the fields shown, the chosen subcommand's alone.
function sent(form, values) {
  const out = {};
  for (const f of form.fields) {
    const v = values[f.id];
    if (v == null || v === "" || !visible(f, form, values)) continue;
    if (f.kind === "subcommands") {
      if (!v.command) continue;
      const sub = f.subcommands.find((s) => s.name === v.command);
      out[f.id] = { command: v.command, values: sub ? sent(sub.form, v.values || {}) : {} };
    } else out[f.id] = v;
  }
  return out;
}

function fieldLabel(f) {
  if (f.positional) return `<span class="arg-name">${esc(f.metavar || f.dest)}</span>`;
  const flags = f.kind === "bool" ? f.flags.slice(0, 1) : f.flags;
  return flags.map((s) => `<code>${esc(s)}</code>`).join(" ");
}

function widgetHTML(f, v, path) {
  const at = `data-id="${esc(f.id)}" data-kind="${f.kind}" data-path="${esc(JSON.stringify(path))}"`;
  const name = f.flags.length ? f.flags[f.flags.length - 1] : (f.metavar || f.dest);
  const aria = `aria-label="${esc(name)}"`;
  if (f.kind === "flag") return `<input type="checkbox" ${at} ${aria}${v === true ? " checked" : ""}>`;
  if (f.kind === "count") return `<input type="number" min="0" step="1" class="num" ${at} ${aria} value="${v ? esc(v) : ""}" placeholder="0">`;
  if (f.kind === "bool") {
    const cur = v === true ? "true" : v === false ? "false" : "";
    const off = f.flags[1] || "--no-" + f.flags[0].replace(/^--/, "");
    return `<select ${at} ${aria}><option value=""${cur === "" ? " selected" : ""}>default (${esc(f.default || "off")})</option>` +
      `<option value="true"${cur === "true" ? " selected" : ""}>${esc(f.flags[0])}</option>` +
      `<option value="false"${cur === "false" ? " selected" : ""}>${esc(off)}</option></select>`;
  }
  if (f.choices && (f.kind === "value")) {
    const first = f.positional && f.required ? `<option value=""${v ? "" : " selected"} disabled>choose</option>`
      : `<option value=""${v ? "" : " selected"}>${f.default != null ? "default (" + esc(f.default) + ")" : "not given"}</option>`;
    return `<select ${at} ${aria}>${first}${f.choices.map((c) => `<option${String(v) === c ? " selected" : ""}>${esc(c)}</option>`).join("")}</select>`;
  }
  if (f.kind === "maybe") {
    return `<span class="maybe"><input type="checkbox" ${at} data-part="on" aria-label="${esc(name)} alone"${v === true || (v && v !== "") ? " checked" : ""}>` +
      `<input type="text" ${at} data-part="value" ${aria} value="${esc(v === true ? "" : textOf(v))}" placeholder="${esc(f.metavar || "value")} (may be left out)" spellcheck="false" autocomplete="off"></span>`;
  }
  const several = f.kind === "list" || f.kind === "raw";
  const filled = path.length === 0 && RUN.command && RUN.command.defaults ? RUN.command.defaults[f.id] : undefined;
  const hint = several ? (f.repeat ? `several: separated by spaces, each its own ${f.flags[0]}` : "several: separated by spaces")
    + "; quote a value with spaces"
    : filled != null ? `${filled} (left empty: the ${f.id === "--image" ? "image" : "target"} this page shows)`
    : f.default != null ? "default: " + f.default : (f.metavar || "");
  const mode = f.type === "int" || f.type === "float" ? ' inputmode="decimal"' : "";
  return `<input type="text" ${at} ${aria}${mode} value="${esc(textOf(v))}" placeholder="${esc(hint)}" spellcheck="false" autocomplete="off">`;
}

function fieldHTML(f, form, values, path) {
  const v = values[f.id];
  const meta = [];
  if (f.required) meta.push(`<span class="req">required</span>`);
  if (f.type === "int" || f.type === "float") meta.push(`<span class="ty">${f.type === "int" ? "a whole number" : "a number"}</span>`);
  return `<div class="field fk-${f.kind}" data-row="${esc(f.id)}" data-rowpath="${esc(JSON.stringify(path))}">
      <label class="fl">${fieldLabel(f)}</label>
      <div class="fw">${widgetHTML(f, v, path)}${meta.length ? `<span class="fmeta">${meta.join(" ")}</span>` : ""}</div>
      ${f.help ? `<div class="fh">${esc(f.help)}</div>` : ""}
    </div>`;
}

function formHTML(form, values, path) {
  let html = "";
  const order = [...form.fields.filter((f) => f.positional && f.kind !== "subcommands"),
    ...form.fields.filter((f) => !f.positional && !(path.length === 0 && (f.id === "--target" || f.id === "--image"))),
    ...form.fields.filter((f) => f.kind === "subcommands")];
  const where = path.length === 0 ? form.fields.filter((f) => f.id === "--target" || f.id === "--image") : [];
  let group = null;
  for (const f of order) {
    if (f.kind === "subcommands") { html += subcommandsHTML(f, values, path); continue; }
    if (f.hidden) continue;
    if (f.group && f.group !== group) { html += `<div class="fgroup">${esc(f.group)}</div>`; }
    group = f.group;
    html += fieldHTML(f, form, values, path);
  }
  if (form.exclusive && form.exclusive.length) {
    html += form.exclusive.map((ids) => `<div class="fnote">one at most of ${ids.map((i) => `<code>${esc(i)}</code>`).join(", ")}</div>`).join("");
  }
  if (where.length) {
    html += `<div class="fgroup">the target and image (left empty: those this page shows)</div>` + where.map((f) => fieldHTML(f, form, values, path)).join("");
  }
  return html;
}

function subcommandsHTML(f, values, path) {
  const v = values[f.id] || {};
  const chosen = f.subcommands.find((s) => s.name === v.command);
  const at = `data-id="${esc(f.id)}" data-path="${esc(JSON.stringify(path))}"`;
  let html = `<div class="field fk-subcommands" data-row="${esc(f.id)}" data-rowpath="${esc(JSON.stringify(path))}">
      <label class="fl"><span class="arg-name">${esc(f.metavar || f.dest || "command")}</span></label>
      <div class="fw"><div class="seg subcmds">${f.subcommands.map((s) => `<button type="button" ${at} data-sub="${esc(s.name)}"${chosen && chosen.name === s.name ? ' class="on"' : ""} title="${esc(s.help)}">${esc(s.name)}</button>`).join("")}</div>
        ${f.required ? `<span class="fmeta"><span class="req">required</span></span>` : ""}</div>
      <div class="fh">${chosen ? esc(chosen.help) : "one of these, each with options of its own"}</div>
    </div>`;
  if (chosen) {
    if (!v.values) v.values = {};
    html += `<div class="subform">${formHTML(chosen.form, v.values, path.concat([f.id]))}</div>`;
  }
  return html;
}

function actionsHTML(cmd) {
  if (GUI.readOnly) return `<span class="legend-line inline">read-only (dread gui --read-only): copy the command line and run it in a terminal</span>`;
  if (!cmd.runnable) return `<span class="warn">${esc(cmd.why || "it does not run as a job here")}</span>`;
  return `<span class="legend-line inline run-where">runs in <span class="mono">${esc(cmd.cwd)}</span>, as a job</span>` +
    `<button type="button" class="primary" id="run-btn" title="Run it as a job (Ctrl+Enter)" disabled>Run</button>`;
}

function renderForm() {
  const cmd = RUN.command, pane = $("#cmd-pane");
  if (!pane) return;
  const about = cmd.form.description && cmd.form.description.replace(/^dread [\w-]+: */, "").replace(/\.$/, "") !== cmd.about
    ? `<p class="cmd-desc">${esc(cmd.form.description)}</p>` : "";
  pane.innerHTML = `
    <div class="cmd-head"><h2 class="cmd-title">dread ${esc(cmd.name)}</h2><span class="legend-line inline">${esc(cmd.about)}</span></div>
    ${about}
    <div class="fields" id="fields">${formHTML(cmd.form, RUN.values, [])}</div>
    <label class="show-all" id="show-all-label"><input type="checkbox" id="show-all"${RUN.showAll ? " checked" : ""}> <span id="show-all-text">every option</span></label>
    <div class="cmdline-box"><pre class="cmdline" id="cmdline" aria-live="polite"></pre>
      <button type="button" id="copy-line" title="Copy the command line">Copy</button></div>
    <div class="cmd-error" id="cmd-error"></div>
    <div class="run-actions">${actionsHTML(cmd)}</div>`;
  applyVisibility();
}

function rerenderFields() {
  const box = $("#fields");
  if (!box) return;
  const active = document.activeElement && document.activeElement.closest("#fields") ? document.activeElement : null;
  const key = active ? [active.dataset.id, active.dataset.path, active.dataset.part || ""] : null;
  box.innerHTML = formHTML(RUN.command.form, RUN.values, []);
  applyVisibility();
  if (key) {
    const again = [...box.querySelectorAll("[data-id]")].find((el) => el.dataset.id === key[0] && el.dataset.path === key[1] && (el.dataset.part || "") === key[2]);
    if (again) again.focus();
  }
}

// Fields shown and hidden as the choices they depend on change; the switch says how many are hidden.
function applyVisibility() {
  let hidden = 0;
  const walk = (form, values, path) => {
    for (const f of form.fields) {
      const rows = [...document.querySelectorAll("#fields .field")].filter((el) => el.dataset.row === f.id && el.dataset.rowpath === JSON.stringify(path));
      const show = visible(f, form, values);
      if (!show && !f.hidden) hidden++;
      for (const row of rows) row.hidden = !show;
      if (f.kind === "subcommands" && values[f.id] && values[f.id].command) {
        const sub = f.subcommands.find((s) => s.name === values[f.id].command);
        if (sub) walk(sub.form, values[f.id].values || {}, path.concat([f.id]));
      }
    }
  };
  walk(RUN.command.form, RUN.values, []);
  const text = $("#show-all-text");
  if (text) {
    text.textContent = RUN.showAll ? "every option, also those for other choices" : `every option${hidden ? ` (${hidden} more, for other choices)` : ""}`;
    $("#show-all-label").hidden = !RUN.showAll && !hidden;
  }
}

function schedulePreview(now = false) {
  clearTimeout(RUN.timer);
  RUN.timer = setTimeout(askPreview, now ? 0 : PREVIEW_WAIT);
}

async function askPreview() {
  const seq = ++RUN.seq, name = RUN.name;
  if (!RUN.command) return;
  let d;
  try {
    d = await api("/api/command-line", { name, values: JSON.stringify(sent(RUN.command.form, RUN.values)) });
  } catch (e) {
    d = { line: null, args: null, error: e.message, field: null };
  }
  if (seq !== RUN.seq || name !== RUN.name || !$("#cmdline")) return;
  Object.assign(RUN, { line: d.line, args: d.args, error: d.error, field: d.field });
  $("#cmdline").textContent = d.line || "";
  $("#cmd-error").textContent = d.error || "";
  for (const row of document.querySelectorAll("#fields .field.bad")) row.classList.remove("bad");
  if (d.field) for (const row of document.querySelectorAll("#fields .field")) if (row.dataset.row === d.field) row.classList.add("bad");
  const btn = $("#run-btn");
  if (btn) btn.disabled = !!d.error || !d.args;
}

async function runIt() {
  const btn = $("#run-btn");
  if (!RUN.command || !btn || btn.disabled || GUI.readOnly) return;
  btn.disabled = true;
  try {
    const r = await api("/api/jobs/start", {}, { command: RUN.name, values: sent(RUN.command.form, RUN.values) });
    toast(`Job ${r.id}: ${r.line}`);
    refreshBadge();
    location.hash = "#/jobs/" + r.id;
  } catch (e) {
    $("#cmd-error").textContent = e.message;
    btn.disabled = false;
  }
}

// What one input changed in the values.
function onInput(el) {
  const path = JSON.parse(el.dataset.path || "[]"), box = container(path), id = el.dataset.id, kind = el.dataset.kind;
  let v;
  if (kind === "flag") v = el.checked || undefined;
  else if (kind === "count") v = el.value ? Number(el.value) : undefined;
  else if (kind === "bool") v = el.value === "" ? undefined : el.value === "true";
  else if (kind === "maybe") {
    const row = el.closest(".maybe"), on = row.querySelector('[data-part="on"]'), text = row.querySelector('[data-part="value"]');
    if (el === text && text.value.trim()) on.checked = true;
    v = text.value.trim() ? text.value : on.checked ? true : undefined;
  } else v = el.value === "" ? undefined : el.value;
  if (v === undefined) delete box[id]; else box[id] = v;
  applyVisibility();
  schedulePreview();
}

function recentHTML(list) {
  if (!list.length) return `<div class="none">No job yet: choose a command, set what it needs and press Run.</div>`;
  return `<div class="jobs">${list.map(jobRowHTML).join("")}</div>`;
}

function jobRowHTML(r) {
  return `<a class="jobrow" href="#/jobs/${r.id}" title="${esc(r.line)}">
      <span class="jid">${r.id}</span>${stateHTML(r.state)}<span class="jline">${esc(r.line)}</span>
      <span class="jwhen" title="${esc(when(r.started || r.queued))}">${esc(ago(r.started || r.queued))}</span>
      <span class="jtime">${esc(duration(r))}</span><span class="jexit">${esc(exitWords(r))}</span></a>`;
}

function statusWords(s) {
  if (!s) return "";
  const parts = [];
  if (s.running) parts.push(`${s.running} running`);
  if (s.queued) parts.push(`${s.queued} waiting`);
  return parts.join(", ") || "none running";
}

async function refreshRecent() {
  let d;
  try {
    d = await api("/api/jobs", { limit: RECENT });
  } catch {
    return;
  }
  const box = $("#run-jobs");
  if (!box || S.view !== "run") return;
  box.innerHTML = recentHTML(d.jobs);
  $("#run-jobs-state").textContent = statusWords(d.status);
}

async function showRun(match, keep) {
  const name = match[1] || null, from = match[2] && /^\d+$/.test(match[2]) ? Number(match[2]) : null;
  const given = match[2] && match[2].startsWith("[") ? match[2] : null;    // (words another page hands over)
  const again = S.view === "run" && $("#run");
  if (keep && again && RUN.name === name) { refreshRecent(); return; }      // (a reload: the form stays as typed)
  const mine = nextRender();
  let list, cmd = null, jobs;
  try {
    [list, jobs] = await Promise.all([api("/api/commands"), api("/api/jobs", { limit: RECENT })]);
    if (name) {
      const params = { name };
      if (from != null) {
        const r = await api("/api/job", { id: from });
        if (r.args) params.args = JSON.stringify(r.args);
      } else if (given) params.args = given;
      cmd = await api("/api/command", params);
    }
  } catch (e) {
    if (isCurrent(mine)) showError(e.message);
    return;
  }
  if (!isCurrent(mine)) return;
  const main = enter("run", name ? "dread " + name : "Run");
  if (!again) { main.innerHTML = runHTML(); main.scrollTop = 0; $("#cmd-filter").value = RUN.filter; }
  RUN.commands = list;
  if (RUN.name) RUN.kept[RUN.name] = RUN.values;     // (each command's form keeps what was set while the page lives)
  if (cmd) {
    const changed = RUN.name !== name || from != null || given != null;
    RUN.name = name;
    RUN.command = cmd;
    if (changed) RUN.values = from != null || given != null ? cmd.values || {} : RUN.kept[name] || {};
    if (cmd.error) toast(`The command line ${from != null ? "of job " + from : "handed over"} does not fit the form: ${cmd.error}`, true);
    renderForm();
    schedulePreview(true);
  } else {
    Object.assign(RUN, { name: null, command: null, values: {} });
    $("#cmd-pane").innerHTML = pickHTML();
  }
  renderCommands();
  $("#run-jobs").innerHTML = recentHTML(jobs.jobs);
  $("#run-jobs-state").textContent = statusWords(jobs.status);
  const on = $("#cmd-list .cmd.on"), box = $("#cmd-list");
  if (on && !again && box.clientHeight) {          // (the list alone: scrollIntoView would move the page too)
    const top = on.offsetTop - box.offsetTop;
    if (top < box.scrollTop || top + on.offsetHeight > box.scrollTop + box.clientHeight) box.scrollTop = top - box.clientHeight / 3;
  }
}

// ---- the jobs ----

async function showJobs(match, keep) {
  const mine = nextRender();
  let d;
  try {
    d = await api("/api/jobs", { limit: LISTED });
  } catch (e) {
    if (isCurrent(mine)) showError(e.message);
    return;
  }
  if (!isCurrent(mine)) return;
  const main = enter("jobs", "Jobs");
  const scroll = keep ? main.scrollTop : 0;
  const s = d.status;
  main.innerHTML = `<div class="ov jobs-view">
      <h1>Jobs</h1>
      <div class="sub">${esc(statusWords(s))}; ${s.limit} at a time · the commands run from the <a href="#/run">Run</a> page, newest
        first; logs and records in BUILD/gui/jobs${s.owner ? ` · <span class="warn">${esc(s.owner)}</span>` : ""}</div>
      <section class="section">${recentHTML(d.jobs)}</section>
    </div>`;
  main.scrollTop = scroll;
}

function jobHTML() {
  return `<div class="job" id="job">
      <div class="fn-head compact job-head" id="job-head"></div>
      <div class="job-log-box" id="job-log-box"><div class="log-note" id="log-note" hidden></div><div class="log-empty" id="log-empty"></div><pre class="job-log" id="job-log"><span id="log-tail"></span></pre></div>
    </div>`;
}

function renderJobHead() {
  const r = JOB.record, head = $("#job-head");
  if (!r || !head) return;
  const live = LIVE.has(r.state), dread = !!r.args && !!r.command;
  const buttons = GUI.readOnly ? "" : [
    live ? `<button type="button" id="job-stop" title="SIGTERM to its processes; SIGKILL after ${Math.round(r.grace || 30)} s if they are still there"${r.state === "stopping" ? " disabled" : ""}>${r.state === "queued" ? "Cancel" : r.state === "stopping" ? "Stopping…" : "Stop"}</button>` : "",
    !live && dread && !r.redacted ? `<button type="button" id="job-again" title="The same command line once more">Run again</button>` : "",
    dread ? `<a class="button" href="#/run/${encodeURIComponent(r.command)}@${r.id}" title="Its form, filled in from this command line">Change and run</a>` : "",
  ].join("");
  const facts = [`in <span class="mono">${esc(r.cwd || "")}</span>`];
  facts.push(r.started ? `started ${esc(new Date(r.started * 1000).toLocaleTimeString())} (${esc(ago(r.started))})` : `queued ${esc(ago(r.queued))}`);
  if (duration(r)) facts.push((live && r.state !== "queued" ? "running for " : r.state === "queued" ? "" : "took ") + esc(duration(r)));
  if (exitWords(r)) facts.push(`<b class="${r.state === "done" ? "good" : r.state === "failed" ? "bad" : ""}">${esc(exitWords(r))}</b>`);
  if (r.by) facts.push("by " + esc(r.by));
  if (r.nice != null) facts.push("nice " + r.nice);
  head.innerHTML = `
    <div class="fn-title"><h1>Job ${r.id}</h1>${stateHTML(r.state)}<span class="grow"></span>${buttons}</div>
    <pre class="cmdline">${esc(r.line || "")}</pre>
    <div class="job-facts">${facts.join(" · ")}</div>
    ${r.why ? `<div class="job-why">${esc(r.why)}</div>` : ""}
    ${r.redacted ? `<div class="job-why">Its command line held the value of a secret of the environment, kept here by the variable's name: change it and run it from its form.</div>` : ""}`;
  sizeLog();
}

function sizeLog() {
  const box = $("#job-log-box");
  if (!box) return;
  const top = box.getBoundingClientRect().top + $("#main").scrollTop - $("#main").getBoundingClientRect().top;
  box.style.height = Math.max(220, Math.floor($("#main").clientHeight - top)) + "px";
  logEmpty();
}

// A word where the log has no text yet, or has none at all.
function logEmpty() {
  const box = $("#log-empty");
  if (!box || !JOB.record) return;
  const empty = JOB.lines.length === 1 && !JOB.lines[0];
  box.hidden = !empty;
  box.textContent = LIVE.has(JOB.record.state) ? (JOB.record.state === "queued" ? "waiting for its turn" : "no output yet") : "no output";
}

// The log's text in lines as a terminal shows it: a carriage return starts its line again.
function appendLog(text) {
  for (const part of text.split(/(\r\n|\n|\r)/)) {
    if (part === "\n" || part === "\r\n") { JOB.lines.push(""); JOB.cr = false; }
    else if (part === "\r") JOB.cr = true;
    else if (part) {
      if (JOB.cr) { JOB.lines[JOB.lines.length - 1] = part; JOB.cr = false; }
      else JOB.lines[JOB.lines.length - 1] += part;
    }
  }
  const pre = $("#job-log"), box = $("#job-log-box");
  if (!pre) return;
  const atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
  if (JOB.lines.length > LOG_LINES) {             // (the page keeps the last lines; the file has them all)
    const drop = JOB.lines.length - LOG_LINES;
    JOB.lines.splice(0, drop);
    JOB.cut += drop;
    pre.innerHTML = `<span id="log-tail"></span>`;
    JOB.rendered = 0;
  }
  const complete = JOB.lines.length - 1;
  if (complete > JOB.rendered) {
    pre.insertBefore(document.createTextNode(JOB.lines.slice(JOB.rendered, complete).join("\n") + "\n"), $("#log-tail"));
    JOB.rendered = complete;
  }
  $("#log-tail").textContent = JOB.lines[JOB.lines.length - 1];
  logNote();
  logEmpty();
  if (atEnd) box.scrollTop = box.scrollHeight;
}

function logNote(start = null) {
  const note = $("#log-note");
  if (!note) return;
  if (start != null) JOB.skipped = start;
  const parts = [];
  if (JOB.skipped) parts.push(`the first ${(JOB.skipped / 1024).toFixed(0)} KiB of the log are left out`);
  if (JOB.cut) parts.push(`${JOB.cut} lines dropped from the top`);
  note.hidden = !parts.length;
  if (parts.length) note.textContent = parts.join("; ") + ` (the whole log is BUILD/gui/jobs/${JOB.id}.log)`;
}

async function pollLog() {
  clearTimeout(JOB.timer);
  const id = JOB.id;
  if (JOB.busy || id == null) return;
  JOB.busy = true;
  try {
    for (;;) {
      const d = await api("/api/job-log", JOB.next == null ? { id } : { id, from: JOB.next });
      if (JOB.id !== id || S.view !== "job") return;
      if (JOB.next == null) logNote(d.start);
      appendLog(d.text);
      JOB.next = d.next;
      if (d.state !== JOB.record.state) {
        JOB.record = await api("/api/job", { id });
        if (JOB.id !== id) return;
        refreshBadge();
      }
      if (!d.more) break;
    }
    renderJobHead();
  } catch {
    /* the server went away or the job is gone: try again at the next look */
  } finally {
    JOB.busy = false;
  }
  if (JOB.id !== id || S.view !== "job") return;
  const live = LIVE.has(JOB.record.state);
  if (live || !JOB.final) {                       // (one more look after the end: the last line)
    if (!live) JOB.final = true;
    JOB.timer = setTimeout(pollLog, live ? LOG_POLL : 0);
  }
}

function stopLog() {
  clearTimeout(JOB.timer);
  JOB.timer = 0;
  JOB.id = null;
}

async function showJob(match, keep) {
  const id = Number(match[1]);
  const mine = nextRender();
  let r;
  try {
    r = await api("/api/job", { id });
  } catch (e) {
    if (isCurrent(mine)) showError(e.message);
    return;
  }
  if (!isCurrent(mine)) return;
  const same = S.view === "job" && JOB.id === id && $("#job-log");
  const main = enter("job", "Job " + id);
  JOB.record = r;
  if (!same) {
    stopLog();
    Object.assign(JOB, { id, lines: [""], cr: false, rendered: 0, next: null, final: false, cut: 0, skipped: 0, busy: false });
    main.innerHTML = jobHTML();
    main.scrollTop = 0;
  }
  renderJobHead();
  if (!same || (!JOB.busy && !JOB.timer)) pollLog();
}

async function stopJob() {
  const r = JOB.record;
  if (!r) return;
  try {
    JOB.record = await api("/api/jobs/stop", {}, { id: r.id });
    renderJobHead();
    pollLog();
  } catch (e) {
    toast(e.message, true);
  }
}

async function runAgain() {
  const r = JOB.record;
  if (!r || !r.args) return;
  try {
    const again = await api("/api/jobs/start", {}, { command: r.command, args: r.args });
    location.hash = "#/jobs/" + again.id;
  } catch (e) {
    toast(e.message, true);
  }
}

// ---- the bar, the status, the wiring ----

// The bar's count at once (a job started or ended here), not at the next status poll.
async function refreshBadge() {
  try {
    onStatus((await api("/api/jobs", { limit: 1 })).status);
  } catch { /* the next poll brings it */ }
}

function onStatus(s) {
  RUN.status = s;
  const badge = document.querySelector('.nav-btn[data-page="run"] .nav-badge');
  if (badge && s) {
    const n = (s.running || 0) + (s.queued || 0);
    badge.hidden = !n;
    badge.textContent = n;
    badge.parentElement.title = "Run a command (x)" + (n ? ` · ${statusWords(s)}` : "");
  }
  if (s && s.changed !== RUN.seen) {
    const first = RUN.seen == null;
    RUN.seen = s.changed;
    if (first) return;
    if (S.view === "run") refreshRecent();
    else if (S.view === "jobs") showJobs(null, true);
  }
}

function wire() {
  const main = $("#main");
  main.addEventListener("input", (e) => {
    if (e.target.id === "cmd-filter") { RUN.filter = e.target.value; renderCommands(); return; }
    const el = e.target.closest("#fields [data-id]");
    if (el && el.dataset.kind && S.view === "run") onInput(el);
  });
  main.addEventListener("change", (e) => {
    if (e.target.id === "cmd-select" && e.target.value) { location.hash = "#/run/" + encodeURIComponent(e.target.value); return; }
    if (e.target.id === "show-all") { RUN.showAll = e.target.checked; applyVisibility(); schedulePreview(); return; }
    const el = e.target.closest("#fields [data-id]");
    if (el && el.dataset.kind && S.view === "run" && (el.type === "checkbox" || el.tagName === "SELECT")) onInput(el);
  });
  main.addEventListener("click", (e) => {
    const sub = e.target.closest("#fields button[data-sub]");
    if (sub) {
      const box = container(JSON.parse(sub.dataset.path || "[]"));
      const old = box[sub.dataset.id];
      box[sub.dataset.id] = { command: sub.dataset.sub, values: old && old.command === sub.dataset.sub ? old.values || {} : {} };
      rerenderFields();
      schedulePreview();
      return;
    }
    if (e.target.closest("#run-btn")) return runIt();
    if (e.target.closest("#copy-line")) {
      if (!RUN.line) return;
      (navigator.clipboard ? navigator.clipboard.writeText(RUN.line) : Promise.reject(new Error("no clipboard here")))
        .then(() => toast("The command line is copied."), (err) => toast("Not copied: " + err.message, true));
      return;
    }
    if (e.target.closest("#job-stop")) return stopJob();
    if (e.target.closest("#job-again")) return runAgain();
  });
  main.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && e.target.closest("#fields")) { e.preventDefault(); runIt(); }
  });
  window.addEventListener("resize", () => { if (S.view === "job") sizeLog(); });
}

wire();

register({
  name: "run", match: /^\/run(?:\/([^@/]+)(?:@(\d+|\[.*\]))?)?$/, show: showRun,
  leave: () => { clearTimeout(RUN.timer); },
  status: onStatus,
  keys: { x: () => { location.hash = "#/run"; } },
  help: [["x", "run a command: a form for each of DREAD's commands, the command line it makes, run as a job; its log, the jobs before"]],
  nav: { title: "Run a command (x)", href: "#/run", icon: ICON },
});
register({ name: "jobs", match: /^\/jobs$/, show: showJobs });
register({ name: "job", match: /^\/jobs\/(\d+)$/, show: showJob, leave: stopLog });
