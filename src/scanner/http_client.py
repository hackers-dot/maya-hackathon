"""The ONLY place MAYA makes outbound HTTP. Every request and every redirect hop passes scope.check first."""
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin

import httpx

from src import scope
from src.evidence import EvidenceStore

USER_AGENT = "MAYA-scanner/0.1 (authorized security check)"
ALLOWED_METHODS = {"GET", "HEAD"}
TIMEOUT_S = 5.0
MIN_INTERVAL_S = 0.2
MAX_REDIRECTS = 5
MAX_BODY_BYTES = 256 * 1024  # never pull more than this into memory
REDIRECT_CODES = {301, 302, 303, 307, 308}


class MethodNotAllowed(ValueError):
    pass


class TooManyRedirects(RuntimeError):
    pass


@dataclass
class Response:
    url: str
    status: int
    headers: list[tuple[str, str]] = field(default_factory=list)  # pairs, so repeated Set-Cookie survives
    body: bytes = b""
    evidence_id: str | None = None

    def header(self, name: str) -> str | None:
        return next((v for k, v in self.headers if k.lower() == name.lower()), None)

    def header_all(self, name: str) -> list[str]:
        return [v for k, v in self.headers if k.lower() == name.lower()]


class HttpClient:
    def __init__(self, verified: frozenset[str] = frozenset(), store: EvidenceStore | None = None,
                 transport: httpx.BaseTransport | None = None, min_interval: float = MIN_INTERVAL_S):
        self.verified = verified
        self.transport = transport  # also used by the DNS-over-HTTPS lookup (tests inject a fake)
        self.store = store
        self.min_interval = min_interval
        self._last = 0.0
        self._lock = threading.Lock()
        # trust_env=False: no proxy from the environment can reroute (or observe) scan traffic
        self._client = httpx.Client(transport=transport, timeout=TIMEOUT_S, follow_redirects=False,
                                    trust_env=False, headers={"User-Agent": USER_AGENT})

    def close(self):
        self._client.close()

    def get(self, url: str) -> Response:
        return self.request("GET", url)

    def head(self, url: str) -> Response:
        return self.request("HEAD", url)

    def request(self, method: str, url: str) -> Response:
        method = method.upper()
        if method not in ALLOWED_METHODS:
            raise MethodNotAllowed(f"{method} is not allowed; MAYA only sends GET and HEAD")
        for _ in range(MAX_REDIRECTS + 1):
            scope.check(url, self.verified)  # before every request, including each redirect hop
            resp = self._send(method, url)
            location = resp.header("location")
            if resp.status not in REDIRECT_CODES or not location:
                return resp
            url = urljoin(url, location)
        raise TooManyRedirects(f"more than {MAX_REDIRECTS} redirects")

    def _throttle(self):  # perf_counter: monotonic() ticks in ~15.6 ms steps on Windows
        with self._lock:
            wait = self._last + self.min_interval - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
            self._last = time.perf_counter()

    def _send(self, method: str, url: str) -> Response:
        self._throttle()
        with self._client.stream(method, url) as r:
            body = b""
            for chunk in r.iter_bytes():
                body += chunk
                if len(body) >= MAX_BODY_BYTES:
                    break
            headers = list(r.headers.multi_items())
            sent = list(r.request.headers.multi_items())
        resp = Response(url=url, status=r.status_code, headers=headers, body=body[:MAX_BODY_BYTES])
        if self.store is not None:
            resp.evidence_id = self.store.add(method, url, sent, r.status_code, headers, resp.body)
        return resp
