import threading

import pytest

from demo_app.app import make_server
from src.evidence import EvidenceStore
from src.scanner.checks_headers import run_all
from src.scanner.http_client import HttpClient

VULNERABLE_CHECKS = {
    "missing_csp", "missing_hsts", "missing_x_frame_options", "missing_x_content_type_options",
    "cookie_missing_secure", "cookie_missing_httponly", "cookie_missing_samesite", "server_version_disclosure",
}


@pytest.fixture(params=[False, True], ids=["vulnerable", "fixed"])
def demo(request, tmp_path):
    server = make_server(port=0, fixed=request.param)  # port 0 = any free loopback port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    store = EvidenceStore(tmp_path / "ev.db")
    client = HttpClient(store=store)
    yield request.param, f"http://127.0.0.1:{server.server_address[1]}", client, store, server
    client.close()
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "localhost", "::", ""])
def test_demo_refuses_non_loopback_bind(host):
    with pytest.raises(ValueError, match="127.0.0.1 only"):
        make_server(port=0, host=host)


def test_header_checks_against_demo(demo):
    fixed, base, client, store, server = demo
    assert server.server_address[0] == "127.0.0.1"
    resp = client.get(base + "/")
    findings = run_all(resp)
    assert {f.check for f in findings} == (set() if fixed else VULNERABLE_CHECKS)
    for f in findings:
        assert f.location == "/"
        assert f.evidence[0].record_id == resp.evidence_id
        assert store.verify(f.evidence[0].record_id)
    assert store.verify(resp.evidence_id)


def test_env_and_backup_exposure(demo):
    fixed, base, client, store, _ = demo
    env = client.get(base + "/.env")
    listing = client.get(base + "/backup/")
    assert (env.status, listing.status) == ((404, 404) if fixed else (200, 200))
    if not fixed:
        assert b"Index of /backup/" in listing.body
        stored = store.get(env.evidence_id)["response"]["body"]
        assert "fake-demo-key-not-real" not in stored and "fak...al" in stored  # redacted even though fake
    assert store.verify(env.evidence_id) and store.verify(listing.evidence_id)
