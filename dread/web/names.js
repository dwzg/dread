// dread gui's names page (the server's dread/guipages/names.py): names for this image's functions from
// elsewhere, each source a command run as a job (dread whatcc: the toolchains' libraries; dread samecode: the
// same code in another image; dread callpair: the order of calls), its proposals listed, and the chosen ones
// applied with dread rename, a job too. Only a proposal that gives a function without a name (FUN_...) a name
// is offered. Address: #/names.

import { $, esc, api, ago, toast, register, enter, nextRender, isCurrent } from "./app.js";

const POLL = 1500;              // ms between looks while a job of this page runs
const SHOWN = 300;              // proposals a table lists
const ICON = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 4h7M3 8h5M3 12h7"/><path d="M11 9l2 2 3-4"/></svg>`;

const N = { data: null, timer: 0, chosen: { whatcc: new Set(), samecode: new Set(), callpair: new Set() } };

const TITLES = {
  whatcc: ["The toolchains' libraries", "dread whatcc: functions whose code is a library function of one of the toolchains"],
  samecode: ["The same code in another image", "dread samecode: functions that are the same instructions as another image's"],
  callpair: ["The order of calls", "dread callpair: names that follow from the order in which paired functions call"],
};

function hex(n) { return "0x" + Number(n).toString(16); }

function formHTML(tool, d) {
  const other = (d.other || {}).other || "", image = (d.other || {}).other_image || "";
  if (tool === "whatcc") return "";
  const seeds = tool === "callpair" ? `<label class="nm-field">seeds, one HERE=THERE a line
      <textarea id="nm-${tool}-seeds" rows="2" spellcheck="false" placeholder="main=main"></textarea></label>` : "";
  const min = tool === "samecode" ? `<label class="nm-field">instructions at least
      <input type="number" id="nm-${tool}-min" min="1" value="3" class="nm-num"></label>` : "";
  return `<div class="nm-form">
      <label class="nm-field">the other target (its directory, as typed from where dread gui started)
        <input type="text" id="nm-${tool}-other" value="${esc(other)}" placeholder="../named-build"></label>
      <label class="nm-field">its image <input type="text" id="nm-${tool}-image" value="${esc(image)}" placeholder="its first" class="nm-short"></label>
      ${min}${seeds}</div>`;
}

function proposalsHTML(tool, result) {
  if (!result) return `<p class="nm-muted">Not run on this target yet.</p>`;
  const rows = result.proposals || [];
  const chosen = N.chosen[tool];
  const head = tool === "whatcc"
    ? `${result.matched} functions matched a library's; ${rows.length} of them without a name get one` +
      (result.summary && result.summary.best ? ` (likeliest toolchain: ${esc(result.summary.best.join(", "))})` : "")
    : tool === "samecode"
      ? `${(result.record.pairs || []).length} pairs of the same code; ${rows.length} give a function without a name one`
      : `${result.record.seeds} seeds, ${(result.record.pairs || []).length} pairs by call order; ${rows.length} give a function without a name one`;
  const when = result.when ? ` · ${ago(result.when * 1000)}` : "";
  if (!rows.length) return `<p class="nm-muted">${head}${when}.</p>`;
  const body = rows.slice(0, SHOWN).map((r) => `<tr><td><input type="checkbox" class="nm-pick" data-tool="${tool}"
      data-here="${esc(r.here)}" data-there="${esc(r.there)}"${chosen.has(r.here) ? " checked" : ""}></td>
      <td class="mono">${hex(r.entry)}</td><td class="mono"><a href="#/f/${encodeURIComponent(r.here)}">${esc(r.here)}</a></td>
      <td class="mono">${esc(r.there)}</td>${tool === "whatcc" ? `<td class="nm-muted">${esc((r.toolchains || []).join(", "))}</td>` : ""}</tr>`).join("");
  return `<p class="nm-muted">${head}${when}.</p>
    <table class="nm-table"><thead><tr><th><input type="checkbox" class="nm-all" data-tool="${tool}" title="all"></th>
      <th>entry</th><th>here</th><th>proposed</th>${tool === "whatcc" ? "<th>toolchains</th>" : ""}</tr></thead><tbody>${body}</tbody></table>
    ${rows.length > SHOWN ? `<p class="nm-muted">the first ${SHOWN} of ${rows.length}</p>` : ""}`;
}

function cardHTML(tool, d) {
  const [title, sub] = TITLES[tool];
  const running = (d.live || []).some((j) => (" " + j.line + " ").includes(" " + tool + " "));
  return `<section class="nm-card" id="nm-${tool}"><h2>${esc(title)}</h2><p class="nm-sub">${esc(sub)}</p>
    ${formHTML(tool, d)}
    <div class="nm-actions"><button type="button" class="nm-run" data-tool="${tool}"${running ? " disabled" : ""}>${running ? "Running…" : "Run"}</button>
      <button type="button" class="nm-apply" data-tool="${tool}">Apply the chosen names</button></div>
    <div class="nm-result">${proposalsHTML(tool, d[tool])}</div></section>`;
}

function render(d) {
  const main = enter("names", "Names from elsewhere");
  const live = (d.live || []).map((j) => `<a href="#/jobs/${j.id}">job ${j.id}</a> ${esc(j.state)}: <span class="mono">${esc(j.line)}</span>`);
  main.innerHTML = `<div class="nm-page"><h1>Names from elsewhere</h1>
    <p class="nm-sub">Each source is a command run as a job; a proposal is offered only where it gives a function without a name one,
      and dread rename applies the chosen ones everywhere a name is written.</p>
    ${live.length ? `<p class="nm-live">${live.join("<br>")}</p>` : ""}
    ${["whatcc", "samecode", "callpair"].map((t) => cardHTML(t, d)).join("")}</div>`;
  main.querySelectorAll(".nm-run").forEach((b) => b.addEventListener("click", () => run(b.dataset.tool)));
  main.querySelectorAll(".nm-apply").forEach((b) => b.addEventListener("click", () => apply(b.dataset.tool)));
  main.querySelectorAll(".nm-pick").forEach((c) => c.addEventListener("change", () => {
    const set = N.chosen[c.dataset.tool];
    if (c.checked) set.add(c.dataset.here); else set.delete(c.dataset.here);
  }));
  main.querySelectorAll(".nm-all").forEach((c) => c.addEventListener("change", () => {
    main.querySelectorAll(`.nm-pick[data-tool="${c.dataset.tool}"]`).forEach((p) => {
      p.checked = c.checked;
      p.dispatchEvent(new Event("change"));
    });
  }));
}

async function run(tool) {
  const v = (id) => ($(id) ? $(id).value.trim() : "");
  const body = { tool };
  if (tool !== "whatcc") {
    body.other = v(`#nm-${tool}-other`);
    body.other_image = v(`#nm-${tool}-image`);
    if (tool === "samecode") body.min = v(`#nm-${tool}-min`) || "3";
    if (tool === "callpair") body.seeds = v(`#nm-${tool}-seeds`).split("\n").map((s) => s.trim()).filter(Boolean);
  }
  try {
    const job = await api("/api/names/run", {}, body);
    toast(`${tool} started: job ${job.id}`);
    refresh();
  } catch (e) {
    toast(e.message, true);
  }
}

async function apply(tool) {
  const result = N.data && N.data[tool];
  const names = {};
  for (const r of (result && result.proposals) || []) if (N.chosen[tool].has(r.here)) names[r.here] = r.there;
  if (!Object.keys(names).length) { toast("nothing chosen", true); return; }
  try {
    const job = await api("/api/names/apply", {}, { names });
    toast(`dread rename started: job ${job.id}`);
    N.chosen[tool].clear();
    refresh();
  } catch (e) {
    toast(e.message, true);
  }
}

async function refresh() {
  clearTimeout(N.timer);
  const mine = nextRender();
  let d;
  try {
    d = await api("/api/names");
  } catch (e) {
    if (!isCurrent(mine)) return;
    enter("names", "Names from elsewhere").innerHTML = `<div class="nm-page"><p class="warn">${esc(e.message)}</p></div>`;
    return;
  }
  if (!isCurrent(mine)) return;
  N.data = d;
  render(d);
  if ((d.live || []).length) N.timer = setTimeout(refresh, POLL);
}

register({
  name: "names",
  match: /^\/names$/,
  show: async () => { await refresh(); },
  leave: () => { clearTimeout(N.timer); },
  status: () => {},
  keys: { e: () => { location.hash = "#/names"; } },
  help: [["e", "names from elsewhere: libraries, the same code, the order of calls"]],
  nav: { title: "Names from elsewhere (e)", href: "#/names", icon: ICON },
});
