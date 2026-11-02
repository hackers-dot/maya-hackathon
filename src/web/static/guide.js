"use strict";
// MAYA guide pet: a mascot that shows the next step from GET /api/guide (a fixed state machine on the server,
// never the model). Everything is built with createElement/createElementNS and textContent. Speech uses the
// browser's built-in speechSynthesis / SpeechRecognition only; MAYA's server never receives audio.
// The pure helpers in `core` have no DOM access, so they are unit-tested in Node (tests/test_guide.py).
(function () {
  const core = {
    // One short, speakable line: no citation ids, code, addresses, long tokens or masked values.
    speakable(text, max) {
      let t = String(text || "")
        .replace(/\[[^\]]*\]/g, " ")          // [finding:id] citations
        .replace(/`[^`]*`/g, " ")             // inline code
        .replace(/\S*[\/\\]\S*/g, " ")        // paths and addresses
        .replace(/\S*\*{3,}\S*/g, " ")        // masked values
        .replace(/\S{24,}/g, " ")             // long tokens, hashes, ids
        .replace(/\s+/g, " ").trim();
      const limit = max || 200;
      const first = t.match(/^.*?[.!?](\s|$)/);
      if (first && first[0].length >= 20) t = first[0].trim();
      if (t.length > limit) t = t.slice(0, limit).replace(/\s+\S*$/, "") + ".";
      return t;
    },
    // en: en-IN, then any English voice; hinglish: hi-IN, then en-IN. null means "use the browser default".
    pickVoice(voices, lang) {
      const list = Array.isArray(voices) ? voices : [];
      const prefs = lang === "hinglish" ? ["hi-IN", "en-IN"] : ["en-IN", "en-"];
      for (const p of prefs) {
        const v = list.find((x) => x && typeof x.lang === "string" && x.lang.replace("_", "-").indexOf(p) === 0);
        if (v) return v;
      }
      return null;
    },
    speechSupported(win) {
      return !!(win && win.speechSynthesis && typeof win.SpeechSynthesisUtterance === "function");
    },
    recognitionCtor(win) {
      return (win && (win.SpeechRecognition || win.webkitSpeechRecognition)) || null;
    },
    // the first target id that exists on this page
    firstTarget(ids, exists) {
      for (const id of ids || []) if (typeof id === "string" && exists(id)) return id;
      return null;
    },
  };
  if (typeof module === "object" && module.exports) { module.exports = core; return; }
  if (typeof document === "undefined") return;

  const $ = (id) => document.getElementById(id);
  const PREVIEW = window.location.pathname.startsWith("/__preview/");
  const SVG_NS = "http://www.w3.org/2000/svg";
  // HINGLISH strings below NEED NATIVE-SPEAKER REVIEW before they are relied on.
  const UI = {
    en: { title: "MAYA guide", next: "Next tip", hide: "Hide guide", show: "Show me", ask: "Ask me anything about security",
      send: "Ask", choose: "How much do you know about security?", settings: "Voice and settings",
      error: "Something did not work. The message on the screen says what happened.",
      nokey: "Model not configured: answers need a model key on the server. The guided steps still work.",
      novoice: "No voice is installed in this browser, so the guide stays text only." },
    hinglish: { title: "MAYA guide", next: "Agla tip", hide: "Guide chhupayein", show: "Dikhao", ask: "Security ke baare mein kuch bhi poochein",
      send: "Poochein", choose: "Aap security ke baare mein kitna jaante hain?", settings: "Awaaz aur settings",
      error: "Kuch kaam nahi kiya. Screen par message batata hai ki kya hua.",
      nokey: "Model configured nahi hai: jawab ke liye server par model key chahiye. Guided steps phir bhi chalte hain.",
      novoice: "Is browser mein koi voice installed nahi hai, isliye guide sirf text mein rahega." },
  };
  const LEVELS = [["new", "I'm new to security"], ["some", "I know some"], ["technical", "I'm technical"]];
  const VOICE_NOTE = "Voice uses your browser's built-in text-to-speech, not a Meta model.";
  const MIC_NOTE = "Your browser may send the audio to its speech provider to turn it into text. MAYA stores nothing.";

  function load(key, fallback) {
    let v = null;
    try { v = window.localStorage.getItem(key); } catch (e) { v = null; }
    return v === null ? fallback : v;
  }
  function save(key, value) {
    try { window.localStorage.setItem(key, value); } catch (e) { /* the choice still applies to this page */ }
  }
  const prefs = {
    hidden: load("maya-guide-hidden", "0") === "1",
    level: load("maya-guide-level", ""),
    lang: load("maya-guide-lang", "en") === "hinglish" ? "hinglish" : "en",
    speak: load("maya-guide-speak", "0") === "1",
    speakAnswers: load("maya-guide-speak-answers", "0") === "1",
    rate: Math.min(1.5, Math.max(0.7, parseFloat(load("maya-guide-rate", "1")) || 1)),
  };
  let csrf = null;
  let steps = [];
  let stepIndex = 0;
  let assetId = null;
  let chatId = null;
  let target = null;
  let talkTimer = null;

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }
  function svg(tag, attrs, parent) {
    const n = document.createElementNS(SVG_NS, tag);
    for (const k of Object.keys(attrs)) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }
  function button(cls, text, onClick) {
    const b = el("button", cls, text);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  }

  // ---------- the mascot: an original round creature with a sprout, drawn in code ----------
  function mascot() {
    const s = svg("svg", { viewBox: "0 0 100 100", class: "pet-svg", "aria-hidden": "true" });
    svg("ellipse", { class: "pet-shadow", cx: 50, cy: 95, rx: 24, ry: 3.5 }, s);
    const body = svg("g", { class: "pet-bodyg" }, s);
    svg("path", { class: "pet-limb pet-arm-l", d: "M22 64 q-10 3 -12 11" }, body);
    svg("path", { class: "pet-limb pet-arm-r", d: "M78 64 q10 3 12 11" }, body);
    svg("path", { class: "pet-limb pet-sprout", d: "M50 27 q-1 -9 6 -13" }, body);
    svg("ellipse", { class: "pet-leaf", cx: 60, cy: 12, rx: 7, ry: 3.6, transform: "rotate(-28 60 12)" }, body);
    svg("circle", { class: "pet-body", cx: 50, cy: 59, r: 32 }, body);
    svg("ellipse", { class: "pet-belly", cx: 50, cy: 70, rx: 18, ry: 13 }, body);
    svg("circle", { class: "pet-cheek", cx: 31, cy: 63, r: 4 }, body);
    svg("circle", { class: "pet-cheek", cx: 69, cy: 63, r: 4 }, body);
    const eyes = svg("g", { class: "pet-eyes" }, body);
    for (const x of [40, 60]) {
      svg("ellipse", { class: "pet-eye", cx: x, cy: 53, rx: 4.5, ry: 5.5 }, eyes);
      svg("circle", { class: "pet-glint", cx: x + 1.5, cy: 51, r: 1.4 }, eyes);
    }
    svg("path", { class: "pet-brow", d: "M34 43 l10 4 M66 43 l-10 4" }, body);
    svg("path", { class: "pet-mouth pet-mouth-smile", d: "M44 65 q6 5 12 0" }, body);
    svg("path", { class: "pet-mouth pet-mouth-happy", d: "M41 63 q9 11 18 0 z" }, body);
    svg("path", { class: "pet-mouth pet-mouth-frown", d: "M44 69 q6 -5 12 0" }, body);
    svg("ellipse", { class: "pet-mouth pet-mouth-talk", cx: 50, cy: 67, rx: 4, ry: 3.2 }, body);
    const think = svg("g", { class: "pet-think" }, s);
    svg("circle", { cx: 80, cy: 30, r: 2.2 }, think);
    svg("circle", { cx: 87, cy: 21, r: 3 }, think);
    svg("circle", { cx: 94, cy: 11, r: 3.8 }, think);
    return s;
  }

  // ---------- the panel ----------
  const ui = {};
  function build() {
    const root = el("aside", "guide");
    root.id = "guide";
    root.setAttribute("aria-label", "MAYA guide");
    const pet = el("div", "pet mood-idle");
    pet.appendChild(mascot());
    root.appendChild(pet);
    const bubble = el("div", "guide-bubble");
    const head = el("div", "guide-head");
    ui.title = el("strong", "guide-title");
    head.appendChild(ui.title);
    ui.collapse = button("guide-icon-btn", "", () => setCollapsed(!root.classList.contains("is-collapsed")));
    ui.collapse.setAttribute("aria-label", "Collapse or expand the guide");
    ui.collapse.appendChild(el("span", "chev"));
    head.appendChild(ui.collapse);
    ui.hide = button("guide-icon-btn", "✕", () => setHidden(true));
    head.appendChild(ui.hide);
    bubble.appendChild(head);

    ui.setup = el("div", "guide-setup");
    ui.setupLabel = el("p", "guide-why");
    ui.setup.appendChild(ui.setupLabel);
    for (const [level, label] of LEVELS) ui.setup.appendChild(button("btn btn-secondary guide-level", label, () => setLevel(level)));
    bubble.appendChild(ui.setup);

    ui.body = el("div", "guide-body");
    ui.line = el("p", "guide-line");
    ui.line.setAttribute("aria-live", "polite");
    ui.why = el("p", "guide-why");
    ui.body.appendChild(ui.line);
    ui.body.appendChild(ui.why);
    const actions = el("div", "guide-actions");
    ui.show = button("btn btn-primary guide-show", "", showTarget);
    ui.next = button("btn btn-secondary", "", nextTip);
    actions.appendChild(ui.show);
    actions.appendChild(ui.next);
    ui.body.appendChild(actions);

    const form = el("form", "guide-ask-form");
    ui.ask = el("input", "guide-ask");
    ui.ask.id = "guide-ask";
    ui.ask.type = "text";
    ui.ask.maxLength = 2000;
    ui.ask.autocomplete = "off";
    form.appendChild(ui.ask);
    ui.mic = button("guide-icon-btn guide-mic", "\u{1F3A4}", listen);
    ui.mic.setAttribute("aria-label", "Speak your question");
    ui.mic.hidden = true;
    form.appendChild(ui.mic);
    ui.send = el("button", "btn btn-primary guide-send");
    ui.send.type = "submit";
    form.appendChild(ui.send);
    form.addEventListener("submit", (e) => { e.preventDefault(); askPet(ui.ask.value); });
    ui.body.appendChild(form);
    ui.answer = el("div", "guide-answer");
    ui.answer.hidden = true;
    ui.body.appendChild(ui.answer);
    ui.body.appendChild(settings());
    bubble.appendChild(ui.body);
    root.appendChild(bubble);
    document.body.appendChild(root);

    ui.launcher = button("guide-launcher", "Guide me", () => setHidden(false));
    ui.launcher.id = "guide-launcher";
    document.body.appendChild(ui.launcher);
    ui.root = root;
    ui.pet = pet;
  }

  function settings() {
    const box = el("details", "guide-settings");
    ui.settingsLabel = el("summary", "");
    box.appendChild(ui.settingsLabel);
    const row = (labelText, control) => {
      const r = el("label", "guide-row");
      r.appendChild(el("span", "", labelText));
      r.appendChild(control);
      box.appendChild(r);
      return r;
    };
    ui.levelSel = el("select", "");
    for (const [level, label] of LEVELS) { const o = el("option", "", label); o.value = level; ui.levelSel.appendChild(o); }
    ui.levelSel.addEventListener("change", () => setLevel(ui.levelSel.value));
    row("Level", ui.levelSel);
    ui.langSel = el("select", "");
    for (const [v, label] of [["en", "English"], ["hinglish", "Hinglish"]]) { const o = el("option", "", label); o.value = v; ui.langSel.appendChild(o); }
    ui.langSel.addEventListener("change", () => { prefs.lang = ui.langSel.value; save("maya-guide-lang", prefs.lang); refresh(); });
    row("Language", ui.langSel);
    const speech = core.speechSupported(window);
    ui.speak = el("input", "");
    ui.speak.type = "checkbox";
    ui.speak.checked = prefs.speak && speech;
    ui.speak.disabled = !speech;
    ui.speak.addEventListener("change", () => { prefs.speak = ui.speak.checked; save("maya-guide-speak", prefs.speak ? "1" : "0"); if (prefs.speak) speakLine(); else stopSpeech(); });
    row("Speak", ui.speak);
    ui.speakAnswers = el("input", "");
    ui.speakAnswers.type = "checkbox";
    ui.speakAnswers.checked = prefs.speakAnswers && speech;
    ui.speakAnswers.disabled = !speech;
    ui.speakAnswers.addEventListener("change", () => { prefs.speakAnswers = ui.speakAnswers.checked; save("maya-guide-speak-answers", prefs.speakAnswers ? "1" : "0"); });
    row("Speak answers too", ui.speakAnswers);
    ui.rate = el("input", "");
    ui.rate.type = "range";
    ui.rate.min = "0.7";
    ui.rate.max = "1.5";
    ui.rate.step = "0.1";
    ui.rate.value = String(prefs.rate);
    ui.rate.disabled = !speech;
    ui.rate.addEventListener("change", () => { prefs.rate = parseFloat(ui.rate.value) || 1; save("maya-guide-rate", String(prefs.rate)); });
    row("Speed", ui.rate);
    const stop = button("btn btn-quiet", "Stop", stopSpeech);
    stop.disabled = !speech;
    box.appendChild(stop);
    ui.voiceNote = el("p", "guide-note", speech ? VOICE_NOTE : VOICE_NOTE + " " + UI.en.novoice);
    box.appendChild(ui.voiceNote);
    const Rec = core.recognitionCtor(window);
    if (Rec) {  // hidden entirely when the browser has no speech recognition
      ui.micToggle = el("input", "");
      ui.micToggle.type = "checkbox";
      ui.micToggle.addEventListener("change", () => { ui.mic.hidden = !ui.micToggle.checked; ui.micNote.hidden = !ui.micToggle.checked; });
      row("Use microphone", ui.micToggle);
      ui.micNote = el("p", "guide-note", MIC_NOTE);
      ui.micNote.hidden = true;
      box.appendChild(ui.micNote);
    }
    return box;
  }

  function texts() { return UI[prefs.lang]; }
  function renderTexts() {
    const t = texts();
    ui.title.textContent = t.title;
    ui.hide.setAttribute("aria-label", t.hide);
    ui.hide.title = t.hide;
    ui.next.textContent = t.next;
    ui.show.textContent = t.show;
    ui.send.textContent = t.send;
    ui.ask.placeholder = t.ask;
    ui.ask.setAttribute("aria-label", t.ask);
    ui.setupLabel.textContent = t.choose;
    ui.settingsLabel.textContent = t.settings;
    ui.levelSel.value = prefs.level || "new";
    ui.langSel.value = prefs.lang;
  }

  // ---------- mood and talking (only real data and real events change these) ----------
  function setMood(mood) {
    ui.pet.className = "pet mood-" + mood + (ui.pet.classList.contains("is-talking") ? " is-talking" : "");
  }
  function talk(ms) {
    ui.pet.classList.add("is-talking");
    clearTimeout(talkTimer);
    if (ms) talkTimer = setTimeout(() => ui.pet.classList.remove("is-talking"), ms);
  }

  function showStep() {
    const s = steps[stepIndex];
    if (!s) return;
    ui.line.textContent = s.line;
    ui.why.textContent = s.why || "";
    ui.why.hidden = !s.why;
    setTarget(core.firstTarget(s.targets, (id) => !!$(id) && !$(id).closest("[hidden]")));
    setMood(target && s.mood === "pointing" ? "pointing" : s.mood === "pointing" ? "idle" : s.mood);
    ui.next.hidden = steps.length < 2;
    talk(2600);
    if (prefs.speak) speakLine();
  }
  function setTarget(id) {
    if (target && $(target)) $(target).classList.remove("guide-target");
    target = id;
    ui.show.hidden = !id;
    if (id) $(id).classList.add("guide-target");
  }
  function showTarget() {
    const n = target && $(target);
    if (!n) return;
    n.scrollIntoView({ block: "center", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    if (typeof n.focus === "function") n.focus({ preventScroll: true });
    if (window.matchMedia("(max-width: 860px)").matches) setCollapsed(true);
  }
  function nextTip() { stepIndex = (stepIndex + 1) % Math.max(1, steps.length); showStep(); }

  async function refresh() {
    renderTexts();
    ui.setup.hidden = !!prefs.level;
    ui.body.hidden = !prefs.level;
    if (!prefs.level) { setMood("idle"); setTarget(null); return; }
    let data = null;
    if (PREVIEW) {
      const r = await fetch("/__preview/sample.json", { credentials: "same-origin" });
      const sample = r.ok ? await r.json() : {};
      const which = new URLSearchParams(window.location.search).get("guide") || "no_account_type";
      data = (sample.guide || {})[which] || null;
    } else {
      const q = "?level=" + encodeURIComponent(prefs.level) + "&lang=" + encodeURIComponent(prefs.lang) +
        (assetId ? "&asset=" + encodeURIComponent(assetId) : "");
      const r = await fetch("/api/guide" + q, { credentials: "same-origin" });
      data = r.ok ? await r.json() : null;
    }
    if (!data) return;
    steps = data.steps || [];
    stepIndex = 0;
    showStep();
  }

  function setLevel(level) {
    if (!LEVELS.some((l) => l[0] === level)) return;
    prefs.level = level;
    save("maya-guide-level", level);
    const role = { new: "owner", some: "developer", technical: "technical" }[level];
    save("maya-role", role);
    document.dispatchEvent(new CustomEvent("maya:role", { detail: role }));  // the findings toggle follows the level
    refresh();
  }
  function setHidden(hidden) {
    prefs.hidden = hidden;
    save("maya-guide-hidden", hidden ? "1" : "0");
    ui.root.hidden = hidden;
    ui.launcher.hidden = !hidden || !!$("guide-open");  // the shell has its own "Guide me" button
    document.body.classList.toggle("guide-open", !hidden);
    if (hidden) { stopSpeech(); setTarget(null); } else refresh();
  }
  function setCollapsed(collapsed) {
    ui.root.classList.toggle("is-collapsed", collapsed);
    ui.collapse.setAttribute("aria-expanded", collapsed ? "false" : "true");
  }

  // ---------- speech out: the browser's own text-to-speech ----------
  function speak(text) {
    if (!core.speechSupported(window)) return;
    const line = core.speakable(text);
    if (!line) return;
    const synth = window.speechSynthesis;
    synth.cancel();
    const u = new window.SpeechSynthesisUtterance(line);
    const voice = core.pickVoice(synth.getVoices(), prefs.lang);
    if (voice) { u.voice = voice; u.lang = voice.lang; } else if (!synth.getVoices().length) ui.voiceNote.textContent = VOICE_NOTE + " " + texts().novoice;
    u.rate = prefs.rate;
    u.onstart = () => talk(0);
    u.onend = () => ui.pet.classList.remove("is-talking");
    u.onerror = () => ui.pet.classList.remove("is-talking");
    synth.speak(u);
  }
  function speakLine() { if (steps[stepIndex]) speak(steps[stepIndex].line); }
  function stopSpeech() {
    if (core.speechSupported(window)) window.speechSynthesis.cancel();
    ui.pet.classList.remove("is-talking");
  }

  // ---------- speech in: only when the user turns the microphone on ----------
  function listen() {
    const Rec = core.recognitionCtor(window);
    if (!Rec || !ui.micToggle || !ui.micToggle.checked) return;
    const rec = new Rec();
    rec.lang = prefs.lang === "hinglish" ? "hi-IN" : "en-IN";
    rec.interimResults = false;
    rec.maxAlternatives = 1;
    rec.onresult = (e) => { const r = e.results && e.results[0] && e.results[0][0]; if (r) ui.ask.value = String(r.transcript || "").slice(0, 2000); };
    try { rec.start(); } catch (e) { /* already listening */ }
  }

  // ---------- "Ask me": the existing chat endpoint, the user's level in the system prompt ----------
  async function askPet(text) {
    text = String(text || "").trim();
    if (!text || PREVIEW) return;
    setMood("thinking");
    showAnswer(null);
    const status = await fetch("/api/chats", { credentials: "same-origin" });
    const s = status.ok ? await status.json() : {};
    if (!s.model_configured) { showAnswer({ error: texts().nokey }); showStep(); return; }
    if (!chatId) {
      const mine = (s.chats || []).find((c) => c.title === "Guide questions");
      chatId = mine ? mine.id : null;
      if (!chatId) {
        const created = await postJSON("/api/chats", { title: "Guide questions" });
        chatId = created && created.chat ? created.chat.id : null;
      }
    }
    if (!chatId) { showAnswer({ error: texts().error }); showStep(); return; }
    const d = await postJSON("/api/chats/" + chatId + "/messages", { content: text, level: prefs.level || "new" });
    if (!d) { showStep(); return; }
    ui.ask.value = "";
    showAnswer(d.reply);
    setMood("idle");
    talk(2600);
    if (prefs.speakAnswers) speak(d.reply.content);
  }
  async function postJSON(path, body) {
    if (!csrf) { const r = await fetch("/api/session", { credentials: "same-origin" }); csrf = r.ok ? (await r.json()).csrf : null; }
    let r;
    try {
      r = await fetch(path, { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf || "" }, body: JSON.stringify(body) });
    } catch (e) { r = null; }
    let data = {};
    try { data = r ? await r.json() : {}; } catch (e) { data = {}; }
    if (!r || !r.ok) { showAnswer({ error: typeof data.error === "string" ? data.error : texts().error }); return null; }
    return data;
  }
  function showAnswer(reply) {
    const box = ui.answer;
    while (box.firstChild) box.removeChild(box.firstChild);
    box.hidden = !reply;
    if (!reply) return;
    if (reply.error) { box.appendChild(el("p", "guide-error", reply.error)); return; }
    box.appendChild(el("p", "guide-answer-text", reply.content));
    const meta = reply.meta || {};
    const row = el("div", "msg-cites");
    row.appendChild(el("span", "label-unverified" + (meta.label === "restates verified findings" ? " badge-ok" : ""), meta.label || "unverified model text"));
    for (const id of meta.cited || []) row.appendChild(el("span", "cite", id));
    for (const id of meta.cited_unknown || []) row.appendChild(el("span", "cite is-unknown", id));
    box.appendChild(row);
  }

  // ---------- real events from the page (app.js) ----------
  function onEvent(e) {
    const type = e.detail && e.detail.type;
    if (type === "asset") assetId = e.detail.id || null;
    if (type === "error") { setMood("concerned"); ui.line.textContent = texts().error; ui.why.hidden = true; talk(2600); return; }
    if (type === "scan-started") setMood("thinking");
    if (!ui.root.hidden) refresh();
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!document.body || document.body.classList.contains("auth-page")) return;  // signed-in pages only
    build();
    const open = $("guide-open");
    if (open) open.addEventListener("click", () => { setHidden(false); setCollapsed(false); });
    if (window.matchMedia("(max-width: 560px)").matches) setCollapsed(true);  // a slim bar on phones
    document.addEventListener("maya:event", onEvent);
    if (core.speechSupported(window) && typeof window.speechSynthesis.addEventListener === "function") {
      window.speechSynthesis.addEventListener("voiceschanged", () => {});  // some browsers load voices late
    }
    const asked = new URLSearchParams(window.location.search);
    if (PREVIEW && asked.get("level")) prefs.level = asked.get("level");
    if (PREVIEW && asked.get("expand")) setCollapsed(false);
    setHidden(prefs.hidden && !PREVIEW);
  });
})();
