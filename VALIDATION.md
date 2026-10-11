# Software validation

Upstream Axiscope base: `9a1a9efe3cfa6dc1e816acaaea87f8ac513282f6`.

The 13-test automatic calibration suite passed separately using the probing helpers and Tool class from each backend:

- KTCE: `e881fe40949a3999b0d63f59c22df589474eae9b`
- DraftShift: `7ae573ec59a68608bbc4e4552c7594e3eab0cce8`
- viesturz upstream: `94756dfde9b729fd69f9b8780067821c5c99a528`

Tests used Klipper's current `klippy/configfile.py` implementation to save and reload temporary configurations with included tool definitions. Successful reload confirmed measured XY values, preserved existing Z offsets, and saved camera coordinates. Failure injection confirmed rollback of included file edits and in-memory native save staging. Probe motion and tool selection were simulated, so these tests do not establish physical accuracy, safe clearance on a real printer, endstop timing, nozzle contact repeatability, or successful hardware toolchanges.

Python compilation, JavaScript syntax checks, shell syntax checks, and `git diff --check` passed. An isolated installer run verified copying of patched app files, backup contents, and installation of the Klipper module symlink without restarting any service.

Upgrade validation: 13 additional tests passed for replacement/addition of Moonraker manager blocks, included config discovery, duplicate removal, idempotence, custom installation path detection, symlink preservation, concurrent edit rejection, and manifest restoration. Installer integration tests used a temporary local git fork and stubbed virtualenv dependency commands with services disabled; they checked a clean checkout, complete old-install backup, module symlink, update-manager migration, repeat installation, dry run, and rollback on dependency/clone failure. They do not establish real sudo/systemd/PyPI behavior on the printer.

Service-unit regression fix: the generated `WorkingDirectory` is now unquoted, and literal percent specifiers are escaped. Actual `systemd-analyze verify` passed for ordinary installation paths, paths containing spaces, and paths containing a literal percent character. The installer now validates the generated unit with `systemd-analyze` when available before installing it.

UI visibility regression: the automatic-calibration panel is now present in the initial HTML, including when the backend is absent, old, or lacks a pin. Node-based UI checks passed for those states, connection errors, camera/homing prerequisites, confirmation-tool gating, and versioned JavaScript references. The Flask server now sends `Cache-Control: no-store, max-age=0`; compilation passed, but an HTTP test was unavailable because Flask is not installed in the test container.

Dock-height regression: 31 tests now pass with KTCE, including its actual `Toolchanger.select_tool`, state save/restore and tool transform. The regression simulates pickup at Z300 followed by the dock macro's Y exit and Z return. It checks that the pre-change raw lift refreshes Klipper's cached G-code position, `RESTORE_AXIS=Z` returns below dock height with nonzero tool/G-code offsets, and Axiscope travels XY only afterward. A failed Z return aborts before calibration XY motion. DraftShift passes the remaining 30 tests; the KTCE-specific transform test is skipped there. Hardware validation remains outstanding.

The **Preserve calibration and sync upstream Axiscope** GitHub Action validates pushes/PRs. Daily and manual runs additionally merge upstream `nic335/Axiscope` into `main`, preserving fork changes. Conflicts or failed tests stop the update. Only a validated merge is published, without force pushing, and only if main has not changed during validation. The write credential is confined to the publish job; candidate code runs in the read-only job.
