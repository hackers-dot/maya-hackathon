"""Evidence pack for one run: the stored (already redacted) evidence records plus a manifest and a custody log.

Layout:
  artifacts/<record id>.json  one file per evidence record, byte-for-byte the redacted summary in the store
  findings.json               code-detected findings (severity from the fixed table, status from the fix flow)
  README.txt                  what this is and what it is not
  manifest.json               per file: sha256, size, UTC time, check ids, finding ids, tool, model, demo flag
  custody_log.jsonl           hash-chained who/what/when: evidence captured, the run's approve/apply/re-test
                              audit entries, findings written, and last "manifest_sealed" with the manifest hash

verify_pack() recomputes every hash and the chain and names each file or log line that fails.
export_zip() / verify_zip(): verify_zip refuses absolute or "../" member names before extracting anything.
"""
import hashlib
import json
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from src.approval import GENESIS, _digest
from src.severity import rule

FORMAT = "maya-evidence-pack/1"
DISCLAIMER = ("Tamper-evident, not tamper-proof (unsigned). Provenance is self-reported. "
              "Designed to support review; not legally validated.")
DEMO_MARKER = ("DEMO: this evidence comes from MAYA's deliberately vulnerable demo app on loopback, "
               "not from a real site.")
CAPTURE_TOOL = "maya HttpClient (GET/HEAD, scope-checked)"
TOOL_FOR_METHOD = {"FILE": "maya code connector (ZIP read in memory, nothing executed)",
                   "LOG": "maya log parser (uploaded log, read only)",
                   "TLS": "maya TLS check (one handshake, scope-checked)",
                   "IMAGE": "maya suspicious-message check (model extraction, code verdicts)"}
PACK_TOOL = "maya evidence_pack"
NO_ISSUES = "No issues detected in the checks run."
SEALED_FILES = {"manifest.json", "custody_log.jsonl"}  # not listed in the manifest (the log seals the manifest)


class PackError(ValueError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_pack(dest, run_id: str, findings: dict, store, audit_entries=(), model: str | None = None,
               who: str = "maya", statuses: dict | None = None, created_at: str | None = None,
               demo: bool = False, extra_files: dict | None = None) -> Path:
    """Write the pack into the empty directory `dest`. findings: id -> Finding. audit_entries: this run's
    entries from the hash-chained audit log, in order. demo: the scanned asset is MAYA's loopback demo app
    (also inferred from any demo finding, so a run with 0 findings is still marked). Refuses to pack a record
    whose stored hash fails."""
    dest = Path(dest)
    (dest / "artifacts").mkdir(parents=True, exist_ok=True)
    created_at = created_at or _now()
    demo = bool(demo) or any(f.demo for f in findings.values())
    files, log = [], []

    def entry(event, at, actor, **details):
        e = {"seq": len(log), "at": at, "who": actor, "event": event, "details": details,
             "prev_hash": log[-1]["hash"] if log else GENESIS}
        e["hash"] = _digest(e)
        log.append(e)

    def add_file(rel, data: bytes, at, check_ids, finding_ids, tool, file_model):
        (dest / rel).write_bytes(data)
        files.append({"path": rel, "sha256": _sha(data), "size": len(data), "time_utc": at,
                      "check_ids": sorted(set(check_ids)), "finding_ids": sorted(set(finding_ids)),
                      "tool": tool, "model": file_model, "demo": demo})
        return files[-1]

    entry("pack_started", created_at, who, run=run_id, format=FORMAT)
    records: dict[str, list] = {}
    for f in sorted(findings.values(), key=lambda f: f.id):
        for ev in f.evidence:
            if ev.record_id:
                records.setdefault(ev.record_id, []).append(f)
    for record_id, fs in sorted(records.items()):
        if not store.verify(record_id):
            raise PackError(f"evidence record {record_id} fails its stored sha256; not packed")
        summary, _ = store._row(record_id)  # exactly the redacted text the store sealed
        captured = json.loads(summary)["captured_at"]
        tool = TOOL_FOR_METHOD.get(json.loads(summary)["request"]["method"], CAPTURE_TOOL)
        meta = add_file(f"artifacts/{record_id}.json", summary.encode("utf-8"), captured,
                        [f.check for f in fs], [f.id for f in fs], tool, None)
        entry("evidence_captured", captured, tool, file=meta["path"], sha256=meta["sha256"],
              finding_ids=meta["finding_ids"])

    for a in audit_entries:  # approve / apply / re-test, copied with their original hash for cross-checking
        d = a.get("details") or {}
        entry(f"audit:{a.get('event')}", a.get("at"), d.get("approver") or "maya",
              audit_seq=a.get("seq"), audit_hash=a.get("hash"), **{k: v for k, v in d.items() if k != "run"})

    for rel, data in sorted((extra_files or {}).items()):  # e.g. the screenshot, only if the user chose to include it
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        meta = add_file(rel, data, created_at, [], [], "maya suspicious-message check (image included by the user)", None)
        entry("attachment_included", created_at, who, file=rel, sha256=meta["sha256"], size=meta["size"])

    items = [{"id": f.id, "check": f.check, "severity": rule(f.check).severity, "location": f.location,
              "target": f.target, "status": (statuses or {}).get(f.id, "DETECTED"), "demo": f.demo,
              "evidence_files": [f"artifacts/{e.record_id}.json" for e in f.evidence if e.record_id]}
             for f in sorted(findings.values(), key=lambda f: f.id)]
    meta = add_file("findings.json", json.dumps({"run": run_id, "findings": items}, indent=2).encode("utf-8"),
                    created_at, [i["check"] for i in items], [i["id"] for i in items], PACK_TOOL, model)
    entry("findings_written", created_at, PACK_TOOL, file="findings.json", sha256=meta["sha256"],
          count=len(items))

    readme = [f"MAYA evidence pack for run {run_id}", ""]
    if demo:
        readme += [DEMO_MARKER, ""]
    if not items:
        readme += [NO_ISSUES, ""]
    readme += [DISCLAIMER, "",
               "artifacts/          stored evidence records (secrets were masked before storing)",
               "findings.json       findings detected by MAYA's code checks; severity from a fixed table",
               "manifest.json       sha256, size, time, check and finding ids, tool and model for each file",
               "custody_log.jsonl   hash-chained log of who did what and when; the last entry seals the manifest",
               "", "Check it with: python -c \"from src.evidence_pack import verify_pack; "
               "print(verify_pack('<this folder>'))\" from the MAYA repository.", ""]
    add_file("README.txt", "\n".join(readme).encode("utf-8"), created_at, [], [], PACK_TOOL, None)

    manifest = json.dumps({"format": FORMAT, "run": run_id, "created_at": created_at, "demo": demo,
                           "demo_marker": DEMO_MARKER if demo else None, "model": model, "files": files},
                          indent=2, sort_keys=True).encode("utf-8")
    (dest / "manifest.json").write_bytes(manifest)
    entry("manifest_sealed", created_at, PACK_TOOL, manifest_sha256=_sha(manifest), files=len(files))
    (dest / "custody_log.jsonl").write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in log),
                                            encoding="utf-8")
    return dest


def verify_pack(path) -> dict:
    """{"ok": bool, "problems": [...]}; each problem names the file or custody log line that fails."""
    root, problems = Path(path), []
    try:
        manifest_bytes = (root / "manifest.json").read_bytes()
        manifest = json.loads(manifest_bytes)
        listed = manifest["files"]
    except (OSError, ValueError, KeyError, TypeError):
        return {"ok": False, "problems": ["manifest.json: missing or unreadable"]}
    names = set()
    for m in listed:
        rel = m.get("path", "")
        names.add(rel)
        f = root / rel
        if not _safe_name(rel) or not f.is_file():
            problems.append(f"{rel}: listed in manifest but missing")
            continue
        data = f.read_bytes()
        if len(data) != m.get("size"):
            problems.append(f"{rel}: size {len(data)} != manifest {m.get('size')}")
        if _sha(data) != m.get("sha256"):
            problems.append(f"{rel}: sha256 does not match manifest")
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = f.relative_to(root).as_posix()
        if rel not in names and rel not in SEALED_FILES:
            problems.append(f"{rel}: not listed in manifest")

    try:
        lines = (root / "custody_log.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = None
        problems.append("custody_log.jsonl: missing")
    prev, last = GENESIS, None
    for i, line in enumerate(lines or [], 1):
        try:
            e = json.loads(line)
        except ValueError:
            problems.append(f"custody_log.jsonl line {i}: not valid JSON")
            break
        if not isinstance(e, dict) or e.get("seq") != i - 1:
            problems.append(f"custody_log.jsonl line {i}: seq {e.get('seq') if isinstance(e, dict) else '?'} "
                            f"out of order (expected {i - 1})")
            break
        if e.get("prev_hash") != prev:
            problems.append(f"custody_log.jsonl line {i} (seq {i - 1}): prev_hash does not match line {i - 1}")
            break
        if e.get("hash") != _digest(e):
            problems.append(f"custody_log.jsonl line {i} (seq {i - 1}): entry hash does not match its content")
            break
        prev, last = e["hash"], e
    else:
        if lines is not None:
            if not last or last.get("event") != "manifest_sealed":
                problems.append("custody_log.jsonl: last entry is not manifest_sealed")
            elif (last.get("details") or {}).get("manifest_sha256") != _sha(manifest_bytes):
                problems.append("manifest.json: sha256 does not match the manifest_sealed entry")
    return {"ok": not problems, "problems": problems}


def _safe_name(name: str) -> bool:
    """Relative, no "..", no drive letter, no backslash tricks."""
    if not name or name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name) or "\x00" in name:
        return False
    return ".." not in PurePosixPath(name.replace("\\", "/")).parts


def export_zip(pack_dir, zip_path) -> Path:
    root = Path(pack_dir)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(p for p in root.rglob("*") if p.is_file()):
            z.write(f, f.relative_to(root).as_posix())
    return Path(zip_path)


def verify_zip(zip_path) -> dict:
    """Refuse unsafe member names before extracting anything, then extract to a temp dir and verify_pack."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            bad = [n for n in z.namelist() if not _safe_name(n)]
            if bad:
                return {"ok": False, "problems": [f"{n}: unsafe member name, refused" for n in bad]}
            with tempfile.TemporaryDirectory(prefix="maya-pack-") as tmp:
                z.extractall(tmp)
                return verify_pack(tmp)
    except zipfile.BadZipFile:
        return {"ok": False, "problems": ["not a zip file"]}
