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


def page_ids_and_classes():
    ids, classes = set(), set()
    for p in STATIC_DIR.glob("*.html"):
        t = p.read_text(encoding="utf-8")
        ids |= set(re.findall(r'\bid="([a-z0-9-]+)"', t))
        for c in re.findall(r'\bclass="([^"]+)"', t):
            classes |= set(c.split())
    for p in STATIC_DIR.glob("*.js"):
        t = p.read_text(encoding="utf-8")
        ids |= set(re.findall(r'\.id = "([a-z0-9-]+)"', t))
        for c in re.findall(r'el\("[a-z0-9]+", "([^"]+)"', t):
            classes |= set(c.split())
    return ids, classes


def test_hover_registry_is_complete_and_matches_the_dom():
    out = brain("console.log(JSON.stringify({hover: b.HOVER, effects: b.EFFECTS, generic: b.GENERIC}));")
    ids, classes = page_ids_and_classes()
    for key, e in out["hover"].items():
        if key.startswith("."):
            assert key[1:] in classes, key
        else:
            assert key in ids, key
        assert e["effect"] in out["effects"], key
        for lang in ("en", "hinglish"):
            what, click = e[lang]
            assert what.strip() and click.strip(), (key, lang)
    for name, eff in out["effects"].items():
        assert eff["en"] and eff["hinglish"], name
        if name.startswith("sends"):  # names the destination
            assert re.search(r"model provider|GitHub|cloudflare-dns\.com|your own verified site|MAYA on this computer", eff["en"]), name
    # the controls that matter most are all explained
    for must in ("scan-btn", "apply-btn", "approve-all-btn", "retest-btn", "delete-btn", "disconnect-btn",
                 "reset-demo-btn", "investigate-btn", "conn-github", "conn-watch", "conn-msg", ".finding-sum", ".send"):
        assert must in out["hover"], must
    assert out["effects"][out["hover"]["delete-btn"]["effect"]]["en"] == "deletes your data"
    assert out["effects"][out["hover"]["apply-btn"]["effect"]]["en"] == "changes something on your demo app"
    assert out["effects"][out["hover"]["approve-all-btn"]["effect"]]["en"] == "needs your approval"


def test_every_pet_text_is_clean():
    out = brain("""
const texts = [];
for (const e of Object.values(b.HOVER)) for (const l of ['en', 'hinglish']) texts.push(e[l][0], e[l][1]);
for (const e of Object.values(b.EFFECTS)) texts.push(e.en, e.hinglish);
texts.push(b.GENERIC.en, b.GENERIC.hinglish, b.suggestText('fixfirst-panel', 'en'), b.suggestText('dl-pack', 'hinglish'));
for (const lang of ['en', 'hinglish']) {
  for (const s of b.tourStops({findings: [], scoreLine: 'Grade A · 100/100'}, lang)) texts.push(s.text);
  for (const s of b.tourStops({findings: [{target: 't', severity: 'high', title: 'T', location: '/', plain: 'P.'}],
    scoreLine: 'Grade C · 70/100'}, lang)) texts.push(s.text);
}
console.log(JSON.stringify(texts));
""")
    pets = (STATIC_DIR / "pet-lines.js").read_text(encoding="utf-8")
    for t in out:
        assert not FORBIDDEN.search(t), t
        assert not TIME_OR_COST.search(t), t
    assert "NEED NATIVE-SPEAKER REVIEW" in (STATIC_DIR / "pet-brain.js").read_text(encoding="utf-8") and pets
    # the tour reads the report templates' plain text; facts like "expires within a month" are allowed there,
    # reassurance never is
    reassure = re.compile(r"(?i)(?:safe|secure|hacked|guarantee[ds]?|compliant)")
    for check, tpl in TEXT.items():
        assert not reassure.search(tpl.plain), check


def test_learning_detail_levels_and_suggestions():
    out = brain("""
let used = {};
used = b.bump(used, 'dl-pack'); used = b.bump(used, 'dl-pack');
console.log(JSON.stringify({
  levels: [0, 1, 4, 5, 50].map(b.detailFor),
  full: b.hoverText('apply-btn', 'en', 0), short: b.hoverText('apply-btn', 'en', 2), none: b.hoverText('apply-btn', 'en', 9),
  hiFull: b.hoverText('scan-btn', 'hinglish', 0), unknown: b.hoverText(null, 'en', 0),
  used: used,
  suggest: b.pickSuggestion(['dl-pack', 'fixfirst-panel'], used), suggestUsed: b.pickSuggestion(['dl-pack'], used),
  suggestNone: b.pickSuggestion([], {}), suggestText: b.suggestText('fixfirst-panel', 'hinglish'),
  keyId: b.keyFor('scan-btn', ['btn']), keyClass: b.keyFor('', ['finding-sum']), keyNone: b.keyFor('x', ['y']),
}));
""")
    assert out["levels"] == ["full", "short", "short", "none", "none"]
    assert out["full"]["isNew"] and out["full"]["text"].startswith("New: Apply approved fixes")
    assert "(changes something on your demo app)" in out["full"]["text"]
    assert out["short"] == {"text": "Apply approved fixes: changes something on your demo app.", "isNew": False}
    assert out["none"] is None
    assert out["hiFull"]["text"].startswith("Naya: Run security scan")
    assert out["unknown"]["text"] == "This control is part of MAYA. Nothing happens until you click it."
    assert out["used"] == {"dl-pack": 2}
    assert (out["suggest"], out["suggestUsed"], out["suggestNone"]) == ("fixfirst-panel", None, None)
    assert out["suggestText"] == "Ye abhi tak try nahi kiya: Fix first"
    assert (out["keyId"], out["keyClass"], out["keyNone"]) == ("scan-btn", ".finding-sum", None)


def test_work_and_play_modes_and_rate_limited_antics():
    out = brain("""
const quiet = {state: 'nothing_to_do', scanRunning: false, typing: false, sinceHoverMs: 60000, idleMs: 30000};
const m = (x) => b.mode(Object.assign({}, quiet, x));
const a = (x) => b.nextAntic(Object.assign({mode: 'play', reduced: false, now: 100000, lastAnticAt: 0, count: 0}, x));
console.log(JSON.stringify({
  play: m({}), scan: m({scanRunning: true}), typing: m({typing: true}), hover: m({sinceHoverMs: 3000}),
  busyIdle: m({idleMs: 5000}), approval: m({state: 'proposals_not_approved'}), apply: m({state: 'applied_not_retested'}),
  first: a({}), work: a({mode: 'work'}), reduced: a({reduced: true}), tooSoon: a({lastAnticAt: 90000}),
  rotate: [0, 1, 2, 3, 4, 5, 6].map((c) => a({count: c})), gap: b.ANTIC_GAP_MS,
}));
""")
    assert out["play"] == "play"
    assert all(out[k] == "work" for k in ("scan", "typing", "hover", "busyIdle", "approval", "apply"))
    assert out["first"] == "wink" and out["work"] is None and out["reduced"] is None and out["tooSoon"] is None
    assert out["rotate"] == ["wink", "dance", "look", "peek", "hide", "chase", "wink"] and out["gap"] == 25000


def test_post_scan_tour_follows_the_severity_order_and_invents_nothing():
    out = brain("""
const f = (sev, n) => ({target: 'card-' + n, severity: sev, title: 'Title ' + n, location: '/p' + n, plain: 'Plain ' + n + '.'});
const findings = [f('low', 1), f('critical', 2), f('medium', 3), f('high', 4)];
console.log(JSON.stringify({
  en: b.tourStops({findings: findings, scoreLine: 'Grade D · 47/100'}, 'en'),
  hi: b.tourStops({findings: findings, scoreLine: 'Grade D · 47/100'}, 'hinglish'),
  zero: b.tourStops({findings: [], scoreLine: 'Grade A · 100/100'}, 'en'),
}));
""")
    en = out["en"]
    assert [s["target"] for s in en] == ["score-card", "card-2", "card-4", "card-3", "card-1", "fixfirst-panel",
                                          "proposals-panel"]
    assert [s.get("severity") for s in en[1:5]] == ["critical", "high", "medium", "low"]
    assert en[0]["expression"] == "nervous" and en[1]["expression"] == "alert" and en[3]["expression"] == "curious"
    assert "Grade D · 47/100" in en[0]["text"] and "found 4 issue(s)" in en[0]["text"]
    assert "(1 of 4): Title 2 at /p2. Plain 2." in en[1]["text"] and "Look at this one first" in en[1]["text"]
    assert "I never approve for you" in en[-1]["text"]
    allowed = {"47", "100", "4", "1", "2", "3"}  # only numbers from the inputs appear
    for s in en + out["hi"]:
        assert set(re.findall(r"\d+", s["text"])) <= allowed, s["text"]
    assert out["zero"][0]["expression"] == "proud" and "no issues detected in the checks run" in out["zero"][0]["text"]
    assert len(out["zero"]) == 1


def test_browser_guards_for_hover_typing_touch_and_forgetting():
    guide = (STATIC_DIR / "guide.js").read_text(encoding="utf-8")
    app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    pet = (STATIC_DIR / "pet.js").read_text(encoding="utf-8")
    assert '"(hover: hover) and (pointer: fine)"' in guide  # never on touch devices
    assert "hoverTimer = setTimeout(() => { if (hoverEl === el) explain(el); }, 400);" in guide
    assert "leaveTimer = setTimeout(endHover, 800);" in guide
    assert "typing() || !document.contains(el)" in guide and "lastTypingAt" in guide
    assert '"maya-pet-used"' in guide and 'type === "data-deleted"' in guide and 'emit("data-deleted")' in app
    assert 'if (seenRunning.delete(runId)) emit("scan-finished"' in app  # only a scan this page saw running
    assert 'opts.getMode && opts.getMode() === "work"' in pet  # no antics while working
    for forbidden in ("click()", ".submit(", "requestSubmit"):
        assert forbidden not in guide, forbidden  # tier 2: the pet only points and explains
