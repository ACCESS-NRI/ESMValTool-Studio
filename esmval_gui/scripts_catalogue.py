"""A small, local catalogue of diagnostic scripts used by shipped recipes."""

from __future__ import annotations

import ast
from collections import Counter
from functools import lru_cache
from pathlib import Path

import yaml

from .recipes import list_recipes


@lru_cache(maxsize=2)
def catalogue(root: Path | None) -> list[dict]:
    if root is None:
        return []
    scripts_root = root.parent / "diag_scripts"
    if not scripts_root.is_dir():
        return []
    usage: Counter[str] = Counter()
    for entry in list_recipes(root):
        try:
            recipe = yaml.safe_load((root / entry["path"]).read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        diagnostics = recipe.get("diagnostics") or {}
        if not isinstance(diagnostics, dict):
            continue
        for diagnostic in diagnostics.values():
            if not isinstance(diagnostic, dict) or not isinstance(diagnostic.get("scripts"), dict):
                continue
            for script in diagnostic["scripts"].values():
                if isinstance(script, dict) and isinstance(script.get("script"), str):
                    usage[script["script"]] += 1
    output = []
    for name, count in usage.items():
        path = (scripts_root / name).resolve()
        if not path.is_relative_to(scripts_root) or not path.is_file():
            continue
        summary = ""
        if path.suffix == ".py" and path.stat().st_size < 200_000:
            try:
                summary = (ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or "").strip().split("\n\n", 1)[0]
                summary = " ".join(summary.split())[:220]
            except (OSError, SyntaxError, UnicodeError):
                pass
        output.append({"path": name, "summary": summary, "used_by": count})
    return sorted(output, key=lambda item: (-item["used_by"], item["path"]))


def search(root: Path | None, query: str = "") -> dict:
    items = catalogue(root)
    needle = query.casefold().strip()
    matching = [item for item in items if needle in item["path"].casefold() or needle in item["summary"].casefold()]
    return {"scripts": matching[:40], "total": len(matching), "source": str(root.parent / "diag_scripts") if root else None}
