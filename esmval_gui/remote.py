from __future__ import annotations

import json
import os
import posixpath
import re
import shlex
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


HOST_RE = re.compile(r"^[A-Za-z0-9_.@-]+$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
STORAGE_RE = re.compile(r"^(?:gdata|scratch)/[A-Za-z0-9_-]+(?:\+(?:gdata|scratch)/[A-Za-z0-9_-]+)*$")
COMMAND_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
JOB_RE = re.compile(r"^[0-9]+(?:\.[A-Za-z0-9_.-]+)?$")


def execution_mode() -> str:
    """Local PBS is enabled by the ARE server, never by a browser request."""
    return "local" if os.environ.get("ESMVAL_GUI_EXECUTION") == "local" else "ssh"


def execute(host: str, command: str, data: str | None = None, timeout: int = 35,
            *, transport: str | None = None) -> str:
    mode = transport or execution_mode()
    if mode == "ssh":
        return ssh(host, command, data, timeout=timeout)
    if mode != "local" or execution_mode() != "local":
        raise RuntimeError("This run uses direct PBS on ARE. Open Studio on ARE to manage it.")
    try:
        result = subprocess.run(["/bin/bash", "-c", command],
                                input=data.encode("utf-8") if data is not None else None,
                                capture_output=True, timeout=timeout, check=False,
                                cwd=Path.home())
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Gadi command timed out after {timeout} seconds") from exc
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"Could not run the Gadi command: {exc}") from exc
    stdout = result.stdout.decode("utf-8", errors="replace").strip()
    if result.returncode:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError((stderr or stdout)[-1500:] or f"Gadi command exit {result.returncode}")
    return stdout


class RemoteSettings(BaseModel):
    host: str = "gadi.nci.org.au"
    username: str = ""
    project: str = ""
    queue: str = "normal"
    ncpus: int = Field(default=1, ge=1, le=48)
    memory_gb: int = Field(default=4, ge=1, le=190)
    walltime: str = "00:20:00"
    jobfs_gb: int = Field(default=10, ge=1, le=400)
    storage: str = ""
    work_dir: str = ""
    esmvaltool_command: str = "esmvaltool"
    setup_command: str = ""
    config_dir: str = ""
    config_file: str = ""

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        if not HOST_RE.fullmatch(value) or value.startswith("-"):
            raise ValueError("Use an SSH hostname or alias")
        return value

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        if value and not USERNAME_RE.fullmatch(value):
            raise ValueError("Use a valid SSH username")
        return value

    @property
    def ssh_target(self) -> str:
        return ssh_target(self.host, self.username)

    @field_validator("project")
    @classmethod
    def validate_project(cls, value: str) -> str:
        if value and not CODE_RE.fullmatch(value):
            raise ValueError("Use a valid NCI project code")
        return value

    @field_validator("queue")
    @classmethod
    def validate_queue(cls, value: str) -> str:
        if value not in ("normal", "express", "normalbw", "expressbw"):
            raise ValueError("Select a supported Gadi queue")
        return value

    @field_validator("walltime")
    @classmethod
    def validate_walltime(cls, value: str) -> str:
        match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2})", value)
        if not match or int(match[2]) > 59 or int(match[3]) > 59 or int(match[1]) > 48:
            raise ValueError("Use HH:MM:SS walltime, at most 48 hours")
        return value

    @field_validator("storage")
    @classmethod
    def validate_storage(cls, value: str) -> str:
        if value and not STORAGE_RE.fullmatch(value):
            raise ValueError("Use storage like gdata/ab12+scratch/ab12")
        return value

    @field_validator("work_dir", "config_dir", "config_file")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if value and (not value.startswith("/") or not COMMAND_RE.fullmatch(value) or ".." in value.split("/")):
            raise ValueError("Use an absolute Gadi path containing letters, numbers, /, _, - or .")
        return value.rstrip("/")

    @field_validator("esmvaltool_command")
    @classmethod
    def validate_command(cls, value: str) -> str:
        if not COMMAND_RE.fullmatch(value):
            raise ValueError("Use an executable name or absolute path")
        return value

    @field_validator("setup_command")
    @classmethod
    def validate_setup(cls, value: str) -> str:
        if any(ch in value for ch in "\r\n\0"):
            raise ValueError("Setup command must be one line")
        return value


def validate_resources(settings: RemoteSettings) -> None:
    if not settings.project:
        raise ValueError("NCI project is required")
    if not settings.work_dir:
        raise ValueError("Remote working directory is required")
    if settings.queue.startswith("express") and settings.walltime > "02:00:00":
        raise ValueError("Express jobs must request at most 2 hours")
    if settings.ncpus == 1 and settings.memory_gb > 32:
        raise ValueError("For a 1 CPU job, request at most 32 GB or increase CPUs")
    if settings.config_dir and settings.config_file:
        raise ValueError("Choose either a config directory or a config file")


def check_local_paths(settings: RemoteSettings) -> list[dict]:
    """Check visible mounts and write permissions without creating anything."""
    checks = []
    work = Path(settings.work_dir)
    for path in (settings.work_dir, settings.config_dir, settings.config_file, settings.esmvaltool_command):
        match = re.match(r"^/(g/data|scratch)/([A-Za-z0-9_-]+)(?:/|$)", path)
        if match and not Path(f"/{match[1]}/{match[2]}").is_dir():
            mount = f"{'gdata' if match[1] == 'g/data' else 'scratch'}/{match[2]}"
            checks.append({"level": "error", "label": "ARE storage",
                           "detail": f"{mount} is unavailable in this session. Include it in ARE Storage and launch a new session."})
    parent = work
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    writable = parent.is_dir() and os.access(parent, os.W_OK | os.X_OK)
    checks.append({"level": "ok" if writable else "error", "label": "Working directory",
                   "detail": f"Can create runs under {work}" if writable else f"Cannot write under {work}. Check the path, ARE Storage mounts and permissions."})
    return checks


def script_for(settings: RemoteSettings, recipe_path: str, remote_dir: str) -> str:
    validate_resources(settings)
    # Every directive value is validated before interpolation; the executable and paths
    # in shell commands are quoted separately below.
    lines = [
        "#!/bin/bash",
        f"#PBS -P {settings.project}",
        f"#PBS -q {settings.queue}",
        f"#PBS -l ncpus={settings.ncpus}",
        f"#PBS -l mem={settings.memory_gb}GB",
        f"#PBS -l jobfs={settings.jobfs_gb}GB",
        f"#PBS -l walltime={settings.walltime}",
    ]
    storage = list(filter(None, settings.storage.split("+")))
    for path in (remote_dir, settings.esmvaltool_command, settings.config_file, settings.config_dir):
        match = re.match(r"^/(g/data|scratch)/([A-Za-z0-9_-]+)(?:/|$)", path)
        if match:
            item = f"{'gdata' if match[1] == 'g/data' else 'scratch'}/{match[2]}"
            if item not in storage:
                storage.append(item)
    if storage:
        lines.append(f"#PBS -l storage={'+'.join(storage)}")
    lines += [
        f"#PBS -o {remote_dir}/pbs-stdout.txt",
        f"#PBS -e {remote_dir}/pbs-stderr.txt",
        "set -euo pipefail",
        f"cd {shlex.quote(remote_dir)}",
        "umask 077",
        "exec > stdout.txt 2> stderr.txt",
        "trap 'studio_exit=$?; printf \"%s\\n\" \"$studio_exit\" > .studio-exit-code.tmp; mv .studio-exit-code.tmp .studio-exit-code' EXIT",
        "mkdir -p output",
        f"export OMP_NUM_THREADS={settings.ncpus}",
    ]
    if settings.setup_command:
        lines.append(settings.setup_command)
    if settings.config_dir:
        lines.append(f"export ESMVALTOOL_CONFIG_DIR={shlex.quote(settings.config_dir)}")
    command = shlex.quote(settings.esmvaltool_command)
    # Gadi's default analysis module can leave ESMFMKFILE pointing at a
    # different conda environment. Prefer the mk file beside the selected
    # ESMValTool executable when that environment provides one.
    lines += [
        f"esmval_executable=$(command -v {command} || true)",
        'if [ -n "$esmval_executable" ]; then',
        '  esmval_prefix=$(dirname "$(dirname "$esmval_executable")")',
        '  if [ -f "$esmval_prefix/lib/esmf.mk" ]; then',
        '    export ESMFMKFILE="$esmval_prefix/lib/esmf.mk"',
        '  fi',
        'fi',
    ]
    args = ["run"]
    if settings.config_file:
        args += ["--config_file", settings.config_file]
    elif settings.config_dir:
        args += ["--config_dir", settings.config_dir]
    args += ["--output_dir", posixpath.join(remote_dir, "output"), recipe_path]
    lines.append(" ".join([command] + [shlex.quote(arg) for arg in args]))
    return "\n".join(lines) + "\n"


def ssh_target(host: str, username: str = "") -> str:
    if not HOST_RE.fullmatch(host) or host.startswith("-"):
        raise ValueError("Use an SSH hostname or alias")
    if username and (not USERNAME_RE.fullmatch(username) or "@" in host):
        raise ValueError("Use a hostname without @ when entering a separate SSH username")
    return f"{username}@{host}" if username else host


def ssh_args(host: str) -> list[str]:
    # Keep user aliases, keys and ProxyJump settings. /dev/null is a POSIX path;
    # on Windows, OpenSSH reads its normal user config when -F is omitted.
    config = Path.home() / ".ssh" / "config"
    args = ["ssh", "-T"]
    if config.is_file():
        args += ["-F", str(config)]
    elif sys.platform != "win32":
        args += ["-F", "/dev/null"]
    return args + ["-o", "BatchMode=yes", "-o", "ForwardX11=no", "-o", "ConnectTimeout=10", host]


def ssh(host: str, command: str, data: str | None = None, timeout: int = 35) -> str:
    try:
        payload = data.encode("utf-8") if data is not None else None
        result = subprocess.run(ssh_args(host) + [command], input=payload, capture_output=True,
                                timeout=timeout, check=False)
    except UnicodeError as exc:
        raise RuntimeError("Recipe or script text could not be encoded as UTF-8") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("OpenSSH client (ssh) was not found on the GUI server. Install or enable it and add ssh to PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"SSH to {host} timed out after {timeout} seconds") from exc
    except OSError as exc:
        raise RuntimeError(f"Could not start SSH on the GUI server: {exc}") from exc
    stdout = result.stdout.decode("utf-8", errors="replace").strip()
    if result.returncode:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError((stderr or stdout)[-1500:] or f"SSH exit {result.returncode}")
    return stdout


def probe(host: str, command: str = "esmvaltool", setup: str = "") -> dict:
    if not HOST_RE.fullmatch(host) or host.startswith("-"):
        raise ValueError("Invalid SSH host")
    if not COMMAND_RE.fullmatch(command):
        raise ValueError("Invalid executable")
    if any(ch in setup for ch in "\r\n\0"):
        raise ValueError("Invalid setup command")
    preamble = f"set -e; {setup}; " if setup else ""
    executable_check = f"command -v {shlex.quote(command)} 2>/dev/null || true; "
    if command == "esmvaltool":
        executable_check += (
            'if ! command -v esmvaltool >/dev/null 2>&1 && test -r "$HOME/.conda/environments.txt"; then '
            'while IFS= read -r env; do if test -x "$env/bin/esmvaltool"; then '
            'printf "%s\\n" "$env/bin/esmvaltool"; break; fi; '
            'done < "$HOME/.conda/environments.txt"; fi'
        )
    remote = (
        "printf 'HOME=%s\\nPROJECT=%s\\nUSER=%s\\n' \"$HOME\" \"$PROJECT\" \"$USER\"; "
        "command -v qsub >/dev/null && echo PBS=yes || echo PBS=no; "
        'if test -f "/scratch/$PROJECT/$USER/esmval-gui-jobs/config/config-user-legacy.yml"; then '
        'printf "CONFIG_FILE=%s\\n" "/scratch/$PROJECT/$USER/esmval-gui-jobs/config/config-user-legacy.yml"; '
        'elif test -f "$HOME/.config/esmvaltool/config-user.yml"; then '
        'printf "CONFIG_FILE=%s\\n" "$HOME/.config/esmvaltool/config-user.yml"; '
        'elif test -f "$HOME/.esmvaltool/config-user.yml"; then '
        'printf "CONFIG_FILE=%s\\n" "$HOME/.esmvaltool/config-user.yml"; fi; '
        + executable_check
    )
    raw = execute(host, preamble + remote)
    values = {}
    for line in raw.splitlines():
        if "=" in line and line.split("=", 1)[0] in ("HOME", "PROJECT", "USER", "PBS", "CONFIG_FILE"):
            key, value = line.split("=", 1)
            values[key.lower()] = value
        elif line.startswith("/"):
            values["esmvaltool_path"] = line
    config_file = values.get("config_file")
    if config_file and config_file.startswith("/"):
        try:
            roots = execute(host, f"grep -Eo '/g/data/[A-Za-z0-9_-]+' {shlex.quote(config_file)} | sort -u || true")
            values["storage_paths"] = [path.replace("/g/data/", "gdata/", 1) for path in roots.splitlines()
                                       if re.fullmatch(r"/g/data/[A-Za-z0-9_-]+", path)]
        except RuntimeError:
            values["storage_paths"] = []
    values["execution_mode"] = execution_mode()
    if values.get("esmvaltool_path", "").startswith("/"):
        try:
            runtime = inspect_scripts(host, values["esmvaltool_path"], [], setup)
            values["config_mode"] = runtime.get("config_mode")
            values["versions"] = runtime.get("versions", {})
            if values["config_mode"] == "directory" and values.get("config_file"):
                values["config_dir"] = posixpath.dirname(values.pop("config_file"))
        except ValueError:
            pass
    return values


_SCRIPT_CHECK = r'''
import ast, importlib.metadata, importlib.util, json, pathlib, sys
paths = json.loads(sys.argv[1])
spec = importlib.util.find_spec("esmvaltool")
root = pathlib.Path(next(iter(spec.submodule_search_locations))) / "diag_scripts" if spec and spec.submodule_search_locations else None
versions = {}
for package in ("ESMValTool", "ESMValCore"):
    try: versions[package] = importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError: versions[package] = None
steps = None
config_mode = None
try:
    core = importlib.util.find_spec("esmvalcore")
    core_root = pathlib.Path(next(iter(core.submodule_search_locations)))
    source = core_root / "preprocessor" / "__init__.py"
    for node in ast.parse(source.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
            steps = ast.literal_eval(node.value)
            break
    for node in ast.walk(ast.parse((core_root / "_main.py").read_text(encoding="utf-8"))):
        if isinstance(node, ast.FunctionDef) and node.name == "run" and node.args.args:
            names = {arg.arg for arg in node.args.args + node.args.kwonlyargs}
            config_mode = "file" if "config_file" in names else "directory"
            break
except Exception:
    pass
print(json.dumps({"root": str(root) if root else None,
                  "scripts": {name: bool(root and (root / name).is_file()) for name in paths},
                  "versions": versions, "preprocessor_steps": steps, "config_mode": config_mode}))
'''.strip()


def inspect_scripts(host: str, executable: str, paths: list[str], setup: str = "") -> dict:
    """Inspect installed scripts and preprocessor API without starting a job."""
    if not executable.startswith("/") or not COMMAND_RE.fullmatch(executable):
        raise ValueError("An absolute ESMValTool executable is needed to check installed scripts")
    if any(path.startswith("/") or ".." in path.split("/") for path in paths):
        raise ValueError("Installed script checks require relative paths")
    if any(ch in setup for ch in "\r\n\0"):
        raise ValueError("Invalid setup command")
    python = str(Path(executable).parent / "python")
    command = f"{shlex.quote(python)} -c {shlex.quote(_SCRIPT_CHECK)} {shlex.quote(json.dumps(paths[:100]))}"
    if setup:
        command = f"set -e; {setup}; " + command
    try:
        return json.loads(execute(host, command, timeout=45))
    except (json.JSONDecodeError, RuntimeError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"Could not inspect diagnostic scripts: {exc}") from exc


def submit(settings: RemoteSettings, recipe: str, state_dir: Path, title: str = "recipe.yml") -> dict:
    validate_resources(settings)
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:12]
    remote_dir = posixpath.join(settings.work_dir, run_id)
    recipe_path = posixpath.join(remote_dir, "recipe.yml")
    job_path = posixpath.join(remote_dir, "job.pbs")
    script = script_for(settings, recipe_path, remote_dir)
    remote = shlex.quote(remote_dir)
    for stage, command, data in (
        ("create the working directory", f"mkdir -p {remote} && chmod 700 {remote}", None),
        ("upload the recipe", f"cat > {shlex.quote(recipe_path)}", recipe),
        ("upload the PBS script", f"cat > {shlex.quote(job_path)}", script),
    ):
        try:
            execute(settings.ssh_target, command, data)
        except RuntimeError as exc:
            raise RuntimeError(f"Could not {stage} on Gadi: {exc}") from exc
    try:
        response = execute(settings.ssh_target, f"qsub {shlex.quote(job_path)}")
    except RuntimeError as exc:
        raise RuntimeError(f"Could not queue the PBS job on Gadi: {exc}. If the connection or command timed out during qsub, check qstat before retrying.") from exc
    job_id = response.splitlines()[-1].strip()
    if not JOB_RE.fullmatch(job_id):
        raise RuntimeError(f"qsub returned an unexpected job ID: {response}")
    record = {
        "id": run_id, "job_id": job_id, "remote_dir": remote_dir,
        "host": settings.ssh_target, "title": title[:200],
        "transport": execution_mode(), "output_dir": posixpath.join(remote_dir, "output"),
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "project": settings.project, "queue": settings.queue,
    }
    (state_dir / f"{run_id}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def read_job(state_dir: Path, run_id: str) -> dict:
    if not re.fullmatch(r"[a-f0-9]{12}", run_id):
        raise FileNotFoundError("Job not found")
    target = state_dir / f"{run_id}.json"
    if not target.is_file():
        raise FileNotFoundError("Job not found")
    return json.loads(target.read_text(encoding="utf-8"))


def status(record: dict) -> dict:
    if record.get("cancelled"):
        return {**record, "state": "cancelled", "exit_status": None}
    job_id = record["job_id"]
    if not JOB_RE.fullmatch(job_id):
        raise ValueError("Invalid stored job ID")
    try:
        raw = execute(record["host"], f"qstat -fx {shlex.quote(job_id)} 2>&1", timeout=25,
                      transport=record.get("transport", "ssh"))
    except RuntimeError as exc:
        # PBS eventually expires completed jobs from its history. Preserve the
        # exit code in the run directory so old runs remain useful in Studio.
        try:
            marker = shlex.quote(posixpath.join(record["remote_dir"], ".studio-exit-code"))
            code = execute(record["host"], f"if test -f {marker}; then head -c 16 {marker}; fi",
                           timeout=10, transport=record.get("transport", "ssh"))
            if re.fullmatch(r"-?\d+", code):
                return {**record, "state": "finished" if int(code) == 0 else "failed", "exit_status": int(code)}
        except (KeyError, RuntimeError):
            pass
        return {**record, "state": "unknown", "detail": str(exc)}
    match = re.search(r"\bjob_state\s*=\s*([A-Z])", raw)
    exit_match = re.search(r"\bExit_status\s*=\s*(-?\d+)", raw)
    state = {"Q": "queued", "R": "running", "H": "held", "F": "finished", "E": "exiting"}.get(match[1], "unknown") if match else "unknown"
    if state == "finished" and exit_match and int(exit_match[1]) != 0:
        state = "failed"
    return {**record, "state": state, "exit_status": int(exit_match[1]) if exit_match else None}


def cancel(state_dir: Path, record: dict) -> dict:
    job_id = record["job_id"]
    if not JOB_RE.fullmatch(job_id):
        raise ValueError("Invalid stored job ID")
    current = status(record)
    if current["state"] in ("finished", "failed", "cancelled"):
        raise ValueError("Job is already finished or cancelled")
    execute(record["host"], f"qdel {shlex.quote(job_id)}", transport=record.get("transport", "ssh"))
    record["cancelled"] = True
    (state_dir / f"{record['id']}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return {**record, "state": "cancelled"}


def logs(record: dict) -> dict:
    directory = record["remote_dir"]
    result = {}
    for key in ("stdout", "stderr"):
        paths = [shlex.quote(posixpath.join(directory, f"{prefix}{key}.txt")) for prefix in ("", "pbs-")]
        try:
            command = "; ".join(f"if test -f {path}; then tail -c 20000 {path}; fi" for path in paths)
            result[key] = execute(record["host"], command, timeout=25, transport=record.get("transport", "ssh"))
        except RuntimeError as exc:
            result[key] = f"Unable to read log: {exc}"
    return result
