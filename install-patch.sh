#!/usr/bin/env bash
# Compatibility entry point: upgrade installs a clean tracked fork checkout.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$script_dir/upgrade.sh" "$@"
