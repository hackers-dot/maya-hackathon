"""Web 5: offline eval harness (scripted model levels x golden demo-app states). No model calls."""
import json

import pytest

from src.eval import harness
from src.eval.harness import LEVELS, NOT_MEASURED, load_cases, run_all, run_case, to_doc, write_outputs


@pytest.fixture(scope="module")
def summary():
    return run_all()


def rows(summary, case=None, level=None):
    return [r for r in summary["rows"] if (case is None or r["case"] == case) and (level is None or r["level"] == level)]


def test_golden_cases_cover_three_states():
    cases = {c["name"]: c for c in load_cases()}
    assert set(cases) == {"vulnerable", "fixed", "partial_env_left"}
    assert len(cases["vulnerable"]["expected"]) == 12 and cases["fixed"]["expected"] == []
    assert cases["partial_env_left"]["expected"] == ["exposed_env_file:/.env"]


def test_no_fake_claim_reaches_any_report(summary):
    assert summary["totals"]["runs"] == 3 * len(LEVELS) == 15
    assert summary["totals"]["fake_claims_in_report"] == 0
    assert summary["totals"]["reports_produced"] == 15
    assert all(r["fake_claim_details"] == [] for r in summary["rows"])


def test_code_findings_match_golden(summary):
    for r in summary["rows"]:
        assert r["code"]["false_positives"] == [] and r["code"]["missed"] == []
    for r in rows(summary, "vulnerable") + rows(summary, "partial_env_left"):
        assert r["code"]["precision"] == r["code"]["recall"] == 1.0
    assert all(r["code"]["precision"] is None for r in rows(summary, "fixed"))  # nothing found, nothing expected


def test_levels_behave_as_designed(summary):
    perfect = rows(summary, "vulnerable", "perfect")[0]
    assert perfect["claims"] == {"kept": 12, "dropped": 0, "drop_reasons": {}} and perfect["model_finished"]
    sloppy = rows(summary, "vulnerable", "sloppy")[0]["claims"]
    assert sloppy["dropped"] > 0 and "unknown finding_id" in sloppy["drop_reasons"]
    assert any(k.startswith("claim_type") for k in sloppy["drop_reasons"])
    adv = rows(summary, "vulnerable", "adversarial")[0]
    assert "malformed claim: Extra inputs are not permitted" in adv["claims"]["drop_reasons"]  # model severity
    assert adv["claims"]["drop_reasons"]["unknown finding_id"] == 3 and adv["notes_dropped"] > 0
    for level in ("truncated", "no_final"):
        r = rows(summary, "vulnerable", level)[0]
        assert not r["model_finished"] and r["claims"]["kept"] == 0 and r["report_produced"]
    assert all(r["tokens"] <= harness.TOKEN_CAP for r in summary["rows"])


def leaky_verify(claims, findings, store):
    """A broken verifier that trusts the model: keeps every claim as written, severity and note included."""
    kept = []
    for c in claims:
        f = findings.get(c.get("finding_id"))
        check = f.check if f else str(c.get("finding_id")).split(":")[0]
        kept.append({"finding_id": c.get("finding_id"), "check": check, "claim_type": c.get("claim_type"),
                     "severity": c.get("severity", f.severity if f else "critical"), "note": c.get("note"),
                     "priority": c.get("priority"), "text": "model says so", "supporting_evidence": "",
                     "note_dropped": None})
    return {"verified": kept, "insufficient": [], "order": "model", "order_note": "leaky"}


@pytest.mark.parametrize("level", ["sloppy", "adversarial"])
def test_leaky_verifier_makes_the_fake_claim_metric_nonzero(tmp_path, level):
    case = next(c for c in load_cases() if c["name"] == "vulnerable")
    r = run_case(case, level, tmp_path, verifier=leaky_verify)
    assert r["fake_claims_in_report"] > 0
    if level == "adversarial":
        details = " ".join(r["fake_claim_details"])
        assert "finding not in golden case" in details and "severity not from the fixed table" in details
        assert "note carries an invented path or host" in details


def test_doc_and_summary_outputs(summary, tmp_path):
    doc, js = tmp_path / "eval_results.md", tmp_path / "eval_results.json"
    write_outputs(summary, doc, js)
    text = doc.read_text(encoding="utf-8")
    assert "| Case | Model level |" in text and "FAKE CLAIMS IN REPORT" in text
    assert "## Live runs" in text and NOT_MEASURED in text
    assert json.loads(js.read_text(encoding="utf-8"))["totals"]["fake_claims_in_report"] == 0
    # measured live runs appended later survive a re-run of the offline eval
    doc.write_text(text.split("## Live runs")[0] + "## Live runs\n\n### 2026-10-28, model x\n| measured |\n",
                   encoding="utf-8")
    write_outputs(summary, doc, js)
    again = doc.read_text(encoding="utf-8")
    assert "### 2026-10-28, model x" in again and NOT_MEASURED not in again.split("## Live runs")[1]
    assert to_doc(summary).count("| vulnerable |") == len(LEVELS)


def test_eval_live_dry_run_never_writes_live_numbers(summary, tmp_path, capsys):
    import scripts.eval_live as live
    with pytest.raises(SystemExit) as e:
        live.main(["--dry-run", "--write-doc"])
    assert e.value.code == 2 and "refused" in capsys.readouterr().err
    doc, js = tmp_path / "eval_results.md", tmp_path / "eval_results.json"
    write_outputs(summary, doc, js)
    row = rows(summary, "vulnerable", "perfect")[0]
    live.write_doc([row], "model-x", doc, js)
    text = doc.read_text(encoding="utf-8")
    live_part = text.split("## Live runs")[1]
    assert NOT_MEASURED not in live_part and "model `model-x`" in live_part and "| 1 | 12 | 12 | 0 | **0** |" in live_part
    data = json.loads(js.read_text(encoding="utf-8"))["live"]
    assert data["status"] == "measured" and data["measured"][0]["model"] == "model-x"


def test_eval_endpoint_reads_the_generated_file_only(summary, tmp_path):
    import threading

    import httpx

    from src.web.server import make_server
    js = tmp_path / "docs" / "eval_results.json"
    server = make_server(port=0, data_dir=tmp_path / "data", eval_path=js, provider_factory=lambda: (None, "none"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}", trust_env=False) as c:
            assert c.get("/api/eval").status_code == 401
            csrf = c.get("/api/session").json()["csrf"]
            assert c.post("/api/signup", json={"email": "q@shop.example", "password": "correct-horse-battery"},
                          headers={"X-CSRF-Token": csrf}).status_code == 200
            assert c.get("/api/eval").json() == {"available": False}  # no file: nothing invented
            write_outputs(summary, tmp_path / "docs" / "eval_results.md", js)
            q = c.get("/api/eval").json()
            assert q["available"] and q["totals"] == summary["totals"] and len(q["rows"]) == 15
            assert q["live_status"] == NOT_MEASURED and q["live_latest"] is None
            js.write_text("{not json", encoding="utf-8")
            assert c.get("/api/eval").json() == {"available": False}
    finally:
        server.shutdown()
        server.server_close()
