// dread gui's Setup page (dread/guipages/setup.py): what this machine has (dread doctor), a new target from
// an image (dread new as a job; the image a path on this machine or a file uploaded into the new target),
// the target opened once it is made, its first analyses as a chain of jobs, and the language-model
// providers (dread llm set and dread llm key; a key typed here goes into a key file of mode 600 and is
// never shown back). Every action shows the command line that does the same; the page computes nothing.
// Address: #/setup.

import { $, esc, api, ago, toast, showError, S, GUI, register, enter, nextRender, isCurrent } from "./app.js";

const ICON = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9.6 2.2a3.3 3.3 0 0 0-3.9 4.5L2.3 10.1a1.3 1.3 0 0 0 1.8 1.8l3.4-3.4a3.3 3.3 0 0 0 4.5-3.9l-1.9 1.9-1.6-.3-.3-1.6z"/></svg>`;
const TOKEN = document.querySelector('meta[name="dread-token"]').content;
const LIVE = new Set(["queued", "running", "stopping"]);
const POLL = 1500;              // ms between looks at a job or a chain that runs
const PREVIEW_WAIT = 150;       // ms after the last change before a command line is asked for
const STEPS = { functions: "the functions", regflow: "register flow", lift: "lift check", readable: "readable C" };

const U = {
  state: null, doctor: null, chain: null, providers: null, every: false, allFindings: false,
  form: { dir: "", source: "path", path: "", file: null, arch: "", base: "", entry: "", limit: "", ram: "", io: "",
    name: "", compiler: "", unnamed: false },
  inspected: null, inspectSeq: 0, inspectTimer: 0, line: null, args: null, lineError: null, lineSeq: 0, lineTimer: 0,
  making: null, makeTimer: 0, chainTimer: 0, chainSeen: null,
  edit: null, provLine: null, provSeq: 0, provTimer: 0, tests: {}, testTimer: 0,
};

// ---- small helpers ----

function stateChip(state) {
  return `<span class="su-state su-${esc(state)}">${esc(state)}</span>`;
}

function seconds(s) {
  if (s == null || !isFinite(s)) return "";
  if (s < 60) return Math.max(0, Math.round(s)) + " s";
  if (s < 3600) return Math.floor(s / 60) + " min " + Math.round(s % 60) + " s";
  return Math.floor(s / 3600) + " h " + Math.round((s % 3600) / 60) + " min";
}

function bytes(n) {
  if (n == null) return "";
  if (n < 1024) return n + " B";
  if (n < 1 << 20) return (n / 1024).toFixed(1) + " KiB";
  return (n / (1 << 20)).toFixed(1) + " MiB";
}

function copyButton(text, label = "Copy") {
  return `<button type="button" class="su-copy" data-copy="${esc(text)}" title="Copy the line">${esc(label)}</button>`;
}

function lineBox(text, id) {
  return `<div class="su-line"><pre class="cmdline su-cmd"${id ? ` id="${id}"` : ""}>${esc(text || "")}</pre>${text ? copyButton(text) : ""}</div>`;
}

function blocked() {
  if (GUI.readOnly) return "this page is read-only (dread gui --read-only): it changes nothing; the command lines still show what to run";
  if (U.state && U.state.changes) return U.state.changes;
  return null;
}

function disabledIf(why) {
  return why ? ` disabled title="${esc(why)}"` : "";
}

async function upload(file, dir, progress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/setup/upload?" + new URLSearchParams({ dir, name: file.name }));
    xhr.setRequestHeader("X-Dread-Token", TOKEN);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) progress(e.loaded / e.total); };
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch { data = { error: xhr.statusText || "no answer" }; }
      if (xhr.status === 200) resolve(data); else reject(new Error(data.error || xhr.statusText));
    };
    xhr.onerror = () => reject(new Error("the upload did not reach the server (larger than its limit?)"));
    xhr.send(file);
  });
}

// What an uploaded file is, from its first bytes (an ELF's machine), as the server's inspect says it.
async function inspectFile(file) {
  const head = new Uint8Array(await file.slice(0, 20).arrayBuffer());
  const out = { kind: "raw", size: file.size, machine: null, path: file.name };
  if (head[0] === 0x7f && head[1] === 0x45 && head[2] === 0x4c && head[3] === 0x46) {
    out.kind = "elf";
    const m = head[18] | (head[19] << 8);
    out.machine = { 40: "arm", 243: "riscv", 105: "msp430", 8: "mips" }[m] || m;
    out.supported = head[4] === 1 && head[5] === 1 && typeof out.machine === "string";
  }
  return out;
}

// ---- the page ----

function pageHTML() {
  return `
    <div class="su" id="su">
      <div class="su-head"><h1>Setup</h1>
        <span class="legend-line inline">what this machine has, a new target from an image, its first analyses, the
          language models; each step is a command you could type, shown beside it</span></div>
      <div class="warn su-blocked" id="su-blocked" hidden></div>
      <section class="section" id="su-doctor"><div class="section-head"><h2>This machine</h2></div><div class="none">looking…</div></section>
      <section class="section" id="su-target"></section>
      <section class="section" id="su-chain"></section>
      <section class="section" id="su-models"><div class="section-head"><h2>Language models</h2></div><div class="none">reading llm.toml…</div></section>
    </div>`;
}

async function showSetup(match, keep) {
  const mine = nextRender();
  let state;
  try {
    state = await api("/api/setup/state");
  } catch (e) {
    if (isCurrent(mine)) showError(e.message);
    return;
  }
  if (!isCurrent(mine)) return;
  const again = keep && S.view === "setup" && $("#su");
  const main = enter("setup", "Setup");
  U.state = state;
  if (!again) {
    main.innerHTML = pageHTML();
    main.scrollTop = 0;
    renderTarget();
    loadDoctor();
    loadProviders();
  } else renderTargetHead();
  const why = blocked();
  $("#su-blocked").hidden = !why;
  $("#su-blocked").textContent = why || "";
  loadChain();
}

// ---- this machine ----

async function loadDoctor() {
  const box = $("#su-doctor");
  if (!box) return;
  const button = $("#su-doctor-again");
  if (button) button.disabled = true;
  try {
    U.doctor = await api("/api/setup/doctor");
  } catch (e) {
    box.innerHTML = `<div class="section-head"><h2>This machine</h2></div><div class="bad">${esc(e.message)}</div>`;
    return;
  }
  renderDoctor();
}

const GROUP_TITLES = { essentials: "Essentials", compilers: "Compilers", python: "Python's optional packages", tools: "Tools",
  models: "Language models" };

function fixLines(fix, manager) {
  if (!fix) return [];
  const out = [];
  for (const m of ["apt", "pacman", "dnf", "brew"]) {
    if (!(m in fix) || !(U.every || manager == null || m === manager)) continue;
    out.push(fix[m] ? `<div class="su-fix"><span class="su-pm">${m}</span><code>${esc(fix[m])}</code>${copyButton(fix[m])}</div>`
      : `<div class="su-fix"><span class="su-pm">${m}</span><span class="su-none">no package known</span></div>`);
  }
  if (fix.pip) out.push(`<div class="su-fix"><span class="su-pm">pip</span><code>${esc(fix.pip)}</code>${copyButton(fix.pip)}</div>`);
  if (fix.text) out.push(`<div class="su-fix su-words">${esc(fix.text)}</div>`);
  return out;
}

function renderDoctor() {
  const d = U.doctor, box = $("#su-doctor");
  if (!d || !box) return;
  const bad = d.findings.filter((f) => f.need === "essential" && f.state !== "ok");
  const problems = d.findings.filter((f) => f.state !== "ok").length;
  const sys = d.system;
  let html = `<div class="section-head"><h2>This machine</h2>
      <span class="su-verdict ${bad.length ? "bad" : "good"}">${bad.length ? "essentials missing: " + esc(bad.map((f) => f.what).join(", ")) : "the essentials are there"}</span>
      <span class="grow"></span>
      <span class="su-controls"><label class="su-check"><input type="checkbox" id="su-all"${U.allFindings ? " checked" : ""}> every finding</label>
      <label class="su-check"><input type="checkbox" id="su-every"${U.every ? " checked" : ""}> every package manager</label>
      <button type="button" id="su-doctor-again" title="dread doctor again">Check again</button></span></div>
    <div class="su-sub">${esc(sys.name || sys.platform)}${sys.manager ? ` · its package manager: <b>${esc(sys.manager)}</b>` : " · package manager not known: all four are shown"}
      · ${problems ? `${problems} thing${problems === 1 ? "" : "s"} to look at, with what to run (nothing here runs it)` : "nothing missing"}
      ${lineBox(d.line)}</div>`;
  const hidden = U.allFindings ? 0 : d.findings.filter((f) => f.state === "ok").length;
  for (const [group, title] of Object.entries(GROUP_TITLES)) {
    const rows = d.findings.filter((f) => f.group === group && (U.allFindings || f.state !== "ok"));
    if (!rows.length) continue;
    html += `<div class="su-group"><h3>${esc(title)}</h3>`;
    for (const f of rows) {
      const fixes = f.state === "ok" ? [] : fixLines(f.fix, sys.manager);
      html += `<div class="su-finding su-f-${esc(f.state)}"${f.state === "ok" && f.detail ? ` title="${esc(f.detail)}"` : ""}>
          ${stateChip(f.state)}<div class="su-fbody"><span class="su-what">${esc(f.what)}${f.need === "essential" ? ` <span class="su-ess">essential</span>` : ""}</span>
          <span class="su-found">${esc(f.found || "")}</span>
          ${f.detail ? `<div class="su-detail">${esc(f.detail)}</div>` : ""}
          ${fixes.length ? `<div class="su-fixes">${fixes.join("")}</div>` : ""}</div>
        </div>`;
    }
    html += `</div>`;
  }
  if (hidden) html += `<div class="su-muted su-oks">${hidden} more found as needed (${esc(d.findings.filter((f) => f.state === "ok").map((f) => f.what).join(", "))}): <a href="" id="su-show-all">show them</a></div>`;
  box.innerHTML = html;
}

// ---- the target ----

function renderTargetHead() {
  const head = $("#su-target-head");
  if (!head) return;
  const s = U.state, t = s.target;
  head.innerHTML = t ? `<div class="su-open"><b>${esc(t.title)}</b> <span class="su-muted">(${esc(t.name)}, image ${esc(t.image)})</span>
        <span class="mono su-path">${esc(t.root)}</span><span class="su-muted">${s.functions != null ? s.functions + " functions in its tables" : ""}</span>
        <span class="grow"></span><a class="button" href="#/">Overview</a><a class="button" href="#/run">Run a command</a></div>`
    : `<div class="su-open su-muted">No target is open: make one from an image below, or open one that exists.</div>`;
  head.innerHTML += `<div class="su-muted su-paths">Paths below are taken as typed, relative to <span class="mono">${esc(s.home)}</span>, where dread gui
      started (no ~, no variables: no shell).</div>`;
}

function archOptions(selected, forElf) {
  const first = forElf ? `<option value="">from the ELF</option>` : `<option value="" disabled${selected ? "" : " selected"}>choose</option>`;
  return first + U.state.arches.map((a) => `<option value="${esc(a.name)}"${a.name === selected ? " selected" : ""}>${esc(a.name)} · ${esc(a.about)}</option>`).join("");
}

function renderTarget() {
  const box = $("#su-target");
  if (!box) return;
  const f = U.form, why = blocked();
  box.innerHTML = `
    <div class="section-head"><h2>Target</h2></div>
    <div id="su-target-head"></div>
    <h3 class="su-h3">A new target from an image</h3>
    <div class="su-form" id="su-new">
      <label class="su-field"><span class="su-label">Directory</span>
        <input type="text" id="su-dir" value="${esc(f.dir)}" placeholder="a new or empty directory, e.g. targets/firmware" spellcheck="false" autocomplete="off">
        <span class="su-hint">dread new makes it; it must not hold anything but original/</span></label>
      <div class="su-field"><span class="su-label">Image</span>
        <div class="seg su-source" role="radiogroup" aria-label="Where the image comes from">
          <button type="button" data-source="path" class="${f.source === "path" ? "on" : ""}">a file on this machine</button>
          <button type="button" data-source="upload" class="${f.source === "upload" ? "on" : ""}">upload a file</button></div>
        <input type="text" id="su-path" value="${esc(f.path)}" placeholder="an ELF or a raw image, e.g. build/firmware.elf" spellcheck="false" autocomplete="off"${f.source === "path" ? "" : " hidden"}>
        <input type="file" id="su-file"${f.source === "upload" ? "" : " hidden"}>
        <span class="su-hint" id="su-inspect"></span></div>
      <label class="su-field" id="su-arch-row"><span class="su-label">Architecture</span>
        <select id="su-arch"></select><span class="su-hint" id="su-arch-hint"></span></label>
      <label class="su-field" id="su-base-row"><span class="su-label">Load address</span>
        <input type="text" id="su-base" value="${esc(f.base)}" placeholder="where the image lies, e.g. 0x08000000" spellcheck="false" autocomplete="off">
        <span class="su-hint">a raw image says nothing of it; an ELF does</span></label>
      <details class="su-more"${f.entry || f.limit || f.ram || f.io || f.name || f.compiler || f.unnamed ? " open" : ""}><summary>More options</summary>
        <label class="su-field"><span class="su-label">Entry</span><input type="text" id="su-entry" value="${esc(f.entry)}" placeholder="an address or a symbol (default: the ELF's, the vector table's, the base)" spellcheck="false" autocomplete="off"></label>
        <label class="su-field"><span class="su-label">Limit</span><input type="text" id="su-limit" value="${esc(f.limit)}" placeholder="the end of the flash: room for C that grows (default: guessed)" spellcheck="false" autocomplete="off"></label>
        <label class="su-field"><span class="su-label">RAM</span><input type="text" id="su-ram" value="${esc(f.ram)}" placeholder="BASE:SIZE, several separated by spaces (default: from the image)" spellcheck="false" autocomplete="off"></label>
        <label class="su-field"><span class="su-label">Device registers</span><input type="text" id="su-io" value="${esc(f.io)}" placeholder="LOW:HIGH, several separated by spaces (default: the architecture's)" spellcheck="false" autocomplete="off"></label>
        <label class="su-field"><span class="su-label">Name</span><input type="text" id="su-name" value="${esc(f.name)}" placeholder="default: the directory's" spellcheck="false" autocomplete="off"></label>
        <label class="su-field"><span class="su-label">Compiler</span><select id="su-compiler">
          <option value="">the original's where this machine has it</option><option${f.compiler === "gcc" ? " selected" : ""}>gcc</option><option${f.compiler === "clang" ? " selected" : ""}>clang</option></select></label>
        <label class="su-field su-flag" id="su-unnamed-row"><span class="su-label">Without names</span>
          <span><input type="checkbox" id="su-unnamed"${f.unnamed ? " checked" : ""}> every function FUN_address (a stripped variant, for the naming tools)</span></label>
      </details>
      ${lineBox("", "su-new-line")}
      <div class="cmd-error su-error" id="su-new-error"></div>
      <div class="su-actions"><span class="su-muted" id="su-new-where">runs in <span class="mono">${esc(U.state.home)}</span>, as a job; the target opens when it is made</span>
        <button type="button" class="primary" id="su-make"${disabledIf(why)}>Make the target</button></div>
      <div class="su-job" id="su-make-job" hidden></div>
    </div>
    <h3 class="su-h3">Or open a target that exists</h3>
    <div class="su-form su-inline">
      <input type="text" id="su-open-path" placeholder="its directory (with a dread.toml)" spellcheck="false" autocomplete="off">
      <button type="button" id="su-open"${disabledIf(why)}>Open</button>
      <span class="su-hint" id="su-open-hint">this page then shows that target (refused while a job of this page runs)</span>
    </div>`;
  renderTargetHead();
  applyKind();
  schedulePreview(true);
}

// The fields a kind of image needs: a raw one its architecture and load address, an ELF neither.
function applyKind() {
  const i = U.inspected, elf = i && i.kind === "elf";
  const arch = $("#su-arch");
  if (!arch) return;
  arch.innerHTML = archOptions(U.form.arch, elf);
  $("#su-base-row").hidden = elf;
  $("#su-unnamed-row").hidden = !elf;
  $("#su-arch-hint").textContent = elf ? "the ELF says it (choose one only to override)" : "a raw image needs it";
  const hint = $("#su-inspect");
  if (!i) hint.textContent = U.form.source === "path" ? "" : `at most ${bytes(GUI.uploadLimit)}; it goes into the new target's original/`;
  else if (i.kind === "elf") hint.innerHTML = `an ELF${i.machine ? " for " + esc(i.machine) : ""}, ${esc(bytes(i.size))}${i.supported === false ? ` <span class="bad">(not one dread new reads: 32-bit little-endian ARM, RISC-V, MSP430, MIPS)</span>` : ""}`;
  else if (i.kind === "raw") hint.textContent = `a raw image, ${bytes(i.size)}: give its architecture and load address`;
  else if (i.kind === "missing") hint.innerHTML = `<span class="bad">no such file: ${esc(i.path)}</span>`;
  else if (i.kind === "target" || i.kind === "directory") hint.innerHTML = `<span class="bad">${esc(i.path)} is a directory</span>`;
  else hint.textContent = i.error || "";
}

function newValues() {
  const f = U.form, out = {};
  if (f.dir.trim()) out.dir = f.dir.trim();
  const image = f.source === "upload" ? (f.file && f.dir.trim() ? f.dir.trim().replace(/\/+$/, "") + "/original/" + plainName(f.file.name) : "") : f.path.trim();
  if (image) out.image = image;
  const elf = U.inspected && U.inspected.kind === "elf";
  if (f.arch) out["--arch"] = f.arch;
  if (!elf && f.base.trim()) out["--base"] = f.base.trim();
  for (const [key, flag] of [["entry", "--entry"], ["limit", "--limit"], ["name", "--name"]]) if (f[key].trim()) out[flag] = f[key].trim();
  if (f.ram.trim()) out["--ram"] = f.ram.trim();
  if (f.io.trim()) out["--io"] = f.io.trim();
  if (f.compiler) out["--compiler"] = f.compiler;
  if (elf && f.unnamed) out["--unnamed"] = true;
  return out;
}

// (the server's plain_name, for the command line shown before the upload)
function plainName(name) {
  const base = String(name || "").split(/[\\/]/).pop();
  return (base.replace(/[^A-Za-z0-9._-]/g, "_").replace(/^\.+/, "").slice(0, 120)) || "image.bin";
}

function schedulePreview(now = false) {
  clearTimeout(U.lineTimer);
  U.lineTimer = setTimeout(askPreview, now ? 0 : PREVIEW_WAIT);
}

async function askPreview() {
  const seq = ++U.lineSeq;
  let d;
  try {
    d = await api("/api/command-line", { name: "new", values: JSON.stringify(newValues()) });
  } catch (e) {
    d = { line: null, args: null, error: e.message };
  }
  if (seq !== U.lineSeq || !$("#su-new-line")) return;
  Object.assign(U, { line: d.line, args: d.args, lineError: d.error });
  const box = $("#su-new-line").parentElement;
  box.outerHTML = lineBox(d.line || "", "su-new-line");
  const missing = !U.form.dir.trim() ? "the new target's directory" : !newValues().image ? "the image" : null;
  const error = $("#su-new-error");
  error.textContent = d.error ? (missing ? `give ${missing}` : d.error) : "";
  error.classList.toggle("su-wait", !!(d.error && missing));
  const make = $("#su-make");
  if (make && !blocked()) make.disabled = !!d.error || !d.args || !!U.making;
}

function scheduleInspect() {
  clearTimeout(U.inspectTimer);
  U.inspectTimer = setTimeout(async () => {
    const seq = ++U.inspectSeq, path = U.form.path.trim();
    if (!path) { U.inspected = null; applyKind(); return; }
    try {
      const d = await api("/api/setup/inspect", {}, { path });
      if (seq === U.inspectSeq) { U.inspected = d; applyKind(); schedulePreview(); }
    } catch (e) {
      if (seq === U.inspectSeq) { U.inspected = null; $("#su-inspect").textContent = e.message; }
    }
  }, 250);
}

async function makeTarget() {
  if (blocked() || U.making) return;
  const f = U.form, error = $("#su-new-error");
  const make = $("#su-make");
  make.disabled = true;
  error.textContent = "";
  try {
    if (f.source === "upload") {
      if (!f.file) throw new Error("choose a file to upload");
      if (f.file.size > GUI.uploadLimit) throw new Error(`the file has ${bytes(f.file.size)}, more than this server takes (${bytes(GUI.uploadLimit)}: dread gui --upload-limit)`);
      const box = $("#su-make-job");
      box.hidden = false;
      box.innerHTML = `<div class="su-muted">uploading ${esc(f.file.name)}… <span id="su-up">0 %</span></div>`;
      await upload(f.file, f.dir.trim(), (p) => { const e = $("#su-up"); if (e) e.textContent = Math.round(p * 100) + " %"; });
    }
    const r = await api("/api/setup/new", {}, { values: newValues() });
    U.making = { id: r.id, record: r, log: "", next: 0 };
    followMake();
  } catch (e) {
    error.textContent = e.message;
    make.disabled = false;
  }
}

function makeJobHTML(m) {
  const r = m.record;
  return `<div class="su-jobhead">${stateChip(r.state)} <a href="#/jobs/${r.id}">job ${r.id}</a>
      <span class="mono su-jline">${esc(r.line)}</span></div>
    <pre class="su-log">${esc(m.log.split("\n").slice(-40).join("\n")) || "…"}</pre>
    ${m.note ? `<div class="${m.bad ? "bad" : "su-muted"}">${esc(m.note)}</div>` : ""}
    ${m.retry ? `<div class="su-actions"><button type="button" id="su-open-made">Open the target</button></div>` : ""}`;
}

async function followMake() {
  clearTimeout(U.makeTimer);
  const m = U.making;
  if (!m) return;
  try {
    for (;;) {
      const d = await api("/api/job-log", { id: m.id, from: m.next });
      m.log += d.text;
      m.next = d.next;
      if (!d.more) break;
    }
    m.record = await api("/api/job", { id: m.id });
  } catch { /* the next look */ }
  const box = $("#su-make-job");
  if (box) { box.hidden = false; box.innerHTML = makeJobHTML(m); }
  if (LIVE.has(m.record.state)) { U.makeTimer = setTimeout(followMake, 700); return; }
  if (m.record.state === "done") return openMade();
  m.note = `dread new ${m.record.state === "failed" ? "refused (its words are above)" : "ended " + m.record.state}`;
  m.bad = true;
  U.making = null;
  if (box) box.innerHTML = makeJobHTML(m);
  if ($("#su-make") && !blocked()) $("#su-make").disabled = false;
  schedulePreview(true);
}

async function openMade() {
  const m = U.making;
  try {
    await api("/api/setup/open", {}, { job: m.id });
    toast("The new target is open.");
    location.hash = "#/setup";
    location.reload();
  } catch (e) {
    m.note = "made, but not opened: " + e.message;
    m.bad = true;
    m.retry = true;
    const box = $("#su-make-job");
    if (box) box.innerHTML = makeJobHTML(m);
  }
}

async function openPath() {
  const path = $("#su-open-path").value.trim();
  if (!path || blocked()) return;
  try {
    await api("/api/setup/open", {}, { path });
    location.hash = "#/setup";
    location.reload();
  } catch (e) {
    $("#su-open-hint").innerHTML = `<span class="bad">${esc(e.message)}</span>`;
  }
}

// ---- the first analyses ----

async function loadChain() {
  clearTimeout(U.chainTimer);
  U.chainTimer = 0;
  let d;
  try {
    d = await api("/api/setup/chain");
  } catch (e) {
    d = { steps: [], error: e.message };
  }
  if (S.view !== "setup") return;
  U.chain = d;
  renderChain();
  if (d.live) U.chainTimer = setTimeout(loadChain, POLL);
}

function stepTime(s) {
  if (!s.started) return "";
  const end = s.ended || Date.now() / 1000;
  return (LIVE.has(s.state) ? "for " : "took ") + seconds(end - s.started);
}

function renderChain() {
  const box = $("#su-chain"), d = U.chain;
  if (!box || !d) return;
  const why = blocked();
  const t = U.state && U.state.target;
  const running = d.steps.find((s) => s.job != null && LIVE.has(s.state) && s.state !== "queued");
  const ended = !d.planned && !d.live && d.steps.length;
  let html = `<div class="section-head"><h2>First analyses</h2>
      ${d.started ? `<span class="su-muted">started ${esc(ago(d.started))}</span>` : ""}<span class="grow"></span>
      ${running ? `<button type="button" id="su-chain-stop" data-job="${running.job}" title="Stop the step that runs; the steps after it are cancelled"${disabledIf(why)}>Stop</button>` : ""}
      ${t && !d.live && !d.error ? `<button type="button" class="primary" id="su-chain-start"${disabledIf(why)}>${ended ? "Run them again" : "Start"}</button>` : ""}</div>`;
  if (!t) html += `<div class="none">Make or open a target first: its first analyses come here.</div>`;
  else if (d.error) html += `<div class="bad su-chain-error">${esc(d.error)}</div>`;
  else {
    html += `<div class="su-muted su-chain-note">${d.planned ? "Start runs these one after another, each as a job that begins once the one before has succeeded:"
      : "each step a job that began once the one before had succeeded; one that fails cancels the rest"}</div><ol class="su-steps">`;
    for (const s of d.steps) {
      const state = s.state === "planned" ? "planned" : s.state;
      html += `<li class="su-step su-s-${esc(state)}">
          <div class="su-step-head">${stateChip(state)}<b>${esc(STEPS[s.step] || s.step)}</b>
            ${s.job != null ? `<a href="#/jobs/${s.job}">job ${s.job}</a>` : ""}<span class="su-muted">${esc(stepTime(s))}${s.exit != null && s.exit !== 0 ? " · exit status " + s.exit : ""}</span></div>
          <div class="su-step-why">${esc(s.why || "")}</div>
          ${s.line ? `<div class="mono su-step-line">$ ${esc(s.line)}</div>` : ""}
          ${s.last ? `<div class="mono su-step-last${s.state === "failed" ? " bad" : ""}">${esc(s.last)}</div>` : ""}
          ${s.why_not && !LIVE.has(s.state) && s.state !== "done" ? `<div class="su-muted">${esc(s.why_not)}</div>` : ""}
        </li>`;
    }
    html += `</ol>`;
    const line = d.steps.filter((s) => s.line).map((s) => s.line).join(" && ");
    if (line) html += `<div class="su-muted">The same in a terminal, in the target's directory:</div>${lineBox(line)}`;
  }
  box.innerHTML = html;
}

async function startChain() {
  if (blocked()) return;
  try {
    U.chain = await api("/api/setup/chain", {}, {});
    renderChain();
    if (U.chain.live) U.chainTimer = setTimeout(loadChain, POLL);
  } catch (e) {
    toast(e.message, true);
  }
}

async function stopStep(id) {
  try {
    await api("/api/jobs/stop", {}, { id });
    loadChain();
  } catch (e) {
    toast(e.message, true);
  }
}

// ---- the language models ----

async function loadProviders() {
  try {
    U.providers = await api("/api/setup/providers");
  } catch (e) {
    U.providers = { error: e.message, providers: [], kinds: [] };
  }
  renderModels();
}

function keyWords(k) {
  if (!k) return "";
  if (k.set && k.how.startsWith("env ")) return `<span class="good">set</span> <span class="su-muted">($${esc(k.how.slice(4))})</span>`;
  if (k.set) return `<span class="good" title="${esc(k.how.slice(5))}">set</span> <span class="su-muted" title="${esc(k.how.slice(5))}">(key file)</span>`;
  if (k.problem) return `<span class="bad">not set</span> <span class="su-muted">(${esc(k.problem)})</span>`;
  return k.needed ? `<span class="warn">not set</span>` : `<span class="su-muted">none needed</span>`;
}

function price(p) {
  if (p.price_in == null && p.price_out == null) return "";
  return `$${p.price_in ?? "?"} / $${p.price_out ?? "?"}`;
}

function renderModels() {
  const box = $("#su-models"), d = U.providers;
  if (!box || !d) return;
  const why = blocked();
  let html = `<div class="section-head"><h2>Language models</h2><span class="grow"></span>
      <button type="button" id="su-prov-new"${disabledIf(why)}>Add a provider</button></div>
    <div class="su-sub">dread pilot asks them; configured in <span class="mono">${esc(d.path || "")}</span>${d.exists === false ? " (not there yet: saving a provider makes it)" : ""}.
      A key typed here goes into a key file readable by you alone (mode 600) and is never shown again.</div>`;
  if (d.error) html += `<div class="bad">${esc(d.error)}</div>`;
  if (d.providers.length) {
    html += `<div class="su-table" role="table"><div class="su-tr su-th" role="row"><span>name</span><span>kind</span><span>model</span><span>server</span><span>price in / out per Mtok</span><span>key</span><span></span></div>`;
    for (const p of d.providers) {
      const test = U.tests[p.name];
      html += `<div class="su-tr" role="row" data-provider="${esc(p.name)}">
          <span class="su-pname"><b>${esc(p.name)}</b>${p.default ? ` <span class="su-default">default</span>` : ""}</span>
          <span>${esc(p.kind || "?")}</span><span class="mono">${esc(p.model || "")}</span>
          <span class="mono su-url">${esc(p.kind === "command" ? (p.command || "") : (p.url || (p.kind === "anthropic" ? "api.anthropic.com" : "")))}</span>
          <span>${esc(price(p))}${p.cache_min != null ? ` <span class="su-muted">cache ≥ ${esc(p.cache_min)}</span>` : ""}${p.batch === false ? ` <span class="su-muted">no batches</span>` : ""}</span>
          <span>${keyWords(p.key)}</span>
          <span class="su-row-actions"><button type="button" data-edit="${esc(p.name)}"${disabledIf(why)}>Edit</button>
            <button type="button" data-test="${esc(p.name)}"${disabledIf(why)} title="dread llm ask --provider ${esc(p.name)}: one question, as a job">Test</button></span>
        </div>
        ${test ? `<div class="su-test" data-test-of="${esc(p.name)}">${testHTML(test)}</div>` : ""}`;
    }
    html += `</div>`;
  } else if (!d.error) html += `<div class="none">No provider yet.</div>`;
  html += `<div id="su-prov-form"></div>`;
  box.innerHTML = html;
  if (U.edit) renderProviderForm();
}

function providerFormHTML() {
  const e = U.edit, why = blocked();
  const kinds = (U.providers.kinds || ["openai", "anthropic", "command"]).map((k) => `<option${k === e.kind ? " selected" : ""}>${esc(k)}</option>`).join("");
  return `<div class="su-form su-prov">
      <h3 class="su-h3">${e.isNew ? "A new provider" : "Provider " + esc(e.name)}</h3>
      <label class="su-field"><span class="su-label">Name</span><input type="text" id="sp-name" value="${esc(e.name)}"${e.isNew ? "" : " readonly"} placeholder="its name in llm.toml, e.g. claude" spellcheck="false" autocomplete="off"></label>
      <label class="su-field"><span class="su-label">Kind</span><select id="sp-kind"${e.kind === "command" && !e.isNew ? " disabled" : ""}>${kinds}</select>
        <span class="su-hint">${e.kind === "command" ? "a command provider's command line is written into llm.toml by hand" : "openai: any OpenAI-compatible server (llama-server, Ollama, vLLM); anthropic"}</span></label>
      <label class="su-field"><span class="su-label">Model</span><input type="text" id="sp-model" value="${esc(e.model)}" placeholder="e.g. claude-sonnet-5-5, or the server's model name" spellcheck="false" autocomplete="off"></label>
      <label class="su-field" id="sp-url-row"${e.kind === "command" ? " hidden" : ""}><span class="su-label">Server's URL</span><input type="text" id="sp-url" value="${esc(e.url)}" placeholder="${e.kind === "anthropic" ? "default: https://api.anthropic.com" : "its base, e.g. http://127.0.0.1:8080"}" spellcheck="false" autocomplete="off"></label>
      <label class="su-field"><span class="su-label">Price in</span><input type="text" inputmode="decimal" id="sp-price-in" value="${esc(e.price_in)}" placeholder="US dollars per million tokens in (its price list)" autocomplete="off"></label>
      <label class="su-field"><span class="su-label">Price out</span><input type="text" inputmode="decimal" id="sp-price-out" value="${esc(e.price_out)}" placeholder="US dollars per million tokens out" autocomplete="off"></label>
      <label class="su-field" id="sp-cache-row"${e.kind === "anthropic" ? "" : " hidden"}><span class="su-label">Cache minimum</span><input type="text" inputmode="numeric" id="sp-cache-min" value="${esc(e.cache_min)}" placeholder="tokens: the model's least cacheable prompt (512 for Haiku, Sonnet and Opus 5.5)" autocomplete="off"></label>
      <label class="su-field" id="sp-batch-row"${e.kind === "anthropic" ? "" : " hidden"}><span class="su-label">Message Batches</span><select id="sp-batch">
        <option value=""${e.batch == null ? " selected" : ""}>default (on)</option><option value="true"${e.batch === true ? " selected" : ""}>on</option><option value="false"${e.batch === false ? " selected" : ""}>off</option></select></label>
      <label class="su-field su-flag"><span class="su-label">Default</span><span><input type="checkbox" id="sp-default"${e.default ? " checked" : ""}> the provider dread pilot asks unless told otherwise</span></label>
      <div class="su-field su-keyrow"${e.kind === "command" ? " hidden" : ""}><span class="su-label">Key</span>
        <input type="password" id="sp-key" placeholder="${e.key && e.key.set ? "set: type a new one to replace it" : "paste the key"}" autocomplete="new-password" spellcheck="false">
        <span class="su-hint">${e.key ? keyWords(e.key) : ""} · written into a key file of mode 600, never shown again</span></div>
      <label class="su-field su-keyrow"${e.kind === "command" ? " hidden" : ""}><span class="su-label">Key file</span><input type="text" id="sp-key-file" value="" placeholder="${esc(e.key_file || "default: keys/NAME beside llm.toml")}" spellcheck="false" autocomplete="off"></label>
      <div class="su-muted">The same on the command line:</div>
      ${lineBox(U.provLine || "", "sp-line")}
      <div class="su-muted su-keyline" id="sp-keyline"></div>
      <div class="cmd-error su-error" id="sp-error"></div>
      <div class="su-actions"><button type="button" id="sp-cancel">Cancel</button><button type="button" class="primary" id="sp-save"${disabledIf(why)}>Save</button></div>
    </div>`;
}

function renderProviderForm() {
  const box = $("#su-prov-form");
  if (!box) return;
  box.innerHTML = U.edit ? providerFormHTML() : "";
  if (U.edit) scheduleProvLine(true);
}

function editProvider(name) {
  const p = name ? U.providers.providers.find((x) => x.name === name) : null;
  U.edit = p ? { isNew: false, name: p.name, kind: p.kind, model: p.model || "", url: p.url || "", price_in: p.price_in ?? "",
    price_out: p.price_out ?? "", cache_min: p.cache_min ?? "", batch: p.batch ?? null, default: p.default, key: p.key, key_file: p.key_file,
    original: p } : { isNew: true, name: "", kind: "anthropic", model: "", url: "", price_in: "", price_out: "", cache_min: "", batch: null,
    default: !U.providers.providers.length, key: null, key_file: null, original: null };
  U.provLine = null;
  renderProviderForm();
  const form = $("#su-prov-form");
  if (form) form.scrollIntoView({ block: "nearest" });
}

// What the form changes against the provider as it is: {changes, unset, default}.
function providerChange() {
  const e = U.edit, o = e.original || {};
  const val = (id) => { const el = $("#" + id); return el ? el.value.trim() : ""; };
  const kind = val("sp-kind") || e.kind;
  // (the fields this kind shows: one it does not show is left as it is, not taken out)
  const want = { kind, model: val("sp-model"), price_in: val("sp-price-in"), price_out: val("sp-price-out") };
  if (kind !== "command") want.url = val("sp-url");
  if (kind === "anthropic") {
    want.cache_min = val("sp-cache-min");
    want.batch = val("sp-batch") ? val("sp-batch") === "true" : null;
  }
  const changes = {}, unset = [];
  for (const [field, value] of Object.entries(want)) {
    const before = o[field] == null ? "" : o[field];
    if (field === "batch") {
      if (value != null && value !== o.batch) changes.batch = value;
      else if (value == null && o.batch != null) unset.push("batch");
      continue;
    }
    if (String(value) === String(before)) continue;
    if (value === "") { if (!["kind", "model"].includes(field) && o[field] != null) unset.push(field); }
    else changes[field] = value;
  }
  const def = $("#sp-default") && $("#sp-default").checked && !o.default;
  return { name: val("sp-name"), changes, unset, default: def };
}

function scheduleProvLine(now = false) {
  clearTimeout(U.provTimer);
  U.provTimer = setTimeout(askProvLine, now ? 0 : PREVIEW_WAIT);
}

async function askProvLine() {
  if (!U.edit) return;
  const seq = ++U.provSeq, c = providerChange();
  const values = { name: c.name };
  for (const [field, value] of Object.entries(c.changes)) {
    if (field === "batch") values["--batch"] = value;
    else values["--" + field.replace(/_/g, "-")] = String(value);
  }
  if (c.default) values["--default"] = true;
  if (c.unset.length) values["--unset"] = c.unset;
  let d;
  try {
    d = await api("/api/command-line", { name: "llm", values: JSON.stringify({ action: { command: "set", values } }) });
  } catch (e) {
    d = { line: null, error: e.message };
  }
  if (seq !== U.provSeq || !$("#sp-line")) return;
  U.provLine = d.line;
  $("#sp-line").parentElement.outerHTML = lineBox(d.line || "", "sp-line");
  const key = $("#sp-key") && $("#sp-key").value;
  const file = $("#sp-key-file") ? $("#sp-key-file").value.trim() : "";
  $("#sp-keyline").innerHTML = key ? `and the key: <span class="mono">dread llm key ${esc(c.name || "NAME")}${file ? " --file " + esc(file) : ""}</span>, which reads it from standard input` : "";
  const nothing = !Object.keys(c.changes).length && !c.unset.length && !c.default && !key;
  $("#sp-error").textContent = !c.name ? "give the provider a name" : nothing ? "nothing changed yet" : (d.error && !/arguments are required/.test(d.error) ? d.error : "");
}

async function saveProvider() {
  if (blocked() || !U.edit) return;
  const c = providerChange(), error = $("#sp-error");
  const keyField = $("#sp-key"), key = keyField ? keyField.value : "";
  const file = $("#sp-key-file") ? $("#sp-key-file").value.trim() : "";
  if (!c.name) { error.textContent = "give the provider a name"; return; }
  $("#sp-save").disabled = true;
  try {
    if (Object.keys(c.changes).length || c.unset.length || c.default || U.edit.isNew) {
      const r = await api("/api/setup/provider", {}, { name: c.name, changes: c.changes, unset: c.unset, default: c.default });
      U.providers = r.providers;
    }
    if (key) {
      if (keyField) keyField.value = "";
      await api("/api/setup/key", {}, { name: c.name, key, file });
    }
    toast(`Provider ${c.name} saved.`);
    U.edit = null;
    await loadProviders();
    loadDoctor();
  } catch (e) {
    error.textContent = e.message;
    $("#sp-save").disabled = false;
  }
}

function testHTML(t) {
  const r = t.record;
  return `<div class="su-jobhead">${stateChip(r.state)} <a href="#/jobs/${r.id}">job ${r.id}</a><span class="mono su-jline">${esc(r.line)}</span></div>
    <pre class="su-log">${esc(t.log.split("\n").slice(-30).join("\n")) || "…"}</pre>`;
}

async function testProvider(name) {
  if (blocked()) return;
  try {
    const r = await api("/api/setup/ask", {}, { name });
    U.tests[name] = { record: r, log: "", next: 0 };
    renderModels();
    followTests();
  } catch (e) {
    toast(e.message, true);
  }
}

async function followTests() {
  clearTimeout(U.testTimer);
  let live = false;
  for (const [name, t] of Object.entries(U.tests)) {
    if (t.final) continue;
    try {
      const d = await api("/api/job-log", { id: t.record.id, from: t.next });
      t.log += d.text;
      t.next = d.next;
      t.record = await api("/api/job", { id: t.record.id });
    } catch { continue; }
    if (!LIVE.has(t.record.state)) t.final = true; else live = true;
    const box = document.querySelector(`[data-test-of="${CSS.escape(name)}"]`);
    if (box) box.innerHTML = testHTML(t);
  }
  if (live && S.view === "setup") U.testTimer = setTimeout(followTests, 1000);
}

// ---- the wiring ----

function wire() {
  const main = $("#main");
  main.addEventListener("input", (e) => {
    if (S.view !== "setup") return;
    const id = e.target.id, f = U.form;
    const fields = { "su-dir": "dir", "su-base": "base", "su-entry": "entry", "su-limit": "limit", "su-ram": "ram", "su-io": "io",
      "su-name": "name" };
    if (fields[id]) { f[fields[id]] = e.target.value; schedulePreview(); return; }
    if (id === "su-path") { f.path = e.target.value; scheduleInspect(); schedulePreview(); return; }
    if (e.target.closest(".su-prov")) scheduleProvLine();
  });
  main.addEventListener("change", async (e) => {
    if (S.view !== "setup") return;
    const id = e.target.id, f = U.form;
    if (id === "su-arch") { f.arch = e.target.value; schedulePreview(); return; }
    if (id === "su-compiler") { f.compiler = e.target.value; schedulePreview(); return; }
    if (id === "su-unnamed") { f.unnamed = e.target.checked; schedulePreview(); return; }
    if (id === "su-every") { U.every = e.target.checked; renderDoctor(); return; }
    if (id === "su-all") { U.allFindings = e.target.checked; renderDoctor(); return; }
    if (id === "su-file") {
      f.file = e.target.files[0] || null;
      U.inspected = f.file ? await inspectFile(f.file) : null;
      applyKind();
      schedulePreview();
      return;
    }
    if (id === "sp-kind") {
      U.edit.kind = e.target.value;
      for (const [row, kind] of [["sp-cache-row", "anthropic"], ["sp-batch-row", "anthropic"]]) $("#" + row).hidden = U.edit.kind !== kind;
      $("#sp-url-row").hidden = U.edit.kind === "command";
      $("#sp-url").placeholder = U.edit.kind === "anthropic" ? "default: https://api.anthropic.com" : "its base, e.g. http://127.0.0.1:8080";
      for (const row of document.querySelectorAll(".su-keyrow")) row.hidden = U.edit.kind === "command";
    }
    if (e.target.closest(".su-prov")) scheduleProvLine();
  });
  main.addEventListener("click", (e) => {
    if (S.view !== "setup") return;
    const copy = e.target.closest("[data-copy]");
    if (copy) {
      (navigator.clipboard ? navigator.clipboard.writeText(copy.dataset.copy) : Promise.reject(new Error("no clipboard here")))
        .then(() => toast("Copied."), (err) => toast("Not copied: " + err.message, true));
      return;
    }
    const source = e.target.closest("[data-source]");
    if (source) {
      U.form.source = source.dataset.source;
      U.inspected = null;
      for (const b of document.querySelectorAll(".su-source button")) b.classList.toggle("on", b === source);
      $("#su-path").hidden = U.form.source !== "path";
      $("#su-file").hidden = U.form.source !== "upload";
      if (U.form.source === "path") scheduleInspect();
      else if (U.form.file) inspectFile(U.form.file).then((i) => { U.inspected = i; applyKind(); schedulePreview(); });
      applyKind();
      schedulePreview();
      return;
    }
    if (e.target.closest("#su-doctor-again")) return loadDoctor();
    if (e.target.closest("#su-show-all")) { e.preventDefault(); U.allFindings = true; renderDoctor(); return; }
    if (e.target.closest("#su-make")) return makeTarget();
    if (e.target.closest("#su-open-made")) return openMade();
    if (e.target.closest("#su-open")) return openPath();
    if (e.target.closest("#su-chain-start")) return startChain();
    const stop = e.target.closest("#su-chain-stop");
    if (stop) return stopStep(Number(stop.dataset.job));
    if (e.target.closest("#su-prov-new")) return editProvider(null);
    const edit = e.target.closest("[data-edit]");
    if (edit) return editProvider(edit.dataset.edit);
    const test = e.target.closest("[data-test]");
    if (test) return testProvider(test.dataset.test);
    if (e.target.closest("#sp-cancel")) { U.edit = null; renderProviderForm(); return; }
    if (e.target.closest("#sp-save")) return saveProvider();
  });
  main.addEventListener("keydown", (e) => {
    if (S.view !== "setup" || e.key !== "Enter") return;
    if (e.target.id === "su-open-path") { e.preventDefault(); openPath(); }
  });
}

function onStatus(s) {
  if (!s || S.view !== "setup") return;
  const seen = JSON.stringify(s.chain || null);
  if (U.chainSeen !== null && seen !== U.chainSeen && !U.chainTimer) loadChain();
  U.chainSeen = seen;
}

wire();

register({
  name: "setup", match: /^\/setup$/, show: showSetup,
  leave: () => { clearTimeout(U.chainTimer); U.chainTimer = 0; clearTimeout(U.lineTimer); clearTimeout(U.provTimer); },
  status: onStatus,
  keys: { u: () => { location.hash = "#/setup"; } },
  help: [["u", "set up: what this machine has, a new target from an image, its first analyses, the language models"]],
  nav: { title: "Setup (u)", href: "#/setup", icon: ICON },
});
