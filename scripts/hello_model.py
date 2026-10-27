"""One live call to the configured model. Run by the user: .venv\\Scripts\\python scripts\\hello_model.py"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.provider import Provider, ProviderError  # noqa: E402

try:
    result = Provider.from_config(ROOT / "config.yaml").chat(
        [{"role": "user", "content": "Say hello in one sentence."}])
except ProviderError as e:
    sys.exit(f"ERROR: {e}")

print("Answer:", result.answer)
print("Reasoning present:", "yes" if result.reasoning_present else "no")
print("Usage:", {k: result.usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")})
