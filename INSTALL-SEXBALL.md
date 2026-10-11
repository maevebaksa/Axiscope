# Automatic SexBall calibration for Axiscope

This patch measures XY using the SexBall's four side contacts. It retains the camera for a visual check. It does not detect nozzle centers from images. Each confirmation advances to the next physical tool; the last confirmation saves the measured offsets and restarts Klipper. XY-only is the default, preserving existing Z offsets. `INCLUDE_Z=1` also measures relative nozzle Z.

## Upgrade or install from the fork

SSH into your printer as its normal user and run:

```bash
curl -fsSL https://raw.githubusercontent.com/maevebaksa/Axiscope/main/upgrade.sh -o /tmp/axiscope-upgrade.sh
bash /tmp/axiscope-upgrade.sh
```

The same installer detects an existing Axiscope installation or creates a new one. It reads the existing `[update_manager axiscope]` path from Moonraker and follows nested/globbed includes. If no existing path is configured, it uses `~/axiscope`. An existing installation is moved into a timestamped backup under `~/axiscope-backups`, and a clean checkout of this fork's `main` branch takes its place. An existing virtual environment is preserved at its original absolute path and its requirements are checked. The Klipper module symlink and Axiscope service are installed/updated.

The installer replaces the existing Moonraker Axiscope update-manager block in its original file, removes duplicate Axiscope blocks, and preserves other update-manager sections. If none exists, it adds one to the main Moonraker config. Affected Moonraker files are backed up. Updates now use `https://github.com/maevebaksa/Axiscope.git`, branch `main`, with `virtualenv`, `requirements.txt`, and `managed_services: axiscope klipper`. Future updates restart both services so the Python backend reloads.

Moonraker and Axiscope are restarted by the installer. **Klipper is not restarted during this first upgrade**, allowing you to verify/change the calibration config first. Run this with your printer idle. It does not home, move, heat, probe, calibrate or change your tool offsets. If a pre-completion install step fails, it attempts to restore the previous installation, module symlink, service file, and changed Moonraker blocks. Backups are retained. After successful installation, a service-restart failure is reported without reverting the now-current installation.

For a read-only preview of the installation/config changes (the new checkout is downloaded to temporary staging):

```bash
bash /tmp/axiscope-upgrade.sh --dry-run
```

Nonstandard paths can be provided explicitly:

```bash
bash /tmp/axiscope-upgrade.sh \
  --moonraker-conf /path/to/moonraker.conf \
  --install-dir /path/to/axiscope \
  --klipper-dir /path/to/klipper
```

`--no-services` installs without changing/restarting systemd services; this is useful for controlled testing or systems that manage services separately. Normal Raspberry Pi/Klipper installs should use the default. Multiple conflicting configured Axiscope paths require an explicit `--install-dir`.

After the upgrade:

1. Replace the existing `[axiscope]` and `[tools_calibrate]` sections with the single block in [examples/maeve-sexball.cfg](examples/maeve-sexball.cfg). Insert the **actual SexBall pin** and polarity from your working configuration. Keep the toolchanger's `tools_calibrate.py` installed: the patch imports its probing implementation but does not instantiate a second `[tools_calibrate]` module. Old macros that reference `printer.tools_calibrate` must not be used with this setup. Leave `readonly-configs` alone; edit your user config. If you already applied this exact block, keep it.
2. Verify `safe_z: 20` clears both calibration fixtures and the entire transit path. This is an example clearance, not a measured clearance for your machine. Coordinates are raw T0 carriage coordinates (`toolhead.position`), independent of `G92`/bed mesh. Your earlier X/Y values are suitable if T0's XY offset and G-code origin were zero when recorded; otherwise recapture raw coordinates. The starting Z must be above the ball, within `max_z_travel`. Camera Z must be a noncontact inspection height. Both fixtures must be stationary relative to the machine.
3. Restart Klipper after editing, then force-reload Axiscope at `http://YOUR_PRINTER:3000`:

```bash
sudo systemctl restart klipper
```

A typical default Moonraker block after installation is:

```ini
[update_manager axiscope]
type: git_repo
channel: dev
path: ~/axiscope
origin: https://github.com/maevebaksa/Axiscope.git
primary_branch: main
virtualenv: ~/axiscope/axiscope-env
requirements: requirements.txt
is_system_service: True
managed_services: axiscope klipper
```

The installer writes resolved absolute paths for the actual detected installation. There is no need to paste a second copy of this block manually. `install.sh` and `install-patch.sh` are compatibility entry points for the same upgrade workflow.

## Configure and check the switch

At rest, run:

```gcode
AXISCOPE_QUERY_PROBE
```

It should report `open`. Hold the ball down and repeat; it must report `TRIGGERED`. The patch rejects an already-triggered switch and bounds the downward search. Clean the nozzles and use the same controlled temperature for all tools when measuring. Retain your existing safe nozzle-cleaning/heating hooks as needed. For KTCE with TAP crash detection, retain your existing `STOP_CRASH_DETECTION` calibration hook after pickup, **only if that command exists in your configuration**; this patch does not change your crash-detection policy.

The example uses your supplied X52.600, Y-9.700, Z9.900 and tentative `pin: ^!PC0`. Verify the pin state with the commands above. With `spread: 7`, the approach envelope spans X45.600–59.600 and Y-16.700–-2.700. Your configured travel and actual mechanism must permit that. If they do not, reposition the fixture or verify an appropriate smaller spread; do not extend travel limits beyond the mechanism. The patch checks the envelope before calibration motion. Z9.900 must be above the ball; if it is a contact position, raise the starting Z before running.

## Capture the camera independently

Home and perform your normal gantry leveling. Clear bed mesh, select **physical T0**, and jog T0 until its nozzle is centered under the Axiscope crosshair at a safe inspection height:

```gcode
BED_MESH_CLEAR
SELECT_TOOL T=0
# Jog T0 into alignment using the normal UI, then:
AXISCOPE_SET_CAMERA_POSITION CURRENT=1
```

The capture uses raw carriage coordinates, so the printer must be homed. The camera and switch are separate positions. The capture survives in memory and is written automatically when the calibration is saved. To save it immediately, independently of an offset calibration, run `AXISCOPE_SAVE_CAMERA_POSITION`; that saves and restarts Klipper.

You can also enter a known **raw T0** position with `AXISCOPE_SET_CAMERA_POSITION X=... Y=... Z=...`.

## Calibrate, verify and save

In Axiscope, use **Calibrate XY & check camera**, then **Alignment confirmed / next tool** for each nozzle. Equivalent console commands:

```gcode
CALIBRATE_ALL_XY_OFFSETS CAMERA_CHECK=1 SAVE=1
# After visually verifying each displayed tool:
AXISCOPE_CONFIRM_TOOL
```

The first tool is T0. Each confirmation selects the next registered physical tool and moves it to the camera with the new measured XY correction. The final confirmation writes all nonreference tool offsets and the camera reference, creates config backups, and restarts Klipper. Do not jog to "fix" an incorrect alignment and then confirm it; discard the run, check the fixture/nozzle and rerun. The confirmation rejects a different tool or a moved nozzle.

Inspect a specific measured tool again with `AXISCOPE_MOVE_TO_CAMERA T=1`. Reject a run using `AXISCOPE_ABORT_CALIBRATION`; this clears results without changing runtime or saved offsets.

```gcode
# Measure XYZ instead of preserving existing Z:
CALIBRATE_ALL_XY_OFFSETS INCLUDE_Z=1 CAMERA_CHECK=1 SAVE=1

# Measure and inspect without automatic saving:
CALIBRATE_ALL_XY_OFFSETS CAMERA_CHECK=1 SAVE=0
# After all tools have been confirmed:
AXISCOPE_SAVE_CALIBRATION

# Explicitly skip camera checking and automatically save:
CALIBRATE_ALL_XY_OFFSETS CAMERA_CHECK=0 SAVE=1
```

Calibration cannot run during a printing or paused job, with unhomed axes, or with an active named bed-mesh profile. Existing T0 offsets are retained as the reference; physical tool numbers and their actual section names are read from the toolchanger, not guessed from `Tn`. Offsets are measured in raw machine coordinates to avoid double-applying the old tool offsets. Tool selection uses `SELECT_TOOL ... RESTORE_AXIS=Z`: KTCE completes its dock exit and Z return, without returning X/Y to the previous fixture. Raw clearance moves refresh Klipper's cached G-code position before the toolchanger captures the return height.

**Upgrade before using automatic calibration if you installed an earlier version that used an empty `RESTORE_AXIS=`.** That version suppressed the Z return too and could travel laterally at dock height after pickup. The corrected version preserves the Z return. Validate your normal toolchange/dock-exit path before retrying, and verify `safe_z` clears the full transit path. Do not automatically move or rehome a tool still engaged with a dock after a collision.

## Persistence and compatibility

Supported backends are `jwellman80/klipper-toolchanger-easy`, `DraftShift/klipper-toolchanger`, and `viesturz/klipper-toolchanger`. This does not claim support for the separate `TypQxQ/Klipper_ToolChanger` KTC implementation.

The patch uses `configfile.set` and `SAVE_CONFIG`, saving under each tool's real `[tool ...]` section in the normal `printer.cfg` autosave block. `config_file_path` is not required for automatic calibration. Existing XY definitions in included files are commented with `# Axiscope migrated to SAVE_CONFIG:` so they no longer conflict with Klipper's save operation. Only the calibrated fields and captured camera fields are migrated; unrelated settings are preserved. Included files get timestamped `.axiscope-backup-*` copies. If saving fails, those include edits and in-memory save staging are rolled back. Runtime tool offsets stay unchanged until the successful Klipper restart. Native Klipper also backs up `printer.cfg`. No separate, unreferenced `axiscope.offsets` file is created.

## Validation

Run the tests against an existing backend checkout:

```bash
AXISCOPE_TOOLCHANGER=/path/to/toolchanger/klipper/extras \
  python3 -m unittest discover -s tests -v
```

Add `KLIPPER_CONFIGFILE=/path/to/klipper/klippy/configfile.py` to test actual native save and reload behavior. The test suite covers nonzero T0 offsets, sparse tool numbering, bounded probing, camera target correction, confirmations, included config migration, save rollback, stale offsets, travel limits, printing/mesh rejection, and symlink preservation. These are simulated software tests; the patch has not been tested on your physical printer.

## Source

Published fork: [maevebaksa/Axiscope](https://github.com/maevebaksa/Axiscope), branch `main`. The upstream project remains `nic335/Axiscope`.

## Repair an already-installed service from the initial upgrade

The initial generator incorrectly quoted `WorkingDirectory`, which systemd interpreted as a nonabsolute path. This is corrected in the current installer. To repair the existing unit without reinstalling:

```bash
sudo cp -a /etc/systemd/system/axiscope.service "/etc/systemd/system/axiscope.service.backup-$(date +%Y%m%d-%H%M%S)"
sudo sed -i -E 's/^WorkingDirectory="(.*)"$/WorkingDirectory=\1/' /etc/systemd/system/axiscope.service
sudo systemctl daemon-reload
sudo systemctl reset-failed axiscope
sudo systemctl restart axiscope
systemctl status axiscope --no-pager -l
```

## Missing automatic-calibration controls

The current page always displays the **Automatic SexBall XY calibration — UI 2** panel. Its status message explains a missing/old backend, an unconfigured SexBall pin, or a printer connection failure, while unavailable controls remain disabled. Update Axiscope, restart Axiscope and Klipper after checking the config, then open `http://YOUR_PRINTER:3000/?v=sexball-ui-2` to bypass an old cached page. The server also disables caching and the page uses versioned JavaScript files. If the UI 2 panel is still absent, the page is being served from an older installation or cached response.


### Refreshing and correcting the camera check

Browser refresh does not discard pending measurements or confirmed tools. The
native Mainsail page now queries the full calibration/tool/position status on
load, reconnect and after every command. It remembers the selected camera in
Mainsail's printer GUI settings. If confirmation is unavailable, read the hint;
use **Refresh calibration status** for a read-only retry. If the tool or position
changed, use **Move Tn to camera** and visually recheck before confirming.
Restarting Klipper discards unsaved calibration, unlike refreshing the browser.

For a nonreference tool with a visible residual, the updated backend supports
`AXISCOPE_ADJUST_CAMERA X=0.05 Y=0` (relative printer-axis millimeters).
Mainsail supplies X/Y ±0.05 mm buttons during that tool's camera check.
Each command moves the nozzle using the existing safe transit path, changes
only that tool's pending XY offsets and requires another visual check.
The final confirmation saves those corrected offsets with the other tools;
Z and live tool offsets are unchanged until the native config save/restart.
A per-command correction above 1 mm, an accumulated offset outside `max_offset`,
the wrong tool, a moved nozzle, T0, or an already confirmed tool is rejected.
To correct T0, discard the calibration and capture a new T0 camera reference.

These controls are manual camera corrections, not automatic image measurement.
A consistent residual can come from probe contact/nozzle geometry, pickup
repeatability, camera inspection height, or measurement error. Record tool
numbers, signed X/Y displacement and repeat measurements before attributing it
to any one cause. Do not confirm a tool that is visibly misaligned.
