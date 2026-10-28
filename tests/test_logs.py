"""Web 7: log upload (small SOC module). Fixture logs use documentation address ranges only."""
import json
import threading
from pathlib import Path

import httpx
import pytest

from src import logs
from src.evidence import EvidenceStore
from src.logs import LOG_CHECKS, detect, log_findings, parse
from src.provider import ChatResult, MockProvider
from src.report import TEXT
from src.severity import TABLE
from src.web.server import make_server

FIXTURES = Path(__file__).parent / "fixtures" / "logs"
ACCESS = (FIXTURES / "access.log").read_text(encoding="utf-8")
AUTH = (FIXTURES / "auth.log").read_text(encoding="utf-8")
PASSWORD = "correct-horse-battery"  # test-only


def ids(text, **kw):
    return sorted(f.id for f in log_findings(text, "x.log", **kw)[0])


def test_fixture_detections():
    assert ids(ACCESS) == ["log_failed_login_burst:ip:203.0.113.50", "log_injection_pattern:ip:192.0.2.44",
                           "log_many_4xx:ip:198.51.100.23", "log_sensitive_path_probe:ip:198.51.100.23"]
    assert ids(AUTH) == ["log_failed_login_burst:ip:203.0.113.7", "log_login_after_failures:ip:203.0.113.7"]


def test_fixtures_use_documentation_ranges_only():
    import ipaddress
    import re
    docs = [ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32")]
    for text in (ACCESS, AUTH):
        for e in parse(text):
            assert any(ipaddress.ip_address(e["ip"]) in n for n in docs), e["ip"]
        assert not re.search(r"\b(?:10|172|8)\.\d+\.\d+\.\d+\b", text)


def test_thresholds_are_configurable_heuristics():
    assert "log_failed_login_burst:ip:203.0.113.7" not in ids(AUTH, failed_threshold=13)
    assert "log_failed_login_burst:ip:203.0.113.7" in ids(AUTH, failed_threshold=12)
    assert "log_failed_login_burst:ip:203.0.113.7" not in ids(AUTH, window_s=30)  # 12 failures span 44 s
    assert "log_login_after_failures:ip:203.0.113.7" not in ids(AUTH, fails_before_success=13)
    assert "log_many_4xx:ip:198.51.100.23" not in ids(ACCESS, many_4xx=25)
    assert logs.FAILED_LOGIN_THRESHOLD == 10 and logs.FAILED_LOGIN_WINDOW_S == 300
    assert "heuristic" in TEXT["log_failed_login_burst"].technical and "not a standard" in TEXT["log_failed_login_burst"].technical


def test_spread_out_failures_are_not_a_burst():
    events = [e for e in parse(AUTH) if e["ip"] == "2001:db8::5"]
    assert len(events) == 3 and detect(events) == {}


def test_evidence_lines_verbatim_with_only_credentials_masked(tmp_path):
    store = EvidenceStore(tmp_path / "e.db")
    fs, groups, _ = log_findings(ACCESS, "access.log", store)
    inj = next(f for f in fs if f.check == "log_injection_pattern")
    body = store.get(inj.evidence[0].record_id)["response"]["body"]
    assert "192.0.2.44" in body and "UNION+SELECT" in body  # IPs and request text stay verbatim
    assert "fakeTokenValue1234567890abcdef12" not in body and "token=fak..." in body  # the credential is masked
    lines = body.splitlines()
    assert lines[0] == "L30: " + ACCESS.splitlines()[29]  # verbatim, with its line number
    assert inj.severity == TABLE["log_injection_pattern"].severity
    assert "this does not show they succeeded" in inj.technical_explanation


def test_grouped_by_source_address_never_attacker():
    fs, groups, _ = log_findings(ACCESS + AUTH, "all.log")
    assert groups["198.51.100.23"] == ["log_many_4xx:ip:198.51.100.23", "log_sensitive_path_probe:ip:198.51.100.23"]
    texts = [f.technical_explanation for f in fs] + [" ".join(TEXT[c].__dict__.values()) for c in LOG_CHECKS]
    texts += [TABLE[c].rationale for c in LOG_CHECKS]
    assert all("attacker" not in t.lower() for t in texts)
    assert all("share" in f.technical_explanation for f in fs)


def test_line_limits(monkeypatch):
    monkeypatch.setattr(logs, "MAX_LINES", 10)
    assert len(parse(ACCESS)) == 10
    _, _, counts = log_findings(ACCESS, "x.log")
    assert counts["truncated"] is True and counts["lines"] == 10
    long = "192.0.2.1 - - [28/Oct/2026:10:00:00 +0000] \"GET /" + "a" * 5000 + " HTTP/1.1\" 404 1"
    assert parse(long) == []  # cut at 2000 characters, so the line no longer parses


# --- web: upload, groups, optional model summary ---

class Summary(MockProvider):
    """Scripted fake model for the optional summary."""

    def __init__(self, claims):
        super().__init__()
        self.answer = json.dumps({"claims": claims})

    def chat(self, messages, **kw):
        super().chat(messages, **kw)
        return ChatResult(self.answer, True, {"prompt_tokens": 300, "completion_tokens": 40, "total_tokens": 340})


@pytest.fixture
def web(tmp_path):
    state = {"provider": None}
    server = make_server(port=0, data_dir=tmp_path,
                         provider_factory=lambda: (state["provider"], "fake-summary-model" if state["provider"] else "none"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    c = httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}", trust_env=False, timeout=60)
    yield {"c": c, "state": state, "server": server}
    c.close()
    server.shutdown()
    server.server_close()


def post(c, path, body=None):
    return c.post(path, json=body or {}, headers={"X-CSRF-Token": c.get("/api/session").json()["csrf"]})


def company(c, email="soc@shop.example"):
    assert post(c, "/api/signup", {"email": email, "password": PASSWORD}).status_code == 200
    for path, body in [("/api/profile", {"account_type": "company"}), ("/api/asset", {"asset": "website"}),
                       ("/api/connect", {"connect": True}), ("/api/consent", {"allow": True})]:
        assert post(c, path, body).status_code == 200


def upload(c, data: bytes, name="access.log", ctype="text/plain"):
    return c.post("/api/uploads/logs", content=data, headers={
        "Content-Type": ctype, "X-Upload-Name": name, "X-CSRF-Token": c.get("/api/session").json()["csrf"]})


def test_log_upload_groups_and_refusals(web):
    c = web["c"]
    company(c)
    r = upload(c, ACCESS.encode())
    assert r.status_code == 200, r.text
    run = c.get(f"/api/runs/{r.json()['run']['id']}").json()
    assert run["run"]["mode"] == "logs" and len(run["result"]["findings"]) == 4
    groups = {g["source"]: g for g in run["result"]["groups"]}
    assert groups["198.51.100.23"]["label"] == "shares source address" and len(groups["198.51.100.23"]["findings"]) == 2
    assert run["result"]["thresholds"] == {"failed_logins": 10, "window_minutes": 5, "many_4xx": 20,
                                           "fails_before_success": 3}
    assert upload(c, b"x", name="access.exe").json()["error"] == "file name must end in .log or .txt"
    assert upload(c, b"\x00\x01binary", name="b.log").json()["error"] == "this does not look like a text log file"
    assert upload(c, b"0" * (5 * 1024 * 1024 + 1)).status_code == 413


def test_summary_without_key_says_model_not_configured(web):
    c = web["c"]
    company(c)
    run_id = upload(c, AUTH.encode(), name="auth.log").json()["run"]["id"]
    r = post(c, f"/api/runs/{run_id}/summarize")
    assert r.status_code == 409 and r.json()["error"].startswith("Model not configured")


def test_summary_claims_verified_like_scans(web):
    c = web["c"]
    company(c)
    run_id = upload(c, AUTH.encode(), name="auth.log").json()["run"]["id"]
    web["state"]["provider"] = Summary([
        {"finding_id": "log_login_after_failures:ip:203.0.113.7", "claim_type": "log_activity", "priority": 1,
         "note": "Check the successful login from 203.0.113.7."},
        {"finding_id": "log_failed_login_burst:ip:203.0.113.7", "claim_type": "log_activity", "priority": 2,
         "note": "Also block 198.51.100.200 now."},                           # invented address: note dropped
        {"finding_id": "log_failed_login_burst:ip:192.0.2.99", "claim_type": "log_activity", "priority": 3},  # invented id
        {"finding_id": "log_failed_login_burst:ip:203.0.113.7", "claim_type": "log_activity", "priority": 4,
         "severity": "critical"},                                            # model-supplied severity
    ])
    r = post(c, f"/api/runs/{run_id}/summarize")
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert res["summary_model"] == "fake-summary-model" and res["model_finished"] is True
    kept = res["claims"]
    assert [k["severity"] for k in kept] == ["high", "high"]  # from the table, never the model
    assert kept[0]["note"] == "Check the successful login from 203.0.113.7."
    line_no, text = kept[0]["evidence_line"].split(": ", 1)  # attached by code from the stored lines
    assert line_no.startswith("L") and text in AUTH.splitlines() and "203.0.113.7" in text
    assert kept[1]["note"] is None and "198.51.100.200" in kept[1]["note_dropped"]
    reasons = sorted(d["reason"].split(":")[0] for d in res["dropped"])
    assert reasons == ["malformed claim", "unknown finding_id"]
    sent = web["state"]["provider"].calls[-1]
    assert sent["max_tokens"] == 1200 and sent["reasoning_effort"] == "low"
    assert "never call anyone an attacker" in sent["messages"][0]["content"]


def test_summary_only_for_logs_and_per_user(web):
    c = web["c"]
    company(c)
    run_id = upload(c, ACCESS.encode()).json()["run"]["id"]
    other = httpx.Client(base_url=str(c.base_url), trust_env=False, timeout=60)
    try:
        company(other, "other@shop.example")
        assert post(other, f"/api/runs/{run_id}/summarize").status_code == 404
        assert other.get(f"/api/runs/{run_id}").status_code == 404
    finally:
        other.close()
