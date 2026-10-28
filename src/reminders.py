"""Re-scan reminders without a scheduler: a stored due date and a small .ics file. Nothing runs by itself."""
from datetime import datetime, timezone

CHOICES = (7, 14, 30)
DAY = 86_400


def due_at(now: float, days: int) -> float:
    if days not in CHOICES:
        raise ValueError(f"choose {', '.join(map(str, CHOICES))} days")
    return now + days * DAY


def _escape(text: str) -> str:
    """RFC 5545 TEXT escaping."""
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", ""))


def _fold(line: str) -> list[str]:
    """Lines longer than 75 octets continue on the next line after a single space (RFC 5545 3.1)."""
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode("utf-8")) > 75:
            out.append(cur)
            cur = " " + ch
        else:
            cur += ch
    return out + [cur]


def ics(label: str, due: float, uid: str, now: float) -> str:
    """An all-day VEVENT on the due date. No URLs, no account data, nothing secret."""
    day = datetime.fromtimestamp(due, timezone.utc).strftime("%Y%m%d")
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//MAYA//re-scan reminder//EN", "CALSCALE:GREGORIAN",
             "BEGIN:VEVENT", f"UID:{uid}@maya.local", f"DTSTAMP:{stamp}", f"DTSTART;VALUE=DATE:{day}",
             f"SUMMARY:{_escape(f'Re-scan {label} with MAYA')}",
             f"DESCRIPTION:{_escape('Open MAYA and press Re-scan for this asset. MAYA does not run anything by itself.')}",
             "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(part for line in lines for part in _fold(line)) + "\r\n"
