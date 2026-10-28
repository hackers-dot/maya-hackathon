"use strict";
// MAYA web client. Server strings are written with textContent only; elements are built with createElement.
// Chat is not implemented yet: nothing here generates or fakes replies.
(function () {
  let csrf = null;
  let pollTimer = null;
  let traceNext = 0;
  let currentAsset = null;
  const $ = (id) => document.getElementById(id);
  const PREVIEW = window.location.pathname.startsWith("/__preview/");

  // ---------- small DOM helpers (no HTML strings anywhere) ----------
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function clear(node) { while (node && node.firstChild) node.removeChild(node.firstChild); }
  function show(id, visible) { const n = $(id); if (n) n.hidden = !visible; }
  function setText(id, text) { const n = $(id); if (n) n.textContent = text === null || text === undefined ? "" : String(text); }
  function setChip(id, text, kind) {
    const n = $(id);
    if (!n) return;
    n.textContent = text;
    n.className = "chip" + (kind ? " chip-" + kind : "");
  }

  function say(text, isError) {
    const n = $("message");
    if (!n) return;
    n.textContent = text;
    n.className = isError ? "message error" : "message";
  }

  function go(next) {
    // only same-origin paths from the server, never a full URL
    if (typeof next === "string" && next.startsWith("/") && !next.startsWith("//")) {
      window.location.assign(next);
    }
  }

  async function getJSON(path) {
    const r = await fetch(path, { credentials: "same-origin" });
    let data = {};
    try { data = await r.json(); } catch (e) { data = {}; }
    return { ok: r.ok, status: r.status, data: data };
  }

  async function loadSession() {
    const r = await getJSON("/api/session");
    csrf = r.data.csrf;
    return r.data;
  }

  async function post(path, body) {
    say("", false);
    let r;
    try {
      r = await fetch(path, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf || "" },
        body: JSON.stringify(body),
      });
    } catch (e) {
      say("Could not reach MAYA. Is the server running?", true);
      return null;
    }
    let data = {};
    try { data = await r.json(); } catch (e) { data = {}; }
    if (!r.ok) {
      say(typeof data.error === "string" ? data.error : "Request failed (" + r.status + ")", true);
      return null;
    }
    if (typeof data.csrf === "string") csrf = data.csrf;
    go(data.next);
    return data;
  }

  function onClick(id, handler) {
    const n = $(id);
    if (n) n.addEventListener("click", handler);
  }

  function onSubmit(id, handler) {
    const form = $(id);
    if (form) form.addEventListener("submit", (e) => { e.preventDefault(); handler(form.elements); });
  }

  // ---------- theme ----------
  function toggleTheme() {
    const next = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", next);
    try { window.localStorage.setItem("maya-theme", next); } catch (e) { /* storage unavailable: still switches */ }
  }

  // ---------- sign-in / sign-up tabs ----------
  function showTab(which) {
    const signin = which === "signin";
    const pairs = [["tab-signin", "signin-form", signin], ["tab-signup", "signup-form", !signin]];
    for (const [tabId, formId, active] of pairs) {
      const tab = $(tabId);
      const form = $(formId);
      if (!tab || !form) continue;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
      form.hidden = !active;
    }
    say("", false);
  }

  // ---------- asset status words ----------
  const STATUS = {
    loopback: ["demo app", "loopback"],
    verified: ["verified", "ok"],
    pending: ["pending", "pending"],
    failed: ["failed", "failed"],
  };
  function statusChip(id, status) {
    const s = STATUS[status] || [String(status || ""), ""];
    setChip(id, s[0], s[1]);
  }

  // ---------- connect-website card ----------
  function renderAssetPanel(asset) {
    if (!$("asset-panel")) return;
    currentAsset = asset;
    show("asset-panel", true);
    setText("ap-label", asset.label);
    statusChip("ap-status", asset.status);
    const reasons = {
      loopback: "MAYA will scan its own demo app on this computer. Nothing outside it is touched.",
      verified: "Ownership verified. You can run a scan from the MAYA home screen.",
      pending: "Waiting for you to add the token, then press Verify ownership.",
    };
    setText("ap-reason", asset.status === "failed" ? "Verification failed: " + (asset.reason || "unknown reason")
      : (reasons[asset.status] || ""));
    const v = asset.verification;
    show("ap-methods", !!v && asset.status !== "verified");
    if (v) {
      setText("ap-meta", v.meta_tag);
      setText("ap-file", v.file_url);
      setText("ap-token", v.token);
    }
  }

  async function initConnectWebsite() {
    if (!$("asset-form")) return;
    onSubmit("asset-form", async (f) => {
      const d = await post("/api/assets", { url: f.url.value });
      if (d && d.asset) renderAssetPanel(d.asset);
    });
    onClick("use-demo", async () => {
      const d = await post("/api/assets", { demo: true });
      if (d && d.asset) renderAssetPanel(d.asset);
    });
    onClick("verify-btn", async () => {
      if (!currentAsset) return;
      say("Checking your site...", false);
      const d = await post("/api/assets/" + currentAsset.id + "/verify", {});
      if (d && d.asset) {
        renderAssetPanel(d.asset);
        say(d.asset.status === "verified" ? "Verified." : "Not verified yet.", d.asset.status !== "verified");
      }
    });
    if (PREVIEW) {
      const sample = await getJSON("/__preview/sample.json");
      if (sample.ok) renderAssetPanel(sample.data.assets[1]);
      return;
    }
    const r = await getJSON("/api/assets");
    if (r.ok && r.data.assets.length) {
      const pending = r.data.assets.filter((a) => a.status === "pending" || a.status === "failed");
      renderAssetPanel(pending.length ? pending[pending.length - 1] : r.data.assets[r.data.assets.length - 1]);
    }
  }

  // ---------- app shell: sidebar ----------
  function setSidebar(open) {
    document.body.classList.toggle("sidebar-open", open);
    const btn = $("sidebar-open");
    if (btn) btn.setAttribute("aria-expanded", open ? "true" : "false");
  }

  function renderAssetList(assets) {
    const list = $("assets-list");
    if (!list) return;
    clear(list);
    for (const a of assets) {
      const li = el("li");
      const btn = el("button", "asset-item");
      btn.type = "button";
      btn.appendChild(el("span", "asset-name", a.kind === "demo" ? "Demo app (loopback)" : a.label));
      const s = STATUS[a.status] || [a.status, ""];
      btn.appendChild(el("span", "chip chip-" + s[1], s[0]));
      btn.addEventListener("click", () => { selectAsset(a); setSidebar(false); });
      li.appendChild(btn);
      list.appendChild(li);
    }
    show("assets-empty", assets.length === 0);
  }

  // ---------- app shell: asset view, live trace, results ----------
  const RUN_CHIP = {
    running: ["running", "running"],
    finished: ["finished", "ok"],
    forced_final: ["forced final", "forced"],
    budget_exceeded: ["token cap reached", "failed"],
    failed: ["failed", "failed"],
  };

  function tokensText(t) {
    if (!t) return "";
    return (t.prompt || 0) + " in / " + (t.completion || 0) + " out (" + (t.reasoning || 0) + " reasoning)";
  }

  // One group per model step: the shared tokens and latency once, then every tool call of that step.
  const stepGroups = new Map();
  function appendSteps(steps) {
    const list = $("trace-list");
    if (!list) return;
    for (const s of steps) {
      let g = stepGroups.get(s.step);
      if (!g) {
        const li = el("li", "trace-step");
        const head = el("div", "trace-head");
        head.appendChild(el("span", "step-no", "Step " + s.step));
        head.appendChild(el("span", "trace-tokens", tokensText(s.tokens)));
        head.appendChild(el("span", "trace-latency",
          (typeof s.latency === "number" ? s.latency.toFixed(2) : "-") + " s"));
        li.appendChild(head);
        const calls = el("ul", "trace-calls");
        li.appendChild(calls);
        list.appendChild(li);
        g = { li: li, calls: calls };
        stepGroups.set(s.step, g);
      }
      if (s.forced) g.li.classList.add("is-forced");
      const call = el("li", "trace-call");
      const top = el("div", "call-head");
      top.appendChild(el("span", "trace-tool", s.tool || "final answer"));
      top.appendChild(el("span", "trace-summary", s.result_summary));
      call.appendChild(top);
      if (s.args) call.appendChild(el("code", "trace-args", s.args));
      g.calls.appendChild(call);
    }
    show("trace-empty", list.children.length === 0);
  }

  // Template text may mark code with `backticks`; render those parts as <code>, all via textContent.
  function richText(tag, cls, text) {
    const node = el(tag, cls);
    String(text || "").split("`").forEach((part, i) => {
      node.appendChild(i % 2 ? el("code", "", part) : document.createTextNode(part));
    });
    return node;
  }

  function fact(dl, term, value) {
    dl.appendChild(el("dt", "", term));
    dl.appendChild(el("dd", "", value));
  }

  // Every finding carries all three audiences' texts (fixed templates); the role toggle only changes which
  // ones CSS shows. Cards are appended, never rebuilt, so an open card stays open while the trace polls.
  const findingCards = new Map();
  function findingCard(f) {
    const card = el("details", "finding");
    const sum = el("summary", "finding-sum");
    sum.appendChild(el("span", "sev sev-" + f.severity, f.severity));
    sum.appendChild(el("span", "f-title", f.title));
    sum.appendChild(el("span", "f-where", f.location));
    card.appendChild(sum);
    const body = el("div", "finding-body");
    body.appendChild(richText("p", "role-owner", f.plain));
    body.appendChild(richText("p", "role-owner analogy", f.analogy));
    body.appendChild(richText("p", "role-developer role-technical", f.technical));
    const fix = el("p", "role-owner role-developer fix");
    fix.appendChild(el("span", "label", "Suggested fix "));
    fix.appendChild(richText("span", "", f.fix));
    body.appendChild(fix);
    const meta = el("dl", "facts role-technical");
    fact(meta, "Check", f.check);
    fact(meta, "Finding id", f.id);
    fact(meta, "Severity", f.severity + " (fixed table)");
    body.appendChild(meta);
    body.appendChild(el("p", "owasp", f.owasp));
    const ev = el("div", "claim-line");
    ev.appendChild(el("span", "label", "Evidence (from code)"));
    ev.appendChild(el("code", "", f.evidence_line));
    body.appendChild(ev);
    card.appendChild(body);
    return card;
  }

  function renderFindings(findings) {
    const list = $("findings-list");
    if (!list) return;
    for (const f of findings) {
      if (findingCards.has(f.id)) continue;
      const card = findingCard(f);
      findingCards.set(f.id, card);
      list.appendChild(card);
    }
    // keep severity order: re-append existing nodes in the server's order (moves, never rebuilds)
    for (const f of findings) list.appendChild(findingCards.get(f.id));
    show("findings-panel", findings.length > 0);
    setText("findings-count", findings.length + " detected by code");
  }

  function renderFixFirst(items) {
    const list = $("fixfirst-list");
    if (!list) return;
    clear(list);
    for (const f of items || []) {
      const li = el("li", "fixfirst-item");
      const head = el("div", "claim-head");
      head.appendChild(el("span", "sev sev-" + f.severity, f.severity));
      head.appendChild(el("span", "f-title", f.title + " at " + f.location));
      li.appendChild(head);
      li.appendChild(richText("p", "hint", f.fix));
      list.appendChild(li);
    }
    show("fixfirst-panel", (items || []).length > 0);
  }

  // ---------- role toggle: display only ----------
  const ROLES = ["owner", "developer", "technical"];
  function setRole(role) {
    const panel = $("findings-panel");
    if (!panel || ROLES.indexOf(role) < 0) return;
    panel.setAttribute("data-role", role);
    for (const r of ROLES) {
      const b = $("role-" + r);
      if (b) b.setAttribute("aria-pressed", r === role ? "true" : "false");
    }
    try { window.localStorage.setItem("maya-role", role); } catch (e) { /* the choice still applies */ }
  }

  // ---------- fix proposals, approval, apply, re-test ----------
  const PROPOSAL_CHIP = {
    PROPOSED: ["proposed", "pending"], APPROVED: ["approved", "loopback"],
    APPLIED: ["applied", "ok"], ROLLED_BACK: ["rolled back", "failed"],
  };
  let currentRunId = null;

  async function copyText(text, btn) {
    try {
      await navigator.clipboard.writeText(text);
      btn.textContent = "Copied";
    } catch (e) {
      btn.textContent = "Copy failed: select the text";
    }
    setTimeout(() => { btn.textContent = "Copy"; }, 1500);
  }

  function snippet(label, text) {
    const box = el("div", "snippet");
    const head = el("div", "snippet-head");
    head.appendChild(el("span", "label", label));
    const btn = el("button", "btn btn-quiet copy-btn", "Copy");
    btn.type = "button";
    btn.addEventListener("click", () => copyText(text, btn));
    head.appendChild(btn);
    box.appendChild(head);
    box.appendChild(el("code", "codebox", text));
    return box;
  }

  function renderProposals(run, proposals) {
    const list = $("proposals-list");
    if (!list) return;
    clear(list);
    for (const p of proposals || []) {
      const card = el("article", "proposal");
      const head = el("div", "proposal-head");
      head.appendChild(el("h3", "proposal-title", p.check_id));
      head.appendChild(el("span", "chip", p.kind));
      head.appendChild(el("span", "chip " + (p.risk === "low" ? "chip-ok" : "chip-pending"), "risk " + p.risk));
      const s = PROPOSAL_CHIP[p.status] || [p.status, ""];
      head.appendChild(el("span", "chip chip-" + s[1], s[0]));
      card.appendChild(head);
      card.appendChild(el("p", "hint", "For: " + p.finding_ids.join(", ") + " - " + p.note));
      card.appendChild(snippet("nginx", p.nginx));
      card.appendChild(snippet("Apache", p.apache));
      card.appendChild(snippet("Python http.server", p.python_note));
      card.appendChild(richText("p", "hint rollback", "Rollback: " + p.rollback));
      const foot = el("div", "proposal-foot");
      if (p.status === "PROPOSED") {
        const btn = el("button", "btn btn-secondary", "Approve");
        btn.type = "button";
        btn.addEventListener("click", () => approve(p.id));
        foot.appendChild(btn);
      } else {
        foot.appendChild(el("span", "hint", "Approved by " + (p.approved_by || "-")));
      }
      card.appendChild(foot);
      list.appendChild(card);
    }
    const approved = (proposals || []).some((p) => p.status === "APPROVED");
    const idle = run && run.status !== "running";
    const approveAllBtn = $("approve-all-btn");
    if (approveAllBtn) approveAllBtn.disabled = !(idle && (proposals || []).some((p) => p.status === "PROPOSED"));
    const apply = $("apply-btn");
    if (apply) apply.disabled = !(run && run.can_apply && idle && approved);
    const retestBtn = $("retest-btn");
    if (retestBtn) retestBtn.disabled = !idle;
    setText("apply-hint", run && run.can_apply
      ? "Apply changes only APPROVED fixes on your loopback demo app, runs a health check, and rolls back automatically if it fails."
      : "Fixes are applied only to the loopback demo app. For your own site, copy a snippet, apply it yourself, then press Re-test.");
    show("proposals-panel", (proposals || []).length > 0);
  }

  function diffList(id, ids) {
    const ul = $(id);
    if (!ul) return;
    clear(ul);
    for (const i of ids || []) ul.appendChild(el("li", "", i));
    if (!(ids || []).length) ul.appendChild(el("li", "muted", "None"));
  }

  function renderRetest(r) {
    show("retest-panel", !!r);
    if (!r) return;
    setChip("retest-status", r.status === "FIXED" ? "FIXED" : "STILL PRESENT", r.status === "FIXED" ? "ok" : "failed");
    diffList("retest-resolved", r.resolved);
    diffList("retest-still", r.still_present);
    diffList("retest-new", r.new);
  }

  function renderAudit(a) {
    if (!a || !$("audit-list")) return;
    show("audit-panel", true);
    setChip("audit-status", a.intact ? "hash chain verified" : "chain broken at entry " + a.first_bad_entry,
      a.intact ? "ok" : "failed");
    setText("audit-count", a.count + " entries");
    const list = $("audit-list");
    clear(list);
    for (const e of a.entries || []) {
      const li = el("li", "audit-entry");
      li.appendChild(el("span", "audit-seq", "#" + e.seq));
      li.appendChild(el("span", "audit-event", e.event));
      const details = Object.keys(e.details || {}).filter((k) => typeof e.details[k] !== "object")
        .map((k) => k + "=" + e.details[k]).join(" ");
      li.appendChild(el("span", "audit-details", details));
      li.appendChild(el("code", "audit-hash", e.hash));
      list.appendChild(li);
    }
    if (!(a.entries || []).length) list.appendChild(el("li", "muted", "No entries yet."));
  }

  function renderUsage(u) {
    const dl = $("usage-facts");
    if (!u || !dl) return;
    show("usage-panel", true);
    clear(dl);
    fact(dl, "Scans", u.runs + " (" + u.model_runs + " with a model)");
    fact(dl, "Prompt", String(u.prompt_tokens));
    fact(dl, "Completion", u.completion_tokens + " (" + u.reasoning_tokens + " reasoning)");
    fact(dl, "Total", String(u.total_tokens));
    setText("usage-cap", "cap " + u.cap_per_run + " tokens per scan");
  }

  function pct(x) {
    return x === null || x === undefined ? "n/a" : Math.round(x * 100) + "%";
  }

  // numbers come only from docs/eval_results.json (written by the eval harness); none are made up here
  function renderQuality(q) {
    if (!q || !$("quality-panel")) return;
    show("quality-panel", true);
    const cases = $("quality-cases");
    clear(cases);
    if (!q.available) {
      setText("quality-meta", "");
      setText("quality-summary", "No eval results yet: run the eval (python -m src.eval.harness --write).");
      setText("quality-live", "");
      return;
    }
    const t = q.totals;
    setText("quality-meta", q.kind + ", " + String(q.generated_at).slice(0, 10));
    setText("quality-summary", "Fake claims that reached a report: " + t.fake_claims_in_report + " in " + t.runs +
      " runs. Reports produced: " + t.reports_produced + "/" + t.runs + ". Claims kept " + t.claims_kept +
      ", dropped " + t.claims_dropped + ".");
    const byCase = {};
    for (const r of q.rows || []) (byCase[r.case] = byCase[r.case] || []).push(r);
    for (const name of Object.keys(byCase)) {
      const rs = byCase[name];
      const li = el("li");
      li.appendChild(el("strong", "", name));
      li.appendChild(el("span", "", ": code precision " + pct(rs[0].precision) + ", recall " + pct(rs[0].recall) +
        ", false positives " + rs[0].false_positives + "; fake claims in report " +
        rs.reduce((n, r) => n + r.fake_claims_in_report, 0) + " across " + rs.length + " model levels"));
      cases.appendChild(li);
    }
    const live = q.live_latest;
    setText("quality-live", live
      ? "Live model (" + live.model + ", " + live.date + "): " + live.rows.length + " runs, fake claims in report " +
        live.rows.reduce((n, r) => n + r.fake_claims_in_report, 0) + "."
      : "Live model quality: not yet measured.");
  }

  function renderDownloads(run) {
    const done = !!(run && run.status !== "running");
    show("downloads-panel", done);
    if (!done) return;
    const base = "/api/runs/" + encodeURIComponent(run.id) + "/";
    for (const [id, name] of [["dl-pack", "evidence.zip"], ["dl-report", "report.html"], ["dl-report-md", "report.md"]]) {
      const a = $(id);
      if (a) a.setAttribute("href", base + name);
    }
  }

  async function refreshSidePanels() {
    if (PREVIEW) return;
    const a = await getJSON("/api/audit");
    if (a.ok) renderAudit(a.data);
    const u = await getJSON("/api/usage");
    if (u.ok) renderUsage(u.data);
    const q = await getJSON("/api/eval");
    if (q.ok) renderQuality(q.data);
  }

  function renderFull(data) {
    renderDownloads(data.run);
    renderProposals(data.run, data.proposals);
    renderRetest(data.retest);
    refreshSidePanels();
  }

  async function approve(proposalId) {
    if (!currentRunId) return;
    const name = $("approver") ? $("approver").value : "";
    const d = await post("/api/runs/" + currentRunId + "/proposals/" + proposalId + "/approve", { approver: name });
    if (d) { renderFull(d); say("Approved by " + name.trim() + ". Recorded in the audit log.", false); }
  }

  async function applyFixes() {
    if (!currentRunId) return;
    say("Applying approved fixes and checking the demo app...", false);
    const d = await post("/api/runs/" + currentRunId + "/apply", {});
    if (d) {
      renderFull(d);
      const rolled = (d.proposals || []).some((p) => p.status === "ROLLED_BACK");
      say(rolled ? "Health check failed; the change was rolled back." : "Applied. Press Re-test to confirm.", rolled);
    }
  }

  async function runRetest() {
    if (!currentRunId) return;
    say("Re-running the same checks...", false);
    const d = await post("/api/runs/" + currentRunId + "/retest", {});
    if (d) { renderFull(d); say("Re-test finished: " + (d.retest ? d.retest.status : "-"), false); }
  }

  function renderRunMeta(run) {
    show("run-panel", true);
    const c = RUN_CHIP[run.status] || [run.status, ""];
    setChip("run-status", c[0], c[1]);
    setText("run-model", run.mode === "model" ? "Model: " + run.model : run.mode_label);
    setText("mode-label", run.mode_label);
    show("demo-banner", !!run.demo);
    if (run.error) say(run.error, true);
    // one scan at a time: the button follows the run, not just the asset
    const btn = $("scan-btn");
    if (btn) btn.disabled = run.status === "running" || !(currentAsset && currentAsset.scannable);
    setResetEnabled(run.status !== "running");
  }

  function renderResult(run, result) {
    if (!result) return;
    show("result-panel", true);
    setText("result-model", run.mode === "model" ? run.model : "none (no model key configured)");
    setText("result-mode", run.mode_label);
    const u = result.usage || {};
    setText("result-tokens", (u.prompt_tokens || 0) + " prompt, " + (u.completion_tokens || 0) + " completion (" +
      (u.reasoning_tokens || 0) + " reasoning), " + (u.total_tokens || 0) + " total");
    setText("result-order", result.order_note || "");
    const list = $("claims-list");
    clear(list);
    for (const c of result.claims || []) {
      const li = el("li", "claim");
      const head = el("div", "claim-head");
      head.appendChild(el("span", "sev sev-" + c.severity, c.severity));
      head.appendChild(el("span", "claim-text", c.text));
      li.appendChild(head);
      const ev = el("div", "claim-line");
      ev.appendChild(el("span", "label", "Code evidence (verified)"));
      ev.appendChild(el("code", "", c.evidence_line));
      li.appendChild(ev);
      const note = el("div", "claim-line");
      if (c.note) {
        note.appendChild(el("span", "label label-unverified", "unverified model text"));
        note.appendChild(el("span", "note", c.note));
      } else if (c.note_dropped) {
        note.appendChild(el("span", "label", "Model note dropped"));
        note.appendChild(el("span", "note muted", c.note_dropped));
      } else {
        note.appendChild(el("span", "label", "No model note"));
      }
      li.appendChild(note);
      list.appendChild(li);
    }
    const noClaims = !(result.claims || []).length;
    show("claims-empty", noClaims);
    setText("claims-empty", run.mode === "model"
      ? (result.model_finished ? "The model made no verified claims." : "The model did not finish; findings above are from code checks.")
      : "No model in deterministic mode; findings above are from code checks.");
    const dropped = $("dropped-list");
    clear(dropped);
    for (const d of result.dropped || []) {
      const li = el("li");
      li.appendChild(el("code", "", d.finding_id || "(none)"));
      li.appendChild(el("span", "", " " + d.reason));
      dropped.appendChild(li);
    }
    if (!(result.dropped || []).length) dropped.appendChild(el("li", "muted", "None."));
  }

  function resetRunPanels() {
    clear($("trace-list"));
    clear($("findings-list"));
    stepGroups.clear();
    findingCards.clear();
    traceNext = 0;
    for (const id of ["run-panel", "findings-panel", "result-panel", "fixfirst-panel", "proposals-panel",
      "retest-panel", "downloads-panel", "demo-banner"]) show(id, false);
    show("trace-empty", true);
  }

  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  async function pollOnce(runId) {
    const r = await getJSON("/api/runs/" + runId + "/trace?after=" + traceNext);
    if (!r.ok) { stopPolling(); say(r.data.error || "Could not load the run.", true); return; }
    appendSteps(r.data.steps);
    traceNext = r.data.next;
    renderFindings(r.data.findings);
    renderRunMeta(r.data.run);
    if (r.data.run.status !== "running") {
      stopPolling();
      const full = await getJSON("/api/runs/" + runId);
      if (full.ok) {
        renderResult(full.data.run, full.data.result);
        renderFixFirst(full.data.result && full.data.result.fix_first);
        renderFull(full.data);
      }
      const btn = $("scan-btn");
      if (btn && currentAsset && currentAsset.scannable) btn.disabled = false;
    }
  }

  function startPolling(runId) {
    stopPolling();
    currentRunId = runId;
    pollOnce(runId);
    pollTimer = setInterval(() => pollOnce(runId), 1000);
  }

  function selectAsset(asset) {
    currentAsset = asset;
    stopPolling();
    resetRunPanels();
    showView("asset");
    setText("av-label", asset.kind === "demo" ? "Demo app (loopback)" : asset.label);
    statusChip("av-status", asset.status);
    setText("av-hint", asset.scannable
      ? (asset.kind === "demo" ? "Loopback demo app: no verification needed." : "Ownership verified.")
      : "Verify ownership on the Connect website page before scanning.");
    setText("mode-label", "");
    const btn = $("scan-btn");
    if (btn) btn.disabled = !asset.scannable;
    show("reset-demo-btn", asset.kind === "demo");
    setResetEnabled(true);
    say("", false);
    if (asset.last_run && !PREVIEW) startPolling(asset.last_run);
  }

  function setResetEnabled(on) {
    const b = $("reset-demo-btn");
    if (b) b.disabled = !(on && currentAsset && currentAsset.kind === "demo");
  }

  // back to the vulnerable demo app (this user's own copy); the server refuses while a scan runs
  async function resetDemo() {
    if (!currentAsset || currentAsset.kind !== "demo") return;
    setResetEnabled(false);
    const d = await post("/api/assets/" + currentAsset.id + "/reset-demo", {});
    setResetEnabled(true);
    if (d) {
      say("Demo app reset to its vulnerable state (" + (d.previous_fixes || []).length +
        " fixes removed). Recorded in the audit log. Run a new scan to see the issues again.", false);
      refreshSidePanels();
    }
  }

  async function startScan() {
    if (!currentAsset) return;
    const btn = $("scan-btn");
    if (btn) btn.disabled = true;
    const d = await post("/api/assets/" + currentAsset.id + "/scan", {});
    if (!d || !d.run) { if (btn) btn.disabled = !currentAsset.scannable; return; }
    resetRunPanels();
    currentAsset.last_run = d.run.id;
    renderRunMeta(d.run);
    startPolling(d.run.id);
  }

  async function initShell(user) {
    if (!$("assets-list")) return;
    const avatar = $("avatar");
    if (avatar) avatar.textContent = user && user.email ? user.email.charAt(0).toUpperCase() : (PREVIEW ? "P" : "?");
    onClick("scan-btn", startScan);
    onClick("reset-demo-btn", resetDemo);
    onClick("apply-btn", applyFixes);
    onClick("approve-all-btn", approveAll);
    onClick("retest-btn", runRetest);
    for (const r of ROLES) onClick("role-" + r, () => setRole(r));
    let saved = null;
    try { saved = window.localStorage.getItem("maya-role"); } catch (e) { saved = null; }
    setRole(new URLSearchParams(window.location.search).get("role") || saved || "owner");
    if (PREVIEW) {
      const r = await getJSON("/__preview/sample.json");
      if (!r.ok) return;
      show("add-asset", true);
      renderAssetList(r.data.assets);
      renderChatList(r.data.chat.list);
      const view = new URLSearchParams(window.location.search).get("view");
      setComposer(view === "chat-nokey" ? { model_configured: false, model: null } : r.data.chat.status);
      if (view === "chat") {
        renderChat(r.data.chat.chat);
        renderChatList(r.data.chat.list);  // after opening, so the open chat is highlighted (as loadChats does)
      }
      if (view === "running" || view === "finished") {
        const sample = r.data[view];
        selectAsset(r.data.assets[0]);
        appendSteps(sample.steps);
        renderFindings(sample.findings);
        renderRunMeta(sample.run);
        if (view === "finished") {
          renderResult(sample.run, sample.result);
          renderFixFirst(sample.result.fix_first);
          renderProposals(sample.run, r.data.extras.proposals);
          renderRetest(r.data.extras.retest);
          renderAudit(r.data.extras.audit);
          renderUsage(r.data.extras.usage);
          renderDownloads(sample.run);
          renderQuality(r.data.eval);
          const open = document.querySelector("#findings-list details");
          if (open) open.open = true;  // show one expanded card in screenshots
        }
      }
      return;
    }
    refreshSidePanels();
    loadChats();
    show("add-asset", !!(user && user.account_type === "company" && user.consented));
    const r = await getJSON("/api/assets");
    if (r.ok) renderAssetList(r.data.assets);
  }

  // ---------- chat ----------
  let chatStatus = { model_configured: false, model: null };
  let currentChatId = null;
  let sending = false;

  function showView(view) {
    show("home", view === "home");
    show("chat-view", view === "chat");
    show("asset-view", view === "asset");
    if (view !== "asset") stopPolling();
  }

  function composerHint(text, kind) {
    const hint = $("composer-hint");
    if (!hint) return;
    hint.textContent = text;
    hint.className = "composer-hint" + (kind ? " is-" + kind : "");
  }

  function setComposer(status) {
    chatStatus = status;
    const box = $("prompt");
    const send = document.querySelector("#composer .send");
    const ready = !!status.model_configured && !sending && !!(box && box.value.trim());
    if (box) box.disabled = !status.model_configured;
    if (send) send.disabled = !ready;  // active only with a model and something to send
    if (!status.model_configured) {
      composerHint("Model not configured: chat needs a model key on the server. No replies are generated.", "error");
    } else {
      composerHint("Model: " + status.model + ". Answers about your sites come only from MAYA's verified findings.", "");
    }
  }

  function renderChatList(chats) {
    const list = $("chats-list");
    if (!list) return;
    clear(list);
    for (const c of chats) {
      const li = el("li");
      const btn = el("button", "asset-item" + (c.id === currentChatId ? " is-current" : ""));
      btn.type = "button";
      btn.appendChild(el("span", "asset-name", c.title));
      btn.addEventListener("click", () => { openChat(c.id); setSidebar(false); });
      li.appendChild(btn);
      list.appendChild(li);
    }
    show("chats-empty", chats.length === 0);
  }

  async function loadChats() {
    const r = await getJSON("/api/chats");
    if (!r.ok) return;
    setComposer(r.data);
    renderChatList(r.data.chats);
  }

  function messageItem(m) {
    const li = el("li", "msg msg-" + m.role);
    li.appendChild(el("div", "msg-body", m.content));
    if (m.role === "assistant" && m.meta) {
      const meta = el("div", "msg-meta");
      const restates = m.meta.label === "restates verified findings";
      meta.appendChild(el("span", "chip " + (restates ? "chip-ok" : "chip-forced"), m.meta.label));
      const t = m.meta.tokens || {};
      meta.appendChild(el("span", "", "Model that ran: " + (m.meta.model || "-")));
      meta.appendChild(el("span", "", (t.prompt || 0) + " in / " + (t.completion || 0) + " out (" +
        (t.reasoning || 0) + " reasoning)"));
      li.appendChild(meta);
      if ((m.meta.cited || []).length) li.appendChild(el("div", "msg-cites", "Cites your findings: " + m.meta.cited.join(", ")));
      if ((m.meta.cited_unknown || []).length) {
        li.appendChild(el("div", "msg-cites is-warning", "Not in your verified findings: " + m.meta.cited_unknown.join(", ")));
      }
    }
    return li;
  }

  function renderChat(chat) {
    currentChatId = chat.id;
    showView("chat");
    setText("chat-title", chat.title);
    const list = $("chat-messages");
    clear(list);
    for (const m of chat.messages || []) list.appendChild(messageItem(m));
    show("chat-empty", !(chat.messages || []).length);
    scrollConversation();
  }

  function scrollConversation() {
    const c = document.querySelector(".conversation");
    if (c) c.scrollTop = c.scrollHeight;
  }

  async function openChat(id) {
    const r = await getJSON("/api/chats/" + id);
    if (!r.ok) { composerHint(r.data.error || "Could not open that chat.", "error"); return; }
    setComposer(r.data);
    renderChat(r.data.chat);
    loadChats();
  }

  async function newChat(title) {
    const d = await post("/api/chats", title ? { title: title } : {});
    if (!d) return null;
    setComposer(d);
    renderChat(d.chat);
    loadChats();
    return d.chat.id;
  }

  async function sendMessage(text) {
    text = (text || "").trim();
    if (!text || sending) return;
    if (!chatStatus.model_configured) { setComposer(chatStatus); return; }
    if (!currentChatId || $("chat-view").hidden) {
      if (!(await newChat(text.slice(0, 60)))) return;
    }
    sending = true;
    setComposer(chatStatus);
    show("chat-empty", false);
    const pending = messageItem({ role: "user", content: text });
    $("chat-messages").appendChild(pending);
    const thinking = el("li", "msg msg-pending", "MAYA is thinking...");
    $("chat-messages").appendChild(thinking);
    scrollConversation();
    const box = $("prompt");
    if (box) box.value = "";
    let r;
    try {
      r = await fetch("/api/chats/" + currentChatId + "/messages", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf || "" },
        body: JSON.stringify({ content: text }),
      });
    } catch (e) { r = null; }
    thinking.remove();
    sending = false;
    let data = {};
    try { data = r ? await r.json() : {}; } catch (e) { data = {}; }
    if (!r || !r.ok) {
      pending.classList.add("is-failed");
      if (box && !box.value) box.value = text;  // keep what the user typed so they can retry
      setComposer(chatStatus);
      composerHint(typeof data.error === "string" ? data.error : "Could not reach MAYA.", "error");
      return;
    }
    pending.replaceWith(messageItem(data.user_message));  // shows the stored (redacted) text
    $("chat-messages").appendChild(messageItem(data.reply));
    setComposer(chatStatus);
    scrollConversation();
    loadChats();
  }

  async function startChatWith(prompt) {
    if (!chatStatus.model_configured) { setComposer(chatStatus); return; }
    if (await newChat(prompt.slice(0, 60))) await sendMessage(prompt);
  }

  async function approveAll() {
    if (!currentRunId) return;
    const name = $("approver") ? $("approver").value : "";
    const d = await post("/api/runs/" + currentRunId + "/approve-all", { approver: name });
    if (d) { renderFull(d); say("All open proposals approved by " + name.trim() + "; each approval is in the audit log.", false); }
  }

  document.addEventListener("DOMContentLoaded", async () => {
    onClick("theme-toggle", toggleTheme);
    onClick("tab-signin", () => showTab("signin"));
    onClick("tab-signup", () => showTab("signup"));
    onClick("sidebar-open", () => setSidebar(true));
    onClick("sidebar-close", () => setSidebar(false));
    onClick("scrim", () => setSidebar(false));
    onClick("new-chat", () => { setSidebar(false); if (!PREVIEW) newChat(); });
    for (const card of document.querySelectorAll(".suggestion")) {
      card.addEventListener("click", () => startChatWith(card.getAttribute("data-prompt") || ""));
    }
    const composer = $("composer");
    if (composer) {
      composer.addEventListener("submit", (e) => { e.preventDefault(); sendMessage($("prompt").value); });
      $("prompt").addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage($("prompt").value); }
      });
      $("prompt").addEventListener("input", () => {
        const send = document.querySelector("#composer .send");
        if (send) send.disabled = !(chatStatus.model_configured && !sending && $("prompt").value.trim());
      });
    }
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") setSidebar(false); });

    let session;
    try { session = await loadSession(); } catch (e) { say("Could not reach MAYA.", true); return; }
    const who = $("who");
    if (who) who.textContent = session.user ? session.user.email : (PREVIEW ? "preview@maya.local" : "");

    onSubmit("signin-form", (f) => post("/api/signin", { email: f.email.value, password: f.password.value }));
    onSubmit("signup-form", (f) => post("/api/signup", { email: f.email.value, password: f.password.value }));
    onClick("choose-individual", () => post("/api/profile", { account_type: "individual" }));
    onClick("choose-company", () => post("/api/profile", { account_type: "company" }));
    onClick("asset-website", () => post("/api/asset", { asset: "website" }));
    onClick("connect-yes", () => post("/api/connect", { connect: true }));
    onClick("connect-no", () => post("/api/connect", { connect: false }));
    onClick("consent-allow", () => post("/api/consent", { allow: true }));
    onClick("signout", () => post("/api/signout", {}));

    await initConnectWebsite();
    await initShell(session.user);
  });
})();
