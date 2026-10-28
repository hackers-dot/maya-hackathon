"""Web 5: evidence pack (build, verify, tamper detection, zip safety) and the download endpoints.

Scans use each user's loopback demo app in deterministic-only mode. No model calls.
"""
import io
import json
import re
import shutil
import threading
import time
import zipfile
from pathlib import Path

import httpx
import pytest

from src.evidence_pack import DEMO_MARKER, DISCLAIMER, verify_pack, verify_zip
from src.web.server import make_server

PASSWORD = "correct-horse-battery"  # test-only
FORBIDDEN = ("court-ready", "compliant", "tamper-proof")
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def web(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pack-web")
    server = make_server(port=0, data_dir=tmp, provider_factory=lambda: (None, "none"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    clients = []

    def new_client():
        c = httpx.Client(base_url=base, trust_env=False, follow_redirects=False, timeout=60)
        clients.append(c)
        return c

    yield {"new_client": new_client}
    for c in clients:
        c.close()
    server.shutdown()
    server.server_close()


def post(c, path, body=None):
    csrf = c.get("/api/session").json()["csrf"]
    return c.post(path, json=body or {}, headers={"X-CSRF-Token": csrf})


def company_user(c, email):
    assert post(c, "/api/signup", {"email": email, "password": PASSWORD}).status_code == 200
    for path, body in [("/api/profile", {"account_type": "company"}), ("/api/asset", {"asset": "website"}),
                       ("/api/connect", {"connect": True}), ("/api/consent", {"allow": True})]:
        assert post(c, path, body).status_code == 200
    return c


def demo_run(c):
    asset = post(c, "/api/assets", {"demo": True}).json()["asset"]
    run_id = post(c, f"/api/assets/{asset['id']}/scan").json()["run"]["id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        body = c.get(f"/api/runs/{run_id}").json()
        if body["run"]["status"] != "running":
            return run_id, body
        time.sleep(0.1)
    raise AssertionError("scan did not finish")


@pytest.fixture(scope="module")
def fixed_run(web):
    """One user, one demo scan taken through approve-all -> apply -> re-test FIXED."""
    c = company_user(web["new_client"](), "pack@shop.example")
    run_id, body = demo_run(c)
    assert post(c, f"/api/runs/{run_id}/approve-all", {"approver": "demo-user"}).status_code == 200
    assert post(c, f"/api/runs/{run_id}/apply").status_code == 200
    assert post(c, f"/api/runs/{run_id}/retest").json()["retest"]["status"] == "FIXED"
    r = c.get(f"/api/runs/{run_id}/evidence.zip")
    assert r.status_code == 200, r.text
    return {"client": c, "run_id": run_id, "body": body, "zip": r.content, "response": r}


@pytest.fixture
def pack(fixed_run, tmp_path):
    """A fresh extracted copy of the downloaded pack, safe to tamper with."""
    with zipfile.ZipFile(io.BytesIO(fixed_run["zip"])) as z:
        z.extractall(tmp_path / "pack")
    return tmp_path / "pack"


def custody(pack):
    return (pack / "custody_log.jsonl").read_text(encoding="utf-8").splitlines(keepends=True)


# --- roundtrip and contents ---

def test_download_roundtrip_verifies(fixed_run, pack, tmp_path):
    r = fixed_run["response"]
    assert r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"].startswith('attachment; filename="maya-')
    zpath = tmp_path / "dl.zip"
    zpath.write_bytes(fixed_run["zip"])
    assert verify_zip(zpath) == {"ok": True, "problems": []}
    assert verify_pack(pack) == {"ok": True, "problems": []}
    names = {p.relative_to(pack).as_posix() for p in pack.rglob("*") if p.is_file()}
    assert {"manifest.json", "findings.json", "custody_log.jsonl", "README.txt"} <= names
    assert any(n.startswith("artifacts/") for n in names)


def test_manifest_fields_and_findings(fixed_run, pack):
    manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    arts = [m for m in manifest["files"] if m["path"].startswith("artifacts/")]
    assert arts and all(len(m["sha256"]) == 64 and m["size"] > 0 and m["time_utc"].endswith("+00:00")
                        and m["check_ids"] and m["finding_ids"] and m["tool"] and m["demo"] is True
                        and "model" in m for m in arts)
    findings = json.loads((pack / "findings.json").read_text(encoding="utf-8"))["findings"]
    expected = {f["id"]: f["severity"] for f in fixed_run["body"]["result"]["findings"]}
    assert {f["id"]: f["severity"] for f in findings} == expected  # severity from the fixed table
    assert {f["status"] for f in findings} == {"FIXED"}
    # evidence is stored redacted: the demo app's fake key never appears in full
    assert "fake-demo-key-not-real" not in "".join(p.read_text(encoding="utf-8") for p in pack.rglob("*.json"))


def test_custody_log_has_this_runs_approve_apply_retest(fixed_run, pack):
    entries = [json.loads(line) for line in custody(pack)]
    events = [e["event"] for e in entries]
    assert events[0] == "pack_started" and events[-1] == "manifest_sealed"
    n = len(fixed_run["body"]["proposals"])
    assert events.count("audit:approved") == n
    assert {"audit:applied", "audit:health_ok", "audit:retested"} <= set(events)
    assert all(e["who"] and e["at"] for e in entries)
    assert {e["who"] for e in entries if e["event"] == "audit:approved"} == {"demo-user"}


def test_demo_marker_in_manifest_and_readme(pack):
    manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["demo"] is True and manifest["demo_marker"] == DEMO_MARKER
    assert DEMO_MARKER in (pack / "README.txt").read_text(encoding="utf-8")


def test_forbidden_words_absent_from_readme_and_code(pack):
    readme = (pack / "README.txt").read_text(encoding="utf-8")
    assert DISCLAIMER in readme  # the one required disclaimer says "not tamper-proof"; nothing else may
    code = (ROOT / "src" / "evidence_pack.py").read_text(encoding="utf-8")
    for text in (readme.replace(DISCLAIMER, ""), code.replace(DISCLAIMER.split(". ")[0], "")):
        for word in FORBIDDEN:
            assert word not in text.lower(), word
    for f in pack.rglob("*"):
        if f.is_file() and f.name != "README.txt":
            assert not any(w in f.read_text(encoding="utf-8").lower() for w in FORBIDDEN), f


# --- tamper detection ---

def test_one_changed_byte_fails_naming_the_file(pack):
    art = sorted((pack / "artifacts").iterdir())[0]
    data = bytearray(art.read_bytes())
    data[10] ^= 0x01
    art.write_bytes(bytes(data))
    result = verify_pack(pack)
    assert not result["ok"] and result["problems"] == [f"artifacts/{art.name}: sha256 does not match manifest"]


def test_changed_manifest_fails(pack):
    m = pack / "manifest.json"
    m.write_text(m.read_text(encoding="utf-8").replace('"demo": true', '"demo": false', 1), encoding="utf-8")
    assert verify_pack(pack)["problems"] == ["manifest.json: sha256 does not match the manifest_sealed entry"]


def test_extra_and_missing_files_fail(pack):
    (pack / "artifacts" / "extra.json").write_text("{}", encoding="utf-8")
    (pack / "findings.json").unlink()
    assert sorted(verify_pack(pack)["problems"]) == ["artifacts/extra.json: not listed in manifest",
                                                     "findings.json: listed in manifest but missing"]


def test_edited_custody_line_fails(pack):
    lines = custody(pack)
    e = json.loads(lines[2])
    e["who"] = "someone-else"
    lines[2] = json.dumps(e, sort_keys=True) + "\n"
    (pack / "custody_log.jsonl").write_text("".join(lines), encoding="utf-8")
    assert verify_pack(pack)["problems"] == ["custody_log.jsonl line 3 (seq 2): entry hash does not match its content"]


def test_deleted_custody_line_fails(pack):
    lines = custody(pack)
    del lines[3]
    (pack / "custody_log.jsonl").write_text("".join(lines), encoding="utf-8")
    assert verify_pack(pack)["problems"] == ["custody_log.jsonl line 4: seq 4 out of order (expected 3)"]


def test_reordered_custody_lines_fail(pack):
    lines = custody(pack)
    lines[1], lines[2] = lines[2], lines[1]
    (pack / "custody_log.jsonl").write_text("".join(lines), encoding="utf-8")
    assert verify_pack(pack)["problems"] == ["custody_log.jsonl line 2: seq 2 out of order (expected 1)"]


def test_truncated_custody_log_fails(pack):
    lines = custody(pack)
    (pack / "custody_log.jsonl").write_text("".join(lines[:-1]), encoding="utf-8")
    assert verify_pack(pack)["problems"] == ["custody_log.jsonl: last entry is not manifest_sealed"]


# --- zip safety ---

@pytest.mark.parametrize("name", ["../evil.txt", "artifacts/../../evil.txt", "/abs.txt", "C:/evil.txt",
                                  "..\\evil.txt"])
def test_unsafe_zip_member_refused_before_extracting(fixed_run, tmp_path, name):
    src = io.BytesIO(fixed_run["zip"])
    zpath = tmp_path / "work" / "bad.zip"
    zpath.parent.mkdir()
    with zipfile.ZipFile(src) as good, zipfile.ZipFile(zpath, "w") as bad:
        for item in good.namelist():
            bad.writestr(item, good.read(item))
        bad.writestr(name, "x")
    result = verify_zip(zpath)
    stored = name.replace("\\", "/")  # zipfile stores "\" as "/" when writing on Windows
    assert not result["ok"] and result["problems"] == [f"{stored}: unsafe member name, refused"]
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path / "work" / "evil.txt").exists()


def test_not_a_zip(tmp_path):
    (tmp_path / "x.zip").write_bytes(b"not a zip")
    assert verify_zip(tmp_path / "x.zip") == {"ok": False, "problems": ["not a zip file"]}


# --- download endpoints: reports and authorization ---

def test_report_downloads(fixed_run):
    c, run_id = fixed_run["client"], fixed_run["run_id"]
    md = c.get(f"/api/runs/{run_id}/report.md")
    page = c.get(f"/api/runs/{run_id}/report.html")
    assert md.status_code == page.status_code == 200
    assert md.headers["content-type"].startswith("text/markdown") and "attachment" in md.headers["content-disposition"]
    assert page.headers["content-type"].startswith("text/html") and "attachment" in page.headers["content-disposition"]
    assert "exposed_env_file" in md.text and "<html" in page.text.lower()
    assert page.headers["x-content-type-options"] == "nosniff"  # security headers on downloads too


def test_other_user_gets_404_and_signed_out_gets_401(web, fixed_run):
    run_id = fixed_run["run_id"]
    other = company_user(web["new_client"](), "intruder@shop.example")
    anon = web["new_client"]()
    for name in ("evidence.zip", "report.html", "report.md"):
        assert other.get(f"/api/runs/{run_id}/{name}").status_code == 404
        assert anon.get(f"/api/runs/{run_id}/{name}").status_code == 401
    assert other.get(f"/api/runs/{'0' * 24}/evidence.zip").status_code == 404
    assert other.get(f"/api/runs/{run_id}/other.zip").status_code == 404


def test_pack_for_unfixed_run_has_no_audit_entries_and_detected_status(web, tmp_path):
    c = company_user(web["new_client"](), "fresh@shop.example")
    run_id, _ = demo_run(c)
    r = c.get(f"/api/runs/{run_id}/evidence.zip")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        z.extractall(tmp_path / "p")
    assert verify_pack(tmp_path / "p")["ok"]
    events = [json.loads(line)["event"] for line in custody(tmp_path / "p")]
    assert not any(e.startswith("audit:") for e in events)
    statuses = {f["status"] for f in json.loads((tmp_path / "p" / "findings.json").read_text())["findings"]}
    assert statuses == {"DETECTED"}


# --- Web 5.1: demo marker from the asset; 0-finding runs ---

def test_zero_finding_demo_run_still_marked_demo(web, fixed_run, tmp_path):
    c = fixed_run["client"]  # this user's demo app has every fix on, so a new scan finds nothing
    run_id, body = demo_run(c)
    assert body["result"]["findings"] == []
    r = c.get(f"/api/runs/{run_id}/evidence.zip")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        z.extractall(tmp_path / "p")
    assert verify_pack(tmp_path / "p")["ok"]
    manifest = json.loads((tmp_path / "p" / "manifest.json").read_text(encoding="utf-8"))
    readme = (tmp_path / "p" / "README.txt").read_text(encoding="utf-8")
    assert manifest["demo"] is True and manifest["demo_marker"] == DEMO_MARKER and DEMO_MARKER in readme
    assert "No issues detected in the checks run." in readme and "secure" not in readme.lower()


def test_build_pack_demo_flag_and_no_issues_unit(tmp_path):
    from src.evidence import EvidenceStore
    from src.evidence_pack import build_pack
    store = EvidenceStore(tmp_path / "e.db")
    plain = build_pack(tmp_path / "plain", "r1", {}, store)
    demo = build_pack(tmp_path / "demo", "r2", {}, store, demo=True)
    for d, flag in ((plain, False), (demo, True)):
        assert verify_pack(d)["ok"]
        readme = (d / "README.txt").read_text(encoding="utf-8")
        assert json.loads((d / "manifest.json").read_text(encoding="utf-8"))["demo"] is flag
        assert (DEMO_MARKER in readme) is flag and "No issues detected in the checks run." in readme
