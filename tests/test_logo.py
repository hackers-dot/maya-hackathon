"""The MAYA logo: the original artwork with blinking eyelids (logo.js in Node with a tiny fake DOM and fake timers;
the PNG files; placement on pages)."""
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from src.report import to_html
from src.web.server import STATIC_DIR

NODE = shutil.which("node")
ROOT = Path(__file__).resolve().parent.parent
LOGO_DIR = STATIC_DIR / "logo"
# the brand asset: byte-identical to the original artwork (Neon Cyber Cat Emblem, 1254 x 1254)
ORIGINAL_SHA256 = "23221779d3944ea7542cfd7026a1ade8ff27e83be3dc7f007db0fb56a5201c0d"

# a minimal DOM: just what logo.js touches (createElementNS, attributes, children, classList, a few queries)
FAKE_DOM = r"""
class El {
  constructor(tag) { this.tag = tag; this.attrs = {}; this.children = []; this.parent = null; this.cls = new Set();
    this.listeners = {}; const self = this;
    this.classList = { add: (c) => self.cls.add(c), remove: (c) => self.cls.delete(c), contains: (c) => self.cls.has(c),
      toggle: (c, on) => { const v = on === undefined ? !self.cls.has(c) : !!on; if (v) self.cls.add(c); else self.cls.delete(c); return v; } }; }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === "class") { this.cls = new Set(String(v).split(/\s+/).filter(Boolean)); } }
  getAttribute(k) { return k === "class" ? Array.from(this.cls).join(" ") : (k in this.attrs ? this.attrs[k] : null); }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
  get isConnected() { let n = this; while (n) { if (n === global.document.body) return true; n = n.parent; } return false; }
  all() { const out = []; const walk = (n) => { for (const c of n.children) { out.push(c); walk(c); } }; walk(this); return out; }
  querySelectorAll(sel) { const m = /^\.([\w-]+):not\(\.([\w-]+)\)$/.exec(sel);
    return this.all().filter((n) => m ? n.cls.has(m[1]) && !n.cls.has(m[2]) : n.cls.has(sel.replace(/^\./, ""))); }
}
global.document = { body: new El("body"), hidden: false, readyState: "complete",
  createElementNS: (ns, tag) => new El(tag), createElement: (tag) => { const e = new El(tag);
    Object.defineProperty(e, "className", { set(v) { e.setAttribute("class", v); }, get() { return e.getAttribute("class"); } }); return e; },
  getElementById(id) { return this.body.all().find((n) => n.attrs.id === id) || null; },
  querySelectorAll(sel) { return this.body.querySelectorAll(sel); }, addEventListener() {} };
let REDUCED = false;
global.window = { matchMedia: () => ({ matches: REDUCED }) };
const L = require(process.argv[1]);
const q = []; let nextId = 1;
const clock = { set: (fn, ms) => { const id = nextId++; q.push({ id, fn, ms }); return id; },
  clear: (id) => { const i = q.findIndex((t) => t.id === id); if (i >= 0) q.splice(i, 1); } };
function step() { const t = q.shift(); if (t) t.fn(); return t; }
function mount(size) {  // the page path: a slot, then fillSlots() fills it and starts the scheduler
  const slot = new El("span"); slot.setAttribute("class", "maya-logo-slot"); slot.setAttribute("data-size", String(size));
  document.body.appendChild(slot); L.fillSlots(); return slot.children[0];
}
"""


def logo(script: str, rnd="() => 0.5"):
    r = subprocess.run([NODE, "-e", FAKE_DOM + f"L._setClock(clock, {rnd});\n" + script, str(STATIC_DIR / "logo.js")],
                       capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@needs_node
def test_builds_the_original_image_and_exactly_two_eyelids():
    out = logo("""
const a = mount(44), b = mount(200), c = mount(1024);
const img = (s) => s.all().filter((n) => n.tag === "image");
const ids = document.body.all().map((n) => n.attrs.id).filter(Boolean);
console.log(JSON.stringify({ images: img(a).length, href: [img(a)[0].attrs.href, img(b)[0].attrs.href, img(c)[0].attrs.href],
  box: [img(a)[0].parent.attrs.viewBox, img(a)[0].attrs.width, img(a)[0].attrs.height], art: img(a)[0].parent.getAttribute("class"),
  layers: a.children.map((n) => n.getAttribute("class")), lidLayerFirst: a.children[1].all().some((n) => n.cls.has("maya-lid")),
  lids: a.all().filter((n) => n.cls.has("maya-lid")).map((n) => n.attrs["data-part"]),
  defs: document.body.children.filter((n) => n.attrs.id === "maya-logo-defs").length, ids,
  blurs: document.getElementById("maya-logo-defs").all().filter((n) => n.tag === "feGaussianBlur").length,
  size: a.attrs["data-size"], hidden: a.attrs["aria-hidden"] }));
""")
    assert out["images"] == 1 and out["art"] == "maya-logo-art"
    assert out["href"] == ["/static/logo/maya-logo-512.png", "/static/logo/maya-logo-1024.png", "/static/logo/maya-logo.png"]
    assert out["box"] == ["0 0 1254 1254", "1254", "1254"]  # the image fills the original's pixel grid: no distortion
    assert out["lids"] == ["lidL", "lidR"]  # exactly two eyelids
    assert out["defs"] == 1 and len(out["ids"]) == len(set(out["ids"])) and out["blurs"] == 1
    assert out["size"] == "44" and out["hidden"] == "true"
    assert out["layers"] == ["maya-logo-art", "maya-logo-lids"] and out["lidLayerFirst"]  # lids above the blended art


@needs_node
def test_eyelids_sit_on_the_measured_eyes():
    lids = logo("""
const s = mount(64); const box = (d) => { const n = d.match(/-?\\d+/g).map(Number); const xs = n.filter((_, i) => i % 2 === 0), ys = n.filter((_, i) => i % 2);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]; };
console.log(JSON.stringify(s.all().filter((n) => n.cls.has("maya-lid")).map((n) => box(n.attrs.d))));
""")
    # measured halos on the original: left 410-575 x 645-759, right 683-848 x 646-760; each lid covers its halo
    for (x0, y0, x1, y1), (hx0, hy0, hx1, hy1) in zip(lids, [(410, 645, 575, 759), (683, 646, 848, 760)]):
        assert x0 <= hx0 and y0 <= hy0 and x1 >= hx1 and y1 >= hy1, (x0, y0, x1, y1)
        assert (x1 - x0) < 1.3 * (hx1 - hx0)  # and stays close to the eye


@needs_node
def test_one_scheduler_for_many_logos_and_the_blink_timing():
    out = logo("""
for (let i = 0; i < 6; i++) mount(64);
const pending = q.length, delay = q[0].ms;
step();
const closed = Array.from(L._instances).every((s) => s.cls.has("is-blink"));
const closeMs = q[0].ms; step();
const open = Array.from(L._instances).every((s) => !s.cls.has("is-blink"));
const openMs = q[0].ms; step();
console.log(JSON.stringify({ pending, delay, closed, closeMs, open, openMs, next: q.length, nextDelay: q[0].ms, kind: L.lastBlink }));
""")
    assert out["pending"] == 1  # ONE scheduler for six logos
    assert out["delay"] == 4500 and 3000 <= out["nextDelay"] <= 6000
    assert out["closed"] and out["open"] and out["closeMs"] == 90 and out["openMs"] == 140 and out["kind"] == "single"


@needs_node
def test_delay_range_and_the_double_blink_path():
    edges = logo("""
mount(64); const low = q[0].ms;
L._setClock(clock, () => 0.9999); L.schedule(); const high = q[q.length - 1].ms;
console.log(JSON.stringify([low, high]));
""", rnd="() => 0")
    assert edges[0] == 3000 and 5990 <= edges[1] <= 6000
    out = logo("""
mount(64); const s = Array.from(L._instances)[0];
const seq = []; step();
for (let i = 0; i < 6 && q.length; i++) { seq.push((s.cls.has("is-blink") ? "closed " : "open ") + q[0].ms); step(); }
console.log(JSON.stringify({ seq, kind: L.lastBlink }));
""", rnd="() => 0.1")
    assert out["kind"] == "double" and out["seq"][:4] == ["closed 90", "open 140", "open 110", "closed 90"], out["seq"]


@needs_node
def test_reduced_motion_and_hidden_tab():
    reduced = logo("""
REDUCED = true; const s = mount(64);
console.log(JSON.stringify({ timers: q.length, blink: s.cls.has("is-blink") }));
""")
    assert reduced == {"timers": 0, "blink": False}  # no blink: the eyes stay open
    hidden = logo("""
mount(64); const before = q.length;
document.hidden = true; L._onVisibility(); const hiddenTimers = q.length;
document.hidden = false; L._onVisibility();
console.log(JSON.stringify({ before, hiddenTimers, after: q.length }));
""")
    assert hidden == {"before": 1, "hiddenTimers": 0, "after": 1}
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert "@media (prefers-reduced-motion: reduce)" in css and "animation: none !important" in css


@needs_node
def test_scanning_state_only_on_real_events():
    out = logo("""
const a = mount(64); const start = a.cls.has("is-scanning");
L.setScanning(true); const on = a.cls.has("is-scanning"); const late = mount(44); const lateOn = late.cls.has("is-scanning");
L.setScanning(false);
console.log(JSON.stringify({ start, on, lateOn, off: !a.cls.has("is-scanning") && !late.cls.has("is-scanning") }));
""")
    assert out == {"start": False, "on": True, "lateOn": True, "off": True}
    app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    calls = re.findall(r"logoScanning\(([^)]*)\)", app)
    assert sorted(calls) == sorted(["on", 'run.status === "running"', "true", "false", "false"]), calls
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert ".maya-scan-glow { opacity: 0; transition: opacity 0.8s ease-out; }" in css  # eased stop


def test_the_art_files_are_the_original():
    assert hashlib.sha256((LOGO_DIR / "maya-logo.png").read_bytes()).hexdigest() == ORIGINAL_SHA256
    assert sorted(p.name for p in LOGO_DIR.iterdir()) == ["maya-logo-1024.png", "maya-logo-512.png", "maya-logo-64.png",
                                                          "maya-logo.png"]


def test_placement_and_no_leftovers_of_the_old_drawing():
    sizes = {}
    for p in STATIC_DIR.glob("*.html"):
        page = p.read_text(encoding="utf-8")
        assert '<link rel="icon" href="/static/logo/maya-logo-64.png" type="image/png">' in page, p.name
        sizes[p.name] = re.findall(r'class="maya-logo-slot[^"]*" data-size="(\d+)"', page)
        if p.name != "pet-sheet.html":
            assert '<script src="/static/logo.js" defer></script>' in page, p.name
    assert sizes["chat.html"] == ["44", "112", "36"]  # sidebar, chat home, result header
    assert sizes["signin.html"] == ["48"] and all(sizes[n] == ["64"] for n in ("consent.html", "connect.html", "company.html"))
    chat = (STATIC_DIR / "chat.html").read_text(encoding="utf-8")
    assert '<span class="wordmark">MAY<span class="wm-a">A</span></span>' in chat
    signin = (STATIC_DIR / "signin.html").read_text(encoding="utf-8")
    assert '<span class="tagline">AI SECURITY ASSISTANT</span>' in signin and signin.count('id="hero-pet"') == 1
    old = re.compile(r"maya-logo(-flat|-white|-black|-outline)?\.svg|favicon\.svg|build_logo")
    for path in list(STATIC_DIR.rglob("*.*")) + list((ROOT / "src").rglob("*.py")) + list((ROOT / "scripts").glob("*.*")):
        if path.suffix in (".py", ".js", ".html", ".css"):
            assert not old.search(path.read_text(encoding="utf-8")), path.name
    # printable report: the art as a same-origin image; the downloaded file stays self-contained (no image)
    from test_report import report_for
    assert 'src="/static/logo/maya-logo-512.png"' in to_html(report_for(["missing_csp"]), printable=True)
    assert "<img" not in to_html(report_for(["missing_csp"]))


def test_every_js_file_passes_node_check():
    if NODE is None:
        pytest.skip("node is not installed")
    for p in sorted(STATIC_DIR.glob("*.js")):
        r = subprocess.run([NODE, "--check", str(p)], capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, (p.name, r.stderr)
