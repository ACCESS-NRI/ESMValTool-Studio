import asyncio
import unittest
from unittest.mock import patch

from fastapi import Request
from fastapi.responses import JSONResponse

from esmval_gui import auth
from esmval_gui.app import require_api_token


def request(path, token=None, root_path="", method="GET", extra_headers=()):
    headers = [(b"x-esmval-token", token.encode())] if token is not None else []
    return Request({"type": "http", "method": method, "scheme": "http",
                    "path": path, "root_path": root_path, "query_string": b"",
                    "headers": headers + list(extra_headers)})


async def next_handler(_):
    return JSONResponse({"ok": True})


class AuthTests(unittest.TestCase):
    def test_api_requires_token(self):
        for path in ("/api/health", "/api/config/remote", "/api/scripts", "/api/scripts/source",
                     "/api/remote/preflight", "/api/remote/submit", "/api/docs"):
            with self.subTest(path=path):
                self.assertEqual(asyncio.run(require_api_token(request(path), next_handler)).status_code, 401)
                self.assertEqual(asyncio.run(require_api_token(request(path, "incorrect"), next_handler)).status_code, 401)
                self.assertEqual(asyncio.run(require_api_token(request(path, auth.ACCESS_TOKEN), next_handler)).status_code, 200)

    def test_static_page_can_show_login(self):
        self.assertEqual(asyncio.run(require_api_token(request("/"), next_handler)).status_code, 200)

    def test_prefixed_api_requires_token_and_is_not_cached(self):
        prefix = "/node/gadi-test/12345/studio"
        for path in ("/api/health", prefix + "/api/health"):
            with self.subTest(path=path):
                denied = asyncio.run(require_api_token(request(path, root_path=prefix), next_handler))
                self.assertEqual(denied.status_code, 401)
                allowed = asyncio.run(require_api_token(request(path, auth.ACCESS_TOKEN, prefix), next_handler))
                self.assertEqual(allowed.status_code, 200)
                self.assertEqual(allowed.headers["Cache-Control"], "no-store")

    @patch.dict("os.environ", {"ESMVAL_GUI_REQUIRE_XSRF": "1"})
    def test_proxy_writes_require_matching_xsrf_header_and_cookie(self):
        for extra, expected in (
            ([], 403),
            ([(b"cookie", b"_xsrf=abc")], 403),
            ([(b"cookie", b"_xsrf=abc"), (b"x-xsrftoken", b"wrong")], 403),
            ([(b"cookie", b"_xsrf=abc"), (b"x-xsrftoken", b"abc")], 200),
        ):
            req = request("/api/parse", auth.ACCESS_TOKEN, method="POST", extra_headers=extra)
            self.assertEqual(asyncio.run(require_api_token(req, next_handler)).status_code, expected)


if __name__ == "__main__":
    unittest.main()
