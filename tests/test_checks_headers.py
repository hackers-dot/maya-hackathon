from src.scanner.checks_headers import (check_cookies, check_security_headers, check_version_disclosure,
                                       run_all)
from src.scanner.http_client import Response
from src.severity import severity

GOOD = [
    ("Content-Security-Policy", "default-src 'self'"),
    ("Strict-Transport-Security", "max-age=31536000"),
    ("X-Frame-Options", "DENY"),
    ("X-Content-Type-Options", "nosniff"),
]


def resp(headers, url="http://127.0.0.1:8081/shop/cart?x=1"):
    return Response(url=url, status=200, headers=headers, evidence_id="ev-123")


def test_missing_security_headers():
    findings = check_security_headers(resp([("Content-Type", "text/html")]))
    assert [f.check for f in findings] == ["missing_csp", "missing_hsts", "missing_x_frame_options",
                                           "missing_x_content_type_options"]
    for f in findings:
        assert f.severity == severity(f.check)
        assert f.location == "/shop/cart" and f.target == "http://127.0.0.1:8081"
        assert f.evidence[0].record_id == "ev-123"
        assert f.demo is False


def test_good_headers_no_findings():
    assert run_all(resp(GOOD)) == []


def test_frame_ancestors_replaces_xfo():
    headers = [h for h in GOOD if h[0] != "X-Frame-Options"]
    headers[0] = ("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'")
    assert check_security_headers(resp(headers)) == []


def test_xcto_wrong_value_flagged():
    headers = GOOD[:3] + [("X-Content-Type-Options", "yes")]
    assert [f.check for f in check_security_headers(resp(headers))] == ["missing_x_content_type_options"]


def test_cookie_flags():
    secret = "FAKEsession" + "value0099"
    findings = check_cookies(resp([("Set-Cookie", f"sid={secret}; Path=/"),
                                   ("Set-Cookie", "ok=1; Secure; HttpOnly; SameSite=Lax")]))
    assert sorted(f.check for f in findings) == ["cookie_missing_httponly", "cookie_missing_samesite",
                                                 "cookie_missing_secure"]
    assert all(f.id.endswith(":sid") for f in findings)
    assert all(secret not in f.evidence[0].data for f in findings)  # cookie value redacted in evidence


def test_version_disclosure():
    findings = check_version_disclosure(resp([("Server", "nginx/1.25.3"), ("X-Powered-By", "PHP/8.1.2")]))
    assert [f.id for f in findings] == ["server_version_disclosure:/shop/cart:server",
                                        "server_version_disclosure:/shop/cart:x-powered-by"]
    assert check_version_disclosure(resp([("Server", "nginx")])) == []


def test_finding_ids_unique():
    findings = run_all(resp([("Set-Cookie", "a=1"), ("Set-Cookie", "b=2"), ("Server", "x/1.0")]))
    assert len({f.id for f in findings}) == len(findings) == 4 + 6 + 1
