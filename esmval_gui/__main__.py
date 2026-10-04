import argparse
import ipaddress
import os
import webbrowser
from threading import Timer

import uvicorn

from . import auth


def main():
    parser = argparse.ArgumentParser(description="Browse, edit, and submit ESMValTool recipes")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    try:
        if not ipaddress.ip_address(args.host).is_loopback:
            parser.error("Bind to a loopback address and use an SSH tunnel for remote access")
    except ValueError:
        if args.host != "localhost":
            parser.error("Use 127.0.0.1, ::1, or localhost as the bind address")
    if os.environ.get("ESMVAL_GUI_ACCESS_TOKEN"):
        print("ESMValTool Studio access token: set by ESMVAL_GUI_ACCESS_TOKEN", flush=True)
    else:
        print("ESMValTool Studio access token (enter in the browser):", auth.ACCESS_TOKEN, flush=True)
    if not args.no_browser:
        Timer(1.0, webbrowser.open, args=(f"http://{args.host}:{args.port}/",)).start()
    uvicorn.run("esmval_gui.app:app", host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
