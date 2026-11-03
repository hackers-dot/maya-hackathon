"""Contrast ratios (WCAG 2.1) for the MAYA visual tokens: text, badges, buttons and the focus ring on their real
backgrounds, in both themes. Translucent surfaces are composited over the page background first.
Run: .venv\\Scripts\\python scripts\\contrast_check.py   (exit 1 if any pair is under its minimum)
"""
import sys


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def over(fg_rgba, bg_hex):
    """Composite rgba(r, g, b, a) over an opaque background."""
    *c, a = fg_rgba
    b = rgb(bg_hex)
    return "#" + "".join(f"{round(c[i] * a + b[i] * (1 - a)):02x}" for i in range(3))


def lum(h):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# --- dark theme (default) ---
BG, BG2, SIDEBAR = "#050B0A", "#071713", "#091311"
CARD = over((13, 27, 23, 0.82), BG)         # glass card over the page
NESTED = "#10231D"                          # --panel-2: rows inside a card
ON_ACCENT = "#03140D"                       # dark text on bright fills
DARK = [
    ("body text #F4F8F6 on page", "#F4F8F6", BG, 4.5),
    ("body text on card", "#F4F8F6", CARD, 4.5),
    ("secondary #A2B1AC on page", "#A2B1AC", BG, 4.5),
    ("secondary on card", "#A2B1AC", CARD, 4.5),
    ("secondary on nested row", "#A2B1AC", NESTED, 4.5),
    ("muted --faint on page", "#7E918A", BG, 4.5),
    ("muted --faint on card", "#7E918A", CARD, 4.5),
    ("muted --faint on nested row", "#7E918A", NESTED, 4.5),
    ("muted --faint on sidebar", "#7E918A", SIDEBAR, 4.5),
    ("document muted #687B74 on card (not used for text)", "#687B74", CARD, 0),
    ("accent text #00E887 on card", "#00E887", CARD, 4.5),
    ("accent text on nested row", "#00E887", NESTED, 4.5),
    ("danger text #FF6B78 on card", "#FF6B78", CARD, 4.5),
    ("warn text #FFC94A on card", "#FFC94A", CARD, 4.5),
    ("primary button text on #00E887", ON_ACCENT, "#00E887", 4.5),
    ("primary button text on #00D6C9 (gradient end)", ON_ACCENT, "#00D6C9", 4.5),
    ("secondary button text on #10231D", "#F4F8F6", NESTED, 4.5),
    ("danger button text #FFE5E8 on #6B1620", "#FFE5E8", "#6B1620", 4.5),
    ("disabled button text #A2B1AC on #163128 (exempt, shown)", "#A2B1AC", "#163128", 0),
    ("badge critical: dark on #FF4D5E", ON_ACCENT, "#FF4D5E", 4.5),
    ("badge high: dark on #FF963D", ON_ACCENT, "#FF963D", 4.5),
    ("badge medium: dark on #FFC94A", ON_ACCENT, "#FFC94A", 4.5),
    ("badge low: dark on #48A8FF", ON_ACCENT, "#48A8FF", 4.5),
    ("badge info: dark on #929CA5", ON_ACCENT, "#929CA5", 4.5),
    ("chip text secondary on nested", "#A2B1AC", NESTED, 4.5),
    ("chip ok #00E887 on nested", "#00E887", NESTED, 4.5),
    ("focus ring #39FF88 vs page (UI, 3:1)", "#39FF88", BG, 3),
    ("focus ring vs card (UI, 3:1)", "#39FF88", CARD, 3),
    ("input border #2A5A47 vs card (UI, 3:1)", "#2A5A47", CARD, 0),
    ("input border #3E7A62 vs nested input (UI, 3:1)", "#3E7A62", NESTED, 3),
    ("wordmark #39FF88 on sidebar (large)", "#39FF88", SIDEBAR, 3),
    ("score ring critical #FF4D5E vs card (graphic, 3:1)", "#FF4D5E", CARD, 3),
    ("score ring low #48A8FF vs card (graphic, 3:1)", "#48A8FF", CARD, 3),
    ("info #929CA5 vs card (graphic, 3:1)", "#929CA5", CARD, 3),
]
# --- light theme ---
LBG = "#EEF5F2"
LCARD = over((255, 255, 255, 0.86), LBG)
LNEST = "#F3F8F6"
LIGHT = [
    ("light: body #0B1A15 on page", "#0B1A15", LBG, 4.5),
    ("light: body on card", "#0B1A15", LCARD, 4.5),
    ("light: secondary #3F524B on card", "#3F524B", LCARD, 4.5),
    ("light: muted #56685F on page", "#56685F", LBG, 4.5),
    ("light: muted on nested", "#56685F", LNEST, 4.5),
    ("light: accent text #00703F on card", "#00703F", LCARD, 4.5),
    ("light: accent text on page", "#00703F", LBG, 4.5),
    ("light: danger #B3261E on card", "#B3261E", LCARD, 4.5),
    ("light: warn #8A5A00 on card", "#8A5A00", LCARD, 4.5),
    ("light: primary button text on #00E887", ON_ACCENT, "#00E887", 4.5),
    ("light: focus ring #00703F vs page (UI, 3:1)", "#00703F", LBG, 3),
    ("light: input border #6E9585 vs nested (UI, 3:1)", "#6E9585", LNEST, 3),
    ("light: badge critical dark on #FF4D5E", ON_ACCENT, "#FF4D5E", 4.5),
]


def main() -> int:
    bad = 0
    print(f"composited card: dark {CARD}, light {LCARD}")
    for label, fg, bg, need in DARK + LIGHT:
        r = ratio(fg, bg)
        ok = r >= need
        bad += not ok
        tag = "info" if need == 0 else ("ok  " if ok else "FAIL")
        print(f"{tag} {r:5.2f}:1  (min {need or '-'})  {label}")
    print(f"{bad} pair(s) under the minimum")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
