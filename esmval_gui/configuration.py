"""Read and explain layered ESMValTool YAML configuration files."""

from __future__ import annotations

import json
import math
import re
import shlex
from datetime import date, datetime

import yaml

from . import remote


MAX_FILES = 50
MAX_FILE_CHARS = 1_000_000
MAX_TOTAL_CHARS = 3_000_000


def _json_value(value, *, depth: int = 0, ancestors: frozenset[int] = frozenset()):
    if depth > 40:
        raise ValueError("Configuration nesting is too deep")
    if isinstance(value, (dict, list)):
        if id(value) in ancestors:
            raise ValueError("Recursive YAML aliases are unsupported")
        ancestors = ancestors | {id(value)}
    if isinstance(value, dict):
        return {str(key): _json_value(child, depth=depth + 1, ancestors=ancestors)
                for key, child in value.items()}
    if isinstance(value, list):
        return [_json_value(child, depth=depth + 1, ancestors=ancestors) for child in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite YAML numbers are unsupported")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError(f"Unsupported YAML value type: {type(value).__name__}")


def _mark_sources(value, path: tuple[str, ...], name: str, origins: dict) -> None:
    if isinstance(value, dict) and value:
        for key, child in value.items():
            _mark_sources(child, (*path, key), name, origins)
    else:
        origins[path] = name


def _merge(target: dict, incoming: dict, path: tuple[str, ...], name: str,
           origins: dict, overrides: list) -> None:
    for key, value in incoming.items():
        current = (*path, key)
        old = target.get(key)
        if isinstance(old, dict) and isinstance(value, dict):
            _merge(old, value, current, name, origins, overrides)
            continue
        previous = next((source for origin, source in origins.items()
                         if origin == current or origin[:len(current)] == current), None)
        if key in target and old != value and previous:
            overrides.append({"path": list(current), "previous": previous, "file": name})
        for origin in [origin for origin in origins if origin[:len(current)] == current]:
            del origins[origin]
        target[key] = value
        _mark_sources(value, current, name, origins)


def inspect_files(files: list[dict[str, str]]) -> dict:
    """Merge files in supplied priority order, then report values and sources."""
    if not files or len(files) > MAX_FILES:
        raise ValueError(f"Choose 1 to {MAX_FILES} YAML files")
    if sum(len(item.get("content", "")) for item in files) > MAX_TOTAL_CHARS:
        raise ValueError("Configuration files exceed the 3 MB combined limit")
    merged: dict = {}
    origins: dict[tuple[str, ...], str] = {}
    overrides: list[dict] = []
    metadata = []
    names = set()
    for item in files:
        name, content = item.get("name", ""), item.get("content", "")
        if not isinstance(name, str) or not name or len(name) > 300 or name in names:
            raise ValueError("Give each configuration file a distinct name")
        if not isinstance(content, str) or len(content) > MAX_FILE_CHARS:
            raise ValueError(f"{name}: file exceeds the 1 MB limit")
        names.add(name)
        try:
            parsed = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise ValueError(f"{name}: invalid YAML: {exc}") from exc
        if parsed is None:
            parsed = {}
        if not isinstance(parsed, dict):
            raise ValueError(f"{name}: expected a mapping of configuration options")
        parsed = _json_value(parsed)
        _merge(merged, parsed, (), name, origins, overrides)
        metadata.append({"name": name, "sections": list(parsed), "size": len(content)})
    projects = merged.get("projects")
    projects = projects if isinstance(projects, dict) else {}
    data_sources = sum(len(project.get("data", {})) for project in projects.values()
                       if isinstance(project, dict) and isinstance(project.get("data"), dict))
    return {
        "files": metadata,
        "effective": merged,
        "origins": [{"path": list(path), "file": name} for path, name in origins.items()],
        "overrides": overrides,
        "counts": {"files": len(files), "sections": len(merged),
                   "projects": len(projects), "data_sources": data_sources},
    }


_REMOTE_SCRIPT = r'''
import json, os, pathlib, sys
given = sys.argv[1]
path = pathlib.Path(given or os.environ.get("ESMVALTOOL_CONFIG_DIR") or pathlib.Path.home() / ".config" / "esmvaltool").expanduser()
if not path.exists() and not given:
    path = pathlib.Path.home() / ".esmvaltool" / "config-user.yml"
if path.is_file():
    files = [path] if path.suffix.lower() in (".yml", ".yaml") else []
elif path.is_dir():
    files = sorted((p for p in path.iterdir() if p.is_file() and p.suffix.lower() in (".yml", ".yaml")), key=lambda p: p.name)
else:
    raise SystemExit("Configuration path does not exist: " + str(path))
if not files:
    raise SystemExit("No YAML configuration files found in " + str(path))
if len(files) > 50 or any(p.stat().st_size > 1000000 for p in files) or sum(p.stat().st_size for p in files) > 3000000:
    raise SystemExit("Configuration exceeds the file count or size limit")
print(json.dumps([{"name": str(p), "content": p.read_text(encoding="utf-8")} for p in files]))
'''.strip()


def load_remote_files(host: str, path: str = "") -> list[dict[str, str]]:
    """Read a single file or one non-recursive config directory over SSH."""
    if not remote.HOST_RE.fullmatch(host) or host.startswith("-"):
        raise ValueError("Enter a valid SSH host or alias")
    if path and (not path.startswith("/") or ".." in path.split("/")
                 or not re.fullmatch(r"[A-Za-z0-9_./-]+", path)):
        raise ValueError("Enter an absolute configuration path on Gadi")
    command = f"python3 -c {shlex.quote(_REMOTE_SCRIPT)} {shlex.quote(path)}"
    try:
        payload = json.loads(remote.ssh(host, command, timeout=45))
    except (json.JSONDecodeError, RuntimeError, TimeoutError) as exc:
        raise ValueError(f"Could not read Gadi configuration: {exc}") from exc
    if not isinstance(payload, list):
        raise ValueError("Gadi returned an unexpected configuration listing")
    return payload
