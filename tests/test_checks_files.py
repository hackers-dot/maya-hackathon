import httpx
import pytest

from src.evidence import EvidenceStore
from src.scanner.checks_files import (check_backup_file, check_directory_listing, check_env_file,
                                      check_git_head)
from src.scanner.http_client import HttpClient, Response
from src.scanner.runner import NotRunnable, run_check, scan_all
from src.severity import severity

FAKE_SECRET = "fake-" + "secret-value-not-real-123"


def resp(body: bytes, ctype="text/plain", status=200, url="http://127.0.0.1:8081/x"):
    return Response(url=url, status=status, headers=[("content-type", ctype)], body=body, evidence_id="ev1")


def test_env_file():
    f = check_env_file(resp(b"# c\nAPP_ENV=prod\nAPI_KEY=" + FAKE_SECRET.encode() + b"\n", url="http://127.0.0.1/.env"))
    assert [x.check for x in f] == ["exposed_env_file"]
    assert f[0].severity == severity("exposed_env_file") == "critical" and f[0].location == "/.env"
    assert FAKE_SECRET not in f[0].evidence[0].data  # body excerpt redacted
    assert f[0].demo is True


@pytest.mark.parametrize("r", [
    resp(b"APP_ENV=prod", ctype="text/html"),       # catch-all HTML page
    resp(b"APP_ENV=prod", status=404),
    resp(b"just some text"),
    resp(b""),
])
def test_env_file_negative(r):
    assert check_env_file(r) == []


@pytest.mark.parametrize("body,hit", [
    (b"ref: refs/heads/main\n", True),
    (b"z" * 40 + b"\n", False),  # 40 chars but not hex
    (b"0123456789abcdef0123456789abcdef01234567\n", True),
    (b"<html>not found</html>", False),
])
def test_git_head(body, hit):
    assert bool(check_git_head(resp(body))) is hit


@pytest.mark.parametrize("r,hit", [
    (resp(b"PK\x03\x04zip", ctype="application/zip"), True),
    (resp(b"-- dump", ctype="application/octet-stream"), True),
    (resp(b"<html>home</html>", ctype="text/html"), False),
    (resp(b"x", status=404), False),
])
def test_backup_file(r, hit):
    assert bool(check_backup_file(r)) is hit


@pytest.mark.parametrize("body,hit", [
    (b"<title>Index of /backup/</title>", True),
    (b"<html><head><title>Directory listing for /</title>", True),  # Python http.server style
    (b"<title>Demo Shop</title>", False),
])
def test_directory_listing(body, hit):
    assert bool(check_directory_listing(resp(body, ctype="text/html"))) is hit


def test_run_check_filters_and_refuses_non_http_checks():
    seen = []

    def site(req):
        seen.append(req.url.path)
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>hi</p>")

    c = HttpClient(transport=httpx.MockTransport(site), min_interval=0)
    assert [f.check for f in run_check(c, "http://127.0.0.1:8081", "missing_hsts", "/login")] == ["missing_hsts"]
    assert seen == ["/login"]
    with pytest.raises(NotRunnable):
        run_check(c, "http://127.0.0.1:8081", "missing_spf", "/")
    assert seen == ["/login"]  # no request for a non-runnable check


@pytest.mark.parametrize("fixed", [False, True], ids=["vulnerable", "fixed"])
def test_scan_all_against_demo(start_demo, tmp_path, fixed):
    store = EvidenceStore(tmp_path / "ev.db")
    findings = scan_all(HttpClient(store=store), start_demo(fixed))
    checks = {f.check for f in findings}
    expected = set() if fixed else {
        "missing_csp", "missing_hsts", "missing_x_frame_options", "missing_x_content_type_options",
        "cookie_missing_secure", "cookie_missing_httponly", "cookie_missing_samesite",
        "server_version_disclosure", "exposed_env_file", "exposed_backup_file", "directory_listing"}
    assert checks == expected
    assert "exposed_git_dir" not in checks  # demo serves no .git
    for f in findings:
        assert f.demo is True and f.severity == severity(f.check)
        assert store.verify(f.evidence[0].record_id)
    if not fixed:
        locations = {f.check: f.location for f in findings}
        assert (locations["exposed_env_file"], locations["exposed_backup_file"], locations["directory_listing"]) == (
            "/.env", "/db.sql", "/backup/")
