"use strict";
// MAYA web client. Server strings are written with textContent only; no HTML is ever built from data.
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

  document.addEventListener("DOMContentLoaded", async () => {
    let session;
    try { session = await loadSession(); } catch (e) { say("Could not reach MAYA.", true); return; }
    const who = $("who");
    if (who && session.user) who.textContent = session.user.email;

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
