# ARE pilot: open Studio from the Jupyter tile

This pilot uses **JupyterLab → Launch → Open JupyterLab** in ARE to open
ESMValTool Studio directly. It does not start a VDI or install a separate
dashboard tile. Each launch is a normal ARE batch job, with the walltime,
project charging, storage mounts and session deletion managed by ARE.

## Shared xp65 installation

The shared pilot is installed at
`/g/data/xp65/public/apps/esmvaltool-studio`, with its modulefile at
`/g/data/xp65/public/modules/esmvaltool-studio`. You need an NCI account with
access to the xp65 filesystem; no personal installation is needed.

Open [ARE](https://are.nci.org.au/pun/sys/dashboard), choose **JupyterLab**, and
select your charging project, resources and walltime. Set the following fields,
expanding **Advanced options** for the module settings:

| Field | Value |
| --- | --- |
| Module directories | `/apps/Modules/modulefiles /g/data/xp65/public/modules` |
| Modules | `python3/3.11.7 esmvaltool-studio` |
| Storage | Include `gdata/xp65`, your working directory's `scratch/PROJECT`, and any data/configuration filesystems needed |
| Python or Conda virtual environment base | Leave empty |
| Conda environment | Leave empty |
| Extra Jupyter arguments / Pre-script | Leave empty |

Click **Launch**, then **Open JupyterLab** when running. Studio opens directly
using ARE's session login; no second Studio access token is needed. Start a new
session if an existing one was launched with different module settings.

The application, Python environment and source-only recipe library are shared
and writable only by the installation owner (`xp65_ci`). The xp65 group has
read and execute access. Session credentials remain in each user's own account,
and job records use `~/.local/state/esmvaltool-studio-are`. Users do not copy
application files into `~/ondemand`; ARE creates its session files there.

Download edited recipes before the session expires. End the session using
**Delete** in ARE's Interactive Sessions when finished; closing the browser tab
does not stop the job.

The installed modulefile uses an explicit `studio_root` pointing at the shared
installation, instead of the relative path used by the repository's modulefile.
Recreate `.are-venv` at the final installation path when reinstalling; do not move
an existing virtual environment. In the deployed copy, the tested versions are
recorded in `.are-venv/installed-requirements.txt`, and
`deploy/are/pilot-constraints.txt` records the dependency versions used for this
deployment. Keep group read and directory/executable access on the installed
files, without group write access.

This uses the existing JupyterLab tile. It does not register a separately named
Studio tile. Start a new ARE session after an application or module update.

## Run diagnostics and view results

Studio uses PBS directly from the ARE node, as the logged-in NCI user. No SSH
key or connection back to a login node is needed. The ARE session hosts the UI;
each submitted diagnostic is a separate PBS job charged to the project chosen
in Studio. Ending the ARE session does not cancel those diagnostic jobs.

1. Open **Submit to Gadi** and select **Check Gadi environment**. The shared module
   defaults to `conda/analysis3-26.10` (ESMValTool/ESMValCore 2.15). Its environment
   setup includes the module paths required by the Singularity wrappers. You
   can choose another installed runtime using the setup and executable fields.
2. Choose your project and a writable working directory, for example
   `/scratch/PROJECT/USERNAME/esmval-studio-runs`. Include that filesystem in
   both the ARE session's **Storage** and the submitted job's **Storage**.
3. Set **Config directory** to the directory containing your ESMValCore YAML
   settings. Leave **Config file** blank for ESMValCore 2.15. The selected
   directory also sets `ESMVALTOOL_CONFIG_DIR` in the job, preventing a module's
   default site configuration from being merged unexpectedly. Include all
   data filesystems referenced by the recipe/configuration in job **Storage**.
4. Select resources, **Review & check**, then **Queue job**. Preflight checks
   runtime/configuration access, PBS, working-directory permissions and visible
   project mounts. It does not establish that the exact climate input files exist.
5. In **Runs**, select the job to see its status and live logs. Active selected
   runs refresh every 30 seconds while the page is visible. Use **Cancel job**
   when needed; completed jobs remain in the run history.
6. Under **Outputs**, filter or search the file list. Images and PDF pages render
   inside Studio; YAML, provenance and other text files show as text. **Download**
   returns the original file, including NetCDF data or a complete PDF.

Each run contains `recipe.yml`, `job.pbs`, live `stdout.txt`/`stderr.txt`, PBS spool
logs, an exit-code marker and `output/`. The output directory is passed explicitly
to ESMValTool. The marker preserves completion status after PBS history expires.
Results remain on Gadi and can be viewed in a later ARE session with the same
account and storage mounts, subject to the filesystem's retention policy.

Previews are restricted to regular files inside a registered ARE run's output
directory. Symlinks are excluded. Listings stop at 1,000 files or 10,000 scanned
entries; image/PDF previews are limited to 25 MiB and text previews to 200 kB.
PDF pages are rendered to images in a separate bounded process. Downloads keep
the original contents. This viewer does not provide interactive NetCDF analysis
or previews for older runs submitted using SSH.

## Install a private copy

For a separate installation, copy this repository to a private directory on
Gadi, then run:

```bash
module use /apps/Modules/modulefiles
module load python3/3.11.7
bash /absolute/path/to/studio/deploy/are/install.sh
```

The installer creates `.are-venv` inside that copy. It does not edit your shell
startup files, your existing Jupyter installation, or `~/ondemand` session data.
It needs outbound package-download access during installation, but not at launch.
The tested package versions are recorded in `.are-venv/installed-requirements.txt`.

Optionally put a source-only ESMValTool checkout in `library/`, with
`library/esmvaltool/recipes`, `library/esmvaltool/diag_scripts` and
`library/doc/sphinx/source`. The module detects this recipe and documentation
library. It is not an ESMValTool runtime for running diagnostics. Alternatively,
set `ESMVAL_GUI_RECIPE_ROOT` and `ESMVAL_GUI_DOCS_ROOT` in ARE's environment field.

### Launch the private copy from ARE

Open <https://are.nci.org.au/pun/sys/dashboard> and choose **JupyterLab**.
For a first short test, choose **1 hour**, the smallest compute size offered for
your selected queue, and your usual NCI project. In **Advanced options**, enter:

| Field | Value |
| --- | --- |
| Module directories | `/apps/Modules/modulefiles /absolute/path/to/studio/deploy/are/modulefiles` |
| Modules | `python3/3.11.7 esmvaltool-studio` |
| Python or Conda virtual environment base | Leave empty |
| Conda environment | Leave empty |
| Extra Jupyter arguments / Pre-script | Leave empty |

Include every project filesystem needed by the session in **Storage**. For a
home-directory installation with a bundled recipe library, the editor does not
require `gdata/xp65`. Reading project configuration/data or testing job access
requires the corresponding storage mounts.

Click **Launch**, then **Open JupyterLab** when running. ARE supplies its session
password automatically; the resulting page should be Studio. No second Studio
access token is needed. Opening a copied URL without the ARE session login can
instead display Jupyter's password page: use the ARE launch button.

Try browsing a recipe, editing its YAML or pipeline, checking its structure,
and downloading it. End the session using **Delete** in ARE's Interactive
Sessions when finished; closing the browser tab does not stop the job.

## Pilot boundaries

- An ARE session cannot acquire additional filesystem mounts after launch. If
  preflight reports missing storage, launch a new session with the required
  mounts. The PBS script adds storage for explicit working/configuration/runtime
  paths; recipe data storage must still be selected by the user.
- The shared scientific runtime is separate from Studio's Python environment.
  Private installations must configure an available ESMValTool runtime when the
  shared `analysis3-26.10` module is unavailable.
- ESGF search needs outbound HTTPS access from the ARE environment. Recipe
  editing works without it.
- Job records persist in `~/.local/state/esmvaltool-studio-are`. Browser drafts,
  access tokens and submission settings are scoped to the session URL. Save a
  recipe before the session expires.
- The wrapper supports the `jupyter lab --config=...` command generated by ARE,
  including its file-browser root argument. It intentionally loads only this
  session configuration and the Studio proxy, not personal Jupyter extensions.

## Authentication and routing

ARE's generated hashed password remains enforced by Jupyter. Only its login
handler accepts ARE's first password POST without a prior XSRF cookie; other
Jupyter endpoints retain XSRF checks. The successful login issues the cookie
used for subsequent Studio writes.

Jupyter Server Proxy launches Studio on a Unix socket inside a private temporary
directory and injects a fresh API token server-side. The browser never receives
that token. Studio additionally checks the XSRF cookie/header on API writes.
The backend receives the correct `/node/HOST/PORT/studio` root path, and assets,
API calls and documentation figures stay under that prefix.

For errors, open the session ID in ARE and inspect its output log. A missing
`jupyter`/module message usually means the module directory or module order is
wrong. A login redirect followed by a 502 indicates a Studio subprocess error;
look for the Python traceback. Do not share the generated `config.py` or
connection password publicly.

## Verification

```bash
.are-venv/bin/python -m unittest discover -s tests -v
```

The optional `test_are.py` test starts a temporary loopback server and exercises
the real password login, redirects, proxied assets/API/figures, successful recipe
parsing, authenticated output previews/downloads, rejection of unauthenticated
requests and missing XSRF headers, and rejection of a configuration without a
password. Execution tests run a generated PBS script with a fake scheduler to
check logs, output paths, exit status and transport selection. The suite does
not submit a real PBS job.

On 11 November 2026, all 58 tests passed on Gadi. A separate synthetic diagnostic
was submitted from an ARE compute node using ESMValTool/ESMValCore 2.15, completed
with exit code 0, and produced PNG, PDF, provenance and NetCDF outputs. The
deployed output handlers listed those results and rendered the PDF successfully.
Cancellation was checked on a queued validation job. This validates execution
and output handling; it does not validate a scientific recipe or climate dataset.

The approach follows [Scott Wales's Marimo example](https://forum.access-hive.org.au/t/glitch-in-jupyter-lab-on-are-xp65/5337/20)
and [NCI's direct Cylc launch through Jupyter](https://opus.nci.org.au/spaces/DAE/pages/252674295/Run+Cylc8+Suites).
