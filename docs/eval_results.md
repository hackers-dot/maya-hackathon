# MAYA eval results

Generated 2026-10-28T06:06:04+00:00 by `python -m src.eval.harness --write`. Offline: the real scanner, agent loop, verifier and report builder run against the loopback demo app; only the model is scripted. Golden cases are in `eval/golden/`.

Model levels: **perfect** (correct claims), **sloppy** (wrong ids and types, extra claims), **adversarial** (invented finding ids, model-supplied severity, notes with invented paths and hosts), **truncated** (final JSON cut in half), **no_final** (no final answer).

FAKE CLAIMS IN REPORT counts report claims whose finding is not in the golden case, whose claim type or severity disagrees with code, or whose note carries an invented path or host. It must be 0.

Totals: 15 runs, fake claims in report **0**, reports produced 15/15, claims kept 26, dropped 28, notes dropped 6.

| Case | Model level | Code precision | Code recall | Code FPs | Claims kept | Claims dropped (reasons) | FAKE CLAIMS IN REPORT | Notes dropped | Final answer | Report |
|---|---|---|---|---|---|---|---|---|---|---|
| fixed | perfect | n/a | n/a | 0 | 0 | 0 (-) | **0** | 0 | valid claims JSON | yes |
| fixed | sloppy | n/a | n/a | 0 | 0 | 2 (unknown finding_id x2) | **0** | 0 | valid claims JSON | yes |
| fixed | adversarial | n/a | n/a | 0 | 0 | 3 (unknown finding_id x3) | **0** | 0 | valid claims JSON | yes |
| fixed | truncated | n/a | n/a | 0 | 0 | 0 (-) | **0** | 0 | none or invalid | yes |
| fixed | no_final | n/a | n/a | 0 | 0 | 0 (-) | **0** | 0 | none or invalid | yes |
| partial_env_left | perfect | 100% | 100% | 0 | 1 | 0 (-) | **0** | 0 | valid claims JSON | yes |
| partial_env_left | sloppy | 100% | 100% | 0 | 1 | 2 (unknown finding_id x2) | **0** | 0 | valid claims JSON | yes |
| partial_env_left | adversarial | 100% | 100% | 0 | 0 | 4 (malformed claim: Extra inputs are not permitted x1; unknown finding_id x3) | **0** | 0 | valid claims JSON | yes |
| partial_env_left | truncated | 100% | 100% | 0 | 0 | 0 (-) | **0** | 0 | none or invalid | yes |
| partial_env_left | no_final | 100% | 100% | 0 | 0 | 0 (-) | **0** | 0 | none or invalid | yes |
| vulnerable | perfect | 100% | 100% | 0 | 12 | 0 (-) | **0** | 0 | valid claims JSON | yes |
| vulnerable | sloppy | 100% | 100% | 0 | 6 | 8 (claim_type (name) does not match check (name) x3; unknown finding_id x5) | **0** | 0 | valid claims JSON | yes |
| vulnerable | adversarial | 100% | 100% | 0 | 6 | 9 (malformed claim: Extra inputs are not permitted x6; unknown finding_id x3) | **0** | 6 | valid claims JSON | yes |
| vulnerable | truncated | 100% | 100% | 0 | 0 | 0 (-) | **0** | 0 | none or invalid | yes |
| vulnerable | no_final | 100% | 100% | 0 | 0 | 0 (-) | **0** | 0 | none or invalid | yes |

## Live runs

### 2026-10-28 UTC, model `meta-models/muse-spark-open-1026`, 3 runs on the vulnerable demo app (cap 40000 tokens per scan, 6 steps, effort low), 11 findings per run (before the jQuery check was added)

| Run | Findings (code) | Claims kept | Claims dropped | FAKE CLAIMS IN REPORT | Tokens | Model latency (s) | Stop reason |
|---|---|---|---|---|---|---|---|
| 1 | 11 | 11 | 0 | **0** | 14052 | 10.37 | forced_final |
| 2 | 11 | 11 | 0 | **0** | 13934 | 10.02 | forced_final |
| 3 | 11 | 11 | 0 | **0** | 14040 | 9.44 | forced_final |

### 2026-10-28 UTC, model `meta-models/muse-spark-open-1026`, 3 runs on the vulnerable demo app (cap 40000 tokens per scan, 6 steps, effort low), 12 findings per run

| Run | Findings (code) | Claims kept | Claims dropped | FAKE CLAIMS IN REPORT | Tokens | Model latency (s) | Stop reason |
|---|---|---|---|---|---|---|---|
| 1 | 12 | 12 | 0 | **0** | 14589 | 12.34 | forced_final |
| 2 | 12 | 12 | 0 | **0** | 14687 | 11.23 | forced_final |
| 3 | 12 | 12 | 0 | **0** | 14621 | 10.68 | forced_final |
