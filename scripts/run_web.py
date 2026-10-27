"""Start the MAYA web app on http://127.0.0.1:8090 (loopback only).

Run: .venv\\Scripts\\python scripts\\run_web.py [--port 8090] [--data-dir DIR] [--ui-preview]
Users: <data-dir>/web.db (default data/, git-ignored); sessions are in memory and end when the server stops.
--ui-preview enables the test-only /__preview/<page>.html route used for screenshots; never use it otherwise.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.web.server import DEFAULT_PORT, HOST, make_server  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--data-dir", type=Path, default=None, help="where web.db lives (default: data/)")
    p.add_argument("--ui-preview", action="store_true", help="test-only page preview route for screenshots")
    args = p.parse_args(argv)
    server = make_server(port=args.port, data_dir=args.data_dir, ui_preview=args.ui_preview)  # 127.0.0.1 only
    mode = " [UI PREVIEW MODE]" if args.ui_preview else ""
    print(f"MAYA web on http://{HOST}:{server.server_address[1]}/{mode}  (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
