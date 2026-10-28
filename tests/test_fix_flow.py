import json
import socket
import threading

import pytest

from demo_app.app import make_server
from scripts import demo_fix_flow
from src.agent.tools import RUNNABLE_CHECKS
from src.approval import ApplyRefused, ApprovalGate, AuditLog
from src.evidence import EvidenceStore
from src.fixes import PLANS, PROPOSED_NOTE, Proposal, build_proposals
from src.retest import retest
from src.scanner.http_client import HttpClient
from src.scanner.runner import scan_all


@pytest.fixture
def demo(tmp_path):
    """Vulnerable demo app on a free loopback port, driven by a control file; plus a scan of it."""
    control = tmp_path / "control.json"
    server = make_server(port=0, control_path=control)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    client = HttpClient(store=EvidenceStore(tmp_path / "ev.db"), min_interval=0)
    before = {f.id: f for f in scan_all(client, base)}
    gate = ApprovalGate(AuditLog(tmp_path / "audit.jsonl"), control)
    yield {"base": base, "control": control, "client": client, "before": before, "gate": gate,
           "proposals": build_proposals(before)}
    client.close()
    server.shutdown()
    server.server_close()


def events(gate):
    return [e["event"] for e in gate.audit._entries()]


# --- proposals ---

def test_every_runnable_check_has_a_fixed_plan():
    from src.scanner.checks_tls import TLS_FINDING_CHECKS
    assert set(PLANS) == set(RUNNABLE_CHECKS) | set(TLS_FINDING_CHECKS)  # DNS findings: no template yet
    for check, p in PLANS.items():
        assert p.kind in ("config diff", "header addition", "file block rule", "dependency update",
                          "certificate renewal"), check
        assert p.nginx and p.apache and p.python_note and p.rollback, check
        assert p.risk in ("low", "medium"), check


def test_proposals_reference_findings_and_are_not_applied(demo):
    proposals = demo["proposals"]
    assert {p.check_id for p in proposals} == {f.check for f in demo["before"].values()}
    for p in proposals:
        assert p.status == "PROPOSED" and p.note == PROPOSED_NOTE == "proposed, not applied"
        assert p.finding_ids and set(p.finding_ids) <= set(demo["before"])
        assert p.plan is PLANS[p.check_id]  # fixed template, no model text
    assert not demo["control"].exists()  # building proposals changes nothing


# --- approval gate ---

def test_apply_refused_without_approval(demo):
    gate, proposals = demo["gate"], demo["proposals"]
    with pytest.raises(ApplyRefused, match="apply needs APPROVED"):
        gate.apply(proposals, demo["base"])
    assert not demo["control"].exists() and all(p.status == "PROPOSED" for p in proposals)
    assert events(gate) == ["apply_refused"]


@pytest.mark.parametrize("target", ["https://shop.example", "http://10.0.0.5:8081", "http://127.0.0.1@evil.example",
                                    "http://2130706433"])
def test_apply_refused_for_non_loopback_targets(tmp_path, target):
    gate = ApprovalGate(AuditLog(tmp_path / "audit.jsonl"), tmp_path / "control.json", health=lambda u: True)
    p = Proposal(id="fix-missing_csp", check_id="missing_csp", finding_ids=["missing_csp:/"], target=target,
                 plan=PLANS["missing_csp"])
    gate.approve(p, "demo-user")
    with pytest.raises(ApplyRefused):
        gate.apply([p], target)
    assert p.status == "APPROVED" and not (tmp_path / "control.json").exists()
    assert events(gate) == ["approved", "apply_refused"]


@pytest.mark.parametrize("approver", ["", "   ", None])
def test_approval_needs_a_named_approver(demo, approver):
    p = demo["proposals"][0]
    with pytest.raises(ValueError, match="approver"):
        demo["gate"].approve(p, approver)
    assert p.status == "PROPOSED" and p.approved_by is None
    assert events(demo["gate"]) == ["approve_refused"]


def test_approval_records_name_and_utc_time(demo):
    p = demo["gate"].approve(demo["proposals"][0], "  demo-user ")
    assert (p.status, p.approved_by) == ("APPROVED", "demo-user") and p.approved_at.endswith("+00:00")
    with pytest.raises(ValueError):
        demo["gate"].approve(p, "someone-else")  # only PROPOSED can be approved


def test_rollback_restores_previous_state(demo):
    gate, control = demo["gate"], demo["control"]
    original = json.dumps({"fixes": ["missing_hsts"]})
    control.write_text(original, encoding="utf-8")
    p = next(p for p in demo["proposals"] if p.check_id == "missing_csp")
    gate.approve(p, "demo-user")
    assert gate.apply([p], demo["base"]) is True
    assert json.loads(control.read_text())["fixes"] == ["missing_csp", "missing_hsts"]
    gate.rollback()
    assert control.read_text(encoding="utf-8") == original


def test_rollback_removes_control_file_that_did_not_exist(demo):
    gate = demo["gate"]
    p = demo["proposals"][0]
    gate.approve(p, "demo-user")
    gate.apply([p], demo["base"])
    assert demo["control"].exists()
    gate.rollback()
    assert not demo["control"].exists()


def test_health_check_failure_rolls_back(demo, tmp_path):
    gate = ApprovalGate(AuditLog(tmp_path / "audit2.jsonl"), demo["control"], health=lambda url: False)
    proposals = demo["proposals"]
    for p in proposals:
        gate.approve(p, "demo-user")
    assert gate.apply(proposals, demo["base"]) is False
    assert not demo["control"].exists()  # previous state (no file) restored
    assert all(p.status == "ROLLED_BACK" for p in proposals)
    assert events(gate)[-2:] == ["applied", "rolled_back"]
    assert retest(demo["client"], demo["base"], demo["before"])["status"] == "STILL_PRESENT"


# --- re-test ---

def test_retest_fixed_after_applying_everything(demo):
    gate = demo["gate"]
    for p in demo["proposals"]:
        gate.approve(p, "demo-user")
    assert gate.apply(demo["proposals"], demo["base"])
    assert all(p.status == "APPLIED" for p in demo["proposals"])
    r = retest(demo["client"], demo["base"], demo["before"])
    assert r["status"] == "FIXED" and r["resolved"] == sorted(demo["before"]) and r["still_present"] == []
    assert set(r["per_check"].values()) == {"FIXED"}


def test_retest_still_present_when_one_issue_remains(demo):
    gate = demo["gate"]
    chosen = [p for p in demo["proposals"] if p.check_id != "exposed_env_file"]
    for p in chosen:
        gate.approve(p, "demo-user")
    gate.apply(chosen, demo["base"])
    r = retest(demo["client"], demo["base"], demo["before"])
    assert r["status"] == "STILL_PRESENT"
    assert r["still_present"] == ["exposed_env_file:/.env"]
    assert r["per_check"]["exposed_env_file"] == "STILL_PRESENT" and r["per_check"]["missing_csp"] == "FIXED"


def test_retest_without_any_change_is_still_present(demo):
    r = retest(demo["client"], demo["base"], demo["before"])
    assert r["status"] == "STILL_PRESENT" and r["resolved"] == [] and r["new"] == []


# --- audit log ---

def test_audit_chain_detects_tampering(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    for i in range(4):
        log.append("approved", proposal=f"fix-{i}", approver="demo-user")
    assert log.verify() == (True, None)
    lines = log.path.read_text(encoding="utf-8").splitlines()

    edited = lines[:]
    edited[2] = edited[2].replace("demo-user", "someone-else")
    log.path.write_text("\n".join(edited) + "\n", encoding="utf-8")
    assert log.verify() == (False, 2)

    log.path.write_text("\n".join(lines[:1] + lines[2:]) + "\n", encoding="utf-8")  # entry deleted
    assert log.verify()[0] is False

    log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert log.verify() == (True, None)


# --- demo app control file + end-to-end script ---

def test_demo_app_applies_fixes_independently(demo):
    demo["control"].write_text(json.dumps({"fixes": ["exposed_env_file"]}), encoding="utf-8")
    c = demo["client"]
    assert c.get(demo["base"] + "/.env").status == 404
    assert c.get(demo["base"] + "/db.sql").status == 200  # other weaknesses untouched


def test_demo_fix_flow_script(tmp_path, monkeypatch, capsys):
    real = socket.create_connection

    def loopback_only(address, *a, **k):
        assert address[0] == "127.0.0.1", address
        return real(address, *a, **k)

    monkeypatch.setattr(socket, "create_connection", loopback_only)
    assert demo_fix_flow.main([], data_dir=tmp_path) == 0
    out = capsys.readouterr().out
    assert "12 fix proposals (fixed templates, proposed, not applied)" in out
    assert "Approved by 'demo-user': 12/12" in out
    assert "APPLIED, health check GET / = 200" in out
    assert "Re-test of the same checks on the same paths: FIXED" in out
    assert "still present (0)" in out and "hash chain intact" in out
