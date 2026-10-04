from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import copy
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
import yaml as pyyaml


MAX_RECIPE_BYTES = 2_000_000


def yaml_reader() -> YAML:
    parser = YAML(typ="rt")
    parser.preserve_quotes = True
    parser.allow_duplicate_keys = False
    parser.indent(mapping=2, sequence=4, offset=2)
    parser.width = 1000
    return parser


def parse_recipe(source: str):
    if len(source.encode("utf-8")) > MAX_RECIPE_BYTES:
        raise ValueError("Recipe exceeds the 2 MB editor limit")
    try:
        data = yaml_reader().load(source)
    except Exception as exc:
        if "found duplicate" not in str(exc):
            raise ValueError(str(exc)) from exc
        # Some upstream recipes repeat merge or ordinary keys. PyYAML accepts this
        # syntax; keep the original text intact for saving and submission.
        try:
            data = pyyaml.safe_load(source)
        except Exception as fallback_exc:
            raise ValueError(str(fallback_exc)) from fallback_exc
    if not isinstance(data, dict):
        raise ValueError("A recipe must be a YAML mapping")
    return data


def plain(value):
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [plain(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


@lru_cache(maxsize=4)
def _reference_catalogue(root: Path | None) -> dict:
    if root is None:
        return {}
    path = root.parent / "config-references.yml"
    if not path.is_file():
        return {}
    try:
        return pyyaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, pyyaml.YAMLError):
        return {}


def _people(values, catalogue: dict) -> list[dict]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    people = []
    for value in values:
        key = str(value)
        record = catalogue.get("authors", {}).get(key, {})
        people.append({"id": key, "name": record.get("name") or key.replace("_", " "),
                       "institute": record.get("institute") or "",
                       "orcid": record.get("orcid") if str(record.get("orcid", "")).startswith("https://orcid.org/") else ""})
    return people


def _references(values, root: Path | None) -> list[dict]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    output = []
    references_dir = (root.parent / "references").resolve() if root else None
    for value in values:
        key = str(value)
        title, url = key.replace("_", " "), ""
        path = (references_dir / f"{key}.bibtex").resolve() if references_dir and re.fullmatch(r"[A-Za-z0-9_-]+", key) else None
        if path and path.is_relative_to(references_dir) and path.is_file():
            bib = path.read_text(encoding="utf-8")
            match = re.search(r"(?im)^\s*title\s*=\s*([\{\"])(.+?)\s*[\}\"]\s*,?\s*$", bib)
            if match:
                title = re.sub(r"[{}]", "", match.group(2)).strip()
            doi = re.search(r"(?im)^\s*doi\s*=\s*[\{\"]([^}\"]+)", bib)
            link = re.search(r"(?im)^\s*url\s*=\s*[\{\"]([^}\"]+)", bib)
            if doi:
                url = "https://doi.org/" + doi.group(1)
            elif link and link.group(1).startswith("https://"):
                url = link.group(1)
        output.append({"id": key, "title": title, "url": url})
    return output


def summarize(source: str, metadata_root: Path | None = None) -> dict:
    recipe = parse_recipe(source)
    messages = []
    for key in ("documentation", "diagnostics"):
        if not isinstance(recipe.get(key), dict):
            messages.append(f"Missing or invalid '{key}' section")
    profiles = recipe.get("preprocessors") or {}
    diagnostics = recipe.get("diagnostics") or {}
    datasets = recipe.get("datasets") or []
    if not isinstance(profiles, dict):
        messages.append("'preprocessors' must be a mapping")
        profiles = {}
    if not isinstance(diagnostics, dict):
        diagnostics = {}
    if not isinstance(datasets, list):
        messages.append("'datasets' must be a list")
        datasets = []
    graph = {"datasets": [], "profiles": [], "variables": [], "diagnostics": []}
    dataset_groups = {}

    def add_dataset(dataset, scope: str, path: list) -> str:
        details = plain(dataset) if isinstance(dataset, dict) else {"value": plain(dataset)}
        occurrence = {"path": path, "scope": scope,
                      "summary": " · ".join(str(details[key]) for key in ("exp", "ensemble", "mip") if details.get(key))}
        if isinstance(dataset, dict) and dataset.get("dataset"):
            group_key = ("model", str(dataset.get("project", "")), str(dataset["dataset"]))
        else:
            group_key = ("definition", json.dumps(details, sort_keys=True, default=str))
        if group_key in dataset_groups:
            item = graph["datasets"][dataset_groups[group_key]]
            item["definition_count"] += 1
            item["occurrences"].append(occurrence)
            if scope not in item["scopes"] and len(item["scopes"]) < 5:
                item["scopes"].append(scope)
            return item["id"]
        index = len(graph["datasets"])
        dataset_groups[group_key] = index
        graph["datasets"].append({
            "id": f"dataset-{index}",
            "label": str(dataset.get("dataset", f"Dataset {index + 1}")) if isinstance(dataset, dict) else str(dataset),
            "detail": str(dataset.get("project", "")) if isinstance(dataset, dict) else "",
            "scope": scope, "scopes": [scope], "settings": details, "definition_count": 1,
            "occurrences": [occurrence],
        })
        return f"dataset-{index}"

    global_dataset_ids = [add_dataset(dataset, "Recipe", ["datasets", index])
                          for index, dataset in enumerate(datasets)]
    for name, steps in profiles.items():
        if not isinstance(steps, dict):
            messages.append(f"Preprocessor '{name}' must be a mapping")
            continue
        graph["profiles"].append({
            "id": str(name), "label": str(name),
            "steps": [{"name": str(step), "parameters": plain(value)} for step, value in steps.items() if step != "custom_order"],
            "custom_order": bool(steps.get("custom_order", False)),
        })
    for name, diagnostic in diagnostics.items():
        if not isinstance(diagnostic, dict):
            messages.append(f"Diagnostic '{name}' must be a mapping")
            continue
        variables = diagnostic.get("variables") or {}
        scripts = diagnostic.get("scripts") or {}
        diagnostic_datasets = diagnostic.get("additional_datasets") or []
        if not isinstance(diagnostic_datasets, list):
            messages.append(f"Diagnostic '{name}' additional_datasets must be a list")
            diagnostic_datasets = []
        diagnostic_dataset_ids = [add_dataset(dataset, f"Diagnostic {name}",
                                              ["diagnostics", str(name), "additional_datasets", index])
                                  for index, dataset in enumerate(diagnostic_datasets)]
        variable_items = []
        if isinstance(variables, dict):
            for var_name, variable in variables.items():
                profile = variable.get("preprocessor") if isinstance(variable, dict) else None
                short_name = variable.get("short_name") if isinstance(variable, dict) else None
                variable_label = str(short_name or var_name)
                variable_datasets = (variable.get("additional_datasets") or []) if isinstance(variable, dict) else []
                if not isinstance(variable_datasets, list):
                    messages.append(f"{name}/{var_name} additional_datasets must be a list")
                    variable_datasets = []
                variable_dataset_ids = [add_dataset(dataset, f"Variable {name}/{var_name}",
                                                    ["diagnostics", str(name), "variables", str(var_name),
                                                     "additional_datasets", index])
                                        for index, dataset in enumerate(variable_datasets)]
                dataset_ids = list(dict.fromkeys(global_dataset_ids + diagnostic_dataset_ids + variable_dataset_ids))
                if profile and profile != "default" and profile not in profiles:
                    messages.append(f"{name}/{var_name} refers to missing preprocessor '{profile}'")
                variable_items.append({
                    "name": str(var_name), "short_name": variable_label,
                    "profile": str(profile or "default"),
                })
                graph["variables"].append({
                    "id": f"{name}/{var_name}", "name": str(var_name),
                    "short_name": variable_label, "diagnostic": str(name),
                    "profile": str(profile or "default"),
                    "dataset_ids": dataset_ids,
                    "detail": plain(variable) if isinstance(variable, dict) else {},
                })
        else:
            messages.append(f"Diagnostic '{name}' variables must be a mapping")
        script_items = []
        if isinstance(scripts, dict):
            for script_name, script in scripts.items():
                path = script.get("script") if isinstance(script, dict) else ""
                script_items.append({"name": str(script_name), "path": str(path or "")})
        graph["diagnostics"].append({
            "id": str(name), "label": str(name),
            "variables": variable_items, "scripts": script_items,
            "ancestors": [str(x) for x in diagnostic.get("ancestors", [])] if isinstance(diagnostic.get("ancestors", []), list) else [],
        })
    documentation = recipe.get("documentation") or {}
    documentation = documentation if isinstance(documentation, dict) else {}
    metadata_root = metadata_root or available_recipe_root()
    catalogue = _reference_catalogue(metadata_root)
    return {
        "title": str(documentation.get("title", "Untitled recipe")),
        "description": str(documentation.get("description", "")),
        "documentation": {
            "authors": _people(documentation.get("authors"), catalogue),
            "maintainers": _people(documentation.get("maintainer"), catalogue),
            "references": _references(documentation.get("references"), metadata_root),
            "projects": [{"id": str(key), "name": catalogue.get("projects", {}).get(str(key), str(key))}
                         for key in (documentation.get("projects") or [])] if isinstance(documentation.get("projects"), list) else [],
        },
        "graph": graph, "messages": messages,
        "counts": {key: len(graph[key]) for key in graph},
    }


def available_recipe_root() -> Path | None:
    configured = os.environ.get("ESMVAL_GUI_RECIPE_ROOT")
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())
    try:
        spec = importlib.util.find_spec("esmvaltool")
        if spec and spec.submodule_search_locations:
            candidates.append(Path(next(iter(spec.submodule_search_locations))) / "recipes")
    except (ImportError, ValueError):
        pass
    candidates.append(Path(__file__).resolve().parents[2] / "ESMValTool" / "ESMValTool" / "esmvaltool" / "recipes")
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    return None


@lru_cache(maxsize=512)
def _declared_realms(path: Path, mtime_ns: int, size: int) -> tuple[str, ...]:
    """Read the realm tags actually declared by a recipe's diagnostics."""
    if size > MAX_RECIPE_BYTES:
        return ()
    try:
        recipe = pyyaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, pyyaml.YAMLError):
        return ()
    diagnostics = recipe.get("diagnostics") if isinstance(recipe, dict) else None
    if not isinstance(diagnostics, dict):
        return ()
    realms = set()
    for diagnostic in diagnostics.values():
        if not isinstance(diagnostic, dict):
            continue
        values = diagnostic.get("realms") or []
        if isinstance(values, str):
            values = [values]
        if isinstance(values, list):
            realms.update(value.strip() for value in values if isinstance(value, str) and value.strip())
    return tuple(sorted(realms, key=str.casefold))


def list_recipes(root: Path, query: str = "") -> list[dict]:
    query = query.casefold().strip()
    items = []
    for path in sorted(root.rglob("recipe_*.yml")):
        relative = str(path.relative_to(root))
        if query not in relative.casefold():
            continue
        stat = path.stat()
        items.append({"path": relative, "name": path.name,
                      "group": str(path.parent.relative_to(root)),
                      "realms": list(_declared_realms(path, stat.st_mtime_ns, stat.st_size))})
    return items


def read_library_recipe(root: Path, relative: str) -> str:
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or not target.is_file() or target.suffix not in (".yml", ".yaml"):
        raise FileNotFoundError("Recipe not found in library")
    if target.stat().st_size > MAX_RECIPE_BYTES:
        raise ValueError("Recipe exceeds the 2 MB editor limit")
    return target.read_text(encoding="utf-8")


def documentation_root(root: Path) -> Path | None:
    configured = os.environ.get("ESMVAL_GUI_DOCS_ROOT")
    candidates = [Path(configured).expanduser()] if configured else []
    candidates.append(root.parent.parent / "doc" / "sphinx" / "source")
    return next((path.resolve() for path in candidates if path.is_dir()), None)


@lru_cache(maxsize=4)
def _documentation_pages(docs_root: Path) -> list[tuple[Path, str]]:
    pages = []
    for path in sorted((docs_root / "recipes").rglob("*.rst")):
        if path.name in {"index.rst", "legacy_recipe_list.rst", "broken_recipe_list.rst"}:
            continue
        try:
            pages.append((path, path.read_text(encoding="utf-8")))
        except OSError:
            continue
    return pages


def _figure_from_page(docs_root: Path, page: Path, content: str, recipe_name: str) -> dict | None:
    figures = []
    for match in re.finditer(r"(?m)^\.\. (?:figure|image)::\s+(\S+)", content):
        asset = match.group(1)
        if not asset.startswith("/recipes/figures/"):
            continue
        target = (docs_root / asset.lstrip("/")).resolve()
        if not target.is_relative_to(docs_root / "recipes" / "figures") or not target.is_file():
            continue
        if target.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".svg"}:
            continue
        next_directive = re.search(r"(?m)^\.\. ", content[match.end():])
        block = content[match.end():match.end() + next_directive.start()] if next_directive else content[match.end():]
        caption_lines = []
        started = False
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped:
                if started:
                    break
                continue
            if stripped.startswith(":") or stripped.startswith(".."):
                continue
            started = True
            caption_lines.append(stripped)
        caption = " ".join(caption_lines)
        figures.append({"path": str(target.relative_to(docs_root)), "caption": caption,
                        "match": recipe_name in (asset + " " + block).lower()})
    if not figures:
        return None
    examples = {
        "recipe_python.yml": "timeseries.png", "recipe_easy_ipcc.yml": "IPCC_AR6_figure_9.3a_1850-2100.png",
        "recipe_extract_shape.yml": "elbe.png", "recipe_decadal.yml": "decadal_first_example.png",
    }
    preferred = examples.get(recipe_name)
    figure = next((item for item in figures if Path(item["path"]).name == preferred), None)
    figure = figure or next((item for item in figures if item["match"]), figures[0])
    return {"url": "/api/docs-figure/" + figure["path"], "caption": figure["caption"],
            "scope": "recipe" if page.stem == Path(recipe_name).stem or preferred else "documentation"}


def _overview_from_page(content: str, recipe_name: str, exact: bool) -> str:
    heading = re.search(r"(?im)^Overview\s*\n[-=]{3,}\s*\n", content)
    if heading is None:
        return ""
    remainder = content[heading.end():]
    next_heading = re.search(r"(?m)^[^\n]+\n[-=]{3,}\s*\n", remainder)
    section = remainder[:next_heading.start()] if next_heading else remainder
    paragraphs = [re.sub(r"\s+", " ", paragraph).strip()
                  for paragraph in re.split(r"\n\s*\n", section)
                  if paragraph.strip() and not paragraph.lstrip().startswith(("*", "..", "#", ":"))]
    if not paragraphs:
        return ""
    paragraph = paragraphs[0]
    if not exact:
        paragraph = next((item for item in paragraphs if recipe_name.lower() in item.lower()), paragraph)
    paragraph = re.sub(r"`([^`<>]+)\s*<https?://[^>]+>`__?", r"\1", paragraph)
    paragraph = re.sub(r"``([^`]+)``", r"\1", paragraph)
    paragraph = re.sub(r"\[([^]]+)\]\(https?://[^)]+\)", r"\1", paragraph)
    return re.sub(r"\s+", " ", paragraph).strip()


def library_recipe_info(root: Path, relative: str) -> dict:
    # Validate the source path before using it in links or documentation lookup.
    read_library_recipe(root, relative)
    name = Path(relative).name.lower()
    info = {"source_path": "esmvaltool/recipes/" + relative,
            "source_url": "https://github.com/ESMValGroup/ESMValTool/blob/main/esmvaltool/recipes/" + "/".join(quote(part, safe="") for part in Path(relative).parts),
            "docs_url": "", "overview": "", "figure": None}
    docs_root = documentation_root(root)
    if docs_root is None:
        return info
    pages = _documentation_pages(docs_root)
    exact = next(((path, content) for path, content in pages if path.stem == Path(relative).stem), None)
    canonical_name = re.sub(r"[^a-z0-9]", "", name)
    page = exact or next(((path, content) for path, content in pages
                          if re.search(r"(?<![\w.-])" + re.escape(name) + r"(?![\w.-])", content.lower())), None)
    page = page or next(((path, content) for path, content in pages
                         if canonical_name in re.sub(r"[^a-z0-9]", "", content.lower())), None)
    if page is None:
        return info
    path, content = page
    info["docs_url"] = "https://docs.esmvaltool.org/en/latest/" + str(path.relative_to(docs_root).with_suffix(".html"))
    info["overview"] = _overview_from_page(content, name, exact is not None)
    info["figure"] = _figure_from_page(docs_root, path, content, name)
    return info


def edit_profile(source: str, name: str, step: str, parameters: str | None, remove: bool = False) -> str:
    parser, recipe = _editable(source)
    profiles = recipe.get("preprocessors")
    if not isinstance(profiles, dict) or name not in profiles or not isinstance(profiles[name], dict):
        raise ValueError("Preprocessor profile not found")
    if remove:
        profiles[name].pop(step, None)
    else:
        if not step or any(ch in step for ch in "\r\n"):
            raise ValueError("Enter a valid step name")
        if not parameters or not parameters.strip():
            value = {}
        else:
            try:
                value = parser.load(parameters)
            except Exception as exc:
                raise ValueError(f"Invalid step parameters: {exc}") from exc
        profiles[name][step] = value
    return _dump_edit(parser, recipe, source)


def _editable(source: str):
    parser = yaml_reader()
    try:
        recipe = parser.load(source)
    except Exception as exc:
        raise ValueError("This recipe uses duplicate YAML keys. Edit its YAML directly to preserve them.") from exc
    if not isinstance(recipe, dict):
        raise ValueError("A recipe must be a YAML mapping")
    return parser, recipe


def _dump_edit(parser: YAML, recipe: dict, source: str) -> str:
    stream = io.StringIO()
    parser.dump(recipe, stream)
    output = stream.getvalue()
    first_key = re.search(r"(?m)^[A-Za-z_][A-Za-z0-9_-]*:\s*", source)
    if first_key:
        header = source[:first_key.start()]
        if header and not output.startswith(header):
            output = header + output
    return output


def _node_parent(recipe: dict, kind: str, path: list):
    dataset_path = (len(path) == 2 and path[0] == "datasets" and type(path[1]) is int) or (
        len(path) == 4 and path[0] == "diagnostics" and isinstance(path[1], str)
        and path[2] == "additional_datasets" and type(path[3]) is int) or (
        len(path) == 6 and path[0] == "diagnostics" and isinstance(path[1], str)
        and path[2] == "variables" and isinstance(path[3], str)
        and path[4] == "additional_datasets" and type(path[5]) is int)
    valid = (
        kind == "dataset" and dataset_path
        or kind == "variable" and len(path) == 4 and path[0] == "diagnostics"
        and isinstance(path[1], str) and path[2] == "variables" and isinstance(path[3], str)
        or kind == "diagnostic" and len(path) == 2 and path[0] == "diagnostics"
        and isinstance(path[1], str)
        or kind == "profile" and len(path) == 2 and path[0] == "preprocessors"
        and isinstance(path[1], str)
    )
    if not valid:
        raise ValueError("Invalid recipe node path")
    current = recipe
    for part in path[:-1]:
        if isinstance(current, dict) and isinstance(part, str) and part in current:
            current = current[part]
        elif isinstance(current, list) and type(part) is int and 0 <= part < len(current):
            current = current[part]
        else:
            raise ValueError("Recipe node no longer exists")
    key = path[-1]
    if isinstance(current, dict) and isinstance(key, str) and key in current:
        return current, key
    if isinstance(current, list) and type(key) is int and 0 <= key < len(current):
        return current, key
    raise ValueError("Recipe node no longer exists")


def read_node(source: str, kind: str, path: list) -> dict:
    parser, recipe = _editable(source)
    parent, key = _node_parent(recipe, kind, path)
    stream = io.StringIO()
    parser.dump(parent[key], stream)
    return {"definition": stream.getvalue(), "name": str(key) if kind != "dataset" else "",
            "settings": plain(parent[key])}


def _detach_anchored_containers(recipe: dict, path: list) -> None:
    current = recipe
    for part in path[:-1]:
        child = current[part]
        if getattr(getattr(child, "anchor", None), "value", None):
            child = copy.deepcopy(child)
            child.yaml_set_anchor(None)
            current[part] = child
        current = child


def edit_node(source: str, kind: str, path: list, definition: str, name: str | None = None) -> tuple[str, list]:
    parser, recipe = _editable(source)
    parent, key = _node_parent(recipe, kind, path)
    try:
        replacement = parser.load(definition)
    except Exception as exc:
        raise ValueError(f"Invalid definition YAML: {exc}") from exc
    if not isinstance(replacement, dict):
        raise ValueError("The definition must be a YAML mapping")
    if kind == "dataset":
        _detach_anchored_containers(recipe, path)
        parent, key = _node_parent(recipe, kind, path)
    new_key = key
    if kind != "dataset" and name is not None and name != key:
        if not name.strip() or name != name.strip() or any(ch in name for ch in "\r\n"):
            raise ValueError("Enter a valid name")
        if kind == "profile" and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name):
            raise ValueError("Use letters, numbers, underscores or hyphens for the profile name")
        if name in parent:
            raise ValueError("That name is already in use")
        new_key = name
    if new_key == key:
        parent[key] = replacement
    else:
        parent.insert(list(parent).index(key), new_key, replacement)
        del parent[key]
        if kind == "profile":
            for diagnostic in (recipe.get("diagnostics") or {}).values():
                if isinstance(diagnostic, dict):
                    for variable in (diagnostic.get("variables") or {}).values():
                        if isinstance(variable, dict) and variable.get("preprocessor") == key:
                            variable["preprocessor"] = new_key
        if kind == "diagnostic":
            for diagnostic in (recipe.get("diagnostics") or {}).values():
                if isinstance(diagnostic, dict) and isinstance(diagnostic.get("ancestors"), list):
                    diagnostic["ancestors"] = [
                        new_key + ancestor[len(key):] if isinstance(ancestor, str)
                        and (ancestor == key or ancestor.startswith(key + "/")) else ancestor
                        for ancestor in diagnostic["ancestors"]
                    ]
    updated_path = [*path[:-1], new_key]
    return _dump_edit(parser, recipe, source), updated_path


def edit_node_fields(source: str, kind: str, path: list, fields: dict[str, str],
                     removed: list[str], name: str | None = None) -> tuple[str, list]:
    parser, recipe = _editable(source)
    parent, key = _node_parent(recipe, kind, path)
    if not isinstance(parent[key], dict):
        raise ValueError("This definition is not a YAML mapping; use the YAML editor")
    replacement = copy.deepcopy(parent[key])
    for field in removed:
        replacement.pop(field, None)
    for field, raw in fields.items():
        if not field or any(ch in field for ch in "\r\n"):
            raise ValueError("Enter a valid setting name")
        if field in replacement and isinstance(replacement[field], (dict, list)):
            raise ValueError(f"Edit nested setting '{field}' in the YAML definition")
        if isinstance(replacement.get(field), str):
            value = raw
        else:
            try:
                value = parser.load(raw if raw.strip() else "''")
            except Exception as exc:
                raise ValueError(f"Invalid value for {field}: {exc}") from exc
        if isinstance(value, (dict, list)):
            raise ValueError(f"Setting '{field}' must be a scalar value")
        replacement[field] = value
    stream = io.StringIO()
    parser.dump(replacement, stream)
    return edit_node(source, kind, path, stream.getvalue(), name)


def create_profile(source: str, name: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name):
        raise ValueError("Use letters, numbers, underscores or hyphens for the profile name")
    parser, recipe = _editable(source)
    profiles = recipe.get("preprocessors")
    if profiles is None:
        profiles = CommentedMap()
        recipe.insert(1 if "documentation" in recipe else 0, "preprocessors", profiles)
    if not isinstance(profiles, dict):
        raise ValueError("'preprocessors' must be a mapping")
    if name in profiles:
        raise ValueError("This preprocessor profile name is already in use")
    profiles[name] = CommentedMap()
    return _dump_edit(parser, recipe, source)


def build_recipe(*, title: str, description: str, author: str, filename: str, dataset: str, project: str,
                 exp: str = "", ensemble: str = "", grid: str = "", diagnostic: str,
                 variable: str, short_name: str = "", mip: str = "Amon", realm: str = "",
                 script_name: str = "", script_path: str = "") -> str:
    """Create a small, editable recipe without borrowing from an existing recipe."""
    title, description, author, filename = title.strip(), description.strip(), author.strip(), filename.strip()
    dataset, project, exp = dataset.strip(), project.strip(), exp.strip()
    ensemble, grid = ensemble.strip(), grid.strip()
    diagnostic, variable, short_name = diagnostic.strip(), variable.strip(), short_name.strip()
    mip, realm, script_name, script_path = mip.strip(), realm.strip(), script_name.strip(), script_path.strip()
    if not title or len(title) > 160 or not description or not author or any(char in author for char in "\r\n"):
        raise ValueError("Enter a title, description and author for the recipe")
    if not re.fullmatch(r"recipe_[A-Za-z0-9_-]+\.ya?ml", filename):
        raise ValueError("Use a filename like recipe_my_analysis.yml")
    if not dataset or len(dataset) > 120 or not project:
        raise ValueError("Enter a dataset name and project")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", diagnostic):
        raise ValueError("Use letters, numbers, underscores or hyphens for the diagnostic name")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", variable):
        raise ValueError("Use letters, numbers, underscores or hyphens for the variable group")
    if short_name and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", short_name):
        raise ValueError("Enter a valid climate variable short name")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", mip):
        raise ValueError("Enter a valid MIP table, such as Amon")
    if realm and realm not in {"atmos", "atmosChem", "land", "ocean", "ocnBgchem", "seaIce"}:
        raise ValueError("Choose a supported realm")
    if script_path and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", script_name or "plot"):
        raise ValueError("Enter a valid script name")
    for value in (dataset, project, exp, ensemble, grid, script_path):
        if any(character in value for character in "\r\n"):
            raise ValueError("Dataset and script settings must be single-line values")

    recipe = CommentedMap()
    recipe["documentation"] = CommentedMap({"title": title, "description": description, "authors": [author]})
    entry = CommentedMap({"dataset": dataset, "project": project})
    for key, value in (("exp", exp), ("ensemble", ensemble), ("grid", grid)):
        if value:
            entry[key] = value
    recipe["datasets"] = [entry]
    recipe["preprocessors"] = CommentedMap()
    variable_entry = CommentedMap()
    if short_name and short_name != variable:
        variable_entry["short_name"] = short_name
    variable_entry["mip"] = mip
    diagnostic_entry = CommentedMap({"description": description})
    if realm:
        diagnostic_entry["realms"] = [realm]
    diagnostic_entry["variables"] = CommentedMap({variable: variable_entry})
    scripts = CommentedMap()
    if script_path:
        scripts[script_name or "plot"] = CommentedMap({"script": script_path})
    diagnostic_entry["scripts"] = scripts
    recipe["diagnostics"] = CommentedMap({diagnostic: diagnostic_entry})
    stream = io.StringIO()
    yaml_reader().dump(recipe, stream)
    return stream.getvalue()


def add_script(source: str, diagnostic: str, name: str, path: str) -> str:
    """Add a diagnostic script while preserving the surrounding recipe YAML."""
    diagnostic, name, path = diagnostic.strip(), name.strip(), path.strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name):
        raise ValueError("Use letters, numbers, underscores or hyphens for the script name")
    if not path or any(character in path for character in "\r\n"):
        raise ValueError("Enter a single-line script path")
    parser, recipe = _editable(source)
    diagnostics = recipe.get("diagnostics")
    target = diagnostics.get(diagnostic) if isinstance(diagnostics, dict) else None
    if not isinstance(target, dict):
        raise ValueError("Select an existing diagnostic")
    scripts = target.get("scripts")
    if scripts is None:
        scripts = CommentedMap()
        target["scripts"] = scripts
    if not isinstance(scripts, dict):
        raise ValueError("This diagnostic's scripts must be a mapping")
    if name in scripts:
        raise ValueError("This script name is already in use in the diagnostic")
    scripts[name] = CommentedMap({"script": path})
    return _dump_edit(parser, recipe, source)


def create_node(source: str, kind: str, name: str, *, diagnostic: str = "", variable: str = "",
                scope: str = "recipe", project: str = "", exp: str = "", ensemble: str = "",
                grid: str = "", mip: str = "", short_name: str = "",
                profile: str = "default", script_name: str = "", script_path: str = "") -> tuple[str, list]:
    name = name.strip()
    if kind not in {"dataset", "profile", "variable", "diagnostic"}:
        raise ValueError("Choose a valid recipe component")
    if kind == "dataset":
        if not name or len(name) > 120 or any(ch in name for ch in "\r\n"):
            raise ValueError("Enter a dataset name")
    elif not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name):
        raise ValueError("Use a name starting with a letter, followed by letters, numbers, underscores or hyphens")
    if kind == "profile":
        return create_profile(source, name), ["preprocessors", name]

    parser, recipe = _editable(source)
    diagnostics = recipe.get("diagnostics")
    if kind in {"diagnostic", "variable"}:
        if diagnostics is None:
            diagnostics = CommentedMap()
            recipe["diagnostics"] = diagnostics
        if not isinstance(diagnostics, dict):
            raise ValueError("'diagnostics' must be a mapping")

    if kind == "diagnostic":
        if name in diagnostics:
            raise ValueError("This diagnostic name is already in use")
        if bool(script_name.strip()) != bool(script_path.strip()):
            raise ValueError("Enter both a script name and path, or leave both blank")
        entry = CommentedMap()
        entry["variables"] = CommentedMap()
        entry["scripts"] = CommentedMap()
        if script_name.strip():
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", script_name.strip()):
                raise ValueError("Enter a valid script name")
            if any(ch in script_path for ch in "\r\n"):
                raise ValueError("Enter a valid script path")
            entry["scripts"][script_name.strip()] = CommentedMap({"script": script_path.strip()})
        diagnostics[name] = entry
        path = ["diagnostics", name]
    elif kind == "variable":
        target = diagnostics.get(diagnostic)
        if not isinstance(target, dict):
            raise ValueError("Select an existing diagnostic")
        variables = target.get("variables")
        if variables is None:
            variables = CommentedMap()
            target["variables"] = variables
        if not isinstance(variables, dict):
            raise ValueError("This diagnostic's variables must be a mapping")
        if name in variables:
            raise ValueError("This variable group name is already in use in the diagnostic")
        profiles = recipe.get("preprocessors") or {}
        if profile != "default" and (not isinstance(profiles, dict) or profile not in profiles):
            raise ValueError("Select an existing preprocessor")
        entry = CommentedMap()
        if short_name.strip() and short_name.strip() != name:
            entry["short_name"] = short_name.strip()
        if profile != "default":
            entry["preprocessor"] = profile
        variables[name] = entry
        path = ["diagnostics", diagnostic, "variables", name]
    else:
        if any("\n" in value or "\r" in value for value in (project, exp, ensemble, grid, mip)):
            raise ValueError("Dataset settings must be single-line values")
        if scope == "recipe":
            parent = recipe
            key = "datasets"
        elif scope in {"diagnostic", "variable"}:
            diagnostic_map = recipe.get("diagnostics")
            target = diagnostic_map.get(diagnostic) if isinstance(diagnostic_map, dict) else None
            if scope == "diagnostic":
                parent = target
            else:
                variables = target.get("variables") if isinstance(target, dict) else None
                parent = variables.get(variable) if isinstance(variables, dict) else None
            key = "additional_datasets"
        else:
            raise ValueError("Choose a valid dataset scope")
        if not isinstance(parent, dict):
            raise ValueError("Select an existing diagnostic or variable for this dataset")
        datasets = parent.get(key)
        if datasets is None:
            datasets = []
            parent[key] = datasets
        if not isinstance(datasets, list):
            raise ValueError(f"'{key}' must be a list")
        entry = CommentedMap({"dataset": name})
        if project.strip():
            entry["project"] = project.strip()
        if exp.strip():
            entry["exp"] = exp.strip()
        if ensemble.strip():
            entry["ensemble"] = ensemble.strip()
        if grid.strip():
            entry["grid"] = grid.strip()
        if mip.strip():
            entry["mip"] = mip.strip()
        datasets.append(entry)
        if scope == "recipe":
            path = ["datasets", len(datasets) - 1]
        elif scope == "diagnostic":
            path = ["diagnostics", diagnostic, "additional_datasets", len(datasets) - 1]
        else:
            path = ["diagnostics", diagnostic, "variables", variable, "additional_datasets", len(datasets) - 1]
    return _dump_edit(parser, recipe, source), path


def set_variable_profile(source: str, diagnostic: str, variable: str, profile: str) -> str:
    parser, recipe = _editable(source)
    profiles = recipe.get("preprocessors") or {}
    if profile != "default" and profile not in profiles:
        raise ValueError("Preprocessor profile not found")
    diagnostics = recipe.get("diagnostics") or {}
    variables = diagnostics.get(diagnostic, {}).get("variables") or {}
    if variable not in variables:
        raise ValueError("Variable not found")
    if not isinstance(variables[variable], dict):
        raise ValueError("Variable definition must be a mapping")
    if profile == "default":
        variables[variable].pop("preprocessor", None)
    else:
        variables[variable]["preprocessor"] = profile
    return _dump_edit(parser, recipe, source)


def set_profile_order(source: str, name: str, custom_order: bool) -> str:
    parser, recipe = _editable(source)
    profiles = recipe.get("preprocessors") or {}
    if name not in profiles or not isinstance(profiles[name], dict):
        raise ValueError("Preprocessor profile not found")
    if custom_order:
        profiles[name]["custom_order"] = True
    else:
        profiles[name].pop("custom_order", None)
    return _dump_edit(parser, recipe, source)


def move_profile_step(source: str, name: str, step: str, direction: int) -> str:
    if direction not in (-1, 1):
        raise ValueError("Direction must be -1 or 1")
    parser, recipe = _editable(source)
    profiles = recipe.get("preprocessors") or {}
    profile = profiles.get(name)
    if not isinstance(profile, CommentedMap) or not profile.get("custom_order"):
        raise ValueError("Enable custom order before moving steps")
    steps = [key for key in profile if key != "custom_order"]
    if step not in steps:
        raise ValueError("Step not found")
    destination = steps.index(step) + direction
    if not 0 <= destination < len(steps):
        return source
    profile.insert(destination, step, profile[step])
    return _dump_edit(parser, recipe, source)


def edit_profile_fields(source: str, name: str, step: str, fields: dict[str, str], extra: str = "") -> str:
    from .catalogue import get_catalogue

    brick = next((item for item in get_catalogue()["bricks"] if item["name"] == step), None)
    if brick is None:
        raise ValueError("Choose a documented ESMValCore brick")
    missing = [parameter["name"] for parameter in brick["parameters"]
               if parameter["required"] and not fields.get(parameter["name"], "").strip()]
    if missing:
        raise ValueError("Required parameters: " + ", ".join(missing))
    parser = yaml_reader()
    parameters = CommentedMap()
    if extra.strip():
        try:
            parsed = parser.load(extra)
        except Exception as exc:
            raise ValueError(f"Invalid additional parameters: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Additional parameters must be a YAML mapping")
        parameters.update(parsed)
    for key, raw in fields.items():
        if not raw.strip():
            continue
        try:
            parameters[key] = parser.load(raw)
        except Exception as exc:
            raise ValueError(f"Invalid value for {key}: {exc}") from exc
    stream = io.StringIO()
    parser.dump(parameters, stream)
    return edit_profile(source, name, step, stream.getvalue())
