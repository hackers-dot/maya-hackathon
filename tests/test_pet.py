"""MAYA pet: expression/pose tables, state-to-emotion mapping, pure helpers (in Node), static rules."""
import json
import re
import shutil
import subprocess
import threading

import httpx
import pytest

from src.guide import EXPRESSIONS, POSES, STATES, step
from src.web.server import STATIC_DIR, make_server

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def node(script: str):
    r = subprocess.run([NODE, "-e", script, str(STATIC_DIR / "pet.js"), str(STATIC_DIR / "guide.js")],
                       capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


# facts that reach each guide state (shared with test_guide.py)
from test_guide import FACTS, SITE, run  # noqa: E402


@needs_node
def test_expression_table_is_complete_and_matches_the_server():
    out = node("""
const p = require(process.argv[1]);
const bad = [];
for (const [name, e] of Object.entries(p.EXPRESSIONS)) {
  if (!Array.isArray(e.eyes) || e.eyes.length !== 2) bad.push(name + ': two eyes');
  for (const eye of e.eyes || []) if (!p.EYE_SHAPES.includes(eye)) bad.push(name + ': eye ' + eye);
  if (!p.MOUTHS.includes(e.mouth)) bad.push(name + ': mouth ' + e.mouth);
  if (e.fx !== null && !p.EFFECTS.includes(e.fx)) bad.push(name + ': fx ' + e.fx);
}
console.log(JSON.stringify({names: Object.keys(p.EXPRESSIONS), poses: p.POSES, bad: bad, events: p.EVENT_EMOTION}));
""")
    assert out["bad"] == []
    assert set(out["names"]) == set(EXPRESSIONS) and out["poses"] == list(POSES)
    required = {"happy", "excited", "laughing", "smiling", "wink", "playful", "sad", "crying", "angry", "surprised",
                "shocked", "confused", "thinking", "curious", "sleepy", "tired", "nervous", "proud", "determined",
                "love", "alert", "analyzing", "idle"}
    assert required <= set(out["names"])
    for event, seq in out["events"].items():
        assert seq and all(e["expression"] in EXPRESSIONS and e["pose"] in POSES for e in seq), event
    ev = out["events"]
    assert ev["scan-started"][0] == {"expression": "analyzing", "pose": "scanning", "ms": None}
    assert ev["error"][-1]["expression"] == "sad" and ev["no-model"][-1]["expression"] == "confused"
    assert ev["waiting-model"][-1]["expression"] == "thinking" and ev["answered"][-1]["expression"] == "happy"
    assert ev["applying"][-1]["expression"] == "determined"


@pytest.mark.parametrize("state", STATES)
def test_every_state_maps_to_a_known_emotion_deterministically(state):
    for level in ("new", "technical"):
        for lang in ("en", "hinglish"):
            a = step(state, level, lang, FACTS[state])["emotion"]
            assert a and a == step(state, level, lang, FACTS[state])["emotion"]
            assert all(e["expression"] in EXPRESSIONS and e["pose"] in POSES for e in a)
            assert a[-1]["ms"] is None and all(isinstance(e["ms"], int) for e in a[:-1])  # the last one holds
    # the emotion does not depend on level or language
    assert step(state, "new", "en", FACTS[state])["emotion"] == step(state, "technical", "hinglish", FACTS[state])["emotion"]


def test_emotions_follow_real_results():
    emo = lambda state, facts: [(e["expression"], e["pose"]) for e in step(state, "new", "en", facts)["emotion"]]  # noqa: E731
    assert emo("scan_running", FACTS["scan_running"]) == [("analyzing", "scanning")]
    assert emo("scan_finished", FACTS["scan_finished"]) == [("alert", "protecting"), ("nervous", "pointing")]
    medium = {**SITE, "run": run(counts={"critical": 0, "high": 0, "medium": 2, "low": 0, "info": 0})}
    assert emo("scan_finished", medium) == [("curious", "pointing")]
    zero = {**SITE, "run": run(counts={s: 0 for s in ("critical", "high", "medium", "low", "info")})}
    assert emo("nothing_to_do", zero) == [("proud", "idle")]
    assert emo("nothing_to_do", {"account_type": "individual"}) == [("smiling", "idle")]
    assert emo("proposals_not_approved", FACTS["proposals_not_approved"]) == [("determined", "pointing")]
    assert emo("applied_not_retested", FACTS["applied_not_retested"]) == [("determined", "pointing")]
    fixed = FACTS["retest_fixed"]
    assert emo("retest_fixed", fixed) == [("excited", "celebrating"), ("proud", "idle")]
    grade_a = {**fixed, "run": {**fixed["run"], "after_grade": "A"}}
    assert emo("retest_fixed", grade_a) == [("excited", "celebrating"), ("love", "idle")]
    assert emo("retest_still_present", FACTS["retest_still_present"])[-1] == ("confused", "protecting")
    assert emo("scan_failed", FACTS["scan_failed"]) == [("sad", "idle")]
    assert emo("no_account_type", FACTS["no_account_type"])[0] == ("happy", "waving")


@needs_node
def test_pure_helpers():
    out = node("""
const p = require(process.argv[1]);
const g = require(process.argv[2]);
const throwing = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('full'); } };
const mem = { v: {}, getItem(k) { return this.v[k] || null; }, setItem(k, x) { this.v[k] = x; } };
const long = 'word '.repeat(80).trim() + '.';
console.log(JSON.stringify({
  split: p.splitSentences('Hello there. This is MAYA! Ready? ok'),
  longParts: p.splitSentences(long, 160).map((s) => s.length),
  empty: p.splitSentences(''),
  reduced: p.behavior(true, false), mobile: p.behavior(false, true), full: p.behavior(false, false),
  readThrows: p.readPos(throwing, 'k'), writeThrows: p.writePos(throwing, 'k', {x: 1, y: 2}),
  readBad: p.readPos({ getItem() { return '{not json'; } }, 'k'),
  roundTrip: (p.writePos(mem, 'k', {x: 10.4, y: 20.6}), p.readPos(mem, 'k')),
  clampNull: p.clampPos(null, 800, 600, 100, 100), clampOut: p.clampPos({x: 5000, y: -9}, 800, 600, 100, 100),
  clampStr: p.clampPos({x: '1', y: 2}, 800, 600, 100, 100),
  beside: p.placeBeside({x: 100, y: 100, w: 80, h: 40}, {w: 60, h: 80}, {w: 800, h: 600}, []),
  besideBlocked: p.placeBeside({x: 100, y: 100, w: 80, h: 40}, {w: 60, h: 80}, {w: 800, h: 600},
    [{x: 180, y: 50, w: 100, h: 150}]),
  nowhere: p.placeBeside({x: 0, y: 0, w: 800, h: 600}, {w: 60, h: 80}, {w: 800, h: 600}, []),
  noSpeech: g.speechSupported({}), est: g.estimateMs('Hello there friend.', 1),
  localFirst: g.pickVoice([{lang: 'en-IN', name: 'Heera net', localService: false}, {lang: 'en-IN', name: 'Heera local', localService: true}], 'en').voice.name,
  networkOnly: g.pickVoice([{lang: 'hi-IN', name: 'Swara', localService: false}], 'hinglish').voice.name,
  hinglishLocal: g.pickVoice([{lang: 'hi-IN', name: 'Google hi', localService: false}, {lang: 'en-IN', name: 'Heera', localService: true}], 'hinglish').voice.name,
}));
""")
    assert out["split"] == ["Hello there.", "This is MAYA!", "Ready?", "ok"]
    assert out["longParts"] and max(out["longParts"]) <= 160 and len(out["longParts"]) >= 3
    assert out["empty"] == []
    assert out["reduced"] == {"roam": False, "loops": False, "drag": True}  # reduced motion: docked, no loops
    assert out["mobile"] == {"roam": False, "loops": True, "drag": False}
    assert out["full"] == {"roam": True, "loops": True, "drag": True}
    assert out["readThrows"] is None and out["writeThrows"] is False and out["readBad"] is None
    assert out["roundTrip"] == {"x": 10, "y": 21}
    assert out["clampNull"] is None and out["clampStr"] is None  # no saved position never means (0, 0)
    assert out["clampOut"] == {"x": 700, "y": 0}
    assert out["beside"]["side"] == "right"
    assert out["besideBlocked"]["side"] != "right"  # another control is there: it stands elsewhere
    assert out["nowhere"] is None  # no free spot: it stays home
    assert out["noSpeech"] is False and out["est"] > 300
    # a female-sounding voice wins over a neutral hi-IN one; on-device voices before online ones
    assert (out["localFirst"], out["networkOnly"], out["hinglishLocal"]) == ("Heera local", "Swara", "Heera")


@needs_node
def test_every_js_file_passes_node_check():
    for js in sorted(STATIC_DIR.glob("*.js")):
        r = subprocess.run([NODE, "--check", str(js)], capture_output=True, text=True)
        assert r.returncode == 0, (js.name, r.stderr)


def test_pet_art_is_code_only_and_safe():
    js = (STATIC_DIR / "pet.js").read_text(encoding="utf-8")
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
                "<image", "createElement(\"img\")", "fetch(", "XMLHttpRequest", "WebSocket"):
        assert bad not in js, bad
    assert not re.search(r"data:[a-z]+/", js)  # no data: URLs (raster or otherwise)
    assert "createElementNS" in js and ".setAttribute(\"style\"" not in js
    # the pet is code only; the one raster asset in static/ is the logo artwork (logo/, pinned in test_web/test_logo)
    assert not [p.name for p in STATIC_DIR.rglob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp")
                and p.parent.name != "logo"]
    sheet = (STATIC_DIR / "pet-sheet.js").read_text(encoding="utf-8")
    assert "innerHTML" not in sheet and "textContent" in sheet
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert "@media (prefers-reduced-motion: reduce)" in css and "animation: none !important" in css
    assert "url(/static/logo/maya-logo-64.png)" in css  # the small mark beside chat answers is the logo art (64 px copy)


def test_pet_on_every_page_and_contact_sheet_only_in_preview(tmp_path):
    for p in STATIC_DIR.glob("*.html"):
        if p.name == "pet-sheet.html":
            continue
        assert '<script src="/static/pet.js" defer></script>' in p.read_text(encoding="utf-8"), p.name
    for preview in (False, True):
        server = make_server(port=0, data_dir=tmp_path / str(preview), ui_preview=preview)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}", trust_env=False) as c:
            codes = [c.get("/static/pet-sheet.js").status_code, c.get("/static/pet-sheet.html").status_code,
                     c.get("/__preview/pet-sheet.html").status_code]
            assert c.get("/static/pet.js").status_code == 200
        server.shutdown()
        server.server_close()
        assert codes == ([200, 200, 200] if preview else [404, 404, 404]), (preview, codes)


def test_voice_and_mic_disclosures_are_in_the_ui():
    js = "\n".join((STATIC_DIR / n).read_text(encoding="utf-8") for n in ("guide.js", "pet-lines.js", "chat.html"))
    for text in ("Voice uses your browser's built-in text-to-speech, not a Meta model",
                 "No female-sounding voice found: pick one manually or install a speech voice in Windows Settings > "
                 "Speech; text still works", "Turn on voice", "Test voice",
                 "onvoiceschanged", "onboundary", "splitSentences", "may send the audio to its speech provider",
                 "the browser may send the spoken text to the voice provider. MAYA itself sends nothing"):
        assert text in js, text
    # speech only after the user's own gesture, and only browser APIs (no audio ever reaches the server)
    # speech waits for the browser's user-activation rule: the latest line is kept until the first gesture
    assert "if (!gesture) { pendingSpeech = {" in js and "getUserMedia" not in js and "MediaRecorder" not in js
    assert 'load("maya-voice", "on") === "off" ? "off" : "on"' in js  # on by default; only mute turns it off
