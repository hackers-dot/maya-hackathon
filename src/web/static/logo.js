"use strict";
// The MAYA logo: the ORIGINAL artwork (static/logo/maya-logo*.png, same-origin image) inside an inline SVG built with
// createElementNS (art layer + eyelid layer), plus two eyelids drawn over the measured eyes so the eyes can blink. The art is never redrawn:
// the <image> fills the 1254 x 1254 viewBox of the original. Its black background is made transparent by one shared
// SVG filter (alpha from the colour channels; colours untouched), which works on every surface. A CSS screen blend
// does not: on the glass card and sidebar (isolated groups with translucent backgrounds) it left a dark square. Behaviour is visual only: one blink scheduler for every logo on the page (random 3-6 s,
// one in five a double blink), paused in a hidden tab, off under reduced motion; a soft glow while a real scan or
// re-test runs.
(function () {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const ART = 1254;  // the original is 1254 x 1254 px
  // eyelids: the convex hull of each eye's glow, measured on the original and enlarged 12 %; CSS adds an 18-unit
  // margin all round (a stroke in the lid colour), so the halo is covered on every side, not just at the tips.
  // Left eye centre (484, 711), slant +30 deg; right eye (772, 712), slant -30 deg (both slope down to the centre).
  const LID_L = "M401 637 L412 639 L453 654 L491 673 L520 690 L544 706 L561 720 L580 742 L586 750 L572 757 " +
    "L547 764 L506 765 L475 757 L454 747 L438 734 L426 720 L413 696 L408 678 L403 653 Z";
  const LID_R = "M672 749 L688 728 L706 711 L727 695 L745 685 L766 674 L797 659 L819 650 L840 642 L857 638 L849 678 L844 697 " +
    "L836 713 L827 726 L810 742 L799 750 L782 758 L763 764 L750 766 L725 766 L706 764 L684 757 Z";
  const EYES = [[484, 711], [772, 712]];

  function file(size) {  // the smallest copy that stays sharp at 2x pixel density
    return size <= 128 ? "/static/logo/maya-logo-512.png" : size <= 512 ? "/static/logo/maya-logo-1024.png"
      : "/static/logo/maya-logo.png";
  }
  function el(tag, attrs) {
    const n = document.createElementNS(SVG_NS, tag);
    for (const k of Object.keys(attrs)) n.setAttribute(k, attrs[k]);
    return n;
  }
  // shared defs, created once per page: black-to-transparent for the art, the soft-edge blur for the lids, the scan glow
  function ensureDefs() {
    if (document.getElementById("maya-logo-defs")) return;
    const svg = el("svg", { id: "maya-logo-defs", class: "maya-logo-defs", width: "0", height: "0", "aria-hidden": "true",
      focusable: "false" });
    const defs = el("defs", {});
    const blur = el("filter", { id: "maya-f-soft", x: "-20%", y: "-20%", width: "140%", height: "140%" });
    blur.appendChild(el("feGaussianBlur", { stdDeviation: "10" }));
    const glow = el("radialGradient", { id: "maya-g-scan", cx: "0.5", cy: "0.55", r: "0.5" });
    glow.appendChild(el("stop", { offset: "0", "stop-color": "#39FFB8", "stop-opacity": "0.55" }));
    glow.appendChild(el("stop", { offset: "1", "stop-color": "#00E887", "stop-opacity": "0" }));
    // alpha = 2R + 8G + 4B (clamped to 1): black becomes transparent, anything with green above ~32/255 stays opaque
    const alpha = el("filter", { id: "maya-f-alpha", x: "0", y: "0", width: "100%", height: "100%", "color-interpolation-filters": "sRGB" });
    alpha.appendChild(el("feColorMatrix", { type: "matrix", values: "1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  2 8 4 0 0" }));
    defs.appendChild(alpha);
    defs.appendChild(blur);
    defs.appendChild(glow);
    svg.appendChild(defs);
    document.body.appendChild(svg);
  }

  const instances = new Set();
  // Two stacked layers in one wrapper: the art, and on top of it the eyelids.
  function create(opts) {
    const size = Number((opts || {}).size) || 48;
    ensureDefs();
    const wrap = document.createElement("span");
    wrap.className = "maya-logo" + (scanning ? " is-scanning" : "");
    wrap.setAttribute("data-size", String(size));
    wrap.setAttribute("aria-hidden", "true");
    const layer = (cls) => el("svg", { viewBox: "0 0 " + ART + " " + ART, width: String(size), height: String(size), class: cls,
      "aria-hidden": "true", focusable: "false" });
    const art = layer("maya-logo-art");
    art.appendChild(el("circle", { class: "maya-scan-glow", cx: "627", cy: "650", r: "600", fill: "url(#maya-g-scan)" }));
    art.appendChild(el("image", { href: file(size), x: "0", y: "0", width: String(ART), height: String(ART),
      preserveAspectRatio: "xMidYMid meet", filter: "url(#maya-f-alpha)" }));
    const top = layer("maya-logo-lids");
    const lift = el("g", { class: "maya-eye-lift", filter: "url(#maya-f-soft)" });  // a soft brighten on pointer enter
    for (const [cx, cy] of EYES) lift.appendChild(el("ellipse", { cx: String(cx), cy: String(cy), rx: "70", ry: "38", fill: "#FFFFFF" }));
    top.appendChild(lift);
    const lids = el("g", { class: "maya-lids", filter: "url(#maya-f-soft)" });
    lids.appendChild(el("path", { class: "maya-lid", "data-part": "lidL", d: LID_L }));
    lids.appendChild(el("path", { class: "maya-lid", "data-part": "lidR", d: LID_R }));
    top.appendChild(lids);
    wrap.appendChild(art);
    wrap.appendChild(top);
    wrap.addEventListener("pointerenter", () => brighten(wrap));
    instances.add(wrap);
    return wrap;  // the caller puts it on the page; fillSlots() then starts the one blink scheduler
  }
  // every <span class="maya-logo-slot" data-size="44"> on the page gets a logo
  function fillSlots(root) {
    for (const slot of (root || document).querySelectorAll(".maya-logo-slot:not(.is-filled)")) {
      slot.appendChild(create({ size: slot.getAttribute("data-size") }));
      slot.classList.add("is-filled");
    }
    if (!timer) schedule();  // only after the logos are on the page (detached ones are dropped)
  }

  // ---------- the blink: one scheduler for every logo on the page ----------
  let timer = null;
  let rnd = Math.random;
  let clock = { set: (fn, ms) => setTimeout(fn, ms), clear: (t) => clearTimeout(t) };
  const BLINK = { close: 90, open: 140, gap: 110, min: 3000, max: 6000, doubleChance: 0.2 };
  function reduced() {
    return typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)").matches : false;
  }
  function hidden() { return typeof document !== "undefined" && document.hidden; }
  function live() {
    for (const s of instances) if (!s.isConnected) instances.delete(s);
    return instances.size > 0;
  }
  function setClosed(closed) { for (const s of instances) s.classList.toggle("is-blink", closed); }
  function blinkOnce(then) {
    setClosed(true);
    clock.set(() => { setClosed(false); clock.set(then, BLINK.open); }, BLINK.close);
  }
  function blink() {
    timer = null;
    if (!live() || hidden() || reduced()) return;
    const twice = rnd() < BLINK.doubleChance;
    api.lastBlink = twice ? "double" : "single";
    blinkOnce(() => (twice ? clock.set(() => blinkOnce(schedule), BLINK.gap) : schedule()));
  }
  function schedule() {
    if (timer) clock.clear(timer);
    timer = null;
    if (!live() || hidden() || reduced()) return;  // reduced motion: the eyes stay open
    const ms = Math.round(BLINK.min + rnd() * (BLINK.max - BLINK.min));
    api.lastDelay = ms;
    timer = clock.set(blink, ms);
  }
  function onVisibility() {
    if (hidden()) { if (timer) clock.clear(timer); timer = null; setClosed(false); } else if (!timer) schedule();
  }
  function brighten(svg) {
    if (reduced()) return;
    svg.classList.add("is-bright");
    clock.set(() => svg.classList.remove("is-bright"), 450);
  }

  // ---------- scanning: a soft glow, only while a real scan or re-test runs ----------
  let scanning = false;
  function setScanning(on) {
    const next = !!on;
    if (next === scanning) return;
    scanning = next;
    for (const s of instances) s.classList.toggle("is-scanning", next);  // CSS fades the glow out when it ends
  }

  const api = { create, fillSlots, setScanning, schedule, BLINK, ART, EYES, file, lastDelay: 0, lastBlink: "",
    get scanning() { return scanning; },
    _instances: instances,
    _setClock(c, r) { clock = c; rnd = r || Math.random; if (timer) clock.clear(timer); timer = null; },
    _timer() { return timer; }, _onVisibility: onVisibility };
  if (typeof module === "object" && module.exports) { module.exports = api; return; }
  window.MayaLogo = api;
  document.addEventListener("visibilitychange", onVisibility);
  if (window.matchMedia) {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (mq.addEventListener) mq.addEventListener("change", () => { setClosed(false); schedule(); });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => fillSlots());
  else fillSlots();
})();
