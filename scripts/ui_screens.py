"""Screenshots of the MAYA web UI with the locally installed Chrome (headless). No new packages.

Starts the web app in UI preview mode on a free 127.0.0.1 port with a throwaway data directory, then drives
one headless Chrome over the DevTools protocol (minimal stdlib WebSocket client) so each page is rendered
at an exact viewport: 1280x800 and 390x844 (mobile emulation), plus light-theme shots. PNGs go to
data/ui_screens/ (git-ignored).

Why not `chrome --screenshot --window-size=390,...`: headless Chrome never lays a page out narrower than
~504 px, so a 390 px CLI screenshot is a cropped 504 px page. Emulation.setDeviceMetricsOverride is exact.

Chrome gets a throwaway profile and a debugging port it picks itself on 127.0.0.1; it is stopped at the end.
"""
import argparse
import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.web.server import make_server  # noqa: E402

OUT = ROOT / "data" / "ui_screens"
SIZES = [("desktop", 1280, 800, False), ("mobile", 390, 844, True)]
PAGES = [  # (name, path) - signed-in pages come through the test-only preview route
    ("signin", "/signin"),
    ("choose-type", "/__preview/onboarding.html"),
    ("what-do-you-have", "/__preview/company.html"),
    ("consent", "/__preview/consent.html"),
    ("shell", "/__preview/chat.html"),
]
LIGHT = [("signin", "/signin"), ("shell", "/__preview/chat.html"), ("consent", "/__preview/consent.html")]
CANDIDATES = [Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
              Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe")]


def find_chrome() -> Path | None:
    for p in CANDIDATES:
        if p.is_file():
            return p
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as k:
                    p = Path(winreg.QueryValue(k, None))
                    if p.is_file():
                        return p
            except OSError:
                continue
    except ImportError:
        pass
    return None


class CDP:
    """Just enough RFC 6455 + Chrome DevTools Protocol for navigate / emulate / screenshot."""

    def __init__(self, ws_url: str):
        u = urlsplit(ws_url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=60)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
                          .encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("websocket handshake failed")
            resp += chunk
        head, _, self.buf = resp.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(head.split(b"\r\n", 1)[0].decode(errors="replace"))
        self.next_id, self.events = 0, []

    def close(self):
        self.sock.close()

    def _send(self, text: str):
        data = text.encode()
        head = bytearray([0x81])
        n = len(data)
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        self.sock.sendall(bytes(head) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(1 << 16)
            if not chunk:
                raise ConnectionError("websocket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _recv(self) -> dict:
        message = b""
        while True:
            b1, b2 = self._exact(2)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._exact(8))[0]
            mask = self._exact(4) if b2 & 0x80 else None
            payload = self._exact(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if b1 & 0x0F == 8:
                raise ConnectionError("websocket closed by Chrome")
            if b1 & 0x0F in (9, 10):
                continue  # ping/pong
            message += payload
            if b1 & 0x80:
                return json.loads(message)

    def call(self, method: str, **params):
        self.next_id += 1
        mid = self.next_id
        self._send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = self._recv()
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            self.events.append(msg)

    def wait_event(self, name: str, timeout: float = 30.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for i, e in enumerate(self.events):
                if e.get("method") == name:
                    return self.events.pop(i)
            self.events.append(self._recv())
        raise TimeoutError(name)


def start_chrome(chrome: Path, profile: Path):
    proc = subprocess.Popen(
        [str(chrome), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
         "--no-default-browser-check", "--disable-extensions", "--disable-sync", "--mute-audio",
         f"--user-data-dir={profile}", "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
         "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    port_file = profile / "DevToolsActivePort"
    for _ in range(300):
        if port_file.is_file() and port_file.read_text().strip():
            port = int(port_file.read_text().splitlines()[0])
            break
        time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError("Chrome did not open a debugging port")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    targets = json.loads(opener.open(f"http://127.0.0.1:{port}/json/list", timeout=30).read())
    page = next(t for t in targets if t.get("type") == "page")
    return proc, port, CDP(page["webSocketDebuggerUrl"])


def shoot(cdp: CDP, url: str, out: Path, width: int, height: int, mobile: bool):
    cdp.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=mobile)
    cdp.events.clear()
    cdp.call("Page.navigate", url=url)
    cdp.wait_event("Page.loadEventFired")
    time.sleep(0.8)  # let app.js fetch /api/session and fill the page
    metrics = cdp.call("Runtime.evaluate", returnByValue=True, expression=(
        "JSON.stringify({vw: innerWidth, scrollW: document.documentElement.scrollWidth})"))["result"]["value"]
    data = cdp.call("Page.captureScreenshot", format="png")["data"]
    out.write_bytes(base64.b64decode(data))
    return json.loads(metrics)


def main(argv=None) -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    chrome = find_chrome()
    if chrome is None:
        print("chrome.exe not found; screenshot step skipped")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    data_dir = Path(tempfile.mkdtemp(prefix="maya-ui-screens-"))
    profile = Path(tempfile.mkdtemp(prefix="maya-chrome-profile-"))
    server = make_server(port=0, data_dir=data_dir, ui_preview=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    proc = cdp = None
    overflow = 0
    try:
        proc, port, cdp = start_chrome(chrome, profile)
        print(f"chrome: {chrome} (pid {proc.pid}, debug port 127.0.0.1:{port})\n"
              f"server: {base} (UI preview mode, throwaway data dir)")
        cdp.call("Page.enable")
        jobs = [(f"{name}-{label}", path, w, h, m) for name, path in PAGES for label, w, h, m in SIZES]
        jobs += [(f"{name}-desktop-light", path + "?theme=light", 1280, 800, False) for name, path in LIGHT]
        jobs += [(f"{name}-mobile-light", path + "?theme=light", 390, 844, True) for name, path in LIGHT[1:2]]
        for shot, path, w, h, mobile in jobs:
            out = OUT / f"{shot}.png"
            m = shoot(cdp, base + path, out, w, h, mobile)
            wide = m["scrollW"] > m["vw"]
            overflow += wide
            print(f"{'ok  ' if not wide else 'WIDE'} {w}x{h}  viewport={m['vw']} scrollWidth={m['scrollW']}  "
                  f"{out.relative_to(ROOT)}")
    finally:
        if cdp:
            cdp.close()
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=15)
            print(f"chrome stopped (exit code {proc.returncode})")
        server.shutdown()
        server.server_close()
        shutil.rmtree(data_dir, ignore_errors=True)
        for _ in range(20):  # Chrome may hold profile files for a moment after exit
            shutil.rmtree(profile, ignore_errors=True)
            if not profile.exists():
                break
            time.sleep(0.25)
    print(f"horizontal overflow on {overflow} shot(s)")
    return 1 if overflow else 0


if __name__ == "__main__":
    sys.exit(main())
