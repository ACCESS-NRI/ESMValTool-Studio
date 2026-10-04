import argparse
import webbrowser
from threading import Timer

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Browse, edit, and submit ESMValTool recipes")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not args.no_browser:
        Timer(1.0, webbrowser.open, args=(f"http://{args.host}:{args.port}/",)).start()
    uvicorn.run("esmval_gui.app:app", host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
