import asyncio
import unittest

from fastapi import Request
from fastapi.responses import JSONResponse

from esmval_gui import auth
from esmval_gui.app import require_api_token


def request(path, token=None):
    headers = [(b"x-esmval-token", token.encode())] if token is not None else []
    return Request({"type": "http", "method": "GET", "scheme": "http",
                    "path": path, "query_string": b"", "headers": headers})


async def next_handler(_):
    return JSONResponse({"ok": True})


class AuthTests(unittest.TestCase):
    def test_api_requires_token(self):
        for path in ("/api/health", "/api/config/remote", "/api/scripts",
                     "/api/remote/preflight", "/api/remote/submit", "/api/docs"):
            with self.subTest(path=path):
                self.assertEqual(asyncio.run(require_api_token(request(path), next_handler)).status_code, 401)
                self.assertEqual(asyncio.run(require_api_token(request(path, "incorrect"), next_handler)).status_code, 401)
                self.assertEqual(asyncio.run(require_api_token(request(path, auth.ACCESS_TOKEN), next_handler)).status_code, 200)

    def test_static_page_can_show_login(self):
        self.assertEqual(asyncio.run(require_api_token(request("/"), next_handler)).status_code, 200)


if __name__ == "__main__":
    unittest.main()
