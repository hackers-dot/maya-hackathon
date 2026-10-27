from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from src.models import Evidence, Finding, sha256


def finding(**over):
    base = dict(id="F-1", check="missing-csp", target="http://127.0.0.1:8000", location="/login",
                severity="medium", evidence=[Evidence.capture("response headers", "HTTP/1.1 200 OK")],
                plain_explanation="p", technical_explanation="t")
    return Finding(**(base | over))


def test_valid_finding():
    f = finding()
    assert f.demo is False
    assert f.evidence[0].sha256 == sha256("HTTP/1.1 200 OK")
    assert f.evidence[0].captured_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize("over", [
    {"severity": "urgent"},
    {"evidence": []},
    {"id": ""},
    {"plain_explanation": ""},
])
def test_invalid_finding(over):
    with pytest.raises(ValidationError):
        finding(**over)


def test_evidence_hash_must_match_data():
    with pytest.raises(ValidationError, match="does not match"):
        Evidence(description="d", data="x", sha256=sha256("y"), captured_at=datetime.now(timezone.utc))


@pytest.mark.parametrize("ts", [
    datetime(2026, 10, 27, 12, 0),                                    # naive
    datetime(2026, 10, 27, 12, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))),  # not UTC
])
def test_evidence_timestamp_must_be_utc(ts):
    with pytest.raises(ValidationError):
        Evidence(description="d", data="x", sha256=sha256("x"), captured_at=ts)
