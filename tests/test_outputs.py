import json
import importlib.util
import os
from pathlib import Path
import tempfile
import time
import unittest
from urllib.parse import urljoin
from unittest.mock import patch

from fastapi import HTTPException

from esmval_gui import app, outputs


@patch.dict(os.environ, {"ESMVAL_GUI_EXECUTION": "local"})
class OutputTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(app.recipe_monitor, "identity", return_value="rb5533"))
        self.enterContext(patch.object(app.recipe_monitor, "allowed_users", return_value={"rb5533"}))
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "run/output"
        self.output.mkdir(parents=True)
        self.record = {"id": "aabbccddeeff", "remote_dir": str(self.root / "run"), "transport": "local"}
        (self.root / "aabbccddeeff.json").write_text(json.dumps(self.record))

    def test_nested_plots_and_data_are_listed_with_limits(self):
        (self.output / "recipe/plots").mkdir(parents=True)
        (self.output / "recipe/plots/figure.png").write_bytes(b"png")
        (self.output / "recipe/metadata.yml").write_text("caption: 'A map'\n")
        (self.output / "recipe/tas.nc").write_bytes(b"data")
        listing = outputs.list_files(self.record)
        self.assertEqual(len(listing["files"]), 3)
        self.assertEqual(listing["files"][0]["path"], "recipe/plots/figure.png")
        self.assertEqual({item["kind"] for item in listing["files"]}, {"image", "text", "data"})
        with patch.object(outputs, "MAX_FILES", 1):
            self.assertTrue(outputs.list_files(self.record)["truncated"])

    def test_escape_and_symlinks_are_rejected(self):
        secret = self.root / "secret.txt"
        secret.write_text("outside the run")
        for path in ("../secret.txt", str(secret), "..\\secret.txt", "a/../../secret.txt"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                outputs.resolve_file(self.record, path)
        if os.name != "nt":
            (self.output / "external.txt").symlink_to(secret)
            (self.output / "external-dir").symlink_to(self.root, target_is_directory=True)
            with self.assertRaises(ValueError):
                outputs.resolve_file(self.record, "external-dir/secret.txt")
            self.assertEqual(outputs.list_files(self.record)["files"], [])
            with self.assertRaises(ValueError):
                outputs.resolve_file(self.record, "external.txt")

    def test_unregistered_and_remote_jobs_are_not_local_file_browsers(self):
        remote = {**self.record, "transport": "ssh"}
        self.assertFalse(outputs.list_files(remote)["available"])
        with patch.object(app, "STATE", self.root):
            with self.assertRaises(HTTPException) as error:
                app.job_output("000000000000", "secret.txt")
            self.assertEqual(error.exception.status_code, 404)

    def test_text_preview_is_bounded_and_downloads_are_attachments(self):
        (self.output / "provenance.yml").write_text("x" * (outputs.MAX_TEXT_BYTES + 50))
        (self.output / "report.html").write_text("<script>alert(1)</script>")
        with patch.object(app, "STATE", self.root):
            response = app.job_output(self.record["id"], "provenance.yml")
            self.assertEqual(len(response.body), outputs.MAX_TEXT_BYTES)
            self.assertEqual(response.headers["X-Studio-Truncated"], "true")
            with self.assertRaises(HTTPException):
                app.job_output(self.record["id"], "report.html")
            response = app.job_output(self.record["id"], "report.html", download=True)
            self.assertIn("attachment", response.headers["content-disposition"])
            self.assertEqual(response.media_type, "application/octet-stream")

    def test_report_discovery_prefers_native_index(self):
        (self.output / "recipe/plots").mkdir(parents=True)
        for path in ("recipe/index.html", "recipe/plots/extra.html", "report.htm"):
            (self.output / path).write_text("<h1>Report</h1>")
        listing = outputs.list_files(self.record)
        self.assertEqual(listing["reports"], ["recipe/index.html", "report.htm", "recipe/plots/extra.html"])
        self.assertTrue(all(file["kind"] == "html" for file in listing["files"]))

    def test_report_links_preserve_assets_and_sandbox(self):
        (self.output / "recipe/plots").mkdir(parents=True)
        (self.output / "recipe/index.html").write_text('<img src="plots/a plot.svg">')
        (self.output / "recipe/plots/a plot.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
        with patch.object(app, "STATE", self.root):
            url = app.job_report(self.record["id"], "recipe/index.html")["url"]
            _, run, ticket, path = url.split("/", 3)
            report = app.report_file(run, ticket, path)
            self.assertEqual(report.media_type, "text/html")
            self.assertIn("sandbox allow-scripts allow-downloads;", report.headers["Content-Security-Policy"])
            self.assertNotIn("allow-same-origin", report.headers["Content-Security-Policy"])
            self.assertEqual(report.headers["Referrer-Policy"], "no-referrer")
            self.assertEqual(app.report_file(run, ticket, "recipe/plots/a plot.svg").media_type, "image/svg+xml")
            for directory in ("recipe/plots", "recipe/plots/"):
                listing = app.report_file(run, ticket, directory).body.decode()
                href = listing.split('href="')[1].split('"')[0]
                self.assertEqual(urljoin(f"https://studio/reports/{run}/{ticket}/{directory}", href),
                                 f"https://studio/reports/{run}/{ticket}/recipe/plots/a%20plot.svg")

    def test_report_capability_rejects_expiry_wrong_run_and_escape(self):
        ticket = outputs.report_ticket(self.record["id"])
        self.assertTrue(outputs.valid_report_ticket(self.record["id"], ticket))
        for bad in ("invalid", ticket + "a", ticket + "é", outputs.report_ticket(self.record["id"], int(time.time()) - 1)):
            with self.assertRaises(HTTPException) as error:
                app.report_file(self.record["id"], bad, "index.html")
            self.assertEqual(error.exception.status_code, 401)
        self.assertFalse(outputs.valid_report_ticket("another-run", ticket))
        (self.root / "secret.html").write_text("secret")
        (self.output / "escape.html").symlink_to(self.root / "secret.html")
        with patch.object(app, "STATE", self.root):
            for path in ("../secret.html", "escape.html"):
                with self.assertRaises(HTTPException) as error:
                    app.report_file(self.record["id"], ticket, path)
                self.assertEqual(error.exception.status_code, 400)
            with self.assertRaises(HTTPException):
                app.job_report(self.record["id"], "escape.html")

    @unittest.skipUnless(importlib.util.find_spec("pypdfium2") and importlib.util.find_spec("PIL"), "Install ARE PDF dependencies")
    def test_pdf_pages_render_as_images_and_download_as_original(self):
        pdf = (Path(__file__).parent / "fixtures/two-pages.pdf").read_bytes()
        (self.output / "plot.pdf").write_bytes(pdf)
        with patch.object(app, "STATE", self.root):
            pages = [app.job_output(self.record["id"], "plot.pdf", page=page) for page in (0, 1)]
            for page in pages:
                self.assertEqual(page.headers["X-Studio-Pages"], "2")
                self.assertEqual(page.media_type, "image/png")
                self.assertTrue(page.body.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertNotEqual(pages[0].body, pages[1].body)
            with self.assertRaises(HTTPException):
                app.job_output(self.record["id"], "plot.pdf", page=2)
            download = app.job_output(self.record["id"], "plot.pdf", download=True)
            self.assertEqual(Path(download.path).read_bytes(), pdf)
            with patch.object(outputs, "MAX_PREVIEW_BYTES", 1), self.assertRaises(HTTPException):
                app.job_output(self.record["id"], "plot.pdf")


if __name__ == "__main__":
    unittest.main()
