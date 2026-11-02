"""Pet v3: complete registry, audience-aware English lines, accurate data destinations, real-input checks."""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import httpx
import pytest

from src.guide import LEVELS, STATES, guide, step
from src.guide_text import AUDIENCES, CATEGORY, FINDING_CONTEXT, STEP_CONTEXT, STEP_GROUP, tour_lines
from src.owner_summary import FORBIDDEN
from src.report import TEXT
from src.web.server import STATIC_DIR, make_server

ROOT = Path(__file__).resolve().parent.parent
TIME_OR_COST = re.compile(r"(?i)\d+\s*(?:s|sec|seconds?|min|minutes?|hours?|hrs?|days?|weeks?|months?|years?|%|usd|inr)\b"
                          r"|[$₹€]\s*\d")
NOT_ENGLISH = re.compile(r"[ऀ-ॿ]|\b(hai|nahi|karo|karta|karti|aapka|aapki|hoon)\b")
MEDICAL = re.compile(r"(?i)\b(diagnos\w*|treatment|medication|symptom\w*|prescri\w*|HIPAA|GDPR|DPDP|complian\w*|"
                     r"legally|law requires)\b")

from test_guide import FACTS  # noqa: E402


REASSURE = re.compile(r"(?i)(?:safe|secure|protected|hacked|guarantee[ds]?|compliant)")


def clean_claims(text):
    """For tour lines that quote a report template: facts such as "expires within a month" are allowed;
    reassurance, estimates with numbers, other languages and long lines are not."""
    assert not REASSURE.search(text), text
    assert not TIME_OR_COST.search(text), text
    assert not NOT_ENGLISH.search(text), text
    assert len(text.split()) <= 35, (len(text.split()), text)


def clean(text):
    assert not FORBIDDEN.search(text), text
    assert not TIME_OR_COST.search(text), text
    assert not NOT_ENGLISH.search(text), text
    assert len(text.split()) <= 35, (len(text.split()), text)


@pytest.mark.parametrize("audience", AUDIENCES)
@pytest.mark.parametrize("level", LEVELS)
def test_every_step_line_exists_per_audience_and_level_and_is_clean(audience, level):
    for state in STATES:
        s = step(state, level, "en", FACTS[state], audience)
        assert s["line"].strip() and s["audience"] == audience
        clean(s["line"])
        assert s == step(state, level, "en", FACTS[state], audience)  # deterministic
        if audience == "clinic":
            assert not MEDICAL.search(s["line"]), s["line"]


def test_audience_changes_the_finding_approval_and_retest_lines():
    for state, group in STEP_GROUP.items():
        for level in ("new", "technical"):
            lines = {a: step(state, level, "en", FACTS[state], a)["line"] for a in AUDIENCES}
            assert len(set(lines.values())) == len(AUDIENCES), (state, level)  # never the same for every audience
    for group, by_aud in STEP_CONTEXT.items():
        for a in AUDIENCES:
            new, tech = by_aud[a]
            assert new != tech, (group, a)
            clean(new)
            clean(tech)
    for cat, by_aud in FINDING_CONTEXT.items():
        assert len({by_aud[a][0] for a in AUDIENCES}) == len(AUDIENCES), cat
        assert len({by_aud[a][1] for a in AUDIENCES}) == len(AUDIENCES), cat
        for a in AUDIENCES:
            for text in by_aud[a]:
                clean(text)
                if a == "clinic":
                    assert not MEDICAL.search(text), text


def test_technical_lines_are_precise():
    tech = " ".join(FINDING_CONTEXT[c]["developer"][1] + " " + FINDING_CONTEXT[c]["company"][1] for c in FINDING_CONTEXT)
    for word in ("CSP", "HSTS", "SPF", "DMARC", "OWASP", "TLS", "HttpOnly", "evidence"):
        assert word in tech, word
    new = " ".join(FINDING_CONTEXT[c]["shop"][0] for c in FINDING_CONTEXT)
    assert "CSP" not in new and "OWASP" not in new and "HSTS" not in new  # no unexplained acronyms for beginners


def test_tour_lines_cover_every_check_for_every_audience_and_level():
    findings = [{"id": k, "check": k, "title": TEXT[k].title, "location": "/p", "severity": "medium"} for k in TEXT]
    assert set(CATEGORY) == set(TEXT)  # every check has a category
    for a in AUDIENCES:
        for level in ("new", "some", "technical"):
            t = tour_lines(findings, "Grade D · 47/100", a, level)
            for f in findings:
                line = t["findings"][f["id"]]
                assert f["title"] in line and "at /p." in line and line.startswith("Medium issue,"), line
                clean_claims(line.replace(f["title"], "TITLE"))  # titles are facts, e.g. "expires within 14 days"
            for key in ("score", "fixfirst", "approve"):
                clean(t[key])
            assert "never approve for you" in t["approve"]
            zero = tour_lines([], "", a, level)
            assert "no issues detected in the checks run" in zero["score"]
    # the first finding of each kind speaks to the audience; later ones use the check's own text (no repeats)
    t = tour_lines(findings, "Grade D · 47/100", "shop", "new")
    assert len(set(t["findings"].values())) == len(findings)
    # and no tour line reads the same for every audience (later findings of a kind use the audience's words too)
    web = [f for f in findings if CATEGORY[f["check"]] not in ("code", "logs", "lookalike", "message")]
    for level in ("new", "technical"):
        per = {a: tour_lines(web, "Grade D · 47/100", a, level) for a in AUDIENCES}
        for key in ("score", "fixfirst", "approve"):
            assert len({per[a][key] for a in AUDIENCES}) == len(AUDIENCES), (level, key)
        for f in web:
            assert len({per[a]["findings"][f["id"]] for a in AUDIENCES}) == len(AUDIENCES), (level, f["check"])


def test_guide_api_takes_the_audience_and_returns_tour_lines(tmp_path):
    server = make_server(port=0, data_dir=tmp_path, provider_factory=lambda: (None, "none"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}", trust_env=False, timeout=60) as c:
            def post(path, body):
                csrf = c.get("/api/session").json()["csrf"]
                return c.post(path, json=body, headers={"X-CSRF-Token": csrf})
            post("/api/signup", {"email": "aud@shop.example", "password": "audience-password"})
            for path, body in [("/api/profile", {"account_type": "company"}), ("/api/asset", {"asset": "website"}),
                               ("/api/connect", {"connect": True}), ("/api/consent", {"allow": True})]:
                post(path, body)
            assert c.get("/api/guide?audience=hospital").status_code == 400
            demo = post("/api/assets", {"demo": True}).json()["asset"]
            run_id = post(f"/api/assets/{demo['id']}/scan", {}).json()["run"]["id"]
            for _ in range(300):
                if c.get(f"/api/runs/{run_id}").json()["run"]["status"] != "running":
                    break
                __import__("time").sleep(0.1)
            body = {a: c.get(f"/api/guide?asset={demo['id']}&level=new&audience={a}").json() for a in AUDIENCES}
            for a, g in body.items():
                assert g["steps"][0]["audience"] == a and g["tour"]["findings"]
            assert len({g["steps"][0]["line"] for g in body.values()}) == len(AUDIENCES)
            findings = c.get(f"/api/runs/{run_id}").json()["result"]["findings"]
            lines = body["clinic"]["tour"]["findings"]
            for i, f in enumerate(findings):  # each tour line names the real finding, in severity order
                assert lines[f["id"]].startswith(f"{f['severity'].capitalize()} issue, {i + 1} of {len(findings)}: {f['title']} at {f['location']}.")
    finally:
        server.shutdown()
        server.server_close()


def test_page_side_english_only_clean_names_and_reactions():
    guide_js = (STATIC_DIR / "guide.js").read_text(encoding="utf-8")
    brain = (STATIC_DIR / "pet-brain.js").read_text(encoding="utf-8")
    page = (STATIC_DIR / "chat.html").read_text(encoding="utf-8")
    assert 'lang: "en",  // English only for now' in guide_js and "&lang=en&audience=" in guide_js
    assert '<div class="field" hidden><label for="pet-lang">' in page and 'id="pet-audience"' in page
    name = guide_js.split("function accessibleName(el) {", 1)[1].split("\n  }\n", 1)[0]
    assert "textContent" not in name and 'getAttribute("data-guide-name")' in name  # never glued child text
    assert "no notes" not in brain and "notes on this" not in brain
    assert "This is the finding for {name}. Open it to see the evidence and the fix." in brain
    assert "This is where you connect a website you own. First I'll ask you to prove it's yours, then I run read-only checks." in brain
    for word in ("Websiteworks", "visibleLabel"):
        assert word not in guide_js
    assert "medical advice" in page and "no claims about privacy law" in page


def _run(script):
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / script)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=900, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    if r.returncode == 2 and "not found" in r.stdout:
        pytest.skip("Chrome is not installed")
    return r


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_every_interactive_element_on_every_screen_has_a_registry_entry():
    r = _run("pet_registry_check.py")
    assert r.returncode == 0 and "REGISTRY COMPLETE" in r.stdout, r.stdout[-3000:]
