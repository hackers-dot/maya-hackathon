"use strict";
// MAYA web client. Server strings are written with textContent only; elements are built with createElement.
// Chat is not implemented yet: nothing here generates or fakes replies.
(function () {
  let csrf = null;
  const $ = (id) => document.getElementById(id);

  function say(text, isError) {
    const el = $("message");
    if (!el) return;
    el.textContent = text;
    el.className = isError ? "message error" : "message";
  }

  function go(next) {
    // only same-origin paths from the server, never a full URL
    if (typeof next === "string" && next.startsWith("/") && !next.startsWith("//")) {
      window.location.assign(next);
    }
  }

  async function loadSession() {
    const r = await fetch("/api/session", { credentials: "same-origin" });
    const data = await r.json();
    csrf = data.csrf;
    return data;
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
    const el = $(id);
    if (el) el.addEventListener("click", handler);
  }

  function onSubmit(id, handler) {
    const form = $(id);
    if (form) form.addEventListener("submit", (e) => { e.preventDefault(); handler(form.elements); });
  }

  // ---- theme ----
  function toggleTheme() {
    const next = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", next);
    try { window.localStorage.setItem("maya-theme", next); } catch (e) { /* storage unavailable: still switches */ }
  }

  // ---- sign-in / sign-up tabs ----
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

  // ---- app shell ----
  function setSidebar(open) {
    document.body.classList.toggle("sidebar-open", open);
    const btn = $("sidebar-open");
    if (btn) btn.setAttribute("aria-expanded", open ? "true" : "false");
  }

  function fillShell(user) {
    const avatar = $("avatar");
    if (avatar) avatar.textContent = user && user.email ? user.email.charAt(0).toUpperCase() : "?";
    const list = $("assets-list");
    const empty = $("assets-empty");
    if (!list) return;
    while (list.firstChild) list.removeChild(list.firstChild);
    const items = [];
    if (user && user.asset === "website") {
      items.push(user.consented ? "Website (consent given)" : "Website (not connected)");
    }
    for (const text of items) {
      const li = document.createElement("li");
      li.textContent = text;
      list.appendChild(li);
    }
    if (empty) empty.hidden = items.length > 0;
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
    onClick("new-chat", chatNotReady);
    for (const card of document.querySelectorAll(".suggestion")) card.addEventListener("click", chatNotReady);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") setSidebar(false); });

    let session;
    try { session = await loadSession(); } catch (e) { say("Could not reach MAYA.", true); return; }
    const who = $("who");
    if (who) {
      const preview = window.location.pathname.startsWith("/__preview/");
      who.textContent = session.user ? session.user.email : (preview ? "preview@maya.local" : "");
    }
    fillShell(session.user);

    onSubmit("signin-form", (f) => post("/api/signin", { email: f.email.value, password: f.password.value }));
    onSubmit("signup-form", (f) => post("/api/signup", { email: f.email.value, password: f.password.value }));
    onClick("choose-individual", () => post("/api/profile", { account_type: "individual" }));
    onClick("choose-company", () => post("/api/profile", { account_type: "company" }));
    onClick("asset-website", () => post("/api/asset", { asset: "website" }));
    onClick("connect-yes", () => post("/api/connect", { connect: true }));
    onClick("connect-no", () => post("/api/connect", { connect: false }));
    onClick("consent-allow", () => post("/api/consent", { allow: true }));
    onClick("signout", () => post("/api/signout", {}));
  });
})();
