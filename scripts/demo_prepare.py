"""Print the exact click order for a MAYA demo in the browser. Prints only: no server, no network, no model.

    .venv\\Scripts\\python scripts\\demo_prepare.py --reset   (starts with "Reset demo app": a fresh, vulnerable demo)
    .venv\\Scripts\\python scripts\\demo_prepare.py           (same steps without the reset)
"""
import argparse
import sys

START = ".venv\\Scripts\\python scripts\\run_web.py   then open http://127.0.0.1:8090 and sign in (Company account)"
RESET = ('Sidebar: click "Demo app (loopback)", then "Reset demo app" (top right). It restores YOUR demo app to the '
         'vulnerable state and writes "demo_reset" to your audit log. Refused while a scan is running.')
STEPS = [
    'Click "Run security scan" and watch the live trace until the status chip says finished (12 findings).',
    'Fix proposals: type your name in "Approver name", click "Approve all" (each approval is audited).',
    'Click "Apply approved fixes" (loopback demo app only; health check with automatic rollback).',
    'Click "Re-test": the before/after panel shows FIXED, 12 resolved.',
    'Downloads: click "Download evidence pack" (re-verified before download), optionally "Download report".',
    'Click "+ New chat" and ask: "Which issues did MAYA find on my demo app, and are they fixed now?" '
    "(needs a model key; the answer should say FIXED and cite finding ids).",
]


def steps(reset: bool) -> list[str]:
    return ([RESET] if reset else []) + STEPS


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--reset", action="store_true", help='start with "Reset demo app" (recommended before a demo)')
    args = p.parse_args(argv)
    print(f"0. Start: {START}")
    for i, s in enumerate(steps(args.reset), 1):
        print(f"{i}. {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
