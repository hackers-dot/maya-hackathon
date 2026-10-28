"""Compare two model providers on the same scan: the vulnerable loopback demo app, the same caps for both.

Configure the second provider in config.yaml (flat keys, empty by default):
    second_base_url: https://.../v1
    second_model: <model id>
    second_api_key_env: <NAME of the environment variable that holds its key>

    .venv\\Scripts\\python scripts\\compare_models.py [--runs 1]

Makes REAL model calls (the user runs it). For each provider it prints one table row: findings by code,
claims kept / dropped, fake claims in report, tokens, model latency, stop reason, and tool-call format problems
(rejected / error / deferred tool calls). It refuses, before any network use, if the second provider is not
configured or either key variable is unset. Keys are read only at call time and never printed.
"""
import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.eval.harness import TOKEN_CAP, load_cases, run_case  # noqa: E402
from src.provider import Provider, ProviderError, key_configured, load_config  # noqa: E402
from src.web.runs import GOAL, MAX_STEPS  # noqa: E402

REASONING_EFFORT = "low"


def providers(cfg: dict) -> tuple[list[dict] | None, str | None]:
    """([{label, base_url, model, api_key_env}], None) or (None, polite refusal)."""
    second = {k: (cfg.get(f"second_{k}") or "").strip() for k in ("base_url", "model", "api_key_env")}
    if not all(second.values()):
        missing = ", ".join(f"second_{k}" for k, v in second.items() if not v)
        return None, (f"The second provider is not configured: set {missing} in config.yaml "
                      "(api_key_env is the NAME of an environment variable). Nothing was run.")
    if not second["base_url"].startswith("https://"):
        return None, "second_base_url must be an https:// address. Nothing was run."
    out = [{"label": "primary", "base_url": cfg["base_url"], "model": cfg["model"], "api_key_env": cfg["api_key_env"]},
           {"label": "second", **second}]
    unset = [p["api_key_env"] for p in out if not key_configured(p["api_key_env"])]
    if unset:
        return None, (f"The key variable {' and '.join(unset)} is not set (checked only whether it exists). "
                      "Set it as a user environment variable, then run again. Nothing was run.")
    return out, None


def table(rows: list[dict]) -> str:
    lines = ["| Provider | Model | Findings (code) | Claims kept | Claims dropped | FAKE CLAIMS IN REPORT | Tokens "
             "(reasoning) | Model latency (s) | Stop reason | Tool-call problems (rejected/error/deferred) |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if "error" in r:
            lines.append(f"| {r['label']} | {r['model']} | - | - | - | - | - | - | failed: {r['error']} | - |")
            continue
        tp = r["tool_problems"]
        lines.append(f"| {r['label']} | {r['model']} | {r['code']['found']} | {r['claims']['kept']} | "
                     f"{r['claims']['dropped']} | **{r['fake_claims_in_report']}** | {r['tokens']} "
                     f"({r['reasoning_tokens']}) | {r['model_seconds']} | {r['stop_reason']} | "
                     f"{tp['rejected']}/{tp['error']}/{tp['deferred']} |")
    return "\n".join(lines)


def main(argv=None, transports: dict | None = None, config_path: Path = ROOT / "config.yaml") -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--runs", type=int, default=1, help="scans per provider (default 1, max 3)")
    args = p.parse_args(argv)
    if not 1 <= args.runs <= 3:
        p.error("--runs must be between 1 and 3")
    cfg = load_config(config_path)
    plist, refusal = providers(cfg)
    if refusal:
        print(refusal)
        return 2
    case = next(c for c in load_cases() if c["name"] == "vulnerable")
    print(f"Comparing {len(plist)} providers on the vulnerable demo app | {args.runs} run(s) each | cap {TOKEN_CAP} "
          f"tokens per scan | {MAX_STEPS} steps | effort {REASONING_EFFORT}")
    rows = []
    with tempfile.TemporaryDirectory(prefix="maya-compare-") as tmp:
        for spec in plist:
            for i in range(1, args.runs + 1):
                provider = Provider(spec["base_url"], spec["model"], spec["api_key_env"],
                                    reasoning_effort=REASONING_EFFORT,
                                    transport=(transports or {}).get(spec["label"]))
                label = spec["label"] + (f" #{i}" if args.runs > 1 else "")
                try:
                    r = run_case(case, f"{spec['label']}-{i}", Path(tmp) / f"{spec['label']}-{i}", provider=provider,
                                 model=spec["model"], goal=GOAL, max_steps=MAX_STEPS)
                except ProviderError as e:  # already key-scrubbed by the provider
                    rows.append({"label": label, "model": spec["model"], "error": str(e)[:120]})
                    continue
                r.update(label=label, model=spec["model"])
                rows.append(r)
    print(table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
