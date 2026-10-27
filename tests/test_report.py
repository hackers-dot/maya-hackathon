import re

import pytest

from src.report import OWASP, SEVERITY_ORDER, TEXT, build_report, to_html, to_markdown, write_report
from src.scanner.findings import make_finding
from src.severity import TABLE, severity

BANNED = re.compile(r"secure|hack|compliant", re.I)
USAGE = {"prompt_tokens": 1200, "completion_tokens": 300, "reasoning_tokens": 200, "total_tokens": 1500}


def finding(check, location="/", demo=True, target="http://127.0.0.1:8081"):
    return make_finding(check, target, location, "test", "excerpt", "tech", f"ev-{check}", demo=demo)


def report_for(checks, demo=True, quote="HTTP 200", insufficient=()):
    findings = {f.id: f for f in (finding(c, demo=demo) for c in checks)}
    verified = [{"finding_id": fid, "evidence_id": f.evidence[0].record_id, "quoted_evidence": quote,
                 "severity": "info"}  # a wrong severity here must be ignored
                for fid, f in findings.items()]
    return build_report(verified, findings, "meta-models/test-model", "low", USAGE, list(insufficient))


def prose(text: str) -> str:
    """Text with code spans removed: `...` in markdown, <code>...</code> in HTML."""
    text = re.sub(r"<code>.*?</code>", "", text, flags=re.S)
    return re.sub(r"(`+).*?\1", "", text, flags=re.S)


def test_every_check_has_fixed_text_and_category():
    assert set(TEXT) == set(TABLE) == set(OWASP)


def test_sorted_by_table_severity_and_fix_first():
    r = report_for(["missing_x_content_type_options", "exposed_env_file", "missing_csp", "server_version_disclosure"])
    sev = [i["severity"] for i in r["items"]]
    assert sev == sorted(sev, key=SEVERITY_ORDER.index)
    assert sev[0] == "critical" and all(i["severity"] == severity(i["check"]) for i in r["items"])
    assert [i["check"] for i in r["fix_first"]] == [i["check"] for i in r["items"][:3]]
    md = to_markdown(r)
    assert md.index("## Fix first") < md.index("## Findings")


def test_all_checks_render_without_banned_words():
    r = report_for(sorted(TABLE), insufficient=[{"finding_id": "x:/", "reason": "unknown finding_id"}])
    md, page = to_markdown(r), to_html(r)
    assert not BANNED.search(prose(md)), BANNED.findall(prose(md))
    assert not BANNED.search(prose(page)), BANNED.findall(prose(page))
    assert "compliant" not in md.lower() and "compliant" not in page.lower()
    assert md.count("mapped to") == len(TABLE)
    assert "outside the OWASP Top 10" in md
    for t in TEXT.values():
        assert t.analogy.startswith("Like ")


def test_header_and_demo_banner():
    md = to_markdown(report_for(["missing_csp"]))
    assert "- Model: ` meta-models/test-model `" in md and "- Reasoning effort: ` low `" in md
    assert "mapped to OWASP Top 10 A05:2021 Security Misconfiguration" in md
    assert "prompt 1200, completion 300 (reasoning 200), total 1500" in md
    assert "DEMO:" in md
    assert "DEMO:" not in to_markdown(report_for(["missing_csp"], demo=False))


def test_html_escapes_everything_and_is_self_contained():
    evil = '<script>alert("x")</script><img src=x onerror=1>'
    page = to_html(report_for(["exposed_env_file"], quote=evil))
    assert "<script" not in page.lower() and "<img" not in page.lower()
    assert "&lt;script&gt;" in page
    tags = re.findall(r"<[^>]+>", page)
    assert not [t for t in tags if re.search(r"\b(src|href|on\w+)\s*=", t, re.I)]  # no loading attributes or handlers
    assert "<link" not in page.lower() and "@import" not in page.lower() and "url(" not in page.lower()


def test_markdown_code_span_survives_backticks():
    md = to_markdown(report_for(["missing_csp"], quote="a `b` c\nd"))
    assert "`` a `b` c ⏎ d ``" in md


def test_empty_report_wording():
    r = build_report([], {}, "m", "low", USAGE)
    for text in (to_markdown(r), to_html(r)):
        assert "No issues were detected in the checks run." in text
        assert not BANNED.search(prose(text))


def test_write_report(tmp_path):
    md, page = write_report(report_for(["missing_csp"]), tmp_path / "reports", "r1")
    assert md.read_text(encoding="utf-8").startswith("# MAYA report")
    assert page.read_text(encoding="utf-8").startswith("<!doctype html>")
