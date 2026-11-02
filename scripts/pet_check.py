"""Real-input check of the MAYA pet in headless Chrome (DevTools protocol, no new packages).

Starts the web app on a free 127.0.0.1 port with a throwaway data directory (deterministic scans, no model),
drives one headless Chrome with REAL input events (Input.dispatchMouseEvent / dispatchKeyEvent), and records
speechSynthesis.speak/cancel calls with a page-side recorder injected for this check only (no sound is played).
Checks: the unlock hint, the first-gesture greeting, level chips by mouse and keyboard, hover explanations on
15 controls across sign-in, onboarding and chat (bubble text + one spoken line each, the 30 s repeat rule and the
cancel rule), mute and unmute, and 30 s of idle (virtual time) producing antics. Prints a raw log; exit 1 on
any failed check. Run: .venv\\Scripts\\python scripts\\pet_check.py
"""
import json
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from ui_screens import find_chrome, start_chrome  # noqa: E402
from src.web.server import make_server  # noqa: E402

RECORDER = """(() => {
  window.__speak = []; window.__cancel = 0;
  const s = window.speechSynthesis;
  if (!s) return;
  s.speak = (u) => { window.__speak.push({ t: Math.round(performance.now()), text: u.text, volume: u.volume,
    voice: u.voice ? u.voice.name : null }); };
  s.cancel = () => { window.__cancel += 1; };
  try { localStorage.setItem('maya-guide-lang', 'hinglish'); } catch (e) {}
})();"""


class Check:
    def __init__(self):
        self.failures = 0

    def ok(self, cond, label, detail=""):
        self.failures += 0 if cond else 1
        print(f"{'PASS' if cond else 'FAIL'}  {label}  [{detail}]")


def main() -> int:
    chrome = find_chrome()
    if chrome is None:
        print("chrome.exe not found; pet check skipped")
        return 2
    data_dir = Path(tempfile.mkdtemp(prefix="maya-pet-check-"))
    profile = Path(tempfile.mkdtemp(prefix="maya-pet-check-chrome-"))
    server = make_server(port=0, data_dir=data_dir, provider_factory=lambda: (None, "none"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    proc = cdp = None
    c = Check()
    try:
        proc, port, cdp = start_chrome(chrome, profile)
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Page.addScriptToEvaluateOnNewDocument", source=RECORDER)
        cdp.call("Emulation.setDeviceMetricsOverride", width=1280, height=720, deviceScaleFactor=1, mobile=False)
        cdp.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "no-preference"}])

        def ev(expr):
            r = cdp.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
            return r.get("result", {}).get("value", r.get("exceptionDetails"))

        def mouse(kind, x, y, buttons=0, button="none", clicks=0):
            cdp.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button=button, buttons=buttons, clickCount=clicks)

        def click(x, y):
            mouse("mouseMoved", x, y)
            mouse("mousePressed", x, y, 1, "left", 1)
            mouse("mouseReleased", x, y, 0, "left", 1)

        def goto(path, wait=3.0):
            cdp.events.clear()
            cdp.call("Page.navigate", url=base + path)
            cdp.wait_event("Page.loadEventFired")
            time.sleep(wait)

        def center(selector):
            return ev(f"""(() => {{ const n = document.querySelector({json.dumps(selector)}); if (!n) return null;
              const r = n.getBoundingClientRect(); return [Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2)]; }})()""")

        bubble = "(() => { const b = document.getElementById('pet-bubble'); return b && !b.hidden ? b.querySelector('.pet-bubble-text').textContent : null; })()"
        speaks = "window.__speak.filter((s) => s.text.trim()).map((s) => s.text)"
        expression = "document.querySelector('#maya-pet .maya-pet-figure').getAttribute('data-expression')"

        # 1-4: sign-in page, the hint, then the first gesture speaks the pending greeting once
        goto("/signin")
        print("bubble:", ev(bubble))
        hint = ev("(() => { const h = document.querySelector('.pet-bubble-hint'); return h ? h.textContent : null; })()")
        c.ok(hint == "Kahin bhi click karo, main bolna shuru karti hoon.", "unlock hint shown before any gesture", hint)
        c.ok(ev(speaks) == [], "nothing spoken before a gesture", ev(speaks))
        click(640, 690)  # an empty spot: a real pointerdown
        time.sleep(0.6)
        said = " ".join(ev(speaks))  # one line is spoken as sentence-sized utterances (Chrome cuts long ones)
        greeting = "Hii! Main MAYA hoon. Aapki website ki security samjhane aur check karne mein main help karungi."
        c.ok(said == greeting, "first gesture speaks the greeting exactly once", said)
        c.ok(ev("window.__speak.some((s) => s.volume === 0)"), "a silent utterance unlocked audio in the gesture")
        c.ok(ev("document.querySelector('.pet-bubble-hint') === null"), "hint removed after unlock")

        # 5: the "Bilkul new" chip with a real mouse click
        chips = ev("Array.from(document.querySelectorAll('.pet-chip')).map((x) => x.textContent)")
        print("chips:", chips)
        pos = center(".pet-chip")
        top = ev(f"(() => {{ const n = document.elementFromPoint({pos[0]}, {pos[1]}); return n ? n.className : null; }})()")
        c.ok(top == "pet-chip", "the chip is on top at its centre", top)
        click(*pos)
        time.sleep(0.5)
        expr_after = ev(expression)
        line_after = ev(bubble)
        c.ok(ev("localStorage.getItem('maya-guide-level')") == "new", "chip saved the level", "new")
        c.ok(expr_after == "happy", "expression after choosing the level", expr_after)
        c.ok(line_after and line_after.startswith("Pehle sign in karo"), "the guide continued at once (next line)", line_after)
        spoken_now = " ".join(ev(speaks))
        c.ok(spoken_now.endswith("Pehle sign in karo, ya upar wale tab se account banao. Phir main step by step guide karungi."),
             "the next line was spoken", spoken_now[-100:])

        # 6: hover explanations on 15 controls across three screens
        hovered = []

        def hover(selector, rest=1.3):
            pos = center(selector)
            if not pos:
                c.ok(False, "control present", selector)
                return
            before = len(ev("window.__speak"))
            cancels = ev("window.__cancel")
            mouse("mouseMoved", *pos)
            time.sleep(rest)
            text = ev(bubble)
            new = 1 if ev("window.__speak").__len__() > before else 0  # one line (possibly in sentence pieces)
            hovered.append(selector)
            spoke = ev("window.__speak.length ? window.__speak[window.__speak.length - 1].text : ''")
            c.ok(bool(text) and new == 1, f"hover {selector}: bubble text and one spoken line",
                 f"{text!r} | spoke {new}: {spoke[:60]!r} | cancel +{ev('window.__cancel') - cancels}")

        for sel in ["#tab-signin", "#tab-signup", "#signin-email", "#signin-password", "#theme-toggle"]:
            hover(sel)
        before = len(ev("window.__speak"))
        hover_pos = center("#tab-signin")
        mouse("mouseMoved", *hover_pos)
        time.sleep(1.2)
        c.ok(len(ev("window.__speak")) == before and ev(bubble), "same control within 30 s: bubble shown, not spoken again",
             ev(bubble))
        mouse("mouseMoved", 640, 690)
        time.sleep(1.2)
        c.ok(ev("window.__cancel") >= 1, "speech cancelled when the pointer left / moved on", ev("window.__cancel"))

        ev("""(async () => { const s = await (await fetch('/api/session')).json();
          const h = {'Content-Type': 'application/json', 'X-CSRF-Token': s.csrf};
          await fetch('/api/signup', {method: 'POST', headers: h, body: JSON.stringify({email: 'pet@shop.example', password: 'pet-check-password'})});
          return 1; })()""")
        goto("/onboarding")
        click(640, 700)
        time.sleep(0.4)
        for sel in ["#choose-individual", "#choose-company"]:
            hover(sel)
        ev("""(async () => { const s = await (await fetch('/api/session')).json();
          await fetch('/api/profile', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': s.csrf},
            body: JSON.stringify({account_type: 'individual'})}); return 1; })()""")
        goto("/chat")
        click(700, 700)
        time.sleep(0.4)
        for sel in ["#new-chat", "#pet-call", "#pet-settings-btn", "#data-link", "#signout",
                    ".suggestion:nth-of-type(1)", ".suggestion:nth-of-type(2)", "#investigate-btn"]:
            hover(sel)
        c.ok(len(hovered) >= 15, "hovered at least 15 controls across sign-in, onboarding and chat", len(hovered))

        # 7: mute (the speaker on the pet), hover again: no audio, bubble and mouth animation continue; unmute
        mute = center("#maya-pet .pet-mute")
        mouse("mouseMoved", *mute)
        time.sleep(0.3)
        click(*mute)
        time.sleep(0.4)
        c.ok(ev("localStorage.getItem('maya-voice')") == "off", "speaker button mutes (saved)", "off")
        before = len(ev("window.__speak"))
        pos = center(".suggestion:nth-of-type(3)")
        mouse("mouseMoved", *pos)
        time.sleep(0.7)
        talking = ev("document.getElementById('maya-pet').classList.contains('is-talking')")
        time.sleep(0.6)
        c.ok(len(ev("window.__speak")) == before and bool(ev(bubble)) and talking,
             "muted: no speak() call, bubble shown, mouth animates", f"bubble={ev(bubble)!r} talking={talking}")
        mouse("mouseMoved", 640, 690)  # away from the controls: the pet walks home
        time.sleep(1.6)
        mute = center("#maya-pet .pet-mute")  # the pet moved while explaining: find its speaker button again
        click(*mute)
        time.sleep(0.3)
        c.ok(ev("localStorage.getItem('maya-voice')") == "on", "speaker button unmutes (saved)", "on")
        before = len(ev("window.__speak"))
        pos = center(".suggestion:nth-of-type(4)")
        mouse("mouseMoved", *pos)
        time.sleep(1.3)
        c.ok(len(ev("window.__speak")) > before, "unmuted: speaking again", ev("window.__speak").pop()["text"][:60])

        # keyboard: a chip activated with a real Enter key
        # keyboard: bring the level chips back (no level chosen), focus the first chip, press a real Enter
        ev("localStorage.removeItem('maya-guide-level'); 1")
        goto("/chat")
        click(700, 700)
        time.sleep(0.5)
        ev("document.querySelector('.pet-chip').focus(); 1")
        label = ev("document.activeElement.textContent")
        before_text = ev(bubble)
        for kind in ("keyDown", "keyUp"):
            cdp.call("Input.dispatchKeyEvent", type=kind, key="Enter", code="Enter", windowsVirtualKeyCode=13,
                     nativeVirtualKeyCode=13, text="\r" if kind == "keyDown" else "")
        time.sleep(0.8)
        c.ok(ev("localStorage.getItem('maya-guide-level')") == "new" and ev(bubble) != before_text,
             f"keyboard: Enter on the chip {label!r} works", ev(bubble))

        # 8: idle for 30 s of virtual time: antics happen
        ev("""window.__antics = []; new MutationObserver((m) => m.forEach((x) => window.__antics.push(
          x.attributeName + '=' + x.target.getAttribute(x.attributeName)))).observe(document.getElementById('maya-pet'),
          {attributes: true, attributeFilter: ['data-antic', 'data-idle']}); 1""")
        cdp.events.clear()
        cdp.call("Emulation.setVirtualTimePolicy", policy="advance", budget=30000)
        cdp.wait_event("Emulation.virtualTimeBudgetExpired", timeout=120)
        antics = ev("window.__antics")
        print("idle log (30 s virtual):", antics)
        c.ok(sum(1 for a in antics if a.startswith("data-antic")) >= 1 and "data-idle=PLAYING" in antics,
             "idle for 30 s: the pet starts playing", len(antics))
    finally:
        if cdp:
            cdp.close()
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except Exception:
                proc.kill()
        server.shutdown()
        server.server_close()
        shutil.rmtree(data_dir, ignore_errors=True)
        for _ in range(20):
            shutil.rmtree(profile, ignore_errors=True)
            if not profile.exists():
                break
            time.sleep(0.25)
    print("ALL PET CHECKS PASSED" if not c.failures else f"{c.failures} PET CHECK(S) FAILED")
    return 1 if c.failures else 0


if __name__ == "__main__":
    sys.exit(main())
