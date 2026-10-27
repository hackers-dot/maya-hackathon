"""Model capability experiment: exactly 4 calls (A reasoning_effort, B tools, C json_schema, D vision).

Run by the user (live):   .venv\\Scripts\\python scripts\\experiment_model.py
Offline preview:          .venv\\Scripts\\python scripts\\experiment_model.py --dry-run

Prints only: call name, HTTP status, finish_reason, token counts, reasoning token count, latency, PASS/FAIL.
Never prints reasoning text or the key. Aborts on the first HTTP error, printing the status only.
"""
import argparse
import base64
import json
import os
import struct
import sys
import time
import zlib
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.provider import load_config, split_reasoning  # noqa: E402

MAX_TOKENS = 1500
HELLO_BASELINE_COMPLETION = 657  # Day 1 hello call, default reasoning effort
TOOL_CHECKS = ["missing_csp", "missing_hsts", "cookie_missing_secure"]
SEVERITIES = ["critical", "high", "medium", "low", "info"]
REASON_MAX = 120


def red_png(size: int = 64) -> bytes:
    """Solid red RGB PNG, built with stdlib only."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    rows = b"".join(b"\x00" + b"\xff\x00\x00" * size for _ in range(size))  # filter byte 0 + RGB pixels
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows))
            + chunk(b"IEND", b""))


def build_bodies(model: str) -> list[tuple[str, dict]]:
    def body(messages, **extra):
        return {"model": model, "messages": messages, "max_tokens": MAX_TOKENS, **extra}

    data_url = "data:image/png;base64," + base64.b64encode(red_png()).decode()
    return [
        ("A_hello_low_effort", body([{"role": "user", "content": "Say hello in one sentence."}],
                                    reasoning_effort="low")),
        ("B_tool_call", body(
            [{"role": "user", "content": "The page /login is missing the Content-Security-Policy header. "
                                         "Which check should be run? Call the run_check tool."}],
            tools=[{"type": "function", "function": {
                "name": "run_check",
                "description": "Run one MAYA security check against a page path.",
                "parameters": {
                    "type": "object",
                    "properties": {"check_id": {"type": "string", "enum": TOOL_CHECKS},
                                   "path": {"type": "string"}},
                    "required": ["check_id", "path"],
                    "additionalProperties": False,
                },
            }}],
            tool_choice="auto", reasoning_effort="low")),
        ("C_json_schema", body(
            [{"role": "user", "content": "Rate the severity of a missing Content-Security-Policy header "
                                         "on a login page. Answer in JSON."}],
            response_format={"type": "json_schema", "json_schema": {
                "name": "severity_verdict",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"severity": {"type": "string", "enum": SEVERITIES},
                                   "reason": {"type": "string", "maxLength": REASON_MAX}},
                    "required": ["severity", "reason"],
                    "additionalProperties": False,
                },
            }})),
        ("D_vision", body([{"role": "user", "content": [
            {"type": "text", "text": "What color is this image? One word."},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]}])),
    ]


# --- evaluators: pure, return (passed, short detail). Only the final answer is ever inspected. ---

def eval_a(message: dict, usage: dict) -> tuple[bool, str]:
    answer, _ = split_reasoning(message)
    c = usage.get("completion_tokens")
    delta = f"{c - HELLO_BASELINE_COMPLETION:+d}" if isinstance(c, int) else "n/a"
    return bool(answer), f"completion_tokens {c} vs baseline {HELLO_BASELINE_COMPLETION} ({delta})"


def eval_b(message: dict, finish_reason) -> tuple[bool, str]:
    calls = message.get("tool_calls") or []
    if not calls:
        answer, _ = split_reasoning(message)
        mentions = "run_check" in answer or any(c in answer for c in TOOL_CHECKS)
        return False, (f"no tool_calls; finish_reason={finish_reason}; content mentions a call: "
                       f"{'yes' if mentions else 'no'}; content[:80]={answer[:80]!r}")
    fn = calls[0].get("function") or {}
    try:
        args = json.loads(fn.get("arguments") or "")
    except (TypeError, ValueError):
        return False, "tool_calls present but arguments are not valid JSON"
    if fn.get("name") != "run_check" or not isinstance(args, dict) or args.get("check_id") not in TOOL_CHECKS:
        return False, f"tool call not valid: name={fn.get('name')!r} check_id={args.get('check_id') if isinstance(args, dict) else None!r}"
    return True, f"run_check(check_id={args['check_id']!r}, path={args.get('path')!r})"


def eval_c(message: dict) -> tuple[bool, str]:
    answer, _ = split_reasoning(message)
    try:
        data = json.loads(answer)
    except ValueError:
        return False, "content is not valid JSON"
    if not isinstance(data, dict) or set(data) != {"severity", "reason"}:
        return False, f"wrong keys: {sorted(data) if isinstance(data, dict) else type(data).__name__}"
    if data["severity"] not in SEVERITIES:
        return False, f"severity not in enum: {data['severity']!r}"
    if not isinstance(data["reason"], str) or len(data["reason"]) > REASON_MAX:
        return False, "reason missing or longer than 120 chars"
    return True, f"severity={data['severity']!r}, reason length {len(data['reason'])}"


def eval_d(message: dict) -> tuple[bool, str]:
    answer, _ = split_reasoning(message)
    return "red" in answer.lower(), f"answer[:40]={answer[:40]!r}"


def evaluate(name: str, choice: dict, usage: dict) -> tuple[bool, str]:
    message = choice.get("message") or {}
    if name.startswith("A"):
        return eval_a(message, usage)
    if name.startswith("B"):
        return eval_b(message, choice.get("finish_reason"))
    if name.startswith("C"):
        return eval_c(message)
    return eval_d(message)


def dry_run(url: str, bodies) -> int:
    print(f"DRY RUN: no network call, no key read. POST {url}")
    headers = {"Authorization": "Bearer [REDACTED]", "Content-Type": "application/json"}
    for name, body in bodies:
        print(f"\n=== {name} ===")
        print(json.dumps({"headers": headers, "body": body}, indent=2))
    return 0


def run(url: str, key_env: str, bodies, transport=None) -> int:
    key = os.environ.get(key_env)  # read at call time only, never printed
    if not key:
        print(f"ERROR: environment variable {key_env} is not set")
        return 2
    results = []
    with httpx.Client(transport=transport, timeout=300.0, trust_env=False) as client:
        for name, body in bodies:
            t0 = time.perf_counter()
            try:
                r = client.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
            except httpx.HTTPError as e:
                print(f"[{name}] request failed: {type(e).__name__}. Aborting.")
                return 1
            latency = time.perf_counter() - t0
            if r.status_code != 200:
                print(f"[{name}] HTTP {r.status_code}. Aborting.")  # status only, never the body
                return 1
            try:
                data = r.json()
                choice = data["choices"][0]
            except (ValueError, KeyError, IndexError, TypeError):
                print(f"[{name}] HTTP 200 but unexpected response shape. FAIL")
                results.append(False)
                continue
            usage = data.get("usage") or {}
            reasoning_tokens = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
            print(f"[{name}] HTTP {r.status_code} | finish_reason={choice.get('finish_reason')} | "
                  f"prompt_tokens={usage.get('prompt_tokens')} completion_tokens={usage.get('completion_tokens')}"
                  + (f" reasoning_tokens={reasoning_tokens}" if reasoning_tokens is not None else "")
                  + f" | latency={latency:.2f}s")
            passed, detail = evaluate(name, choice, usage)
            results.append(passed)
            print(f"[{name}] {'PASS' if passed else 'FAIL'}: {detail}")
    print(f"\nSummary: {sum(results)}/{len(bodies)} PASS")
    return 0


def main(argv=None, transport=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dry-run", action="store_true", help="print the 4 request bodies, no network, no key")
    args = p.parse_args(argv)
    cfg = load_config(ROOT / "config.yaml")
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    bodies = build_bodies(cfg["model"])
    return dry_run(url, bodies) if args.dry_run else run(url, cfg["api_key_env"], bodies, transport)


if __name__ == "__main__":
    sys.exit(main())
