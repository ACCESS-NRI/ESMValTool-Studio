from __future__ import annotations

import os
import shutil
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import auth, catalogue, configuration, esgf, recipes, remote


ROOT = Path(__file__).resolve().parent.parent
STATE = Path(os.environ.get("ESMVAL_GUI_STATE_DIR", ROOT / ".esmval-gui"))
app = FastAPI(title="ESMValTool GUI", docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.middleware("http")
async def require_api_token(request: Request, call_next):
    if request.url.path.startswith("/api/") and not auth.authorized(request.headers.get("X-ESMVal-Token")):
        return JSONResponse({"detail": "GUI access token required"}, status_code=401,
                            headers={"Cache-Control": "no-store"})
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
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
    host: str = "gadi"
    esmvaltool_command: str = "esmvaltool"
    setup_command: str = ""


class RunInput(RecipeInput):
    settings: remote.RemoteSettings


class ConfigFile(BaseModel):
    name: str
    content: str


class ConfigInspect(BaseModel):
    files: list[ConfigFile]


class ConfigRemote(BaseModel):
    host: str = "gadi"
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
        files = configuration.load_remote_files(body.host, body.path)
        configuration.inspect_files(files)
        return {"files": files}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "library_available": recipes.available_recipe_root() is not None,
        "local_esmvaltool": shutil.which("esmvaltool") is not None,
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
        return remote.probe(body.host, body.esmvaltool_command, body.setup_command)
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


@app.post("/api/remote/submit")
def submit(body: RunInput):
    try:
        summary = recipes.summarize(body.yaml)
        if summary["messages"]:
            raise ValueError("Fix recipe structure before submission: " + "; ".join(summary["messages"]))
        found = remote.probe(body.settings.host, body.settings.esmvaltool_command, body.settings.setup_command)
        if found.get("pbs") != "yes":
            raise ValueError("qsub is unavailable on this host")
        if not found.get("esmvaltool_path"):
            raise ValueError("ESMValTool executable was not found. Set its path or an environment setup command.")
        return remote.submit(body.settings, body.yaml, STATE, summary["title"])
    except (ValueError, RuntimeError, TimeoutError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/jobs")
def jobs():
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
    try:
        return remote.status(remote.read_job(STATE, run_id))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/jobs/{run_id}/logs")
def job_logs(run_id: str):
    try:
        return remote.logs(remote.read_job(STATE, run_id))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/jobs/{run_id}/cancel")
def cancel_job(run_id: str):
    try:
        return remote.cancel(STATE, remote.read_job(STATE, run_id))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, RuntimeError, TimeoutError) as exc:
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
