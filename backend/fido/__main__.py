"""Development server: python -m fido [--host 0.0.0.0] [--port 5000]"""

import argparse

from .app import create_app

parser = argparse.ArgumentParser(description="Run the Fido backend (development server).")
parser.add_argument("--host", default="0.0.0.0", help="0.0.0.0 lets phones and the robot on the LAN connect")
parser.add_argument("--port", type=int, default=5000)
args = parser.parse_args()

# threaded: each Server-Sent Events stream holds one thread open.
create_app().run(host=args.host, port=args.port, threaded=True)
