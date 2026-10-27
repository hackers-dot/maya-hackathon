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

  function appendSteps(steps) {
    const list = $("trace-list");
    if (!list) return;
    for (const s of steps) {
      const li = el("li", "trace-step" + (s.forced ? " is-forced" : ""));
      const head = el("div", "trace-head");
      head.appendChild(el("span", "step-no", "Step " + s.step));
      head.appendChild(el("span", "trace-tool", s.tool || "final answer"));
      head.appendChild(el("span", "trace-latency", (typeof s.latency === "number" ? s.latency.toFixed(2) : "-") + " s"));
      li.appendChild(head);
      if (s.args) li.appendChild(el("code", "trace-args", s.args));
      li.appendChild(el("div", "trace-summary", s.result_summary));
      li.appendChild(el("div", "trace-tokens", tokensText(s.tokens)));
      list.appendChild(li);
    }
    show("trace-empty", list.children.length === 0);
  }

  function renderFindings(findings) {
    const body = $("findings-body");
    if (!body) return;
    clear(body);
    for (const f of findings) {
      const tr = el("tr");
      const sev = el("td");
      sev.appendChild(el("span", "sev sev-" + f.severity, f.severity));
      tr.appendChild(sev);
      const what = el("td");
      what.appendChild(el("div", "f-title", f.title));
      what.appendChild(el("div", "f-check", f.check));
      tr.appendChild(what);
      tr.appendChild(el("td", "f-where", f.location));
      const ev = el("td");
      ev.appendChild(el("code", "f-evidence", f.evidence_line));
      tr.appendChild(ev);
      body.appendChild(tr);
    }
    show("findings-panel", findings.length > 0);
    setText("findings-count", findings.length + " detected by code");
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
    clear($("findings-body"));
    traceNext = 0;
    show("run-panel", false);
    show("findings-panel", false);
    show("result-panel", false);
    show("trace-empty", true);
    show("demo-banner", false);
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
      if (full.ok) renderResult(full.data.run, full.data.result);
      const btn = $("scan-btn");
      if (btn && currentAsset && currentAsset.scannable) btn.disabled = false;
    }
  }

  function startPolling(runId) {
    stopPolling();
    pollOnce(runId);
    pollTimer = setInterval(() => pollOnce(runId), 1000);
  }

  function selectAsset(asset) {
    currentAsset = asset;
    stopPolling();
    resetRunPanels();
    show("home", false);
    show("asset-view", true);
    setText("av-label", asset.kind === "demo" ? "Demo app (loopback)" : asset.label);
    statusChip("av-status", asset.status);
    setText("av-hint", asset.scannable
      ? (asset.kind === "demo" ? "Loopback demo app: no verification needed." : "Ownership verified.")
      : "Verify ownership on the Connect website page before scanning.");
    setText("mode-label", "");
    const btn = $("scan-btn");
    if (btn) btn.disabled = !asset.scannable;
    say("", false);
    if (asset.last_run && !PREVIEW) startPolling(asset.last_run);
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
    if (PREVIEW) {
      const r = await getJSON("/__preview/sample.json");
      if (!r.ok) return;
      show("add-asset", true);
      renderAssetList(r.data.assets);
      const view = new URLSearchParams(window.location.search).get("view");
      if (view === "running" || view === "finished") {
        const sample = r.data[view];
        selectAsset(r.data.assets[0]);
        appendSteps(sample.steps);
        renderFindings(sample.findings);
        renderRunMeta(sample.run);
        if (view === "finished") renderResult(sample.run, sample.result);
      }
      return;
    }
    show("add-asset", !!(user && user.account_type === "company" && user.consented));
    const r = await getJSON("/api/assets");
    if (r.ok) renderAssetList(r.data.assets);
  }

  function chatNotReady() {
    const hint = $("composer-hint");
    if (!hint) return;
    hint.textContent = "Chat coming next. Suggestions will work once chat is live.";
    hint.classList.add("is-notice");
  }

  document.addEventListener("DOMContentLoaded", async () => {
    onClick("theme-toggle", toggleTheme);
    onClick("tab-signin", () => showTab("signin"));
    onClick("tab-signup", () => showTab("signup"));
    onClick("sidebar-open", () => setSidebar(true));
    onClick("sidebar-close", () => setSidebar(false));
    onClick("scrim", () => setSidebar(false));
    onClick("new-chat", () => { stopPolling(); show("asset-view", false); show("home", true); chatNotReady(); });
    for (const card of document.querySelectorAll(".suggestion")) card.addEventListener("click", chatNotReady);
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
