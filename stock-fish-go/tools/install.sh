#!/bin/sh
set -eu
[ "$#" = 1 ] || { echo 'usage: ./install.sh EXPLICIT_INSTALL_ROOT' >&2; exit 1; }
case "$1" in /*) root=$1;; *) echo 'absolute install root required' >&2; exit 1;; esac
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
[ ! -e "$root/bin/stockfish" ] || { echo 'existing stockfish: refusing overwrite' >&2; exit 1; }
[ ! -e "$root/share/stockfish" ] || { echo 'existing resources: refusing overwrite' >&2; exit 1; }
mkdir -p "$root/bin" "$root/share"
cp -R "$source_dir" "$root/share/stockfish"
# Link creation is exclusive even if another install wins after the checks.
ln "$root/share/stockfish/stockfish" "$root/bin/stockfish"
echo 'Installed under explicit root. PATH and shell configuration were not changed.'
