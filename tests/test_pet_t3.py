"""Pet v2 tier 3: "Let the pet help me" - allowlisted actions only, never a state-changing control, Esc cancels."""
import json
import re
import shutil
import subprocess

import pytest

from src.web.server import STATIC_DIR

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def brain(script: str):
    r = subprocess.run([NODE, "-e", "const b = require(process.argv[1]);\n" + script, str(STATIC_DIR / "pet-brain.js")],
                       capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


FORBIDDEN_CONTROLS = ["approve-all-btn", "apply-btn", "retest-btn", "scan-btn", "scan-empty-btn", "reset-demo-btn",
                      "consent-allow", "verify-btn", "verify-dns-btn", "delete-btn", "delete-confirm", "disconnect-btn",
                      "signout", "up-btn", "up-file", "official-save", "reminder-btn", "approver"]


def test_every_forbidden_control_is_refused_for_every_action_and_logged():
    out = brain(f"""
const ids = {json.dumps(FORBIDDEN_CONTROLS)};
const res = [];
for (const id of ids) for (const action of b.HELP_ACTIONS) {{
  const v = b.checkAction({{helpOn: true, action, id, tag: 'BUTTON', classes: ['btn'], value: 'owner', isFinding: false}});
  res.push([id, action, v.ok, b.logLine(v.ok, action, '#' + id, v.reason)]);
}}
console.log(JSON.stringify(res));
""")
    assert len(out) == len(FORBIDDEN_CONTROLS) * 7
    for id_, action, ok, log in out:
        assert ok is False, (id_, action)
        assert log.startswith("Pet refused: " + action + " #" + id_) and "never" in log, log


def test_allowlist_only_and_off_by_default():
    out = brain("""
const c = (x) => b.checkAction(Object.assign({helpOn: true, id: '', tag: 'DIV', classes: []}, x));
console.log(JSON.stringify({
  off: b.checkAction({helpOn: false, action: 'scroll', id: 'findings-panel', tag: 'SECTION'}),
  scroll: c({action: 'scroll', id: 'findings-panel', tag: 'SECTION'}),
  highlight: c({action: 'highlight', id: 'score-card', tag: 'SECTION'}),
  expand: c({action: 'expand', tag: 'DETAILS', isFinding: true, classes: ['finding']}),
  expandOther: c({action: 'expand', tag: 'DETAILS', isFinding: false}),
  collapse: c({action: 'collapse', tag: 'DETAILS', isFinding: true}),
  role: c({action: 'role', value: 'technical'}), roleBad: c({action: 'role', value: 'admin'}),
  lang: c({action: 'summary-lang', value: 'hinglish'}), langBad: c({action: 'summary-lang', value: 'fr'}),
  panel: c({action: 'open-panel', tag: 'DETAILS', isReadOnlyDetails: true}),
  panelForm: c({action: 'open-panel', tag: 'DETAILS', isReadOnlyDetails: false}),
  click: c({action: 'click', id: 'findings-panel', tag: 'SECTION'}), submit: c({action: 'submit', tag: 'FORM'}),
  type: c({action: 'type', tag: 'INPUT'}), input: c({action: 'scroll', tag: 'INPUT', id: 'some-field'}),
  sendClass: c({action: 'highlight', tag: 'BUTTON', classes: ['send']}),
  actions: b.HELP_ACTIONS,
}));
""")
    assert out["off"] == {"ok": False, "reason": "help is off"}
    for k in ("scroll", "highlight", "expand", "collapse", "role", "lang", "panel"):
        assert out[k]["ok"] is True, k
    for k in ("expandOther", "roleBad", "langBad", "panelForm", "click", "submit", "type", "input", "sendClass"):
        assert out[k]["ok"] is False, k
    assert out["actions"] == ["scroll", "expand", "collapse", "role", "summary-lang", "highlight", "open-panel"]


def test_every_state_changing_control_in_the_registry_is_on_the_never_list():
    out = brain("console.log(JSON.stringify({g: b.G, ids: b.NEVER_IDS, classes: b.NEVER_CLASSES}));")
    harmless = {"reads", "view", "nowhere", "field"}  # fields: checkAction refuses every INPUT/TEXTAREA/SELECT anyway
    for key, e in out["g"].items():
        if e["e"] not in harmless:
            assert key in out["ids"], key
    for id_ in FORBIDDEN_CONTROLS:
        assert id_ in out["ids"], id_
    from test_pet_t2 import page_keys
    keys = page_keys()
    for id_ in out["ids"]:
        assert id_ in keys, id_


def test_esc_or_a_manual_click_cancels_pending_actions():
    out = brain("""
const timers = new Map(); let next = 1; const ran = [];
const setT = (fn) => { const id = next++; timers.set(id, fn); return id; };
const clearT = (id) => timers.delete(id);
const q = b.ActionQueue(setT, clearT);
q.add(() => ran.push('a')); q.add(() => ran.push('b'));
const cancelled = q.cancel();
for (const fn of timers.values()) fn();  // a timer that was cancelled never runs
const q2 = b.ActionQueue(setT, clearT);
q2.add(() => ran.push('c'));
for (const [id, fn] of Array.from(timers)) { timers.delete(id); fn(); }
console.log(JSON.stringify({cancelled, ran, left: q2.size(), again: q.cancel()}));
""")
    assert out == {"cancelled": 2, "ran": ["c"], "left": 0, "again": 0}


def test_the_page_side_never_clicks_and_logs_every_action():
    guide = (STATIC_DIR / "guide.js").read_text(encoding="utf-8")
    page = (STATIC_DIR / "chat.html").read_text(encoding="utf-8")
    for bad in (".click()", ".submit(", "requestSubmit", "dispatchEvent(new MouseEvent", 'new Event("click"',
                ".value = name", "approver\").value ="):
        assert bad not in guide, bad
    act = guide.split("function act(action, el, value) {", 1)[1].split("\n  }\n", 1)[0]
    assert "b.checkAction(" in act and "logAct(b.logLine(" in act and "if (!verdict.ok) return false;" in act
    performed = set(re.findall(r'action === "([a-z-]+)"', act))
    assert performed <= {"scroll", "expand", "collapse", "open-panel", "highlight", "role", "summary-lang"}
    assert 'stopActions("Esc")' in guide and 'stopActions("you clicked")' in guide
    assert 'load("maya-pet-help", "0")' in guide  # off until the user turns it on
    consent = page.split('id="pet-help-consent"', 1)[1].split("</div>\n<h3", 1)[0]
    for words in ("never presses Approve", "Delete my data", "Sign out", "types your name", "Esc"):
        assert words in consent, words
    assert 'id="pet-log"' in page and 'id="pet-help-allow"' in page
