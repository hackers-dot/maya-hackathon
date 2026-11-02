"""Pet v2 tier 2: hover registry, learning, work/play modes, antics, post-scan tour (pet-brain.js in Node)."""
import json
import re
import shutil
import subprocess

import pytest

from src.owner_summary import FORBIDDEN
from src.report import TEXT
from src.web.server import STATIC_DIR

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")
TIME_OR_COST = re.compile(r"(?i)\d+\s*(?:s|sec|seconds?|min|minutes?|hours?|hrs?|days?|weeks?|months?|years?|%|usd|inr)\b"
                          r"|[$₹€]\s*\d")


def brain(script: str):
    r = subprocess.run([NODE, "-e", "const b = require(process.argv[1]);\n" + script, str(STATIC_DIR / "pet-brain.js")],
                       capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def page_keys():
    """Every guide key used in the pages: element ids and data-guide values (static HTML and app.js guide() calls)."""
    keys = set()
    for p in STATIC_DIR.glob("*.html"):
        t = p.read_text(encoding="utf-8")
        keys |= set(re.findall(r'\bid="([a-z0-9-]+)"', t)) | set(re.findall(r'data-guide="([a-z0-9-]+)"', t))
    for p in STATIC_DIR.glob("*.js"):
        t = p.read_text(encoding="utf-8")
        keys |= set(re.findall(r'\.id = "([a-z0-9-]+)"', t))
        for line in t.splitlines():  # app.js: guide(el(...), "key", name) calls
            if "guide(" in line:
                keys |= set(re.findall(r'\), "([a-z0-9-]+)"', line)) | set(re.findall(r'guide\(\w+, "([a-z0-9-]+)"', line))
    return keys


def test_registry_entries_match_the_pages_and_name_the_data_destination():
    out = brain("console.log(JSON.stringify({g: b.G, effects: b.EFFECTS}));")
    keys = page_keys()
    for key, e in out["g"].items():
        assert key in keys, key
        assert e["e"] in out["effects"], key
        assert e["n"].strip() and (e.get("d") or e.get("f")), key
    for name, eff in out["effects"].items():
        assert eff["label"] and eff["say"] is not None, name
        if name.startswith("sends") or name == "scanSite":  # names where the data goes
            assert re.search(r"model provider|GitHub|cloudflare-dns\.com|\{site\}|this computer", eff["label"] + eff["say"]), name
    eff = lambda k: out["effects"][out["g"][k]["e"]]["label"]  # noqa: E731
    assert eff("delete-btn") == "deletes your data" and eff("apply-btn") == "changes your demo app"
    assert eff("approve-all-btn") == "needs your approval" and eff("retest-btn").startswith("sends read-only requests")
    assert eff("send") == "sends data to the AI model provider" and eff("theme-toggle") == "just changes what you see"


def test_every_pet_text_is_clean_short_and_english():
    out = brain("""
const texts = [];
for (const k of Object.keys(b.G)) for (const audience of Object.keys(b.AUD)) for (const level of ['new', 'technical'])
  for (const count of [0, 3]) for (const demo of [false, true]) {
    const t = b.hoverText(k, {count, audience, level, demo, name: 'Configuration file appears publicly readable'});
    texts.push(t.text, t.spoken);
    const a = b.afterText(k, audience); if (a) texts.push(a);
  }
texts.push(b.suggestText('fixfirst-panel'), b.suggestText('dl-pack'));
console.log(JSON.stringify(Array.from(new Set(texts))));
""")
    for t in out:
        assert not FORBIDDEN.search(t), t
        assert not TIME_OR_COST.search(t), t
        assert len(t.split()) <= 35, (len(t.split()), t)
        assert not re.search(r"[\u0900-\u097F]|\b(hai|nahi|karo|karta|aapka|aapki)\b", t), t  # English only
        assert "no notes" not in t and "notes on this" not in t, t
    reassure = re.compile(r"(?i)\b(?:safe|secure|hacked|guarantee[ds]?|compliant)\b")
    for check, tpl in TEXT.items():  # report templates the tour may quote
        assert not reassure.search(tpl.plain), check


def test_learning_detail_levels_dynamic_names_and_after_lines():
    out = brain("""
let used = {};
used = b.bump(used, 'dl-pack'); used = b.bump(used, 'dl-pack');
console.log(JSON.stringify({
  levels: [0, 1, 4, 5, 50].map(b.detailFor),
  full: b.hoverText('apply-btn', {count: 0}), short: b.hoverText('apply-btn', {count: 2}),
  tech: b.hoverText('apply-btn', {count: 0, level: 'technical'}),
  finding: b.hoverText('finding', {name: 'Backup file appears publicly downloadable'}),
  unknown: b.hoverText(null, {name: 'Step 4'}), nameless: b.hoverText(null, {}),
  suggestion: b.hoverText('suggest-scan', {audience: 'clinic'}),
  shopScan: b.hoverText('scan-btn', {audience: 'shop'}), demoScan: b.hoverText('scan-btn', {audience: 'shop', demo: true}),
  website: b.afterText('conn-website', 'shop'), noAfter: b.afterText('apply-btn', 'shop'),
  used: used, suggest: b.pickSuggestion(['dl-pack', 'fixfirst-panel'], used),
  keyGuide: b.keyFor('conn-website', ''), keyId: b.keyFor(null, 'scan-btn'), keyNone: b.keyFor('x', 'y'),
}));
""")
    assert out["levels"] == ["full", "short", "short", "short", "short"]
    assert out["full"]["isNew"] and out["full"]["text"].startswith("Apply approved fixes: applies only the fixes you approved")
    assert out["short"] == {"text": "Apply approved fixes: changes your demo app.", "spoken": "Apply approved fixes: changes your demo app.",
                            "isNew": False, "known": True}
    assert "loopback demo app's control file" in out["tech"]["text"]
    assert out["finding"]["text"] == ("This is the finding for Backup file appears publicly downloadable. Open it to see the "
                                      "evidence and the fix.")
    assert out["unknown"]["text"] == "This is Step 4. Open it to see the details." and not out["unknown"]["known"]
    assert "no notes" not in out["nameless"]["text"]
    assert "clinic's booking site" in out["suggestion"]["text"] and "AI model provider" in out["suggestion"]["text"]
    assert "your shop's website" in out["shopScan"]["text"] and "the practice demo app" in out["demoScan"]["text"]
    assert out["website"].startswith("This is where you connect a website you own.") and out["noAfter"] is None
    assert out["used"] == {"dl-pack": 2} and out["suggest"] == "fixfirst-panel"
    assert (out["keyGuide"], out["keyId"], out["keyNone"]) == ("conn-website", "scan-btn", None)


def test_work_and_play_modes_and_rate_limited_antics():
    out = brain("""
const quiet = {state: 'nothing_to_do', scanRunning: false, typing: false, sinceHoverMs: 60000, idleMs: 30000};
const m = (x) => b.mode(Object.assign({}, quiet, x));
const a = (x) => b.nextAntic(Object.assign({mode: 'play', reduced: false, now: 100000, lastAnticAt: 0, count: 0}, x));
console.log(JSON.stringify({
  play: m({}), scan: m({scanRunning: true}), typing: m({typing: true}), hover: m({sinceHoverMs: 5000}),
  oldHover: m({sinceHoverMs: 7000}), approval: m({state: 'proposals_not_approved'}), apply: m({state: 'applied_not_retested'}),
  first: a({}), work: a({mode: 'work'}), reduced: a({reduced: true}), tooSoon: a({lastAnticAt: 90000}),
  rotate: [0, 1, 2, 3, 4, 5, 6].map((c) => a({count: c})), gap: b.ANTIC_GAP_MS,
}));
""")
    assert out["play"] == "play" and out["oldHover"] == "play"  # pointer on a control in the last 6 s = work
    assert all(out[k] == "work" for k in ("scan", "typing", "hover", "approval", "apply"))
    assert out["first"] == "wink" and out["work"] is None and out["reduced"] is None and out["tooSoon"] is None
    assert out["rotate"] == ["wink", "dance", "look", "peek", "hide", "chase", "wink"] and out["gap"] == 25000


def test_post_scan_tour_follows_the_severity_order_and_uses_the_server_lines():
    out = brain("""
const f = (sev, n) => ({target: 'card-' + n, id: 'id-' + n, severity: sev, title: 'Title ' + n, location: '/p' + n});
const findings = [f('low', 1), f('critical', 2), f('medium', 3), f('high', 4)];
const lines = {score: 'SCORE', fixfirst: 'FIX', approve: 'APPROVE', findings: {'id-2': 'LINE 2', 'id-4': 'LINE 4', 'id-3': 'LINE 3', 'id-1': 'LINE 1'}};
console.log(JSON.stringify({
  stops: b.tourStops({findings: findings, lines: lines, scoreLine: 'Grade D · 47/100'}),
  fallback: b.tourStops({findings: findings, scoreLine: 'Grade D · 47/100'}),
  zero: b.tourStops({findings: [], scoreLine: 'Grade A · 100/100'}),
}));
""")
    st = out["stops"]
    assert [s["target"] for s in st] == ["score-card", "card-2", "card-4", "card-3", "card-1", "fixfirst-panel",
                                          "proposals-panel"]
    assert [s["text"] for s in st] == ["SCORE", "LINE 2", "LINE 4", "LINE 3", "LINE 1", "FIX", "APPROVE"]
    assert st[0]["expression"] == "nervous" and st[1]["expression"] == "alert" and st[3]["expression"] == "curious"
    assert out["fallback"][1]["text"] == "Critical issue, 1 of 4: Title 2 at /p2."
    allowed = {"47", "100", "4", "1", "2", "3"}
    for s in out["fallback"]:
        assert set(re.findall(r"\d+", s["text"])) <= allowed, s["text"]
    assert out["zero"][0]["expression"] == "proud" and "no issues detected in the checks run" in out["zero"][0]["text"]


def test_browser_guards_for_hover_typing_touch_and_forgetting():
    guide = (STATIC_DIR / "guide.js").read_text(encoding="utf-8")
    app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    pet = (STATIC_DIR / "pet.js").read_text(encoding="utf-8")
    assert '"(hover: hover) and (pointer: fine)"' in guide  # never on touch devices
    assert "hoverTimer = setTimeout(() => { if (hoverEl === el) explain(el); }, 400);" in guide
    assert "leaveTimer = setTimeout(endHover, 800);" in guide
    assert "typing() || !document.contains(el)" in guide and "lastTypingAt" in guide
    assert 'el.getAttribute("data-guide-name") || el.getAttribute("aria-label")' in guide  # never child text
    assert '"maya-pet-used"' in guide and 'type === "data-deleted"' in guide and 'emit("data-deleted")' in app
    assert 'if (seenRunning.delete(runId)) emit("scan-finished"' in app  # only a scan this page saw running
    assert 'opts.getMode && opts.getMode() === "work"' in pet  # no antics while working
    for forbidden in ("click()", ".submit(", "requestSubmit"):
        assert forbidden not in guide, forbidden  # tier 2: the pet only points and explains
