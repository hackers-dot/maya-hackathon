"""Token budget: per-run hard cap, usage appended to data/usage.jsonl."""
import json
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_CAP = 200_000
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "usage.jsonl"
KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")


class BudgetExceeded(RuntimeError):
    pass


class TokenBudget:
    def __init__(self, run_id: str, cap: int = DEFAULT_CAP, path: str | Path = DEFAULT_PATH):
        if cap <= 0:
            raise ValueError("cap must be positive")
        self.run_id, self.cap, self.path = run_id, cap, Path(path)
        self.run = dict.fromkeys(KEYS, 0)

    def add(self, usage: dict) -> dict:
        """Record one call's usage (OpenAI-style dict). Raises BudgetExceeded once the run total passes the cap."""
        p = int(usage.get("prompt_tokens") or 0)
        c = int(usage.get("completion_tokens") or 0)
        t = int(usage.get("total_tokens") or p + c)
        if min(p, c, t) < 0:
            raise ValueError("token counts cannot be negative")
        for k, v in zip(KEYS, (p, c, t)):
            self.run[k] += v
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:  # logged even when this call breaks the cap
            f.write(json.dumps({"run_id": self.run_id, "at": datetime.now(timezone.utc).isoformat(),
                                **dict(zip(KEYS, (p, c, t))), "run_total": self.run["total_tokens"]}) + "\n")
        if self.run["total_tokens"] > self.cap:
            raise BudgetExceeded(f"run {self.run_id!r} used {self.run['total_tokens']} tokens, cap is {self.cap}")
        return dict(self.run)

    @property
    def remaining(self) -> int:
        return max(0, self.cap - self.run["total_tokens"])

    @staticmethod
    def overall(path: str | Path = DEFAULT_PATH) -> dict:
        """Totals across every run recorded in the usage file."""
        totals = dict.fromkeys(KEYS, 0)
        path = Path(path)
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                rec = json.loads(line)
                for k in KEYS:
                    totals[k] += rec[k]
        return totals
