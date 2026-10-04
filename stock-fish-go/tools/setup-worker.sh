#!/bin/sh
set -eu
[ "$#" = 2 ] || { echo 'usage: ./setup-worker.sh PYTHON NEW_VENV_ROOT' >&2; exit 1; }
[ ! -e "$2" ] || { echo 'refusing existing venv root' >&2; exit 1; }
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
"$1" -m venv "$2"
"$2/bin/python" -m pip install -r "$source_dir/requirements-daily.txt"
echo 'Optional fixed collector configured. Go queries/local research need no Python.'
