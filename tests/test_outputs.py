import json
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from esmval_gui import app, outputs


@patch.dict(os.environ, {"ESMVAL_GUI_EXECUTION": "local"})
class OutputTests(unittest.TestCase):
    def setUp(self):
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
