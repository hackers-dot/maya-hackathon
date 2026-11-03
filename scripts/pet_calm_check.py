"""Real-input check of Calm mode and the single sign-in mascot (headless Chrome, throwaway data dir, no model).

Sign-in: exactly one visible mascot (the hero); the live pet's bubble and speech still work.
Chat, Calm off: the live pet is visible and walks to a hovered control.
Calm on (a real click on the Pet settings checkbox): the pet stays home when a control is hovered, the bubble and
the speech call still happen, no idle antic in 20 s, and the eyes still blink.
Run: .venv\\Scripts\\python scripts\\pet_calm_check.py
"""
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from pet_review import RECORDER, Walker  # noqa: E402
from ui_screens import find_chrome, start_chrome, stop_chrome  # noqa: E402
from src.web.server import make_server  # noqa: E402

FIGURE_VISIBLE = """(() => { const f = document.querySelector('#maya-pet .maya-pet-figure');
  return !!f && getComputedStyle(f).visibility === 'visible' && f.getBoundingClientRect().width > 0; })()"""
PET_POS = "(() => { const h = document.getElementById('maya-pet'); const r = h.getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y)]; })()"


def main() -> int:
    chrome = find_chrome()
    if chrome is None:
        print("chrome.exe not found")
        return 2
    data_dir = Path(tempfile.mkdtemp(prefix="maya-calm-"))
    profile = Path(tempfile.mkdtemp(prefix="maya-calm-chrome-"))
    server = make_server(port=0, data_dir=data_dir, provider_factory=lambda: (None, "none"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    failed = 0
    proc = cdp = None

    def ok(cond, label, detail=""):
        nonlocal failed
        failed += not cond
        print(f"{'PASS' if cond else 'FAIL'}  {label}  [{detail}]", flush=True)

    try:
        proc, _, cdp = start_chrome(chrome, profile)
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Page.addScriptToEvaluateOnNewDocument", source=RECORDER)
        cdp.call("Emulation.setDeviceMetricsOverride", width=1280, height=800, deviceScaleFactor=1, mobile=False)
        cdp.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "no-preference"}])
        w = Walker(cdp, base)
        ev = w.ev

        def at(sel):  # scroll instantly (motion is on, so the page would scroll smoothly), then measure
            ev("(() => { const n = document.querySelector(%r); if (n) n.scrollIntoView({block: 'center', behavior: 'instant'}); return 1; })()" % sel)
            time.sleep(0.4)
            pos = ev("(() => { const r = document.querySelector(%r).getBoundingClientRect(); return r.width ? [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)] : null; })()" % sel)
            assert pos, f"{sel} is not visible"
            return pos

        def press(sel, wait=0.8):  # a real mouse click
            x, y = at(sel)
            w.mouse("mouseMoved", x, y)
            w.mouse("mousePressed", x, y, 1, "left", 1)
            w.mouse("mouseReleased", x, y, 0, "left", 1)
            time.sleep(wait)

        def rest_on(sel, wait=2.0):  # a real hover; the guide ignores hovers for 1.5 s after a click
            time.sleep(1.7)
            x, y = at(sel)
            w.mouse("mouseMoved", x - 3, y)
            w.mouse("mouseMoved", x, y)
            time.sleep(wait)
        # ---------- sign-in: one visible mascot ----------
        w.goto("/signin", 1.0)
        ev("localStorage.clear(); localStorage.setItem('maya-guide-level', 'new'); localStorage.setItem('maya-guide-audience', 'shop');"
           " localStorage.setItem('maya-greeted', '1'); 1")
        w.goto("/signin", 2.0)
        mascots = ev("""(() => { const hero = document.querySelector('#hero-pet svg');
          const heroVisible = !!hero && hero.getBoundingClientRect().width > 0 && getComputedStyle(hero).visibility === 'visible';
          return {hero: heroVisible, live: %s}; })()""" % FIGURE_VISIBLE)
        ok(mascots["hero"] and not mascots["live"], "sign-in: exactly one visible mascot (the hero)", str(mascots))
        w.click("body", 0.4)  # first gesture
        w.hover("sign-in", "#tab-signup", 1.2)
        ok(bool(w.bubble()) or len(ev("window.__speak")) > 0, "sign-in: the bubble and speech still work",
           (w.bubble() or "")[:60] + f" | speech calls {len(ev('window.__speak'))}")
        ok(ev("!!document.querySelector('#maya-pet .pet-mute')"), "sign-in: the mute button is still there")
        # ---------- a company user with the demo app ----------
        # a fresh CSRF token per call: signing up starts a new session with a new token
        setup = ev("""(async () => { const out = [];
          for (const [p, b] of [['/api/signup', {email: 'calm@shop.example', password: 'calm-password-123'}], ['/api/profile', {account_type: 'company'}],
                                ['/api/asset', {asset: 'website'}], ['/api/connect', {connect: true}], ['/api/consent', {allow: true}], ['/api/assets', {demo: true}]]) {
            const s = await (await fetch('/api/session')).json();
            const r = await fetch(p, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': s.csrf}, body: JSON.stringify(b)});
            out.push(p + ' ' + r.status); }
          return out; })()""")
        print("setup:", setup)
        # ---------- chat, Calm off: the pet is visible and walks to a hovered control ----------
        w.goto("/chat", 2.0)
        w.click("body", 0.3)
        ok(ev(FIGURE_VISIBLE), "chat: the live pet is visible on other pages")
        home = ev(PET_POS)
        rest_on("#new-chat")
        moved = ev(PET_POS)
        ok(moved != home, "Calm off: hovering a control makes the pet walk to it", f"home {home} -> {moved}")
        w.mouse("mouseMoved", 5, 5)
        time.sleep(0.5)
        # ---------- Calm on, by a real click on the Pet settings checkbox ----------
        press("#pet-settings-btn", 1.0)
        press("#pet-calm", 0.8)
        stored = ev("localStorage.getItem('maya-pet-calm')")
        ok(stored == "1" and ev("document.getElementById('maya-pet').classList.contains('is-docked')"),
           "Calm on: saved in this browser and the pet docks at once", f"maya-pet-calm={stored}")
        w.goto("/chat", 2.0)  # and it stays on after a reload
        w.click("body", 0.3)
        home = ev(PET_POS)
        speech_before = len(ev("window.__speak"))
        rest_on("#new-chat")
        after = ev(PET_POS)
        bubble = w.bubble()
        ok(after == home, "Calm on: hovering a control does not move the pet", f"home {home} -> {after}")
        ok(bool(bubble) and len(ev("window.__speak")) > speech_before, "Calm on: the bubble shows and the speech call happens",
           f"{bubble[:60]!r} | speech calls +{len(ev('window.__speak')) - speech_before}")
        w.mouse("mouseMoved", 5, 5)
        # 20 s idle: no antic (normally one after about 8 s), but the eyes keep blinking
        # the eyes are looked up on every tick: an expression change swaps the face set (and its eyes)
        watch = ev("""new Promise((done) => { const host = document.getElementById('maya-pet');
          let blinks = 0, was = false; const antic0 = host.getAttribute('data-antic');
          const id = setInterval(() => { const b = !!host.querySelector('.pet-eyes.is-blinking'); if (b && !was) blinks++; was = b; }, 30);
          setTimeout(() => { clearInterval(id); done({ antic: host.getAttribute('data-antic'), antic0: antic0, blinks: blinks,
            idle: host.getAttribute('data-idle') }); }, 20000); })""")
        ok(watch["antic"] == watch["antic0"], "Calm on: no idle antic in 20 s", str(watch))
        ok(watch["blinks"] >= 1, "Calm on: the pet still blinks", f"{watch['blinks']} blink(s)")
    finally:
        if cdp:
            cdp.close()
        if proc:
            stop_chrome(proc)
        server.shutdown()
        server.server_close()
        shutil.rmtree(data_dir, ignore_errors=True)
        shutil.rmtree(profile, ignore_errors=True)
    print("ALL CALM CHECKS PASSED" if not failed else f"{failed} CALM CHECK(S) FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
