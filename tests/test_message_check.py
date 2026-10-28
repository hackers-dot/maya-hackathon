"""Web 8: suspicious message check. Scripted fake model only; the test image is a stdlib PNG with no text."""
import base64
import io
import json
import socket
import struct
import threading
import zipfile
import zlib
from pathlib import Path

import httpx
import pytest

from src import brand_check
from src.brand_check import (KEYS, MESSAGE_CHECKS, NOT_IN_LIST, RESEMBLES, TEMPLATES, ImageRefused, image_type,
                             normalise_domain, parse_extraction, verdict_domain)
from src.evidence_pack import verify_zip
from src.provider import ChatResult, MockProvider
from src.report import TEXT
from src.severity import TABLE
from src.web.server import make_server

PASSWORD = "correct-horse-battery"  # test-only
SRC = Path(__file__).resolve().parent.parent / "src"
FORBIDDEN = ("fake", "scam", "fraud")


def png(width=24, height=16, rgb=(40, 90, 200)) -> bytes:
    """A solid-colour PNG built with zlib + struct (no text, no external library)."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    rows = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


IMAGE = png()
EXTRACTION = {
    "urls": ["https://shop-example-login.com/pay", "https://www.shopexample.com/help", "weather.example"],
    "phone_numbers": ["+1 (555) 010-0199", "+1 555 010 0100"],
    "brand_names": ["ShopExample"],
    "payment_requests": ["Pay 49.99 by gift card", "Your login: password=SuperSecretValue123"],
    "urgency_phrases": ["Act within 1 hour"],
    "confidence": "high",  # invented field: must be dropped
}


class Reader(MockProvider):
    """Scripted fake model that 'reads' the image: returns a fixed JSON extraction and records the request."""

    def __init__(self, extraction):
        super().__init__()
        self.answer = json.dumps(extraction) if not isinstance(extraction, str) else extraction

    def chat(self, messages, **kw):
        super().chat(messages, **kw)
        return ChatResult(self.answer, True, {"prompt_tokens": 900, "completion_tokens": 80, "total_tokens": 980})


# --- pure checks ---

def test_image_magic_bytes():
    assert image_type(IMAGE) == "image/png"
    assert image_type(b"\xff\xd8\xff\xe0" + b"\x00" * 20) == "image/jpeg"
    for bad in (b"GIF89a....", b"<svg xmlns='x'>", b"PNG but text", b""):
        with pytest.raises(ImageRefused, match="PNG or JPEG"):
            image_type(bad)
    with pytest.raises(ImageRefused, match="5 MB"):
        image_type(b"\x89PNG\r\n\x1a\n" + b"0" * (5 * 1024 * 1024))


def test_schema_enforced_and_invented_fields_dropped():
    out, dropped = parse_extraction(json.dumps({**EXTRACTION, "urls": EXTRACTION["urls"] + ["x" * 201],
                                                "brand_names": "not a list", "verdict": "scam"}))
    assert set(out) == set(KEYS) and out["brand_names"] == []
    assert len(out["urls"]) == 3
    assert "field 'confidence' is not in the schema" in dropped and "field 'verdict' is not in the schema" in dropped
    assert "urls: an item is longer than 200 characters" in dropped and "brand_names: not a list" in dropped
    out, dropped = parse_extraction(json.dumps({"urls": [f"a{i}.example" for i in range(25)]}))
    assert len(out["urls"]) == 20 and dropped.count("urls: more than 20 items") == 5
    with pytest.raises(ValueError, match="did not return JSON"):
        parse_extraction("I think this is a scam")
    with pytest.raises(ValueError, match="JSON object"):
        parse_extraction("[1, 2]")
    assert brand_check.SCHEMA["additionalProperties"] is False


def test_secrets_in_image_text_are_masked():
    out, _ = parse_extraction(json.dumps(EXTRACTION))
    assert out["payment_requests"][1] == "Your login: password=Sup...23"
    assert "SuperSecretValue123" not in json.dumps(out)


OFFICIAL = ["shopexample.com"]


@pytest.mark.parametrize("candidate,rule", [
    ("shopexample.net", "same name, different ending"),
    ("shop-example.com", "extra or missing hyphen"),
    ("sh0pexample.com", "look-alike characters"),
    ("shopexamp1e.com", "look-alike characters"),
    ("shоpexample.com", "look-alike characters"),  # Cyrillic o, arrives as punycode
    ("shopexample-login.com", "your name plus a keyword"),
    ("secure-shopexample.com", "your name plus a keyword"),
    ("shopexample.com.other.example", "your name as a subdomain of another domain"),
    ("shopexmple.com", "1 character(s) different"),
])
def test_lookalikes_flagged(candidate, rule):
    d = normalise_domain(candidate)
    assert verdict_domain(d, OFFICIAL) == (RESEMBLES, rule, "shopexample.com")


@pytest.mark.parametrize("candidate,verdict", [
    ("https://www.ShopExample.com/login?x=1", "matches your list"),
    ("pay.shopexample.com", "matches your list"),
    ("example.com", NOT_IN_LIST), ("shopping.com", NOT_IN_LIST), ("shopify.com", NOT_IN_LIST),
    ("weather.example", NOT_IN_LIST), ("notshopexample.com", NOT_IN_LIST),
])
def test_non_lookalikes_not_flagged(candidate, verdict):
    assert verdict_domain(normalise_domain(candidate), OFFICIAL)[0] == verdict


def test_short_official_names_never_match_by_distance():
    assert verdict_domain("abd.com", ["abc.com"])[0] == NOT_IN_LIST


def test_wording_never_fake_scam_fraud():
    texts = [TABLE[c].rationale + TABLE[c].remediation + " ".join(TEXT[c].__dict__.values()) for c in MESSAGE_CHECKS]
    texts += list(TEMPLATES.values()) + [NOT_IN_LIST, RESEMBLES, brand_check.PROMPT, brand_check.UNVERIFIED]
    for t in texts:
        assert not any(w in t.lower() for w in FORBIDDEN), t


def test_no_sending_code_anywhere():
    for path in SRC.rglob("*.py"):
        code = path.read_text(encoding="utf-8")
        for needle in ("smtplib", "sendmail", "import imaplib", "mailto:"):
            assert needle not in code, f"{needle} in {path}"


# --- web ---

@pytest.fixture
def web(tmp_path):
    state = {"provider": None, "http": []}

    def transport(req: httpx.Request):  # every outbound request the app makes (DoH included) lands here
        state["http"].append(req)
        if req.url.host == "cloudflare-dns.com":
            if req.url.params["name"] == "shop-example-login.com":
                return httpx.Response(200, json={"Status": 0, "Answer": [{"type": 1, "data": "192.0.2.80"}]})
            return httpx.Response(200, json={"Status": 3})
        return httpx.Response(404)

    server = make_server(port=0, data_dir=tmp_path, http_transport=httpx.MockTransport(transport),
                         provider_factory=lambda: (state["provider"], "fake-vision-model" if state["provider"] else "none"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    clients = []

    def new_client():
        c = httpx.Client(base_url=base, trust_env=False, timeout=60)
        clients.append(c)
        return c

    yield {"new_client": new_client, "state": state, "tmp": tmp_path}
    for c in clients:
        c.close()
    server.shutdown()
    server.server_close()


def post(c, path, body=None):
    return c.post(path, json=body or {}, headers={"X-CSRF-Token": c.get("/api/session").json()["csrf"]})


def company(c, email):
    assert post(c, "/api/signup", {"email": email, "password": PASSWORD}).status_code == 200
    for path, body in [("/api/profile", {"account_type": "company"}), ("/api/asset", {"asset": "website"}),
                       ("/api/connect", {"connect": True}), ("/api/consent", {"allow": True})]:
        assert post(c, path, body).status_code == 200
    return c


def upload(c, data=IMAGE, confirm=True, include=False, name="message.png"):
    headers = {"Content-Type": "image/png", "X-Upload-Name": name,
               "X-CSRF-Token": c.get("/api/session").json()["csrf"]}
    if confirm:
        headers["X-Confirm-Send"] = "yes"
    if include:
        headers["X-Include-Image"] = "yes"
    return c.post("/api/uploads/image", content=data, headers=headers)


def test_no_key_and_no_confirmation(web):
    c = company(web["new_client"](), "nokey@shop.example")
    r = upload(c)
    assert r.status_code == 409 and r.json()["error"].startswith("Model not configured")
    web["state"]["provider"] = Reader(EXTRACTION)
    r = upload(c, confirm=False)
    assert r.status_code == 400 and r.json()["error"] == "confirm that this image will be sent to the model provider"
    assert web["state"]["provider"].calls == []  # nothing was sent without the confirmation
    assert upload(c, data=b"GIF89a....").json()["error"] == "only PNG or JPEG images are accepted"
    assert c.get("/api/assets").json()["assets"] == []


def test_official_lists_are_per_user_and_masked(web):
    c = company(web["new_client"](), "lists@shop.example")
    r = post(c, "/api/official", {"domains": ["https://www.ShopExample.com/"], "numbers": ["+1 555 010 0100"]})
    assert r.status_code == 200
    view = c.get("/api/official").json()
    assert view["domains"] == ["sho...om"] and view["numbers"] == ["+15...00"]
    assert view["counts"] == {"domains": 1, "numbers": 1}
    assert post(c, "/api/official", {"domains": ["not a domain!"], "numbers": []}).status_code == 400
    other = company(web["new_client"](), "other@shop.example")
    assert other.get("/api/official").json()["counts"] == {"domains": 0, "numbers": 0}


def test_end_to_end_model_reads_code_decides(web, monkeypatch):
    c = company(web["new_client"](), "msg@shop.example")
    post(c, "/api/official", {"domains": ["shopexample.com"], "numbers": ["+1 555 010 0100"]})
    reader = web["state"]["provider"] = Reader(EXTRACTION)
    connects = []
    real = socket.create_connection
    monkeypatch.setattr(socket, "create_connection", lambda addr, *a, **k: connects.append(addr) or real(addr, *a, **k))
    r = upload(c)
    assert r.status_code == 200, r.text
    run_id = r.json()["run"]["id"]
    # the request to the model: the image as a data URL, a strict JSON schema
    call = reader.calls[-1]
    parts = call["messages"][1]["content"]
    assert parts[1]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(IMAGE).decode()
    assert call["response_format"]["json_schema"]["strict"] is True and call["max_tokens"] == 1200
    body = c.get(f"/api/runs/{run_id}").json()
    res = body["result"]
    assert body["run"]["mode"] == "message" and res["summary_model"] == "fake-vision-model"
    assert "field 'confidence' is not in the schema" in res["dropped_from_model"]
    by_id = {f["id"]: f for f in res["findings"]}
    assert set(by_id) == {"msg_domain_resembles_yours:domain:shop-example-login.com",
                          "msg_domain_not_in_list:domain:weather.example", "msg_phone_not_in_list:phone:+15...99",
                          "msg_payment_request:payment", "msg_urgency_language:urgency"}
    assert by_id["msg_domain_resembles_yours:domain:shop-example-login.com"]["severity"] == "high"
    verdicts = {(i["kind"], i["text"]): i["verdict"] for i in res["items"]}
    assert verdicts[("url", "https://www.shopexample.com/help")] == "matches your list"
    assert verdicts[("phone", "+1 555 010 0100")] == "matches your list"
    assert all(i["source"] == "read by the model from the image (unverified)" for i in res["items"])
    # passive only: nothing contacted any extracted host, and no outbound HTTP at all during the upload
    assert web["state"]["http"] == []
    assert all(addr[0] in ("127.0.0.1", "localhost") for addr in connects)
    assert "SuperSecretValue123" not in json.dumps(body)


def test_dns_check_only_for_confirmed_domains_via_doh(web):
    c = company(web["new_client"](), "dns@shop.example")
    web["state"]["provider"] = Reader(EXTRACTION)
    run_id = upload(c).json()["run"]["id"]
    assert post(c, f"/api/runs/{run_id}/dns-check", {"domain": "shop-example-login.com"}).status_code == 400
    assert post(c, f"/api/runs/{run_id}/dns-check", {"domain": "elsewhere.example", "confirm": True}).status_code == 400
    assert web["state"]["http"] == []
    r = post(c, f"/api/runs/{run_id}/dns-check", {"domain": "shop-example-login.com", "confirm": True})
    assert r.status_code == 200
    [req] = web["state"]["http"]
    assert req.url.host == "cloudflare-dns.com" and dict(req.url.params) == {"name": "shop-example-login.com", "type": "A"}
    item = next(i for i in r.json()["result"]["items"] if i.get("domain") == "shop-example-login.com")
    assert item["dns"]["resolves"] is True
    r = post(c, f"/api/runs/{run_id}/dns-check", {"domain": "weather.example", "confirm": True})
    item = next(i for i in r.json()["result"]["items"] if i.get("domain") == "weather.example")
    assert item["dns"] == {"resolves": False, "status": "NXDOMAIN", "evidence_id": item["dns"]["evidence_id"]}
    assert {q.url.host for q in web["state"]["http"]} == {"cloudflare-dns.com"}  # never the domain itself


@pytest.mark.parametrize("include", [False, True])
def test_pack_has_image_hash_and_image_only_if_included(web, tmp_path, include):
    c = company(web["new_client"](), f"pack{int(include)}@shop.example")
    web["state"]["provider"] = Reader(EXTRACTION)
    run_id = upload(c, include=include).json()["run"]["id"]
    z = c.get(f"/api/runs/{run_id}/evidence.zip")
    assert z.status_code == 200
    (tmp_path / "p.zip").write_bytes(z.content)
    assert verify_zip(tmp_path / "p.zip")["ok"]
    digest = brand_check.sha256(IMAGE)
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        names = zf.namelist()
        art = [json.loads(json.loads(zf.read(n))["response"]["body"]) for n in names if n.startswith("artifacts/")]
        info = json.loads(zf.read("image/image_info.json"))
        everything = b"".join(zf.read(n) for n in names if not n.endswith(".png"))
        image_files = [n for n in names if n.endswith(".png")]
        if include:
            assert zf.read(image_files[0]) == IMAGE and brand_check.sha256(zf.read(image_files[0])) == digest
        custody = [json.loads(line)["event"] for line in zf.read("custody_log.jsonl").decode().splitlines()]
        manifest = json.loads(zf.read("manifest.json"))
    assert info == {"image_sha256": digest, "image_bytes": len(IMAGE), "media_type": "image/png",
                    "image_file_included": include}
    assert "image/image_info.json" in {m["path"] for m in manifest["files"]}  # hashed and sealed like the rest
    assert art[0]["image_sha256_prefix"] == digest[:16] and art[0]["image_bytes"] == len(IMAGE)
    assert art[0]["extraction"]["payment_requests"][1] == "Your login: password=Sup...23"
    assert b"SuperSecretValue123" not in everything
    assert image_files == ([f"image/{digest[:16]}.png"] if include else [])  # pixels only when the user ticked it
    assert custody.count("attachment_included") == (2 if include else 1)


def test_takedown_note_draft_then_ready_only_via_gate(web):
    c = company(web["new_client"](), "note@shop.example")
    post(c, "/api/official", {"domains": ["shopexample.com"], "numbers": []})
    web["state"]["provider"] = Reader(EXTRACTION)
    run_id = upload(c).json()["run"]["id"]
    assert post(c, f"/api/runs/{run_id}/notes", {"template": "email", "domain": "weather.example"}).status_code == 400
    assert post(c, f"/api/runs/{run_id}/notes", {"template": "registrar",
                                                 "domain": "shopexample.com"}).status_code == 409  # yours
    r = post(c, f"/api/runs/{run_id}/notes", {"template": "registrar", "domain": "shop-example-login.com"})
    note = r.json()["result"]["notes"][0]
    assert note["status"] == "DRAFT" and "[your business name]" in note["text"]
    assert "The domain shop-example-login.com resembles our domain shopexample.com" in note["text"]
    assert brand_check.sha256(IMAGE) in note["text"]
    path = f"/api/runs/{run_id}/notes/{note['id']}/approve"
    assert post(c, path, {"approver": "  "}).status_code == 400
    r = post(c, path, {"approver": "demo-user"})
    note = r.json()["result"]["notes"][0]
    assert note["status"] == "READY" and note["approved_by"] == "demo-user"
    assert post(c, path, {"approver": "demo-user"}).status_code == 409
    events = [e["event"] for e in c.get("/api/audit").json()["entries"]]
    assert events[:2] == ["note_approved", "approve_refused"]


def test_cross_user_404(web):
    a, b = company(web["new_client"](), "a@shop.example"), company(web["new_client"](), "b@shop.example")
    web["state"]["provider"] = Reader(EXTRACTION)
    run_id = upload(a).json()["run"]["id"]
    note_id = post(a, f"/api/runs/{run_id}/notes", {"template": "platform", "domain": "weather.example"}
                   ).json()["result"]["notes"][0]["id"]
    for path in ("", "/report.md", "/evidence.zip"):
        assert b.get(f"/api/runs/{run_id}{path}").status_code == 404
    assert post(b, f"/api/runs/{run_id}/dns-check", {"domain": "weather.example", "confirm": True}).status_code == 404
    assert post(b, f"/api/runs/{run_id}/notes", {"template": "platform", "domain": "weather.example"}).status_code == 404
    assert post(b, f"/api/runs/{run_id}/notes/{note_id}/approve", {"approver": "mallory"}).status_code == 404


def test_delete_my_data_clears_official_lists_and_images(web):
    c = company(web["new_client"](), "del@shop.example")
    post(c, "/api/official", {"domains": ["shopexample.com"], "numbers": ["+15550100100"]})
    web["state"]["provider"] = Reader(EXTRACTION)
    run_id = upload(c, include=True).json()["run"]["id"]
    r = post(c, "/api/data/delete", {"confirm": "DELETE"})
    assert r.json()["deleted"]["official_entries"] == 2 and r.json()["deleted"]["runs"] == 1
    assert c.get("/api/official").json()["counts"] == {"domains": 0, "numbers": 0}
    assert c.get(f"/api/runs/{run_id}").status_code == 404
