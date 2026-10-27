import re

import pytest

from src.report import (MODEL_FINISHED, MODEL_UNFINISHED, NO_ISSUES, OWASP, SEVERITY_ORDER, TEXT, build_report,
                        to_html, to_markdown, write_report)
from src.scanner.findings import make_finding
from src.severity import TABLE, severity

BANNED = re.compile(r"secure|hack|compliant", re.I)
USAGE = {"prompt_tokens": 1200, "completion_tokens": 300, "reasoning_tokens": 200, "total_tokens": 1500}


def report_for(checks, demo=True, quote="HTTP 200", insufficient=(), verified=(), finished=True):
    """No store: each finding's evidence line falls back to the excerpt captured by the check (`quote` here)."""
    findings = {f.id: f for f in (make_finding(c, "http://127.0.0.1:8081", "/", "test", quote, "tech", f"ev-{c}",
                                               demo=demo) for c in checks)}
    claims = {"verified": list(verified), "insufficient": list(insufficient)}
    return build_report(findings, None, "meta-models/test-model", "low", USAGE, claims, finished)


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
    assert md.index("## Detected by deterministic checks") < md.index("### Fix first") < md.index("[CRITICAL]")


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


@pytest.mark.parametrize("finished", [True, False])
def test_zero_findings_wording(finished):
    r = build_report({}, None, "m", "low", USAGE, model_finished=finished)
    for text in (to_markdown(r), to_html(r)):
        assert "No issues detected in the checks run" in text and NO_ISSUES == "No issues detected in the checks run."
        assert not BANNED.search(prose(text))
        assert "secure" not in text.lower()  # not even inside code spans


def test_report_with_findings_only_when_model_did_not_finish():
    r = report_for(["exposed_env_file", "missing_csp"], finished=False)  # no claims at all
    md, page = to_markdown(r), to_html(r)
    for text in (md, page):
        assert MODEL_UNFINISHED in text and "model did not finish; findings are from code checks" in text
        assert "Detected by deterministic checks" in text and "Issues detected: 2" in text
        assert "None. The model did not finish." in text
    assert "[CRITICAL] Configuration file appears publicly readable" in md


def verified_claim(check, priority, note=None, note_dropped=None):
    return {"finding_id": f"{check}:/", "check": check, "severity": "info",  # wrong on purpose: table wins
            "text": f"/ on http://127.0.0.1:8081 ({check}).", "priority": priority,
            "supporting_evidence": "recorded response headers: content-type", "note": note,
            "note_dropped": note_dropped}


def test_model_verified_claims_section_is_separate():
    verified = [verified_claim("missing_x_content_type_options", 1, note="Send nosniff on /."),
                verified_claim("missing_csp", 2, note_dropped="note mentions '/admin', which is not in this finding's evidence")]
    r = report_for(["missing_csp", "missing_x_content_type_options"], verified=verified)
    r_claims = dict(r)
    md = to_markdown(r_claims)
    deterministic = md[:md.index("## Model-verified claims")]
    claims = md[md.index("## Model-verified claims"):]
    assert MODEL_FINISHED in md
    # model's priority order kept (low finding first), severity still from the table
    assert claims.index("1. **LOW**: / on http://127.0.0.1:8081 (missing_x_content_type_options).") < \
        claims.index("2. **MEDIUM**: / on http://127.0.0.1:8081 (missing_csp).")
    assert "   - Code evidence (verified): ` recorded response headers: content-type `" in claims
    assert "   - Model note (unverified model text): ` Send nosniff on /. `" in claims
    assert "   - Model note dropped: note mentions '/admin'" in claims
    # the deterministic section is unchanged and still severity-sorted
    assert deterministic.index("[MEDIUM]") < deterministic.index("[LOW]")
    page = to_html(r)
    assert "Model note (unverified model text): <q>Send nosniff on /.</q>" in page


def test_model_note_is_escaped_in_html():
    r = report_for(["missing_csp"], verified=[verified_claim("missing_csp", 1, note="<script>x</script>")])
    page = to_html(r)
    assert "<script" not in page.lower() and "&lt;script&gt;" in page


def test_write_report(tmp_path):
    md, page = write_report(report_for(["missing_csp"]), tmp_path / "reports", "r1")
    assert md.read_text(encoding="utf-8").startswith("# MAYA report")
    assert page.read_text(encoding="utf-8").startswith("<!doctype html>")
