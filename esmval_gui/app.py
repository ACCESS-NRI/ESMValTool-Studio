from __future__ import annotations

import os
import secrets
import shutil
from html import escape
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from . import auth, catalogue, configuration, esgf, outputs, pdf_preview, recipes, remote, scripts_catalogue, recipe_monitor


ROOT = Path(__file__).resolve().parent.parent
STATE = Path(os.environ.get("ESMVAL_GUI_STATE_DIR", ROOT / ".esmval-gui"))
app = FastAPI(title="ESMValTool Studio", docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.middleware("http")
async def require_api_token(request: Request, call_next):
    # Uvicorn includes root_path in scope.path when hosted under an ARE proxy.
    path = request.scope["path"]
    prefix = request.scope.get("root_path", "").rstrip("/")
    if prefix and path.startswith(prefix + "/"):
        path = path[len(prefix):]
    is_api = path.startswith("/api/")
    if is_api and not auth.authorized(request.headers.get("X-ESMVal-Token")):
        return JSONResponse({"detail": "GUI access token required"}, status_code=401,
                            headers={"Cache-Control": "no-store"})
    # Jupyter Server Proxy delegates CSRF checks to the proxied application.
    # Its server-side API token must not turn cookie-authenticated writes into
    # requests that a third-party website can trigger.
    if (is_api and os.environ.get("ESMVAL_GUI_REQUIRE_XSRF") == "1"
            and request.method not in {"GET", "HEAD", "OPTIONS"}):
        cookie = request.cookies.get("_xsrf", "")
        header = request.headers.get("X-XSRFToken", "")
        if not cookie or not header or not secrets.compare_digest(cookie.encode(), header.encode()):
            return JSONResponse({"detail": "Reload Studio before making changes (XSRF token required)"},
                                status_code=403, headers={"Cache-Control": "no-store"})
    response = await call_next(request)
    if is_api:
        response.headers["Cache-Control"] = "no-store"
    return response


class RecipeInput(BaseModel):
    yaml: str


class ProfileEdit(RecipeInput):
    profile: str
    step: str
    parameters: str | None = None
    remove: bool = False


class ProfileCreate(RecipeInput):
    profile: str


class ProfileOrder(RecipeInput):
    profile: str
    custom_order: bool


class ProfileFields(RecipeInput):
    profile: str
    step: str
    fields: dict[str, str]
    extra: str = ""


class ProfileMove(RecipeInput):
    profile: str
    step: str
    direction: int


class VariableProfile(RecipeInput):
    diagnostic: str
    variable: str
    profile: str


class NodeReference(RecipeInput):
    kind: str
    path: list[str | int]


class NodeEdit(NodeReference):
    definition: str
    name: str | None = None


class NodeDelete(NodeReference):
    all_occurrences: bool = False


class NodeFields(NodeReference):
    fields: dict[str, str]
    removed: list[str] = Field(default_factory=list)
    name: str | None = None


class NodeCreate(RecipeInput):
    kind: str
    name: str
    diagnostic: str = ""
    variable: str = ""
    scope: str = "recipe"
    project: str = ""
    exp: str = ""
    ensemble: str = ""
    grid: str = ""
    mip: str = ""
    short_name: str = ""
    profile: str = "default"
    script_name: str = ""
    script_path: str = ""


class NewRecipe(BaseModel):
    title: str
    description: str
    author: str
    filename: str
    dataset: str
    project: str
    exp: str = ""
    ensemble: str = ""
    grid: str = ""
    diagnostic: str
    variable: str
    short_name: str = ""
    mip: str = "Amon"
    realm: str = ""
    script_name: str = ""
    script_path: str = ""


class ScriptCreate(RecipeInput):
    diagnostic: str
    name: str
    path: str


class ProbeInput(BaseModel):
    host: str = "gadi.nci.org.au"
    username: str = ""
    esmvaltool_command: str = "esmvaltool"
    setup_command: str = ""
    analysis3_module: str = ""


class RunInput(RecipeInput):
    settings: remote.RemoteSettings
    recipe_path: str | None = None


class MonitorInput(BaseModel):
    username: str = ""


def monitor_user(body: MonitorInput) -> str:
    try:
        user = recipe_monitor.identity(body.username)
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        raise HTTPException(403, f"Could not verify ARE run access: {exc}") from exc
    if user not in recipe_monitor.allowed_users():
        raise HTTPException(403, f"Runs and Recipe monitor are not enabled for ARE account {user}")
    return user


@app.post("/api/recipe-monitor/access")
def monitor_access(body: MonitorInput):
    return {"user": monitor_user(body), "allowed": True}


@app.post("/api/recipe-monitor")
def monitor_overview(body: MonitorInput):
    return recipe_monitor.overview(STATE, monitor_user(body))


@app.post("/api/recipe-monitor/analysis3")
def monitor_analysis3(body: MonitorInput):
    user = monitor_user(body)
    try:
        return {"modules": remote.analysis3_versions(user)}
    except (ValueError, RuntimeError, TimeoutError) as exc:
        raise HTTPException(400, str(exc)) from exc


class ConfigFile(BaseModel):
    name: str
    content: str


class ConfigInspect(BaseModel):
    files: list[ConfigFile]


class ConfigRemote(BaseModel):
    host: str = "gadi.nci.org.au"
    username: str = ""
    path: str = ""


@app.post("/api/config/inspect")
def inspect_config(body: ConfigInspect):
    try:
        return configuration.inspect_files([item.model_dump() for item in body.files])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/config/remote")
def remote_config(body: ConfigRemote):
    try:
        files = configuration.load_remote_files(remote.ssh_target(body.host, body.username), body.path)
        configuration.inspect_files(files)
        return {"files": files}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/health")
def health():
    try:
        run_access = {"allowed": True, "user": monitor_user(MonitorInput())}
    except HTTPException as exc:
        run_access = {"allowed": False, "detail": exc.detail}
    return {
        "ok": True,
        "library_available": recipes.available_recipe_root() is not None,
        "local_esmvaltool": shutil.which("esmvaltool") is not None,
        "execution_mode": remote.execution_mode(),
        "are_session": recipe_monitor.is_are(),
        "run_access": run_access,
        "runtime_setup": os.environ.get("ESMVAL_GUI_RUNTIME_SETUP", "") if remote.execution_mode() == "local" else "",
        "runtime_command": os.environ.get("ESMVAL_GUI_RUNTIME_COMMAND", "esmvaltool") if remote.execution_mode() == "local" else "esmvaltool",
    }


@app.get("/api/recipes")
def library(query: str = Query(default="", max_length=120)):
    root = recipes.available_recipe_root()
    return {"root": str(root) if root else None,
            "recipes": recipes.list_recipes(root, query) if root else []}


@app.get("/api/esgf/datasets")
def esgf_datasets(query: str = Query(min_length=2, max_length=80), project: str = "CMIP6",
                  experiment: str = Query(default="", max_length=80),
                  variable: str = Query(default="", max_length=80),
                  ensemble: str = Query(default="", max_length=80)):
    try:
        return esgf.search_datasets(query, project, experiment=experiment,
                                    variable=variable, ensemble=ensemble)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/recipes/{relative:path}")
def library_recipe(relative: str):
    root = recipes.available_recipe_root()
    if root is None:
        raise HTTPException(404, "No ESMValTool recipe library found")
    try:
        source = recipes.read_library_recipe(root, relative)
        return {"yaml": source, "summary": recipes.summarize(source, root),
                "library_info": recipes.library_recipe_info(root, relative)}
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/docs-figure/{relative:path}")
def docs_figure(relative: str):
    root = recipes.available_recipe_root()
    docs_root = recipes.documentation_root(root) if root else None
    if docs_root is None:
        raise HTTPException(404, "Documentation figures unavailable")
    allowed = docs_root / "recipes" / "figures"
    target = (docs_root / relative).resolve()
    if (not target.is_relative_to(allowed) or not target.is_file()
            or target.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".svg"}):
        raise HTTPException(404, "Figure not found")
    return FileResponse(target)


@app.post("/api/parse")
def parse(body: RecipeInput):
    try:
        return recipes.summarize(body.yaml)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/recipe/new")
def new_recipe(body: NewRecipe):
    try:
        source = recipes.build_recipe(**body.model_dump())
        return {"yaml": source, "summary": recipes.summarize(source), "name": body.filename}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/script/create")
def create_script(body: ScriptCreate):
    try:
        source = recipes.add_script(body.yaml, body.diagnostic, body.name, body.path)
        return {"yaml": source, "summary": recipes.summarize(source)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/node/read")
def read_node(body: NodeReference):
    try:
        return recipes.read_node(body.yaml, body.kind, body.path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/node/create")
def create_node(body: NodeCreate):
    try:
        source, path = recipes.create_node(body.yaml, body.kind, body.name,
                                           diagnostic=body.diagnostic, variable=body.variable,
                                           scope=body.scope, project=body.project, exp=body.exp,
                                           ensemble=body.ensemble, grid=body.grid, mip=body.mip,
                                           short_name=body.short_name, profile=body.profile,
                                           script_name=body.script_name, script_path=body.script_path)
        return {"yaml": source, "summary": recipes.summarize(source), "path": path}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/node/edit")
def edit_node(body: NodeEdit):
    try:
        source, path = recipes.edit_node(body.yaml, body.kind, body.path, body.definition, body.name)
        return {"yaml": source, "summary": recipes.summarize(source), "path": path}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/node/delete")
def delete_node(body: NodeDelete):
    try:
        source = recipes.delete_node(body.yaml, body.kind, body.path, body.all_occurrences)
        return {"yaml": source, "summary": recipes.summarize(source)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/node/edit-fields")
def edit_node_fields(body: NodeFields):
    try:
        source, path = recipes.edit_node_fields(body.yaml, body.kind, body.path,
                                                body.fields, body.removed, body.name)
        return {"yaml": source, "summary": recipes.summarize(source), "path": path}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/preprocessors")
def preprocessors():
    return catalogue.get_catalogue()


@app.get("/api/scripts")
def scripts(query: str = Query(default="", max_length=100)):
    return scripts_catalogue.search(recipes.available_recipe_root(), query)


@app.get("/api/scripts/source")
def script_source(path: str = Query(min_length=1, max_length=500)):
    try:
        return scripts_catalogue.read_source(recipes.available_recipe_root(), path)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/profile/create")
def create_profile(body: ProfileCreate):
    try:
        source = recipes.create_profile(body.yaml, body.profile)
        return {"yaml": source, "summary": recipes.summarize(source)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/profile/order")
def profile_order(body: ProfileOrder):
    try:
        source = recipes.set_profile_order(body.yaml, body.profile, body.custom_order)
        return {"yaml": source, "summary": recipes.summarize(source)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/profile/move")
def profile_move(body: ProfileMove):
    try:
        source = recipes.move_profile_step(body.yaml, body.profile, body.step, body.direction)
        return {"yaml": source, "summary": recipes.summarize(source)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/variable/preprocessor")
def variable_profile(body: VariableProfile):
    try:
        source = recipes.set_variable_profile(body.yaml, body.diagnostic, body.variable, body.profile)
        return {"yaml": source, "summary": recipes.summarize(source)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/profile/edit")
def edit_profile(body: ProfileEdit):
    try:
        source = recipes.edit_profile(body.yaml, body.profile, body.step, body.parameters, body.remove)
        return {"yaml": source, "summary": recipes.summarize(source)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/profile/edit-fields")
def edit_profile_fields(body: ProfileFields):
    try:
        source = recipes.edit_profile_fields(body.yaml, body.profile, body.step, body.fields, body.extra)
        return {"yaml": source, "summary": recipes.summarize(source)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/remote/probe")
def probe(body: ProbeInput):
    try:
        setup = remote.analysis3_setup(body.analysis3_module) if body.analysis3_module else body.setup_command
        command = "esmvaltool" if body.analysis3_module else body.esmvaltool_command
        return remote.probe(remote.ssh_target(body.host, body.username), command, setup,
                            allow_fallback=not bool(body.analysis3_module))
    except (ValueError, RuntimeError, TimeoutError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/remote/preview")
def preview(body: RunInput):
    try:
        summary = recipes.summarize(body.yaml)
        if summary["messages"]:
            raise ValueError("Fix recipe structure before submission: " + "; ".join(summary["messages"]))
        path = body.settings.work_dir.rstrip("/") + "/preview"
        script = remote.script_for(body.settings, path + "/recipe.yml", path)
        return {"script": script, "summary": summary}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/remote/preflight")
def preflight(body: RunInput):
    """Bounded checks without uploading files or submitting a PBS job."""
    try:
        summary = recipes.summarize(body.yaml)
        remote.validate_resources(body.settings)
        checks = [{"level": "error", "label": "Recipe structure", "detail": message}
                  for message in summary["messages"]]
        if remote.execution_mode() == "local":
            checks.extend(remote.check_local_paths(body.settings))
        found = remote.probe(body.settings.ssh_target, body.settings.esmvaltool_command,
                             body.settings.setup_command, allow_fallback=not bool(body.settings.analysis3_module))
        checks.append({"level": "ok" if found.get("pbs") == "yes" else "error", "label": "PBS",
                       "detail": "qsub found" if found.get("pbs") == "yes" else "qsub unavailable"})
        executable = found.get("esmvaltool_path")
        checks.append({"level": "ok" if executable else "error", "label": "ESMValTool",
                       "detail": executable or "Executable not found"})
        if body.settings.config_file and found.get("config_mode") == "directory":
            checks.append({"level": "error", "label": "Configuration format",
                           "detail": "This runtime uses configuration directories. Clear Config file and set Config directory to the directory containing your YAML settings."})
        selected_config = body.settings.config_file or body.settings.config_dir or found.get("config_dir") or found.get("config_file", "")
        config = None
        try:
            files = configuration.load_remote_files(body.settings.ssh_target, selected_config)
            config = configuration.inspect_files(files)
            checks.append({"level": "ok", "label": "Configuration",
                           "detail": f"Read {len(files)} YAML file(s) from Gadi"})
        except ValueError as exc:
            checks.append({"level": "error" if body.settings.config_file or body.settings.config_dir else "warning",
                           "label": "Configuration", "detail": str(exc)})
        projects = sorted({str(item["settings"].get("project")) for item in summary["graph"]["datasets"]
                           if item["settings"].get("project")}
                          | {str(item["detail"].get("project")) for item in summary["graph"]["variables"]
                             if item["detail"].get("project")})
        configured = config["effective"].get("projects") if config else None
        legacy_roots = config["effective"].get("rootpath") if config else None
        for project in projects:
            project_config = configured.get(project) if isinstance(configured, dict) else None
            if isinstance(project_config, dict) and project_config.get("data"):
                checks.append({"level": "ok", "label": f"{project} data",
                               "detail": "Project data source configured; matching input files still need ESMValCore validation"})
            elif isinstance(legacy_roots, dict) and legacy_roots.get(project):
                checks.append({"level": "ok", "label": f"{project} data",
                               "detail": "Legacy rootpath configured; matching input files still need ESMValCore validation"})
            else:
                detail = "No project-specific data root in the selected configuration"
                if isinstance(legacy_roots, dict) and legacy_roots.get("default"):
                    detail += "; a default root may apply"
                checks.append({"level": "warning", "label": f"{project} data", "detail": detail})
        script_paths = sorted({item["path"] for diagnostic in summary["graph"]["diagnostics"]
                               for item in diagnostic["scripts"] if item["path"]})
        if executable:
            try:
                inspected = remote.inspect_scripts(body.settings.ssh_target, executable, script_paths,
                                                   body.settings.setup_command)
                versions = inspected.get("versions") or {}
                checks.append({"level": "ok" if versions.get("ESMValCore") else "warning",
                               "label": "Gadi environment",
                               "detail": f"ESMValTool {versions.get('ESMValTool') or 'unknown'} · ESMValCore {versions.get('ESMValCore') or 'unknown'}"})
                for path, exists in inspected["scripts"].items():
                    checks.append({"level": "ok" if exists else "warning", "label": "Diagnostic script",
                                   "detail": f"{path}: {'found' if exists else 'not found in installed ESMValTool'}"})
                if len(script_paths) > 100:
                    checks.append({"level": "warning", "label": "Diagnostic scripts",
                                   "detail": f"Only the first 100 of {len(script_paths)} paths were checked"})
                needed = sorted({step["name"] for profile in summary["graph"]["profiles"] for step in profile["steps"]})
                available = inspected.get("preprocessor_steps")
                if needed and available is not None:
                    missing = sorted(set(needed) - set(available))
                    checks.append({"level": "error" if missing else "ok", "label": "Preprocessor steps",
                                   "detail": "Unsupported on Gadi: " + ", ".join(missing) if missing else f"All {len(needed)} named steps exposed by Gadi ESMValCore"})
                elif needed:
                    checks.append({"level": "warning", "label": "Preprocessor steps",
                                   "detail": "Could not compare named steps with Gadi ESMValCore"})
            except ValueError as exc:
                checks.append({"level": "warning", "label": "Gadi installation", "detail": str(exc)})
        checks.append({"level": "warning", "label": "Input files",
                       "detail": "Exact dataset facets and files have not been checked on Gadi"})
        return {"ready": not any(item["level"] == "error" for item in checks),
                "checks": checks, "summary": summary}
    except (ValueError, RuntimeError, TimeoutError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/remote/submit")
def submit(body: RunInput):
    try:
        if body.recipe_path is not None:
            root = recipes.available_recipe_root()
            if root is None:
                raise ValueError("Recipe library is unavailable")
            try:
                recipes.read_library_recipe(root, body.recipe_path)
            except FileNotFoundError as exc:
                raise ValueError("Recipe not found in library") from exc
            body.recipe_path = (root / body.recipe_path).resolve().relative_to(root.resolve()).as_posix()
        summary = recipes.summarize(body.yaml)
        if summary["messages"]:
            raise ValueError("Fix recipe structure before submission: " + "; ".join(summary["messages"]))
        found = remote.probe(body.settings.ssh_target, body.settings.esmvaltool_command, body.settings.setup_command,
                             allow_fallback=not bool(body.settings.analysis3_module))
        if found.get("pbs") != "yes":
            raise ValueError("qsub is unavailable on this host")
        if not found.get("esmvaltool_path"):
            raise ValueError("ESMValTool executable was not found. Set its path or an environment setup command.")
        if body.settings.config_file and found.get("config_mode") == "directory":
            raise ValueError("This ESMValCore runtime requires a config directory. Clear Config file and select Config directory instead.")
        settings = body.settings.model_copy(update={"esmvaltool_command": found["esmvaltool_path"]})
        if remote.execution_mode() == "local":
            errors = [item["detail"] for item in remote.check_local_paths(settings) if item["level"] == "error"]
            if errors:
                raise ValueError("; ".join(errors))
        return remote.submit(settings, body.yaml, STATE, summary["title"], recipe_path=body.recipe_path)
    except (ValueError, RuntimeError, TimeoutError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/jobs")
def jobs():
    monitor_user(MonitorInput())
    if not STATE.is_dir():
        return {"jobs": []}
    records = []
    for path in sorted(STATE.glob("*.json"), reverse=True):
        try:
            records.append(remote.read_job(STATE, path.stem))
        except (FileNotFoundError, ValueError):
            continue
    records.sort(key=lambda item: item.get("submitted_at", ""), reverse=True)
    return {"jobs": records[:50]}


@app.get("/api/jobs/{run_id}")
def job(run_id: str):
    monitor_user(MonitorInput())
    try:
        return remote.refresh_status(STATE, run_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/jobs/{run_id}/logs")
def job_logs(run_id: str):
    monitor_user(MonitorInput())
    try:
        return remote.logs(remote.read_job(STATE, run_id))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/jobs/{run_id}/cancel")
def cancel_job(run_id: str):
    monitor_user(MonitorInput())
    try:
        return remote.cancel(STATE, remote.read_job(STATE, run_id))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, RuntimeError, TimeoutError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/jobs/{run_id}/outputs")
def job_outputs(run_id: str):
    monitor_user(MonitorInput())
    try:
        return outputs.list_files(remote.read_job(STATE, run_id))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/jobs/{run_id}/output")
def job_output(run_id: str, path: str = Query(min_length=1, max_length=2048), download: bool = False,
               page: int = 0):
    monitor_user(MonitorInput())
    try:
        target = outputs.resolve_file(remote.read_job(STATE, run_id), path)
        headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                   "Content-Security-Policy": "sandbox; default-src 'none'"}
        if download:
            return FileResponse(target, media_type="application/octet-stream", filename=target.name, headers=headers)
        kind = outputs.kind_for(target)
        if kind == "text":
            with target.open("rb") as handle:
                data = handle.read(outputs.MAX_TEXT_BYTES + 1)
            headers["X-Studio-Truncated"] = str(len(data) > outputs.MAX_TEXT_BYTES).lower()
            return Response(data[:outputs.MAX_TEXT_BYTES].decode("utf-8", errors="replace"),
                            media_type="text/plain", headers=headers)
        if kind not in ("image", "pdf"):
            raise ValueError("Download this file to inspect it with a compatible application")
        if target.stat().st_size > outputs.MAX_PREVIEW_BYTES:
            raise ValueError("This file exceeds the 25 MB preview limit. Use Download instead.")
        if kind == "pdf":
            if not 0 <= page <= 10000:
                raise ValueError("Invalid PDF page")
            data, pages = pdf_preview.render(target, page)
            headers["X-Studio-Pages"] = str(pages)
            return Response(data, media_type="image/png", headers=headers)
        return FileResponse(target, media_type=outputs.IMAGE_TYPES[target.suffix.lower()], headers=headers)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Output file or job not found") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/jobs/{run_id}/report")
def job_report(run_id: str, path: str = Query(min_length=1, max_length=2048)):
    monitor_user(MonitorInput())
    try:
        target = outputs.resolve_file(remote.read_job(STATE, run_id), path)
        if outputs.kind_for(target) != "html":
            raise ValueError("Choose an HTML report")
        return {"url": f"reports/{run_id}/{outputs.report_ticket(run_id)}/{quote(path, safe='/')}"}
    except FileNotFoundError as exc:
        raise HTTPException(404, "Report or job not found") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/reports/{run_id}/{ticket}/{path:path}")
def report_file(run_id: str, ticket: str, path: str):
    monitor_user(MonitorInput())
    # Iframes cannot send Studio's API header. This capability grants only GET
    # access to this run, preserving relative URLs without exposing the API token.
    if not outputs.valid_report_ticket(run_id, ticket):
        raise HTTPException(401, "Report link expired. Refresh Results in Studio.")
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
               "Referrer-Policy": "no-referrer",
               "Content-Security-Policy": (
                   "sandbox allow-scripts allow-downloads; default-src 'none'; "
                   "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://ajax.googleapis.com; "
                   "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                   "img-src 'self' data: https://raw.githubusercontent.com; "
                   "font-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
               )}
    try:
        target = outputs.resolve_file(remote.read_job(STATE, run_id), path, allow_directory=True)
        if target.is_dir():
            # Preserve ESMValCore's figures/data directory links without exposing
            # arbitrary filesystem paths or following symlinks.
            links = []
            with os.scandir(target) as entries:
                for index, entry in enumerate(entries):
                    if index >= outputs.MAX_FILES:
                        links.append("<li>Listing limit reached. Use Studio's Files view.</li>")
                        break
                    if entry.is_symlink() or not (entry.is_file() or entry.is_dir()):
                        continue
                    child = path.rstrip('/') + '/' + entry.name
                    # Absolute-to-ticket relative links work with or without a trailing slash.
                    levels = len(path.split('/')) - 1
                    href = '../' * levels + quote(child, safe='/')
                    links.append(f'<li><a href="{href}">{escape(entry.name)}</a></li>')
            return Response('<!doctype html><title>Output files</title><h1>Output files</h1><ul>' + ''.join(sorted(links)) + '</ul>', media_type="text/html", headers=headers)
        kind = outputs.kind_for(target)
        media_type = {"html": "text/html", "text": "text/plain", "pdf": "application/pdf"}.get(kind)
        media_type = outputs.IMAGE_TYPES.get(target.suffix.lower(), media_type)
        media_type = {".css": "text/css", ".js": "text/javascript", ".woff": "font/woff", ".woff2": "font/woff2"}.get(target.suffix.lower(), media_type)
        if media_type is None:
            return FileResponse(target, media_type="application/octet-stream", filename=target.name, headers=headers)
        return FileResponse(target, media_type=media_type, headers=headers)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Report file or job not found") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/app.js")
def javascript():
    return FileResponse(ROOT / "web" / "app.js", media_type="text/javascript")


@app.get("/style.css")
def stylesheet():
    return FileResponse(ROOT / "web" / "style.css", media_type="text/css")


@app.get("/esmvaltool-logo.png")
def logo():
    return FileResponse(ROOT / "web" / "esmvaltool-logo.png", media_type="image/png")
