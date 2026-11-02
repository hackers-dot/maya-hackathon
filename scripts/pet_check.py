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

from ui_screens import find_chrome, start_chrome, stop_chrome  # noqa: E402
from src.web.server import make_server  # noqa: E402

RECORDER = """(() => {
  window.__speak = []; window.__cancel = 0;
  const s = window.speechSynthesis;
  if (!s) return;
  s.speak = (u) => { window.__speak.push({ t: Math.round(performance.now()), text: u.text, volume: u.volume,
    voice: u.voice ? u.voice.name : null }); };
  s.cancel = () => { window.__cancel += 1; };
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
        c.ok(hint == "Click anywhere and I'll start talking.", "unlock hint shown before any gesture", hint)
        c.ok(ev(speaks) == [], "nothing spoken before a gesture", ev(speaks))
        click(640, 690)  # an empty spot: a real pointerdown
        time.sleep(0.6)
        said = " ".join(ev(speaks))  # one line is spoken as sentence-sized utterances (Chrome cuts long ones)
        greeting = "Hi! I'm MAYA. I'll help you understand and check your website's security, one small step at a time."
        c.ok(said == greeting, "first gesture speaks the greeting exactly once", said)
        c.ok(ev("window.__speak.some((s) => s.volume === 0)"), "a silent utterance unlocked audio in the gesture")
        c.ok(ev("document.querySelector('.pet-bubble-hint') === null"), "hint removed after unlock")

        # 5: the "I'm new" chip with a real mouse click, then the audience question
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
        c.ok(expr_after == "happy" or expr_after == "curious", "expression after choosing the level", expr_after)
        c.ok(line_after and line_after.startswith("One more thing: what do you run?"), "next: what do you run?", line_after)
        chips = ev("Array.from(document.querySelectorAll('.pet-chip')).map((x) => x.textContent)")
        c.ok(chips == ["Shop or small business", "Company or team", "Clinic or healthcare practice", "Developer", "Something else"],
             "audience chips", chips)
        shop = ev("(() => { const c = Array.from(document.querySelectorAll('.pet-chip')).find((x) => x.textContent === 'Shop or small business'); const r = c.getBoundingClientRect(); return [Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2)]; })()")
        click(*shop)
        time.sleep(0.5)
        c.ok(ev("localStorage.getItem('maya-guide-audience')") == "shop", "audience chip saved the audience", "shop")
        c.ok(ev(expression) == "happy", "happy after choosing what you run", ev(expression))
        line_after = ev(bubble)
        c.ok(line_after and line_after.startswith("First, sign in"), "the guide continued at once (next line)", line_after)
        spoken_now = " ".join(ev(speaks))
        c.ok(spoken_now.endswith("First, sign in, or create an account with the tab above. Then I'll guide you step by step."),
             "the next line was spoken", spoken_now[-100:])

        # 6: hover explanations on 15 controls across three screens
        hovered = []

        def hover(selector, rest=1.3):
            ev(f"(() => {{ const n = document.querySelector({json.dumps(selector)}); if (n) n.scrollIntoView({{block: 'nearest'}}); return 1; }})()")
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
        # a company account with consent sees the sidebar connectors; one demo asset gives an asset item to hover
        ev("""(async () => { const s = await (await fetch('/api/session')).json();
          const h = {'Content-Type': 'application/json', 'X-CSRF-Token': s.csrf};
          for (const [path, body] of [['/api/profile', {account_type: 'company'}], ['/api/asset', {asset: 'website'}],
                                      ['/api/connect', {connect: true}], ['/api/consent', {allow: true}], ['/api/assets', {demo: true}]])
            await fetch(path, {method: 'POST', headers: h, body: JSON.stringify(body)});
          return 1; })()""")
        goto("/chat")
        click(700, 700)
        time.sleep(0.4)
        for sel in ["#new-chat", "#pet-call", "#pet-settings-btn", "#data-link", "#signout",
                    ".suggestion:nth-of-type(1)", ".suggestion:nth-of-type(2)", "#investigate-btn",
                    "[data-guide=conn-website]", "#conn-code", "#conn-logs", "#conn-msg", "#conn-github", "#conn-watch",
                    "#add-asset", "[data-guide=brand-home]", "#theme-toggle", "[data-guide=asset-item]"]:
            hover(sel)
        c.ok(len(hovered) >= 25, "hovered at least 25 controls across sign-in, onboarding and chat", len(hovered))
        # every hover line was a real sentence: no "no notes" fallback and no text glued from child elements
        texts = ev("window.__speak.map((s) => s.text).join(' | ')")
        c.ok("no notes" not in texts and "Websiteworks" not in texts, "no useless fallback, no glued labels", "")
        # a click reacts to what happened: the Website connector line is said on the next page
        click(*center("[data-guide=conn-website]"))
        time.sleep(3.0)
        said = " ".join(ev(speaks)[-3:])
        c.ok("This is where you connect a website you own." in (ev(bubble) or "") + said,
             "clicking the Website connector: the pet explains what this page is for", (ev(bubble) or "")[:90])
        goto("/chat")
        click(700, 700)
        time.sleep(0.4)

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
        english = ev("window.__speak.map((s) => s.text).join(' ')")
        c.ok(not __import__("re").search(r"[\u0900-\u097F]|\b(hai|nahi|karo|aapki)\b", english),
             "everything the pet said was English", english[-80:])

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
            stop_chrome(proc)
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
