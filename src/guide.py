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

# Each state: plain line (levels new/some) and technical line, each (English, Hinglish, spoken Hindi); a "why"
# pair (English, Hinglish) or None (for findings the why comes from the report template of the top finding);
# targets; mood. MAYA speaks as herself, warm and in the first person (female persona).
# HINGLISH and DEVANAGARI strings below NEED NATIVE-SPEAKER REVIEW before they are relied on. The Devanagari
# ("spoken") versions are only for a hi-IN voice; the screen always shows the Roman text.
T = {
    "no_account_type": {
        "plain": ("Hi, I'm MAYA! First, tell me who you are: a company with a website, or someone who wants to learn.",
                  "Hii! Main MAYA hoon. Pehle batao, aap company ho jiski website hai, ya bas seekhna hai?",
                  "हाय! मैं माया हूँ। पहले बताओ, आप कंपनी हो जिसकी वेबसाइट है, या बस सीखना है?"),
        "tech": ("Hi, I'm MAYA. Pick an account type first; company accounts can connect a website for read-only "
                 "checks.",
                 "Hii, main MAYA. Pehle account type chuno; company account se website connect karke read-only "
                 "checks chala sakte ho.",
                 "हाय, मैं माया। पहले अकाउंट टाइप चुनो; कंपनी अकाउंट से वेबसाइट कनेक्ट करके रीड-ओनली चेक्स चला "
                 "सकते हो।"),
        "why": ("I only check sites you own, so I need to know if you have one.",
                "Main sirf aapki apni sites check karti hoon, isliye ye jaanna zaroori hai."),
        "targets": ("choose-company",), "mood": "pointing"},
    "no_asset_type": {
        "plain": ("Choose Website. That's what I can check today; the other options are coming later.",
                  "Website chuno. Abhi main wahi check kar sakti hoon, baaki options baad mein aayenge.",
                  "वेबसाइट चुनो। अभी मैं वही चेक कर सकती हूँ, बाकी ऑप्शन्स बाद में आएँगे।"),
        "tech": ("Select Website. API, mobile and cloud assets aren't available yet.",
                 "Website select karo. API, mobile aur cloud abhi available nahi hain.",
                 "वेबसाइट सिलेक्ट करो। एपीआई, मोबाइल और क्लाउड अभी अवेलेबल नहीं हैं।"),
        "why": ("The type decides which read-only checks I can run.",
                "Type se decide hota hai ki main kaunse read-only checks chala sakti hoon."),
        "targets": ("asset-website",), "mood": "pointing"},
    "no_connect": {
        "plain": ("Before anything runs, I ask if you want to connect. Saying yes just shows the next step; nothing "
                  "gets scanned yet.",
                  "Kuch bhi chalane se pehle main poochti hoon. Haan bolne se bas agla step khulega, abhi kuch scan "
                  "nahi hoga.",
                  "कुछ भी चलाने से पहले मैं पूछती हूँ। हाँ बोलने से बस अगला स्टेप खुलेगा, अभी कुछ स्कैन नहीं होगा।"),
        "tech": ("Confirm the connection to continue. No request goes to your site at this step.",
                 "Aage badhne ke liye connection confirm karo. Is step par aapki site ko koi request nahi jaati.",
                 "आगे बढ़ने के लिए कनेक्शन कन्फ़र्म करो। इस स्टेप पर आपकी साइट को कोई रिक्वेस्ट नहीं जाती।"),
        "why": ("You stay in control: every step waits for you.",
                "Control aapke haath mein hai, har step aapka wait karta hai."),
        "targets": ("connect-yes",), "mood": "pointing"},
    "no_consent": {
        "plain": ("Have a look at what I will and won't do. If it's okay with you, press Allow. I only read; I never "
                  "change your site.",
                  "Dekh lo main kya karungi aur kya nahi. Theek lage to Allow dabao. Main sirf padhti hoon, site "
                  "kabhi nahi badalti.",
                  "देख लो मैं क्या करूँगी और क्या नहीं। ठीक लगे तो अलाउ दबाओ। मैं सिर्फ़ पढ़ती हूँ, साइट कभी नहीं "
                  "बदलती।"),
        "tech": ("Review the scope list, then give consent. Checks are read-only requests inside the verified "
                 "scope.",
                 "Scope list dekho, phir consent do. Saare checks verified scope ke andar read-only requests hain.",
                 "स्कोप लिस्ट देखो, फिर कंसेंट दो। सारे चेक्स वेरिफ़ाइड स्कोप के अंदर रीड-ओनली रिक्वेस्ट्स हैं।"),
        "why": ("Your consent is recorded, so it's always clear what you agreed to.",
                "Aapka consent record hota hai, taaki hamesha clear rahe ki aapne kya allow kiya."),
        "targets": ("consent-allow",), "mood": "pointing"},
    "no_asset": {
        "plain": ("Now add your website address, or try my demo shop first. The demo is a practice site that runs "
                  "only on this computer.",
                  "Ab apni website ka address daalo, ya pehle meri demo shop try karo. Demo ek practice site hai jo "
                  "sirf is computer par chalti hai.",
                  "अब अपनी वेबसाइट का एड्रेस डालो, या पहले मेरी डेमो शॉप ट्राय करो। डेमो एक प्रैक्टिस साइट है जो "
                  "सिर्फ़ इस कंप्यूटर पर चलती है।"),
        "tech": ("Add a site address (ownership must be verified) or use the loopback demo app, which needs no "
                 "verification.",
                 "Site address add karo (ownership verify karni hogi), ya loopback demo app lo, usme verification "
                 "nahi chahiye.",
                 "साइट एड्रेस ऐड करो, ओनरशिप वेरिफ़ाई करनी होगी, या लूपबैक डेमो ऐप लो, उसमें वेरिफ़िकेशन नहीं "
                 "चाहिए।"),
        "why": ("I never check a site until I know it's yours.",
                "Jab tak mujhe pata na ho ki site aapki hai, main use check nahi karti."),
        "targets": ("asset-url", "use-demo", "add-asset"), "mood": "pointing"},
    "verification_pending": {
        "plain": ("I need proof that this site is yours. Put the small token shown here on your site, then press "
                  "Verify ownership.",
                  "Mujhe proof chahiye ki ye site aapki hai. Yahan dikha chhota token apni site par lagao, phir Verify "
                  "ownership dabao.",
                  "मुझे प्रूफ़ चाहिए कि ये साइट आपकी है। यहाँ दिखा छोटा टोकन अपनी साइट पर लगाओ, फिर वेरिफ़ाई "
                  "ओनरशिप दबाओ।"),
        "tech": ("Publish the token as a meta tag, a /.well-known/maya-verify.txt file or a DNS TXT record, then "
                 "verify.",
                 "Token ko meta tag, /.well-known/maya-verify.txt file ya DNS TXT record mein daalo, phir verify "
                 "karo.",
                 "टोकन को मेटा टैग, वेल-नोन फ़ाइल या डीएनएस टीएक्सटी रिकॉर्ड में डालो, फिर वेरिफ़ाई करो।"),
        "why": ("This way nobody can use me on a site that isn't theirs.",
                "Isse koi bhi mujhe kisi aur ki site par use nahi kar sakta."),
        "targets": ("verify-btn", "add-asset"), "mood": "pointing"},
    "verification_failed": {
        "plain": ("I couldn't find the token yet. Check that it's on the live site, then press Verify ownership "
                  "again.",
                  "Mujhe abhi token nahi mila. Dekho live site par laga hai ya nahi, phir Verify ownership dobara "
                  "dabao.",
                  "मुझे अभी टोकन नहीं मिला। देखो लाइव साइट पर लगा है या नहीं, फिर वेरिफ़ाई ओनरशिप दोबारा दबाओ।"),
        "tech": ("Verification failed: the token wasn't found at any accepted location. Fix the placement and "
                 "retry.",
                 "Verification fail ho gaya: token kisi sahi jagah nahi mila. Jagah theek karke phir try karo.",
                 "वेरिफ़िकेशन फ़ेल हो गया: टोकन किसी सही जगह नहीं मिला। जगह ठीक करके फिर ट्राय करो।"),
        "why": ("This way nobody can use me on a site that isn't theirs.",
                "Isse koi bhi mujhe kisi aur ki site par use nahi kar sakta."),
        "targets": ("verify-btn", "add-asset"), "mood": "concerned"},
    "ready_to_scan": {
        "plain": ("All set! Press Run security scan. I'll look at your site the way a visitor's browser does, "
                  "without changing anything.",
                  "Sab ready hai! Run security scan dabao. Main aapki site waise hi dekhungi jaise visitor ka browser "
                  "dekhta hai, kuch badle bina.",
                  "सब रेडी है! रन सिक्योरिटी स्कैन दबाओ। मैं आपकी साइट वैसे ही देखूँगी जैसे विज़िटर का ब्राउज़र "
                  "देखता है, कुछ बदले बिना।"),
        "tech": ("Ready to scan. I make read-only requests inside the scope and store each response as evidence.",
                 "Scan ke liye ready. Main scope ke andar read-only requests karti hoon aur har response evidence ki "
                 "tarah save hota hai.",
                 "स्कैन के लिए रेडी। मैं स्कोप के अंदर रीड-ओनली रिक्वेस्ट्स करती हूँ और हर रिस्पॉन्स एविडेंस की "
                 "तरह सेव होता है।"),
        "why": ("Every finding comes with the stored response that proves it.",
                "Har finding ke saath wo response hota hai jo use prove karta hai."),
        "targets": ("scan-empty-btn", "scan-btn"), "mood": "pointing"},
    "scan_running": {
        "plain": ("I'm scanning now. You can watch every step in the live trace.",
                  "Main abhi scan kar rahi hoon. Live trace mein har step dekh sakte ho.",
                  "मैं अभी स्कैन कर रही हूँ। लाइव ट्रेस में हर स्टेप देख सकते हो।"),
        "tech": ("Scan in progress. Each trace step shows the tool, its arguments and the token use.",
                 "Scan chal raha hai. Har trace step mein tool, uske arguments aur token use dikhta hai.",
                 "स्कैन चल रहा है। हर ट्रेस स्टेप में टूल, उसके आर्ग्युमेंट्स और टोकन यूज़ दिखता है।"),
        "why": ("Nothing is hidden: the trace is the record of what I actually did.",
                "Kuch chhupa nahi hai, trace hi record hai ki maine asal mein kya kiya."),
        "targets": ("run-panel",), "mood": "thinking"},
    "scan_failed": {
        "plain": ("The last scan stopped before it finished. The message on screen says why; press Re-scan "
                  "whenever you're ready.",
                  "Pichla scan beech mein ruk gaya. Screen par wajah likhi hai; jab ready ho tab Re-scan dabao.",
                  "पिछला स्कैन बीच में रुक गया। स्क्रीन पर वजह लिखी है; जब रेडी हो तब री-स्कैन दबाओ।"),
        "tech": ("The run ended early ({status}). Read the error, then start a new scan.",
                 "Run jaldi khatam ho gaya ({status}). Error padho, phir naya scan chalao.",
                 "रन जल्दी ख़त्म हो गया ({status})। एरर पढ़ो, फिर नया स्कैन चलाओ।"),
        "why": ("I only report what finished checks found.",
                "Main sirf poore hue checks ka result batati hoon."),
        "targets": ("scan-btn",), "mood": "concerned"},
    "scan_finished": {
        "plain": ("Scan done! The checks found {total} issue(s): {sev}. Let's start with the first card under Fix "
                  "first: {top}.",
                  "Scan ho gaya! Checks ne {total} issue(s) dhoondhe: {sev}. Fix first ke pehle card se shuru karte "
                  "hain: {top}.",
                  "स्कैन हो गया! चेक्स ने {total} इश्यू ढूँढे: {sev}। फ़िक्स फ़र्स्ट के पहले कार्ड से शुरू करते हैं: "
                  "{top}।"),
        "tech": ("Scan finished: {total} finding(s) ({sev}). Highest priority by the severity table: {top}.",
                 "Scan poora: {total} finding(s) ({sev}). Severity table ke hisaab se sabse pehle: {top}.",
                 "स्कैन पूरा: {total} फ़ाइंडिंग्स ({sev})। सिवेरिटी टेबल के हिसाब से सबसे पहले: {top}।"),
        "why": None, "targets": ("score-card", "fixfirst-panel", "findings-panel"), "mood": "idle"},
    "proposals_not_approved": {
        "plain": ("Here are my fix proposals, made from fixed templates. Read each one first: approving only records "
                  "your decision, and nothing changes until you also press Apply.",
                  "Ye mere fix proposals hain, fixed templates se bane. Pehle har ek padho: approve karne se bas aapka "
                  "decision record hota hai, Apply dabane tak kuch nahi badalta.",
                  "ये मेरे फ़िक्स प्रपोज़ल्स हैं, फ़िक्स्ड टेम्पलेट्स से बने। पहले हर एक पढ़ो: अप्रूव करने से बस आपका "
                  "फ़ैसला रिकॉर्ड होता है, अप्लाई दबाने तक कुछ नहीं बदलता।"),
        "tech": ("Each proposal is a fixed template (nginx, Apache, Python) with a risk level and a rollback. "
                 "Approval is written to the hash-chained audit log; it applies nothing.",
                 "Har proposal ek fixed template hai (nginx, Apache, Python), risk level aur rollback ke saath. "
                 "Approval hash-chained audit log mein jaata hai; isse kuch apply nahi hota.",
                 "हर प्रपोज़ल एक फ़िक्स्ड टेम्पलेट है, रिस्क लेवल और रोलबैक के साथ। अप्रूवल हैश-चेन्ड ऑडिट लॉग में "
                 "जाता है; इससे कुछ अप्लाई नहीं होता।"),
        "why": ("The decision is always yours; I never approve for you.",
                "Decision hamesha aapka hai; main aapki jagah kabhi approve nahi karti."),
        "targets": ("proposals-panel", "approver"), "mood": "pointing"},
    "approved_not_applied": {
        "plain": ("You approved {approved} fix(es). On the demo app, Apply makes the change and checks the app "
                  "still works; on your own site, copy the snippet and apply it yourself.",
                  "Aapne {approved} fix approve kiye. Demo app par Apply change karta hai aur check karta hai ki app "
                  "theek chal raha hai; apni site par snippet copy karke khud lagao.",
                  "आपने {approved} फ़िक्स अप्रूव किए। डेमो ऐप पर अप्लाई चेंज करता है और चेक करता है कि ऐप ठीक चल रहा "
                  "है; अपनी साइट पर स्निपेट कॉपी करके ख़ुद लगाओ।"),
        "tech": ("{approved} approved, not applied. Apply (demo app only) runs a health check and rolls back on "
                 "failure; on your own server, apply the snippet by hand.",
                 "{approved} approved, apply nahi hue. Apply (sirf demo app) health check chalata hai aur fail hone "
                 "par rollback karta hai; apne server par snippet khud lagao.",
                 "{approved} अप्रूव्ड, अप्लाई नहीं हुए। अप्लाई, सिर्फ़ डेमो ऐप पर, हेल्थ चेक चलाता है और फ़ेल होने पर "
                 "रोलबैक करता है; अपने सर्वर पर स्निपेट ख़ुद लगाओ।"),
        "why": ("If the health check fails, the change is rolled back automatically.",
                "Health check fail hua to change apne aap wapas ho jaata hai."),
        "targets": ("apply-btn",), "mood": "pointing"},
    "applied_not_retested": {
        "plain": ("The fix is applied, but not confirmed yet. Press Re-test and I'll run the same checks again to "
                  "see if the issue is really gone.",
                  "Fix lag gaya, par abhi confirm nahi hua. Re-test dabao, main wahi checks dobara chalaungi aur "
                  "dekhungi ki issue sach mein gaya ya nahi.",
                  "फ़िक्स लग गया, पर अभी कन्फ़र्म नहीं हुआ। री-टेस्ट दबाओ, मैं वही चेक्स दोबारा चलाऊँगी और देखूँगी "
                  "कि इश्यू सच में गया या नहीं।"),
        "tech": ("Applied but not verified. Re-test runs the same checks again; only a re-test can mark a finding "
                 "FIXED.",
                 "Applied hai par verify nahi hua. Re-test wahi checks dobara chalata hai; sirf re-test hi finding ko "
                 "FIXED mark kar sakta hai.",
                 "अप्लाइड है पर वेरिफ़ाई नहीं हुआ। री-टेस्ट वही चेक्स दोबारा चलाता है; सिर्फ़ री-टेस्ट ही फ़ाइंडिंग "
                 "को फ़िक्स्ड मार्क कर सकता है।"),
        "why": ("A fix only counts when the same check proves it.",
                "Fix tabhi maana jaata hai jab wahi check use prove kare."),
        "targets": ("retest-btn",), "mood": "pointing"},
    "retest_fixed": {
        "plain": ("Yay! The re-test no longer sees the {resolved} issue(s) you fixed. That's what the checks run "
                  "found, not a promise about anything else.",
                  "Yay! Re-test ko ab wo {resolved} issue(s) nahi dikhe jo aapne fix kiye. Ye bas chalaye gaye checks "
                  "ka result hai, baaki cheezon ka promise nahi.",
                  "ये! री-टेस्ट को अब वो {resolved} इश्यू नहीं दिखे जो आपने फ़िक्स किए। ये बस चलाए गए चेक्स का "
                  "रिज़ल्ट है, बाकी चीज़ों का वादा नहीं।"),
        "tech": ("Re-test result FIXED: {resolved} finding(s) resolved, none still present in the checks run.",
                 "Re-test result FIXED: {resolved} finding(s) resolved, chalaye gaye checks mein koi baaki nahi.",
                 "री-टेस्ट रिज़ल्ट फ़िक्स्ड: {resolved} फ़ाइंडिंग्स रिज़ॉल्व्ड, चलाए गए चेक्स में कोई बाकी नहीं।"),
        "why": ("You can download the evidence pack or a client report to show what changed.",
                "Kya badla, ye dikhane ke liye evidence pack ya client report download kar sakte ho."),
        "targets": ("retest-panel", "downloads-panel"), "mood": "happy"},
    "retest_still_present": {
        "plain": ("The re-test confirmed {resolved} fix(es), and {still} issue(s) are still there. The proposals for "
                  "those are just below.",
                  "Re-test ne {resolved} fix confirm kiye, aur {still} issue(s) abhi bhi hain. Unke proposals neeche "
                  "hi hain.",
                  "री-टेस्ट ने {resolved} फ़िक्स कन्फ़र्म किए, और {still} इश्यू अभी भी हैं। उनके प्रपोज़ल्स नीचे ही "
                  "हैं।"),
        "tech": ("Re-test: {resolved} resolved, {still} still present. Approve and apply the remaining proposals, "
                 "then re-test.",
                 "Re-test: {resolved} resolved, {still} abhi bhi present. Baaki proposals approve aur apply karke "
                 "dobara re-test karo.",
                 "री-टेस्ट: {resolved} रिज़ॉल्व्ड, {still} अभी भी प्रेज़ेंट। बाकी प्रपोज़ल्स अप्रूव और अप्लाई करके "
                 "दोबारा री-टेस्ट करो।"),
        "why": ("Each remaining issue keeps its stored evidence, so you can see exactly what was found.",
                "Har baaki issue ka evidence save hai, taaki aap dekh sako ki asal mein kya mila."),
        "targets": ("retest-panel", "proposals-panel"), "mood": "concerned"},
    "nothing_to_do": {
        "plain": ("Nothing's waiting for you right now. Ask me a security question in the chat; a scan only covers "
                  "the checks it ran, so re-scanning later is a good habit.",
                  "Abhi kuch pending nahi hai. Chat mein mujhse security ka koi bhi sawaal poochho; scan sirf chalaye "
                  "gaye checks cover karta hai, isliye baad mein re-scan karna achhi aadat hai.",
                  "अभी कुछ पेंडिंग नहीं है। चैट में मुझसे सिक्योरिटी का कोई भी सवाल पूछो; स्कैन सिर्फ़ चलाए गए चेक्स "
                  "कवर करता है, इसलिए बाद में री-स्कैन करना अच्छी आदत है।"),
        "tech": ("No pending actions. Results cover only the checks run; set a re-scan reminder or ask a question.",
                 "Koi pending action nahi. Results sirf chalaye gaye checks ke hain; re-scan reminder set karo ya "
                 "sawaal poochho.",
                 "कोई पेंडिंग एक्शन नहीं। रिज़ल्ट्स सिर्फ़ चलाए गए चेक्स के हैं; री-स्कैन रिमाइंडर सेट करो या सवाल "
                 "पूछो।"),
        "why": ("I never call a site fully fixed; I say what my checks did and did not find.",
                "Main kabhi nahi kehti ki site poori tarah theek hai; main bas batati hoon ki checks ko kya mila aur "
                "kya nahi."),
        "targets": ("reminder-panel", "prompt"), "mood": "idle"},
}
STATES = tuple(T)
# The pet's face and pose per state: a fixed sequence (ms = how long a step shows; None = it holds).
# Names match EXPRESSIONS / POSES in static/pet.js (tested).
EXPRESSIONS = ("idle", "happy", "excited", "laughing", "smiling", "wink", "playful", "sad", "crying", "angry",
               "surprised", "shocked", "confused", "thinking", "curious", "sleepy", "tired", "nervous", "proud",
               "determined", "love", "alert", "analyzing")
POSES = ("idle", "waving", "scanning", "protecting", "analyzing", "pointing", "celebrating", "walking")
def E(expression: str, pose: str = "idle", ms: int | None = None) -> dict:
    return {"expression": expression, "pose": pose, "ms": ms}


EMOTION = {
    "no_account_type": [E("happy", "waving", 1800), E("smiling", "pointing")],
    "no_asset_type": [E("smiling", "pointing")],
    "no_connect": [E("happy", "waving", 1800), E("smiling", "pointing")],
    "no_consent": [E("determined", "pointing")],
    "no_asset": [E("curious", "pointing")],
    "verification_pending": [E("curious", "pointing")],
    "verification_failed": [E("confused", "pointing")],
    "ready_to_scan": [E("determined", "pointing")],
    "scan_running": [E("analyzing", "scanning")],
    "scan_failed": [E("sad")],
    "proposals_not_approved": [E("determined", "pointing")],
    "approved_not_applied": [E("determined", "pointing")],
    "applied_not_retested": [E("determined", "pointing")],
    "retest_still_present": [E("confused", "protecting")],
}
WHY_PREFIX = {"en": "Why this matters: ", "hinglish": "Ye kyon zaroori hai: "}
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
    texts = t["tech"] if level == "technical" else t["plain"]
    line = texts[i].format(**values)
    spoken = texts[2].format(**values) if lang == "hinglish" else None  # Devanagari, for a hi-IN voice only
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
    emotion = _emotion(state, facts, counts, values)
    if state == "approved_not_applied" and not run.get("can_apply"):
        targets = ("retest-btn",)  # own site: the user applies the snippet, then re-tests
    return {"state": state, "level": level, "lang": lang, "line": line,
            "spoken_hi": spoken, "why": (WHY_PREFIX[lang] + why) if why else None, "targets": list(targets),
            "mood": mood,
            "emotion": emotion, "role": ROLE_FOR_LEVEL[level]}


def _emotion(state: str, facts: dict, counts: dict, values: dict) -> list[dict]:
    """The pet's emotion for a state, from the same stored facts as the line (deterministic)."""
    run = facts.get("run") or {}
    if state == "scan_finished":
        if counts.get("critical") or counts.get("high"):
            return [E("alert", "protecting", 1500), E("nervous", "pointing")]
        if counts.get("medium"):
            return [E("curious", "pointing")]
        return [E("smiling", "pointing")]
    if state == "retest_fixed":
        follow = E("love") if run.get("after_grade") == "A" else E("proud")
        return [E("excited", "celebrating", 2600), follow]
    if state == "retest_still_present" and values["resolved"] and run.get("score_improved"):
        return [E("happy", "celebrating", 1600), E("confused", "protecting")]
    if state == "nothing_to_do":
        # zero findings after a scan: proud of the result of the checks run; otherwise a calm smile
        return [E("proud")] if run and not sum(counts.values()) else [E("smiling")]
    return [dict(x) for x in EMOTION[state]]


def guide(facts: dict, level: str, lang: str) -> dict:
    """All steps for the user's current situation, in order."""
    return {"steps": [step(s, level, lang, facts) for s in states(facts)]}
