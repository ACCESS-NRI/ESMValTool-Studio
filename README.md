# ESMValTool Studio

A local GUI for browsing ESMValTool recipes, building preprocessor profiles, assigning them to variables, and submitting bounded PBS jobs to Gadi over your existing SSH connection.

## Deploy locally

The GUI runs on your workstation or a trusted login machine. Gadi only needs an existing ESMValTool installation; the GUI sends recipes and PBS scripts there over SSH. Use Python 3.11 or newer, `git`, and OpenSSH. An ESMValTool or ESMValCore checkout on the GUI machine is optional, but provides the recipe library, local documentation, and the current preprocessor catalogue.

From a checkout of this repository:

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -q
.venv/bin/python -m esmval_gui
```

The app opens at <http://127.0.0.1:8765>. Use `--no-browser` to start it without opening a tab, or `--port 8766` to choose another port. On startup, the server prints a random access token in the terminal. Enter that token in the browser to unlock the GUI; it is kept in the tab's session storage and changes when the server restarts. For a managed deployment, set `ESMVAL_GUI_ACCESS_TOKEN` to a secret of at least 32 characters before starting the server. Do not put the token in a URL or a shared log.

The server only accepts a loopback bind address. The API can submit and cancel Gadi jobs through your SSH account, so keep it on a trusted personal machine. For a remote GUI host, connect through an SSH tunnel instead of exposing the port:

```bash
# On your own computer, after starting the GUI on the remote host:
ssh -L 8765:127.0.0.1:8765 user@your-gui-host
```

Open <http://127.0.0.1:8765> in a browser on your computer. The GUI host needs outbound HTTPS access for ESGF lookup and outbound SSH access to Gadi for submission.

### Optional paths and state

Set these environment variables before starting the server when your checkouts are elsewhere:

| Variable | Purpose |
| --- | --- |
| `ESMVAL_GUI_RECIPE_ROOT` | ESMValTool `esmvaltool/recipes` directory |
| `ESMVAL_GUI_CORE_ROOT` | ESMValCore source/package directory for preprocessor documentation |
| `ESMVAL_GUI_DOCS_ROOT` | ESMValTool `doc/sphinx/source` directory for recipe descriptions and figures |
| `ESMVAL_GUI_ESGF_SEARCH_URL` | Trusted ESGF Search API endpoint; defaults to DKRZ |
| `ESMVAL_GUI_STATE_DIR` | Directory for local job records; defaults to `.esmval-gui/` in this checkout |

For example:

```bash
export ESMVAL_GUI_RECIPE_ROOT=/path/to/ESMValTool/esmvaltool/recipes
export ESMVAL_GUI_CORE_ROOT=/path/to/ESMValCore/esmvalcore
.venv/bin/python -m esmval_gui --no-browser
```

### Connect to Gadi

Configure an SSH host or alias in `~/.ssh/config` on the GUI host, then check that non-interactive SSH works:

```bash
ssh -o BatchMode=yes your-gadi-alias 'command -v qsub'
```

Enter that alias in the GUI's **Submit to Gadi** dialog and use **Check Gadi connection**. The Gadi account must have an NCI project, PBS access, an `esmvaltool` executable, and an ESMValTool user config with the data roots needed by the recipe. Set the executable path or a one-line environment setup command in the dialog if it is not already on `PATH`. If the SSH private key has a passphrase, unlock it in your operating system's SSH agent first (for example, `ssh-add /path/to/private-key` in a terminal). The GUI uses noninteractive SSH and never requests, receives, or stores the private key or passphrase. In the dialog, choose the remote working directory, storage projects, and config file or directory before submitting.

The application is run from this checkout rather than installed as a Python package. If you update the checkout later, restart the server to load backend changes.

The recipe library is discovered from an installed `esmvaltool` Python package, or from a sibling `ESMValTool/ESMValTool/esmvaltool/recipes` checkout. Set `ESMVAL_GUI_RECIPE_ROOT=/path/to/recipes` to choose another collection. You can also open any local `.yml` or `.yaml` recipe from the browser. Use the **Realm** filter to browse recipes by the realms declared in their diagnostics; a recipe may appear under several realms. Recipes without a declared realm are listed as **Realm unspecified**.

Select **New recipe** to build a recipe from scratch. The guided form asks for documentation (including an author), one input dataset, and a first diagnostic and variable. It previews the YAML before creation. A script is optional while drafting; the guide opens a script form in the diagnostic inspector when one is needed. The script fields include a searchable picker of diagnostic scripts used by recipes in the local ESMValTool collection, with brief descriptions when available. After creation, the pipeline offers shortcuts for more datasets, variables and diagnostics. It also leads through creating a preprocessor profile and assigning that profile to a variable. Use **Download YAML** to keep the new recipe as a local file.

When adding a dataset in either the new recipe builder or pipeline, use **Find on ESGF** to search by model name. Choose a project and optionally narrow by experiment, variable, or ensemble. Selecting a published record fills the editable dataset, project, experiment, ensemble, grid and MIP table fields. The new recipe builder also fills the first variable from the chosen record. Search includes current ESGF replicas and deduplicates identical records; it is a catalogue lookup, not a check that matching files exist on Gadi. Manual entry remains available if the index is unavailable or a dataset is not indexed. The default search node is DKRZ; set `ESMVAL_GUI_ESGF_SEARCH_URL` to a trusted ESGF Search API endpoint to use a different index.

The **About this recipe** panel displays the recipe's full description, source path, authors, maintainers, projects and references. Author names and citation titles are resolved from the adjacent ESMValTool `config-references.yml` and `references/*.bibtex` files. When a matching Sphinx page exists, the panel adds its overview when it gives more detail, links to the page and displays an example figure from the local documentation. Set `ESMVAL_GUI_DOCS_ROOT=/path/to/doc/sphinx/source` if the documentation is elsewhere. Figures from pages covering several recipes are identified as shared examples. If the local documentation is unavailable, the recipe metadata remains visible without a figure.

## Build preprocessors

The **Pipeline** separates datasets, named preprocessor profiles, variables and diagnostics. Select any card to trace its connected path with lines and fade unrelated cards. The inspector edits common settings as fields; **Advanced YAML** handles nested or uncommon settings. Save a change to update the in-memory recipe. You can rename variable groups, profiles and diagnostics. Profile renames update variable references, and diagnostic renames update `ancestors`. Dataset links include recipe-level datasets and diagnostic or variable `additional_datasets`. Repeated definitions of the same model and project appear as one dataset group; choose the specific definition in the inspector before editing it. Select a variable to assign a profile; select a profile and open the **Preprocessor builder** for guided step editing. The builder can create profiles, search the 60 configurable bricks in the adjacent ESMValCore source, show each brick's description and parameter documentation, add/update/remove steps, and enable custom order with move controls. In standard mode, steps are shown in ESMValCore's execution order. Click **Edit YAML** above the pipeline to edit the entire recipe directly. The pipeline updates as the YAML changes, and syntax or structure issues appear beside the editor. **Download YAML** downloads the current recipe; Ctrl/Cmd+S also downloads it from the editor. Edited drafts are kept in this browser tab's session storage for recovery after a reload, and switching recipes warns before discarding edits.

Use the **+** button in any pipeline column to add a dataset, preprocessor profile, variable, or diagnostic. New datasets can apply to the whole recipe, one diagnostic, or one variable. Variables are added under a chosen diagnostic and can use an existing preprocessor. A new diagnostic can start with an optional script; add variables and further settings afterward in the pipeline or YAML editor. Changes remain in memory until you download the YAML.

The brick catalogue is read from a local ESMValCore source checkout or installed package without importing its heavy runtime. `ESMVAL_GUI_CORE_ROOT=/path/to/esmvalcore` selects another source tree. A bundled catalogue snapshot keeps the builder usable without a checkout. The catalogue describes the local source version, so confirm compatibility with the ESMValCore version installed on Gadi before using newer steps.

## Submit to Gadi

The **Configuration** tab reads `.yml` and `.yaml` ESMValTool settings from several local files, a selected local folder, or one file or directory on Gadi through your configured SSH alias. For Gadi, leave the path blank to use `ESMVALTOOL_CONFIG_DIR` or the standard user configuration location. No job is started when loading configuration. The file list shows merge order; move or remove files to inspect another order. Later files override earlier values while nested mappings merge. The view highlights project data sources, shows the merged settings and their source files, and lists overrides. Configuration text stays in browser memory and is not saved by the GUI. Values with credential-like names are masked in the view.

This is a view of the **selected files**, not a claim about every effective runtime option. ESMValCore defaults, other configuration directories and command line arguments may add or override settings. To inspect the settings intended for a particular Gadi run, load the same directory or file that you enter in the submission dialog, then add any additional configuration files in their intended priority order.

1. Load or open a recipe. Review and edit it in the **Pipeline** and **YAML** tabs.
2. Select **Submit to Gadi**, then **Check Gadi connection**. The app uses your system `ssh` command and SSH config; it does not store keys or passwords. It detects the Gadi project, working directory, PBS and a registered ESMValTool conda environment when available.
3. Set walltime, CPUs, memory, jobfs and storage. Include every `/g/data` and `/scratch` project used by the executable, recipe data and output in **Storage**. Older ESMValCore installations may require **Config file**; the Gadi probe detects an existing `config-user.yml`.
4. Select **Review & check**. The GUI previews the PBS script and checks PBS, the executable, selected configuration, project data roots, installed diagnostic scripts and named preprocessor steps without uploading a recipe or submitting a job. Any environment setup command you entered also runs during the check. Review warnings about anything that could not be checked, then select **Queue job**. Monitor status and output in **Runs**, where a queued or running job can also be cancelled.

The GUI runs `esmvaltool run` inside a PBS job. It uploads a copy of the current YAML to a unique directory under your remote working directory; editing the source recipe never changes the library copy. The GUI records job IDs in `.esmval-gui/` locally. Job output remains on Gadi.

## Scope and limitations

- The pipeline view summarizes datasets, named preprocessor profiles, variables, diagnostics and scripts. ESMValCore chooses preprocessor order unless the recipe enables `custom_order`.
- Node and step editing use `ruamel.yaml` to preserve comments outside the edited definition and comments retained in its editor. Fifteen recipes in the bundled checkout have duplicate YAML keys or merges; the GUI can load, inspect, edit as raw YAML, save and submit those, but does not offer node or step form editing for them.
- **Check structure** validates YAML, basic section shapes, named preprocessors, script paths and exact diagnostic/script ancestor references. The Gadi preflight inspects selected config roots and installed components without starting a job. ESMValCore still validates full recipe semantics and exact input-file availability when a job starts; the GUI does not yet guarantee that every requested facet exists under a configured data root.
- Queue limits can change. PBS is authoritative for queue and project limits; the form applies conservative local bounds and reports `qsub` errors.

Run local checks with `.venv/bin/python -m unittest discover -s tests -v`.
