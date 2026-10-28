"""Web 10: GitHub connector, lookalike domain watch, model comparison. Faked transports only; no live calls."""
import io
import json
import threading
import time
import zipfile
from datetime import datetime, timezone

import httpx
import pytest

from src import github_fetch, lookalike
from src.brand_check import LOOKALIKE_TEMPLATES
from src.github_fetch import GitHubError, fetch_zip, parse_github_url, zip_url
from src.lookalike import (ALLOWED_HOSTS, LOOKALIKE_CHECKS, SHARED_LABEL, STATUS, UNKNOWN, Lookups, generate, watch)
from src.provider import ChatResult, MockProvider
from src.report import TEXT
from src.severity import TABLE
from src.web.server import make_server

PASSWORD = "correct-horse-battery"  # test-only
FAKE_SECRET = "fake-github-secret-1234567890"
NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)


def repo_zip(extra: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:  # like GitHub: one top folder
        z.writestr("demo-main/app.py", f'SECRET_KEY = "{FAKE_SECRET}"\nDEBUG = True\n')
        z.writestr("demo-main/requirements.txt", "flask==1.1.2\n")
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return buf.getvalue()


# --- GitHub URL parsing ---

@pytest.mark.parametrize("url,expected", [
    ("https://github.com/demo-org/demo-repo", ("demo-org", "demo-repo", None)),
    ("https://github.com/demo-org/demo-repo/", ("demo-org", "demo-repo", None)),
    ("https://github.com/demo-org/demo.repo.git", ("demo-org", "demo.repo", None)),
    ("https://github.com/demo-org/demo-repo/tree/dev", ("demo-org", "demo-repo", "dev")),
    ("https://github.com/demo-org/demo-repo/tree/feature/login-fix", ("demo-org", "demo-repo", "feature/login-fix")),
])
def test_valid_github_urls(url, expected):
    assert parse_github_url(url) == expected


@pytest.mark.parametrize("url", [
    "http://github.com/demo-org/demo-repo",                  # not https
    "https://gitlab.com/demo-org/demo-repo",                 # other host
    "https://github.com.evil.example/demo-org/demo-repo",    # lookalike host
    "https://user@github.com/demo-org/demo-repo",            # userinfo
    "https://github.com:8443/demo-org/demo-repo",            # port
    "https://140.82.112.3/demo-org/demo-repo",               # numeric host
    "https://GITHUB.com/demo-org/demo-repo",                 # not the exact host
    "https://github.com/demo-org",                           # no repo
    "https://github.com/demo-org/demo-repo/blob/main/x.py",  # not a repo or tree URL
    "https://github.com/demo-org/demo-repo?tab=readme",      # query
    "https://github.com/demo-org/demo-repo#readme",          # fragment
    "https://github.com/../demo-repo",                       # traversal
    "https://github.com/demo-org/..",                        # traversal
    "https://github.com/-demo/demo-repo",                    # invalid owner
    "https://github.com/demo-org/demo-repo/tree/../../etc",  # traversal in branch
    " https://github.com/demo-org/demo-repo",                # whitespace
])
def test_bad_github_urls_refused(url):
    with pytest.raises(GitHubError):
        parse_github_url(url)


# --- GitHub fetching ---

def codeload(calls: list, body: bytes = b"", status=200, headers=None, redirect_to=None):
    def handler(req: httpx.Request):
        calls.append(req)
        if redirect_to and len(calls) == 1:
            return httpx.Response(302, headers={"location": redirect_to})
        return httpx.Response(status, content=body, headers=headers or {})
    return httpx.MockTransport(handler)


def test_fetch_default_and_branch_urls():
    calls = []
    assert fetch_zip("demo-org", "demo-repo", None, transport=codeload(calls, b"PK-zip")) == b"PK-zip"
    assert fetch_zip("demo-org", "demo-repo", "dev", transport=codeload(calls, b"PK-zip")) == b"PK-zip"
    assert [str(c.url) for c in calls] == ["https://codeload.github.com/demo-org/demo-repo/zip/HEAD",
                                           "https://codeload.github.com/demo-org/demo-repo/zip/refs/heads/dev"]
    assert all(c.method == "GET" for c in calls)


def test_redirect_only_to_codeload():
    calls = []
    ok = fetch_zip("o", "r", None, transport=codeload(calls, b"PK", redirect_to="https://codeload.github.com/o/r/zip/x"))
    assert ok == b"PK" and [c.url.host for c in calls] == ["codeload.github.com"] * 2
    calls = []
    with pytest.raises(GitHubError, match="another host"):
        fetch_zip("o", "r", None, transport=codeload(calls, b"PK", redirect_to="https://evil.example/r.zip"))
    assert [c.url.host for c in calls] == ["codeload.github.com"]  # the other host is never requested


def test_size_caps(monkeypatch):
    with pytest.raises(GitHubError, match="larger than 25 MB"):
        fetch_zip("o", "r", None, transport=codeload([], b"x", headers={"content-length": str(26 * 1024 * 1024)}))
    monkeypatch.setattr(github_fetch, "MAX_BYTES", 1000)  # streamed body without a length: reading stops at the cap
    with pytest.raises(GitHubError, match="larger than 25 MB"):
        fetch_zip("o", "r", None, transport=codeload([], b"x" * 5000))


def test_private_or_missing_repo_message():
    with pytest.raises(GitHubError, match="not found, or the repository is private"):
        fetch_zip("o", "r", None, transport=codeload([], status=404))


# --- GitHub through the web app ---

class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def web(tmp_path):
    state = {"zip": repo_zip(), "requests": [], "provider": None}
    clock = Clock()

    def handler(req: httpx.Request):
        state["requests"].append(req)
        host = req.url.host
        if host == "codeload.github.com":
            if "/missing/" in req.url.path:
                return httpx.Response(404)
            return httpx.Response(200, content=state["zip"])
        if host == "crt.sh":
            q = req.url.params["q"]
            return httpx.Response(200, json=[{"id": 7, "not_before": "2026-10-20T08:00:00", "issuer_name": "CN=Example CA"}]
                                  if q == "demoshop-care.example" else [])
        if host == "cloudflare-dns.com":
            name, rtype = req.url.params["name"], req.url.params["type"]
            if name in ("demoshop-care.example", "demoshop.com"):
                ans = [{"type": 1, "data": "192.0.2.10"}] if rtype == "A" else [{"type": 2, "data": "ns1.host.example."}]
                return httpx.Response(200, json={"Status": 0, "Answer": ans})
            return httpx.Response(200, json={"Status": 3})
        return httpx.Response(599)  # anything else would be a bug; tests also assert it never happens

    server = make_server(port=0, data_dir=tmp_path, clock=clock, http_transport=httpx.MockTransport(handler),
                         provider_factory=lambda: (state["provider"], "fake-model" if state["provider"] else "none"),
                         lookalike_sleep=lambda s: None)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    clients = []

    def new_client():
        c = httpx.Client(base_url=base, trust_env=False, timeout=60)
        clients.append(c)
        return c

    yield {"new_client": new_client, "state": state, "clock": clock, "tmp": tmp_path, "server": server}
    for c in clients:
        c.close()
    server.shutdown()
    server.server_close()


def post(c, path, body=None):
    return c.post(path, json=body or {}, headers={"X-CSRF-Token": c.get("/api/session").json()["csrf"]})


def company(c, email):
    assert post(c, "/api/signup", {"email": email, "password": PASSWORD}).status_code == 200
    for path, body in [("/api/profile", {"account_type": "company"}), ("/api/asset", {"asset": "website"}),
                       ("/api/connect", {"connect": True}), ("/api/consent", {"allow": True})]:
        assert post(c, path, body).status_code == 200
    return c


def test_github_end_to_end_rate_limit_and_cross_user(web, tmp_path):
    a, b = company(web["new_client"](), "gh@shop.example"), company(web["new_client"](), "other@shop.example")
    r = post(a, "/api/connectors/github", {"url": "https://github.com/demo-org/demo-repo"})
    assert r.status_code == 200, r.text
    asset, run = r.json()["asset"], r.json()["run"]
    assert asset["kind"] == "code" and asset["label"] == "github.com/demo-org/demo-repo" and run["mode"] == "code"
    body = a.get(f"/api/runs/{run['id']}").json()
    ids = {f["id"] for f in body["result"]["findings"]}
    assert {"code_hardcoded_secret:demo-main/app.py:1", "code_debug_enabled:demo-main/app.py:2",
            "code_outdated_dependency:demo-main/requirements.txt:1"} <= ids
    assert body["result"]["upload"]["source"] == "github" and body["score"] is None  # uploads are not scored
    assert FAKE_SECRET not in json.dumps(body) and FAKE_SECRET not in a.get(f"/api/runs/{run['id']}/report.md").text
    assert a.get(f"/api/runs/{run['id']}/evidence.zip").status_code == 200
    assert b.get(f"/api/runs/{run['id']}").status_code == 404  # another user's result
    # one fetch per 10 s per user (counted even when it fails)
    r = post(a, "/api/connectors/github", {"url": "github.com/demo-org/demo-repo/tree/dev"})
    assert r.status_code == 429
    web["clock"].t += 10
    r = post(a, "/api/connectors/github", {"url": "github.com/demo-org/demo-repo/tree/dev"})  # the page's bare form
    assert r.status_code == 200 and r.json()["asset"]["label"] == "github.com/demo-org/demo-repo@dev"
    hosts = {q.url.host for q in web["state"]["requests"]}
    assert hosts == {"codeload.github.com"}


def test_github_refusals(web):
    c = company(web["new_client"](), "ghbad@shop.example")
    assert post(c, "/api/connectors/github", {"url": "https://gitlab.com/a/b"}).status_code == 400
    r = post(c, "/api/connectors/github", {"url": "https://github.com/missing/repo"})
    assert r.status_code == 400 and "private" in r.json()["error"]
    web["clock"].t += 10
    web["state"]["zip"] = b"not a zip"
    r = post(c, "/api/connectors/github", {"url": "https://github.com/demo-org/demo-repo"})
    assert r.status_code == 400 and r.json()["error"] == "ZIP refused: not a ZIP file"
    assert c.get("/api/assets").json()["assets"] == []
    individual = web["new_client"]()
    assert post(individual, "/api/signup", {"email": "ind@shop.example", "password": PASSWORD}).status_code == 200
    assert post(individual, "/api/connectors/github", {"url": "https://github.com/a/b"}).status_code == 409


def test_github_never_writes_to_disk(web, monkeypatch):
    import tempfile
    def forbidden(*a, **k):
        raise AssertionError("disk write attempted")
    for name in ("mkstemp", "mkdtemp", "NamedTemporaryFile", "TemporaryDirectory", "TemporaryFile"):
        monkeypatch.setattr(tempfile, name, forbidden)
    monkeypatch.setattr(zipfile.ZipFile, "extract", forbidden)
    monkeypatch.setattr(zipfile.ZipFile, "extractall", forbidden)
    c = company(web["new_client"](), "nodisk@shop.example")
    before = sorted(p.name for p in web["tmp"].rglob("*"))
    r = post(c, "/api/connectors/github", {"url": "https://github.com/demo-org/demo-repo"})
    assert r.status_code == 200
    after = sorted(p.name for p in web["tmp"].rglob("*"))
    # only MAYA's own SQLite databases (findings, masked evidence); never the ZIP or any file from it
    assert set(after) - set(before) <= {"evidence.db", "web.db-journal", "evidence.db-journal"}
    assert not any(n.endswith((".zip", ".py", ".txt")) for n in after)


# --- lookalike generator ---

def test_generator_deterministic_bounded_and_varied():
    c = generate("demoshop.example")
    assert c == generate("demoshop.example") and len(c) == 40
    names = [x["domain"] for x in c]
    assert names == sorted(names) and len(set(names)) == 40 and "demoshop.example" not in names
    kinds = {x["kind"] for x in c}
    assert kinds == {"homoglyph", "homoglyph (punycode)", "keyword", "tld swap", "hyphenation", "omission", "swap",
                     "duplicate", "keyboard", "subdomain trick"}
    assert any(n.startswith("xn--") for n in names) and "demoshop.example-login.com" in names
    assert {"demoshop-care.example", "demoshop-login.example"} <= set(names)
    assert sorted(x["rank"] for x in c) == list(range(40))
    with pytest.raises(ValueError):
        generate("not a domain")


# --- lookups: GET only, two hosts, no redirects, retries, 1 request/s ---

class FakeTime:
    def __init__(self):
        self.t, self.sleeps = 0.0, []

    def sleep(self, s):
        self.sleeps.append(round(s, 3))
        self.t += s

    def clock(self):
        return self.t


def test_lookups_throttle_retry_and_no_redirect():
    calls, ft = [], FakeTime()
    responses = iter([httpx.Response(503), httpx.Response(503), httpx.Response(200, json={"Status": 0, "Answer": [
        {"type": 1, "data": "192.0.2.1"}]}), httpx.Response(302, headers={"location": "https://candidate.example/"})])

    def handler(req):
        calls.append(req)
        return next(responses)
    lk = Lookups(transport=httpx.MockTransport(handler), sleep=ft.sleep, clock=ft.clock)
    assert lk.dns("x.example", "A") == (["192.0.2.1"], '{"Status":0,"Answer":[{"type":1,"data":"192.0.2.1"}]}')
    assert lk.dns("x.example", "NS") == (None, "HTTP 302")  # redirect not followed, not retried
    assert len(calls) == 4 and {c.url.host for c in calls} == {"cloudflare-dns.com"} and ft.sleeps == [1.0] * 3
    with pytest.raises(ValueError, match="not allowed"):
        lk._get("https://candidate.example/", {}, "application/json")
    lk.close()


def test_watch_findings_groups_unknown_and_hosts(tmp_path):
    from src.evidence import EvidenceStore
    calls = []

    def handler(req):
        calls.append(req)
        if req.url.host == "crt.sh":
            return httpx.Response(200, json=[{"id": 7, "not_before": "2026-10-20T08:00:00", "issuer_name": "CN=CA"}]
                                  if req.url.params["q"] == "demoshop-care.example" else
                                  [{"id": 8, "not_before": "2024-01-01T00:00:00", "issuer_name": "CN=CA"}])
        name = req.url.params["name"]
        if name == "dem0shop.example":
            return httpx.Response(500)
        if name in ("demoshop-care.example", "demoshop.com"):
            ans = [{"type": 1, "data": "192.0.2.10"}] if req.url.params["type"] == "A" else [{"type": 2, "data": "ns1.x.example."}]
            return httpx.Response(200, json={"Status": 0, "Answer": ans})
        return httpx.Response(200, json={"Status": 3})
    store = EvidenceStore(tmp_path / "e.db")
    lk = Lookups(transport=httpx.MockTransport(handler), sleep=lambda s: None)
    findings, items, groups = watch("demoshop.example", lk, store, now=NOW)
    lk.close()
    by = {f.id: f for f in findings}
    assert by["lookalike_recent_certificate:domain:demoshop-care.example"].severity == "high"
    assert by["lookalike_has_dns:domain:demoshop.com"].severity == "medium"  # only an old certificate
    assert all(f.severity == "info" for f in findings if f.check == "lookalike_no_dns")
    unknown = next(i for i in items if i["domain"] == "dem0shop.example")
    assert unknown["status"] == UNKNOWN and not any("dem0shop.example" in f.id for f in findings)  # nothing invented
    assert sum(1 for i in items if i["status"] == "not looked up (beyond the top 25)") == 15
    assert groups == [{"source": "IP 192.0.2.10", "label": SHARED_LABEL, "findings": ["demoshop-care.example", "demoshop.com"]},
                      {"source": "NS ns1.x.example", "label": SHARED_LABEL, "findings": ["demoshop-care.example", "demoshop.com"]}]
    assert {c.url.host for c in calls} <= ALLOWED_HOSTS and all(c.method == "GET" for c in calls)
    assert not any(c.url.host.endswith(".example") and c.url.host != "crt.sh" for c in calls)
    from src.agent.verifier import support_lines
    rec = store.get(by["lookalike_recent_certificate:domain:demoshop-care.example"].evidence[0].record_id)
    assert support_lines(by["lookalike_recent_certificate:domain:demoshop-care.example"], rec)[0].startswith(
        "demoshop-care.example: certificate 7 not_before 2026-10-20T08:00:00")


def test_lookalike_wording():
    texts = [TABLE[c].rationale + TABLE[c].remediation + " ".join(TEXT[c].__dict__.values()) for c in LOOKALIKE_CHECKS]
    texts += list(STATUS.values()) + [SHARED_LABEL, UNKNOWN] + list(LOOKALIKE_TEMPLATES.values())
    for t in texts:
        assert not any(w in t.lower() for w in ("fake", "scam", "fraud", "malicious")), t
    assert [TABLE[c].severity for c in LOOKALIKE_CHECKS] == ["high", "medium", "info"]


# --- lookalike through the web app ---

def wait_run(c, run_id):
    for _ in range(300):
        body = c.get(f"/api/runs/{run_id}").json()
        if body["run"]["status"] != "running":
            return body
        time.sleep(0.1)
    raise AssertionError("watch did not finish")


class Summary(MockProvider):
    def __init__(self, claims):
        super().__init__()
        self.answer = json.dumps({"claims": claims})

    def chat(self, messages, **kw):
        super().chat(messages, **kw)
        return ChatResult(self.answer, True, {"prompt_tokens": 200, "completion_tokens": 30, "total_tokens": 230})


def test_lookalike_web_flow_notes_and_model(web):
    a, b = company(web["new_client"](), "watch@shop.example"), company(web["new_client"](), "nosy@shop.example")
    assert post(a, "/api/lookalike", {"domain": "demoshop.example"}).status_code == 400  # not theirs yet
    post(a, "/api/official", {"domains": ["demoshop.example"], "numbers": []})
    r = post(a, "/api/lookalike", {"domain": "demoshop.example"})
    assert r.status_code == 200, r.text
    run_id = r.json()["run"]["id"]
    body = wait_run(a, run_id)
    assert body["run"]["mode"] == "lookalike" and body["run"]["status"] == "finished"
    assert body["result"]["upload"]["requests"] > 0 and body["result"]["groups"][0]["label"] == SHARED_LABEL
    hosts = {q.url.host for q in web["state"]["requests"]}
    assert hosts <= ALLOWED_HOSTS  # never a candidate domain
    assert post(b, "/api/lookalike", {"domain": "demoshop.example"}).status_code == 400  # Bob does not own it
    assert b.get(f"/api/runs/{run_id}").status_code == 404
    # report note: DRAFT -> READY only through the approval gate
    assert post(a, f"/api/runs/{run_id}/notes", {"template": "platform", "domain": "demoshop.com"}).status_code == 400
    assert post(a, f"/api/runs/{run_id}/notes", {"template": "registrar",
                                                 "domain": "demoshop-login.example"}).status_code == 200  # no DNS: still a draft
    r = post(a, f"/api/runs/{run_id}/notes", {"template": "hosting", "domain": "demoshop-care.example"})
    note = r.json()["result"]["notes"][-1]
    assert note["status"] == "DRAFT" and "resembles our domain demoshop.example and a recent certificate was issued" in note["text"]
    assert post(a, f"/api/runs/{run_id}/notes/{note['id']}/approve", {"approver": "demo-user"}).json()["result"][
        "notes"][-1]["status"] == "READY"
    # optional model step
    r = post(a, f"/api/runs/{run_id}/summarize")
    assert r.status_code == 409 and r.json()["error"].startswith("Model not configured")
    web["state"]["provider"] = Summary([
        {"finding_id": "lookalike_recent_certificate:domain:demoshop-care.example", "claim_type": "lookalike_domain",
         "priority": 1, "note": "Look at demoshop-care.example first."},
        {"finding_id": "lookalike_has_dns:domain:invented.example", "claim_type": "lookalike_domain", "priority": 2}])
    res = post(a, f"/api/runs/{run_id}/summarize").json()["result"]
    assert [c["severity"] for c in res["claims"]] == ["high"] and res["claims"][0]["evidence_line"].startswith(
        "demoshop-care.example: certificate 7")
    assert [d["reason"] for d in res["dropped"]] == ["unknown finding_id"]


def test_lookalike_live_script_dry_run(capsys):
    from scripts import lookalike_live
    assert lookalike_live.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "hosts contacted: ['cloudflare-dns.com', 'crt.sh']" in out
    assert "[HIGH] lookalike_recent_certificate:domain:demoshop-care.example" in out and UNKNOWN in out


# --- model comparison ---

def write_config(tmp_path, second: dict) -> "Path":
    p = tmp_path / "config.yaml"
    p.write_text("base_url: https://primary.example/v1\nmodel: primary-model\napi_key_env: MAYA_TEST_KEY_ONE\n"
                 "reasoning_effort: low\nmax_tokens: 1500\n"
                 + "".join(f"second_{k}: {v}\n" for k, v in second.items()), encoding="utf-8")
    return p


def test_compare_refuses_without_second_provider(tmp_path, capsys, monkeypatch):
    from scripts import compare_models
    monkeypatch.setenv("MAYA_TEST_KEY_ONE", "k1-not-a-real-key")
    cfg = write_config(tmp_path, {"base_url": "", "model": "", "api_key_env": ""})
    assert compare_models.main([], config_path=cfg) == 2
    assert "not configured" in capsys.readouterr().out
    cfg = write_config(tmp_path, {"base_url": "https://second.example/v1", "model": "m2", "api_key_env": "MAYA_TEST_KEY_TWO"})
    monkeypatch.delenv("MAYA_TEST_KEY_TWO", raising=False)
    assert compare_models.main([], config_path=cfg) == 2
    out = capsys.readouterr().out
    assert "MAYA_TEST_KEY_TWO is not set" in out and "k1-not-a-real-key" not in out


def chat_transport(script: list, seen: list):
    """OpenAI-compatible fake: returns the scripted messages in order."""
    def handler(req):
        seen.append(req)
        msg = script.pop(0) if script else {"role": "assistant", "content": json.dumps({"claims": []})}
        return httpx.Response(200, json={"choices": [{"message": msg, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 500, "completion_tokens": 50, "total_tokens": 550,
                                                   "completion_tokens_details": {"reasoning_tokens": 20}}})
    return httpx.MockTransport(handler)


def tool(name, args, cid):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def test_compare_two_providers_one_table(tmp_path, capsys, monkeypatch):
    from scripts import compare_models
    monkeypatch.setenv("MAYA_TEST_KEY_ONE", "k1-not-a-real-key")
    monkeypatch.setenv("MAYA_TEST_KEY_TWO", "k2-not-a-real-key")
    cfg = write_config(tmp_path, {"base_url": "https://second.example/v1", "model": "second-model",
                                  "api_key_env": "MAYA_TEST_KEY_TWO"})
    claim = json.dumps({"claims": [{"finding_id": "exposed_env_file:/.env", "claim_type": "file_exposed", "priority": 1}]})
    s1, s2 = [], []
    primary = [{"role": "assistant", "content": "", "tool_calls": [tool("run_check", {"check_id": "exposed_env_file",
                                                                                      "path": "/.env"}, "a")]},
               {"role": "assistant", "content": claim}]
    second = [{"role": "assistant", "content": "", "tool_calls": [tool("run_check", {"check_id": "nope", "path": "/"}, "b"),
                                                                  tool("run_check", {"check_id": "exposed_env_file",
                                                                                     "path": "/.env"}, "c")]},
              {"role": "assistant", "content": claim + " trailing words"}]
    rc = compare_models.main([], config_path=cfg, transports={"primary": chat_transport(primary, s1),
                                                              "second": chat_transport(second, s2)})
    out = capsys.readouterr().out
    assert rc == 0
    rows = [l for l in out.splitlines() if l.startswith("| primary") or l.startswith("| second")]
    assert rows[0].startswith("| primary | primary-model | 1 | 1 | 0 | **0** | 1100 (40) |") and rows[0].endswith("| 0/0/0 |")
    assert rows[1].startswith("| second | second-model | 1 | 0 | 0 | **0** |") and rows[1].endswith("| 1/0/0 |")
    assert {r.url.host for r in s1} == {"primary.example"} and {r.url.host for r in s2} == {"second.example"}
    assert "k1-not-a-real-key" not in out and "k2-not-a-real-key" not in out
