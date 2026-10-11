#!/usr/bin/env bash
# Installation and upgrade share the same existing-install detection.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$script_dir/upgrade.sh" ]]; then
  exec bash "$script_dir/upgrade.sh" "$@"
fi
installer="$(mktemp "${TMPDIR:-/tmp}/axiscope-upgrade-script.XXXXXX")"
trap 'rm -f "$installer"' EXIT
curl -fsSL https://raw.githubusercontent.com/maevebaksa/Axiscope/main/upgrade.sh -o "$installer"
bash "$installer" "$@"
