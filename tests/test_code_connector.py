"""Web 7: source-code ZIP connector. ZIPs are built in memory here; every secret in them is FAKE."""
import io
import json
import stat
import sys
import threading
import zipfile

import httpx
import pytest

from src import code_scan
from src.code_scan import CODE_CHECKS, UploadRefused, read_zip, scan_upload
from src.evidence import EvidenceStore
from src.evidence_pack import verify_zip
from src.report import TEXT, owasp_label
from src.severity import TABLE
from src.web.server import make_server

PASSWORD = "correct-horse-battery"  # test-only
FAKE_SECRET = "fake-demo-secret-1234567890"
FAKE_ENV_KEY = "fake-demo-key-not-real-42"


def make_zip(files: dict, infos: list | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
        for info, data in infos or []:
            z.writestr(info, data)
    return buf.getvalue()


SAMPLE = {
    "app/settings.py": f'DEBUG = True\nSECRET_KEY = "{FAKE_SECRET}"\npassword = request.form["password"]\n'
                       "h = hashlib.md5(password.encode())\n",
    "app/run.py": "import pickle, subprocess\ndata = pickle.loads(blob)\nsubprocess.run(cmd, shell=True)\n"
                  'result = eval(expr)\nkey = os.environ["API_KEY"]\n',
    ".env": f"# fake\nAPI_KEY={FAKE_ENV_KEY}\n",
    ".env.example": "API_KEY=your-key-here\n",
    "requirements.txt": "Django==3.2.0\nrequests==2.31.0\nflask==1.1.2\n",
    "package.json": '{\n  "dependencies": {\n    "lodash": "^4.17.15",\n    "express": "4.21.0"\n  }\n}\n',
    "img/logo.png": b"\x89PNG\x00\x00binary",
    # assembled at runtime so no PEM header literal sits in the repo; the body is not a key at all
    "keys/server.pem": "-----BEGIN " + "PRIVATE KEY-----\nMIIfakefakefake\n-----END " + "PRIVATE KEY-----\n",
}


# --- safe extraction ---

def info(name, attr=None):
    i = zipfile.ZipInfo("placeholder")
    i.filename = name  # set after construction, so nothing normalizes it
    if attr is not None:
        i.external_attr = attr
    return i


@pytest.mark.parametrize("name", ["../evil.py", "a/../../evil.py", "/etc/evil.py", "C:/evil.py", "a\\..\\evil.py"])
def test_unsafe_names_refused(name):
    with pytest.raises(UploadRefused, match="unsafe path"):
        read_zip(make_zip({"ok.py": "x = 1\n"}, [(info(name), "x = 2\n")]))


def test_symlink_refused():
    with pytest.raises(UploadRefused, match="symbolic link"):
        read_zip(make_zip({}, [(info("link", (stat.S_IFLNK | 0o777) << 16), "/etc/passwd")]))


def test_too_many_files_refused():
    with pytest.raises(UploadRefused, match="more than 2000 entries"):
        read_zip(make_zip({f"f{i}.txt": "x" for i in range(2001)}))


def test_zip_bomb_ratio_refused():
    with pytest.raises(UploadRefused, match="zip bomb"):
        read_zip(make_zip({"zeros.txt": "0" * (1024 * 1024)}))


def test_total_uncompressed_cap(monkeypatch):
    monkeypatch.setattr(code_scan, "MAX_TOTAL_UNCOMPRESSED", 1000)
    import os
    with pytest.raises(UploadRefused, match="50 MB"):
        read_zip(make_zip({"a.txt": os.urandom(400).hex(), "b.txt": os.urandom(400).hex()}))


def test_not_a_zip_and_too_large():
    with pytest.raises(UploadRefused, match="not a ZIP"):
        read_zip(b"PK but not really")
    with pytest.raises(UploadRefused, match="10 MB"):
        read_zip(b"0" * (10 * 1024 * 1024 + 1))


def test_binary_skipped_and_nothing_executed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    marker = tmp_path / "PWNED"
    evil = f"open({str(marker)!r}, 'w').write('ran')\n"
    files, counts = read_zip(make_zip({**SAMPLE, "maya_evil_probe.py": evil, "__init__.py": evil,
                                       "setup.py": evil}))
    scan_upload(files, "demo.zip")
    assert counts["skipped_binary"] == 1 and counts["scanned"] == len(SAMPLE) - 1 + 3
    assert not marker.exists() and "maya_evil_probe" not in sys.modules  # read as text, never run or imported
    assert not any(tmp_path.iterdir())  # nothing extracted to disk


# --- checks ---

def findings(files=None):
    found, _ = read_zip(make_zip(files or SAMPLE))
    return scan_upload(found, "demo.zip")


def test_every_check_detected_with_path_line_and_excerpt():
    by_id = {f.id: f for f in findings()}
    assert set(by_id) == {
        "code_debug_enabled:app/settings.py:1", "code_hardcoded_secret:app/settings.py:2",
        "code_weak_password_hash:app/settings.py:4", "code_unsafe_deserialization:app/run.py:2",
        "code_shell_true:app/run.py:3", "code_dangerous_eval:app/run.py:4", "code_env_file_committed:.env:2",
        "code_hardcoded_secret:.env:2", "code_outdated_dependency:requirements.txt:1",
        "code_outdated_dependency:requirements.txt:3", "code_outdated_dependency:package.json:3",
        "code_hardcoded_secret:keys/server.pem:1"}
    for f in by_id.values():
        assert f.severity == TABLE[f.check].severity and f.location == f.id.split(":", 1)[1]
        assert f.evidence[0].data.startswith(f.location + ": ")


def test_secrets_are_masked_everywhere(tmp_path):
    store = EvidenceStore(tmp_path / "e.db")
    found, _ = read_zip(make_zip(SAMPLE))
    fs = scan_upload(found, "demo.zip", store)
    secret = next(f for f in fs if f.id == "code_hardcoded_secret:app/settings.py:2")
    assert secret.evidence[0].data == 'app/settings.py:2: SECRET_KEY = "fak...90"'
    env = next(f for f in fs if f.id == "code_hardcoded_secret:.env:2")
    assert env.evidence[0].data == ".env:2: API_KEY=fak...42"
    dump = (tmp_path / "e.db").read_bytes() + json.dumps([f.model_dump(mode="json") for f in fs]).encode()
    for value in (FAKE_SECRET, FAKE_ENV_KEY, "MIIfakefakefake"):
        assert value.encode() not in dump  # never in findings, never in the evidence database


@pytest.mark.parametrize("line", ['key = os.environ["API_KEY"]', 'API_KEY = os.getenv("API_KEY")',
                                  'password = request.form["password"]', 'PASSWORD = "changeme"',
                                  "token = get_token()", 'SECRET = "${SECRET}"', "password = None"])
def test_references_and_placeholders_not_flagged(line):
    assert not [f for f in findings({"app.py": line + "\n"}) if f.check == "code_hardcoded_secret"]


def test_env_templates_not_flagged():
    assert findings({".env.example": f"API_KEY={FAKE_ENV_KEY}\n", ".env.sample": "X=1\n"}) == []


def test_dependency_table_is_a_short_example_list():
    source = open(code_scan.__file__, encoding="utf-8").read()
    assert "A SHORT EXAMPLE LIST" in source
    deps = [f for f in findings() if f.check == "code_outdated_dependency"]
    assert {f.evidence[0].data for f in deps} == {"requirements.txt:1: django 3.2.0",
                                                 "requirements.txt:3: flask 1.1.2", "package.json:3: lodash 4.17.15"}
    assert all("appears outdated" in f.technical_explanation for f in deps)


def test_wording_and_labels():
    for c in CODE_CHECKS:
        words = " ".join([TABLE[c].rationale, TABLE[c].remediation, *TEXT[c].__dict__.values()]).lower()
        assert "vulnerable" not in words and "vulnerability" not in words
        assert owasp_label(c).startswith("mapped to OWASP Top 10 A0")
    assert "Rotate" in TEXT["code_hardcoded_secret"].fix and "environment variable" in TEXT["code_hardcoded_secret"].fix


# --- web endpoint ---

@pytest.fixture
def web(tmp_path):
    server = make_server(port=0, data_dir=tmp_path, provider_factory=lambda: (None, "none"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    clients = []

    def new_client():
        c = httpx.Client(base_url=base, trust_env=False, follow_redirects=False, timeout=60)
        clients.append(c)
        return c

    yield {"new_client": new_client, "tmp": tmp_path}
    for c in clients:
        c.close()
    server.shutdown()
    server.server_close()


def post(c, path, body=None):
    return c.post(path, json=body or {}, headers={"X-CSRF-Token": c.get("/api/session").json()["csrf"]})


def company_user(c, email):
    assert post(c, "/api/signup", {"email": email, "password": PASSWORD}).status_code == 200
    for path, body in [("/api/profile", {"account_type": "company"}), ("/api/asset", {"asset": "website"}),
                       ("/api/connect", {"connect": True}), ("/api/consent", {"allow": True})]:
        assert post(c, path, body).status_code == 200
    return c


def upload(c, data: bytes, name="demo.zip", path="/api/uploads/code", ctype="application/zip", csrf=True):
    headers = {"Content-Type": ctype, "X-Upload-Name": name}
    if csrf:
        headers["X-CSRF-Token"] = c.get("/api/session").json()["csrf"]
    return c.post(path, content=data, headers=headers)


def test_upload_end_to_end_findings_report_pack(web, tmp_path):
    c = company_user(web["new_client"](), "code@shop.example")
    r = upload(c, make_zip(SAMPLE))
    assert r.status_code == 200, r.text
    asset, run = r.json()["asset"], r.json()["run"]
    assert asset["kind"] == "code" and asset["label"] == "demo.zip" and asset["scannable"] is False
    assert run["mode"] == "code" and run["status"] == "finished"
    body = c.get(f"/api/runs/{run['id']}").json()
    ids = {f["id"] for f in body["result"]["findings"]}
    assert "code_hardcoded_secret:app/settings.py:2" in ids and len(ids) == 12
    assert body["result"]["upload"]["skipped_binary"] == 1
    assert {x["check"] for x in body["result"]["checks"]} == set(CODE_CHECKS)
    assert c.get("/api/assets").json()["assets"][0]["id"] == asset["id"]
    md = c.get(f"/api/runs/{run['id']}/report.md").text
    assert "Hard-coded secret detected in source" in md and FAKE_SECRET not in md
    z = c.get(f"/api/runs/{run['id']}/evidence.zip")
    (tmp_path / "p.zip").write_bytes(z.content)
    assert z.status_code == 200 and verify_zip(tmp_path / "p.zip")["ok"]
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        everything = b"".join(zf.read(n) for n in zf.namelist())
    assert {m["tool"] for m in manifest["files"] if m["path"].startswith("artifacts/")} == \
        {"maya code connector (ZIP read in memory, nothing executed)"}
    assert FAKE_SECRET.encode() not in everything and FAKE_ENV_KEY.encode() not in everything
    assert FAKE_SECRET not in json.dumps(body)


def test_upload_refusals(web):
    c = company_user(web["new_client"](), "refuse@shop.example")
    assert upload(c, make_zip(SAMPLE), csrf=False).status_code == 403
    assert upload(c, make_zip(SAMPLE), name="code.tar").json()["error"] == "file name must end in .zip"
    assert upload(c, make_zip(SAMPLE), ctype="application/json").status_code == 415
    assert upload(c, b"not a zip").json()["error"] == "ZIP refused: not a ZIP file"
    bad = make_zip({"ok.py": "x\n"}, [(info("../evil.py"), "x\n")])
    assert upload(c, bad).json()["error"].startswith("ZIP refused: unsafe path")
    big = upload(c, b"0" * (10 * 1024 * 1024 + 1))
    assert big.status_code == 413
    assert c.get("/api/assets").json()["assets"] == []  # nothing was created by a refused upload
    individual = web["new_client"]()
    assert post(individual, "/api/signup", {"email": "ind@shop.example", "password": PASSWORD}).status_code == 200
    assert upload(individual, make_zip(SAMPLE)).status_code == 409  # needs company + consent
    assert upload(web["new_client"](), make_zip(SAMPLE)).status_code == 401


def test_uploads_are_per_user(web):
    a = company_user(web["new_client"](), "a@shop.example")
    b = company_user(web["new_client"](), "b@shop.example")
    run_id = upload(a, make_zip(SAMPLE)).json()["run"]["id"]
    for path in ("", "/trace", "/report.md", "/evidence.zip"):
        assert b.get(f"/api/runs/{run_id}{path}").status_code == 404
    assert b.get("/api/assets").json()["assets"] == []
    assert post(b, f"/api/runs/{run_id}/apply").status_code == 404
    assert post(a, f"/api/runs/{run_id}/retest").json()["error"].startswith("this result comes from an upload")
