"""Calm mode (no walking, no idle antics; bubble and speech stay) and one visible mascot on the sign-in page."""
import json
import shutil
import subprocess

import pytest

from src.web.server import STATIC_DIR

NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_calm_turns_off_roaming_and_loops_and_leaves_the_rest():
    r = subprocess.run([NODE, "-e", "const p = require(process.argv[1]); console.log(JSON.stringify({"
                        "calm: p.behavior(false, false, true), normal: p.behavior(false, false),"
                        "reducedCalm: p.behavior(true, false, true), mobileCalm: p.behavior(false, true, true)}));",
                        str(STATIC_DIR / "pet.js")], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["calm"] == {"roam": False, "loops": False, "drag": True}  # still draggable by hand
    assert out["normal"] == {"roam": True, "loops": True, "drag": True}  # unchanged without calm
    assert out["reducedCalm"] == {"roam": False, "loops": False, "drag": True}
    assert out["mobileCalm"] == {"roam": False, "loops": False, "drag": False}


def test_calm_is_a_saved_browser_setting_wired_to_the_live_pet():
    guide = (STATIC_DIR / "guide.js").read_text(encoding="utf-8")
    pet = (STATIC_DIR / "pet.js").read_text(encoding="utf-8")
    page = (STATIC_DIR / "chat.html").read_text(encoding="utf-8")
    assert '<input id="pet-calm" type="checkbox">' in page
    assert 'calm: load("maya-pet-calm", "0") === "1",' in guide  # off by default, stored only in this browser
    assert "window.MayaPet.live({ mobile: mobile, reduced: reduced, calm: prefs.calm," in guide
    assert 'save("maya-pet-calm", prefs.calm ? "1" : "0");' in guide and "pet.setCalm(prefs.calm)" in guide
    assert "if (!idle || drag || calm) return;" in pet  # no antics while calm
    assert "if (calm && !atHome) goHome(false);" in pet  # turning it on brings the pet home
    # the bubble and speech are untouched: say() and the speech path never look at calm
    say = pet.split("function say(", 1)[1].split("\n    }\n", 1)[0]
    assert "calm" not in say


def test_one_visible_mascot_on_the_sign_in_page():
    signin = (STATIC_DIR / "signin.html").read_text(encoding="utf-8")
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert signin.count('id="hero-pet"') == 1 and '<body class="auth-page">' in signin
    # the live pet's figure is hidden there; its bubble and mute/hide buttons stay (speech still works)
    assert ".auth-page .maya-pet .maya-pet-figure { visibility: hidden; }" in css
    assert ".auth-page .pet-bubble" not in css and ".auth-page .pet-tools" not in css
