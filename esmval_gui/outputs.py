"""Bounded browsing of one registered ARE job's output directory."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import stat

from . import remote


MAX_FILES = 1000
MAX_ENTRIES = 10000
MAX_PREVIEW_BYTES = 25 * 1024 * 1024
MAX_TEXT_BYTES = 200_000
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml"}
TEXT_SUFFIXES = {".txt", ".log", ".yml", ".yaml", ".json", ".csv", ".tsv", ".xml", ".ncl", ".r", ".py"}


def root_for(record: dict) -> Path:
    if remote.execution_mode() != "local" or record.get("transport") != "local":
        raise ValueError("Output previews are available for jobs submitted from ARE.")
    run = Path(record["remote_dir"])
    root = run / "output"
    if not run.is_absolute() or root.is_symlink():
        raise ValueError("Invalid output directory")
    return root.resolve()


def kind_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_TYPES:
        return "image"
    if suffix == ".pdf":
        return "pdf"
    if suffix in TEXT_SUFFIXES:
        return "text"
    return "data"


def list_files(record: dict) -> dict:
    try:
        root = root_for(record)
    except ValueError as exc:
        return {"available": False, "files": [], "detail": str(exc)}
    result = {"available": True, "root": str(root), "files": [], "truncated": False}
    if not root.exists():
        result["detail"] = "No outputs yet. Refresh after the job starts writing results."
        return result
    stack = [(root, 0)]
    visited = 0
    while stack:
        directory, depth = stack.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    visited += 1
                    if visited > MAX_ENTRIES or len(result["files"]) >= MAX_FILES:
                        result["truncated"] = True
                        stack.clear()
                        break
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if depth < 12:
                            stack.append((Path(entry.path), depth + 1))
                        else:
                            result["truncated"] = True
                    elif entry.is_file(follow_symlinks=False):
                        path = Path(entry.path)
                        info = entry.stat(follow_symlinks=False)
                        result["files"].append({"path": path.relative_to(root).as_posix(),
                                                "name": path.name, "size": info.st_size,
                                                "kind": kind_for(path), "modified": info.st_mtime})
        except OSError as exc:
            result["detail"] = f"Some outputs could not be read: {exc.strerror}. Check ARE Storage mounts and permissions."
    result["files"].sort(key=lambda item: (item["kind"] not in ("image", "pdf"), item["path"]))
    return result


def resolve_file(record: dict, relative: str) -> Path:
    root = root_for(record)
    parts = PurePosixPath(relative).parts
    if not parts or relative.startswith("/") or "\\" in relative or "\0" in relative or any(part in (".", "..") for part in parts):
        raise ValueError("Choose a file inside this run's output directory")
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("Symbolic links cannot be opened as run outputs")
    if not path.resolve().is_relative_to(root) or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Choose a regular output file")
    return path
