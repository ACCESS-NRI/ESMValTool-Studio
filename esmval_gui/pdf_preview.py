"""Render PDF pages in a bounded, separate process (PDFium is not thread safe)."""

import io
import json
from pathlib import Path
import subprocess
import sys


def render(path: Path, page: int) -> tuple[bytes, int]:
    try:
        result = subprocess.run([sys.executable, "-m", "esmval_gui.pdf_preview", str(path), str(page)],
                                capture_output=True, timeout=20)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("PDF preview timed out. Download the original file instead.") from exc
    if result.returncode:
        raise ValueError("Could not render this PDF page. Download the original file instead.")
    metadata, data = result.stdout.split(b"\n", 1)
    return data, json.loads(metadata)["pages"]


def _main():
    if sys.platform != "win32":
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
    import pypdfium2 as pdfium

    with pdfium.PdfDocument(sys.argv[1]) as document:
        page_number = int(sys.argv[2])
        if not 0 <= page_number < len(document):
            raise ValueError("Page is outside this document")
        page = document[page_number]
        width, height = page.get_size()
        bitmap = page.render(scale=min(1.5, 1600 / max(width, height)))
        buffer = io.BytesIO()
        bitmap.to_pil().save(buffer, format="PNG")
        sys.stdout.buffer.write(json.dumps({"pages": len(document)}).encode() + b"\n" + buffer.getvalue())
        bitmap.close()
        page.close()


if __name__ == "__main__":
    _main()
