"use strict";
// MAYA guide: what the MAYA pet (pet.js) says and when. The next step comes from GET /api/guide (a fixed state
// machine on the server, never the model); browser-side lines come from pet-lines.js (fixed templates). The pet
// shows a small bubble with chips beside itself; all settings live on the "Pet settings" page. Voice uses the
// browser's own speechSynthesis / SpeechRecognition only; MAYA's server never receives audio.
// `core` has no DOM access and is unit-tested in Node.
(function () {
  // names that suggest a female or a male voice (the default never picks a male-sounding voice)
  const FEMALE = /(zira|heera|kalpana|neerja|swara|aria|jenny|samantha|veena|susan|hazel|sonia|libby|natasha|catherine|female)/i;
  const MALE = /\b(david|mark|ravi|hemant|prabhat|madhur|guy|daniel|alex|george|james|ryan|richard|fred|male)\b/i;
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
        const first = t.match(/^.*?[.!?।](\s|$)/);
        if (first && first[0].length >= 20) t = first[0].trim();
      }
      if (t.length > limit) t = t.slice(0, limit).replace(/\s+\S*$/, "") + ".";
      return t;
    },
    isFemale(v) { return !!v && FEMALE.test(String(v.name || "")) && !MALE.test(String(v.name || "")); },
    isMale(v) { return !!v && MALE.test(String(v.name || "")); },
    // {voice, kind}: kind = "saved" (the user's pick), "female" (default), "neutral" (no female-sounding voice,
    // but not a male-sounding one) or "none". Language order: hinglish = hi-IN, en-IN, other English;
    // en = en-IN, other English. Voices on this device come before online voices.
    pickVoice(voices, lang, savedURI) {
      const list = (Array.isArray(voices) ? voices : []).filter((x) => x && typeof x.lang === "string");
      if (savedURI) {
        const saved = list.find((x) => x.voiceURI === savedURI);
        if (saved) return { voice: saved, kind: "saved" };
      }
      const langs = lang === "hinglish" ? ["hi-IN", "en-IN", "en-"] : ["en-IN", "en-"];
      const inLang = (x, l) => x.lang.replace("_", "-").indexOf(l) === 0;
      const ordered = (pool) => pool.filter((x) => x.localService !== false).concat(pool.filter((x) => x.localService === false));
      for (const want of ["female", "neutral"]) {
        for (const l of langs) {
          const pool = ordered(list.filter((x) => inLang(x, l) && !core.isMale(x) && (want === "neutral" || core.isFemale(x))));
          if (pool.length) return { voice: pool[0], kind: want };
        }
      }
      return { voice: null, kind: "none" };
    },
    // the line to speak: the Devanagari version only with a Hindi voice, otherwise the Roman text
    spokenText(line, spokenHi, voice) {
      return spokenHi && voice && String(voice.lang || "").replace("_", "-").indexOf("hi") === 0 ? spokenHi : line;
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
  const VOICE_NOTE = "Voice uses your browser's built-in text-to-speech, not a Meta model. Some browser voices are " +
    "online voices: the browser may send the spoken text to the voice provider. MAYA itself sends nothing.";
  const NO_FEMALE = "No female-sounding voice found: pick one manually or install a speech voice in Windows Settings > Speech; text still works";
  const NO_TTS = "This browser has no text-to-speech; text still works.";
  const MIC_NOTE = "Your browser may send the audio to its speech provider to turn it into text. MAYA stores nothing.";
  const LEARNED_KEYS = ["maya-pet-used", "maya-pet-tour-done"];

  function load(key, fallback) {
    let v = null;
    try { v = window.localStorage.getItem(key); } catch (e) { v = null; }
    return v === null ? fallback : v;
  }
  function save(key, value) {
    try { window.localStorage.setItem(key, value); } catch (e) { /* the choice still applies to this page */ }
  }
  function forget(key) {
    try { window.localStorage.removeItem(key); } catch (e) { /* nothing stored */ }
  }
  const num = (v, lo, hi, d) => Math.min(hi, Math.max(lo, parseFloat(v) || d));
  const prefs = {
    petHidden: load("maya-pet-hidden", "0") === "1",
    level: load("maya-guide-level", ""),
    lang: load("maya-guide-lang", "en") === "hinglish" ? "hinglish" : "en",
    voice: load("maya-voice", "unset"),            // unset | on | off; "on" only after the user's own click
    voiceURI: load("maya-voice-uri", ""),
    speakSteps: load("maya-speak-steps", "1") === "1",
    speakAnswers: load("maya-guide-speak-answers", "0") === "1",
    rate: num(load("maya-voice-rate", "1"), 0.7, 1.5, 1),
    pitch: num(load("maya-voice-pitch", "1.2"), 0.6, 1.8, 1.2),
  };
  let steps = [];
  let stepIndex = 0;
  let assetId = null;
  let target = null;
  let pet = null;
  let voices = [];
  let gesture = false;  // speechSynthesis may only start after a user gesture
  let speechRun = 0;
  let greetKey = null;
  const L = () => (window.MayaLines || { LINES: {}, CHIPS: {} });
  const line = (name) => { const x = L().LINES[name] || {}; return { text: x[prefs.lang] || x.en || "", spoken: prefs.lang === "hinglish" ? x.spoken_hi : null }; };
  const chip = (name, onClick) => ({ label: (L().CHIPS[name] || {})[prefs.lang] || name, onClick: onClick });

  // ---------- what MAYA says ----------
  function say(text, spokenHi, chips, opts) {
    if (!pet || prefs.petHidden) return;
    pet.say(text, chips || [], opts || {});
    if (prefs.voice === "on" && gesture && prefs.speakSteps) speak(text, spokenHi, false);
    else briefTalk(text);
  }
  function showStep() {
    const s = steps[stepIndex];
    if (!s || !pet) return;
    target = core.firstTarget(s.targets, (id) => !!$(id) && !$(id).closest("[hidden]"));
    document.querySelectorAll(".guide-target").forEach((n) => n.classList.remove("guide-target"));
    const t = target && $(target);
    if (t) t.classList.add("guide-target");
    pet.setEmotion(s.emotion);
    if (t && s.emotion.some((e) => e.pose === "pointing")) setTimeout(() => pet.moveTo(t), 500);
    const chips = [];
    if (t) chips.push(chip("show", showTarget));
    if (steps.length > 1) chips.push(chip("next", nextTip));
    say(s.line, s.spoken_hi, chips, { avoid: t || null });
  }
  function showTarget() {
    const n = target && $(target);
    if (!n) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    n.scrollIntoView({ block: "center", behavior: reduced ? "auto" : "smooth" });
    if (typeof n.focus === "function") n.focus({ preventScroll: true });
    if (pet) setTimeout(() => pet.moveTo(n), reduced ? 0 : 500);
  }
  function nextTip() { stepIndex = (stepIndex + 1) % Math.max(1, steps.length); showStep(); }

  async function refresh() {
    if (!prefs.level) return askLevel();
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

  // ---------- first time on this device for this account: wave, greet, ask the level ----------
  function levelChips() {
    return [chip("levelNew", () => setLevel("new")), chip("levelSome", () => setLevel("some")),
      chip("levelTech", () => setLevel("technical"))];
  }
  function greet() {
    if (pet) pet.setEmotion(window.MayaPet.core.EVENT_EMOTION.greet);
    const g = line("greeting");
    say(g.text, g.spoken, levelChips(), { sticky: true });
    if (greetKey) save(greetKey, "1");
  }
  function askLevel() {
    const a = line("askLevel");
    say(a.text, a.spoken, levelChips(), { sticky: true });
  }
  function setLevel(level) {
    if (["new", "some", "technical"].indexOf(level) < 0) return;
    gesture = true;
    prefs.level = level;
    save("maya-guide-level", level);
    const role = { new: "owner", some: "developer", technical: "technical" }[level];
    save("maya-role", role);
    document.dispatchEvent(new CustomEvent("maya:role", { detail: role }));  // the findings toggle follows the level
    syncSettings();
    if (prefs.voice === "unset" && core.speechSupported(window)) {
      const v = line("askVoice");
      say(v.text, v.spoken, [chip("voiceOn", () => { setVoice("on"); refresh(); }),
        chip("later", () => { setVoice("off"); refresh(); })], { sticky: true });
    } else {
      refresh();
    }
  }

  // ---------- voice: the browser's own text-to-speech ----------
  function setVoice(v) {
    gesture = true;
    prefs.voice = v;
    save("maya-voice", v);
    if (pet) pet.setMuted(v !== "on");
    if (v !== "on") stopSpeech();
    syncSettings();
  }
  function chosenVoice() {
    return core.pickVoice(voices.length ? voices : (core.speechSupported(window) ? window.speechSynthesis.getVoices() : []),
      prefs.lang, prefs.voiceURI);
  }
  // Windows Chrome loads voices late: listen for voiceschanged and retry getVoices() for about 3 s.
  function loadVoices() {
    if (!core.speechSupported(window)) { setStatus(NO_TTS); return; }
    const synth = window.speechSynthesis;
    const done = () => {
      voices = synth.getVoices() || [];
      fillVoicePicker();
      return voices.length > 0;
    };
    if (done()) return;
    synth.onvoiceschanged = done;
    let tries = 0;
    const timer = setInterval(() => { tries += 1; if (done() || tries >= 12) clearInterval(timer); }, 250);
  }
  function setStatus(text) { const n = $("pet-voice-status"); if (n) n.textContent = text; }
  function fillVoicePicker() {
    const pick = chosenVoice();
    const sel = $("pet-voice");
    if (sel) {
      while (sel.firstChild) sel.removeChild(sel.firstChild);
      for (const v of voices) {
        const o = document.createElement("option");
        o.value = v.voiceURI;
        o.textContent = v.name + " (" + v.lang + ")" + (v.localService === false ? ", online" : "");
        sel.appendChild(o);
      }
      if (pick.voice) sel.value = pick.voice.voiceURI;
    }
    if (!voices.length) setStatus("No voice is installed in this browser; text still works.");
    else if (pick.kind === "female" || pick.kind === "saved") {
      setStatus("Voice: " + pick.voice.name + " (" + pick.voice.lang + ")" + (pick.voice.localService === false ? ", an online voice" : ""));
    } else setStatus(NO_FEMALE + (pick.voice ? ". For now: " + pick.voice.name + "." : "."));
  }
  // Speak in sentence-sized pieces (Chrome stops one long utterance after about 15 s); the mouth follows
  // word boundary events, or a timer when the voice sends none.
  function speak(text, spokenHi, summary, force) {
    if (!core.speechSupported(window) || (!force && prefs.voice !== "on") || !gesture) return;
    const pick = chosenVoice();
    const clean = core.speakable(core.spokenText(text, spokenHi, pick.voice), summary ? 200 : 600, summary);
    const parts = window.MayaPet ? window.MayaPet.core.splitSentences(clean.replace(/।/g, "."), 160) : [clean];
    if (!parts.length) return;
    const synth = window.speechSynthesis;
    synth.cancel();
    const run = ++speechRun;
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
      if (pick.voice) { u.voice = pick.voice; u.lang = pick.voice.lang; }
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

  // ---------- speech in: only when the user turns the microphone on (settings) ----------
  function listen(into) {
    const Rec = core.recognitionCtor(window);
    if (!Rec || load("maya-pet-mic", "0") !== "1" || !into) return;
    const rec = new Rec();
    rec.lang = prefs.lang === "hinglish" ? "hi-IN" : "en-IN";
    rec.interimResults = false;
    rec.maxAlternatives = 1;
    rec.onresult = (e) => { const r = e.results && e.results[0] && e.results[0][0]; if (r) into.value = String(r.transcript || "").slice(0, 2000); };
    try { rec.start(); } catch (e) { /* already listening */ }
  }

  // a microphone button in the chat composer, only when the user turned it on and the browser supports it
  function syncMic() {
    const composer = $("composer");
    const want = !!composer && !!core.recognitionCtor(window) && load("maya-pet-mic", "0") === "1";
    const btn = $("composer-mic");
    if (!want) { if (btn) btn.remove(); return; }
    if (btn) return;
    const b = document.createElement("button");
    b.type = "button";
    b.id = "composer-mic";
    b.className = "investigate";
    b.textContent = "\u{1F3A4}";
    b.setAttribute("aria-label", "Speak your message (your browser's speech recognition)");
    b.addEventListener("click", () => listen($("prompt")));
    composer.insertBefore(b, $("investigate-btn"));
  }

  // ---------- the Pet settings page ----------
  function syncSettings() {
    const set = (id, prop, value) => { const n = $(id); if (n) n[prop] = value; };
    set("pet-level", "value", prefs.level || "new");
    set("pet-lang", "value", prefs.lang);
    set("pet-rate", "value", String(prefs.rate));
    set("pet-pitch", "value", String(prefs.pitch));
    set("pet-speak-steps", "checked", prefs.voice === "on" && prefs.speakSteps);
    set("pet-speak-answers", "checked", prefs.speakAnswers);
    set("pet-mic", "checked", load("maya-pet-mic", "0") === "1");
  }
  function wireSettings() {
    if (!$("pet-view")) return;
    const speech = core.speechSupported(window);
    const on = (id, ev, fn) => { const n = $(id); if (n) n.addEventListener(ev, fn); };
    const test = () => { gesture = true; const t = line("voiceTest"); speak(t.text, t.spoken, false, true); };
    $("pet-voice-note").textContent = VOICE_NOTE;
    on("pet-level", "change", () => setLevel($("pet-level").value));
    on("pet-lang", "change", () => { prefs.lang = $("pet-lang").value; save("maya-guide-lang", prefs.lang); fillVoicePicker(); refresh(); });
    on("pet-voice", "change", () => { prefs.voiceURI = $("pet-voice").value; save("maya-voice-uri", prefs.voiceURI); fillVoicePicker(); });
    on("pet-voice-preview", "click", test);
    on("pet-voice-test", "click", test);
    on("pet-voice-stop", "click", stopSpeech);
    on("pet-rate", "change", () => { prefs.rate = num($("pet-rate").value, 0.7, 1.5, 1); save("maya-voice-rate", String(prefs.rate)); });
    on("pet-pitch", "change", () => { prefs.pitch = num($("pet-pitch").value, 0.6, 1.8, 1.2); save("maya-voice-pitch", String(prefs.pitch)); });
    on("pet-speak-steps", "change", () => {
      prefs.speakSteps = $("pet-speak-steps").checked;
      save("maya-speak-steps", prefs.speakSteps ? "1" : "0");
      if (prefs.speakSteps) setVoice("on");
    });
    on("pet-speak-answers", "change", () => {
      prefs.speakAnswers = $("pet-speak-answers").checked;
      save("maya-guide-speak-answers", prefs.speakAnswers ? "1" : "0");
    });
    on("pet-mic", "change", () => {
      save("maya-pet-mic", $("pet-mic").checked ? "1" : "0");
      $("pet-mic-note").hidden = !$("pet-mic").checked;
      syncMic();
    });
    on("pet-reset", "click", () => {
      for (const k of LEARNED_KEYS) forget(k);
      if (greetKey) forget(greetKey);
      $("pet-reset-message").textContent = "Done: the pet forgot what it learned on this device.";
    });
    for (const id of ["pet-voice", "pet-voice-preview", "pet-voice-test", "pet-voice-stop", "pet-rate", "pet-pitch",
      "pet-speak-steps", "pet-speak-answers"]) if ($(id)) $(id).disabled = !speech;
    $("pet-mic").closest(".panel").hidden = !core.recognitionCtor(window);  // no speech recognition: no microphone option
    $("pet-mic-note").textContent = MIC_NOTE;
    $("pet-mic-note").hidden = !$("pet-mic").checked;
    syncSettings();
    syncMic();
  }

  // ---------- hide / call ----------
  function setPetHidden(hidden) {
    prefs.petHidden = hidden;
    save("maya-pet-hidden", hidden ? "1" : "0");
    if (pet) pet.show(!hidden);
    const launcher = $("guide-launcher");
    if (launcher) launcher.hidden = !hidden || !!$("pet-call");  // the app shell has its own "Call pet" button
    if (hidden) stopSpeech();
  }
  function callPet() {
    setPetHidden(false);
    if (pet) { pet.goHome(true); pet.hop(); }
    refresh();
  }

  // ---------- real events from the page (app.js) ----------
  function onEvent(e) {
    const type = e.detail && e.detail.type;
    const table = window.MayaPet ? window.MayaPet.core.EVENT_EMOTION : {};
    if (type === "asset") assetId = e.detail.id || null;
    if (type === "view") { if (pet) pet.hop(); return; }
    if (type === "error") {
      if (pet) pet.setEmotion(table.error);
      const x = line("error");
      say(x.text, x.spoken, [], {});
      return;
    }
    if (type === "answered") {
      if (pet) pet.setEmotion(table.answered);
      if (prefs.speakAnswers && prefs.voice === "on") speak(e.detail.text, null, true);
      return;
    }
    if (table[type] && pet) pet.setEmotion(table[type]);
    if (type !== "applying") refresh();
  }

  // the greeting is remembered per account on this device (a short hash of the email, never the email itself)
  async function accountKey() {
    let email = "";
    try {
      const r = await fetch("/api/session", { credentials: "same-origin" });
      const d = r.ok ? await r.json() : {};
      email = d.user && d.user.email ? d.user.email : (PREVIEW ? "preview@maya.local" : "");
    } catch (e) { email = ""; }
    if (!email || !window.crypto || !window.crypto.subtle) return "maya-greeted";
    const hash = await window.crypto.subtle.digest("SHA-256", new TextEncoder().encode(email.toLowerCase()));
    return "maya-greeted-" + Array.from(new Uint8Array(hash)).slice(0, 6).map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  document.addEventListener("DOMContentLoaded", async () => {
    if (!document.body || document.body.classList.contains("auth-page")) return;  // signed-in pages only
    const mobile = window.matchMedia("(max-width: 860px)").matches;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const asked = new URLSearchParams(window.location.search);
    if (PREVIEW) {  // screenshots: a clean device, the voice not yet chosen
      prefs.level = asked.get("level") || "";
      prefs.voice = asked.get("voice") || "unset";
      prefs.petHidden = false;
    }
    if (window.MayaPet) {
      pet = window.MayaPet.live({ mobile: mobile, reduced: reduced,
        onMute: () => setVoice(prefs.voice === "on" ? "off" : "on"),
        onHide: () => setPetHidden(true),
        onOpen: () => (steps.length ? showStep() : refresh()),
        homeAnchor: () => { const c = $("composer"); return mobile && c ? c.getBoundingClientRect() : null; } });
      pet.setMuted(prefs.voice !== "on");
    }
    const launcher = document.createElement("button");
    launcher.type = "button";
    launcher.id = "guide-launcher";
    launcher.className = "guide-launcher";
    launcher.textContent = "Call MAYA";
    launcher.addEventListener("click", callPet);
    document.body.appendChild(launcher);
    const call = $("pet-call");
    if (call) call.addEventListener("click", callPet);
    wireSettings();
    loadVoices();
    document.addEventListener("maya:event", onEvent);
    // browsers allow speech only after a user gesture: the first click or key press counts
    const firstGesture = () => { gesture = true; };
    document.addEventListener("pointerdown", firstGesture, { once: true });
    document.addEventListener("keydown", firstGesture, { once: true });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") { stopSpeech(); if (pet) pet.hideBubble(); } });
    setPetHidden(prefs.petHidden);
    greetKey = await accountKey();
    if (prefs.petHidden) return;
    const shouldGreet = PREVIEW ? !!asked.get("greet") : load(greetKey, "0") !== "1";
    if (shouldGreet) greet(); else refresh();
    if (PREVIEW && asked.get("talk") && pet) setTimeout(() => { pet.talkStart(); pet.mouth(true); }, 3000);  // speaking frame
  });
  window.MayaGuide = { listen: listen };
})();
