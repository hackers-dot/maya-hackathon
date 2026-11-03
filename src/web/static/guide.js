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
    lang: "en",  // English only for now (the Hinglish lines stay in the code, out of the UI)
    audience: load("maya-guide-audience", ""),
    voice: load("maya-voice", "on") === "off" ? "off" : "on",  // "off" only when the user pressed mute
    voiceURI: load("maya-voice-uri", ""),
    speakSteps: load("maya-speak-steps", "1") === "1",
    speakHover: load("maya-speak-hover", "1") === "1",
    speakAnswers: load("maya-guide-speak-answers", "1") === "1",
    calm: load("maya-pet-calm", "0") === "1",  // Calm mode: no walking or antics (bubble and speech stay)
    rate: num(load("maya-voice-rate", "1"), 0.7, 1.5, 1),
    pitch: num(load("maya-voice-pitch", "1.2"), 0.6, 1.8, 1.2),
  };
  let steps = [];
  let stepIndex = 0;
  let assetId = null;
  let target = null;
  let pet = null;
  let voices = [];
  // browsers allow speechSynthesis only after a user activation: until the first gesture the latest line waits
  let gesture = !!(navigator.userActivation && navigator.userActivation.hasBeenActive);
  let pendingSpeech = null;
  let speechRun = 0;
  let sticky = null;          // the last bubble with choices, shown again after a hover explanation
  let hoverSpeaking = false;
  let lastHoverSpoken = {};   // control -> time: the same control is not spoken again within 30 s
  let chosenLevelJustNow = false;
  let tourLines = null;
  let lastClickAt = 0;
  let currentAssetKind = null;
  const SIGNIN = document.body ? document.body.classList.contains("auth-page") : false;
  let greetKey = null;
  // tier 2: hover guide, what the pet learned on this device, work/play mode, post-scan tour
  const INTERACTIVE = "button, a, [role=button], [role=tab], input, select, summary, label, .finding-card, .tab, " +
    "[data-guide], .finding-sum, .asset-item";
  let used = {};
  let hoverEl = null;
  let hoverTimer = null;
  let leaveTimer = null;
  let hoverShown = false;
  let lastHoverAt = 0;
  let lastActiveAt = Date.now();
  let lastTypingAt = 0;
  let suggestedHere = false;
  let pendingTour = false;
  let tour = null;
  // tier 3: the pet may act only from the allowlist in pet-brain.js, and only after the user turned it on
  let helpOn = load("maya-pet-help", "0") === "1";
  let actions = null;
  const L = () => (window.MayaLines || { LINES: {}, CHIPS: {} });
  const line = (name) => { const x = L().LINES[name] || {}; return { text: x.en || "", spoken: null }; };
  const chip = (name, onClick) => ({ label: (L().CHIPS[name] || {})[prefs.lang] || name, onClick: onClick });

  // ---------- what MAYA says ----------
  function unlockHint() {
    const h = L().LINES.tapToTalk || {};
    return !gesture && prefs.voice === "on" && core.speechSupported(window) ? (h[prefs.lang] || h.en || "") : "";
  }
  function say(text, spokenHi, chips, opts, kind) {
    if (!pet || prefs.petHidden) return;
    const o = Object.assign({}, opts || {}, { hint: unlockHint() });
    pet.say(text, chips || [], o);
    if (o.sticky && (chips || []).length) sticky = { text: text, chips: chips, opts: opts || {} };
    else if ((chips || []).length === 0 && kind !== "hover") sticky = null;
    const allowed = kind === "hover" ? prefs.speakHover : kind === "answer" ? prefs.speakAnswers : prefs.speakSteps;
    if (prefs.voice === "on" && allowed) speak(text, spokenHi, kind === "answer");
    else briefTalk(text, true);
  }
  function showStep() {
    const s = steps[stepIndex];
    if (!s || !pet) return;
    target = core.firstTarget(s.targets, (id) => !!$(id) && !$(id).closest("[hidden]"));
    document.querySelectorAll(".guide-target").forEach((n) => n.classList.remove("guide-target"));
    const t = target && $(target);
    if (t) t.classList.add("guide-target");
    const emotion = chosenLevelJustNow ? [{ expression: "happy", pose: "idle", ms: 1400 }].concat(s.emotion) : s.emotion;
    chosenLevelJustNow = false;
    pet.setEmotion(emotion);
    if (t && s.emotion.some((e) => e.pose === "pointing")) setTimeout(() => pet.moveTo(t), 500);
    const chips = [];
    const never = t && brain() && brain().NEVER_IDS.indexOf(t.id) >= 0;
    if (t && never && helpOn) chips.push(chip(/^scan(-empty)?-btn$/.test(t.id) ? "askScan" : "whereIs", () => showWhere(t)));
    else if (t) chips.push(chip("show", showTarget));
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
    if (!prefs.audience) return askAudience();
    let data = null;
    if (SIGNIN) {
      const x = line("signinStep");
      data = { steps: [{ state: "signin", line: x.text, spoken_hi: x.spoken, why: null, targets: ["signin-email", "signup-email"],
        emotion: [{ expression: "smiling", pose: "pointing", ms: null }] }] };
    } else if (PREVIEW) {
      const r = await fetch("/__preview/sample.json", { credentials: "same-origin" });
      const sample = r.ok ? await r.json() : {};
      data = (sample.guide || {})[new URLSearchParams(window.location.search).get("guide") || "no_account_type"] || null;
    } else {
      const q = "?level=" + encodeURIComponent(prefs.level) + "&lang=en&audience=" + encodeURIComponent(prefs.audience || "other") +
        (assetId ? "&asset=" + encodeURIComponent(assetId) : "");
      const r = await fetch("/api/guide" + q, { credentials: "same-origin" });
      data = r.ok ? await r.json() : null;
    }
    if (!data) return;
    tourLines = data.tour || null;
    steps = data.steps || [];
    stepIndex = 0;
    if (pendingTour) { pendingTour = false; offerTour(); } else showStep();
  }

  // ---------- hover guide: what a control does, beside it, never covering it ----------
  const brain = () => window.MayaBrain;
  function loadUsed() {
    let raw = null;
    try { raw = window.localStorage.getItem("maya-pet-used"); } catch (e) { raw = null; }
    try { const v = JSON.parse(raw || "{}"); return v && typeof v === "object" ? v : {}; } catch (e) { return {}; }
  }
  function saveUsed() { save("maya-pet-used", JSON.stringify(used)); }
  // typing = a key pressed in a text field in the last 2.5 s (a focused but quiet field does not count)
  function typing() { return Date.now() - lastTypingAt < 2500; }
  function chipsOpen() { return !!pet && !pet.bubble.hidden && !!pet.bubble.querySelector(".pet-chip"); }
  function hoverKey(el) {
    const b = brain();
    return b ? b.keyFor(el.getAttribute("data-guide"), el.id) : null;
  }
  // a clean accessible name: data-guide-name or aria-label only, never the concatenated text of child elements
  function accessibleName(el) {
    return (el.getAttribute("data-guide-name") || el.getAttribute("aria-label") || "").replace(/\s+/g, " ").trim();
  }
  function hoverFor(el, key) {
    return brain().hoverText(key, { count: key ? used[key] || 0 : 0, name: accessibleName(el), audience: prefs.audience || "other",
      level: prefs.level || "new", demo: currentAssetKind === "demo" });
  }
  function explain(el) {
    const b = brain();
    if (!b || !pet || prefs.petHidden || tour || typing() || !document.contains(el)) return;
    const key = hoverKey(el);
    const t = hoverFor(el, key);
    lastHoverAt = Date.now();
    hoverShown = true;
    pet.setEmotion([{ expression: t.isNew ? "excited" : "curious", pose: "pointing", ms: null }]);
    pet.moveTo(el, Infinity);
    pet.say(t.text, [], { avoid: el, ms: 9000, hint: unlockHint() });
    // one entry per control: dynamic items share a key, so their clean name tells them apart
    const id = (key || "") + ":" + accessibleName(el);
    const now = Date.now();
    if (prefs.voice === "on" && prefs.speakHover && now - (lastHoverSpoken[id] || 0) > 30000) {
      lastHoverSpoken[id] = now;
      hoverSpeaking = true;
      speak(t.spoken, null, false);  // cancels whatever was being said: never more than one hover line
    } else briefTalk(t.text, true);
  }
  function endHover() {
    hoverShown = false;
    if (hoverSpeaking) { hoverSpeaking = false; stopSpeech(); }
    if (!pet || tour) return;
    pet.goHome(false);
    if (sticky) pet.say(sticky.text, sticky.chips, Object.assign({}, sticky.opts, { hint: unlockHint() }));  // shown again, not spoken
    else pet.hideBubble();
    if (steps[stepIndex]) pet.setEmotion(steps[stepIndex].emotion);
  }
  function wireHover() {
    const fine = window.matchMedia("(hover: hover) and (pointer: fine)").matches;
    if (!fine || window.matchMedia("(max-width: 860px)").matches) return;  // never on touch devices or phones
    document.addEventListener("pointerover", (e) => {
      const el = e.target.closest && e.target.closest(INTERACTIVE);
      if (!el || el.closest(".pet-layer") || el === hoverEl) return;
      if (Date.now() - lastClickAt < 1500) return;  // the page is reacting to a click: let the reaction show
      hoverEl = el;
      clearTimeout(hoverTimer);
      clearTimeout(leaveTimer);
      hoverTimer = setTimeout(() => { if (hoverEl === el) explain(el); }, 400);
    }, true);
    document.addEventListener("pointerout", (e) => {
      if (!hoverEl || (e.relatedTarget && hoverEl.contains(e.relatedTarget))) return;
      if (e.relatedTarget && e.relatedTarget.closest && e.relatedTarget.closest(".pet-layer")) return;
      hoverEl = null;
      clearTimeout(hoverTimer);
      if (hoverShown) leaveTimer = setTimeout(endHover, 800);
    }, true);
  }
  // what the pet learns: which controls this user clicks, how often (this device only)
  function wireLearning() {
    used = loadUsed();
    document.addEventListener("click", (e) => {
      const el = e.target.closest && e.target.closest(INTERACTIVE);
      if (!el || el.closest(".pet-layer") || !brain()) return;
      lastClickAt = Date.now();
      clearTimeout(hoverTimer);
      const key = hoverKey(el);
      if (key) used = brain().bump(used, key);
      if (el.closest("#fixfirst-panel")) used = brain().bump(used, "fixfirst-panel");
      if (key || el.closest("#fixfirst-panel")) saveUsed();
      const after = key ? brain().afterText(key, prefs.audience || "other") : null;
      if (!after) return;
      if (el.tagName === "A" && el.getAttribute("href") && el.getAttribute("href").charAt(0) === "/") {
        save("maya-pet-after", JSON.stringify({ text: after, at: Date.now() }));  // said when the next page opens
      } else {
        setTimeout(() => { clearTimeout(leaveTimer); hoverShown = false; say(after, null, [], { avoid: el }); }, 350);
      }
    }, true);
    for (const ev of ["pointermove", "pointerdown", "scroll", "keydown"]) {
      document.addEventListener(ev, () => { lastActiveAt = Date.now(); }, { passive: true, capture: true });
    }
    document.addEventListener("keydown", (e) => {
      const t = e.target;
      if (t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT" || t.isContentEditable)) lastTypingAt = Date.now();
    }, true);
  }
  function currentMode() {
    const b = brain();
    const state = steps[0] ? steps[0].state : null;
    if (!b) return "work";
    return b.mode({ state: state, scanRunning: state === "scan_running" || !!tour, typing: typing(),
      sinceHoverMs: Date.now() - lastHoverAt, idleMs: Date.now() - lastActiveAt });
  }
  // when the user is idle on a screen: suggest ONE useful control they have never tried
  function maybeSuggest() {
    const b = brain();
    if (!b || !pet || suggestedHere || tour || chipsOpen() || prefs.petHidden || currentMode() !== "play") return;
    const visible = b.SUGGEST.filter((id) => {
      const n = $(id);
      if (!n || n.closest("[hidden]")) return false;
      const r = n.getBoundingClientRect();
      return r.width > 0 && r.bottom > 0 && r.top < window.innerHeight;
    });
    const key = b.pickSuggestion(visible, used);
    if (!key) return;
    suggestedHere = true;
    const text = b.suggestText(key, prefs.lang);
    pet.setEmotion([{ expression: "curious", pose: "pointing", ms: null }]);
    pet.say(text, [], { avoid: $(key), ms: 6000 });
    briefTalk(text);
  }

  // ---------- "Let the pet help me": allowlisted actions only, logged, cancelled by Esc or any manual click ----------
  function logAct(text) {
    const list = $("pet-log");
    if (!list) return;
    const li = document.createElement("li");
    li.textContent = text;
    list.insertBefore(li, list.firstChild);
    while (list.children.length > 30) list.removeChild(list.lastChild);
  }
  function act(action, el, value) {
    const b = brain();
    if (!b) return false;
    const verdict = b.checkAction({ helpOn: helpOn, action: action, id: el ? el.id : "", tag: el ? el.tagName : "",
      classes: el ? Array.from(el.classList) : [], value: value,
      isFinding: !!(el && el.matches && el.matches("details.finding")),
      isReadOnlyDetails: !!(el && el.tagName === "DETAILS" && !el.querySelector("input, textarea, select, button")) });
    const label = el ? (el.id ? "#" + el.id : (el.querySelector && el.querySelector(".f-title") ? "'" + el.querySelector(".f-title").textContent + "'" : el.tagName.toLowerCase())) : String(value);
    logAct(b.logLine(verdict.ok, action, label, verdict.reason));
    if (!verdict.ok) return false;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (action === "scroll") el.scrollIntoView({ block: "center", behavior: reduced ? "auto" : "smooth" });
    else if (action === "expand" || action === "open-panel") el.open = true;
    else if (action === "collapse") el.open = false;
    else if (action === "highlight") el.classList.add("guide-target");
    else if (action === "role") document.dispatchEvent(new CustomEvent("maya:role", { detail: value }));
    else if (action === "summary-lang") document.dispatchEvent(new CustomEvent("maya:summary-lang", { detail: value }));
    return true;
  }
  function stopActions(why) {
    const n = actions ? actions.cancel() : 0;
    if (n) logAct("Pet stopped: " + why);
    return n;
  }
  // for a control MAYA never presses: show where it is; the user presses it
  function showWhere(el) {
    if (!el || !pet) return;
    if (helpOn) { act("scroll", el); act("highlight", el); } else el.scrollIntoView({ block: "center" });
    setTimeout(() => pet.moveTo(el), 500);
    const x = line("pressYourself");
    say(x.text, x.spoken, [], { avoid: el });
  }

  // ---------- after a scan: a guided tour of the real result ----------
  function offerTour() {
    const s = steps[0];
    if (!s || !pet) return;
    pet.setEmotion(s.emotion);
    say(s.line, s.spoken_hi, [chip("tourStart", startTour), chip("skip", endTour)], { sticky: true });
  }
  function tourInput() {
    const findings = Array.from(document.querySelectorAll("#findings-list .finding")).map((card) => {
      const txt = (sel) => { const n = card.querySelector(sel); return n ? n.textContent.trim() : ""; };
      const sum = card.querySelector(".finding-sum");
      return { target: card, id: sum ? sum.getAttribute("data-finding-id") : "", severity: txt(".finding-sum .sev").toLowerCase(),
        title: txt(".finding-sum .f-title"), location: txt(".finding-sum .f-where") };
    }).filter((f) => f.severity);
    const line = $("score-line");
    return { findings: findings, scoreLine: line ? line.textContent.trim() : "", lines: tourLines || {} };
  }
  function startTour(from) {
    const b = brain();
    if (!b || !pet) return;
    tour = { stops: b.tourStops(tourInput()), i: typeof from === "number" ? from : 0 };
    showStop();
  }
  function showStop() {
    if (!tour) return;
    const stop = tour.stops[tour.i];
    if (!stop) return endTour();
    const el = typeof stop.target === "string" ? $(stop.target) : stop.target;
    if (!el || el.closest("[hidden]")) { tour.i += 1; return showStop(); }
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView({ block: "center", behavior: reduced ? "auto" : "smooth" });
    const last = tour.i === tour.stops.length - 1;
    const chips = last ? [chip("done", endTour)] : [chip("nextStop", () => { tour.i += 1; showStop(); }), chip("skip", endTour)];
    if (helpOn && actions) {
      const prev = tour.i > 0 ? tour.stops[tour.i - 1] : null;
      const prevEl = prev && typeof prev.target !== "string" ? prev.target : null;
      if (prevEl) actions.add(() => act("collapse", prevEl), 0);
      if (el.matches("details.finding")) actions.add(() => act("expand", el), reduced ? 0 : 400);
    }
    setTimeout(() => {
      if (!tour) return;
      pet.setEmotion([{ expression: stop.expression, pose: "pointing", ms: null }]);
      pet.moveTo(el, Infinity);
      say(stop.text, null, chips, { avoid: el, sticky: true });
    }, reduced ? 0 : 650);
  }
  function endTour() {
    tour = null;
    if (!pet) return;
    pet.hideBubble();
    pet.goHome(false);
    if (steps[0]) pet.setEmotion(steps[0].emotion);
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
  function audienceChips() {
    return ["shop", "company", "clinic", "developer", "other"].map((a) => chip("aud-" + a, () => setAudience(a)));
  }
  function askAudience() {
    const a = line("askAudience");
    if (pet) pet.setEmotion([{ expression: "curious", pose: "idle", ms: null }]);
    say(a.text, null, audienceChips(), { sticky: true });
  }
  function setAudience(a) {
    if (["shop", "company", "clinic", "developer", "other"].indexOf(a) < 0) return;
    prefs.audience = a;
    save("maya-guide-audience", a);
    syncSettings();
    sticky = null;
    if (pet) pet.setEmotion([{ expression: "happy", pose: "idle", ms: null }]);
    chosenLevelJustNow = true;
    refresh();
  }
  function setLevel(level) {
    if (["new", "some", "technical"].indexOf(level) < 0) return;
    prefs.level = level;
    save("maya-guide-level", level);
    const role = { new: "owner", some: "developer", technical: "technical" }[level];
    save("maya-role", role);
    document.dispatchEvent(new CustomEvent("maya:role", { detail: role }));  // the findings toggle follows the level
    syncSettings();
    sticky = null;
    if (pet) pet.setEmotion([{ expression: "happy", pose: "idle", ms: null }]);
    chosenLevelJustNow = true;
    refresh();  // the guide continues at once, in the chosen level's words
  }

  // ---------- voice: the browser's own text-to-speech ----------
  function setVoice(v) {
    unlock();
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
  function unlock() {
    if (gesture) return;
    gesture = true;
    if (core.speechSupported(window)) {
      const synth = window.speechSynthesis;
      synth.cancel();
      const silent = new window.SpeechSynthesisUtterance(" ");
      silent.volume = 0;
      synth.speak(silent);  // unlocks audio inside the gesture
    }
    const p = pendingSpeech;
    pendingSpeech = null;
    if (pet) pet.bubble.querySelectorAll(".pet-bubble-hint").forEach((n) => n.remove());
    if (p && prefs.voice === "on") speak(p.text, p.spokenHi, p.summary);
  }
  function speak(text, spokenHi, summary, force) {
    if (!core.speechSupported(window) || (!force && prefs.voice !== "on")) return;
    if (!gesture) { pendingSpeech = { text: text, spokenHi: spokenHi, summary: summary }; briefTalk(text, true); return; }
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
        hoverSpeaking = false;
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
  function briefTalk(text, whole) {
    if (!pet || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    pet.talkStart();
    let open = false;
    const until = Date.now() + (whole ? Math.min(9000, core.estimateMs(text, prefs.rate)) : Math.min(2400, core.estimateMs(text, 1.4)));
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
    set("pet-audience", "value", prefs.audience || "other");
    set("pet-rate", "value", String(prefs.rate));
    set("pet-pitch", "value", String(prefs.pitch));
    set("pet-speak-steps", "checked", prefs.speakSteps);
    set("pet-speak-answers", "checked", prefs.speakAnswers);
    set("pet-speak-hover", "checked", prefs.speakHover);
    set("pet-calm", "checked", prefs.calm);
    set("pet-mic", "checked", load("maya-pet-mic", "0") === "1");
    set("pet-help", "checked", helpOn);
  }
  function wireSettings() {
    if (!$("pet-view")) return;
    const speech = core.speechSupported(window);
    const on = (id, ev, fn) => { const n = $(id); if (n) n.addEventListener(ev, fn); };
    const test = () => { unlock(); const t = line("voiceTest"); speak(t.text, t.spoken, false, true); };
    $("pet-voice-note").textContent = VOICE_NOTE;
    on("pet-level", "change", () => setLevel($("pet-level").value));
    on("pet-audience", "change", () => setAudience($("pet-audience").value));
    on("pet-voice", "change", () => { prefs.voiceURI = $("pet-voice").value; save("maya-voice-uri", prefs.voiceURI); fillVoicePicker(); });
    on("pet-voice-preview", "click", test);
    on("pet-voice-test", "click", test);
    on("pet-voice-stop", "click", stopSpeech);
    on("pet-rate", "change", () => { prefs.rate = num($("pet-rate").value, 0.7, 1.5, 1); save("maya-voice-rate", String(prefs.rate)); });
    on("pet-pitch", "change", () => { prefs.pitch = num($("pet-pitch").value, 0.6, 1.8, 1.2); save("maya-voice-pitch", String(prefs.pitch)); });
    on("pet-speak-steps", "change", () => {
      prefs.speakSteps = $("pet-speak-steps").checked;
      save("maya-speak-steps", prefs.speakSteps ? "1" : "0");
    });
    on("pet-speak-hover", "change", () => {
      prefs.speakHover = $("pet-speak-hover").checked;
      save("maya-speak-hover", prefs.speakHover ? "1" : "0");
    });
    on("pet-calm", "change", () => {
      prefs.calm = $("pet-calm").checked;
      save("maya-pet-calm", prefs.calm ? "1" : "0");
      if (pet && pet.setCalm) pet.setCalm(prefs.calm);
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
    on("pet-help", "change", () => {
      if ($("pet-help").checked) { $("pet-help").checked = false; $("pet-help-consent").hidden = false; return; }
      helpOn = false;
      save("maya-pet-help", "0");
      stopActions("you turned help off");
      logAct("Help turned off");
    });
    on("pet-help-allow", "click", () => {
      helpOn = true;
      save("maya-pet-help", "1");
      $("pet-help").checked = true;
      $("pet-help-consent").hidden = true;
      logAct("Help turned on by you");
    });
    on("pet-help-cancel", "click", () => { $("pet-help-consent").hidden = true; });
    on("pet-reset", "click", () => {
      for (const k of LEARNED_KEYS) forget(k);
      used = {};
      if (greetKey) forget(greetKey);
      $("pet-reset-message").textContent = "Done: the pet forgot what it learned on this device.";
    });
    for (const id of ["pet-voice", "pet-voice-preview", "pet-voice-test", "pet-voice-stop", "pet-rate", "pet-pitch",
      "pet-speak-steps", "pet-speak-hover", "pet-speak-answers"]) if ($(id)) $(id).disabled = !speech;
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
    if (type === "asset") { assetId = e.detail.id || null; currentAssetKind = e.detail.kind || null; suggestedHere = false; }
    if (type === "view") { suggestedHere = false; if (pet) pet.hop(); return; }
    if (type === "data-deleted") { for (const k of LEARNED_KEYS) forget(k); used = {}; if (greetKey) forget(greetKey); return; }
    if (type === "scan-finished") pendingTour = true;
    if (type === "error") {
      if (pet) pet.setEmotion(table.error);
      const x = line("error");
      say(x.text, x.spoken, [], {});
      return;
    }
    if (type === "answered") {
      if (pet) pet.setEmotion(table.answered);
      if (prefs.speakAnswers && prefs.voice === "on") speak(e.detail.text, null, true);
      else briefTalk(e.detail.text);
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
    if (!email || !window.crypto || !window.crypto.subtle) return "maya-greeted";  // no account yet (sign-in page)
    const hash = await window.crypto.subtle.digest("SHA-256", new TextEncoder().encode(email.toLowerCase()));
    return "maya-greeted-" + Array.from(new Uint8Array(hash)).slice(0, 6).map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  document.addEventListener("DOMContentLoaded", async () => {
    if (!document.body) return;
    const mobile = window.matchMedia("(max-width: 860px)").matches;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const asked = new URLSearchParams(window.location.search);
    if (PREVIEW) {  // screenshots: a clean device
      helpOn = asked.get("help") === "on";
      prefs.level = asked.get("level") || "";
      prefs.audience = asked.get("audience") || (prefs.level ? "other" : "");
      prefs.voice = asked.get("voice") === "off" ? "off" : "on";
      prefs.petHidden = false;
    }
    if (window.MayaPet) {
      pet = window.MayaPet.live({ mobile: mobile, reduced: reduced, calm: prefs.calm,
        onMute: () => setVoice(prefs.voice === "on" ? "off" : "on"),
        onHide: () => setPetHidden(true),
        onOpen: () => (steps.length ? showStep() : refresh()),
        homeAnchor: () => { const c = $("composer"); return mobile && c ? c.getBoundingClientRect() : null; },
        getMode: currentMode });
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
    // browsers allow speech only after a user gesture: the first pointerdown, key press or touch anywhere unlocks it
    for (const ev of ["pointerdown", "keydown", "touchstart"]) document.addEventListener(ev, unlock, { once: true, capture: true });
    if (window.MayaBrain) actions = window.MayaBrain.ActionQueue(window.setTimeout.bind(window), window.clearTimeout.bind(window));
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      stopSpeech();
      stopActions("Esc");
      if (tour) endTour(); else if (pet) pet.hideBubble();
    });
    // any manual click outside the pet stops whatever the pet was about to do
    document.addEventListener("pointerdown", (e) => {
      if (!(e.target.closest && e.target.closest(".pet-layer"))) stopActions("you clicked");
    }, true);
    wireLearning();
    wireHover();  // with reduced motion the pet explains from home without walking (moveTo does not roam)
    setInterval(maybeSuggest, 5000);
    setPetHidden(prefs.petHidden);
    greetKey = await accountKey();
    if (prefs.petHidden) return;
    const shouldGreet = PREVIEW ? !!asked.get("greet") : load(greetKey, "0") !== "1";
    let after = null;
    try { after = JSON.parse(load("maya-pet-after", "null")); } catch (e) { after = null; }
    forget("maya-pet-after");
    if (shouldGreet) greet();
    else if (after && Date.now() - after.at < 15000 && prefs.level && prefs.audience) {
      say(after.text, null, [chip("next", () => refresh())], {});
      setTimeout(() => { if (!tour && !hoverShown) refresh(); }, 9000);
    } else refresh();
    if (PREVIEW && asked.get("talk") && pet) setTimeout(() => { pet.talkStart(); pet.mouth(true); }, 3000);  // speaking frame
    if (PREVIEW && asked.get("antic") && pet) setTimeout(() => { pet.hideBubble(); pet.talkStop(); pet.antic(asked.get("antic")); }, 3400);  // an idle antic frame
    if (PREVIEW && asked.get("help") === "consent" && $("pet-help-consent")) $("pet-help-consent").hidden = false;
    if (PREVIEW && asked.get("tour")) setTimeout(() => startTour(Number(asked.get("tour")) || 0), 1200);  // a tour stop
    if (PREVIEW && asked.get("hover")) setTimeout(() => {  // an id or a selector; scrolled into view first, like a real pointer
      const h = asked.get("hover");
      let n = $(h);
      try { n = n || document.querySelector(h); } catch (e) { n = null; }
      if (n && pet) { n.scrollIntoView({ block: "center" }); pet.hideBubble(); explain(n); }
    }, 2000);
  });
  window.MayaGuide = { listen: listen };
})();
