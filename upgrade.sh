#!/usr/bin/env bash
# Upgrade or install the fork and replace the existing Moonraker manager block.
set -euo pipefail
repo_url='https://github.com/maevebaksa/Axiscope.git'
branch='main'
moonraker_conf="${HOME}/printer_data/config/moonraker.conf"
klipper_dir="${HOME}/klipper"
install_override=''
backup_root="${HOME}/axiscope-backups"
no_services=0
dry_run=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir) install_override="$2"; shift 2 ;;
    --moonraker-conf) moonraker_conf="$2"; shift 2 ;;
    --klipper-dir) klipper_dir="$2"; shift 2 ;;
    --backup-root) backup_root="$2"; shift 2 ;;
    --repo-url) repo_url="$2"; shift 2 ;;
    --branch) branch="$2"; shift 2 ;;
    --no-services) no_services=1; shift ;;
    --dry-run) dry_run=1; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done
if [[ "$EUID" -eq 0 && "$no_services" -eq 0 && "$dry_run" -eq 0 ]]; then
  echo 'Run as your normal printer user, not sudo/root.' >&2; exit 1
fi
for dependency in git python3; do command -v "$dependency" >/dev/null || { echo "Missing dependency: $dependency" >&2; exit 1; }; done
[[ -f "$moonraker_conf" ]] || { echo "Moonraker config not found: $moonraker_conf (use --moonraker-conf)" >&2; exit 1; }
[[ -f "$klipper_dir/klippy/extras/tools_calibrate.py" ]] || { echo 'KTCE/DraftShift tools_calibrate.py must already be installed.' >&2; exit 1; }
staging="$(mktemp -d "${TMPDIR:-/tmp}/axiscope-upgrade.XXXXXX")"
install_dir=''
backup_dir=''
swapped=0
committed=0
service_stopped=0
was_active=0
link_backed=0
old_exists=0
manager_backed=0
service_changed=0
service_existed=0
cleanup() {
  result=$?
  if [[ "$result" -ne 0 && "$committed" -eq 0 ]]; then
    set +e
    if [[ "$manager_backed" -eq 1 && -f "$backup_dir/moonraker-edits.json" ]]; then
      python3 "$helper" restore --config "$moonraker_conf" --manifest "$backup_dir/moonraker-edits.json"
    fi
    if [[ "$swapped" -eq 1 ]]; then
      echo 'Upgrade failed; restoring the previous Axiscope installation.' >&2
      if [[ -e "$install_dir" ]]; then mv "$install_dir" "${backup_dir}/failed-new-install"; fi
      if [[ "$old_exists" -eq 1 ]]; then mv "${backup_dir}/previous-install" "$install_dir"; fi
      rm -f "$klipper_dir/klippy/extras/axiscope.py"
      if [[ "$link_backed" -eq 1 ]]; then cp -a "${backup_dir}/axiscope.py" "$klipper_dir/klippy/extras/axiscope.py"; fi
    fi
    if [[ "$service_changed" -eq 1 ]]; then
      if [[ "$service_existed" -eq 1 ]]; then
        sudo cp "$backup_dir/axiscope.service" /etc/systemd/system/axiscope.service
      else
        sudo rm -f /etc/systemd/system/axiscope.service
      fi
      sudo systemctl daemon-reload
    fi
    if [[ "$service_stopped" -eq 1 && "$was_active" -eq 1 ]]; then sudo systemctl start axiscope; fi
  fi
  rm -rf "$staging"
  exit "$result"
}
trap cleanup EXIT
# Obtain and validate the new checkout before touching an existing install.
git clone --branch "$branch" --single-branch "$repo_url" "$staging/repository"
helper="$staging/repository/scripts/moonraker_axiscope.py"
[[ -f "$helper" && -f "$staging/repository/klippy/extras/axiscope.py" ]] || { echo 'The selected repository/branch lacks this upgrade.' >&2; exit 1; }
cp "$helper" "$staging/moonraker_axiscope.py"
helper="$staging/moonraker_axiscope.py"
location_args=(locate --config "$moonraker_conf" --default-dir "${HOME}/axiscope")
if [[ -n "$install_override" ]]; then location_args+=(--install-dir "$install_override"); fi
python3 "$helper" "${location_args[@]}" > "$staging/location"
IFS= read -r install_dir < "$staging/location"
python3 - "$install_dir" "$backup_root" <<'PY'
from pathlib import Path
import sys
install = Path(sys.argv[1]).resolve(); backup = Path(sys.argv[2]).resolve()
if install == Path('/') or install == Path.home() or install in Path.home().parents:
    raise SystemExit('Refusing to replace a home directory or filesystem root')
if backup == install or install in backup.parents:
    raise SystemExit('Backup directory must be outside the installation')
if install.exists() and not (install / 'app.py').is_file():
    raise SystemExit('Existing directory is not an Axiscope installation; choose --install-dir')
PY
python3 "$helper" check --config "$moonraker_conf" --install-dir "$install_dir" --origin "$repo_url" --branch "$branch"
if [[ "$dry_run" -eq 1 ]]; then echo 'Dry run complete; no installation/config/service changes.'; exit 0; fi
if [[ "$no_services" -eq 0 ]]; then
  sudo -v
  if systemctl is-active --quiet axiscope; then was_active=1; fi
  sudo systemctl stop axiscope || { [[ "$was_active" -eq 0 ]] || exit 1; }
  service_stopped=1
fi
backup_dir="${backup_root}/upgrade-$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$backup_dir" "$(dirname "$install_dir")"
chmod 700 "$backup_dir"
if [[ -e "$klipper_dir/klippy/extras/axiscope.py" || -L "$klipper_dir/klippy/extras/axiscope.py" ]]; then
  cp -a "$klipper_dir/klippy/extras/axiscope.py" "$backup_dir/axiscope.py"
  link_backed=1
fi
if [[ -d "$install_dir" ]]; then
  echo "Existing Axiscope found at ${install_dir}; backing up and replacing it."
  mv "$install_dir" "$backup_dir/previous-install"
  old_exists=1
else
  echo "Installing Axiscope at ${install_dir}."
fi
swapped=1
mv "$staging/repository" "$install_dir"
# Retain the old venv at the same absolute install path; recreate if absent.
if [[ -x "$backup_dir/previous-install/axiscope-env/bin/python3" ]]; then
  cp -a "$backup_dir/previous-install/axiscope-env" "$install_dir/axiscope-env"
else
  python3 -m venv "$install_dir/axiscope-env" || {
    echo 'Install python3-venv (sudo apt-get install python3-venv), then rerun.' >&2; exit 1;
  }
fi
"$install_dir/axiscope-env/bin/python3" -m pip install -r "$install_dir/requirements.txt"
"$install_dir/axiscope-env/bin/python3" -c 'import flask, waitress'
ln -sfn "$install_dir/klippy/extras/axiscope.py" "$klipper_dir/klippy/extras/axiscope.py"
if [[ "$no_services" -eq 0 ]]; then
  if [[ -f /etc/systemd/system/axiscope.service ]]; then cp /etc/systemd/system/axiscope.service "$backup_dir/axiscope.service"; service_existed=1; fi
  python3 - "$install_dir" "$(id -un)" "$staging/axiscope.service" <<'PY'
from pathlib import Path
import sys
path, user, output = sys.argv[1:]
def quote(value): return '"' + value.replace('%', '%%').replace('\\', '\\\\').replace('"', '\\"') + '"'
Path(output).write_text('[Unit]\nDescription=Axiscope tool alignment\nAfter=network.target moonraker.service\n\n'
    '[Service]\nType=simple\nUser=%s\nWorkingDirectory=%s\nExecStart=%s %s\nRestart=always\nRestartSec=2\n\n'
    '[Install]\nWantedBy=multi-user.target\n' % (user, path.replace('%', '%%'), quote(path + '/axiscope-env/bin/python3'), quote(path + '/app.py')))
PY
  if command -v systemd-analyze >/dev/null 2>&1; then
    systemd-analyze verify "$staging/axiscope.service"
  fi
  service_changed=1
  sudo cp "$staging/axiscope.service" /etc/systemd/system/axiscope.service
  sudo systemctl daemon-reload
  sudo systemctl enable axiscope
fi
manager_backed=1
python3 "$helper" update --config "$moonraker_conf" --install-dir "$install_dir" --origin "$repo_url" --branch "$branch" --manifest "$backup_dir/moonraker-edits.json"
# Register with Moonraker's service allowlist beside the printer_data config.
asvc_file="$(dirname "$(dirname "$moonraker_conf")")/moonraker.asvc"
if [[ ! -f "$asvc_file" ]]; then touch "$asvc_file"; fi
if ! grep -qx 'axiscope' "$asvc_file"; then printf '\naxiscope\n' >> "$asvc_file"; fi
committed=1
if [[ "$no_services" -eq 0 ]]; then
  sudo systemctl restart moonraker
  sudo systemctl start axiscope
fi
echo "Upgrade complete. Previous installation: ${backup_dir}/previous-install"
echo "Update manager: ${repo_url} (${branch})"
echo 'Klipper was not restarted: apply/verify the [axiscope] configuration first.'
echo 'Then: sudo systemctl restart klipper'
echo 'Reload Axiscope in your browser. Future Moonraker updates restart Axiscope and Klipper.'
