"""`python -m recorder [--port 5100]`: start the studio and open the browser."""

from __future__ import annotations

import argparse
import threading
import webbrowser

from .app import create_app


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m recorder", description="Fido's local recording studio.")
    ap.add_argument("--port", type=int, default=5100)
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")
    args = ap.parse_args()
    url = f"http://127.0.0.1:{args.port}/"
    print(f"\n  Fido recording studio: {url}\n  (Press Ctrl+C in this window to stop.)\n")
    if not args.no_browser:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    try:
        create_app().run(host="127.0.0.1", port=args.port, debug=False, threaded=True)
    except OSError as e:
        raise SystemExit(f"Could not start on port {args.port} ({e}). Try: python -m recorder --port {args.port + 1}")


if __name__ == "__main__":
    main()
