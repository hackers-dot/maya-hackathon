"""Guide pet brain: a deterministic state machine over the user's stored data. Never the model.

states(facts) -> the ordered guide states for where the user is now (the first is the main one; the rest
are follow-ups the "Next tip" button walks through). step(state, level, lang, facts) -> the line to show
or speak, the element ids to point at (the first that exists on the page), an optional "why this matters"
line and a mood for the mascot. Lines come only from the fixed templates below and from the fixed report
templates (src/report.py) for findings. The guide explains before it asks for an action, never decides or
approves for the user, never calls a site safe or secure, and gives no time or cost estimates.
Same state + level + language + facts gives the same lines.
"""
from src.report import TEXT

LEVELS = ("new", "some", "technical")
LANGS = ("en", "hinglish")
SEVERITIES = ("critical", "high", "medium", "low", "info")
ROLE_FOR_LEVEL = {"new": "owner", "some": "developer", "technical": "technical"}
MOODS = ("idle", "talking", "thinking", "happy", "concerned", "pointing")
UPLOAD_KINDS = ("code", "logs", "image", "watch")

# Each state: plain line (levels new/some) and technical line, each (English, Hinglish); a "why" pair or None
# (None for findings: the why comes from the report template of the top finding); targets; mood.
# HINGLISH strings below NEED NATIVE-SPEAKER REVIEW before they are relied on.
T = {
    "no_account_type": {
        "plain": ("Hi, I am your MAYA guide. First, tell MAYA who you are: a company with a website, or a person "
                  "who wants to learn.",
                  "Namaste, main aapka MAYA guide hoon. Pehle batayein: aap ek company hain jiski website hai, ya "
                  "aap seekhna chahte hain?"),
        "tech": ("Start by choosing an account type. Company accounts can connect a website for read-only checks.",
                 "Shuru karne ke liye account type chunein. Company account website connect karke read-only checks "
                 "chala sakta hai."),
        "why": ("MAYA only checks sites you own, so it needs to know whether you have one.",
                "MAYA sirf aapki apni sites check karta hai, isliye use pata hona chahiye ki aapki site hai ya nahi."),
        "targets": ("choose-company",), "mood": "pointing"},
    "no_asset_type": {
        "plain": ("Choose Website: that is what MAYA can check today. The other options come later.",
                  "Website chunein: abhi MAYA wahi check kar sakta hai. Baaki options baad mein aayenge."),
        "tech": ("Select Website. Other asset types (API, mobile, cloud) are not available yet.",
                 "Website select karein. Baaki asset types (API, mobile, cloud) abhi available nahi hain."),
        "why": ("The type decides which read-only checks MAYA can run.",
                "Type se tay hota hai ki MAYA kaunse read-only checks chala sakta hai."),
        "targets": ("asset-website",), "mood": "pointing"},
    "no_connect": {
        "plain": ("Before anything runs, MAYA asks if you want to connect. Saying yes only shows the next step; "
                  "nothing is scanned yet.",
                  "Kuch bhi chalne se pehle MAYA poochta hai ki aap connect karna chahte hain. Haan kehne se sirf "
                  "agla step dikhega; abhi kuch scan nahi hoga."),
        "tech": ("Confirm the connection to continue. No request is sent to your site at this step.",
                 "Aage badhne ke liye connection confirm karein. Is step par aapki site ko koi request nahi jaati."),
        "why": ("You stay in control: every step waits for you.",
                "Control aapke haath mein rehta hai: har step aapka intezaar karta hai."),
        "targets": ("connect-yes",), "mood": "pointing"},
    "no_consent": {
        "plain": ("Please read what MAYA will and will not do. If you agree, press Allow; MAYA only reads, it never "
                  "changes your site.",
                  "Padhiye MAYA kya karega aur kya nahi karega. Agar aap sahmat hain to Allow dabayein; MAYA sirf "
                  "padhta hai, site ko badalta nahi."),
        "tech": ("Review the scope list, then give consent. Checks are read-only requests inside the verified "
                 "scope.",
                 "Scope list dekhein, phir consent dein. Checks sirf verified scope ke andar read-only requests hain."),
        "why": ("Your consent is recorded, so it is always clear what you agreed to.",
                "Aapka consent record hota hai, taaki hamesha saaf rahe ki aapne kya manjoor kiya."),
        "targets": ("consent-allow",), "mood": "pointing"},
    "no_asset": {
        "plain": ("Now add your website address, or try MAYA's demo shop first. The demo is a practice site that "
                  "runs only on this computer.",
                  "Ab apni website ka address daalein, ya pehle MAYA ki demo shop try karein. Demo ek practice site "
                  "hai jo sirf is computer par chalti hai."),
        "tech": ("Add a site address (ownership must be verified) or use the loopback demo app, which needs no "
                 "verification.",
                 "Site address add karein (ownership verify karni hogi) ya loopback demo app use karein, jise "
                 "verification nahi chahiye."),
        "why": ("MAYA never checks a site until it knows the site is yours.",
                "Jab tak MAYA ko pata na ho ki site aapki hai, woh use check nahi karta."),
        "targets": ("asset-url", "use-demo", "add-asset"), "mood": "pointing"},
    "verification_pending": {
        "plain": ("MAYA needs proof that this site is yours. Put the small token shown here on your site, then "
                  "press Verify ownership.",
                  "MAYA ko saboot chahiye ki yeh site aapki hai. Yahan dikha chhota token apni site par lagayein, "
                  "phir Verify ownership dabayein."),
        "tech": ("Publish the token as a meta tag, a /.well-known/maya-verify.txt file or a DNS TXT record, then "
                 "verify.",
                 "Token ko meta tag, /.well-known/maya-verify.txt file ya DNS TXT record ke roop mein publish karein, "
                 "phir verify karein."),
        "why": ("This stops anyone from using MAYA on a site that is not theirs.",
                "Isse koi bhi MAYA ko kisi aur ki site par use nahi kar sakta."),
        "targets": ("verify-btn", "add-asset"), "mood": "pointing"},
    "verification_failed": {
        "plain": ("The last ownership check did not find the token. Check that it is on the live site, then press "
                  "Verify ownership again.",
                  "Pichhle ownership check mein token nahi mila. Dekhein ki woh live site par hai, phir Verify "
                  "ownership dobara dabayein."),
        "tech": ("Verification failed: the token was not found at any accepted location. Fix the placement and "
                 "retry.",
                 "Verification fail hua: token kisi bhi sahi jagah nahi mila. Jagah theek karke dobara try karein."),
        "why": ("This stops anyone from using MAYA on a site that is not theirs.",
                "Isse koi bhi MAYA ko kisi aur ki site par use nahi kar sakta."),
        "targets": ("verify-btn", "add-asset"), "mood": "concerned"},
    "ready_to_scan": {
        "plain": ("Everything is ready. Press Run security scan: MAYA looks at your site the way a visitor's "
                  "browser does, without changing anything.",
                  "Sab tayyar hai. Run security scan dabayein: MAYA aapki site ko waise hi dekhta hai jaise visitor "
                  "ka browser, bina kuch badle."),
        "tech": ("Ready to scan. The run makes read-only requests inside the scope and stores each response as "
                 "evidence.",
                 "Scan ke liye tayyar. Run scope ke andar read-only requests karta hai aur har response evidence ke "
                 "roop mein store hota hai."),
        "why": ("Every finding comes with the stored response that proves it.",
                "Har finding ke saath woh stored response hota hai jo use saabit karta hai."),
        "targets": ("scan-empty-btn", "scan-btn"), "mood": "pointing"},
    "scan_running": {
        "plain": ("The scan is running. You can watch each step in the live trace as it happens.",
                  "Scan chal raha hai. Live trace mein har step hote hue dekh sakte hain."),
        "tech": ("Scan in progress. Each trace step shows the tool, its arguments and the token use.",
                 "Scan chal raha hai. Har trace step mein tool, uske arguments aur token use dikhta hai."),
        "why": ("Nothing is hidden: the trace is the record of what MAYA actually did.",
                "Kuch chhupa nahi hai: trace hi record hai ki MAYA ne asal mein kya kiya."),
        "targets": ("run-panel",), "mood": "thinking"},
    "scan_failed": {
        "plain": ("The last scan stopped before it finished. The message on screen says why; press Re-scan when "
                  "you are ready.",
                  "Pichhla scan poora hone se pehle ruk gaya. Screen par wajah likhi hai; jab tayyar hon tab Re-scan "
                  "dabayein."),
        "tech": ("The run ended early ({status}). Read the error, then start a new scan.",
                 "Run jaldi khatam hua ({status}). Error padhein, phir naya scan shuru karein."),
        "why": ("MAYA reports only what finished checks found.",
                "MAYA sirf poore hue checks ka result batata hai."),
        "targets": ("scan-btn",), "mood": "concerned"},
    "scan_finished": {
        "plain": ("The scan is done. The checks found {total} issue(s): {sev}. Start with the first card under Fix "
                  "first: {top}.",
                  "Scan ho gaya. Checks ne {total} issue(s) dhoondhe: {sev}. Fix first ke pehle card se shuru karein: "
                  "{top}."),
        "tech": ("Scan finished: {total} finding(s) ({sev}). Highest priority by the severity table: {top}.",
                 "Scan poora: {total} finding(s) ({sev}). Severity table ke hisaab se sabse pehle: {top}."),
        "why": None, "targets": ("fixfirst-panel", "findings-panel"), "mood": "idle"},
    "proposals_not_approved": {
        "plain": ("Below are fix proposals written from fixed templates. Read each one first: approving only "
                  "records your decision, and nothing changes until you also press Apply.",
                  "Neeche fix proposals hain jo fixed templates se bane hain. Pehle har ek padhein: approve karne se "
                  "sirf aapka faisla record hota hai, jab tak Apply na dabayein kuch nahi badalta."),
        "tech": ("Each proposal is a fixed template (nginx, Apache, Python) with a risk level and a rollback. "
                 "Approval is written to the hash-chained audit log; it applies nothing.",
                 "Har proposal ek fixed template hai (nginx, Apache, Python), risk level aur rollback ke saath. "
                 "Approval hash-chained audit log mein likha jaata hai; isse kuch apply nahi hota."),
        "why": ("The decision is always yours; MAYA never approves for you.",
                "Faisla hamesha aapka hai; MAYA aapki jagah kabhi approve nahi karta."),
        "targets": ("proposals-panel", "approver"), "mood": "pointing"},
    "approved_not_applied": {
        "plain": ("You approved {approved} fix(es). On the demo app, Apply makes the change and checks that the app "
                  "still works; on your own site, copy the snippet and apply it yourself.",
                  "Aapne {approved} fix approve kiye. Demo app par Apply badlaav karta hai aur check karta hai ki app "
                  "chal raha hai; apni site par snippet copy karke khud lagayein."),
        "tech": ("{approved} approved, not applied. Apply (demo app only) runs a health check and rolls back on "
                 "failure; on your own server, apply the snippet by hand.",
                 "{approved} approved, apply nahi hue. Apply (sirf demo app) health check chalata hai aur fail hone "
                 "par rollback karta hai; apne server par snippet khud lagayein."),
        "why": ("If the health check fails, the change is rolled back automatically.",
                "Health check fail ho to badlaav apne aap wapas ho jaata hai."),
        "targets": ("apply-btn",), "mood": "pointing"},
    "applied_not_retested": {
        "plain": ("The fix is applied but not confirmed yet. Press Re-test: MAYA runs the same checks again to see "
                  "whether the issue is really gone.",
                  "Fix lag gaya hai, par abhi confirm nahi hua. Re-test dabayein: MAYA wahi checks dobara chalata hai "
                  "yeh dekhne ke liye ki issue sach mein gaya ya nahi."),
        "tech": ("Applied but not verified. Re-test runs the same checks again; only a re-test can mark a finding "
                 "FIXED.",
                 "Applied par verify nahi hua. Re-test wahi checks dobara chalata hai; sirf re-test hi finding ko "
                 "FIXED mark kar sakta hai."),
        "why": ("A fix only counts when the same check proves it.",
                "Fix tabhi maana jaata hai jab wahi check use saabit kare."),
        "targets": ("retest-btn",), "mood": "pointing"},
    "retest_fixed": {
        "plain": ("Good news: the re-test no longer sees the {resolved} issue(s) you fixed. That is what the checks "
                  "run found, not a promise about anything else.",
                  "Achhi khabar: re-test ko ab woh {resolved} issue(s) nahi dikhe jo aapne fix kiye. Yeh sirf chalaye "
                  "gaye checks ka result hai, baaki kisi cheez ka vaada nahi."),
        "tech": ("Re-test result FIXED: {resolved} finding(s) resolved, none still present in the checks run.",
                 "Re-test result FIXED: {resolved} finding(s) resolved, chalaye gaye checks mein koi baaki nahi."),
        "why": ("You can download the evidence pack or a client report to show what changed.",
                "Kya badla, yeh dikhane ke liye evidence pack ya client report download kar sakte hain."),
        "targets": ("retest-panel", "downloads-panel"), "mood": "happy"},
    "retest_still_present": {
        "plain": ("The re-test confirmed {resolved} fix(es), and {still} issue(s) are still there. The proposals "
                  "for the remaining ones are below.",
                  "Re-test ne {resolved} fix confirm kiye, aur {still} issue(s) abhi bhi hain. Baaki issues ke "
                  "proposals neeche hain."),
        "tech": ("Re-test: {resolved} resolved, {still} still present. Approve and apply the remaining proposals, "
                 "then re-test.",
                 "Re-test: {resolved} resolved, {still} abhi bhi present. Baaki proposals approve aur apply karke "
                 "dobara re-test karein."),
        "why": ("Each remaining issue keeps its stored evidence, so you can see exactly what was found.",
                "Har baaki issue ka stored evidence rehta hai, taaki aap dekh sakein ki asal mein kya mila."),
        "targets": ("retest-panel", "proposals-panel"), "mood": "concerned"},
    "nothing_to_do": {
        "plain": ("Nothing is waiting for you right now. You can ask me a security question below; a scan only "
                  "covers the checks it ran, so re-scanning later is a good habit.",
                  "Abhi aapke liye kuch baaki nahi hai. Neeche mujhse security ka koi bhi sawaal poochein; scan sirf "
                  "chalaye gaye checks ko cover karta hai, isliye baad mein re-scan karna achhi aadat hai."),
        "tech": ("No pending actions. Results cover only the checks run; set a re-scan reminder or ask a question.",
                 "Koi pending action nahi. Results sirf chalaye gaye checks ke hain; re-scan reminder set karein ya "
                 "sawaal poochein."),
        "why": ("MAYA never calls a site fully fixed; it says what its checks did and did not find.",
                "MAYA kabhi nahi kehta ki site poori tarah theek hai; woh batata hai ki checks ko kya mila aur kya "
                "nahi."),
        "targets": ("reminder-panel", "guide-ask"), "mood": "idle"},
}
STATES = tuple(T)
WHY_PREFIX = {"en": "Why this matters: ", "hinglish": "Yeh kyon zaroori hai: "}
# English report templates are reused for findings; the Hinglish guide says so instead of translating them
TEMPLATE_NOTE = {"en": "", "hinglish": "(English mein) "}


def states(facts: dict) -> list[str]:
    """Ordered guide states for the user's stored data (see GuideFacts in src/web/server.py for the keys)."""
    if facts.get("account_type") is None:
        return ["no_account_type"]
    if facts["account_type"] == "individual":
        return ["nothing_to_do"]
    if facts.get("asset_choice") != "website":
        return ["no_asset_type"]
    if facts.get("connect") is None:
        return ["no_connect"]
    if not facts.get("connect"):
        return ["nothing_to_do"]
    if not facts.get("consented"):
        return ["no_consent"]
    asset = facts.get("asset")
    if asset is None:
        return ["no_asset"]
    if asset["status"] == "pending":
        return ["verification_pending"]
    if asset["status"] == "failed":
        return ["verification_failed"]
    run = facts.get("run")
    if run is None:
        return ["nothing_to_do"] if asset.get("kind") in UPLOAD_KINDS else ["ready_to_scan"]
    if run["status"] == "running":
        return ["scan_running"]
    if run["status"] in ("failed", "budget_exceeded"):
        return ["scan_failed"]
    statuses = run.get("proposals", [])
    retest = run.get("retest")
    if retest and not any(s == "APPROVED" for s in statuses):
        return ["retest_fixed"] if retest["status"] == "FIXED" else ["retest_still_present", "proposals_not_approved"]
    if sum(run["counts"].values()) == 0:
        return ["nothing_to_do"]
    if "APPROVED" in statuses:
        return ["approved_not_applied"]
    if "APPLIED" in statuses:
        return ["applied_not_retested"]
    if "PROPOSED" in statuses:
        return ["scan_finished", "proposals_not_approved"]  # explain the findings before asking for approval
    return ["scan_finished"]


def _sev_text(counts: dict) -> str:
    return ", ".join(f"{counts[s]} {s}" for s in SEVERITIES if counts.get(s)) or "none"


def step(state: str, level: str, lang: str, facts: dict) -> dict:
    """The guide's line for one state. Unknown state, level or language raises ValueError."""
    if state not in T or level not in LEVELS or lang not in LANGS:
        raise ValueError("unknown guide state, level or language")
    t = T[state]
    i = 0 if lang == "en" else 1
    run = facts.get("run") or {}
    counts = run.get("counts", {})
    top = run.get("top")  # {"check", "title", "location"} of the highest-priority open finding
    values = {"total": sum(counts.values()), "sev": _sev_text(counts),
              "top": f"{top['title']} at {top['location']}" if top else "-",
              "approved": run.get("proposals", []).count("APPROVED"),
              "resolved": len((run.get("retest") or {}).get("resolved", [])),
              "still": len((run.get("retest") or {}).get("still_present", [])),
              "status": run.get("status", "")}
    line = (t["tech"] if level == "technical" else t["plain"])[i].format(**values)
    if t["why"]:
        why = t["why"][i]
    elif top and top["check"] in TEXT:
        tpl = TEXT[top["check"]]
        text = {"new": f"{tpl.plain} {tpl.analogy}", "some": tpl.plain, "technical": tpl.technical}[level]
        why = TEMPLATE_NOTE[lang] + text
    else:
        why = None
    mood = t["mood"]
    if state == "scan_finished" and (counts.get("critical") or counts.get("high")):
        mood = "concerned"
    if state == "retest_still_present" and values["resolved"] and run.get("score_improved"):
        mood = "happy"  # some fixes were confirmed and the score went up
    targets = t["targets"]
    if state == "approved_not_applied" and not run.get("can_apply"):
        targets = ("retest-btn",)  # own site: the user applies the snippet, then re-tests
    return {"state": state, "level": level, "lang": lang, "line": line,
            "why": (WHY_PREFIX[lang] + why) if why else None, "targets": list(targets), "mood": mood,
            "role": ROLE_FOR_LEVEL[level]}


def guide(facts: dict, level: str, lang: str) -> dict:
    """All steps for the user's current situation, in order."""
    return {"steps": [step(s, level, lang, facts) for s in states(facts)]}
