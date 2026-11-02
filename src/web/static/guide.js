"use strict";
// MAYA guide: the speech bubble and voice for the MAYA pet (pet.js). The next step comes from GET /api/guide (a
// fixed state machine on the server, never the model); the pet's emotion comes with it. Everything is built
// with createElement and textContent. Voice uses the browser's own speechSynthesis / SpeechRecognition only;
// MAYA's server never receives audio. `core` has no DOM access and is unit-tested in Node.
(function () {
  const core = {
    // Text that is fine to speak: no citation ids, code, paths or addresses, masked values or long tokens.
    // summary=true keeps only the first sentence (answers, never long evidence).
    speakable(text, max, summary) {
      let t = String(text || "")
        .replace(/\[[^\]]*\]/g, " ")          // [finding:id] citations
        .replace(/`[^`]*`/g, " ")             // inline code
        .replace(/\S*[\/\\]\S*/g, " ")        // paths and addresses
        .replace(/\S*\*{3,}\S*/g, " ")        // masked values
        .replace(/\S{24,}/g, " ")             // long tokens, hashes, ids
        .replace(/\s+/g, " ").trim();
      const limit = max || 200;
      if (summary !== false) {
        const first = t.match(/^.*?[.!?](\s|$)/);
        if (first && first[0].length >= 20) t = first[0].trim();
      }
      if (t.length > limit) t = t.slice(0, limit).replace(/\s+\S*$/, "") + ".";
      return t;
    },
    // en: en-IN, then any English voice; hinglish (Roman script): en-IN, then hi-IN, then any English.
    // Voices on this device come first; a network voice (the browser sends the text to its provider) only when
    // no local voice matches. null = the browser default.
    pickVoice(voices, lang) {
      const list = (Array.isArray(voices) ? voices : []).filter((x) => x && typeof x.lang === "string");
      const prefs = lang === "hinglish" ? ["en-IN", "hi-IN", "en-"] : ["en-IN", "en-"];
      for (const pool of [list.filter((x) => x.localService !== false), list]) {
        for (const p of prefs) {
          const v = pool.find((x) => x.lang.replace("_", "-").indexOf(p) === 0);
          if (v) return v;
        }
      }
      return null;
    },
    speechSupported(win) {
      return !!(win && win.speechSynthesis && typeof win.SpeechSynthesisUtterance === "function");
    },
    recognitionCtor(win) {
      return (win && (win.SpeechRecognition || win.webkitSpeechRecognition)) || null;
    },
    firstTarget(ids, exists) {
      for (const id of ids || []) if (typeof id === "string" && exists(id)) return id;
      return null;
    },
    // a rough speaking time, used when a voice fires no boundary events (the mouth then moves on a timer)
    estimateMs(text, rate) {
      return Math.round((String(text || "").length / 14) * 1000 / Math.max(0.5, rate || 1)) + 300;
    },
  };
  if (typeof module === "object" && module.exports) { module.exports = core; return; }
  if (typeof document === "undefined") return;

  const $ = (id) => document.getElementById(id);
  const PREVIEW = window.location.pathname.startsWith("/__preview/");
  // HINGLISH strings below NEED NATIVE-SPEAKER REVIEW before they are relied on.
  const UI = {
    en: { next: "Next tip", close: "Close", show: "Show me", ask: "Ask me anything about security", send: "Ask",
      choose: "How much do you know about security?", settings: "Voice and settings",
      voiceCard: "MAYA can read each step aloud.", voiceOn: "Turn on voice", voiceLater: "Not now",
      error: "Something did not work. The message on the screen says what happened.",
      nokey: "Model not configured: answers need a model key on the server. The guided steps still work.",
      test: "Hi, I am MAYA. My voice is working." },
    hinglish: { next: "Agla tip", close: "Band karein", show: "Dikhao", ask: "Security ke baare mein kuch bhi poochein", send: "Poochein",
      choose: "Aap security ke baare mein kitna jaante hain?", settings: "Awaaz aur settings",
      voiceCard: "MAYA har step bolkar padh sakta hai.", voiceOn: "Awaaz chalu karein", voiceLater: "Abhi nahi",
      error: "Kuch kaam nahi kiya. Screen par message batata hai ki kya hua.",
      nokey: "Model configured nahi hai: jawab ke liye server par model key chahiye. Guided steps phir bhi chalte hain.",
      test: "Namaste, main MAYA hoon. Meri awaaz kaam kar rahi hai." },
  };
  const LEVELS = [["new", "I'm new to security"], ["some", "I know some"], ["technical", "I'm technical"]];
  const VOICE_NOTE = "Voice uses your browser's built-in text-to-speech, not a Meta model.";
  const NO_VOICE = "No voice found: install a Windows speech voice; text still works.";
  const NO_TTS = "This browser has no text-to-speech; text still works.";
  const MIC_NOTE = "Your browser may send the audio to its speech provider to turn it into text. MAYA stores nothing.";

  function load(key, fallback) {
    let v = null;
    try { v = window.localStorage.getItem(key); } catch (e) { v = null; }
    return v === null ? fallback : v;
  }
  function save(key, value) {
    try { window.localStorage.setItem(key, value); } catch (e) { /* the choice still applies to this page */ }
  }
  const num = (v, lo, hi, d) => Math.min(hi, Math.max(lo, parseFloat(v) || d));
  const prefs = {
    petHidden: load("maya-pet-hidden", "0") === "1",
    level: load("maya-guide-level", ""),
    lang: load("maya-guide-lang", "en") === "hinglish" ? "hinglish" : "en",
    voice: load("maya-voice", "unset"),            // unset | on | off; "on" only after the user's own click
    speakAnswers: load("maya-guide-speak-answers", "0") === "1",
    rate: num(load("maya-voice-rate", "1"), 0.7, 1.5, 1),
    pitch: num(load("maya-voice-pitch", "1.1"), 0.6, 1.6, 1.1),
  };
  let csrf = null;
  let steps = [];
  let stepIndex = 0;
  let assetId = null;
  let chatId = null;
  let target = null;
  let pet = null;
  let voices = [];
  let gesture = false;  // speechSynthesis may only start after a user gesture
  let speechRun = 0;
  const ui = {};

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }
  function button(cls, text, onClick) {
    const b = el("button", cls, text);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  }
  const texts = () => UI[prefs.lang];

  // ---------- the bubble ----------
  function build() {
    const root = el("aside", "guide");
    root.id = "guide";
    root.setAttribute("aria-label", "MAYA guide");
    const bubble = el("div", "guide-bubble");
    const head = el("div", "guide-head");
    head.appendChild(el("strong", "guide-title", "MAYA"));
    ui.speaker = button("guide-icon-btn guide-speaker", "", () => {
      gesture = true;
      setVoice(prefs.voice === "on" ? "off" : "on");
    });
    head.appendChild(ui.speaker);
    ui.collapse = button("guide-icon-btn", "", () => setCollapsed(!root.classList.contains("is-collapsed")));
    ui.collapse.setAttribute("aria-label", "Collapse or expand the guide");
    ui.collapse.appendChild(el("span", "chev"));
    head.appendChild(ui.collapse);
    ui.close = button("guide-icon-btn", "✕", () => openBubble(false));
    head.appendChild(ui.close);
    bubble.appendChild(head);

    ui.setup = el("div", "guide-setup");
    ui.setupLabel = el("p", "guide-why");
    ui.setup.appendChild(ui.setupLabel);
    for (const [level, label] of LEVELS) ui.setup.appendChild(button("btn btn-secondary guide-level", label, () => { gesture = true; setLevel(level); }));
    bubble.appendChild(ui.setup);

    ui.body = el("div", "guide-body");
    ui.voiceCard = el("div", "guide-voice-card");
    ui.voiceCardText = el("p", "guide-why");
    ui.voiceCard.appendChild(ui.voiceCardText);
    const vrow = el("div", "guide-actions");
    ui.voiceOn = button("btn btn-primary", "", () => { gesture = true; setVoice("on"); });
    ui.voiceLater = button("btn btn-quiet", "", () => setVoice("off"));
    vrow.appendChild(ui.voiceOn);
    vrow.appendChild(ui.voiceLater);
    ui.voiceCard.appendChild(vrow);
    ui.voiceCard.appendChild(el("p", "guide-note", VOICE_NOTE));
    ui.body.appendChild(ui.voiceCard);
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
    form.addEventListener("submit", (e) => { e.preventDefault(); gesture = true; askPet(ui.ask.value); });
    ui.body.appendChild(form);
    ui.answer = el("div", "guide-answer");
    ui.answer.hidden = true;
    ui.body.appendChild(ui.answer);
    ui.body.appendChild(settings());
    bubble.appendChild(ui.body);
    root.appendChild(bubble);
    document.body.appendChild(root);
    ui.root = root;

    ui.launcher = button("guide-launcher", "Call MAYA", () => callPet());
    ui.launcher.id = "guide-launcher";
    document.body.appendChild(ui.launcher);
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
    };
    const select = (options, onChange) => {
      const s = el("select", "");
      for (const [v, label] of options) { const o = el("option", "", label); o.value = v; s.appendChild(o); }
      s.addEventListener("change", () => onChange(s.value));
      return s;
    };
    ui.levelSel = select(LEVELS, (v) => setLevel(v));
    row("Level", ui.levelSel);
    ui.langSel = select([["en", "English"], ["hinglish", "Hinglish"]], (v) => { prefs.lang = v; save("maya-guide-lang", v); loadVoices(); refresh(); });
    row("Language", ui.langSel);
    const speech = core.speechSupported(window);
    ui.voiceToggle = el("input", "");
    ui.voiceToggle.type = "checkbox";
    ui.voiceToggle.disabled = !speech;
    ui.voiceToggle.addEventListener("change", () => { gesture = true; setVoice(ui.voiceToggle.checked ? "on" : "off"); });
    row("Speak the steps", ui.voiceToggle);
    ui.speakAnswers = el("input", "");
    ui.speakAnswers.type = "checkbox";
    ui.speakAnswers.checked = prefs.speakAnswers;
    ui.speakAnswers.disabled = !speech;
    ui.speakAnswers.addEventListener("change", () => { prefs.speakAnswers = ui.speakAnswers.checked; save("maya-guide-speak-answers", prefs.speakAnswers ? "1" : "0"); });
    row("Speak answers too", ui.speakAnswers);
    const slider = (min, max, value, key, field) => {
      const r = el("input", "");
      r.type = "range";
      r.min = String(min);
      r.max = String(max);
      r.step = "0.1";
      r.value = String(value);
      r.disabled = !speech;
      r.addEventListener("change", () => { prefs[field] = parseFloat(r.value) || 1; save(key, String(prefs[field])); });
      return r;
    };
    row("Speed", slider(0.7, 1.5, prefs.rate, "maya-voice-rate", "rate"));
    row("Pitch", slider(0.6, 1.6, prefs.pitch, "maya-voice-pitch", "pitch"));
    const btns = el("div", "guide-actions");
    ui.testVoice = button("btn btn-secondary", "Test voice", () => { gesture = true; speak(texts().test, false, true); });
    ui.testVoice.id = "voice-test";
    ui.testVoice.disabled = !speech;
    const stop = button("btn btn-quiet", "Stop", stopSpeech);
    stop.disabled = !speech;
    btns.appendChild(ui.testVoice);
    btns.appendChild(stop);
    box.appendChild(btns);
    ui.voiceStatus = el("p", "guide-note guide-voice-status", speech ? "Looking for a voice..." : NO_TTS);
    ui.voiceStatus.id = "voice-status";
    box.appendChild(ui.voiceStatus);
    box.appendChild(el("p", "guide-note", VOICE_NOTE));
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

  function renderTexts() {
    const t = texts();
    ui.close.setAttribute("aria-label", t.close);
    ui.close.title = t.close;
    ui.next.textContent = t.next;
    ui.show.textContent = t.show;
    ui.send.textContent = t.send;
    ui.ask.placeholder = t.ask;
    ui.ask.setAttribute("aria-label", t.ask);
    ui.setupLabel.textContent = t.choose;
    ui.settingsLabel.textContent = t.settings;
    ui.voiceCardText.textContent = t.voiceCard;
    ui.voiceOn.textContent = t.voiceOn;
    ui.voiceLater.textContent = t.voiceLater;
    ui.levelSel.value = prefs.level || "new";
    ui.langSel.value = prefs.lang;
    ui.voiceCard.hidden = prefs.voice !== "unset" || !core.speechSupported(window);
    ui.voiceToggle.checked = prefs.voice === "on";
    ui.speaker.textContent = prefs.voice === "on" ? "\u{1F50A}" : "\u{1F507}";
    ui.speaker.setAttribute("aria-label", prefs.voice === "on" ? "Voice on: turn off" : "Voice off: turn on");
    ui.speaker.setAttribute("aria-pressed", prefs.voice === "on" ? "true" : "false");
    ui.speaker.hidden = !core.speechSupported(window);
  }

  // ---------- steps ----------
  function showStep() {
    const s = steps[stepIndex];
    if (!s) return;
    ui.line.textContent = s.line;
    ui.why.textContent = s.why || "";
    ui.why.hidden = !s.why;
    setTarget(core.firstTarget(s.targets, (id) => !!$(id) && !$(id).closest("[hidden]")));
    ui.next.hidden = steps.length < 2;
    if (pet) {
      pet.setEmotion(s.emotion);
      const t = target && $(target);
      if (t && s.emotion.some((e) => e.pose === "pointing")) setTimeout(() => pet.moveTo(t), 600);
    }
    if (prefs.voice === "on" && gesture) speak(s.line, false);
    else if (pet && !ui.root.hidden) briefTalk(s.line);
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
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    n.scrollIntoView({ block: "center", behavior: reduced ? "auto" : "smooth" });
    if (typeof n.focus === "function") n.focus({ preventScroll: true });
    if (window.matchMedia("(max-width: 860px)").matches) setCollapsed(true);
    if (pet) setTimeout(() => pet.moveTo(n), reduced ? 0 : 500);
  }
  function nextTip() { stepIndex = (stepIndex + 1) % Math.max(1, steps.length); showStep(); }

  async function refresh() {
    renderTexts();
    ui.setup.hidden = !!prefs.level;
    ui.body.hidden = !prefs.level;
    if (!prefs.level) { if (pet) pet.setEmotion([{ expression: "happy", pose: "waving", ms: 1800 }, { expression: "curious", pose: "idle" }]); setTarget(null); return; }
    let data = null;
    if (PREVIEW) {
      const r = await fetch("/__preview/sample.json", { credentials: "same-origin" });
      const sample = r.ok ? await r.json() : {};
      data = (sample.guide || {})[new URLSearchParams(window.location.search).get("guide") || "no_account_type"] || null;
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
  function openBubble(open) {
    ui.root.hidden = !open;
    document.body.classList.toggle("guide-open", open && !prefs.petHidden);
    if (!open) { stopSpeech(); setTarget(null); }
    if (pet && !prefs.petHidden) pet.goHome(false);
    if (open) refresh();
  }
  function setPetHidden(hidden) {
    prefs.petHidden = hidden;
    save("maya-pet-hidden", hidden ? "1" : "0");
    if (pet) pet.show(!hidden);
    ui.launcher.hidden = !hidden || !!$("pet-call");  // the app shell has its own "Call pet" button
    const hide = $("pet-hide");
    if (hide) {  // keep the icon, change only the label text
      const label = hide.lastChild && hide.lastChild.nodeType === 3 ? hide.lastChild : hide.appendChild(document.createTextNode(""));
      label.textContent = hidden ? "Show pet" : "Hide pet";
      hide.setAttribute("aria-pressed", hidden ? "true" : "false");
    }
    if (hidden) { openBubble(false); document.body.classList.remove("guide-open"); }
  }
  function callPet() {
    setPetHidden(false);
    if (pet) { pet.goHome(true); pet.hop(); }
    openBubble(true);
    setCollapsed(false);
  }
  function setCollapsed(collapsed) {
    ui.root.classList.toggle("is-collapsed", collapsed);
    ui.collapse.setAttribute("aria-expanded", collapsed ? "false" : "true");
  }

  // ---------- speech out: the browser's own text-to-speech ----------
  function setVoice(v) {
    prefs.voice = v;
    save("maya-voice", v);
    renderTexts();
    if (v === "on") { loadVoices(); if (steps[stepIndex]) speak(steps[stepIndex].line, false); } else stopSpeech();
  }
  // Windows Chrome loads voices late: listen for voiceschanged and retry getVoices() for about 3 s.
  function loadVoices() {
    if (!core.speechSupported(window)) return;
    const synth = window.speechSynthesis;
    const done = () => {
      voices = synth.getVoices() || [];
      const v = core.pickVoice(voices, prefs.lang);
      ui.voiceStatus.textContent = voices.length ? "Voice ready: " + (v ? v.name + " (" + v.lang + ")" : "browser default") +
        (v && v.localService === false ? ". This is a network voice: your browser sends the spoken text to its provider." : "")
        : NO_VOICE;
      return voices.length > 0;
    };
    if (done()) return;
    synth.onvoiceschanged = done;
    let tries = 0;
    const timer = setInterval(() => { tries += 1; if (done() || tries >= 12) clearInterval(timer); }, 250);
  }
  // Speak in sentence-sized pieces (Chrome stops one long utterance after about 15 s); the mouth follows
  // word boundary events, or a timer when the voice sends none.
  function speak(text, summary, force) {
    if (!core.speechSupported(window) || (!force && prefs.voice !== "on") || !gesture) return;
    const clean = core.speakable(text, summary ? 200 : 600, summary);
    const parts = window.MayaPet ? window.MayaPet.core.splitSentences(clean, 160) : [clean];
    if (!parts.length) return;
    const synth = window.speechSynthesis;
    synth.cancel();
    const run = ++speechRun;
    const voice = core.pickVoice(voices.length ? voices : synth.getVoices(), prefs.lang);
    let flap = null;
    let sawBoundary = false;
    const mouthTimer = (ms) => {
      clearInterval(flap);
      let open = false;
      const until = Date.now() + ms;
      flap = setInterval(() => {
        if (sawBoundary || Date.now() > until || run !== speechRun) { clearInterval(flap); return; }
        open = !open;
        if (pet) pet.mouth(open);
      }, 130);
    };
    parts.forEach((part, i) => {
      const u = new window.SpeechSynthesisUtterance(part);
      if (voice) { u.voice = voice; u.lang = voice.lang; }
      u.rate = prefs.rate;
      u.pitch = prefs.pitch;
      u.onstart = () => {
        if (run !== speechRun) return;
        if (pet) pet.talkStart();
        sawBoundary = false;
        mouthTimer(core.estimateMs(part, prefs.rate));
      };
      u.onboundary = () => {
        if (run !== speechRun || !pet) return;
        sawBoundary = true;
        pet.mouth(true);
        setTimeout(() => { if (pet) pet.mouth(false); }, 110);
      };
      u.onend = u.onerror = () => {
        if (run !== speechRun || i < parts.length - 1) return;
        clearInterval(flap);
        if (pet) pet.talkStop();
      };
      synth.speak(u);
    });
  }
  function stopSpeech() {
    speechRun += 1;
    if (core.speechSupported(window)) window.speechSynthesis.cancel();
    if (pet) pet.talkStop();
  }
  // without voice the mouth still moves briefly when a new line appears
  function briefTalk(text) {
    if (!pet || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    pet.talkStart();
    let open = false;
    const until = Date.now() + Math.min(2400, core.estimateMs(text, 1.4));
    const t = setInterval(() => {
      open = !open;
      pet.mouth(open);
      if (Date.now() > until) { clearInterval(t); pet.talkStop(); }
    }, 140);
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
    if (pet) pet.setEmotion(window.MayaPet.core.EVENT_EMOTION["waiting-model"]);
    showAnswer(null);
    const status = await fetch("/api/chats", { credentials: "same-origin" });
    const s = status.ok ? await status.json() : {};
    if (!s.model_configured) {
      showAnswer({ error: texts().nokey });
      if (pet) pet.setEmotion(window.MayaPet.core.EVENT_EMOTION["no-model"]);
      return;
    }
    if (!chatId) {
      const mine = (s.chats || []).find((c) => c.title === "Guide questions");
      chatId = mine ? mine.id : null;
      if (!chatId) {
        const created = await postJSON("/api/chats", { title: "Guide questions" });
        chatId = created && created.chat ? created.chat.id : null;
      }
    }
    if (!chatId) { showAnswer({ error: texts().error }); return; }
    const d = await postJSON("/api/chats/" + chatId + "/messages", { content: text, level: prefs.level || "new" });
    if (!d) return;
    ui.ask.value = "";
    showAnswer(d.reply);
    if (pet) pet.setEmotion(window.MayaPet.core.EVENT_EMOTION.answered);
    if (prefs.speakAnswers && prefs.voice === "on") speak(d.reply.content, true);
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
    if (!r || !r.ok) {
      showAnswer({ error: typeof data.error === "string" ? data.error : texts().error });
      if (pet) pet.setEmotion(window.MayaPet.core.EVENT_EMOTION.error);
      return null;
    }
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
    const table = window.MayaPet ? window.MayaPet.core.EVENT_EMOTION : {};
    if (type === "asset") assetId = e.detail.id || null;
    if (type === "view" && pet) pet.hop();
    if (type === "error") {
      if (pet) pet.setEmotion(table.error);
      ui.line.textContent = texts().error;
      ui.why.hidden = true;
      return;
    }
    if (table[type] && pet) pet.setEmotion(table[type]);
    if (type !== "view" && type !== "applying") refresh();
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!document.body || document.body.classList.contains("auth-page")) return;  // signed-in pages only
    build();
    const mobile = window.matchMedia("(max-width: 860px)").matches;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (window.MayaPet) {
      pet = window.MayaPet.live({ mobile: mobile, reduced: reduced, onOpen: () => { openBubble(true); setCollapsed(false); },
        homeAnchor: () => (ui.root.hidden ? null : ui.root.getBoundingClientRect()), anchorElement: () => ui.root });
    }
    const call = $("pet-call");
    if (call) call.addEventListener("click", callPet);
    const hide = $("pet-hide");
    if (hide) hide.addEventListener("click", () => setPetHidden(!prefs.petHidden));
    if (window.matchMedia("(max-width: 560px)").matches) setCollapsed(true);  // a slim bar on phones
    document.addEventListener("maya:event", onEvent);
    const asked = new URLSearchParams(window.location.search);
    if (PREVIEW && asked.get("level")) prefs.level = asked.get("level");
    if (PREVIEW) prefs.voice = asked.get("voice") || "off";
    if (PREVIEW && asked.get("expand")) setCollapsed(false);
    if (PREVIEW && asked.get("talk") && pet) setTimeout(() => { pet.talkStart(); pet.mouth(true); }, 3000);  // screenshot of a speaking frame
    loadVoices();
    // browsers allow speech only after a user gesture: the first click or key press counts
    const firstGesture = () => {
      if (gesture) return;
      gesture = true;
      if (prefs.voice === "on" && steps[stepIndex]) speak(steps[stepIndex].line, false);
    };
    document.addEventListener("pointerdown", firstGesture, { once: true });
    document.addEventListener("keydown", firstGesture, { once: true });
    setPetHidden(prefs.petHidden && !PREVIEW);
    openBubble(!prefs.petHidden || PREVIEW);
    if (pet) pet.goHome(false);
  });
})();
