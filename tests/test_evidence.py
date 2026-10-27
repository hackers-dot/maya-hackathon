import sqlite3

import pytest

from src.evidence import MAX_BODY_BYTES, EvidenceStore, mask, redact_text

# FAKE test secrets, built by concatenation so no secret-shaped literal sits in the repo
API_KEY = "sk-" + "test" + "FAKE1234567890abcdXY"
PASSWORD = "hunter2-" + "FAKE-password-99"
URL_TOKEN = "FAKEtoken" + "0123456789"
BEARER = "FAKEbearer" + "0123456789xyz"
COOKIE_VAL = "FAKEsession" + "value0099"
PEM_INNER = "MIIFAKEKEYMATERIAL" + "0123"
PEM = "-----BEGIN " + "PRIVATE KEY-----\n" + PEM_INNER + "\n-----END " + "PRIVATE KEY-----"
SECRETS = [API_KEY, PASSWORD, URL_TOKEN, BEARER, COOKIE_VAL, PEM_INNER]


@pytest.fixture
def store(tmp_path):
    return EvidenceStore(tmp_path / "ev.db")


def add_secret_record(store):
    return store.add(
        "GET", f"http://127.0.0.1/login?user=a&token={URL_TOKEN}",
        [("User-Agent", "t"), ("Authorization", "Bearer " + BEARER), ("Cookie", f"session={COOKIE_VAL}")],
        200, [("Content-Type", "text/plain"), ("X-Debug", "dropped")],
        f"password={PASSWORD}\n{PEM}\nkey in use: {API_KEY}\n".encode())


def test_roundtrip_and_verify(store):
    rid = store.add("GET", "http://127.0.0.1/", [("User-Agent", "t")], 200,
                    [("Server", "x"), ("Content-Type", "text/html")], b"hello")
    rec = store.get(rid)
    assert rec["request"]["method"] == "GET" and rec["response"]["status"] == 200
    assert rec["response"]["body"] == "hello"
    assert rec["captured_at"].endswith("+00:00")
    assert len(rec["sha256"]) == 64
    assert store.verify(rid)


def test_tampering_is_detected(store):
    rid = store.add("GET", "http://127.0.0.1/", [], 200, [], b"original")
    with sqlite3.connect(store.path) as c:
        c.execute("UPDATE evidence SET summary = replace(summary, 'original', 'edited') WHERE id = ?", (rid,))
    c.close()
    assert not store.verify(rid)


def test_unknown_id(store):
    with pytest.raises(KeyError):
        store.get("nope")


def test_full_secret_never_stored(store):
    rid = add_secret_record(store)
    raw = store.path.read_bytes()
    for secret in SECRETS:
        assert secret.encode() not in raw, f"secret leaked into DB: {secret[:3]}..."
    rec = store.get(rid)
    text = str(rec)
    assert mask(API_KEY) in text and mask(PASSWORD) in text  # masked form kept for the reader
    assert "Bearer " + mask(BEARER) in text
    assert "[REDACTED PRIVATE KEY]" in text
    assert store.verify(rid)


def test_only_selected_response_headers_kept(store):
    rec = store.get(add_secret_record(store))
    assert [h[0] for h in rec["response"]["headers"]] == ["Content-Type"]


def test_body_truncated_to_2kb(store):
    rid = store.add("GET", "http://127.0.0.1/", [], 200, [], b"a " * 5000)
    assert len(store.get(rid)["response"]["body"].encode()) <= MAX_BODY_BYTES


def test_mask_shape():
    assert mask("abcdefghij") == "abc...ij"
    assert mask("short") == "***"


@pytest.mark.parametrize("text,leak", [
    ("DB_PASSWORD=" + PASSWORD, PASSWORD),
    ('{"api_key": "' + "abc" + 'def123456789"}', "abcdef123456789"),
    ("Authorization: Basic " + "dXNlcjpGQUtFcGFzcw==", "dXNlcjpGQUtFcGFzcw=="),
    ("x " + "ghp_" + "FAKE" * 6 + " y", "ghp_" + "FAKE" * 6),
    ("opaque " + "Zq9" * 12, "Zq9" * 12),
])
def test_redact_text_patterns(text, leak):
    assert leak not in redact_text(text)
