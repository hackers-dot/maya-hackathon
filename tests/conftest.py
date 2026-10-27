import threading

import pytest

from demo_app.app import make_server


@pytest.fixture
def start_demo():
    """start_demo(fixed=False) -> base URL of a demo shop on a free 127.0.0.1 port; stopped after the test."""
    servers = []

    def start(fixed: bool = False) -> str:
        server = make_server(port=0, fixed=fixed)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    for s in servers:
        s.shutdown()
        s.server_close()
