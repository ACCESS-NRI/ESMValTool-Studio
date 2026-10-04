"""Small ESGF Search API client for filling recipe dataset facets."""

from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SEARCH_URL = os.environ.get(
    "ESMVAL_GUI_ESGF_SEARCH_URL", "https://esgf-data.dkrz.de/esg-search/search/"
)
FACETS = {
    # Match ESMValCore's ESGF facet mapping, not the visible Solr record ID.
    "CMIP3": {"dataset": "model", "exp": "experiment", "ensemble": "ensemble"},
    "CMIP5": {"dataset": "model", "exp": "experiment", "ensemble": "ensemble", "mip": "cmor_table"},
    "CMIP6": {"dataset": "source_id", "exp": "experiment_id", "ensemble": "member_id",
              "grid": "grid_label", "mip": "table_id"},
    "CMIP7": {"dataset": "source_id", "exp": "experiment_id", "ensemble": "variant_label",
              "grid": "grid_label", "mip": "realm"},
    "obs4MIPs": {"dataset": "source_id"},
}
VARIABLE_FACET = {"CMIP3": "variable", "CMIP5": "variable", "CMIP6": "variable",
                  "CMIP7": "variable_id", "obs4MIPs": "variable"}
# ESMValCore's CMIP5 recipe names differ from these ESGF model facet values.
CMIP5_RECIPE_TO_ESGF = {
    "ACCESS1-0": "ACCESS1.0", "ACCESS1-3": "ACCESS1.3",
    "bcc-csm1-1": "BCC-CSM1.1", "bcc-csm1-1-m": "BCC-CSM1.1(m)",
    "CESM1-BGC": "CESM1(BGC)", "CESM1-CAM5": "CESM1(CAM5)",
    "CESM1-CAM5-1-FV2": "CESM1(CAM5.1,FV2)", "CESM1-FASTCHEM": "CESM1(FASTCHEM)",
    "CESM1-WACCM": "CESM1(WACCM)", "CSIRO-Mk3-6-0": "CSIRO-Mk3.6.0",
    "fio-esm": "FIO-ESM", "GFDL-CM2p1": "GFDL-CM2.1",
    "inmcm4": "INM-CM4", "MRI-AGCM3-2H": "MRI-AGCM3.2H",
    "MRI-AGCM3-2S": "MRI-AGCM3.2S",
}
CMIP5_ESGF_TO_RECIPE = {value: key for key, value in CMIP5_RECIPE_TO_ESGF.items()}


def _value(record: dict, field: str) -> str:
    value = record.get(field, "")
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value) if value is not None else ""


def normalize_results(payload: dict, project: str) -> dict:
    """Expose only recipe-relevant fields and concise provenance from Solr."""
    mapping = FACETS[project]
    response = payload.get("response", {})
    results = []
    seen = set()
    for record in response.get("docs", []):
        if not isinstance(record, dict):
            continue
        item = {name: _value(record, facet) for name, facet in mapping.items()}
        if not item["dataset"]:
            continue
        if project == "CMIP5":
            item["dataset"] = CMIP5_ESGF_TO_RECIPE.get(item["dataset"], item["dataset"])
        item["project"] = project
        item["variable"] = _value(record, "variable_id") or _value(record, "variable")
        item["version"] = _value(record, "version")
        item["id"] = _value(record, "id")
        signature = tuple(item.get(key, "") for key in ("dataset", "exp", "ensemble", "grid", "mip", "variable"))
        if signature in seen:
            continue
        seen.add(signature)
        results.append(item)
    return {"results": results, "total": int(response.get("numFound", len(results)))}


def search_datasets(query: str, project: str, *, experiment: str = "", variable: str = "",
                    ensemble: str = "", limit: int = 40) -> dict:
    if project not in FACETS:
        raise ValueError("Choose a supported ESGF project")
    query = query.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{2,80}", query):
        raise ValueError("Enter at least two letters or numbers from a dataset name")
    experiment, variable, ensemble = experiment.strip(), variable.strip(), ensemble.strip()
    for value in (experiment, variable, ensemble):
        if value and not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value):
            raise ValueError("Enter valid ESGF facet filters")
    facet = FACETS[project]["dataset"]
    exact_model = CMIP5_RECIPE_TO_ESGF.get(query) if project == "CMIP5" else None
    params = {
        "type": "Dataset", "project": project,
        "latest": "true", "distrib": "true",
        "limit": min(max(limit, 1), 100),
        "fields": ",".join(sorted({*FACETS[project].values(), "id", "version", "variable_id", "variable"})),
        "format": "application/solr+json",
    }
    if exact_model:
        params[facet] = exact_model
    else:
        params["query"] = f"{facet}:{query}*"
    if experiment and "exp" in FACETS[project]:
        params[FACETS[project]["exp"]] = experiment
    if variable:
        params[VARIABLE_FACET[project]] = variable
    if ensemble and "ensemble" in FACETS[project]:
        params[FACETS[project]["ensemble"]] = ensemble
    url = SEARCH_URL.rstrip("/") + "/?" + urlencode(params)
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "ESMValTool-GUI/1.0"})
    try:
        with urlopen(request, timeout=12) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        raise RuntimeError("ESGF search is unavailable. You can enter dataset facets manually.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("response"), dict):
        raise RuntimeError("ESGF returned an unexpected response. You can enter dataset facets manually.")
    return normalize_results(payload, project)
