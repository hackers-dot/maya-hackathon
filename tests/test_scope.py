import pytest

from src.scope import OutOfScope, check_target


@pytest.mark.parametrize("url", [
    "http://localhost:8000/",
    "http://LOCALHOST/login",
    "http://127.0.0.1/",
    "https://127.5.5.5:8443/x",
    "http://[::1]:8080/",
])
def test_loopback_allowed(url):
    assert check_target(url)


@pytest.mark.parametrize("url", [
    "http://8.8.8.8/",                    # public IP
    "http://10.0.0.1/",                   # private but not ours
    "https://example.com/",               # domain
    "http://localhost.evil.com/",         # lookalike
    "http://169.254.169.254/latest/",     # link-local (cloud metadata)
    "http://[fe80::1]/",                  # IPv6 link-local
    "http://127.0.0.1@evil.com/",         # userinfo trick
    "http://evil.com@127.0.0.1/",         # userinfo, any direction
    "http://127.0.0.1\\@evil.com/",       # backslash trick
    "http://2130706433/",                 # decimal 127.0.0.1
    "http://0x7f000001/",                 # hex 127.0.0.1
    "http://0x7f.0.0.1/",                 # hex octet
    "http://0177.0.0.1/",                 # octal octet
    "http://127.1/",                      # short form
    "file:///etc/passwd",
    "ftp://127.0.0.1/",
    "http:///nohost",
    "http://127.0.0.1:99999/",            # bad port
    "http://127.0.0.1/\nHost: evil.com",  # control chars
    "",
])
def test_refused(url):
    with pytest.raises(OutOfScope):
        check_target(url)


def test_verified_list_empty_by_default_and_exact_match():
    with pytest.raises(OutOfScope):
        check_target("https://myshop.example/")
    assert check_target("https://myshop.example/", {"MyShop.example"}) == "myshop.example"
    with pytest.raises(OutOfScope):
        check_target("https://sub.myshop.example/", {"myshop.example"})
