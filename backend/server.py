import os
from http.server import ThreadingHTTPServer

from sci_platform.api import SciPlatformHandler
from sci_platform.db import initialize_database


def main() -> None:
    initialize_database()
    host = os.getenv("SCI_HOST", "127.0.0.1")
    port = int(os.getenv("SCI_PORT", "8000"))
    server = ThreadingHTTPServer((host, port), SciPlatformHandler)
    print(f"SCI platform running at http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
