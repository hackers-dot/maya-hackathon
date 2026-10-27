import json

import httpx
import pytest

from src import scope
from src.ownership import (TOKEN_PREFIX, WELL_KNOWN_PATH, VerifiedTargets, meta_tokens, new_token,
                           verify_ownership)

TARGET = "https://shop.example/"


@pytest.fixture
def targets(tmp_path):
    return VerifiedTargets(tmp_path / "verified_targets.json")


def site(routes: dict):
    """Fake HTTP for any host; records every URL requested. routes: path -> httpx.Response kwargs."""
    seen = []

    def handler(req):
        seen.append(str(req.url))
        if req.url.host != "shop.example":
            return httpx.Response(200, text="token everywhere " + routes.get("_evil_body", ""))
        kw = routes.get(req.url.path)
        return httpx.Response(**kw) if kw else httpx.Response(404, text="nope")

    return httpx.MockTransport(handler), seen


def page(meta_token=None, extra=""):
    meta = f'<meta name="maya-verify" content="{meta_token}">' if meta_token else ""
    return {"status_code": 200, "headers": {"content-type": "text/html"},
            "text": f"<!doctype html><html><head>{meta}</head><body>{extra}</body></html>"}


def test_new_token_is_random():
    a, b = new_token(), new_token()
    assert a.startswith(TOKEN_PREFIX) and b.startswith(TOKEN_PREFIX) and a != b and len(a) >= 40


def test_meta_tag_passes_and_is_persisted(targets):
    token = new_token()
    transport, seen = site({"/": page(token)})
    r = verify_ownership(TARGET, token, targets, transport=transport)
    assert (r.verified, r.method, r.host) == (True, "meta", "shop.example")
    saved = json.loads(targets.path.read_text())
    assert saved["shop.example"]["method"] == "meta" and saved["shop.example"]["verified_at"].endswith("+00:00")
    assert seen == ["https://shop.example/"]
    assert scope.check_target("https://shop.example/login", targets.hosts()) == "shop.example"


def test_well_known_file_passes(targets):
    token = new_token()
    transport, seen = site({"/": page(), WELL_KNOWN_PATH: {"status_code": 200, "text": token + "\n"}})
    r = verify_ownership(TARGET, token, targets, transport=transport)
    assert (r.verified, r.method) == (True, "file")
    assert seen == ["https://shop.example/", "https://shop.example" + WELL_KNOWN_PATH]


def test_meta_attribute_order_and_case(targets):
    token = new_token()
    html = {"status_code": 200, "text": f'<html><head><META CONTENT="{token}" NAME="Maya-Verify" /></head></html>'}
    transport, _ = site({"/": html})
    assert verify_ownership(TARGET, token, targets, transport=transport).verified


def test_wrong_token_fails(targets):
    token, other = new_token(), new_token()
    transport, _ = site({"/": page(other), WELL_KNOWN_PATH: {"status_code": 200, "text": other}})
    r = verify_ownership(TARGET, token, targets, transport=transport)
    assert not r.verified and "token not found" in r.reason
    assert not targets.path.exists() and targets.hosts() == frozenset()
    with pytest.raises(scope.OutOfScope):
        scope.check_target("https://shop.example/", targets.hosts())


def test_token_in_a_different_place_fails(targets):
    token = new_token()
    transport, seen = site({
        "/": page(extra=f"<p>{token}</p><!-- <meta name=\"maya-verify\" content=\"{token}\"> -->"),
        "/maya-verify.txt": {"status_code": 200, "text": token},          # wrong path
        WELL_KNOWN_PATH: {"status_code": 200, "text": f"prefix {token}"},  # not the whole content
    })
    r = verify_ownership(TARGET, token, targets, transport=transport)
    assert not r.verified
    assert "https://shop.example/maya-verify.txt" not in seen  # only the two fixed paths are fetched
    assert len(seen) == 2


def test_redirect_to_another_host_fails(targets):
    token = new_token()
    transport, seen = site({
        "/": {"status_code": 302, "headers": {"location": "https://evil.example/"}},
        WELL_KNOWN_PATH: {"status_code": 302, "headers": {"location": "https://evil.example/t.txt"}},
        "_evil_body": token,
    })
    r = verify_ownership(TARGET, token, targets, transport=transport)
    assert not r.verified and "refused" in r.reason
    assert not any("evil.example" in u for u in seen)  # the other host was never contacted
    assert targets.hosts() == frozenset()


def test_same_host_redirect_is_followed(targets):
    token = new_token()
    transport, _ = site({"/": {"status_code": 301, "headers": {"location": "/home"}}, "/home": page(token)})
    assert verify_ownership(TARGET, token, targets, transport=transport).method == "meta"


def test_unverified_hosts_stay_refused(targets):
    token = new_token()
    transport, _ = site({"/": page(token)})
    verify_ownership(TARGET, token, targets, transport=transport)
    assert "shop.example" in targets.hosts()
    for other in ["https://other.example/", "https://sub.shop.example/", "https://shop.example.evil.com/"]:
        with pytest.raises(scope.OutOfScope):
            scope.check_target(other, targets.hosts())


def no_network():
    return httpx.MockTransport(lambda req: pytest.fail(f"must not fetch {req.url}"))


def test_loopback_needs_no_verification(targets):
    r = verify_ownership("http://127.0.0.1:8081/", new_token(), targets, transport=no_network())
    assert (r.verified, r.method) == (True, "loopback") and not targets.path.exists()


@pytest.mark.parametrize("url", ["https://8.8.8.8/", "https://user@shop.example/", "ftp://shop.example/",
                                 "https://2130706433/"])
def test_bad_targets_refused_without_fetching(targets, url):
    r = verify_ownership(url, new_token(), targets, transport=no_network())
    assert not r.verified and r.reason.startswith("target refused")


@pytest.mark.parametrize("bad", ["", "abc", TOKEN_PREFIX, None])
def test_bad_token_rejected(targets, bad):
    with pytest.raises(ValueError):
        verify_ownership(TARGET, bad, targets, transport=no_network())


def test_meta_tokens_parser():
    assert meta_tokens('<meta name="maya-verify" content=" t1 "><meta name="other" content="x">') == ["t1"]
    assert meta_tokens("<!-- <meta name=\"maya-verify\" content=\"t\"> -->") == []
