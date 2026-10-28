"""Web 10.1: fewer code-scanner false positives; crt.sh timeout, retry and time budget. No network."""
import io
import zipfile
from datetime import datetime, timezone

import httpx
import pytest

from src import lookalike
from src.code_scan import ONE_LOWER, TEST_LABEL, read_zip, scan_upload, strip_js
from src.lookalike import SKIPPED, Lookups, watch
from src.report import build_report, to_html, to_markdown

NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)


def scan(files: dict) -> list:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    found, _ = read_zip(buf.getvalue())
    return scan_upload(found, "repo.zip")


# --- MAYA's own false positives from the live run: NOT flagged ---

NOT_FLAGGED_PY = "\n".join([
    "import argparse",
    "TOKEN_CAP = 40000",
    "TOKEN_CAP = 40_000",
    "CHAT_TOKEN_CAP = 60_000  # per user, rolling 24 hours",
    'TOKEN_PREFIX = "maya-verify-"',
    'cfg = {"api_key_env": "BASETEN_API_KEY", "second_api_key_env": "MAYA_TEST_KEY_TWO"}',
    'CLAIMS = {"code_hardcoded_secret": "code_pattern", "code_weak_password_hash": "code_pattern"}',
    'p = argparse.ArgumentParser(description="MAYA offline eval (scripted model, loopback demo app)")',
    'RULE = (r"(?<![\\w.])(?:eval|exec)\\s*\\(", "eval( / exec( call detected")',
    'SHELL = (r"\\bshell\\s*=\\s*True\\b", "subprocess call with shell=True detected")',
    'PICKLE = "A `pickle.load(` or `pickle.loads(` call appears at the line shown."',
    'HINT = "MD5 or SHA-1 appears to be used near password handling."',
    'DEBUG_HINT = "`DEBUG = True` or `debug=True` is set in the file shown."',
    "# never call eval(user_input) or pickle.loads(blob) here; shell=True is banned",
    'headers = {"X-CSRF-Token": "forged"}',
    'sample = {"token": "maya-verify-PREVIEWxSAMPLExTOKENx0000000000"}',
    'CONFIG = "reasoning_effort: low\\nmax_tokens: 1500\\n"',  # the file holds the two characters \ n, not a newline
    "",
])
NOT_FLAGGED_JS = "\n".join([
    'setText("quality-summary", "No eval results yet: run the eval (python -m src.eval.harness --write).");',
    "const hint = 'shell=True and debug=True are flagged';  // eval(x) in a comment",
    "/* exec(command) inside a block comment */",
    "const tpl = `pickle.loads( and eval( inside a template string`;",
    "",
])


def test_maya_false_positives_are_not_flagged():
    findings = scan({"src/settings.py": NOT_FLAGGED_PY, "src/web/static/app.js": NOT_FLAGGED_JS})
    assert [f.id for f in findings] == []


# --- real cases: still flagged ---

REAL_PY = "\n".join([
    'API_KEY = "fake-demo-key-not-real-42"',                 # a real-shaped key (fake value)
    'aws = "' + "AKIA" + 'ABCDEFGHIJKLMNOP"',                 # known prefix, assembled so no key shape sits in the repo
    "-----BEGIN " + "PRIVATE KEY-----",                     # PEM header
    "app.run(debug=True)",
    "result = eval(user_input)",
    "data = pickle.loads(blob)",
    "subprocess.run(cmd, shell=True)",
    "digest = hashlib.md5(password.encode()).hexdigest()",
    "",
])


def test_real_cases_still_flagged():
    findings = scan({"app/main.py": REAL_PY, ".env": "API_KEY=fake-demo-key-not-real-42\n",
                     "web/app.js": "const x = eval(userInput);\nwindow.go = 1;\n"})
    by = {(f.check, f.location) for f in findings}
    assert by == {
        ("code_hardcoded_secret", "app/main.py:1"), ("code_hardcoded_secret", "app/main.py:2"),
        ("code_hardcoded_secret", "app/main.py:3"), ("code_debug_enabled", "app/main.py:4"),
        ("code_dangerous_eval", "app/main.py:5"), ("code_unsafe_deserialization", "app/main.py:6"),
        ("code_shell_true", "app/main.py:7"), ("code_weak_password_hash", "app/main.py:8"),
        ("code_env_file_committed", ".env:1"), ("code_hardcoded_secret", ".env:1"),
        ("code_dangerous_eval", "web/app.js:1")}
    assert all(f.context is None for f in findings)  # not test or demo paths


def test_tokenize_failure_falls_back_and_says_so():
    broken = 'x = """never closed\nresult = eval(user_input)\n'
    [f] = [f for f in scan({"app/broken.py": broken}) if f.check == "code_dangerous_eval"]
    assert "could not be tokenized" in f.technical_explanation and f.location == "app/broken.py:2"


def test_strip_js_keeps_lines_and_blanks_strings_and_comments():
    src = 'a = "x\\"eval(" + b; // exec(\nc = eval(d) /* pickle.loads(\n */ e'
    out = strip_js(src)
    assert out.count("\n") == src.count("\n") and len(out) == len(src)
    assert "exec(" not in out and "pickle" not in out and "eval(d)" in out and 'eval("' not in out


# --- test or demo code: labelled, one level lower, grouped apart in the report ---

@pytest.mark.parametrize("folder", ["tests", "test", "fixtures", "demo_app", "examples", "docs"])
def test_test_and_demo_paths_are_labelled_and_lower(folder):
    [f] = [f for f in scan({f"repo/{folder}/x.py": 'API_KEY = "fake-demo-key-not-real-42"\n'})]
    assert f.context == TEST_LABEL and f.severity == "medium"  # high -> medium
    assert f.technical_explanation.startswith("In test or demo code: ")
    assert ONE_LOWER["info"] == "info" and ONE_LOWER["critical"] == "high"


def test_report_and_views_group_test_findings_apart():
    from src.web.runs import finding_view
    findings = {f.id: f for f in scan({"app/main.py": "result = eval(user_input)\n",
                                       "tests/test_x.py": 'API_KEY = "fake-demo-key-not-real-42"\n'})}
    report = build_report(findings, None, "none", "low", {})
    assert [i["finding_id"] for i in report["items"]] == ["code_dangerous_eval:app/main.py:1"]
    assert [i["finding_id"] for i in report["test_items"]] == ["code_hardcoded_secret:tests/test_x.py:1"]
    md, page = to_markdown(report), to_html(report)
    main, test = md.split("## Detected in test or demo code")
    assert "tests/test_x.py" not in main and "**MEDIUM**" in test and "tests/test_x.py" in test
    assert "<h2>Detected in test or demo code</h2>" in page
    view = finding_view(findings["code_hardcoded_secret:tests/test_x.py:1"], None)
    assert view["severity"] == "medium" and view["context"] == TEST_LABEL


# --- crt.sh: 5 s timeout, 1 retry, 90 s budget for the whole watch ---

class FakeTime:
    def __init__(self):
        self.t = 0.0

    def sleep(self, s):
        self.t += s

    def clock(self):
        return self.t


def test_crtsh_timeout_and_single_retry():
    seen = []

    def handler(req):
        seen.append((req.url.host, req.extensions["timeout"]["read"]))
        if req.url.host == "crt.sh":
            raise httpx.ReadTimeout("slow", request=req)
        return httpx.Response(200, json={"Status": 0, "Answer": [{"type": 1, "data": "192.0.2.1"}]})
    lk = Lookups(transport=httpx.MockTransport(handler), sleep=lambda s: None)
    assert lk.certs("x.example") == (None, "ReadTimeout")
    assert lk.dns("x.example", "A")[0] == ["192.0.2.1"]
    lk.close()
    assert seen == [("crt.sh", 5.0), ("crt.sh", 5.0), ("cloudflare-dns.com", 10.0)]  # 1 retry; DNS keeps 10 s


def test_time_budget_skips_remaining_crtsh_but_finishes_dns():
    ft, calls = FakeTime(), []

    def handler(req):
        calls.append(req.url.host)
        if req.url.host == "crt.sh":
            ft.t += 40  # a slow crt.sh answer
            return httpx.Response(200, json=[])
        ft.t += 0.1
        return httpx.Response(200, json={"Status": 0, "Answer": [{"type": 1, "data": "192.0.2.9"}]})
    lk = Lookups(transport=httpx.MockTransport(handler), sleep=ft.sleep, clock=ft.clock)
    findings, items, groups = watch("demoshop.example", lk, now=NOW)
    lk.close()
    looked = [i for i in items if i["rank"] < 25]
    skipped = [i for i in looked if i["status"].endswith(SKIPPED)]
    assert calls.count("cloudflare-dns.com") == 50  # every DNS lookup still completed
    assert calls.count("crt.sh") == 3 and len(skipped) == 22  # 3 crt.sh answers use up the 90 s budget
    assert all(i["status"] == "resembles your domain and has DNS records; " + SKIPPED for i in skipped)
    assert sum(1 for f in findings if f.check == "lookalike_has_dns") == 25  # skipped names keep their DNS finding
    assert lookalike.BUDGET_S == 90.0 and lookalike.CRT_TIMEOUT_S == 5.0 and lookalike.CRT_RETRIES == 1


def test_live_script_prints_a_ns_and_groups(capsys):
    from scripts import lookalike_live
    assert lookalike_live.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "A: 192.0.2.10 | NS: ns1.hosting.example" in out
    assert "IP 192.0.2.10: d3moshop.example, demoshop-care.example" in out


def test_watch_trace_shows_progress_per_candidate(tmp_path):
    import threading
    import time
    from src.web.server import make_server

    def handler(req):
        if req.url.host == "crt.sh":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"Status": 3})
    server = make_server(port=0, data_dir=tmp_path, http_transport=httpx.MockTransport(handler),
                         provider_factory=lambda: (None, "none"), lookalike_sleep=lambda s: None)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    c = httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}", trust_env=False, timeout=60)

    def post(path, body=None):
        return c.post(path, json=body or {}, headers={"X-CSRF-Token": c.get("/api/session").json()["csrf"]})
    try:
        post("/api/signup", {"email": "trace@shop.example", "password": "correct-horse-battery"})
        for path, body in [("/api/profile", {"account_type": "company"}), ("/api/asset", {"asset": "website"}),
                           ("/api/connect", {"connect": True}), ("/api/consent", {"allow": True}),
                           ("/api/official", {"domains": ["demoshop.example"], "numbers": []})]:
            assert post(path, body).status_code == 200
        run_id = post("/api/lookalike", {"domain": "demoshop.example"}).json()["run"]["id"]
        for _ in range(300):
            if c.get(f"/api/runs/{run_id}").json()["run"]["status"] != "running":
                break
            time.sleep(0.1)
        steps = c.get(f"/api/runs/{run_id}/trace?after=0").json()["steps"]
        assert len(steps) == 25 and steps[0]["result_summary"].startswith("candidate 1 of 25: ")
        assert steps[-1]["result_summary"].startswith("candidate 25 of 25: ")
    finally:
        c.close()
        server.shutdown()
        server.server_close()
