import json
import socket

import httpx
import pytest

from scripts import live_agent_run as live

FAKE_KEY = "fake-key-for-tests-not-real"
SECRET_REASONING = "PRIVATE-REASONING-SHOULD-NEVER-PRINT"


@pytest.fixture
def loopback_only(monkeypatch):
    """Any socket connection other than to 127.0.0.1 fails the test."""
    real = socket.create_connection

    def guard(address, *a, **k):
        assert address[0] in ("127.0.0.1", "localhost"), f"non-loopback connection to {address}"
        return real(address, *a, **k)

    monkeypatch.setattr(socket, "create_connection", guard)


def test_hard_caps():
    assert (live.MAX_STEPS, live.TOKEN_CAP, live.REASONING_EFFORT) == (6, 40_000, "low")


def test_dry_run_vulnerable(loopback_only, tmp_path, capsys):
    assert live.main(["--dry-run"], data_dir=tmp_path) == 0
    out = capsys.readouterr().out
    assert "demo app VULNERABLE at http://127.0.0.1:" in out
    assert "step 1: tool=run_check" in out and "reasoning=80" in out and "model latency=" in out
    assert "stop_reason=final" in out
    assert "[CRITICAL]" in out and "[MEDIUM]" in out
    assert "exposed_git_dir:/.git/HEAD: unknown finding_id" in out
    assert "missing_csp:/: claim_type 'file_exposed' does not match check 'missing_csp'" in out
    assert "(a) model quote" not in out  # diagnostics only with --show-dropped
    assert "Verified claims (ordered by the model's priority):" in out
    assert "note dropped: note mentions '/admin'" in out  # invented path: note dropped, claim kept
    reports_md = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "reports").glob("*.md"))
    assert "recorded response headers: content-length, content-type, server, set-cookie" in reports_md
    assert "Detected by deterministic checks" in reports_md and "model finished with a final answer" in reports_md
    claims = reports_md[reports_md.index("## Model-verified claims"):]
    assert "Order: ordered by the model's priority." in claims
    assert ("Model note (unverified model text): ` Anyone can download /.env; block it and rotate the keys it holds. `"
            in claims)
    assert "Code evidence (verified): ` API_KEY=fak...al `" in claims  # secret-looking key first, masked
    assert "Model note dropped: note mentions '/admin'" in claims
    assert claims.index("**CRITICAL**") < claims.index("**MEDIUM**")
    reports = list((tmp_path / "reports").iterdir())
    assert sorted(p.suffix for p in reports) == [".html", ".md"]
    md = next(p for p in reports if p.suffix == ".md").read_text(encoding="utf-8")
    assert "DEMO:" in md and "exposed_env_file" in md


def test_show_dropped_diagnostic(loopback_only, tmp_path, capsys):
    assert live.main(["--dry-run", "--show-dropped"], data_dir=tmp_path) == 0
    out = capsys.readouterr().out
    # unknown finding: quote shown, no evidence line to compare against
    assert "(a) model quote: 'ref: refs'" in out and "(b) evidence line: (no such finding)" in out
    assert "(c) diff hint: n/a" in out
    # existing finding: the summary-prefixed quote vs the real evidence line, with a diff hint
    assert "(a) model quote: 'missing_csp:/ | missing_csp | recorded response headers: content-length'" in out
    assert "(b) evidence line: 'recorded response headers: content-length, content-type, server, set-cookie'" in out
    assert "whitespace-normalized equal=no, quote inside line=no, line inside quote=no" in out


def test_diagnose_unit():
    lines = live.diagnose({"finding_id": "x", "quoted_evidence": "q" * 300}, {}, None)
    assert lines[0] == f"(a) model quote: {'q' * 100!r}"  # truncated to 100 chars
    assert live.diagnose({"finding_id": "x"}, {}, None)[0] == "(a) model quote: (none)"


def test_dry_run_fixed(loopback_only, tmp_path, capsys):
    assert live.main(["--dry-run", "--fixed"], data_dir=tmp_path) == 0
    out = capsys.readouterr().out
    assert "demo app FIXED" in out
    assert "Verified claims (no verified claims):\n  (none)" in out
    assert out.count("unknown finding_id") == 4
    md = next((tmp_path / "reports").glob("*.md")).read_text(encoding="utf-8")
    assert "No issues detected in the checks run." in md and "secure" not in md.lower()


def test_report_built_when_model_never_finishes(loopback_only, monkeypatch, tmp_path, capsys):
    """The live failure: the model keeps calling tools and never answers. The report still lists findings."""
    monkeypatch.setenv("BASETEN_API_KEY", FAKE_KEY)
    seen = []
    tool = {"content": None, "tool_calls": [{"id": "a", "type": "function", "function": {
        "name": "run_check", "arguments": '{"check_id": "exposed_env_file", "path": "/.env"}'}}]}
    replies = [reply(tool, finish="tool_calls") for _ in range(live.MAX_STEPS)]
    assert live.main([], transport=model_transport(seen, replies), data_dir=tmp_path) == 0
    out = capsys.readouterr().out
    assert len(seen) == live.MAX_STEPS and seen[-1]["tool_choice"] == "none"
    assert "stop_reason=forced_final" in out and "Model finished with valid claims: no" in out
    md = next((tmp_path / "reports").glob("*.md")).read_text(encoding="utf-8")
    assert "model did not finish; findings are from code checks" in md
    assert "[CRITICAL] Configuration file appears publicly readable" in md
    assert "- Supporting evidence: ` API_KEY=fak...al `" in md  # first secret-looking KEY=value line, masked
    assert "fake-demo-key-not-real" not in md
    assert "None. The model did not finish." in md


def model_transport(seen, replies):
    """Fake model endpoint for the real Provider (no network); the demo app stays real on loopback."""
    def handler(req):
        body = json.loads(req.content)
        seen.append(body)
        return httpx.Response(200, json=replies.pop(0))
    return httpx.MockTransport(handler)


def reply(message, usage_total=500, finish="stop"):
    return {"choices": [{"message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": usage_total - 100, "completion_tokens": 100, "total_tokens": usage_total,
                      "completion_tokens_details": {"reasoning_tokens": 70}}}


def test_real_provider_path_with_fake_model(loopback_only, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("BASETEN_API_KEY", FAKE_KEY)  # test process only
    seen = []
    replies = [
        reply({"content": None, "reasoning_content": SECRET_REASONING, "tool_calls": [
            {"id": "a", "type": "function",
             "function": {"name": "run_check", "arguments": '{"check_id": "missing_csp", "path": "/"}'}}]},
              finish="tool_calls"),
        reply({"content": f"<think>{SECRET_REASONING}</think>" + json.dumps({"claims": [
            {"finding_id": "missing_csp:/", "claim_type": "header_missing",
             "quoted_evidence": "recorded response headers: content-length"}]})}),
    ]
    assert live.main([], transport=model_transport(seen, replies), data_dir=tmp_path) == 0
    out = capsys.readouterr().out
    assert SECRET_REASONING not in out and FAKE_KEY not in out
    assert all(b["reasoning_effort"] == "low" and b["max_tokens"] == 1500 for b in seen)
    assert seen[0]["tools"][0]["function"]["name"] == "run_check" and seen[0]["tool_choice"] == "auto"
    assert "model=meta-models/muse-spark-open-1026" in out
    assert "[MEDIUM]" in out and "reasoning=70" in out
    for report in (tmp_path / "reports").iterdir():
        assert SECRET_REASONING not in report.read_text(encoding="utf-8")


def test_budget_cap_stops_run(loopback_only, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("BASETEN_API_KEY", FAKE_KEY)
    replies = [reply({"content": None, "tool_calls": [
        {"id": "a", "type": "function", "function": {"name": "list_findings", "arguments": "{}"}}]},
        usage_total=50_000, finish="tool_calls")]
    assert live.main([], transport=model_transport([], replies), data_dir=tmp_path) == 0
    assert "stop_reason=budget_exceeded" in capsys.readouterr().out


def test_model_http_error_never_prints_key(loopback_only, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("BASETEN_API_KEY", FAKE_KEY)
    transport = httpx.MockTransport(lambda req: httpx.Response(401, text=f"bad {req.headers['authorization']}"))
    assert live.main([], transport=transport, data_dir=tmp_path) == 1
    out = capsys.readouterr().out
    assert "Model call failed: HTTP 401" in out and FAKE_KEY not in out
