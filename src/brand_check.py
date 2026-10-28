"""Suspicious message check: a screenshot that claims to come from the user's business.

The MODEL only reads text in the image and returns a strict JSON extraction. CODE decides everything else:
it normalises the extracted domains and phone numbers, compares them with the user's verified assets and
official lists, and flags lookalikes with a fixed, deterministic rule. Passive only: nothing here (or
anywhere else) ever sends a request to an extracted URL. Wording: "not in your list, needs review" and
"resembles your domain"; this module never labels anything as malicious.
"""
import hashlib
import json
import re
import unicodedata
from urllib.parse import urlsplit

from src.evidence import mask, redact_text
from src.models import Finding
from src.scanner.findings import make_finding

MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_TYPES = {b"\x89PNG\r\n\x1a\n": "image/png", b"\xff\xd8\xff": "image/jpeg"}
KEYS = ("urls", "phone_numbers", "brand_names", "payment_requests", "urgency_phrases")
ITEM_MAX = 200
ITEMS_MAX = 20
MESSAGE_CHECKS = ("msg_domain_resembles_yours", "msg_domain_not_in_list", "msg_phone_not_in_list",
                  "msg_payment_request", "msg_urgency_language")
UNVERIFIED = "read by the model from the image (unverified)"
NOT_IN_LIST = "not in your list, needs review"
RESEMBLES = "resembles your domain"
OFFICIAL = "matches your list"

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": list(KEYS),
    "properties": {k: {"type": "array", "maxItems": ITEMS_MAX, "items": {"type": "string", "maxLength": ITEM_MAX}}
                   for k in KEYS},
}
PROMPT = (
    "Read the text that is visible in this screenshot. Return ONLY a JSON object with exactly these keys: "
    + ", ".join(KEYS) + ". Each value is a list of strings copied from text visible in the image (max "
    f"{ITEM_MAX} characters each, at most {ITEMS_MAX} per list): web addresses or domains, phone numbers, brand "
    "or company names, requests for payment (amounts, payment methods, account details), and phrases that "
    "create urgency. Do not add anything that is not visible. Do not judge or label the message."
)


class ImageRefused(ValueError):
    pass


def image_type(data: bytes) -> str:
    """Media type from the magic bytes (never from the file name); raises ImageRefused."""
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageRefused("the image is larger than 5 MB")
    for magic, mtype in IMAGE_TYPES.items():
        if data.startswith(magic):
            return mtype
    raise ImageRefused("only PNG or JPEG images are accepted")


def parse_extraction(answer: str) -> tuple[dict, list[str]]:
    """Enforce the schema in code: ({key: [items]}, [what was dropped and why]). Text is masked for secrets."""
    try:
        data = json.loads(answer or "")
    except ValueError:
        raise ValueError("the model did not return JSON") from None
    if not isinstance(data, dict):
        raise ValueError("the model did not return a JSON object")
    dropped = [f"field {k!r} is not in the schema" for k in sorted(set(data) - set(KEYS))]
    out = {}
    for k in KEYS:
        items = data.get(k, [])
        if not isinstance(items, list):
            dropped.append(f"{k}: not a list")
            items = []
        clean = []
        for it in items:
            if not isinstance(it, str) or not it.strip():
                dropped.append(f"{k}: an item is not text")
            elif len(it) > ITEM_MAX:
                dropped.append(f"{k}: an item is longer than {ITEM_MAX} characters")
            elif len(clean) >= ITEMS_MAX:
                dropped.append(f"{k}: more than {ITEMS_MAX} items")
            else:
                clean.append(redact_text(it.strip()))  # e.g. a password shown in the message is masked
        out[k] = clean
    return out, dropped


# --- normalisation ---

DNS_NAME = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}$")


def normalise_domain(text: str) -> str | None:
    """"https://WWW.Shop-Example.com:443/login?x" -> "shop-example.com"; None if it is not a domain."""
    t = (text or "").strip().strip(".,;:!?()[]<>\"'").lower()
    if not t:
        return None
    if "://" not in t:
        t = "//" + t
    try:
        host = urlsplit(t).hostname or ""
    except ValueError:
        return None
    try:
        host = host.encode("idna").decode("ascii") if any(ord(c) > 127 for c in host) else host
    except UnicodeError:
        return None
    host = host.rstrip(".").removeprefix("www.")
    return host if DNS_NAME.match(host) else None


def normalise_phone(text: str) -> str | None:
    digits = re.sub(r"[^\d+]", "", text or "")
    plus = digits.startswith("+")
    digits = digits.replace("+", "")
    if not 6 <= len(digits) <= 15:
        return None
    return ("+" if plus else "") + digits


def _registrable(domain: str) -> str:
    # ponytail: last two labels, no public-suffix list; "shop.co.uk" style names compare on "co.uk"
    return ".".join(domain.split(".")[-2:])


def _label(domain: str) -> str:
    return _registrable(domain).split(".")[0]


# --- lookalike rule (deterministic) ---

CONFUSABLES = {"0": "o", "1": "l", "3": "e", "5": "s", "rn": "m", "vv": "w", "cl": "d",
               "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i", "ӏ": "l"}
KEYWORDS = ("login", "secure", "verify", "account", "support", "help", "pay", "billing", "update", "official", "my")


def skeleton(label: str) -> str:
    s = unicodedata.normalize("NFKC", label.lower())
    try:
        s = s.encode("ascii").decode("idna") if "xn--" in s else s  # punycode -> unicode, then fold
    except UnicodeError:
        pass
    for k in sorted(CONFUSABLES, key=len, reverse=True):
        s = s.replace(k, CONFUSABLES[k])
    return s.replace("l", "i")  # l and i look alike in many fonts


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def resembles(candidate: str, official: str) -> str | None:
    """Which rule says `candidate` resembles `official` (both normalised domains), or None."""
    if candidate == official or candidate.endswith("." + official):
        return None
    c_label, o_label = _label(candidate), _label(official)
    c_label_ascii = candidate.split(".")[-2] if candidate.count(".") >= 1 else candidate
    if len(o_label) < 4:
        return None
    if c_label == o_label:
        return "same name, different ending" if _registrable(candidate) != _registrable(official) else \
            "your name inside another domain"
    if c_label.replace("-", "") == o_label.replace("-", ""):
        return "extra or missing hyphen"
    if skeleton(c_label) == skeleton(o_label) or skeleton(c_label_ascii) == skeleton(o_label):
        return "look-alike characters"
    c_flat, o_flat = c_label.replace("-", ""), o_label.replace("-", "")
    if o_flat in c_flat and any(k in c_flat.replace(o_flat, "") for k in KEYWORDS):
        return "your name plus a keyword"
    if o_label in candidate.split(".")[:-2] or any(o_label == p for p in candidate.split(".")[:-2]):
        return "your name as a subdomain of another domain"
    d = edit_distance(c_label, o_label)
    if 1 <= d <= (1 if len(o_label) < 7 else 2):
        return f"{d} character(s) different"
    return None


def verdict_domain(domain: str, official: list[str]) -> tuple[str, str | None, str | None]:
    """(verdict, rule, the official domain it resembles)."""
    for o in official:
        if domain == o or domain.endswith("." + o):
            return OFFICIAL, None, o
    for o in official:
        rule = resembles(domain, o)
        if rule:
            return RESEMBLES, rule, o
    return NOT_IN_LIST, None, None


# --- analysis -> findings ---

def analyse(extraction: dict, official_domains: list[str], official_numbers: list[str], image_sha256: str,
            name: str, evidence_id: str | None = None) -> tuple[list[Finding], list[dict]]:
    """Code decides: (findings, items). Every item keeps the model's text, labelled unverified, beside the verdict."""
    official = sorted({d for d in (normalise_domain(x) for x in official_domains) if d})
    numbers = {n for n in (normalise_phone(x) for x in official_numbers) if n}
    target = f"image:{image_sha256[:16]}"
    findings, items = [], []

    def add(check, location, technical):
        findings.append(make_finding(check, target, location, f"screenshot {name}", f"{location}: {technical}",
                                     technical, evidence_id))

    seen = set()
    for text in extraction["urls"]:
        d = normalise_domain(text)
        item = {"kind": "url", "text": text, "source": UNVERIFIED, "domain": d}
        if d is None:
            item.update(verdict="not a web address (ignored)")
        else:
            verdict, rule, o = verdict_domain(d, official)
            item.update(verdict=verdict, rule=rule, resembles=o)
            if d not in seen:
                seen.add(d)
                if verdict == RESEMBLES:
                    add("msg_domain_resembles_yours", f"domain:{d}", f"{d} {RESEMBLES} {o} ({rule}).")
                elif verdict == NOT_IN_LIST:
                    add("msg_domain_not_in_list", f"domain:{d}", f"{d} is {NOT_IN_LIST}.")
        items.append(item)
    for text in extraction["phone_numbers"]:
        n = normalise_phone(text)
        item = {"kind": "phone", "text": text, "source": UNVERIFIED, "normalised": n}
        if n is None:
            item["verdict"] = "not a phone number (ignored)"
        else:
            item["verdict"] = OFFICIAL if n in numbers else NOT_IN_LIST
            if n not in numbers and f"phone:{n}" not in seen:
                seen.add(f"phone:{n}")
                add("msg_phone_not_in_list", f"phone:{mask(n)}", f"Phone number {mask(n)} is {NOT_IN_LIST}.")
        items.append(item)
    for i, text in enumerate(extraction["payment_requests"], 1):
        items.append({"kind": "payment", "text": text, "source": UNVERIFIED, "verdict": "payment request: needs review"})
    if extraction["payment_requests"]:
        add("msg_payment_request", "payment",
            f"{len(extraction['payment_requests'])} payment request(s) read from the image: needs review.")
    for text in extraction["urgency_phrases"]:
        items.append({"kind": "urgency", "text": text, "source": UNVERIFIED, "verdict": "urgency wording"})
    if extraction["urgency_phrases"]:
        add("msg_urgency_language", "urgency",
            f"{len(extraction['urgency_phrases'])} urgency phrase(s) read from the image.")
    for text in extraction["brand_names"]:
        items.append({"kind": "brand", "text": text, "source": UNVERIFIED, "verdict": "brand name (informational)"})
    return findings, items


def evidence_body(image_sha256: str, size: int, media_type: str, extraction: dict, dropped: list[str]) -> bytes:
    """What the evidence store keeps: hash prefix, size, type and the MASKED extraction, never pixels.
    The store's redaction masks long hex strings, so the full SHA-256 goes into the pack's image/image_info.json."""
    return json.dumps({"image_sha256_prefix": image_sha256[:16], "image_bytes": size, "media_type": media_type,
                       "extraction": extraction, "dropped": dropped}, sort_keys=True).encode()


def image_info(image_sha256: str, size: int, media_type: str, included: bool) -> bytes:
    """Always in the evidence pack: the full hash and size of the screenshot (no pixels)."""
    return json.dumps({"image_sha256": image_sha256, "image_bytes": size, "media_type": media_type,
                       "image_file_included": included}, indent=2, sort_keys=True).encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- takedown / report note drafts (fixed templates; nothing here sends anything) ---

TEMPLATES = {
    "registrar": (
        "To: [registrar abuse contact for {domain}]\n"
        "Subject: Domain {domain} uses our brand without permission\n\n"
        "Hello,\n\nWe are [your business name], owner of {official}. The domain {domain} {relation} and appears in a "
        "message that claims to come from us. We did not create or authorise it.\n\n"
        "Evidence: screenshot SHA-256 {image_sha256}, captured {date}. MAYA evidence pack available on request.\n\n"
        "Please review this domain under your abuse policy.\n\n[your name]\n[your role]\n[contact details]"),
    "platform": (
        "To: [platform's report form or trust & safety contact]\n"
        "Subject: Content using our brand without permission\n\n"
        "Hello,\n\nWe are [your business name], owner of {official}. A message or ad on your platform claims to come "
        "from us and points to {domain}, which {relation}. We did not create or authorise it.\n\n"
        "Where it appeared: [link or location of the post or ad]\n"
        "Evidence: screenshot SHA-256 {image_sha256}, captured {date}.\n\n"
        "Please review it under your policies.\n\n[your name]\n[your role]\n[contact details]"),
}


def draft_note(template: str, domain: str, official: str | None, verdict: str, image_sha256: str, date: str) -> str:
    # the note speaks for the business ("our domain"), unlike MAYA's own verdict labels ("your domain")
    relation = f"resembles our domain {official}" if verdict == RESEMBLES else "is not one of our domains"
    return TEMPLATES[template].format(domain=domain, official=official or "[your official domain]",
                                      relation=relation, image_sha256=image_sha256, date=date)
