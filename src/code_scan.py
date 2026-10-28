"""Source-code connector: read an uploaded ZIP safely, then run pure text checks on it.

Nothing from the upload is ever executed, imported or written to disk: the archive is read in memory with
zipfile, member by member, with hard limits. Findings carry a file path, a line number and a MASKED excerpt;
a secret's full value is never put into a finding, the evidence store or a log. Wording: "appears",
"detected"; the checks show patterns in text, not that a weakness is reachable.
"""
import io
import re
import stat
import tokenize
import zipfile

from src.evidence import mask, redact_text
from src.models import Finding
from src.scanner.findings import make_finding

MAX_UPLOAD_BYTES = 10 * 1024 * 1024        # the request body (the ZIP itself)
MAX_FILES = 2000                           # entries in the archive
MAX_TOTAL_UNCOMPRESSED = 50 * 1024 * 1024  # sum of all members, declared AND actually read
MAX_RATIO = 100                            # uncompressed / compressed, per member (zip bomb guard)
MAX_FILE_BYTES = 2 * 1024 * 1024           # larger single files are skipped, not read
MAX_FINDINGS = 300
BINARY_SNIFF = 8192

CODE_CHECKS = ("code_hardcoded_secret", "code_env_file_committed", "code_debug_enabled", "code_dangerous_eval",
               "code_unsafe_deserialization", "code_shell_true", "code_weak_password_hash",
               "code_outdated_dependency")


class UploadRefused(ValueError):
    pass


def safe_name(name: str) -> bool:
    """Relative, no "..", no drive letter, no backslash, no NUL."""
    if not name or name.startswith(("/", "\\")) or "\\" in name or "\x00" in name or re.match(r"^[A-Za-z]:", name):
        return False
    return ".." not in name.split("/")


def read_zip(data: bytes, max_bytes: int = MAX_UPLOAD_BYTES) -> tuple[list[tuple[str, str]], dict]:
    """[(path, text)] for every text member, plus counts. Raises UploadRefused for an unsafe archive.
    max_bytes: 10 MB for uploads, 25 MB for GitHub downloads; every other limit is the same."""
    if len(data) > max_bytes:
        raise UploadRefused(f"the ZIP is larger than {max_bytes // (1024 * 1024)} MB")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise UploadRefused("not a ZIP file") from None
    with z:
        infos = z.infolist()
        if len(infos) > MAX_FILES:
            raise UploadRefused(f"the ZIP has more than {MAX_FILES} entries")
        for info in infos:  # every name and type is checked before ANY member is read
            if not safe_name(info.filename):
                raise UploadRefused(f"unsafe path in the ZIP: {info.filename[:80]!r}")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise UploadRefused(f"symbolic link in the ZIP: {info.filename[:80]!r}")
            if info.flag_bits & 0x1:
                raise UploadRefused("encrypted ZIP members are not supported")
        if sum(i.file_size for i in infos) > MAX_TOTAL_UNCOMPRESSED:
            raise UploadRefused("the ZIP expands to more than 50 MB")
        files, counts, total = [], {"entries": len(infos), "scanned": 0, "skipped_binary": 0, "skipped_large": 0}, 0
        for info in infos:
            if info.is_dir():
                continue
            if info.file_size > MAX_FILE_BYTES:
                counts["skipped_large"] += 1
                continue
            if info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
                raise UploadRefused(f"suspicious compression ratio in {info.filename[:80]!r} (possible zip bomb)")
            with z.open(info) as f:
                raw = f.read(MAX_FILE_BYTES + 1)  # never trust the declared size: read at most the cap
            total += len(raw)
            if len(raw) > MAX_FILE_BYTES or total > MAX_TOTAL_UNCOMPRESSED:
                raise UploadRefused("the ZIP expands to more than its declared size or 50 MB")
            if b"\x00" in raw[:BINARY_SNIFF]:
                counts["skipped_binary"] += 1
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                counts["skipped_binary"] += 1
                continue
            files.append((info.filename, text))
            counts["scanned"] += 1
    return files, counts


# --- checks: pure functions over (path, text) ---

SECRET_NAME = r"[A-Za-z0-9_.-]*(?:api[_-]?key|secret|token|passw(?:or)?d|pwd|access[_-]?key|private[_-]?key|client[_-]?secret|auth)[A-Za-z0-9_.-]*"
SECRET_ASSIGN = re.compile(rf"""(?i)(?P<name>{SECRET_NAME})["']?\s*[:=]\s*(?P<q>["']?)(?P<value>[^\s"'`,;)]{{6,}})(?P=q)""")
TOKEN_PREFIX = re.compile(r"\b(?:(?:sk|pk|rk)[-_][A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}"
                          r"|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,})")
PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
# values that are references or placeholders, not secrets
PLACEHOLDER = re.compile(r"(?i)^(?:os\.|process\.env|env\(|getenv|\$\{|\$[A-Z_]|<|%\(|\{\{|settings\.|config\.|"
                         r"none$|null$|true$|false$|changeme|change_me|example|placeholder|your[_-]|x{4,}|\*{3,}|"
                         r"\.\.\.|test$|dummy|redacted)")
ENV_TEMPLATES = (".env.example", ".env.sample", ".env.template", ".env.dist")
PATTERNS = {  # check -> (regex, what was detected)
    "code_debug_enabled": (re.compile(r"\bDEBUG\s*=\s*True\b|\bdebug\s*=\s*True\b|app\.run\([^)]*debug\s*=\s*True"),
                           "debug mode appears to be switched on"),
    "code_dangerous_eval": (re.compile(r"(?<![\w.])(?:eval|exec)\s*\("), "eval( / exec( call detected"),
    "code_unsafe_deserialization": (re.compile(r"\bpickle\.loads?\s*\("), "pickle.load(s) call detected"),
    "code_shell_true": (re.compile(r"\bshell\s*=\s*True\b"), "subprocess call with shell=True detected"),
}
WEAK_HASH = re.compile(r"(?i)\b(?:md5|sha1)\b")
PASSWORD_WORD = re.compile(r"(?i)passw|pwd")
CODE_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx", ".php", ".rb", ".go", ".java", ".cs",
                 ".sh", ".env", ".yml", ".yaml", ".json", ".toml", ".ini", ".cfg", ".conf", ".txt", ".html",
                 ".pem", ".key")

# --- fewer false positives (Web 10.1) ---
TEST_DIRS = {"tests", "test", "fixtures", "demo_app", "examples", "docs"}
TEST_LABEL = "test or demo code"
ONE_LOWER = {"critical": "high", "high": "medium", "medium": "low", "low": "info", "info": "info"}
NUMERIC = re.compile(r"^[\d_.,]+$")                                   # 40000, 40_000, 1.5
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")              # BASETEN_API_KEY (a variable NAME)
IDENTIFIER = re.compile(r"^[a-z]+(?:[_-][a-z]+)*[_-]?$")    # code_pattern, maya-verify-, forged (letters only)
SAMPLE_WORDS = re.compile(r"(?i)preview|sample|example|dummy|placeholder|changeme|your[_-]")
JS_SUFFIXES = (".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx")


def is_test_path(path: str) -> bool:
    return any(part.lower() in TEST_DIRS for part in path.split("/")[:-1])


def looks_like_secret(value: str) -> bool:
    """A literal that is shaped like a key, token or password, not a number, a variable name or an identifier.
    (Known token prefixes and PEM headers are detected separately and always count.)"""
    value = re.sub(r"(?:\\+[nrt])+$", "", value)  # "1500\n" written inside a string literal is still the number 1500
    if NUMERIC.match(value) or ENV_NAME.match(value) or IDENTIFIER.match(value) or SAMPLE_WORDS.search(value):
        return False
    return bool(re.search(r"\d", value)) or (value.lower() != value and value.upper() != value)


def code_only_lines(path: str, text: str) -> tuple[list[str], str | None]:
    """Each line with string literals and comments blanked out, for the code-pattern rules.
    .py: the standard tokenize module; .js and friends: a small string/comment stripper. Other files:
    unchanged. Returns (lines, note); note says when tokenizing failed and the raw lines were used."""
    lines = text.splitlines()
    name = path.rsplit("/", 1)[-1].lower()
    if name.endswith(".py"):
        out = [list(l) for l in lines]
        try:
            for tok in tokenize.generate_tokens(io.StringIO(text).readline):
                if tok.type in (tokenize.STRING, tokenize.COMMENT):
                    (r1, c1), (r2, c2) = tok.start, tok.end
                    for r in range(r1, r2 + 1):
                        if r - 1 >= len(out):
                            break
                        start = c1 if r == r1 else 0
                        end = c2 if r == r2 else len(out[r - 1])
                        for c in range(start, min(end, len(out[r - 1]))):
                            out[r - 1][c] = " "
        except (tokenize.TokenError, IndentationError, SyntaxError):
            return lines, "matched without tokenizing: the file could not be tokenized"
        return ["".join(l) for l in out], None
    if name.endswith(JS_SUFFIXES):
        return strip_js(text).splitlines(), None
    return lines, None


def strip_js(text: str) -> str:
    """Blank out '...', "...", `...` strings and // and /* */ comments; newlines are kept."""
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "'\"`":
            j = i + 1
            while j < n and text[j] != ch and not (ch != "`" and text[j] == "\n"):
                j += 2 if text[j] == "\\" else 1
            seg = text[i:j + 1]
            out.append("".join(c if c == "\n" else " " for c in seg))
            i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join(c if c == "\n" else " " for c in text[i:j]))
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


# A SHORT EXAMPLE LIST, not a complete advisory database: package -> first release this table treats as
# current enough. Older pinned versions "appear outdated".
PY_OLD_BEFORE = {"django": (4, 2, 0), "flask": (2, 3, 0), "requests": (2, 31, 0), "pyyaml": (5, 4, 0),
                 "jinja2": (3, 1, 0), "urllib3": (1, 26, 18)}
NPM_OLD_BEFORE = {"lodash": (4, 17, 21), "jquery": (3, 5, 0), "express": (4, 19, 0), "minimist": (1, 2, 6),
                  "axios": (1, 6, 0)}
VERSION = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def masked_line(line: str, value: str | None = None) -> str:
    """The line with the secret value masked (first 3 + "..." + last 2), then the generic redaction on top."""
    line = line.strip()[:240]
    if value:
        line = line.replace(value, mask(value))
    return redact_text(line)


def _finding(check, upload, path, lineno, excerpt, technical, store) -> Finding:
    location = f"{path}:{lineno}"
    evidence_id = store.add("FILE", f"zip://{upload}/{path}#L{lineno}", [], 0, [], excerpt.encode()) \
        if store is not None else None
    test = is_test_path(path)
    f = make_finding(check, f"zip:{upload}", location, f"{path} line {lineno}", f"{location}: {excerpt}",
                     (f"In {TEST_LABEL}: " if test else "") + technical, evidence_id)
    if test:  # fixed rule: one severity level lower than the table, labelled (info stays info)
        f.severity, f.context = ONE_LOWER[f.severity], TEST_LABEL
    return f


def _version(text: str):
    m = VERSION.search(text)
    return tuple(int(x or 0) for x in m.groups()) if m else None


def dependency_hits(path: str, text: str) -> list[tuple[int, str, str, str]]:
    """(line, package, version, current) for pinned versions older than the example tables."""
    name = path.rsplit("/", 1)[-1].lower()
    hits = []
    if name == "requirements.txt" or (name.startswith("requirements") and name.endswith(".txt")):
        for n, line in enumerate(text.splitlines(), 1):
            m = re.match(r"\s*([A-Za-z0-9_.-]+)\s*==\s*([0-9][0-9A-Za-z.]*)", line)
            if m and m[1].lower() in PY_OLD_BEFORE and (v := _version(m[2])) and v < PY_OLD_BEFORE[m[1].lower()]:
                hits.append((n, m[1].lower(), m[2], ".".join(map(str, PY_OLD_BEFORE[m[1].lower()]))))
    elif name == "package.json":
        for n, line in enumerate(text.splitlines(), 1):
            m = re.match(r'\s*"([@A-Za-z0-9_./-]+)"\s*:\s*"[~^=v]?([0-9][0-9A-Za-z.]*)"', line)
            if m and m[1].lower() in NPM_OLD_BEFORE and (v := _version(m[2])) and v < NPM_OLD_BEFORE[m[1].lower()]:
                hits.append((n, m[1].lower(), m[2], ".".join(map(str, NPM_OLD_BEFORE[m[1].lower()]))))
    return hits


def scan_file(path: str, text: str, upload: str, store=None) -> list[Finding]:
    out = []
    base = path.rsplit("/", 1)[-1].lower()
    if (base == ".env" or base.startswith(".env.")) and base not in ENV_TEMPLATES:
        lines = [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
        first = next((n for n, l in enumerate(text.splitlines(), 1) if l.strip() and not l.lstrip().startswith("#")), 1)
        excerpt = masked_line(lines[0].split("=", 1)[0] + "=" + mask(lines[0].split("=", 1)[1].strip())) \
            if lines and "=" in lines[0] else f"(file with {len(lines)} setting lines)"
        out.append(_finding("code_env_file_committed", upload, path, first, excerpt,
                            f"A {base} file with {len(lines)} setting line(s) is in the upload.", store))
    if not base.endswith(CODE_SUFFIXES) and not base.startswith(".env"):
        return out
    is_python = base.endswith(".py")
    code_lines, token_note = code_only_lines(path, text)
    for n, line in enumerate(text.splitlines(), 1):
        code = code_lines[n - 1] if n - 1 < len(code_lines) else line
        if len(out) >= MAX_FINDINGS:
            break
        if PRIVATE_KEY.search(line):
            out.append(_finding("code_hardcoded_secret", upload, path, n, "-----BEGIN ... PRIVATE KEY----- (key body not stored)",
                                "A private key block appears in the source.", store))
            continue
        m = SECRET_ASSIGN.search(line)
        # an unquoted value must look like a literal (code like request.form["x"] or get_pw() is a reference)
        literal = bool(m) and (m["q"] or (not re.search(r"[\[(.]", m["value"]) and re.search(r"\d", m["value"])))
        if (m and literal and not PLACEHOLDER.match(m["value"]) and looks_like_secret(m["value"])
                and not base.endswith(ENV_TEMPLATES)):
            out.append(_finding("code_hardcoded_secret", upload, path, n, masked_line(line, m["value"]),
                                f"A value assigned to {m['name']!r} appears to be a hard-coded secret.", store))
        elif (t := TOKEN_PREFIX.search(line)):
            out.append(_finding("code_hardcoded_secret", upload, path, n, masked_line(line, t[0]),
                                "A string with a well-known API token prefix appears in the source.", store))
        note = f" ({token_note})" if token_note else ""
        for check, (rx, what) in PATTERNS.items():  # code patterns: matched on the code, not inside strings/comments
            if rx.search(code) and (check != "code_dangerous_eval" or base.endswith((".py", ".js", ".mjs", ".cjs",
                                                                                      ".ts", ".jsx", ".tsx", ".php"))):
                if check == "code_unsafe_deserialization" and not is_python:
                    continue
                out.append(_finding(check, upload, path, n, masked_line(line), f"{what}{note}.", store))
        if WEAK_HASH.search(code) and PASSWORD_WORD.search(line):
            out.append(_finding("code_weak_password_hash", upload, path, n, masked_line(line),
                                f"MD5 or SHA-1 appears to be used near password handling{note}.", store))
    for n, pkg, version, current in dependency_hits(path, text):
        out.append(_finding("code_outdated_dependency", upload, path, n, masked_line(f"{pkg} {version}"),
                            f"{pkg} {version} appears outdated (MAYA's short example table treats releases "
                            f"before {current} as outdated).", store))
    return out


def scan_upload(files: list[tuple[str, str]], upload: str, store=None) -> list[Finding]:
    findings: dict[str, Finding] = {}
    for path, text in files:
        for f in scan_file(path, text, upload, store):
            findings.setdefault(f.id, f)
            if len(findings) >= MAX_FINDINGS:
                return list(findings.values())
    return list(findings.values())
