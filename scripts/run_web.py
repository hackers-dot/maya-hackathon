"""Start the MAYA web app on http://127.0.0.1:8090 (loopback only).

Run: .venv\\Scripts\\python scripts\\run_web.py [--port 8090]
Users and sessions: data/web.db (git-ignored); sessions are in memory and end when the server stops.
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
    args = p.parse_args(argv)
    server = make_server(port=args.port)  # binds 127.0.0.1 only
    print(f"MAYA web on http://{HOST}:{server.server_address[1]}/  (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
