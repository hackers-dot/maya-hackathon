"""Pet v2: compact pet with a small bubble, female voice selection, Hinglish/Devanagari lines, settings page."""
import json
import re
import shutil
import subprocess

import pytest

from src.guide import LANGS, LEVELS, STATES, step
from src.owner_summary import FORBIDDEN
from src.web.server import STATIC_DIR

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
DEVANAGARI = re.compile(r"[ऀ-ॿ]")
TIME_OR_COST = re.compile(r"(?i)\d+\s*(?:s|sec|seconds?|min|minutes?|hours?|hrs?|days?|weeks?|months?|years?|%|usd|inr)\b"
                          r"|[$₹€]\s*\d")

from test_guide import FACTS  # noqa: E402


def node(script: str, *files: str):
    r = subprocess.run([NODE, "-e", script] + [str(STATIC_DIR / f) for f in files], capture_output=True, text=True,
                       encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@needs_node
def test_voice_selection_prefers_female_never_defaults_to_male():
    out = node("""
const g = require(process.argv[1]);
const V = (name, lang, local) => ({ name, lang, voiceURI: name, localService: local !== false });
const win = [V('Microsoft David - English (United States)', 'en-US'), V('Microsoft Ravi - English (India)', 'en-IN'),
  V('Microsoft Heera - English (India)', 'en-IN'), V('Microsoft Zira - English (United States)', 'en-US'),
  V('Google हिन्दी', 'hi-IN', false), V('Google UK English Male', 'en-GB', false), V('Google UK English Female', 'en-GB', false)];
const pick = (list, lang, saved) => { const p = g.pickVoice(list, lang, saved); return [p.voice ? p.voice.name : null, p.kind]; };
console.log(JSON.stringify({
  en: pick(win, 'en'),
  hinglish: pick(win, 'hinglish'),
  hiFemale: pick(win.concat([V('Microsoft Swara - Hindi (India)', 'hi-IN')]), 'hinglish'),
  onlyMale: pick([V('Microsoft David', 'en-US'), V('Microsoft Ravi', 'en-IN'), V('Hemant', 'hi-IN')], 'en'),
  neutral: pick([V('Microsoft Mark', 'en-US'), V('Google US English', 'en-US', false)], 'en'),
  none: pick([], 'en'),
  saved: pick(win, 'en', 'Microsoft David - English (United States)'),
  savedGone: pick(win, 'en', 'missing-uri'),
  usFemale: pick([V('Microsoft Zira', 'en-US'), V('Microsoft David', 'en-US')], 'en'),
  femaleWord: [g.isFemale({name: 'Google UK English Female'}), g.isMale({name: 'Google UK English Female'}),
               g.isMale({name: 'Google UK English Male'})],
  spokenHi: g.spokenText('Roman', 'देवनागरी', {lang: 'hi-IN'}),
  spokenEn: g.spokenText('Roman', 'देवनागरी', {lang: 'en-IN'}),
  spokenNone: g.spokenText('Roman', 'देवनागरी', null),
}));
""", "guide.js")
    assert out["en"] == ["Microsoft Heera - English (India)", "female"]          # en-IN female first
    assert out["hinglish"] == ["Microsoft Heera - English (India)", "female"]    # no female hi-IN: en-IN female
    assert out["hiFemale"] == ["Microsoft Swara - Hindi (India)", "female"]      # hi-IN female for Hinglish
    assert out["onlyMale"] == [None, "none"]                                     # never a male default
    assert out["neutral"] == ["Google US English", "neutral"]                    # not male-sounding: allowed
    assert out["none"] == [None, "none"]
    assert out["saved"] == ["Microsoft David - English (United States)", "saved"]  # the user's own pick wins
    assert out["savedGone"][1] == "female"
    assert out["usFemale"] == ["Microsoft Zira", "female"]
    assert out["femaleWord"] == [True, False, True]
    assert (out["spokenHi"], out["spokenEn"], out["spokenNone"]) == ("देवनागरी", "Roman", "Roman")


@needs_node
def test_bubble_never_covers_the_pet_or_the_target_and_stays_on_screen():
    out = node("""
const p = require(process.argv[1]);
const vp = { w: 1280, h: 720 };
const pet = { x: 1190, y: 620, w: 64, h: 80 };
const size = { w: 260, h: 90 };
const target = { x: 900, y: 600, w: 200, h: 120 };
const free = p.placeBubble(pet, size, vp, []);
const avoid = p.placeBubble(pet, size, vp, [target]);
const corner = p.placeBubble({ x: 4, y: 4, w: 64, h: 80 }, size, vp, []);
const sendBtn = { x: 1135, y: 630, w: 40, h: 40 };
const soft = p.placeBubble(pet, size, vp, [], [sendBtn]);  // keeps a nearby control free when it can
const box = (r) => ({ x: r.x, y: r.y, w: size.w, h: size.h });
console.log(JSON.stringify({ free, avoid, corner, softFree: !p.overlaps(box(soft), sendBtn),
  freeOverPet: p.overlaps(box(free), pet), avoidOverTarget: p.overlaps(box(avoid), target),
  inside: [free, avoid, corner].every((r) => r.x >= 8 && r.y >= 8 && r.x + size.w <= vp.w - 8 && r.y + size.h <= vp.h - 8) }));
""", "pet.js")
    assert out["free"]["side"] == "left" and not out["free"]["covers"] and not out["freeOverPet"]
    assert not out["avoid"]["covers"] and not out["avoidOverTarget"]
    assert out["inside"] is True and out["softFree"] is True


@needs_node
def test_fixed_lines_are_complete_reviewed_and_clean():
    out = node("const l = require(process.argv[1]); console.log(JSON.stringify(l));", "pet-lines.js")
    for name, x in out["LINES"].items():
        assert x["en"] and x["hinglish"] and x["en"] != x["hinglish"], name
        assert DEVANAGARI.search(x["spoken_hi"]) and not DEVANAGARI.search(x["hinglish"]), name
        for text in (x["en"], x["hinglish"]):
            assert not FORBIDDEN.search(text), (name, text)
            assert not TIME_OR_COST.search(text), (name, text)
    for name, x in out["CHIPS"].items():
        assert x["en"] and x["hinglish"], name
    assert out["LINES"]["greeting"]["hinglish"].startswith("Hii! Main MAYA hoon")
    assert [out["CHIPS"][k]["en"] for k in ("levelNew", "levelSome", "levelTech")] == ["I'm new", "I know some", "I'm technical"]
    src = (STATIC_DIR / "pet-lines.js").read_text(encoding="utf-8")
    assert "NEED NATIVE-SPEAKER REVIEW" in src


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("level", LEVELS)
def test_every_hinglish_line_has_a_spoken_devanagari_version(state, level):
    s = step(state, level, "hinglish", FACTS[state])
    assert s["spoken_hi"] and DEVANAGARI.search(s["spoken_hi"]) and "{" not in s["spoken_hi"]
    assert not DEVANAGARI.search(s["line"])  # the screen always shows Roman text
    assert step(state, level, "en", FACTS[state])["spoken_hi"] is None


def test_hinglish_is_first_person_feminine():
    lines = " ".join(step(s, "new", "hinglish", FACTS[s])["line"] for s in STATES)
    assert re.search(r"\bkarti hoon\b|\bdekhungi\b|\bkarungi\b|\bpoochti hoon\b|\bkar rahi hoon\b", lines)
    assert not re.search(r"\bkarta hoon\b|\bdekhunga\b|\bkarunga\b|\bkar raha hoon\b", lines)  # never masculine "I"
    assert "LANGS" and set(LANGS) == {"en", "hinglish"}


def test_fixed_templates_never_carry_the_model_badge():
    for name in ("guide.js", "pet-lines.js", "pet.js"):
        assert "unverified model text" not in (STATIC_DIR / name).read_text(encoding="utf-8"), name


def test_pet_surface_has_only_mute_and_hide_and_settings_live_on_their_own_page():
    js = (STATIC_DIR / "pet.js").read_text(encoding="utf-8")
    assert js.count('tool("pet-') == 2 and 'tool("pet-mute"' in js and 'tool("pet-hide-btn"' in js
    guide = (STATIC_DIR / "guide.js").read_text(encoding="utf-8")
    for gone in ('el("select"', 'type = "range"', "guide-settings", "guide-ask"):
        assert gone not in guide, gone  # no settings or text box on the pet any more
    page = (STATIC_DIR / "chat.html").read_text(encoding="utf-8")
    view = page.split('<section id="pet-view"', 1)[1].split('<section id="data-view"', 1)[0]
    for control in ("pet-level", "pet-lang", "pet-voice", "pet-voice-preview", "pet-rate", "pet-pitch",
                    "pet-speak-steps", "pet-speak-answers", "pet-mic", "pet-help", "pet-voice-test", "pet-reset",
                    "pet-voice-status", "pet-learn-note"):
        assert f'id="{control}"' in view, control
    assert 'id="pet-settings-btn"' in page and 'id="pet-call"' in page
    assert re.search(r'load\("maya-voice-pitch", "1\.2"\)', guide) and re.search(r'load\("maya-voice-rate", "1"\)', guide)
    assert 'id="pet-help" type="checkbox">' in view and 'load("maya-pet-help", "0")' in guide  # off by default


def test_compact_sizes():
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert re.search(r"\.maya-pet \{\s*position: absolute; left: 0; top: 0; width: 64px; height: 80px;", css)
    assert ".maya-pet.is-mobile { width: 45px; height: 56px;" in css
    assert "max-width: min(260px, calc(100vw - 32px))" in css
