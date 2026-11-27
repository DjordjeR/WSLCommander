# Changelog

All notable changes to this project will be documented in this file.

The format is based loosely on Keep a Changelog and uses semantic versioning for releases.

## [Unreleased]

### Added
- **Auto-attach persistence**: a **Keep after restart** option on the USB page marks an
  auto-attach session so it survives app restarts and Windows logoffs. Persisted sessions are
  restored automatically when WSL Commander starts, and leftover helper processes from a
  previous session are cleaned up before a fresh one is spawned.
- **System tray icon**: WSL Commander now lives in the notification area with a green badge
  and device count while auto-attach sessions are active. The tray menu can open the app,
  stop running sessions, restart saved ones, toggle **Close to tray** and **Start at login**,
  and quit. Native balloon notifications report auto-attach events even when the window is closed.
- **Close to tray** (disabled by default): when enabled, closing the window minimizes to the
  notification area instead of exiting; use the tray menu's **Quit** to exit.
- **Start at login** toggle: registers WSL Commander in the per-user Windows Run key and launches
  minimized to the tray with `--tray`, restoring persisted auto-attach sessions after a reboot.
- **Single-instance enforcement**: a second launch raises the running window instead of starting
  a duplicate app (prevents double-spawned auto-attach helpers).
- Non-persistent auto-attach helpers are placed in a Windows **kill-on-close job object**, so they
  can never linger as orphans if the app crashes; persistent ones intentionally outlive the app.
- **Recent-devices tray submenu**: the tray menu lists the most recently plugged-in USB devices
  (any device, not just shared ones) with a live ●/○ attached indicator, the time each was last
  connected ("just now", "5 min ago", …), and one-click **Auto attach** /
  **Auto attach (keep after restart)** actions (or **Stop** when already running). The USB page
  table gained a matching **Last connected** column that highlights freshly plugged devices,
  making it easy to spot which identically-named device you just plugged in.
- **Attach / detach notifications**: a background watcher polls `usbipd list` and raises a Windows
  notification whenever a device attaches to or detaches from WSL, including devices attached by
  other means. Redundant notifications for the app's own start/stop actions are suppressed.
- **Auto-attach is now a desired state**: entries on the auto-attach list are automatically
  (re-)attached whenever the device appears — if it was unplugged, not shared yet, or an attach
  attempt failed, the entry is kept and retried on the next device-list update (with a short
  backoff so a persistently failing device never rapid-fires). Only an explicit stop removes it.
- **Stable device identity (hwid)**: auto-attach entries and recent-device history are keyed
  by a stable hardware identity (``VID:PID:serial`` from the Windows device instance path via
  ``usbipd state``) instead of the usbipd bus ID, which changes whenever a device enumerates
  on a different port. A saved auto-attach now survives re-plugging into any port; legacy
  bus-ID-keyed settings migrate automatically on the first poll. (Devices without a real
  serial number fall back to vendor+product identity, or to the bus ID when even that is
  ambiguous.)
- **Settings page**: toggles for **Minimize to tray**, **Start at login**, and per-category
  notification preferences (auto-attach started / stopped, errors, device attached / detached).
- **Notification anti-spam**: repeated identical balloons are rate-limited, the "minimized to
  tray" / "running in background" hints appear at most once per session, and auto-attach errors
  are throttled to one per device every 5 minutes (a retrying worker no longer spams).

### Changed
- Auto-attach state now lives in an app-level service instead of the USB page, so the tray,
  the USB page, and startup restoration all share one source of truth.
- Settings are persisted to `%APPDATA%\WSLCommander\settings.json` (atomic writes).

## [1.2.0] - 2026-08-27

### Added
- **WSL container support** on the Distributions page, powered by Microsoft's
  `wslc.exe` container CLI (ships with WSL 2.9.3+).
  - Unified Distributions page with an **All / Distros Only / Containers Only** filter.
  - Container cards showing name, image, status, and published-port summary.
  - **Expandable container details** (ID, ports, volumes, environment with masked
    secrets, network, restart policy, created time, and best-effort CPU/memory usage).
  - Container actions: **Start**, **Stop**, **Remove**, and **Logs** viewer.
- Session-mismatch hint: when no containers are visible but another `wslc` session with a
  different elevation exists, an info bar suggests running WSL Commander as administrator (or as a
  normal user) so the containers become visible.
- Explicit Windows AppUserModelID so the app shows its own icon in the taskbar when run from source.
- Graceful degradation: the container filter is hidden with an informational note
  when a compatible WSL version isn't installed.

## [1.1.0] - 2026-06-04

### Added
- Inline **Install** button on the selected distribution card in the install catalogue.
- Color-coded USB device state labels.
- Sorting support for the USB device table.
- USB **Auto-Attach** support, including optional support for unplugged devices.
- Automatic UAC elevation for USB bind and unbind operations when administrator privileges are required.

### Changed
- Improved the USB page workflow for managing shared and attached devices.
- Streamlined the install experience by moving the install action onto the selected catalogue item.
- Updated the README to reflect the latest USB and install features.

### Fixed
- Minor internal cleanup and removal of unused imports.

## [1.0.1] - 2026-03-05

### Added
- Auto-refresh for the distros page after install and import actions.
- Signals to detect launched installs and completed distro imports.

### Changed
- Distros polling behavior now temporarily switches to long-running install-aware polling after catalogue installs.

## [1.0.0] - 2026-02-27

### Added
- Initial public release of WSL Commander.
- WSL distribution management for listing, launching, stopping, removing, exporting, installing, and importing distros.
- USB device listing and sharing support through the USB page.
- Windows build and release workflow with PyInstaller packaging.

