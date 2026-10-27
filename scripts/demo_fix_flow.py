"""Fix flow on the loopback demo app, no model needed:
scan -> proposals (fixed templates) -> approve ("demo-user") -> apply (control file) -> health check -> re-test.

Run: .venv\\Scripts\\python scripts\\demo_fix_flow.py
Everything stays on 127.0.0.1. Evidence, audit log and control file go to data/ (git-ignored).
"""
import argparse
import sys
import threading
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from demo_app.app import make_server  # noqa: E402
from src.approval import ApprovalGate, AuditLog  # noqa: E402
from src.evidence import EvidenceStore  # noqa: E402
from src.fixes import build_proposals  # noqa: E402
from src.retest import retest  # noqa: E402
from src.scanner.http_client import HttpClient  # noqa: E402
from src.scanner.runner import scan_all  # noqa: E402

APPROVER = "demo-user"


def main(argv=None, data_dir: Path | None = None) -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    data_dir = Path(data_dir or ROOT / "data")
    run = uuid.uuid4().hex[:8]
    control = data_dir / f"demo_control-{run}.json"  # fresh per run: the demo starts vulnerable
    server = make_server(port=0, control_path=control)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    store = EvidenceStore(data_dir / "evidence.db")
    client = HttpClient(store=store)
    audit = AuditLog(data_dir / "audit.jsonl")
    try:
        print(f"[1] Scan (before) of the VULNERABLE demo app at {base}")
        before = {f.id: f for f in scan_all(client, base)}
        for f in sorted(before.values(), key=lambda f: f.id):
            print(f"    {f.severity:<8} {f.id}")

        proposals = build_proposals(before)
        print(f"\n[2] {len(proposals)} fix proposals (fixed templates, {proposals[0].note if proposals else '-'})")
        for p in proposals:
            print(f"    {p.id:<36} {p.plan.kind:<15} risk={p.plan.risk:<6} findings={len(p.finding_ids)} "
                  f"nginx: {p.plan.nginx.splitlines()[0]}")

        gate = ApprovalGate(audit, control)
        for p in proposals:
            gate.approve(p, APPROVER)
        print(f"\n[3] Approved by {APPROVER!r}: {sum(p.status == 'APPROVED' for p in proposals)}/{len(proposals)}")

        ok = gate.apply(proposals, base)
        print(f"[4] Apply to loopback demo app: {'APPLIED, health check GET / = 200' if ok else 'ROLLED BACK'}")
        if not ok:
            return 1

        result = retest(client, base, before)
        print(f"\n[5] Re-test of the same checks on the same paths: {result['status']}")
        print(f"    resolved      ({len(result['resolved'])}): {', '.join(result['resolved']) or '-'}")
        print(f"    still present ({len(result['still_present'])}): {', '.join(result['still_present']) or '-'}")
        print(f"    new           ({len(result['new'])}): {', '.join(result['new']) or '-'}")
        print("\n    before -> after per check")
        for check, status in result["per_check"].items():
            print(f"    {check:<32} present -> {'absent' if status == 'FIXED' else 'present'} ({status})")

        intact, bad = audit.verify()
        print(f"\n[6] Audit log {audit.path.name}: hash chain {'intact' if intact else f'BROKEN at entry {bad}'}")
        return 0 if result["status"] == "FIXED" and intact else 1
    finally:
        client.close()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
