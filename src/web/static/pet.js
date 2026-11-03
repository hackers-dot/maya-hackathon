"use strict";
// MAYA, the on-screen pet: the project's own character (white cat-ear helmet with neon green trim, black visor
// face screen, cream chin, headset cups, flowing ribbons, green suit with a white V collar and a shield "M"
// emblem, white boots, glowing ground ring), drawn in layers with createElementNS. No raster images, no data:
// URLs, no markup strings. The face (eyes + mouth + effect) is swapped per expression; poses swap limb and
// prop layers. `core` holds the pure tables and helpers; it is unit-tested in Node (tests/test_pet.py).
(function () {
  // ---------- tables (pure data) ----------
  // eyes: [left, right] shapes; mouth: a mouth shape; fx: an optional effect near the head
  const EXPRESSIONS = {
    idle:       { eyes: ["arcUp", "arcUp"], mouth: "smileSmall", fx: null },
    happy:      { eyes: ["arcUp", "arcUp"], mouth: "openSmile", fx: null },
    excited:    { eyes: ["sparkle", "sparkle"], mouth: "openSmile", fx: "sparkles" },
    laughing:   { eyes: ["chevL", "chevR"], mouth: "bigOpen", fx: null },
    smiling:    { eyes: ["arcUp", "arcUp"], mouth: "smile", fx: null },
    wink:       { eyes: ["arcUp", "chevR"], mouth: "openSmile", fx: null },
    playful:    { eyes: ["chevL", "chevR"], mouth: "tongue", fx: null },
    sad:        { eyes: ["droopL", "droopR"], mouth: "frown", fx: "tear" },
    crying:     { eyes: ["cryL", "cryR"], mouth: "frownWavy", fx: null },
    angry:      { eyes: ["angryL", "angryR"], mouth: "frownSmall", fx: "vein" },
    surprised:  { eyes: ["ring", "ring"], mouth: "o", fx: "exclaim2" },
    shocked:    { eyes: ["ringBig", "ringBig"], mouth: "bigO", fx: "shock" },
    confused:   { eyes: ["lineTilt", "droopR"], mouth: "wavy", fx: "question2" },
    thinking:   { eyes: ["line", "arcUp"], mouth: "flat", fx: "question" },
    curious:    { eyes: ["ring", "ring"], mouth: "o", fx: "question" },
    sleepy:     { eyes: ["closed", "closed"], mouth: "yawn", fx: "zz" },
    tired:      { eyes: ["half", "half"], mouth: "flat", fx: null },
    nervous:    { eyes: ["nervousL", "nervousR"], mouth: "wobbly", fx: "sweat" },
    proud:      { eyes: ["smugL", "smugR"], mouth: "smirk", fx: "sparkle1" },
    determined: { eyes: ["determinedL", "determinedR"], mouth: "flat", fx: null },
    love:       { eyes: ["heart", "heart"], mouth: "openSmile", fx: "hearts" },
    alert:      { eyes: ["dot", "reticle"], mouth: "o", fx: "exclaim" },
    analyzing:  { eyes: ["lineDot", "reticle"], mouth: "flat", fx: null },
  };
  const POSES = ["idle", "waving", "scanning", "protecting", "analyzing", "pointing", "celebrating", "walking"];
  // real page events (app.js / guide.js) -> a short, fixed emotion sequence; the last entry holds
  const EVENT_EMOTION = {
    "scan-started": [{ expression: "analyzing", pose: "scanning", ms: null }],
    "applying": [{ expression: "determined", pose: "idle", ms: null }],
    "error": [{ expression: "sad", pose: "idle", ms: null }],
    "no-model": [{ expression: "confused", pose: "idle", ms: null }],
    "waiting-model": [{ expression: "thinking", pose: "analyzing", ms: null }],
    "answered": [{ expression: "happy", pose: "idle", ms: null }],
    "greet": [{ expression: "happy", pose: "waving", ms: 1800 }, { expression: "smiling", pose: "idle", ms: null }],
  };

  // ---------- pure helpers ----------
  // Sentences of at most `max` characters (Chrome stops a single utterance after about 15 s).
  function splitSentences(text, max) {
    const limit = max || 160;
    // a dot inside a word (cloudflare-dns.com, issues.md, /.env) is not the end of a sentence
    const safe = String(text || "").replace(/\s+/g, " ").trim().replace(/\.(?=\S)/g, "\u2024");
    const parts = (safe.match(/[^.!?]+[.!?]*/g) || []).map((x) => x.replace(/\u2024/g, "."));
    const out = [];
    for (let p of parts) {
      p = p.trim();
      while (p.length > limit) {
        let cut = p.lastIndexOf(" ", limit);
        if (cut < limit / 2) cut = limit;
        out.push(p.slice(0, cut).trim());
        p = p.slice(cut).trim();
      }
      if (p) out.push(p);
    }
    return out;
  }
  function clampPos(pos, vw, vh, w, h) {
    if (!pos || typeof pos.x !== "number" || typeof pos.y !== "number") return null;  // null must not become (0, 0)
    const x = pos.x, y = pos.y;
    if (!isFinite(x) || !isFinite(y)) return null;
    return { x: Math.min(Math.max(0, x), Math.max(0, vw - w)), y: Math.min(Math.max(0, y), Math.max(0, vh - h)) };
  }
  // A saved position from a storage-like object; any storage error or bad value means "no saved position".
  function readPos(store, key) {
    let raw = null;
    try { raw = store ? store.getItem(key) : null; } catch (e) { raw = null; }
    if (!raw) return null;
    try { const v = JSON.parse(raw); return typeof v === "object" && v ? { x: v.x, y: v.y } : null; } catch (e) { return null; }
  }
  function writePos(store, key, pos) {
    try { if (store) store.setItem(key, JSON.stringify({ x: Math.round(pos.x), y: Math.round(pos.y) })); return true; } catch (e) { return false; }
  }
  // calm (Pet settings > Calm mode): no walking and no idle antics; the bubble, speech and blinking stay
  function behavior(reducedMotion, mobile, calm) {
    return { roam: !reducedMotion && !mobile && !calm, loops: !reducedMotion && !calm, drag: !mobile };
  }
  function overlaps(a, b) {
    return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
  }
  // Where the pet can stand next to `target` without covering any other control: right, left, above, below.
  // Returns {x, y, side} or null (then it stays home). All rects are {x, y, w, h} in viewport pixels.
  function placeBeside(target, size, viewport, obstacles) {
    const gap = 8;
    const c = [
      { side: "right", x: target.x + target.w + gap, y: target.y + target.h / 2 - size.h / 2 },
      { side: "left", x: target.x - size.w - gap, y: target.y + target.h / 2 - size.h / 2 },
      { side: "above", x: target.x + target.w / 2 - size.w / 2, y: target.y - size.h - gap },
      { side: "below", x: target.x + target.w / 2 - size.w / 2, y: target.y + target.h + gap },
    ];
    for (const p of c) {
      const box = { x: p.x, y: p.y, w: size.w, h: size.h };
      if (box.x < 0 || box.y < 0 || box.x + box.w > viewport.w || box.y + box.h > viewport.h) continue;
      if (overlaps(box, target)) continue;
      if ((obstacles || []).some((o) => overlaps(box, o))) continue;
      return { x: Math.round(p.x), y: Math.round(p.y), side: p.side };
    }
    return null;
  }

  // Where the small speech bubble goes next to the pet: left, right, above or below (or above/below both the pet
  // and the target when they sit side by side), clamped inside the viewport. It never covers the pet or the
  // `must` rects (the control the pet is talking about); among those spots it picks the one that covers the
  // fewest `prefer` rects (other controls nearby). `covers` is true only if every spot covers a `must` rect.
  function placeBubble(pet, size, viewport, must, prefer) {
    const m = 8, gap = 6;
    const clamp = (x, y) => ({ x: Math.round(Math.min(Math.max(m, x), Math.max(m, viewport.w - size.w - m))),
      y: Math.round(Math.min(Math.max(m, y), Math.max(m, viewport.h - size.h - m))) });
    const hard = (must || []).filter(Boolean);
    const soft = (prefer || []).filter(Boolean);
    const top = Math.min.apply(null, [pet.y].concat(hard.map((a) => a.y)));
    const bottom = Math.max.apply(null, [pet.y + pet.h].concat(hard.map((a) => a.y + a.h)));
    const tries = [["left", pet.x - size.w - gap, pet.y], ["right", pet.x + pet.w + gap, pet.y],
      ["above", pet.x + pet.w - size.w, pet.y - size.h - gap], ["below", pet.x + pet.w - size.w, pet.y + pet.h + gap],
      ["above", pet.x + pet.w - size.w, top - size.h - gap], ["below", pet.x + pet.w - size.w, bottom + gap]];
    let best = null;
    let first = null;
    for (const [side, x, y] of tries) {
      const p = clamp(x, y);
      const box = { x: p.x, y: p.y, w: size.w, h: size.h };
      if (!first) first = { x: p.x, y: p.y, side: side, covers: true };
      if (overlaps(box, pet) || hard.some((a) => overlaps(box, a))) continue;
      const cost = soft.filter((a) => overlaps(box, a)).length;
      if (!best || cost < best.cost) best = { x: p.x, y: p.y, side: side, covers: false, cost: cost };
      if (cost === 0) break;
    }
    return best || first;
  }

  const core = { EXPRESSIONS, POSES, EVENT_EMOTION, splitSentences, clampPos, readPos, writePos, behavior,
    placeBeside, placeBubble, overlaps, EYE_SHAPES: null, MOUTHS: null, EFFECTS: null };

  // ---------- drawing primitives (shape name -> list of [tag, attrs]) ----------
  const G = "#00FF66", INK = "#0B1F16", MOUTH = "#5E1A1F", TONGUE = "#F2878F", TEAR = "#7CF5D0", PINK = "#FF8FB6";
  const S = (d, w) => ["path", { d: d, fill: "none", stroke: G, "stroke-width": w || 4.2, "stroke-linecap": "round", "stroke-linejoin": "round" }];
  const F = (d, fill) => ["path", { d: d, fill: fill || G }];
  const EYES = {
    arcUp: [S("M-9 3 Q0 -9 9 3")],
    droopL: [S("M-9 -2 Q-1 6 8 3")],
    droopR: [S("M9 -2 Q1 6 -8 3")],
    cryL: [S("M-9 0 Q0 -6 9 0"), ["path", { d: "M-4 3 Q-6 14 -4 22", fill: "none", stroke: TEAR, "stroke-width": 4, "stroke-linecap": "round" }]],
    cryR: [S("M-9 0 Q0 -6 9 0"), ["path", { d: "M4 3 Q6 14 4 22", fill: "none", stroke: TEAR, "stroke-width": 4, "stroke-linecap": "round" }]],
    ring: [["circle", { r: 7, fill: "none", stroke: G, "stroke-width": 3.6 }]],
    ringBig: [["circle", { r: 9, fill: "none", stroke: G, "stroke-width": 3.6 }]],
    dot: [["ellipse", { rx: 4.2, ry: 6, fill: G }]],
    line: [S("M-8 0 H8")],
    lineTilt: [S("M-8 1 L8 -2")],
    lineDot: [S("M-9 -4 H9", 3), ["circle", { cy: 2, r: 3, fill: G }]],
    closed: [S("M-8 0 Q0 3 8 0")],
    half: [F("M-8 -1 H8 Q8 6 0 6 Q-8 6 -8 -1 Z")],
    chevL: [S("M-6 -6 L5 0 L-6 6")],
    chevR: [S("M6 -6 L-5 0 L6 6")],
    sparkle: [F("M0 -10 Q1.6 -1.6 10 0 Q1.6 1.6 0 10 Q-1.6 1.6 -10 0 Q-1.6 -1.6 0 -10 Z")],
    heart: [F("M0 8 C-12 0 -9 -9 -3.5 -8 C-1.5 -8 0 -6 0 -4.5 C0 -6 1.5 -8 3.5 -8 C9 -9 12 0 0 8 Z", PINK)],
    reticle: [["circle", { r: 8, fill: "none", stroke: G, "stroke-width": 2.6 }], ["circle", { r: 2.6, fill: G }],
      S("M-13 0 H-9 M9 0 H13 M0 -13 V-9 M0 9 V13", 2.4)],
    angryL: [F("M-10 -5 L9 1 L7 5 L-8 3 Z")],
    angryR: [F("M10 -5 L-9 1 L-7 5 L8 3 Z")],
    determinedL: [F("M-10 -2 L9 1 L8 5 L-8 5 Z")],
    determinedR: [F("M10 -2 L-9 1 L-8 5 L8 5 Z")],
    smugL: [S("M-9 3 Q-1 -5 9 0")],
    smugR: [S("M9 3 Q1 -5 -9 0")],
    nervousL: [["ellipse", { rx: 5.5, ry: 7.5, fill: "none", stroke: G, "stroke-width": 3 }], ["circle", { cx: 1.5, cy: 2, r: 2.4, fill: G }]],
    nervousR: [["ellipse", { rx: 5.5, ry: 7.5, fill: "none", stroke: G, "stroke-width": 3 }], ["circle", { cx: -1.5, cy: 2, r: 2.4, fill: G }]],
  };
  const MS = (d, w) => ["path", { d: d, fill: "none", stroke: MOUTH, "stroke-width": w || 1.8, "stroke-linecap": "round", "stroke-linejoin": "round" }];
  const MOUTHS = {
    smileSmall: [MS("M-3.5 0 Q0 3 3.5 0")],
    smile: [MS("M-5.5 -0.5 Q0 4.5 5.5 -0.5")],
    openSmile: [F("M-6.5 -2 Q0 -1 6.5 -2 Q5.5 6.5 0 6.5 Q-5.5 6.5 -6.5 -2 Z", MOUTH), ["ellipse", { cy: 4.2, rx: 3.4, ry: 1.8, fill: TONGUE }]],
    bigOpen: [F("M-8 -3 Q0 -2 8 -3 Q7 9 0 9 Q-7 9 -8 -3 Z", MOUTH), ["ellipse", { cy: 5.8, rx: 4.4, ry: 2.4, fill: TONGUE }]],
    tongue: [MS("M-5.5 -1 Q0 3 5.5 -1"), F("M-2.5 1 Q0 8 3.5 1 Z", TONGUE)],
    frown: [MS("M-5 2.5 Q0 -2 5 2.5")],
    frownSmall: [MS("M-3.5 1.5 Q0 -1 3.5 1.5")],
    frownWavy: [MS("M-6 2 q2 -2.4 4 0 q2 2.4 4 0 q2 -2.4 4 0")],
    wavy: [MS("M-5 1 q2.5 -2.4 5 0 q2.5 2.4 5 0")],
    wobbly: [MS("M-5 1 q1.7 -2 3.3 0 q1.7 2 3.3 0 q1.7 -2 3.4 0")],
    flat: [MS("M-4 1 H4")],
    smirk: [MS("M-4.5 1.5 Q1 4 5 -1")],
    o: [["ellipse", { rx: 2.6, ry: 3.2, fill: MOUTH }]],
    bigO: [["ellipse", { rx: 4.4, ry: 5.4, fill: MOUTH }], ["ellipse", { cy: 3, rx: 2.4, ry: 1.4, fill: TONGUE }]],
    yawn: [["ellipse", { rx: 4.2, ry: 4.8, fill: MOUTH }], ["ellipse", { cy: 2.6, rx: 2.6, ry: 1.5, fill: TONGUE }]],
  };
  const star = (x, y, r) => F(`M${x} ${y - r} Q${x + r * 0.16} ${y - r * 0.16} ${x + r} ${y} Q${x + r * 0.16} ${y + r * 0.16} ${x} ${y + r} Q${x - r * 0.16} ${y + r * 0.16} ${x - r} ${y} Q${x - r * 0.16} ${y - r * 0.16} ${x} ${y - r} Z`);
  const qmark = (x, y, s) => [S(`M${x - 4 * s} ${y - 6 * s} Q${x - 4 * s} ${y - 12 * s} ${x + 2 * s} ${y - 12 * s} Q${x + 8 * s} ${y - 12 * s} ${x + 8 * s} ${y - 6 * s} Q${x + 8 * s} ${y - 2 * s} ${x + 2 * s} ${y} V${y + 3 * s}`, 3 * s),
    ["circle", { cx: x + 2 * s, cy: y + 8 * s, r: 1.9 * s, fill: G }]];
  const emark = (x, y) => [S(`M${x} ${y - 12} V${y + 1}`, 3.6), ["circle", { cx: x, cy: y + 7, r: 2.2, fill: G }]];
  const heartAt = (x, y, s) => F(`M${x} ${y + 8 * s} C${x - 12 * s} ${y} ${x - 9 * s} ${y - 9 * s} ${x - 3.5 * s} ${y - 8 * s} C${x - 1.5 * s} ${y - 8 * s} ${x} ${y - 6 * s} ${x} ${y - 4.5 * s} C${x} ${y - 6 * s} ${x + 1.5 * s} ${y - 8 * s} ${x + 3.5 * s} ${y - 8 * s} C${x + 9 * s} ${y - 9 * s} ${x + 12 * s} ${y} ${x} ${y + 8 * s} Z`, PINK);
  // effects are drawn in head space (the visor is centred on x=100)
  const EFFECTS = {
    question: qmark(160, 26, 1),
    question2: qmark(154, 28, 0.8).concat(qmark(168, 20, 0.8)),
    exclaim: emark(162, 24),
    exclaim2: emark(156, 26).concat(emark(166, 22)),
    shock: [S("M150 22 L146 12 M160 18 V6 M170 22 L174 12", 3)],
    sparkles: [star(158, 20, 7), star(170, 34, 4.5), star(40, 22, 4)],
    sparkle1: [star(162, 24, 6.5)],
    hearts: [heartAt(160, 22, 0.8), heartAt(172, 38, 0.55), heartAt(36, 30, 0.6)],
    zz: [S("M150 26 H158 L150 34 H158", 2.6), S("M162 12 H172 L162 22 H172", 3)],
    sweat: [F("M156 46 Q163 56 156 60 Q149 56 156 46 Z", TEAR)],
    tear: [F("M78 99 Q82 106 78 109 Q74 106 78 99 Z", TEAR)],
    vein: [S("M150 22 Q154 22 154 18 M162 22 Q158 22 158 18 M150 30 Q154 30 154 34 M162 30 Q158 30 158 34", 2.6)],
  };
  core.EYE_SHAPES = Object.keys(EYES);
  core.MOUTHS = Object.keys(MOUTHS);
  core.EFFECTS = Object.keys(EFFECTS);

  if (typeof module === "object" && module.exports) { module.exports = core; return; }
  if (typeof document === "undefined") return;

  const SVG_NS = "http://www.w3.org/2000/svg";
  let uid = 0;
  function svg(tag, attrs, parent) {
    const n = document.createElementNS(SVG_NS, tag);
    for (const k of Object.keys(attrs || {})) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }
  function drawList(list, parent, filter) {
    for (const [tag, attrs] of list) {
      const n = svg(tag, attrs, parent);
      if (filter && (attrs.stroke === G || attrs.fill === G)) n.setAttribute("filter", filter);
    }
  }

  // ---------- the character (one SVG, layered) ----------
  function character(opts) {
    const id = "mp" + (++uid);
    const u = (name) => "url(#" + id + "-" + name + ")";
    const root = svg("svg", { viewBox: "0 0 200 250", class: "pet-svg", "aria-hidden": "true" });
    const defs = svg("defs", {}, root);
    const grad = (name, stops, kind, attrs) => {
      const g = svg(kind || "linearGradient", Object.assign({ id: id + "-" + name }, attrs || { x1: 0, y1: 0, x2: 0, y2: 1 }), defs);
      for (const [o, c, a] of stops) svg("stop", { offset: o, "stop-color": c, "stop-opacity": a === undefined ? 1 : a }, g);
    };
    grad("white", [[0, "#FFFFFF"], [0.6, "#EEF7F1"], [1, "#C9DDD0"]]);
    grad("suit", [[0, "#5BEA6E"], [0.45, "#22B83F"], [1, "#0B6B26"]]);
    grad("suitArm", [[0, "#7BF08A"], [1, "#1E9E3A"]]);
    grad("glass", [[0, "#7DFF8F"], [0.5, "#1FC447"], [1, "#0A5C22"]]);
    grad("dome", [[0, "#2BD660"], [0.22, "#0E6E30"], [0.6, "#073D1C"], [1, "#04281A"]], "radialGradient", { cx: 0.5, cy: 0.05, r: 0.95 });
    grad("visor", [[0, "#10291A"], [0.5, "#030D07"], [1, "#000000"]], "radialGradient", { cx: 0.45, cy: 0.3, r: 0.8 });
    grad("skin", [[0, "#FFE9D6"], [1, "#F2C9A6"]]);
    grad("ring", [[0, G, 0.45], [1, G, 0]], "radialGradient", { cx: 0.5, cy: 0.5, r: 0.5 });
    grad("ribbonL", [[0, "#FFFFFF"], [0.55, "#DFFFE8"], [1, "#6CFF98"]], "linearGradient", { x1: 1, y1: 0, x2: 0, y2: 1 });
    grad("ribbonR", [[0, "#FFFFFF"], [0.55, "#DFFFE8"], [1, "#6CFF98"]], "linearGradient", { x1: 0, y1: 0, x2: 1, y2: 1 });
    const glow = svg("filter", { id: id + "-glow", x: "-60%", y: "-60%", width: "220%", height: "220%" }, defs);
    svg("feGaussianBlur", { stdDeviation: 1.8, result: "b" }, glow);
    const merge = svg("feMerge", {}, glow);
    svg("feMergeNode", { in: "b" }, merge);
    svg("feMergeNode", { in: "SourceGraphic" }, merge);
    const soft = svg("filter", { id: id + "-soft", x: "-50%", y: "-50%", width: "200%", height: "200%" }, defs);
    svg("feGaussianBlur", { stdDeviation: 5 }, soft);
    const GLOW = u("glow");

    // ground ring
    const ring = svg("g", { class: "pet-ring" }, root);
    svg("ellipse", { cx: 100, cy: 234, rx: 66, ry: 13, fill: u("ring") }, ring);
    svg("ellipse", { cx: 100, cy: 234, rx: 56, ry: 9, fill: "none", stroke: G, "stroke-width": 3, filter: GLOW }, ring);

    const fig = svg("g", { class: "pet-figure" }, root);
    // walking trail (shown only while moving)
    svg("path", { class: "pet-trail", d: "M28 214 Q100 250 176 206", fill: "none", stroke: G, "stroke-width": 5, "stroke-linecap": "round", filter: GLOW, opacity: 0.8 }, fig);
    // ribbons (behind the body): flat white bands from under the headset cups, flowing out and curling up
    const flip = (d) => d.replace(/(\d+(\.\d+)?) (\d+(\.\d+)?)/g, (m, x, _a, y) => (200 - parseFloat(x)) + " " + y);
    const band = "M54 116 C52 134 36 140 22 143 C8 146 2 160 10 168 C17 174 27 168 24 159 " +
      "C25 164 20 166 17 163 C14 159 17 153 27 152 C45 149 65 140 65 120 Z";
    for (const side of [1, -1]) {
      const g = svg("g", { class: "pet-ribbon " + (side === 1 ? "pet-ribbon-l" : "pet-ribbon-r") }, fig);
      const d = side === 1 ? band : flip(band);
      svg("path", { d: d, fill: G, opacity: 0.22, filter: u("soft") }, g);
      svg("path", { d: d, fill: u(side === 1 ? "ribbonL" : "ribbonR"), stroke: "#B4FFB4", "stroke-width": 0.8, "stroke-opacity": 0.7 }, g);
    }
    // legs and boots
    const legs = svg("g", { class: "pet-legs" }, fig);
    svg("path", { d: "M83 188 H99 V210 H83 Z", fill: u("suit") }, legs);
    svg("path", { d: "M101 188 H117 V210 H101 Z", fill: u("suit") }, legs);
    svg("path", { d: "M82 204 H99 V224 Q99 229 94 229 H86 Q81 229 81 224 Z", fill: u("white") }, legs);
    svg("path", { d: "M101 204 H118 V224 Q118 229 114 229 H106 Q101 229 101 224 Z", fill: u("white") }, legs);
    // arms: small scalloped wings, rotated per pose around the shoulder
    const armL = svg("g", { class: "pet-arm pet-arm-l" }, fig);
    svg("path", { d: "M82 136 Q62 144 55 166 Q58 172 63 167 Q65 174 70 168 Q74 173 77 165 Q78 152 86 147 Z", fill: u("suitArm") }, armL);
    const armR = svg("g", { class: "pet-arm pet-arm-r" }, fig);
    svg("path", { d: "M118 136 Q138 144 145 166 Q142 172 137 167 Q135 174 130 168 Q126 173 123 165 Q122 152 114 147 Z", fill: u("suitArm") }, armR);
    // body suit
    svg("path", { class: "pet-body", d: "M88 124 Q100 121 112 124 Q126 134 129 160 Q132 186 117 196 L83 196 Q68 186 71 160 Q74 134 88 124 Z", fill: u("suit") }, fig);
    svg("path", { d: "M84 132 Q78 150 80 172", fill: "none", stroke: "#B4FFB4", "stroke-width": 3, "stroke-linecap": "round", opacity: 0.45 }, fig);
    // white V collar and the glowing shield "M" emblem
    svg("path", { d: "M76 128 L124 128 L100 180 Z M87 133 L113 133 L100 165 Z", "fill-rule": "evenodd", fill: u("white") }, fig);
    const emblem = svg("g", { class: "pet-emblem" }, fig);
    svg("path", { d: "M100 135 L111.5 139 V148 Q111.5 157.5 100 162.5 Q88.5 157.5 88.5 148 V139 Z", fill: INK, stroke: G, "stroke-width": 2, filter: GLOW }, emblem);
    svg("path", { d: "M94.5 155 V143 L100 149.5 L105.5 143 V155", fill: "none", stroke: "#B4FFB4", "stroke-width": 2.3, "stroke-linejoin": "round", "stroke-linecap": "round", filter: GLOW }, emblem);

    // props (one per pose; hidden unless the pose shows it)
    const props = svg("g", { class: "pet-props" }, fig);
    const holo = svg("g", { class: "pet-prop prop-holo" }, props);
    svg("rect", { x: 128, y: 128, width: 52, height: 38, rx: 5, fill: G, "fill-opacity": 0.14, stroke: G, "stroke-width": 2, filter: GLOW, transform: "skewY(-8)" }, holo);
    drawList([["circle", { cx: 154, cy: 128, r: 9, fill: "none", stroke: G, "stroke-width": 2 }], ["circle", { cx: 154, cy: 128, r: 3, fill: G }],
      S("M140 128 H145 M163 128 H168 M154 114 V119 M154 137 V142", 2)], holo, GLOW);
    const shield = svg("g", { class: "pet-prop prop-shield" }, props);
    svg("path", { d: "M152 118 L180 128 V150 Q180 172 152 186 Q124 172 124 150 V128 Z", fill: G, "fill-opacity": 0.22, stroke: G, "stroke-width": 3, filter: GLOW }, shield);
    svg("rect", { x: 144, y: 148, width: 16, height: 13, rx: 2.5, fill: G }, shield);
    svg("path", { d: "M147 148 V143 Q152 136 157 143 V148", fill: "none", stroke: G, "stroke-width": 2.6 }, shield);
    const charts = svg("g", { class: "pet-prop prop-charts" }, props);
    for (const [x, y] of [[8, 104], [150, 98]]) {
      svg("rect", { x: x, y: y, width: 42, height: 32, rx: 4, fill: G, "fill-opacity": 0.13, stroke: G, "stroke-width": 1.8, filter: GLOW }, charts);
      drawList([S(`M${x + 7} ${y + 26} V${y + 18} M${x + 15} ${y + 26} V${y + 12} M${x + 23} ${y + 26} V${y + 16} M${x + 31} ${y + 26} V${y + 8}`, 3),
        S(`M${x + 5} ${y + 10} L${x + 14} ${y + 14} L${x + 24} ${y + 6} L${x + 37} ${y + 9}`, 1.4)], charts, GLOW);
    }
    const party = svg("g", { class: "pet-prop prop-party" }, props);
    drawList([star(34, 70, 6), star(168, 64, 7), star(152, 100, 4), star(48, 104, 4)], party, GLOW);

    // head: white cat ears, a dark-green glass dome with neon trim, the big black visor, cream chin, white cups
    const head = svg("g", { class: "pet-head" }, fig);
    for (const side of [1, -1]) {
      const m = (d) => (side === 1 ? d : flip(d));
      const ear = svg("g", { class: side === 1 ? "pet-ear pet-ear-l" : "pet-ear pet-ear-r" }, head);
      svg("path", { d: m("M50 62 Q38 30 50 2 Q72 10 92 34 Z"), fill: u("white") }, ear);
      svg("path", { d: m("M57 52 Q50 30 55 13 Q70 21 83 35 Z"), fill: u("glass") }, ear);
      svg("path", { d: m("M56 48 Q51 30 55 17"), fill: "none", stroke: G, "stroke-width": 2, "stroke-linecap": "round", filter: GLOW }, ear);
    }
    svg("ellipse", { cx: 100, cy: 76, rx: 57, ry: 50, fill: u("white") }, head);
    svg("ellipse", { class: "pet-dome", cx: 100, cy: 75, rx: 53, ry: 47, fill: u("dome") }, head);
    svg("path", { d: "M50 64 Q56 30 100 27 Q144 30 150 64", fill: "none", stroke: G, "stroke-width": 3, "stroke-linecap": "round", filter: GLOW }, head);
    svg("path", { d: "M68 36 Q84 29 100 29", fill: "none", stroke: "#DFFFE8", "stroke-width": 2.6, "stroke-linecap": "round", opacity: 0.55 }, head);
    svg("ellipse", { class: "pet-chin", cx: 100, cy: 113, rx: 30, ry: 19, fill: u("skin") }, head);
    svg("path", { class: "pet-visor", d: "M50 80 Q50 41 100 40 Q150 41 150 80 Q150 106 124 111 Q111 113 100 109 Q89 113 76 111 Q50 106 50 80 Z", fill: u("visor"), stroke: "#0F3D22", "stroke-width": 1.5 }, head);
    svg("path", { d: "M61 66 Q68 51 88 47", fill: "none", stroke: "#FFFFFF", "stroke-width": 3.4, "stroke-linecap": "round", opacity: 0.2 }, head);
    svg("ellipse", { cx: 128, cy: 62, rx: 5, ry: 2.4, fill: "#FFFFFF", opacity: 0.12, transform: "rotate(25 128 62)" }, head);
    for (const side of [1, -1]) {
      const cx = side === 1 ? 50 : 150;
      const tilt = `rotate(${side * -14} ${cx} 98)`;
      const cup = svg("g", { class: "pet-cup" }, head);
      svg("ellipse", { cx: cx, cy: 100, rx: 16, ry: 30, fill: u("white"), transform: tilt }, cup);
      // neon stripe along the cup's inner-lower edge (no ring on the front, as on the character sheet)
      svg("path", { d: `M${cx + side * 9} 84 Q${cx + side * 16} 104 ${cx + side * 6} 124`, fill: "none", stroke: G, "stroke-width": 2.6, "stroke-linecap": "round", filter: GLOW, transform: tilt }, cup);
      svg("path", { d: `M${cx - side * 6} 76 Q${cx - side * 12} 90 ${cx - side * 10} 104`, fill: "none", stroke: "#FFFFFF", "stroke-width": 3, "stroke-linecap": "round", opacity: 0.8, transform: tilt }, cup);
    }
    // face screen: eyes, mouth, effect (rebuilt per expression) + the lip-sync mouth
    const face = svg("g", { class: "pet-face" }, head);
    const talkMouth = svg("g", { class: "pet-mouth-talk", transform: "translate(100 120)" }, head);
    svg("ellipse", { rx: 4.6, ry: 4.4, fill: MOUTH }, talkMouth);
    svg("ellipse", { cy: 2.4, rx: 2.8, ry: 1.5, fill: TONGUE }, talkMouth);

    const fxLayer = svg("g", { class: "pet-fx" }, root);
    return { root: root, face: face, fx: fxLayer, glow: GLOW, size: opts && opts.size };
  }

  function drawFace(ch, name) {
    const e = EXPRESSIONS[name] || EXPRESSIONS.idle;
    const g = svg("g", { class: "pet-face-set" });
    const eyes = svg("g", { class: "pet-eyes" }, g);
    [[80, e.eyes[0]], [120, e.eyes[1]]].forEach(([x, shape]) => {
      const eg = svg("g", { transform: `translate(${x} 79)` }, eyes);
      drawList(EYES[shape] || EYES.arcUp, eg, ch.glow);
    });
    const mouth = svg("g", { class: "pet-mouth-expr", transform: "translate(100 120)" }, g);
    drawList(MOUTHS[e.mouth] || MOUTHS.smileSmall, mouth, null);
    const fx = svg("g", { class: "pet-fx-set" });
    if (e.fx) drawList(EFFECTS[e.fx], fx, ch.glow);
    return { g: g, fx: fx };
  }

  // ---------- a pet instance: expression + pose with smooth swaps ----------
  function create(container, opts) {
    const o = opts || {};
    const ch = character(o);
    const el = document.createElement("div");
    el.className = "maya-pet-figure pose-idle";
    el.appendChild(ch.root);
    container.appendChild(el);
    let current = null;
    let currentSet = null;
    let blinkTimer = null;
    function setExpression(name) {
      if (!EXPRESSIONS[name] || name === current) return;
      current = name;
      el.setAttribute("data-expression", name);
      const next = drawFace(ch, name);
      next.g.setAttribute("class", "pet-face-set is-entering");
      next.fx.setAttribute("class", "pet-fx-set is-entering");
      ch.face.appendChild(next.g);
      ch.fx.appendChild(next.fx);
      const old = currentSet;
      currentSet = next;
      requestAnimationFrame(() => { next.g.setAttribute("class", "pet-face-set"); next.fx.setAttribute("class", "pet-fx-set"); });
      if (old) {
        old.g.setAttribute("class", "pet-face-set is-leaving");
        old.fx.setAttribute("class", "pet-fx-set is-leaving");
        setTimeout(() => { old.g.remove(); old.fx.remove(); }, o.instant ? 0 : 220);
      }
    }
    function setPose(pose) {
      if (POSES.indexOf(pose) < 0) return;
      for (const p of POSES) el.classList.toggle("pose-" + p, p === pose);
      el.setAttribute("data-pose", pose);
    }
    function blinkLoop() {
      clearTimeout(blinkTimer);
      blinkTimer = setTimeout(() => {
        const eyes = currentSet && currentSet.g.querySelector(".pet-eyes");
        if (eyes) { eyes.classList.add("is-blinking"); setTimeout(() => eyes.classList.remove("is-blinking"), 140); }
        blinkLoop();
      }, 3000 + Math.random() * 3000);  // every 3-6 s, at random
    }
    function stopLoops() { clearTimeout(blinkTimer); }
    setExpression(o.expression || "idle");
    setPose(o.pose || "idle");
    if (o.loops) blinkLoop();
    return { el: el, setExpression: setExpression, setPose: setPose, stopLoops: stopLoops,
      get expression() { return current; } };
  }

  // ---------- the living pet: one instance in a fixed layer above the page ----------
  const CONTROLS = "button, a[href], input, select, textarea, summary, [role=button]";
  const POS_KEY = "maya-pet-pos";
  function live(opts) {
    let calm = !!opts.calm;
    const normal = behavior(opts.reduced, opts.mobile);  // without calm mode
    const b = behavior(opts.reduced, opts.mobile, calm);
    let store = null;
    try { store = window.localStorage; } catch (e) { store = null; }
    const layer = document.createElement("div");
    layer.className = "pet-layer";
    const host = document.createElement("div");
    host.className = "maya-pet" + (opts.mobile ? " is-mobile" : "") + (b.roam ? "" : " is-docked");
    host.id = "maya-pet";
    host.tabIndex = 0;
    host.setAttribute("role", "button");
    host.setAttribute("aria-label", "MAYA. Double-click or press Enter to open the speech bubble.");
    layer.appendChild(host);
    document.body.appendChild(layer);
    const pet = create(host, { expression: "smiling", pose: "idle", loops: true });  // blinking stays under reduced motion
    // the pet's own surface: only mute/unmute and hide
    const tools = document.createElement("div");
    tools.className = "pet-tools";
    const tool = (cls, label, onClick) => {
      const t = document.createElement("button");
      t.type = "button";
      t.className = "pet-tool " + cls;
      t.setAttribute("aria-label", label);
      t.title = label;
      t.addEventListener("click", (e) => { e.stopPropagation(); onClick(); });
      tools.appendChild(t);
      return t;
    };
    const muteBtn = tool("pet-mute", "Mute or unmute MAYA", () => opts.onMute && opts.onMute());
    tool("pet-hide-btn", "Hide MAYA", () => opts.onHide && opts.onHide()).textContent = "\u2715";
    host.appendChild(tools);
    // the small speech bubble beside the pet; choices appear as chips on it
    const bubble = document.createElement("div");
    bubble.className = "pet-bubble";
    bubble.id = "pet-bubble";
    bubble.hidden = true;
    bubble.setAttribute("role", "status");
    bubble.setAttribute("aria-live", "polite");
    bubble.setAttribute("aria-label", "MAYA says");
    bubble.tabIndex = 0;  // reachable with Tab; its chips are buttons (Enter / Space work)
    const bubbleText = document.createElement("p");
    bubbleText.className = "pet-bubble-text";
    const chipRow = document.createElement("div");
    chipRow.className = "pet-chips";
    bubble.appendChild(bubbleText);
    bubble.appendChild(chipRow);
    layer.appendChild(bubble);
    let bubbleTimer = null;
    let avoidEl = null;
    let base = { expression: "smiling", pose: "idle" };
    let timers = [];
    let flashTimer = null;
    let returnTimer = null;
    let scrollTimer = null;
    let atHome = true;
    let lastActive = Date.now();
    let sleeping = false;
    let talking = false;
    let drag = null;
    const size = () => ({ w: host.offsetWidth || 120, h: host.offsetHeight || 150 });

    function place(x, y) {
      host.style.transform = "translate3d(" + Math.round(x) + "px," + Math.round(y) + "px,0)";
      host.dataset.x = String(Math.round(x));
      host.dataset.y = String(Math.round(y));
      positionBubble();
      setTimeout(positionBubble, b.roam ? 950 : 0);  // again once the walk has finished
    }
    function rectOf(n) { const r = n.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; }
    function positionBubble() {
      if (bubble.hidden) return;
      const s = size();
      const petBox = { x: parseFloat(host.dataset.x || "0"), y: parseFloat(host.dataset.y || "0"), w: s.w, h: s.h };
      const bsize = { w: bubble.offsetWidth, h: bubble.offsetHeight };
      const vp = { w: window.innerWidth, h: window.innerHeight };
      const must = avoidEl ? [rectOf(avoidEl)] : [];
      // first try to keep every nearby control free too; the target itself is never covered if any spot allows it
      const near = [];
      for (const n of document.querySelectorAll(CONTROLS)) {
        if (n.closest(".pet-layer") || n === avoidEl) continue;
        const r = n.getBoundingClientRect();
        if (r.width && r.height && Math.abs(r.left - petBox.x) < 700 && Math.abs(r.top - petBox.y) < 400) near.push({ x: r.left, y: r.top, w: r.width, h: r.height });
      }
      const spot = placeBubble(petBox, bsize, vp, must, near);
      bubble.style.left = spot.x + "px";  // left/top, not transform: the entry animation owns transform
      bubble.style.top = spot.y + "px";
      bubble.setAttribute("data-side", spot.side);
    }
    // say(text, chips, {ms, avoid, sticky}): chips are [{label, onClick}]; the bubble hides after reading time
    function say(text, chips, o) {
      const opt = o || {};
      clearTimeout(bubbleTimer);
      bubbleText.textContent = String(text || "");
      while (chipRow.firstChild) chipRow.removeChild(chipRow.firstChild);
      for (const c of chips || []) {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "pet-chip";
        chip.textContent = c.label;
        chip.addEventListener("click", (e) => { e.stopPropagation(); c.onClick(); });
        chipRow.appendChild(chip);
      }
      chipRow.hidden = !(chips || []).length;
      bubble.querySelectorAll(".pet-bubble-hint").forEach((n) => n.remove());
      if (opt.hint) {
        const h = document.createElement("p");
        h.className = "pet-bubble-hint";
        h.textContent = opt.hint;
        bubble.appendChild(h);
      }
      avoidEl = opt.avoid || null;
      bubble.hidden = false;
      positionBubble();
      const words = String(text || "").split(/\s+/).length;
      const ms = opt.ms || Math.min(16000, Math.max(3500, words * 320 + ((chips || []).length ? 5000 : 0)));
      if (!opt.sticky) bubbleTimer = setTimeout(hideBubble, ms);
    }
    function hideBubble() { clearTimeout(bubbleTimer); bubble.hidden = true; }
    function setMuted(muted) {
      muteBtn.textContent = muted ? "\u{1F507}" : "\u{1F50A}";
      muteBtn.setAttribute("aria-pressed", muted ? "false" : "true");
      muteBtn.setAttribute("aria-label", muted ? "Voice is off: turn it on" : "Voice is on: mute");
    }
    function homePos() {
      const s = size();
      const saved = b.drag ? clampPos(readPos(store, POS_KEY), window.innerWidth, window.innerHeight, s.w, s.h) : null;
      if (saved) return saved;
      const anchor = opts.homeAnchor ? opts.homeAnchor() : null;
      const x = window.innerWidth - s.w - (opts.mobile ? 10 : 22);
      const y = anchor ? anchor.top - s.h - 6 : window.innerHeight - s.h - 18;
      return { x: x, y: Math.max(8, y) };
    }
    function restore() {
      if (sleeping) return;
      pet.setExpression(base.expression);
      pet.setPose(atHome ? base.pose : "pointing");
    }
    function goHome(forget) {
      if (forget) { try { if (store) store.removeItem(POS_KEY); } catch (e) { /* nothing saved */ } }
      clearTimeout(returnTimer);
      const p = homePos();
      const moving = !atHome && b.roam;
      if (moving) pet.setPose("walking");
      host.classList.toggle("face-left", false);
      place(p.x, p.y);
      atHome = true;
      setTimeout(restore, moving ? 950 : 0);
    }
    // a fixed emotion sequence; the last step holds and becomes the base the pet returns to
    function setEmotion(seq) {
      timers.forEach(clearTimeout);
      timers = [];
      const steps = (seq || []).filter((x) => x && EXPRESSIONS[x.expression]);
      if (!steps.length) return;
      sleeping = false;
      let t = 0;
      steps.forEach((s, i) => {
        const apply = () => {
          if (i === steps.length - 1) base = { expression: s.expression, pose: s.pose || "idle" };
          pet.setExpression(s.expression);
          if (atHome || s.pose === "pointing") pet.setPose(s.pose || "idle");
        };
        if (i === 0) apply(); else timers.push(setTimeout(apply, t));
        t += s.ms || 0;
      });
    }
    function flash(expression, pose, ms) {
      if (talking && expression !== "surprised") return;
      clearTimeout(flashTimer);
      pet.setExpression(expression);
      if (pose) pet.setPose(pose);
      flashTimer = setTimeout(restore, ms);
    }
    function obstacles(target) {
      const out = [];
      for (const n of document.querySelectorAll(CONTROLS)) {
        if (n === target || target.contains(n) || n.closest(".pet-layer")) continue;
        const r = n.getBoundingClientRect();
        if (r.width && r.height) out.push({ x: r.left, y: r.top, w: r.width, h: r.height });
      }
      const bubble = document.getElementById("guide");
      if (bubble && !bubble.hidden) {
        const r = bubble.getBoundingClientRect();
        out.push({ x: r.left, y: r.top, w: r.width, h: r.height });
      }
      return out;
    }
    // walk to the element MAYA is talking about, point at it, then go home; never cover another control
    function moveTo(target, holdMs) {
      if (!target) return false;
      const r = target.getBoundingClientRect();
      const inView = r.bottom > 0 && r.top < window.innerHeight && r.width > 0;
      if (!b.roam || !inView) { pet.setPose("pointing"); return false; }
      const spot = placeBeside({ x: r.left, y: r.top, w: r.width, h: r.height }, size(),
        { w: window.innerWidth, h: window.innerHeight }, obstacles(target));
      if (!spot) { pet.setPose("pointing"); return false; }
      clearTimeout(returnTimer);
      host.classList.toggle("face-left", spot.x < parseFloat(host.dataset.x || "0"));
      pet.setPose("walking");
      place(spot.x, spot.y);
      atHome = false;
      timers.push(setTimeout(() => {
        host.classList.toggle("face-left", spot.side === "right");  // the arm points to the right by default
        pet.setPose("pointing");
      }, 950));
      if (holdMs !== Infinity) returnTimer = setTimeout(() => goHome(false), holdMs || 5200);
      return true;
    }
    function hop() {
      if (!b.loops) return;
      host.classList.remove("is-hopping");
      void host.offsetWidth;
      host.classList.add("is-hopping");
    }
    function talkStart() { talking = true; host.classList.add("is-talking"); }
    function talkStop() { talking = false; host.classList.remove("is-talking", "mouth-open"); }
    function mouth(open) { host.classList.toggle("mouth-open", !!open); }

    // drag (desktop), click = laugh and hop, double-click = speech bubble, hover = wink
    host.addEventListener("pointerdown", (e) => {
      if (!b.drag || e.button !== 0 || e.target.closest("button")) return;
      drag = { sx: e.clientX, sy: e.clientY, x: parseFloat(host.dataset.x || "0"), y: parseFloat(host.dataset.y || "0"), moved: false };
      host.setPointerCapture(e.pointerId);
    });
    host.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const dx = e.clientX - drag.sx, dy = e.clientY - drag.sy;
      if (!drag.moved && Math.abs(dx) + Math.abs(dy) < 5) return;
      if (!drag.moved) { drag.moved = true; host.classList.add("is-dragging"); clearTimeout(returnTimer); }
      const s = size();
      const p = clampPos({ x: drag.x + dx, y: drag.y + dy }, window.innerWidth, window.innerHeight, s.w, s.h);
      place(p.x, p.y);
    });
    host.addEventListener("pointerup", (e) => {
      if (e.target.closest("button")) return;
      const moved = drag && drag.moved;
      drag = null;
      host.classList.remove("is-dragging");
      if (moved) {
        writePos(store, POS_KEY, { x: parseFloat(host.dataset.x), y: parseFloat(host.dataset.y) });
        atHome = true;
      } else {
        flash("laughing", null, 1200);
        hop();
      }
    });
    host.addEventListener("dblclick", (e) => { if (!e.target.closest("button") && opts.onOpen) opts.onOpen(); });
    host.addEventListener("keydown", (e) => { if (e.key === "Enter" && opts.onOpen) opts.onOpen(); });
    host.addEventListener("pointerenter", () => { if (!drag) flash("wink", null, 900); });

    // idle life: ONE scheduler (pet-brain.js idleTick) ticked every second. Any input resets it; after 8 s the pet
    // looks around or waves, then one antic every 15-25 s, sleepy at 45 s, surprised when you come back.
    // WORK (scan running, approval pending, typing, pointer on a control in the last 6 s) pauses antics.
    // Reduced motion: no walking or dancing, only expression changes (wink, sleepy, wake).
    let lastInputAt = Date.now();
    let lastPointer = null;
    let idle = window.MayaBrain ? window.MayaBrain.idleStart() : null;
    const markInput = () => { lastInputAt = Date.now(); lastActive = lastInputAt; };
    for (const ev of ["pointermove", "pointerdown", "keydown", "touchstart", "wheel"]) {
      document.addEventListener(ev, markInput, { passive: true, capture: true });
    }
    document.addEventListener("pointermove", (e) => { lastPointer = { x: e.clientX, y: e.clientY }; }, { passive: true });
    function classFor(cls, ms) {
      host.classList.add(cls);
      setTimeout(() => host.classList.remove(cls), ms);
    }
    function antic(name) {
      if (name === "wink") flash("wink", null, 1100);
      else if (name === "wave") flash("happy", "waving", 1800);
      else if (name === "dance") flash("happy", "celebrating", 1600);
      else if (name === "stretch") { flash("tired", "celebrating", 1400); }
      else if (name === "peek") classFor("is-peeking", 1700);
      else if (name === "lookPointer") {  // turn towards the pointer and look at it
        const x0 = parseFloat(host.dataset.x || "0");
        if (lastPointer) host.classList.toggle("face-left", lastPointer.x < x0);
        flash("curious", null, 1600);
        classFor(lastPointer && lastPointer.x < x0 ? "look-l" : "look-r", 1500);
      }
      host.setAttribute("data-antic", name);
    }
    function idleLoop() {
      if (!idle || drag || calm) return;  // calm: no antics, no sleepy phase
      const work = !!(opts.getMode && opts.getMode() === "work");
      const [next, action] = window.MayaBrain.idleTick(idle, { now: Date.now(), lastInputAt: lastInputAt, work: work,
        reduced: !b.loops });
      if (next.phase !== idle.phase) host.setAttribute("data-idle", next.phase);
      idle = next;
      // a pet left beside a control comes home once things are quiet
      if (!atHome && !work && Date.now() - lastInputAt >= 8000) goHome(false);
      if (!action || talking) return;
      if (action === "sleepy") { sleeping = true; pet.setExpression("sleepy"); pet.setPose("idle"); }
      else if (action === "wake") { sleeping = false; flash("surprised", null, 900); }
      else if (atHome) antic(action);
    }
    if (window.MayaBrain) setInterval(idleLoop, 1000);
    if (b.loops) {
      document.addEventListener("scroll", () => {
        host.classList.add("is-scrolling");
        clearTimeout(scrollTimer);
        scrollTimer = setTimeout(() => host.classList.remove("is-scrolling"), 420);
      }, { capture: true, passive: true });
    }
    // the bubble changes height when its text changes: stay just above it while at home
    function rehome() { if (atHome && !drag) { const p = homePos(); place(p.x, p.y); } }
    window.addEventListener("resize", rehome);
    const anchorEl = opts.anchorElement ? opts.anchorElement() : null;
    if (anchorEl && typeof ResizeObserver === "function") new ResizeObserver(rehome).observe(anchorEl);
    const start = homePos();
    place(start.x, start.y);
    function setCalm(on) {
      calm = !!on;
      b.roam = normal.roam && !calm;
      b.loops = normal.loops && !calm;
      host.classList.toggle("is-docked", !b.roam);
      if (calm && !atHome) goHome(false);
    }
    return { el: host, layer: layer, behavior: b, setCalm: setCalm, setEmotion: setEmotion, flash: flash, moveTo: moveTo, goHome: goHome,
      hop: hop, talkStart: talkStart, talkStop: talkStop, mouth: mouth, rehome: rehome, say: say,
      hideBubble: hideBubble, setMuted: setMuted, bubble: bubble, antic: antic,
      expression() { return pet.expression; },
      isHome() { return atHome; },
      show(visible) { layer.hidden = !visible; if (!visible) hideBubble(); } };
  }

  window.MayaPet = { core: core, create: create, live: live };

  // sign-in hero: the character, standing on its ring (no roaming, no data)
  document.addEventListener("DOMContentLoaded", () => {
    const hero = document.getElementById("hero-pet");
    if (!hero) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const p = create(hero, { expression: "happy", pose: "idle", loops: !reduced });
    if (!reduced) setTimeout(() => { p.setPose("waving"); setTimeout(() => p.setPose("idle"), 1800); }, 900);
  });
})();
