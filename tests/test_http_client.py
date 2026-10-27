import socket
import time

import httpx
import pytest

from src.evidence import EvidenceStore
from src.scanner.http_client import (TIMEOUT_S, USER_AGENT, HttpClient, MethodNotAllowed,
                                     TooManyRedirects)
from src.scope import OutOfScope


def client_with(handler, **kw):
    kw.setdefault("min_interval", 0)
    return HttpClient(transport=httpx.MockTransport(handler), **kw)


def never(req):
    pytest.fail(f"no request may be sent, got {req.method} {req.url}")


@pytest.mark.parametrize("url", ["http://example.com/", "http://8.8.8.8/", "http://127.0.0.1@evil.com/"])
def test_out_of_scope_refused_before_any_socket(monkeypatch, url):
    def boom(*a, **k):
        raise AssertionError("a socket was opened")
    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    c = HttpClient(min_interval=0)  # real transport: any network attempt would hit boom
    with pytest.raises(OutOfScope):
        c.get(url)


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
def test_only_get_and_head(method):
    with pytest.raises(MethodNotAllowed):
        client_with(never).request(method, "http://127.0.0.1/")


def test_redirect_to_other_host_refused():
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(302, headers={"Location": "http://evil.example/steal"})

    with pytest.raises(OutOfScope):
        client_with(handler).get("http://127.0.0.1:8081/")
    assert seen == ["http://127.0.0.1:8081/"]


def test_in_scope_redirect_followed():
    def handler(req):
        if req.url.path == "/":
            return httpx.Response(302, headers={"Location": "/home"})
        return httpx.Response(200, text="home")

    r = client_with(handler).get("http://127.0.0.1/")
    assert (r.status, r.url, r.body) == (200, "http://127.0.0.1/home", b"home")


def test_redirect_loop_stops():
    with pytest.raises(TooManyRedirects):
        client_with(lambda req: httpx.Response(302, headers={"Location": "/"})).get("http://127.0.0.1/")


def test_fixed_user_agent_and_timeout():
    seen = {}

    def handler(req):
        seen["ua"] = req.headers["user-agent"]
        return httpx.Response(200)

    c = client_with(handler)
    c.head("http://127.0.0.1/")
    assert seen["ua"] == USER_AGENT == "MAYA-scanner/0.1 (authorized security check)"
    assert c._client.timeout.read == c._client.timeout.connect == TIMEOUT_S == 5.0


def test_rate_limit():
    stamps = []

    def handler(req):
        stamps.append(time.monotonic())
        return httpx.Response(200)

    c = client_with(handler, min_interval=0.2)
    for _ in range(3):
        c.get("http://127.0.0.1/")
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert all(g >= 0.19 for g in gaps), gaps


def test_evidence_recorded(tmp_path):
    store = EvidenceStore(tmp_path / "ev.db")
    r = client_with(lambda req: httpx.Response(200, headers={"Server": "x/1.0"}, text="ok"), store=store).get(
        "http://127.0.0.1/")
    rec = store.get(r.evidence_id)
    assert rec["request"]["method"] == "GET" and rec["response"]["status"] == 200
    assert ["user-agent", USER_AGENT] in rec["request"]["headers"]
    assert store.verify(r.evidence_id)
