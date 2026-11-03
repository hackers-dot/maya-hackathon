# MAYA

MAYA is an AI security agent for sites and apps you own. You connect a target, prove you own it, and MAYA runs safe, non-destructive checks. It produces an evidence-backed report: which vulnerability, on which page, at what severity, with proof. It explains each finding in plain language and proposes a patch that a human must approve. After the patch is applied, MAYA re-runs the same check to prove the issue is fixed.

All code in this repository was written during the Meta Global AI Developer Hackathon (26 Oct to 4 Nov 2026). A private prototype existed before the event, but none of its code was reused here.

Built with AI coding assistance; design, decisions and testing by the author.

## Where to look

- This README: [Status](#status), [Live runs](#live-runs-during-development-run-once-not-a-general-accuracy-claim), [Evaluation](#evaluation), [What data leaves your computer](#what-data-leaves-your-computer-and-when), [Limits](#limits) and [Demo](#demo).
- [docs/design.md](docs/design.md): architecture and the safety rules behind each part.
- [docs/eval_results.md](docs/eval_results.md): the offline and live eval numbers.
- [docs/BUILD_LOG.md](docs/BUILD_LOG.md): the dated build log.
- [docs/PET.md](docs/PET.md): the guide pet in detail; [docs/pet_lines_review.md](docs/pet_lines_review.md): every pet line, reviewed.
- Code: `src/` (scanner, agent, verifier, web app), `demo_app/` (the deliberately vulnerable practice shop), `tests/`.

## Requirements

- **Python 3.10 or newer** (tested on 3.10.0, Windows).
- **Pinned dependencies** in `requirements.txt`: `httpx==0.28.1`, `pydantic==2.14.1`, `pytest==9.1.1`. Everything else is the Python standard library.
- **Optional tools:**
  - `openssl` on the PATH: used only by the 5 TLS tests (3 expiry cases, protocol, evidence), to make throwaway test certificates. Without it those tests are skipped, with the reason shown.
  - Google Chrome: used only by `scripts/ui_screens.py` (screenshots).
- **Model key (optional):** with no model key, scans run in Deterministic-only mode. With a key set (`BASETEN_API_KEY`), the agent uses the model. The key is read from the environment at call time and is never written to a file.

### Run it (Windows)

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pytest -v
.venv\Scripts\python scripts\run_web.py
```

Then open http://127.0.0.1:8090 (loopback only). Options: `--port 8095` and `--data-dir <folder>` (default `data/`, git-ignored).

Demo flow:

```
.venv\Scripts\python scripts\demo_fix_flow.py
.venv\Scripts\python scripts\demo_prepare.py --reset
```

`demo_fix_flow.py` runs scan, propose, approve, apply and re-test against the loopback demo app in the terminal, with no model. `demo_prepare.py --reset` prints the click order for the same flow in the browser: reset, scan, approve all, apply, re-test, download pack, chat question.

## Demo

Video: (link added at submission)

To reproduce the demo in the browser (no model key needed; with `BASETEN_API_KEY` set the scan is model-guided):

1. `.venv\Scripts\python scripts\run_web.py`, then open http://127.0.0.1:8090.
2. Create account → Company → Website → Yes, continue → Allow.
3. "Use the demo app" → Continue → in the sidebar, "Demo app".
4. More → Reset demo app (puts the practice shop back in its broken state).
5. Run security scan, and wait for the score card.
6. Type an approver name → Approve all → Apply approved fixes → Re-test.
7. Export → Download evidence pack.

`demo_fix_flow.py` does steps 4-6 in the terminal; `demo_prepare.py --reset` prints this click order.

## Status

"Tested live" means run for real against the real model or a real third-party service. Everything else ran on the loopback demo app, on this repository's own files, or against faked services in the tests.

| Feature | Status | Note |
|---|---|---|
| Scan agent with the Meta model (`meta-models/muse-spark-open-1026` on Baseten) | Built and tested live | 9 live runs on the demo app (see Evaluation) |
| Verifier (model claims checked against code findings) | Built and tested live | 9 live runs and 15 scripted runs |
| Website scanner (19 checks: headers, cookies, exposed files, JS libraries, version disclosure) | Built, tested on the demo app or fakes only | See "Real-site test" |
| Real-site ownership verification (meta tag, file, DNS TXT) | Built, tested on the demo app or fakes only | See "Real-site test" |
| DNS checks (SPF, DMARC) and TLS check | Built, tested on the demo app or fakes only | They do not apply to the loopback demo |
| Fix proposals, approval gate, apply, re-test | Built, tested on the demo app or fakes only | By design, apply only ever changes the demo app |
| Evidence pack, audit log, score, scan diff, exports, reminders | Built, tested on the demo app or fakes only | |
| Chat (answers from your own verified findings) | Built, tested on the demo app or fakes only | Without a key it says "Model not configured" |
| Investigation agent (Investigate in the chat) | Built and tested live | Run once, not a general accuracy claim |
| Source code ZIP and log uploads | Built, tested on the demo app or fakes only | Also run on this repository's own files |
| GitHub connector | Built and tested live | Run once, not a general accuracy claim |
| Suspicious message check (vision) | Built and tested live | Run once, not a general accuracy claim |
| Lookalike watch | Built and tested live | Run once, not a general accuracy claim |
| Model comparison script (`scripts/compare_models.py`) | Built, tested on the demo app or fakes only | Not run live |
| Guide pet (hover guide, tour, Calm mode) | Built, tested on the demo app or fakes only | Real mouse and keyboard events in headless Chrome |
| Voice sound quality | Built, tested on the demo app or fakes only | Not tested by ear |
| Logo eye blink | Built, tested on the demo app or fakes only | Sampled every 20 ms in headless Chrome |
| Ollama (local model) fallback | Not built | |
| DNS-rebinding pinning for real targets | Not built | See Limits |

## Live runs during development (run once, not a general accuracy claim)

| What | When it ran | Recorded figures |
|---|---|---|
| Suspicious message check | One synthetic test screenshot | 1 model call, HTTP 200 in 6.65 s, 1,007 tokens (353 reasoning); 7 items read, 0 dropped; 2 findings by code (a look-alike domain, high; a payment request, medium) |
| Lookalike watch | `example.com` | 81 requests, only to cloudflare-dns.com (50, all HTTP 200) and crt.sh (31: 3 answered, 7 HTTP 502, 21 timeouts); 306.9 s; the lookalike domains themselves were never contacted |
| GitHub connector | This repository | 1 request to codeload.github.com, HTTP 200 in 1.39 s, 339,776 bytes; 122 entries, 107 scanned; 75 findings, mostly pattern text in the scanner's own source and tests (11 after the false-positive fixes of Web 10.1) |
| Investigation agent | The demo app's results | 2 model calls (2.08 s and 3.87 s), 3,493 tokens; 10 claims kept, 0 dropped |

## Evaluation

0 fake claims reached a report in 15 scripted adversarial runs and in 9 live runs (3 runs x 3 sets). The scripted runs are 3 demo-app states x 5 model behaviours (perfect, sloppy, adversarial, truncated, no answer). The live runs used the Meta model on the vulnerable demo app, with a 40,000-token cap per scan. Details are in [docs/eval_results.md](docs/eval_results.md).

All 9 live runs ended with the stop reason `forced_final`. This is the designed path, not a failure: the loop reserves its last step for the final answer. On that step, or as soon as every runnable check has been tried, it asks the model for its final claims with tools switched off. The report is built from the code's findings either way, so it never depends on the model finishing.

## Real-site test

Not done as a full test. Ownership verification, the DNS/TLS checks and the scanner were tested against the demo app and fakes.
<!-- PLACEHOLDER: one hand test on a test site owned by the author; results will be added if completed. -->

## What data leaves your computer, and when

| When | What leaves | Where it goes |
|---|---|---|
| Sign-up, onboarding, consent, results, approvals, exports, Delete my data | Nothing | Stays on this computer |
| Verify ownership of a real site | GET requests for `/` and `/.well-known/maya-verify.txt` | Your site |
| Verify by DNS TXT | Your domain name | cloudflare-dns.com |
| Scan | Read-only GET/HEAD requests | The target (your verified site, or the local demo app) |
| Scan of a verified real site | Your domain name; one TLS handshake | cloudflare-dns.com (SPF, DMARC); your site on port 443 |
| Scan with a model key | Masked check results and the model's tool calls | The model provider (Baseten) |
| Chat or Investigate | Your question and your own verified findings | The model provider (Baseten) |
| Suspicious message | The screenshot, only after you tick the box; optionally a domain name | The model provider; cloudflare-dns.com |
| GitHub connector | The repository name | codeload.github.com |
| Lookalike watch | The candidate domain names | cloudflare-dns.com and crt.sh |
| The pet speaking | The spoken text, only if the browser uses an online voice | That voice's provider |

## Limits

- The server listens only on 127.0.0.1 and uses plain HTTP, so the session cookie has no `Secure` flag.
- Sessions are kept in memory: restarting the server signs everyone out.
- DNS rebinding (a verified name that later resolves to an internal address) is not handled for real targets.
- There is one model provider. If it is down or out of credits while a key is set, a scan fails with "model call failed" (there is no automatic fallback). With no key set, scans run in Deterministic-only mode and the chat says "Model not configured".
- When the operating system asks for reduced motion (on Windows: Animation effects off), the logo does not blink and the pet does not walk or play.
- The pet's voice depends on the voices installed in the browser, and speech starts only after your first click or key press.

## Guide pet

MAYA the character lives on every signed-in page as a small pet that guides you one step at a time.
- Hover any control and she says what it is, what a click does and where the data goes (fixed text, never a model).
- After a scan she offers a short tour: the score, each finding in severity order, Fix first, then the approve step.
- She never approves, applies, re-tests or scans for you; that is enforced in code and in the tests.
- Speech uses your browser's own voices; mute and Calm mode are in Pet settings. English only for now.
- Full notes: [docs/PET.md](docs/PET.md).

## Visual design

MAYA uses one visual language across all screens: a deep black-to-emerald background, emerald and cyan accents, and translucent cards. The text on bright buttons and severity badges is always dark. Severity has one colour per level everywhere: critical red, high orange, medium amber, low blue, info gray. Every text and badge colour pair is checked for contrast (`scripts/contrast_check.py`; at least 4.5:1, or 3:1 for borders and large text). The effects are plain CSS: two corner glows, a faint dot grid kept away from the centre, and one slow wave layer that pauses when the tab is hidden and stops under reduced motion. Only the sidebar and the centered form card are blurred. The app uses system fonts and no external assets, and the light theme follows the same rules.

## How it was built

MAYA was built during the Meta Global AI Developer Hackathon (26 Oct to 4 Nov 2026), in small dated steps, each with its own tests and measured results. The full step-by-step log, including what was fixed after each live run, is in [docs/BUILD_LOG.md](docs/BUILD_LOG.md).

## License

MIT. See [LICENSE](LICENSE).
