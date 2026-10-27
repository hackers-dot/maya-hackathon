import json

import pytest

from src.budget import DEFAULT_CAP, BudgetExceeded, TokenBudget


def test_tracks_run_totals(tmp_path):
    b = TokenBudget("r1", path=tmp_path / "u.jsonl")
    b.add({"prompt_tokens": 151, "completion_tokens": 657, "total_tokens": 808,
           "completion_tokens_details": {"reasoning_tokens": 600}})
    run = b.add({"prompt_tokens": 10, "completion_tokens": 5})  # total derived when missing
    assert run == {"prompt_tokens": 161, "completion_tokens": 662, "total_tokens": 823, "reasoning_tokens": 600}
    assert b.remaining == DEFAULT_CAP - 823 and DEFAULT_CAP == 200_000


def test_cap_raises_and_is_still_logged(tmp_path):
    path = tmp_path / "u.jsonl"
    b = TokenBudget("r1", cap=1000, path=path)
    b.add({"total_tokens": 1000})  # exactly at cap is allowed
    with pytest.raises(BudgetExceeded):
        b.add({"total_tokens": 1})
    with pytest.raises(BudgetExceeded):
        b.add({"total_tokens": 1})  # stays exceeded
    lines = [json.loads(l) for l in path.read_text().splitlines()]
    assert [l["run_total"] for l in lines] == [1000, 1001, 1002]
    assert b.remaining == 0


def test_overall_across_runs(tmp_path):
    path = tmp_path / "u.jsonl"
    TokenBudget("a", path=path).add({"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3})
    TokenBudget("b", path=path).add({"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30})
    assert TokenBudget.overall(path) == {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33,
                                        "reasoning_tokens": 0}
    assert TokenBudget.overall(tmp_path / "missing.jsonl") == {"prompt_tokens": 0, "completion_tokens": 0,
                                                                "total_tokens": 0, "reasoning_tokens": 0}


def test_invalid_input(tmp_path):
    with pytest.raises(ValueError):
        TokenBudget("x", cap=0, path=tmp_path / "u.jsonl")
    with pytest.raises(ValueError):
        TokenBudget("x", path=tmp_path / "u.jsonl").add({"prompt_tokens": -5})
