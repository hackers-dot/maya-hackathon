"""Web 9: scan diff, heuristic score, developer export, DNS TXT ownership, re-scan. No model calls."""
import threading
import time

import httpx
import pytest

from src.issues import code_span, issue, issues_md
from src.ownership import VerifiedTargets, new_token, txt_record, verify_dns_txt
from src.scan_diff import diff
from src.scope import OutOfScope
from src.score import LABEL, score
from src.web.server import STATIC_DIR, make_server

PASSWORD = "correct-horse-battery"  # test-only


# --- score ---

@pytest.mark.parametrize("sevs,points,grade", [
    ([], 100, "A"), (["info"] * 9, 100, "A"),
    (["medium", "low", "low"], 90, "A"),                                  # A lower bound
    (["high"], 85, "B"), (["high", "medium", "low", "low"], 75, "B"),     # B lower bound
    (["medium"] * 3 + ["low"] * 4, 74, "C"),                              # just below B
    (["critical", "medium", "low", "low"], 60, "C"),                      # C lower bound
    (["high"] + ["medium"] * 4 + ["low"], 59, "D"),                       # just below C
    (["critical", "critical"], 40, "D"),                                  # D lower bound
    (["critical", "high", "medium", "medium", "low", "low"], 39, "F"),    # just below D
    (["critical"] * 4, 0, "F"),                                           # floor at 0
])
def test_score_formula_boundaries(sevs, points, grade):
    s = score(sevs)
    assert (s["score"], s["grade"]) == (points, grade) and s["label"] == "heuristic score of the checks run"


def test_score_rejects_unknown_severity_and_wording():
    with pytest.raises(ValueError):
        score(["severe"])
    for word in ("secure", "safe", "protected"):
        assert word not in LABEL
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    badge = js.split("function renderScore", 1)[1].split("\n  }\n", 1)[0]
    for word in ("secure", "safe", "protected"):
        assert word not in badge.lower()


# --- diff ---

def v(i, sev="medium"):
    return {"id": i, "severity": sev}


def test_diff_new_resolved_unchanged_severity_change():
    d = diff([v("a"), v("b"), v("c", "low")], [v("b"), v("c", "high"), v("d")])
    assert d["new"] == ["d"] and d["resolved"] == ["a"] and d["unchanged"] == ["b"]
    assert d["severity_changed"] == [{"id": "c", "from": "low", "to": "high"}]
    assert d["counts"] == {"new": 1, "resolved": 1, "unchanged": 1, "severity_changed": 1}
    assert diff([], [v("x")])["new"] == ["x"] and diff([v("x")], [])["resolved"] == ["x"]


# --- developer export ---

FINDING = {"id": "exposed_env_file:/.env", "check": "exposed_env_file", "severity": "critical", "location": "/.env",
           "title": "x", "evidence_line": "API_KEY=fak...al"}


def test_issue_markdown_content():
    from src.report import TEXT
    i = issue(FINDING, "MAYA demo app (loopback)")
    assert i["title"] == f"[CRITICAL] {TEXT['exposed_env_file'].title} (/.env)"
    body = i["body"]
    for part in ("**Severity:** `critical`", "**Location:** `/.env` on `MAYA demo app (loopback)`",
                 "```\nAPI_KEY=fak...al\n```", "location ~ /\\.env { deny all; return 404; }", "### Rollback",
                 "### Acceptance criterion", "MAYA re-test of this check shows FIXED", "mapped to OWASP"):
        assert part in body, part


def test_issue_markdown_escaping():
    evil = {**FINDING, "location": "/x`y](https://evil.example) @someone", "id": "exposed_env_file:/x`y",
            "evidence_line": "``` broken fence\n# heading @someone"}
    i = issue(evil, "t`arget")
    assert "\n" not in i["title"]
    assert code_span(evil["location"]) in i["body"] and "``/x`y](https://evil.example) @someone``" in i["body"]
    assert "````\n``` broken fence\n# heading @someone\n````" in i["body"]  # longer fence than the content's
    assert "]( https" not in i["body"] and "\n# heading" not in i["body"].replace("````\n``` broken fence\n# heading", "")
    assert code_span("`") == "`` ` ``" and code_span("a\nb") == "`a b`"
    md = issues_md("r" * 24, [FINDING, evil], "t")
    assert md.count("\n## [CRITICAL]") == 2


# --- DNS TXT ownership ---

def doh(answers: dict, calls: list, status=200):
    def handler(req):
        calls.append(req)
        if status != 200:
            return httpx.Response(status, headers={"location": "https://other.example/"})
        name = req.url.params["name"]
        return httpx.Response(200, json={"Status": 0, "Answer": [{"type": 16, "data": d} for d in answers.get(name, [])]})
    return httpx.MockTransport(handler)


@pytest.fixture
def token():
    return new_token()


def test_txt_record_shape(token):
    assert txt_record("shop.example", token) == {"name": "_maya-verify.shop.example", "type": "TXT",
                                                 "value": f"maya-verify={token}"}


def test_dns_txt_correct_token(tmp_path, token):
    calls, targets = [], VerifiedTargets(tmp_path / "v.json")
    r = verify_dns_txt("https://shop.example", token, targets,
                       transport=doh({"_maya-verify.shop.example": [f'"maya-verify={token}"']}, calls))
    assert r.verified and r.method == "dns_txt" and targets.load()["shop.example"]["method"] == "dns_txt"
    [req] = calls
    assert req.method == "GET" and req.url.host == "cloudflare-dns.com"
    assert dict(req.url.params) == {"name": "_maya-verify.shop.example", "type": "TXT"}


@pytest.mark.parametrize("answers", [
    {"_maya-verify.shop.example": ['"maya-verify=maya-verify-wrongwrongwrongwrongwrongwrong"']},  # wrong token
    {"shop.example": ["TOKEN"]},                         # right token, wrong name (apex, not _maya-verify.)
    {"_maya-verify.other.example": ["TOKEN"]},           # right token on another domain
    {"_maya-verify.shop.example": ['"TOKEN-extra"']},    # not an exact match
    {},                                                  # no record
])
def test_dns_txt_not_verified(tmp_path, token, answers):
    answers = {k: [a.replace("TOKEN", f"maya-verify={token}") for a in vs] for k, vs in answers.items()}
    targets = VerifiedTargets(tmp_path / "v.json")
    r = verify_dns_txt("https://shop.example", token, targets, transport=doh(answers, []))
    assert not r.verified and "token not found" in r.reason and targets.load() == {}


def test_dns_txt_multiple_and_split_strings(tmp_path, token):
    half = len(token) // 2
    answers = {"_maya-verify.shop.example": ['"v=spf1 -all"', f'"maya-verify={token[:half]}" "{token[half:]}"',
                                             '"something-else"']}
    r = verify_dns_txt("https://shop.example", token, VerifiedTargets(tmp_path / "v.json"), transport=doh(answers, []))
    assert r.verified


def test_dns_txt_redirect_not_followed(tmp_path, token):
    calls = []
    r = verify_dns_txt("https://shop.example", token, VerifiedTargets(tmp_path / "v.json"),
                       transport=doh({}, calls, status=302))
    assert not r.verified and "DnsUnavailable" in r.reason and len(calls) == 1


@pytest.mark.parametrize("url", ["http://127.0.0.1:8081", "http://localhost", "http://[::1]/", "http://10.0.0.5/",
                                 "http://2130706433/"])
def test_dns_txt_refused_before_any_request(tmp_path, token, url):
    calls = []
    with pytest.raises(OutOfScope):
        verify_dns_txt(url, token, VerifiedTargets(tmp_path / "v.json"), transport=doh({}, calls))
    assert calls == []


# --- web: diff, score, history, export, DNS TXT, re-scan ---

@pytest.fixture
def web(tmp_path):
    state = {"txt": {}}

    def handler(req):
        if req.url.host == "cloudflare-dns.com":
            name = req.url.params["name"]
            return httpx.Response(200, json={"Status": 0, "Answer": [{"type": 16, "data": d}
                                                                     for d in state["txt"].get(name, [])]})
        return httpx.Response(404)

    server = make_server(port=0, data_dir=tmp_path, http_transport=httpx.MockTransport(handler),
                         provider_factory=lambda: (None, "none"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    clients = []

    def new_client():
        c = httpx.Client(base_url=base, trust_env=False, timeout=60)
        clients.append(c)
        return c

    yield {"new_client": new_client, "state": state, "tmp": tmp_path, "server": server}
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


def scan(c, asset_id):
    run_id = post(c, f"/api/assets/{asset_id}/scan").json()["run"]["id"]
    for _ in range(300):
        body = c.get(f"/api/runs/{run_id}").json()
        if body["run"]["status"] != "running":
            return run_id, body
        time.sleep(0.1)
    raise AssertionError("scan did not finish")


def test_diff_score_history_through_fix_and_reset(web):
    c = company(web["new_client"](), "diff@shop.example")
    demo = post(c, "/api/assets", {"demo": True}).json()["asset"]
    r1, b1 = scan(c, demo["id"])
    assert b1["score"]["grade"] == "F" and b1["score"]["label"] == LABEL
    assert {x["check"] for x in b1["score"]["checks_not_run"]} == {"missing_spf", "missing_dmarc",
                                                                    "weak_tls_or_expiring_cert"}
    first = c.get(f"/api/assets/{demo['id']}/diff")
    assert first.status_code == 409 and "only one completed scan" in first.json()["error"]
    assert post(c, f"/api/runs/{r1}/approve-all", {"approver": "demo-user"}).status_code == 200
    assert post(c, f"/api/runs/{r1}/apply").status_code == 200
    rt = post(c, f"/api/runs/{r1}/retest").json()
    assert rt["retest"]["status"] == "FIXED" and (rt["score"]["grade"], rt["retest_score"]["grade"]) == ("F", "A")
    r2, b2 = scan(c, demo["id"])  # "Re-scan": the same endpoint, same caps
    d = c.get(f"/api/assets/{demo['id']}/diff").json()
    assert (d["from"], d["to"]) == (r1, r2) and d["counts"]["resolved"] == 12 and d["counts"]["new"] == 0
    assert (d["score_from"]["grade"], d["score_to"]["grade"]) == ("F", "A")
    assert post(c, f"/api/assets/{demo['id']}/reset-demo").status_code == 200
    r3, b3 = scan(c, demo["id"])
    d = c.get(f"/api/assets/{demo['id']}/diff").json()
    assert (d["from"], d["to"]) == (r2, r3)
    assert d["counts"] == {"new": 12, "resolved": 0, "unchanged": 0, "severity_changed": 0}  # all new after reset
    d13 = c.get(f"/api/assets/{demo['id']}/diff", params={"from": r1, "to": r3}).json()
    assert d13["counts"]["unchanged"] == 12 and d13["counts"]["new"] == 0
    hist = c.get(f"/api/assets/{demo['id']}/history").json()["runs"]
    assert [h["run"] for h in hist] == [r1, r2, r3] and [h["score"]["grade"] for h in hist] == ["F", "A", "F"]
    assert hist[0]["retest_score"]["grade"] == "A"


def test_diff_refusals_and_cross_user(web):
    a, b = company(web["new_client"](), "a@shop.example"), company(web["new_client"](), "b@shop.example")
    demo = post(a, "/api/assets", {"demo": True}).json()["asset"]
    r1, _ = scan(a, demo["id"])
    r2, _ = scan(a, demo["id"])
    up = a.post("/api/uploads/logs", content=b"x\n", headers={
        "Content-Type": "text/plain", "X-Upload-Name": "a.log", "X-CSRF-Token": a.get("/api/session").json()["csrf"]})
    other_asset_run = up.json()["run"]["id"]
    r = a.get(f"/api/assets/{demo['id']}/diff", params={"from": other_asset_run, "to": r2})
    assert r.status_code == 400 and r.json()["error"] == "both runs must be scans of this asset"
    assert b.get(f"/api/assets/{demo['id']}/diff").status_code == 404
    assert b.get(f"/api/assets/{demo['id']}/history").status_code == 404
    b_demo = post(b, "/api/assets", {"demo": True}).json()["asset"]
    rb, _ = scan(b, b_demo["id"])
    assert b.get(f"/api/assets/{b_demo['id']}/diff", params={"from": r1, "to": rb}).status_code == 404
    assert web["new_client"]().get(f"/api/assets/{demo['id']}/diff").status_code == 401
    for path in ("issues.md", "issues.json", "report-print.html"):
        assert b.get(f"/api/runs/{r1}/{path}").status_code == 404


def test_chat_history_has_diff_line(web):
    from src.web.chat import history_context
    c = company(web["new_client"](), "chatdiff@shop.example")
    demo = post(c, "/api/assets", {"demo": True}).json()["asset"]
    r1, _ = scan(c, demo["id"])
    r2, _ = scan(c, demo["id"])
    history = web["server"].app.runs.history_for_user(1, {demo["id"]: "MAYA demo app (loopback)"})
    assert [h["run"] for h in history] == [r2, r1] and history[1]["changes"] is None  # oldest: nothing to compare
    lines = history_context(history)
    assert lines[2] == (f"  changes since the previous scan of this asset (run {r1}): 0 new, 0 resolved, "
                        "12 unchanged, 0 severity change(s)")


def test_issue_export_and_printable_report(web):
    c = company(web["new_client"](), "export@shop.example")
    demo = post(c, "/api/assets", {"demo": True}).json()["asset"]
    r1, body = scan(c, demo["id"])
    j = c.get(f"/api/runs/{r1}/issues.json").json()
    assert len(j["issues"]) == 12 and j["issues"][0]["title"].startswith("[CRITICAL] ")
    md = c.get(f"/api/runs/{r1}/issues.md")
    assert md.status_code == 200 and "attachment" in md.headers["content-disposition"]
    assert md.text.count("\n## [") == 12 and "API_KEY=fak...al" in md.text
    assert "fake-demo-key-not-real" not in md.text and "fake-demo-key-not-real" not in str(j)
    page = c.get(f"/api/runs/{r1}/report-print.html")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert "content-disposition" not in page.headers  # opened in the browser to print or save as PDF
    assert '<link rel="stylesheet" href="/static/report-print.css">' in page.text and "<style" not in page.text
    assert '<script src="/static/print.js" defer></script>' in page.text and "<script>" not in page.text
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert c.get("/static/report-print.css").status_code == 200 and c.get("/static/print.js").status_code == 200
    assert "@media print" in c.get("/static/report-print.css").text


def test_dns_txt_verification_through_the_api(web):
    c = company(web["new_client"](), "txt@shop.example")
    site = post(c, "/api/assets", {"url": "https://shop.example"}).json()["asset"]
    rec = site["verification"]["dns_txt"]
    assert rec["name"] == "_maya-verify.shop.example" and rec["value"] == "maya-verify=" + site["verification"]["token"]
    r = post(c, f"/api/assets/{site['id']}/verify-dns").json()["asset"]
    assert r["status"] == "failed" and "token not found" in r["reason"]
    web["state"]["txt"][rec["name"]] = [f'"{rec["value"]}"']
    r = post(c, f"/api/assets/{site['id']}/verify-dns").json()["asset"]
    assert r["status"] == "verified" and r["scannable"] is True
    import json as _json
    assert _json.loads((web["tmp"] / "verified_targets.json").read_text())["shop.example"]["method"] == "dns_txt"
    demo = post(c, "/api/assets", {"demo": True}).json()["asset"]
    assert post(c, f"/api/assets/{demo['id']}/verify-dns").json()["asset"]["status"] == "loopback"  # untouched
