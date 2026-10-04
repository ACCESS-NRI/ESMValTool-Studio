"""Read the public ESMValCore preprocessor API without importing heavy dependencies."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
from functools import lru_cache
from pathlib import Path


SNAPSHOT = Path(__file__).with_name("preprocessor_catalogue.json")
MODULE_LABELS = {
    "area": "Area and regions", "compare_with_refs": "Reference comparison",
    "cycles": "Cycles", "derive": "Derived variables", "detrend": "Trends",
    "mask": "Masks", "multimodel": "Multi-model", "other": "Other",
    "regrid": "Grid and points", "rolling_window": "Rolling windows",
    "time": "Time", "trend": "Trends", "units": "Units",
    "volume": "Volume and levels", "weighting": "Weights",
}


def source_root() -> Path | None:
    candidates = []
    if os.environ.get("ESMVAL_GUI_CORE_ROOT"):
        candidates.append(Path(os.environ["ESMVAL_GUI_CORE_ROOT"]).expanduser())
    try:
        spec = importlib.util.find_spec("esmvalcore")
        if spec and spec.submodule_search_locations:
            candidates.append(Path(next(iter(spec.submodule_search_locations))))
    except (ImportError, ValueError):
        pass
    candidates.append(Path(__file__).resolve().parents[2] / "ESMValCore" / "esmvalcore")
    for candidate in candidates:
        root = candidate / "preprocessor" if candidate.name == "esmvalcore" else candidate
        if (root / "__init__.py").is_file():
            return root.resolve()
    return None


def _parameter_docs(doc: str) -> dict[str, str]:
    lines = doc.splitlines()
    result: dict[str, str] = {}
    in_parameters = False
    current = None
    for index, line in enumerate(lines):
        if line.strip() == "Parameters" and index + 1 < len(lines) and set(lines[index + 1].strip()) == {"-"}:
            in_parameters = True
            continue
        if not in_parameters:
            continue
        if line.strip() and set(line.strip()) == {"-"}:
            continue
        if line and not line.startswith(" "):
            if index + 1 < len(lines) and lines[index + 1].strip().startswith("---"):
                break
            current = line.split(":", 1)[0].strip().lstrip("*")
            result[current] = ""
        elif current and line.strip() and set(line.strip()) != {"-"}:
            result[current] += " " + line.strip()
    return {key: _clean_description(value) for key, value in result.items()}


def _clean_description(value: str) -> str:
    value = re.sub(r":[a-zA-Z_]+:`([^`<>]+) <[^`<>]+>`", r"\1", value)
    value = re.sub(r":[a-zA-Z_]+:`~?([^`]+)`", lambda match: match.group(1).split(".")[-1], value)
    value = value.replace("``", "'").replace("`", "")
    return re.sub(r"\s+", " ", value).strip()


def _default(node: ast.expr | None):
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return ast.unparse(node)


def _parameters(function: ast.FunctionDef, doc: str) -> list[dict]:
    args = function.args
    normal = args.posonlyargs + args.args
    defaults = [None] * (len(normal) - len(args.defaults)) + list(args.defaults)
    docs = _parameter_docs(doc)
    result = []
    for arg, default in zip(normal, defaults):
        if arg.arg in ("cube", "cubes", "products", "product", "dataset", "datasets", "session", "metadata"):
            continue
        result.append({"name": arg.arg, "type": ast.unparse(arg.annotation) if arg.annotation else "",
                       "required": default is None, "default": _default(default), "description": docs.get(arg.arg, "")})
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        result.append({"name": arg.arg, "type": ast.unparse(arg.annotation) if arg.annotation else "",
                       "required": default is None, "default": _default(default), "description": docs.get(arg.arg, "")})
    return result


def build_catalogue(root: Path) -> dict:
    entry = ast.parse((root / "__init__.py").read_text(encoding="utf-8"))
    order = next([node.value for node in assignment.value.elts]
                 for assignment in entry.body if isinstance(assignment, ast.Assign)
                 for target in assignment.targets if isinstance(target, ast.Name) and target.id == "__all__")
    imports = {}
    for statement in entry.body:
        if isinstance(statement, ast.ImportFrom) and statement.module and statement.module.startswith("esmvalcore.preprocessor."):
            module = statement.module.rsplit(".", 1)[-1]
            imports.update({alias.asname or alias.name: module for alias in statement.names})
    functions = {}
    for module in set(imports.values()):
        path = root / f"{module}.py"
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions[module] = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    initial_end = order.index("add_supplementary_variables") + 1
    final_start = order.index("remove_supplementary_variables")
    bricks = []
    for position, name in enumerate(order[initial_end:final_start], initial_end):
        module = imports.get(name)
        function = functions.get(module, {}).get(name)
        doc = ast.get_docstring(function) if function else ""
        summary = re.split(r"\n\s*\n", doc or "", maxsplit=1)[0].replace("\n", " ").strip()
        bricks.append({"name": name, "category": MODULE_LABELS.get((module or "").lstrip("_"), "Other"),
                       "summary": summary or name.replace("_", " ").capitalize(),
                       "documentation": doc or "Documentation unavailable in this source checkout.",
                       "parameters": _parameters(function, doc or "") if function else [],
                       "order": position})
    return {"source": str(root), "bricks": bricks, "order": order,
            "initial_steps": order[:initial_end], "final_steps": order[final_start:]}


@lru_cache(maxsize=1)
def get_catalogue() -> dict:
    root = source_root()
    if root:
        return build_catalogue(root)
    if SNAPSHOT.is_file():
        snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        snapshot["source"] = "Bundled ESMValCore catalogue"
        return snapshot
    return {"source": None, "bricks": [], "order": [], "initial_steps": [], "final_steps": []}
