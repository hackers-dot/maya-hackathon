"""Open every screen in headless Chrome and check that EVERY interactive element has a guide-pet registry entry.

Uses the test-only preview pages (throwaway data dir, no model, no network) plus the real sign-in page. An element's
key is its data-guide value, else its id; the key must exist in pet-brain.js (window.MayaBrain.G). Elements inside
the pet's own layer are skipped. Exit 1 if anything is missing. Run: .venv\\Scripts\\python scripts\\pet_registry_check.py
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

from ui_screens import PAGES, find_chrome, start_chrome  # noqa: E402
from src.web.server import make_server  # noqa: E402

SEL = "button, a[href], input, select, textarea, summary, [role=button], [role=tab], [data-guide]"
EXTRA = ["/signin", "/__preview/connect.html", "/__preview/chat.html?view=pet&level=new",
         "/__preview/chat.html?view=finished&level=new&tour=1&help=on"]


def main() -> int:
    chrome = find_chrome()
    if chrome is None:
        print("chrome.exe not found; registry check skipped")
        return 2
    data_dir = Path(tempfile.mkdtemp(prefix="maya-registry-"))
    profile = Path(tempfile.mkdtemp(prefix="maya-registry-chrome-"))
    server = make_server(port=0, data_dir=data_dir, ui_preview=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    proc = cdp = None
    missing, total, screens = {}, 0, 0
    try:
        proc, port, cdp = start_chrome(chrome, profile)
        cdp.call("Page.enable")
        cdp.call("Emulation.setDeviceMetricsOverride", width=1280, height=720, deviceScaleFactor=1, mobile=False)
        urls = sorted({p for _, p in PAGES} | set(EXTRA))
        for url in urls:
            cdp.events.clear()
            cdp.call("Page.navigate", url=base + url)
            cdp.wait_event("Page.loadEventFired")
            time.sleep(1.4)
            r = cdp.call("Runtime.evaluate", returnByValue=True, expression=f"""JSON.stringify((() => {{
              const G = (window.MayaBrain && window.MayaBrain.G) || {{}};
              const els = Array.from(document.querySelectorAll({json.dumps(SEL)})).filter((n) => !n.closest('.pet-layer') && n.type !== 'hidden');
              return {{ total: els.length, missing: els.filter((n) => !G[n.getAttribute('data-guide') || n.id]).map((n) =>
                (n.getAttribute('data-guide') || n.id || '(no key)') + ' <' + n.tagName.toLowerCase() + '> ' + (n.getAttribute('aria-label') || '').slice(0, 30)) }};
            }})())""")
            out = json.loads(r["result"]["value"])
            screens += 1
            total += out["total"]
            for m in out["missing"]:
                missing.setdefault(m, []).append(url)
            print(f"{'ok  ' if not out['missing'] else 'MISS'} {out['total']:3d} controls  {url}")
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
    for m, where in sorted(missing.items()):
        print("MISSING", m, "on", where[0])
    print(f"{screens} screens, {total} interactive elements checked, {len(missing)} without a registry entry")
    print("REGISTRY COMPLETE" if not missing else "REGISTRY INCOMPLETE")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
