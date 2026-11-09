#!/bin/bash
# Run from a copy of this repository on Gadi after loading Python 3.11+.
set -euo pipefail
umask 077
studio_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11 or newer is required"'
python3 -m venv "$studio_root/.are-venv"
"$studio_root/.are-venv/bin/python" -m pip install -r "$studio_root/deploy/are/requirements.txt"
"$studio_root/.are-venv/bin/python" -m pip check
"$studio_root/.are-venv/bin/python" -m pip freeze > "$studio_root/.are-venv/installed-requirements.txt"
printf '\nARE Module directories: /apps/Modules/modulefiles %s/deploy/are/modulefiles\n' "$studio_root"
printf 'ARE Modules: python3/3.11.7 esmvaltool-studio\n'
