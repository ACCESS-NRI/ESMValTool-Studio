# ESMValTool Studio — Implementation Plan

Companion to [specifications.md](specifications.md). This document proposes a stack,
an architecture, and a delivery sequence.

---

## 1. The finding that shapes everything

Before choosing a stack, I checked how ESMValCore actually executes a recipe. Two
facts change the design of the editor:

**A recipe is not one pipeline.** Its top-level sections are `documentation`,
`datasets`, `preprocessors`, `diagnostics`. The `preprocessors` section is a *library
of named profiles*. A diagnostic's variable then selects one by name:

```yaml
preprocessors:
  regrid_and_average:        # a named profile, reusable
    regrid: {target_grid: 1x1, scheme: linear}
    area_statistics: {operator: mean}

diagnostics:
  my_diagnostic:
    variables:
      tas:
        preprocessor: regrid_and_average    # <- reference by name
    scripts:
      main:
        script: examples/diagnostic.py
```

So the true graph is `datasets → (named profile) → variables → scripts`, plus
`ancestors:` which links diagnostic tasks into a real DAG. It is many-to-one: several
variables can share one profile.

**Order inside a profile is not the order you write it.** ESMValCore applies steps in
a canonical `DEFAULT_ORDER` (`esmvalcore/preprocessor/__init__.py`), chosen to
minimise information loss. The YAML order is ignored *unless* the profile sets
`custom_order: true` — and even then, `INITIAL_STEPS` and `FINAL_STEPS` are fixed
boundaries; only the steps between them may be reordered.

> Verify both against the ESMValCore version you target — `_extract_preprocessor_order()`
> in `esmvalcore/_recipe/recipe.py` is the authority.

**Design consequence.** A naive "wire any box to any box" canvas would be actively
misleading: a user could wire `area_statistics → regrid` and ESMValCore would silently
run it the other way round. The editor must be **two-level**:

| Level | UI | Why |
|---|---|---|
| **Recipe canvas** | node graph — datasets, profiles, diagnostics, scripts, `ancestors` edges | genuinely a graph; drag-and-drop is honest here |
| **Profile editor** | ordered step list, shown in canonical order, steps toggled on/off | not a free graph; reordering is only meaningful with `custom_order` |

The profile editor gets an explicit **"custom order"** switch. Off (default): steps
render in canonical order, greyed drag handles, with a note that ESMValCore sets the
order. On: drag to reorder within the mutable band, with the fixed initial/final steps
pinned and visibly locked. This turns the single biggest source of recipe confusion
into something the GUI *teaches*.

This also resolves open question 3 in the spec: connection-level type checking matters
much less than expected, because there are few free connections. Validation effort
belongs in profile/argument validation instead.

---

## 2. Stack recommendation

**Python backend (FastAPI) + React/TypeScript frontend + React Flow canvas, delivered
as a locally-launched web app, with a desktop shell deferred.**

### The backend must be Python — this is forced

Three of the spec's core features are *ESMValCore introspection problems*:

- the preprocessor catalogue = importing `esmvalcore.preprocessor`, reading signatures
  and numpydoc docstrings;
- recipe validation = reusing ESMValCore's own recipe loader;
- YAML round-trip = matching ESMValCore's exact schema semantics.

Any non-Python backend means reimplementing ESMValCore's semantics in another language
and watching it drift out of sync every release. So: Python.

**This answers spec open question 1.** Generate the catalogues by introspecting the
*installed* ESMValCore. Ship the GUI as a conda-forge package that lands in the same
environment as ESMValTool, and the catalogue is automatically correct for the version
the user actually has. No pinning, no hand-maintained duplicate, no separate interface
per release.

### React is a good call — keep it

React Flow (`@xyflow/react`, MIT) is the mature node-graph library: nodes, typed
handles, connection validation hooks, minimap, undo. It is the reason to pick React
over Vue/Svelte here; the alternatives (Rete.js, Drawflow, LiteGraph) are smaller and
less maintained. Vite + TypeScript + Zustand for graph state (React Flow's own
recommendation) + Monaco for the YAML side-panel.

### Why a web app before a desktop app

The spec asks for Windows/Mac/Linux, and separately asks how to reach Gadi. **These are
the same question**, and browser-first answers both at once:

- The frontend talks to a backend over HTTP. That backend can run **locally** (offline
  builder + local runs) or **on Gadi**, reached by an SSH tunnel — precisely the VS Code
  Remote model the spec floats, with *no second code path*.
- Cross-platform for free: the hard part is the Python env, which conda already solves
  on all three OSes and which users installing ESMValTool have anyway.
- Distribution is `conda install esmvaltool-studio`, then `esmvaltool-studio` opens a browser.
  No installers, no code signing, no notarisation, no auto-update channel.

| Option | Verdict |
|---|---|
| **Local web app** | **Recommended.** No packaging burden; remote mode is the same architecture. Cost: a browser tab, no OS integration (file dialogs, tray). |
| **Tauri + React** | Small binaries, but adds Rust to a Python-centric community, and Linux WebKitGTK diverges from WebView2/WKWebView. Still needs the Python sidecar, so it buys a window, not an architecture. |
| **Electron + React** | Predictable, but ~150 MB, and still needs the Python sidecar. Choose only if native OS integration becomes a real requirement. |
| **PyQt/PySide + NodeGraphQt** | One language, familiar to contributors. But slower UI development, weaker node-editor ecosystem, and it forfeits the run-on-HPC-in-a-browser story. |
| **JupyterLab extension** | Tempting — the audience lives in Jupyter, and NCI ARE already serves JupyterLab on Gadi. Worth prototyping as a *second* frontend later; too constraining as the only one. |

If OS integration is later wanted, wrap the same frontend in Tauri. Because the
frontend is just an HTTP client, that stays a packaging decision rather than a rewrite.

---

## 3. Architecture

```
┌─────────────────────────────────────────┐
│  React + React Flow (browser)           │
│  canvas · profile editor · catalogue    │
│  browser · YAML preview · run monitor   │
└───────────────┬─────────────────────────┘
                │  HTTP + WebSocket (logs, job status)
┌───────────────▼─────────────────────────┐
│  FastAPI backend (Python)               │
│  ├── introspection  → catalogues        │
│  ├── recipe I/O     → ruamel.yaml       │
│  ├── validation     → ESMValCore loader │
│  ├── data discovery → site config       │
│  └── execution      → local | PBS       │
└───────────────┬─────────────────────────┘
                │ imports
┌───────────────▼─────────────────────────┐
│  Installed ESMValCore / ESMValTool       │
└─────────────────────────────────────────┘
```

Backend runs **either** on the user's machine **or** on the HPC login node. Same
process, same API; only the execution backend and the data-discovery root differ.

### Modules

**`introspection`** — walks `esmvalcore.preprocessor.__all__`, pulls signatures via
`inspect` and parses numpydoc docstrings into `{name, category, summary, params[],
defaults, docs_url}`. Also exposes `DEFAULT_ORDER`, `INITIAL_STEPS`, `FINAL_STEPS` so
the frontend can render the canonical order and lock the fixed bands. Diagnostics need
a *different* path: `diag_scripts/` holds Python, R, NCL and Julia, so parse each
recipe's script references plus any script-level metadata rather than importing.

**`recipe`** — parse YAML → internal graph model → JSON for the frontend, and back.
Use **ruamel.yaml** (not PyYAML) to preserve comments and formatting on round-trip;
users will hand-edit these files and losing their comments is unacceptable.

**`validation`** — layered, fastest first: (1) JSON-schema-ish checks on the graph;
(2) per-step argument checks against introspected signatures; (3) full load through
ESMValCore's recipe loader for authoritative errors. Map errors back to node IDs so
they surface on the canvas, not just as a log dump.

**`data`** — read the site configuration to discover available data. Behind an
interface from day one: local filesystem, then Gadi. Long-term, `esmvalcore.dataset`
search is the right engine.

**`execution`** — one interface, several backends: local subprocess first, then PBS.
PBS: render a job script, `qsub`, poll `qstat`, stream logs over WebSocket. Encode
Gadi's queue rules (walltime/CPU/memory limits per queue, storage flags like
`-l storage=gdata/...`) as data, and validate the request *before* submitting so users
learn about a bad queue choice in the UI rather than from a rejected job.

**SSH:** rely on the user's existing keys and `ssh-agent`. The app should never prompt
for or store a passphrase or password — offer the tunnel command, or use the system
`ssh` binary and inherit its config, so `~/.ssh/config` jump hosts and MFA keep working.

---

## 4. Delivery sequence

**Phase 0 — de-risking spike (~2–3 weeks).** Do not skip. Prove: (a) introspection
yields a usable catalogue with sane docs for all ~70 preprocessors; (b) a real
ESMValTool recipe survives YAML → graph → YAML byte-comparably; (c) the two-level
editor model expresses the recipes in the repository. Run (b) as a batch over *every*
recipe in ESMValTool and count failures. If round-tripping is worse than expected, the
scope changes, and better to learn it now.

**Phase 1 — offline builder MVP.** Canvas, profile editor with canonical order and the
`custom_order` toggle, preprocessor catalogue with docs, live YAML preview, save/load,
validation layers 1–2. Ships value on its own with no connectivity at all.

**Phase 2 — recipe library.** Browse bundled recipes, load into the canvas, edit,
save-as. This is the "start from something that works" workflow and is likely the
feature users adopt first — consider pulling it earlier if early feedback agrees.

**Phase 3 — local execution.** Run a recipe locally, stream logs, surface outputs,
validation layer 3. Proves the execution interface before the HPC complexity lands.

**Phase 4 — HPC / Gadi.** Remote backend over SSH tunnel, data discovery from site
config, PBS submission with queue-constraint validation, job monitoring.

**Phase 5 — hardening.** Packaging to conda-forge, docs, version-skew handling,
optional Tauri shell if wanted.

Phases 1–3 are useful shipped alone; a user with a local ESMValTool install gets a
working builder and runner before any HPC work starts.

---

## 5. Risks

| Risk | Mitigation |
|---|---|
| **Round-trip fidelity** — recipes are hand-edited and comment-rich | ruamel.yaml; batch round-trip test over all bundled recipes in Phase 0 |
| **Graph metaphor misleads** — users assume wiring order is execution order | two-level editor; canonical order shown explicitly; `custom_order` surfaced as a real, explained control |
| **Introspection gaps** — inconsistent docstrings, `**kwargs`, dynamic signatures | audit in Phase 0; allow a small hand-written override file for stragglers |
| **Version skew** — GUI vs installed ESMValCore | catalogue generated from the installed package; assert a supported version range at startup |
| **Diagnostics are multi-language** (Python/R/NCL/Julia) | treat the diagnostics catalogue as a separate, metadata-driven problem — do not assume introspection works |
| **Gadi specifics** (queues, `storage` flags, ARE) | keep site rules in data, not code, so other HPC sites are a config file rather than a fork |
| **Scope** — this is a large project | Phases 1–2 are independently valuable; treat HPC as a distinct milestone |

---

## 6. Recommended next step

Run Phase 0 before committing to the stack. The round-trip test over the bundled
recipes is a few hundred lines and will tell you more about feasibility than any
further design discussion.
