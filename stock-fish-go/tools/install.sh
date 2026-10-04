#!/bin/sh
set -eu
[ "$#" = 1 ] || { echo 'usage: ./install.sh EXPLICIT_INSTALL_ROOT' >&2; exit 1; }
case "$1" in /*) root=$1;; *) echo 'absolute install root required' >&2; exit 1;; esac
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
for required in stockfish BUILD.json stockfish.identity.json checksums.sha256 resources/config.example.json THIRD_PARTY_NOTICES.md; do
 [ -f "$source_dir/$required" ] || { echo 'incomplete candidate package: refusing install' >&2; exit 1; }
done
if command -v shasum >/dev/null 2>&1; then
 (cd "$source_dir" && shasum -a 256 -c checksums.sha256) || { echo 'candidate checksum mismatch: refusing install' >&2; exit 1; }
elif command -v sha256sum >/dev/null 2>&1; then
 (cd "$source_dir" && sha256sum -c checksums.sha256) || { echo 'candidate checksum mismatch: refusing install' >&2; exit 1; }
else
 echo 'SHA-256 verifier unavailable: refusing install' >&2; exit 1
fi
[ ! -e "$root/bin/stockfish" ] || { echo 'existing stockfish: refusing overwrite' >&2; exit 1; }
[ ! -e "$root/share/stockfish" ] || { echo 'existing resources: refusing overwrite' >&2; exit 1; }
mkdir -p "$root/bin" "$root/share"
cp -R "$source_dir" "$root/share/stockfish"
# Link creation is exclusive even if another install wins after the checks.
ln "$root/share/stockfish/stockfish" "$root/bin/stockfish"
echo 'Installed under explicit root. PATH and shell configuration were not changed.'
