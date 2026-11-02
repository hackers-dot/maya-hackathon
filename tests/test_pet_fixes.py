"""Pet v2 fixes: the idle scheduler (fake clock), speech from the first gesture, mute as the only silence switch,
hover guide on every page, clickable chips; plus the real-input browser check when Chrome is installed."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from src.web.server import STATIC_DIR

NODE = shutil.which("node")
ROOT = Path(__file__).resolve().parent.parent
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def brain(script: str):
    r = subprocess.run([NODE, "-e", "const b = require(process.argv[1]);\n" + script, str(STATIC_DIR / "pet-brain.js")],
                       capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


SIM = """
function run(opts) {
  let st = b.idleStart(); let last = 0; const log = [];
  for (let t = 0; t <= opts.until; t += 1000) {
    if ((opts.inputs || []).includes(t)) last = t;
    const work = (opts.work || []).some(([a, z]) => t >= a && t < z);
    const [n, a] = b.idleTick(st, { now: t, lastInputAt: last, work: work, reduced: !!opts.reduced });
    if (a) log.push([t, a, n.phase]);
    st = n;
  }
  return log;
}
"""


@needs_node
def test_idle_scheduler_plays_sleeps_and_wakes_on_a_fake_clock():
    out = brain(SIM + """console.log(JSON.stringify({
  plain: run({ until: 90000, inputs: [0, 60000] }),
  work: run({ until: 40000, inputs: [0], work: [[0, 40000]] }),
  afterWork: run({ until: 70000, inputs: [0], work: [[0, 40000]] }),
  reduced: run({ until: 90000, inputs: [0, 60000], reduced: true }),
  same: JSON.stringify(run({ until: 90000, inputs: [0, 60000] })) === JSON.stringify(run({ until: 90000, inputs: [0, 60000] })),
}));""")
    log = out["plain"]
    antics = [x for x in log if x[1] not in ("sleepy", "wake")]
    assert len(antics) >= 3, log
    assert [x[1] for x in log].count("sleepy") == 1 and [x[1] for x in log].count("wake") == 1, log
    first = antics[0]
    assert 8000 <= first[0] <= 15000 and first[1] in ("lookPointer", "wave"), first  # 8-15 s, look or wave
    gaps = [b[0] - a[0] for a, b in zip(antics, antics[1:]) if b[0] < 45000]
    assert all(15000 <= g <= 25000 for g in gaps), gaps
    sleepy = next(x for x in log if x[1] == "sleepy")
    assert sleepy[0] == 45000 and not [x for x in antics if 45000 < x[0] < 60000]  # nothing while asleep
    assert next(x for x in log if x[1] == "wake")[0] == 60000  # the first input wakes her (surprised)
    assert out["work"] == []  # WORK pauses everything
    assert out["afterWork"] and out["afterWork"][0][0] >= 47000  # after WORK the 8-15 s wait starts again
    assert {x[1] for x in out["reduced"]} <= {"wink", "sleepy", "wake"} and "sleepy" in [x[1] for x in out["reduced"]]
    assert out["same"] is True


def test_speech_unlock_mute_and_defaults_in_the_page_code():
    js = (STATIC_DIR / "guide.js").read_text(encoding="utf-8")
    pet = (STATIC_DIR / "pet.js").read_text(encoding="utf-8")
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    # on by default; the speaker button (mute) is the only off switch
    assert 'voice: load("maya-voice", "on") === "off" ? "off" : "on"' in js
    for key in ('load("maya-speak-steps", "1")', 'load("maya-speak-hover", "1")', 'load("maya-guide-speak-answers", "1")'):
        assert key in js, key
    # first gesture anywhere (capture, once) unlocks; until then only the latest line waits
    assert 'for (const ev of ["pointerdown", "keydown", "touchstart"]) document.addEventListener(ev, unlock, { once: true, capture: true });' in js
    assert "navigator.userActivation && navigator.userActivation.hasBeenActive" in js
    assert "if (!gesture) { pendingSpeech = { text: text" in js and "silent.volume = 0;" in js
    assert "askVoice" not in js  # no "turn on voice" question blocks the guide any more
    # muted: no audio, but the bubble, expressions and a timed mouth keep going
    assert "else briefTalk(text, true);" in js
    # hover: capture-phase delegation on document from startup, the wide selector, generic labels
    assert '"button, a, [role=button], [role=tab], input, select, summary, label, .finding-card, .tab, "' in js
    assert js.count("}, true);") >= 2 and "accessibleName(el)" in js
    assert "now - (lastHoverSpoken[id] || 0) > 30000" in js and "if (hoverSpeaking) { hoverSpeaking = false; stopSpeech(); }" in js
    # chips: clickable above the page, focusable bubble, the level chip continues the guide with a happy face
    assert ".pet-bubble {" in css and "pointer-events: auto;" in css.split(".pet-bubble {", 1)[1].split("}", 1)[0]
    assert "bubble.tabIndex = 0;" in pet
    level = js.split("function setLevel(level) {", 1)[1].split("\n  }\n", 1)[0]
    assert '{ expression: "happy"' in level and "refresh();" in level and "askVoice" not in level


def test_the_pet_and_its_guide_run_on_every_page():
    for p in STATIC_DIR.glob("*.html"):
        if p.name == "pet-sheet.html":
            continue
        page = p.read_text(encoding="utf-8")
        for script in ("pet-lines.js", "pet-brain.js", "pet.js", "guide.js"):
            assert f'<script src="/static/{script}" defer></script>' in page, (p.name, script)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_real_input_browser_check():
    """scripts/pet_check.py drives headless Chrome with real mouse and key events (skipped without Chrome)."""
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "pet_check.py")], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600, env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    if r.returncode == 2 and "chrome.exe not found" in r.stdout:
        pytest.skip("Chrome is not installed")
    assert r.returncode == 0 and "ALL PET CHECKS PASSED" in r.stdout, r.stdout[-3000:]
