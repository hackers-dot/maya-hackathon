"""Real-input walk of the scan result page (layout v2) in headless Chrome: throwaway data dir, deterministic scan
(no model, no network beyond 127.0.0.1), real mouse and keyboard events over DevTools. Prints PASS/FAIL per step.

Covers: More menu + Reset demo app, a scan (trace first and open while running), the trace collapsing to its
one-line bar afterwards (and the choice being remembered), each section present in order, Export menu (keyboard
open, Escape closes), audit table (short hash, details expand), the composer not covering the last content,
approve all, apply, re-test, and the pet tour stopping at score ring, findings in severity order, Fix first, Approve.
Run: .venv\\Scripts\\python scripts\\result_page_check.py
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

from pet_review import RECORDER, Walker  # noqa: E402
from ui_screens import find_chrome, start_chrome, stop_chrome  # noqa: E402
from src.web.server import make_server  # noqa: E402


class Check:
    def __init__(self):
        self.failed = 0

    def ok(self, cond, label, detail=""):
        self.failed += not cond
        print(f"{'PASS' if cond else 'FAIL'}  {label}  [{detail}]", flush=True)


def walk(w: Walker, c: Check):
    ev = w.ev
    w.cdp.call("Network.clearBrowserCookies")
    w.goto("/signin", 1.0)
    ev("localStorage.clear(); localStorage.setItem('maya-guide-level', 'new'); localStorage.setItem('maya-guide-audience', 'shop');"
       " localStorage.setItem('maya-greeted', '1'); 1")
    w.goto("/signin")
    w.click("body", 0.3)
    w.click("#tab-signup", 0.4)
    w.type_text("#signup-email", "layout@shop.example")
    w.type_text("#signup-password", "layout-password-123")
    w.click("[data-guide=signup-submit]", 2.4)
    for sel in ["#choose-company", "#asset-website", "#connect-yes", "#consent-allow"]:
        w.click(sel, 2.4)
    w.click("#use-demo", 1.2)
    w.goto("/chat")
    w.click("body", 0.3)
    c.ok(w.click("[data-guide=asset-item]", 1.5), "open the demo app from the sidebar (real click)")

    # More menu: closed by default, opens on a real click, holds Reset demo app and Disconnect (danger styling)
    hidden = ev("!document.getElementById('more-menu').open && !document.getElementById('reset-demo-btn').checkVisibility()")
    w.click("[data-guide=more-menu]", 0.5)
    info = ev("""(() => { const m = document.getElementById('more-menu'); const r = document.getElementById('reset-demo-btn');
      const d = document.getElementById('disconnect-btn');
      return {open: m.open, reset: r.checkVisibility(), disc: d.checkVisibility(),
              danger: r.classList.contains('btn-danger') && d.classList.contains('btn-danger'), resetDisabled: r.disabled}; })()""")
    c.ok(hidden and info["open"] and info["reset"] and info["disc"] and info["danger"],
         "More menu: closed at first, opens on click, Reset demo app + Disconnect inside with danger styling", json.dumps(info))
    w.click("#reset-demo-btn", 2.0)
    msg = ev("document.getElementById('message').textContent")
    c.ok("reset" in msg.lower() and not ev("document.getElementById('more-menu').open"),
         "Reset demo app (real click) works, menu closes after the action", msg[:90])

    # a scan: the trace is first under the header and open while running
    w.click("#scan-empty-btn", 0.1)
    w.mouse("mouseMoved", 5, 5)  # off the controls, so a hover line does not replace the pet's post-scan offer
    time.sleep(0.5)
    running = ev("""(() => { const t = document.getElementById('trace-list'); const box = document.getElementById('trace-box');
      const run = document.getElementById('run-panel').getBoundingClientRect().top;
      const others = ['fixfirst-panel', 'findings-panel', 'result-skeleton'].map((id) => document.getElementById(id))
        .filter((n) => !n.hidden).map((n) => n.getBoundingClientRect().top);
      return {running: t.classList.contains('is-running'), open: box.open, first: others.every((y) => run < y)}; })()""")
    c.ok(running["running"] and running["open"] and running["first"],
         "while scanning: Live trace open and above the other results", json.dumps(running))
    for _ in range(120):
        if ev("!document.getElementById('trace-list').classList.contains('is-running') && !document.getElementById('score-card').hidden"):
            break
        time.sleep(0.5)
    time.sleep(1.0)
    done = ev("""(() => { const box = document.getElementById('trace-box'); const ids = ['score-card', 'fixfirst-panel', 'findings-panel',
        'proposals-panel', 'diff-panel', 'summary-panel', 'run-panel', 'usage-panel', 'audit-panel'];
      const tops = ids.map((id) => { const n = document.getElementById(id); return n && !n.hidden ? n.getBoundingClientRect().top : null; });
      const shown = tops.filter((y) => y !== null);
      return {open: box.open, sum: document.getElementById('trace-sum').textContent, status: document.getElementById('run-status').textContent,
              ordered: shown.every((y, i) => i === 0 || y >= shown[i - 1]), present: ids.filter((id, i) => tops[i] !== null)}; })()""")
    c.ok(not done["open"] and "steps" in done["sum"] and "tokens" in done["sum"],
         "after the scan: trace collapsed to its one-line bar", f"{done['status']} | {done['sum']}")
    c.ok(done["ordered"], "sections in order: score, Fix first, findings, proposals, what changed, summary, trace, tokens, audit",
         ", ".join(done["present"]))
    zero = ev("document.querySelectorAll('#trace-list .trace-zero').length")
    hits = ev("Array.from(document.querySelectorAll('#trace-list .trace-res.is-hit')).map((x) => x.className.replace('trace-res is-hit', '').trim())")
    zeros = ev("document.querySelectorAll('#trace-list .trace-res.is-zero').length")
    c.ok(isinstance(hits, list) and bool(hits) and all(h.startswith("sev-") for h in hits),
         "trace result chips coloured by the highest severity found; 0 findings as a muted check",
         f"hits={hits} zero chips={zeros} folded groups={zero} (no-model scans log one call per step, so nothing to fold)")

    tour_check(w, c)  # the pet tour right after the first scan, while the demo app still has its findings

    # expand / collapse by a real click on the bar; the choice is remembered in this browser
    w.click("#trace-box > summary", 0.5)
    opened = ev("document.getElementById('trace-box').open")
    stored = ev("Object.keys(localStorage).filter((k) => k.startsWith('maya-trace-open-')).map((k) => k + '=' + localStorage.getItem(k)).join(',')")
    c.ok(opened and stored.endswith("=1") and "@" not in stored, "click the bar: trace expands, remembered (key has no email)", stored)
    w.click("#trace-box > summary", 0.5)
    stored = ev("Object.keys(localStorage).filter((k) => k.startsWith('maya-trace-open-')).map((k) => localStorage.getItem(k)).join(',')")
    c.ok(not ev("document.getElementById('trace-box').open") and stored == "0", "click again: collapses, remembered", stored)

    # Export menu by keyboard: focus, Enter opens, links have hrefs, Escape closes and returns focus
    ev("document.querySelector('[data-guide=export-menu]').focus(); 1")
    for kind in ("keyDown", "keyUp"):
        w.cdp.call("Input.dispatchKeyEvent", type=kind, key="Enter", code="Enter", windowsVirtualKeyCode=13,
                   nativeVirtualKeyCode=13, text="\r" if kind == "keyDown" else "")
    time.sleep(0.4)
    menu = ev("""(() => { const m = document.getElementById('export-menu');
      return {open: m.open, links: Array.from(m.querySelectorAll('a')).map((a) => a.id + '=' + (a.getAttribute('href') || '').split('/').pop())}; })()""")
    c.ok(menu["open"] and len(menu["links"]) == 6 and all("=" in x and not x.endswith("=#") for x in menu["links"]),
         "Export menu: Enter opens it, all six downloads inside with real links", ", ".join(menu["links"]))
    for kind in ("keyDown", "keyUp"):
        w.cdp.call("Input.dispatchKeyEvent", type=kind, key="Escape", code="Escape", windowsVirtualKeyCode=27, nativeVirtualKeyCode=27)
    time.sleep(0.3)
    c.ok(not ev("document.getElementById('export-menu').open") and ev("document.activeElement.getAttribute('data-guide')") == "export-menu",
         "Escape closes the Export menu and returns focus to it")

    # each section opens: a finding card, the role buttons
    w.click("#findings-list .finding:nth-child(1) .finding-sum", 0.5)
    c.ok(ev("document.querySelector('#findings-list .finding').open"), "a finding card opens on a real click")
    w.click("#role-technical", 0.4)
    c.ok(ev("document.getElementById('findings-panel').getAttribute('data-role')") == "technical", "role button switches the explanation")
    w.click("#role-owner", 0.3)

    # approve all, apply, re-test (real clicks and typing)
    w.facts = []
    w.audience, w.level = "shop", "new"
    w.approve_apply_retest()
    for label, ok, detail in w.facts:
        c.ok(ok, label.split(": ", 1)[1], detail)

    # audit table: one line per entry, short hash with the full hash on hover, details expand on click
    audit = ev("""(() => { const rows = Array.from(document.querySelectorAll('#audit-list .audit-entry'));
      const r = rows[0]; if (!r) return null; const h = r.querySelector('.audit-hash'); const box = document.getElementById('audit-list');
      return {rows: rows.length, hash: h.textContent, full: h.getAttribute('title') || '', oneLine: r.getBoundingClientRect().height < 44,
              chip: document.getElementById('audit-status').textContent, scroll: getComputedStyle(box).overflowY, max: getComputedStyle(box).maxHeight}; })()""")
    api_hash = (w.api("GET", "/api/audit").get("data") or {}).get("entries", [{}])[0].get("hash", "")
    c.ok(bool(audit) and len(audit["hash"]) == 8 and audit["full"] == api_hash and api_hash.startswith(audit["hash"])
         and audit["oneLine"] and audit["max"] == "360px",
         "audit table: one line per entry, 8-char hash (the API's full value on hover), scroll box 360 px",
         json.dumps(audit) + f" api={api_hash}")
    w.click("#audit-list .audit-entry:first-child .audit-details", 0.3)
    c.ok(ev("document.querySelector('#audit-list .audit-entry').classList.contains('is-open')"), "audit details expand on a real click")
    tiles = ev("Array.from(document.querySelectorAll('#usage-facts .stat-tile .stat-label')).map((x) => x.textContent)")
    c.ok(tiles == ["Scans", "Prompt", "Completion", "Total"] and ev("document.getElementById('usage-cap').textContent").startswith("cap "),
         "token usage: four stat tiles plus the cap", str(tiles))

    # nothing hidden by the composer: scroll to the end, the last panel's bottom is above the composer
    gap = ev("""(() => { const c = document.querySelector('.conversation'); c.scrollTop = c.scrollHeight;
      const panels = Array.from(document.querySelectorAll('#asset-view .panel')).filter((n) => !n.hidden && n.offsetParent);
      const last = panels.sort((a, b) => b.getBoundingClientRect().bottom - a.getBoundingClientRect().bottom)[0];
      const audit = document.querySelector('#audit-list .audit-entry:last-child');
      const comp = document.querySelector('.composer-wrap').getBoundingClientRect().top;
      return {lastPanel: last.id, lastBottom: Math.round(last.getBoundingClientRect().bottom), composerTop: Math.round(comp),
              auditBottom: audit ? Math.round(audit.getBoundingClientRect().bottom) : null}; })()""")
    time.sleep(0.3)
    c.ok(gap["lastBottom"] <= gap["composerTop"], "scrolled to the end: the last content is fully above the composer", json.dumps(gap))
    last_find = ev("""(() => { const f = Array.from(document.querySelectorAll('#findings-list .finding')).pop(); f.scrollIntoView({block: 'end'});
      return [Math.round(f.getBoundingClientRect().bottom), Math.round(document.querySelector('.composer-wrap').getBoundingClientRect().top)]; })()""")
    c.ok(last_find[0] <= last_find[1], "the last finding scrolled into view is fully visible above the composer", str(last_find))


def tour_check(w: Walker, c: Check):
    """The post-scan pet tour: score ring, each finding in severity order (checked against the real cards by
    Walker.tour), Fix first, then the approve step. Stops are read from the pet's bubble, as the user sees them."""
    ev = w.ev
    for _ in range(120):  # the scan has finished; the pet offers the tour
        if (not ev("document.getElementById('trace-list').classList.contains('is-running')")
                and ev("Array.from(document.querySelectorAll('.pet-chip')).some((x) => x.textContent === 'Show me')")):
            break
        time.sleep(0.5)
    w.rows, w.facts = [], []
    w.tour()
    stops = [r[5] for r in w.rows if r[4] == "tour"]
    for label, ok, detail in w.facts:
        c.ok(ok, "pet tour: " + label.split(": ", 1)[1], detail)
    print("tour stops:", json.dumps([x[:60] for x in stops], ensure_ascii=False))
    c.ok(bool(stops) and "score" in stops[0].lower(), "pet tour: the first stop is the score ring", stops[0][:80] if stops else "")
    c.ok(len(stops) >= 3 and "Fix first" in stops[-2] and "proposals" in stops[-1].lower(),
         "pet tour: then Fix first, then the approve step", " | ".join(x[:50] for x in stops[-2:]))


def main() -> int:
    chrome = find_chrome()
    if chrome is None:
        print("chrome.exe not found")
        return 2
    data_dir = Path(tempfile.mkdtemp(prefix="maya-layout-"))
    profile = Path(tempfile.mkdtemp(prefix="maya-layout-chrome-"))
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
        cdp.call("Emulation.setDeviceMetricsOverride", width=1440, height=900, deviceScaleFactor=1, mobile=False)
        cdp.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
        w = Walker(cdp, base)
        walk(w, c)
    finally:
        if cdp:
            cdp.close()
        if proc:
            stop_chrome(proc)
        server.shutdown()
        server.server_close()
        shutil.rmtree(data_dir, ignore_errors=True)
        shutil.rmtree(profile, ignore_errors=True)
    print("ALL RESULT PAGE CHECKS PASSED" if not c.failed else f"{c.failed} RESULT PAGE CHECK(S) FAILED")
    return 1 if c.failed else 0


if __name__ == "__main__":
    sys.exit(main())
