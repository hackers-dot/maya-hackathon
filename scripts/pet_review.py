"""Walk the MAYA pet through the whole app with REAL mouse and keyboard events, for every audience x level, and
write every line it showed and said to docs/pet_lines_review.md.

Throwaway data dir, deterministic scans (no model, no network beyond 127.0.0.1), headless Chrome over DevTools.
For each audience in {shop, company, clinic, developer, other} and level in {new, technical}: sign-in/sign-up,
onboarding, connect, consent, connect website (demo app), chat, a real scan, the post-scan tour, every result
control, approve all, apply, re-test, summary, exports, the connector views, Your data and Pet settings. At each
control: hover with a real pointer, record the control key, the bubble text and the spoken text (speechSynthesis is
replaced by a recorder; no sound). Also checks facts against the API (approve changes nothing, apply only touches
the demo app, re-test only reads, delete really deletes) and that the tour follows the real findings.
Run: .venv\\Scripts\\python scripts\\pet_review.py [--quick]   (--quick: one audience x level)
Light mode (one Chrome per combination): --combo shop/new --json out.json, then --render a.json b.json ... writes the doc.
"""
import json
import re
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

AUDIENCES = ["shop", "company", "clinic", "developer", "other"]
LEVELS = ["new", "technical"]
RECORDER = """(() => {
  window.__speak = [];
  const s = window.speechSynthesis;
  if (s) { s.speak = (u) => { if (u.text.trim()) window.__speak.push(u.text); }; s.cancel = () => {}; }
})();"""
OUT = ROOT / "docs" / "pet_lines_review.md"


class Walker:
    def __init__(self, cdp, base):
        self.cdp, self.base = cdp, base
        self.rows = []      # (screen, key, audience, level, kind, text)
        self.facts = []     # (label, ok, detail)
        self.audience = self.level = ""

    def ev(self, expr):
        r = self.cdp.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value", r.get("exceptionDetails"))

    def mouse(self, kind, x, y, buttons=0, button="none", clicks=0):
        self.cdp.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button=button, buttons=buttons, clickCount=clicks)

    def center(self, sel):
        return self.ev(f"""(() => {{ const n = document.querySelector({json.dumps(sel)}); if (!n || n.closest('[hidden]')) return null;
          n.scrollIntoView({{block: 'center'}}); const r = n.getBoundingClientRect();
          return r.width ? [Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2)] : null; }})()""")

    def click(self, sel, wait=0.8):
        pos = self.center(sel)
        if not pos:
            return False
        self.mouse("mouseMoved", *pos)
        self.mouse("mousePressed", *pos, 1, "left", 1)
        self.mouse("mouseReleased", *pos, 0, "left", 1)
        time.sleep(wait)
        return True

    def type_text(self, sel, text):
        self.click(sel, 0.2)
        self.cdp.call("Input.insertText", text=text)
        time.sleep(0.2)

    def goto(self, path, wait=2.2):
        self.cdp.events.clear()
        self.cdp.call("Page.navigate", url=self.base + path)
        self.cdp.wait_event("Page.loadEventFired")
        time.sleep(wait)

    def bubble(self):
        return self.ev("(() => { const b = document.getElementById('pet-bubble'); return b && !b.hidden ? "
                       "b.querySelector('.pet-bubble-text').textContent : ''; })()") or ""

    def record_line(self, screen, key, kind="step"):
        text = self.bubble()
        if text:
            self.rows.append((screen, key, self.audience, self.level, kind, text))
        return text

    def hover(self, screen, sel, rest=0.95):
        pos = self.center(sel)
        if not pos:
            return None
        key = self.ev(f"(() => {{ const n = document.querySelector({json.dumps(sel)}); return n.getAttribute('data-guide') || n.id; }})()")
        before = len(self.ev("window.__speak"))
        self.mouse("mouseMoved", *pos)
        time.sleep(rest)
        text = self.bubble()
        spoken = self.ev("window.__speak")[before:]
        self.rows.append((screen, key, self.audience, self.level, "hover", text))
        said = " ".join(spoken)
        if said and said != text:  # spoken text differs from the bubble (e.g. paths and ids are not read aloud)
            self.rows.append((screen, key, self.audience, self.level, "hover (spoken)", said))
        self.mouse("mouseMoved", 5, 5)
        time.sleep(0.25)
        return text

    def api(self, method, path, body=None):
        return self.ev(f"""(async () => {{ const s = await (await fetch('/api/session')).json();
          const r = await fetch({json.dumps(path)}, {{method: {json.dumps(method)}, headers: {{'Content-Type': 'application/json',
            'X-CSRF-Token': s.csrf}}, body: {json.dumps(json.dumps(body)) if body is not None else 'undefined'}}});
          let d = null; try {{ d = await r.json(); }} catch (e) {{}} return {{status: r.status, data: d}}; }})()""")

    def fact(self, label, ok, detail=""):
        self.facts.append((f"{self.audience}/{self.level}: {label}", bool(ok), detail))

    # ---------------------------------------------------------------- the walk
    def walk(self, audience, level, n):
        self.audience, self.level = audience, level
        self.cdp.call("Network.clearBrowserCookies")
        self.goto("/signin", 1.0)
        self.ev(f"""localStorage.clear(); localStorage.setItem('maya-guide-level', '{level}');
          localStorage.setItem('maya-guide-audience', '{audience}'); localStorage.setItem('maya-greeted', '1'); 1""")
        self.goto("/signin")
        self.click("body", 0.3)  # the first gesture unlocks speech
        self.record_line("sign-in", "(guide step)")
        for sel in ["#tab-signin", "#signin-email", "#signin-password", "[data-guide=signin-submit]", "#theme-toggle", "#tab-signup"]:
            self.hover("sign-in", sel)
        self.click("#tab-signup", 0.4)
        for sel in ["#signup-email", "#signup-password", "[data-guide=signup-submit]"]:
            self.hover("sign-up", sel)
        email = f"review-{n}@shop.example"
        self.type_text("#signup-email", email)
        self.type_text("#signup-password", "review-password-123")
        self.click("[data-guide=signup-submit]", 2.4)  # real submit: lands on onboarding
        self.record_line("type choice", "(guide step)")
        for sel in ["#choose-individual", "#choose-company"]:
            self.hover("type choice", sel)
        self.click("#choose-company", 2.4)
        self.record_line("what do you have", "(guide step)")
        self.hover("what do you have", "#asset-website")
        self.click("#asset-website", 2.4)
        self.record_line("connect", "(guide step)")
        for sel in ["#connect-yes", "#connect-no"]:
            self.hover("connect", sel)
        self.click("#connect-yes", 2.4)
        self.record_line("consent", "(guide step)")
        for sel in ["#consent-allow", "[data-guide=consent-back]"]:
            self.hover("consent", sel)
        self.click("#consent-allow", 2.4)
        self.record_line("connect website", "(guide step)")
        for sel in ["#asset-url", "[data-guide=asset-add]", "#use-demo"]:
            self.hover("connect website", sel)
        self.click("#use-demo", 1.0)
        self.hover("connect website", "#ap-continue")
        self.goto("/chat")
        self.click("body", 0.3)
        self.record_line("chat", "(guide step)")
        for sel in ["#new-chat", "#pet-call", "#pet-settings-btn", "#add-asset", "[data-guide=conn-website]", "#conn-code",
                    "#conn-logs", "#conn-msg", "#conn-github", "#conn-watch", "#data-link", "#signout",
                    "[data-guide=suggest-scan]", "[data-guide=suggest-explain]", "[data-guide=suggest-fix-first]",
                    "[data-guide=suggest-how]", "#prompt", "#investigate-btn", "[data-guide=brand-home]"]:
            self.hover("chat", sel)
        # the demo app, a real scan, the post-scan tour
        self.click("[data-guide=asset-item]", 1.5)
        self.record_line("result (before scan)", "(guide step)")
        for sel in ["#scan-empty-btn", "#reset-demo-btn", "#disconnect-btn"]:
            self.hover("result", sel)
        self.click("#scan-empty-btn", 1.2)
        self.record_line("scan running", "(guide step)")
        self.hover("scan running", "[data-guide=trace-step]", 0.9)
        for _ in range(80):
            if self.ev("!!document.querySelector('#score-card:not([hidden])')") and "Done" in (self.bubble() + "Done"):
                if self.ev("Array.from(document.querySelectorAll('.pet-chip')).some((c) => c.textContent === 'Show me')"):
                    break
            time.sleep(0.5)
        self.record_line("scan finished", "(guide step)")
        self.tour()
        # the result screen (the first finding card open, so its buttons can be hovered)
        self.ev("(() => { const d = document.querySelector('#findings-list .finding'); if (d) d.open = true; return 1; })()")
        for sel in ["#score-card", "#findings-list .finding:nth-child(1) .finding-sum", "#findings-list .finding:nth-child(2) .finding-sum",
                    "#findings-list .finding:nth-child(3) .finding-sum", "[data-guide=fixfirst-card]", "#role-owner", "#role-developer",
                    "#role-technical", "#findings-list .finding[open] [data-guide=copy-issue]", "[data-guide=proposal-card]", "[data-guide=copy-snippet]",
                    "#approver", "[data-guide=proposal-approve]", "#approve-all-btn", "#apply-btn", "#retest-btn"]:
            self.hover("result", sel)
        self.approve_apply_retest()
        for sel in ["#sum-en", "#sum-hinglish", "#reword-btn", "#reminder-days", "#reminder-btn", "#dl-pack", "#dl-report",
                    "#dl-report-md", "#dl-issues", "#dl-client", "#dl-print"]:
            self.hover("summary and exports", sel)
        # the other views
        for view, opener, controls in [
            ("code upload", "#conn-code", ["#up-file", "#up-btn"]),
            ("logs upload", "#conn-logs", ["#up-file", "#up-btn"]),
            ("message upload", "#conn-msg", ["#up-file", "#msg-confirm", "#msg-include", "[data-guide=official-box]"]),
            ("GitHub", "#conn-github", ["#github-url", "#github-btn"]),
            ("lookalike watch", "#conn-watch", ["#watch-domain", "#watch-btn"]),
            ("your data", "#data-link", ["#delete-confirm", "#delete-btn"]),
            ("pet settings", "#pet-settings-btn", ["#pet-level", "#pet-audience", "#pet-voice", "#pet-voice-preview", "#pet-rate",
                                                   "#pet-pitch", "#pet-speak-steps", "#pet-speak-hover", "#pet-speak-answers",
                                                   "#pet-voice-test", "#pet-help", "#pet-reset"]),
        ]:
            self.click(opener, 1.0)
            self.record_line(view, "(after click)", "after click")
            for sel in controls:
                self.hover(view, sel)
        if level == "technical":  # delete really deletes (once per audience)
            before = self.api("GET", "/api/assets")
            self.click("#data-link", 0.8)
            self.type_text("#delete-confirm", "DELETE")
            self.click("#delete-btn", 2.0)
            after = self.api("GET", "/api/assets")
            n_before = len((before.get("data") or {}).get("assets", [])) if isinstance(before, dict) else -1
            n_after = len((after.get("data") or {}).get("assets", [])) if isinstance(after, dict) else -1
            self.fact("Delete my data really deletes the connected assets", n_before >= 1 and n_after == 0, f"{n_before} -> {n_after}")

    def tour(self):
        chips = self.ev("Array.from(document.querySelectorAll('.pet-chip')).map((c) => c.textContent)") or []
        if "Show me" not in chips:
            self.fact("post-scan tour offered after the scan", False, str(chips))
            return
        self.click_chip("Show me")
        cards = self.ev("""Array.from(document.querySelectorAll('#findings-list .finding .finding-sum')).map((s) => ({
          id: s.getAttribute('data-finding-id'), sev: s.querySelector('.sev').textContent, title: s.querySelector('.f-title').textContent,
          where: s.querySelector('.f-where').textContent}))""")
        stops = []
        for _ in range(30):
            time.sleep(1.3)
            text = self.bubble()
            stops.append(text)
            self.rows.append(("post-scan tour", f"stop {len(stops)}", self.audience, self.level, "tour", text))
            labels = self.ev("Array.from(document.querySelectorAll('.pet-chip')).map((c) => c.textContent)") or []
            if "Done" in labels:
                self.click_chip("Done")
                break
            if not self.click_chip("Next"):
                break
        order = ["critical", "high", "medium", "low", "info"]
        sevs = [c["sev"] for c in cards]
        self.fact("finding cards are in severity order", sevs == sorted(sevs, key=order.index), str(sevs))
        finding_stops = stops[1:1 + len(cards)]
        ok = len(finding_stops) == len(cards) and all(c["title"] in s and c["where"] in s and c["sev"].capitalize() in s
                                                      for c, s in zip(cards, finding_stops))
        self.fact("each tour stop names the real finding, location and severity, in order", ok,
                  f"{len(cards)} findings, {len(stops)} stops")

    def click_chip(self, label):
        pos = self.ev(f"""(() => {{ const c = Array.from(document.querySelectorAll('.pet-chip')).find((x) => x.textContent === {json.dumps(label)});
          if (!c) return null; const r = c.getBoundingClientRect(); return [Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2)]; }})()""")
        if not pos:
            return False
        self.mouse("mouseMoved", *pos)
        self.mouse("mousePressed", *pos, 1, "left", 1)
        self.mouse("mouseReleased", *pos, 0, "left", 1)
        return True

    def approve_apply_retest(self):
        run_id = self.ev("(async () => { const a = await (await fetch('/api/assets')).json(); return a.assets.find((x) => x.kind === 'demo').last_run; })()")
        audit_before = (self.api("GET", "/api/audit").get("data") or {}).get("count", 0)
        self.type_text("#approver", "Review Owner")
        self.click("#approve-all-btn", 1.6)
        self.record_line("approve all", "(guide step)")
        run = (self.api("GET", f"/api/runs/{run_id}").get("data") or {})
        statuses = sorted({p["status"] for p in run.get("proposals", [])})
        audit_after = (self.api("GET", "/api/audit").get("data") or {}).get("count", 0)
        self.fact("Approve all records approvals and changes nothing yet", statuses == ["APPROVED"] and audit_after > audit_before,
                  f"statuses={statuses}, audit {audit_before} -> {audit_after}")
        self.click("#apply-btn", 3.0)
        self.record_line("apply", "(guide step)")
        run = (self.api("GET", f"/api/runs/{run_id}").get("data") or {})
        statuses = sorted({p["status"] for p in run.get("proposals", [])})
        self.fact("Apply touches only the loopback demo app (proposals APPLIED, asset is the demo)",
                  "APPLIED" in statuses and run.get("run", {}).get("demo") is True, f"statuses={statuses}")
        findings_before = len((run.get("result") or {}).get("findings", []))
        self.click("#retest-btn", 4.0)
        self.record_line("re-test", "(guide step)")
        run = (self.api("GET", f"/api/runs/{run_id}").get("data") or {})
        retest = run.get("retest") or {}
        self.fact("Re-test only reads: same run, a result, the original findings kept",
                  bool(retest.get("status")) and len((run.get("result") or {}).get("findings", [])) == findings_before,
                  f"retest={retest.get('status')}, findings kept {findings_before}")


def write(rows, facts, combos):
    words = lambda t: len(t.split())  # noqa: E731
    lines = ["# Guide pet: every line, reviewed", "",
             "Generated by `scripts/pet_review.py`: a real walk through the app with real mouse events in headless Chrome,",
             "for each audience and level. `kind` is hover (shown), hover (spoken) when the spoken text differs, a guide step,",
             "a line after a click, or a tour stop. Lines are fixed templates in the code, never model output.", "",
             f"Combinations walked: {', '.join(combos)}. Rows: {len(rows)}.", "",
             "## Fact checks against the running app", "", "| check | result | detail |", "|---|---|---|"]
    for label, ok, detail in facts:
        lines.append(f"| {label} | {'OK' if ok else 'MISMATCH'} | {detail} |")
    lines += ["", "## Lines", "", "| screen | control | audience | level | kind | words | line |", "|---|---|---|---|---|---|---|"]
    for screen, key, aud, lvl, kind, text in rows:
        lines.append(f"| {screen} | {key} | {aud} | {lvl} | {kind} | {words(text)} | {text.replace('|', '/')} |")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render(paths) -> int:
    rows, facts, combos = [], [], []
    for path in paths:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        rows += [tuple(r) for r in d["rows"]]
        facts += [(label, ok, detail) for label, ok, detail in d["facts"]]  # labels already name the combination
        combos.append(d["combo"])
    write(rows, facts, combos)
    print(f"wrote {OUT.relative_to(ROOT)}: {len(rows)} rows from {', '.join(combos)}")
    return 0 if all(ok for _, ok, _ in facts) else 1


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--render" in args:
        return render(args[args.index("--render") + 1:])
    quick = "--quick" in args
    one = args[args.index("--combo") + 1].split("/") if "--combo" in args else None
    json_out = Path(args[args.index("--json") + 1]) if "--json" in args else None
    chrome = find_chrome()
    if chrome is None:
        print("chrome.exe not found; review skipped")
        return 2
    data_dir = Path(tempfile.mkdtemp(prefix="maya-review-"))
    profile = Path(tempfile.mkdtemp(prefix="maya-review-chrome-"))
    server = make_server(port=0, data_dir=data_dir, provider_factory=lambda: (None, "none"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    proc = cdp = None
    combos = [tuple(one)] if one else [(a, lv) for a in AUDIENCES for lv in LEVELS][:1 if quick else None]
    assert all(a in AUDIENCES and lv in LEVELS for a, lv in combos), combos
    try:
        proc, port, cdp = start_chrome(chrome, profile)
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Page.addScriptToEvaluateOnNewDocument", source=RECORDER)
        cdp.call("Emulation.setDeviceMetricsOverride", width=1280, height=720, deviceScaleFactor=1, mobile=False)
        cdp.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
        w = Walker(cdp, base)
        for n, (aud, lvl) in enumerate(combos):
            t0 = time.time()
            w.walk(aud, lvl, n)
            print(f"walked {aud}/{lvl}: {len(w.rows)} rows so far ({time.time() - t0:.0f} s)", flush=True)
        if json_out:  # light mode: rows and facts of this one combination; --render builds the doc
            json_out.write_text(json.dumps({"combo": "/".join(combos[0]), "rows": w.rows, "facts": w.facts}), encoding="utf-8")
        else:
            write(w.rows, w.facts, [f"{a}/{lv}" for a, lv in combos])
        for label, ok, detail in w.facts:
            print(f"{'OK      ' if ok else 'MISMATCH'} {label} [{detail}]")
        print(f"wrote {json_out or OUT.relative_to(ROOT)}: {len(w.rows)} rows")
        return 0 if all(ok for _, ok, _ in w.facts) else 1
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


if __name__ == "__main__":
    sys.exit(main())
