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
| Storage | Include `gdata/xp65`, plus any data/configuration filesystems needed |
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
Studio tile. Editor functions were tested in ARE, but job submission through
the existing SSH backend still needs resolving before end-to-end use.

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

- Job submission still uses Studio's existing SSH backend. Before submitting,
  use **Check Gadi connection** and **Review & check**. The ARE node must reach
  the configured login host and authenticate non-interactively. The editor
  pilot does not provision SSH keys or an ESMValTool runtime, and does not
  establish that submitting jobs from ARE works for your account.
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
parsing, rejection of unauthenticated requests and missing XSRF headers, and
rejection of a configuration without a password. It does not submit a PBS job.

The approach follows [Scott Wales's Marimo example](https://forum.access-hive.org.au/t/glitch-in-jupyter-lab-on-are-xp65/5337/20)
and [NCI's direct Cylc launch through Jupyter](https://opus.nci.org.au/spaces/DAE/pages/252674295/Run+Cylc8+Suites).
