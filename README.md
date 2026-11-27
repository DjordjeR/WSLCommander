# WSL Commander

A modern Windows desktop application for managing your **Windows Subsystem for Linux (WSL)** distributions and **WSL Containers** — built with Python and PyQt6 Fluent Widgets.

---

## Features

- View all installed WSL distributions with live status and version info
- Launch a terminal for any distribution with a single click
- Set a distribution as the system default
- Stop a running distribution
- Remove a distribution
- Export a distribution to a `.tar` archive for backup or transfer
- Install new distributions from the official online catalogue
- Import a distribution from a `.tar` archive with a custom name and install location
- Share USB devices with WSL, including bind, unbind, attach, detach, and auto-attach workflows
- Keep auto-attach sessions alive over app restarts and reboots, with a **Keep after restart** option
- Auto-attach as a desired state: devices on the list are (re-)attached automatically whenever
  they appear, even if they were unplugged or unavailable when the entry was created
- System tray icon with a live auto-attach badge, quick start/stop controls, a recent-devices
  quick-attach submenu, **Close to tray**, and **Start at login**
- Settings page to pick which notifications you receive (started / stopped / errors /
  attached / detached)
- Browse USB devices in a sortable table with color-coded device states
- Manage **WSL containers** alongside distributions on a unified page, with an All / Distros / Containers filter, expandable container details, logs, and start/stop/remove actions *(requires WSL 2.9.3+)*
- Use a modern Fluent Design UI with automatic light/dark theme support

## What's New in v1.2.0

- Added **WSL container support** on the Distributions page, powered by Microsoft's `wslc.exe` container CLI *(requires WSL 2.9.3+)*.
- Unified the Distributions page with an **All / Distros Only / Containers Only** filter.
- Added container cards with **expandable details** (ports, volumes, environment with masked secrets, network, restart policy, created time, and best-effort CPU/memory usage).
- Added container actions: **Start**, **Stop**, **Remove**, and a **Logs** viewer.
- Added a session-mismatch hint that suggests re-running as administrator (or as a normal user) when containers from a differently-elevated `wslc` session aren't visible.
- Added graceful degradation so the container filter is hidden with an informational note when a compatible WSL version isn't installed.
- Added an explicit Windows AppUserModelID so the app shows its own taskbar icon when run from source.

For a release-by-release history, see [`CHANGELOG.md`](./CHANGELOG.md). The release notes for `v1.2.0` are available in [`docs/release-notes-v1.2.0.md`](./docs/release-notes-v1.2.0.md).

---

## Screenshots

### Installed Distributions
![Distros Page](docs/screenshots/distros_page.png)

### Install / Import a Distribution
![Install Page](docs/screenshots/install_page.png)

### USB Device Management
![USB Page](docs/screenshots/usb_page.png)

---

## Requirements

- Windows 10 or Windows 11 with WSL2 enabled
- [usbipd-win](https://github.com/dorssel/usbipd-win) *(optional — required for USB sharing functionality)*

### Enable WSL2 (if not already set up)

Open an elevated PowerShell and run:

```powershell
wsl --install
```

Restart your machine before launching WSL Commander.

### Enable WSL container support (optional)

Container management uses Microsoft's `wslc.exe` container CLI, which ships with **WSL 2.9.3 or higher**. This is currently available as a pre-release. To enable it, open PowerShell and run:

```powershell
wsl --update --pre-release
```

Confirm your version with `wsl --version`. Without a compatible WSL version, WSL Commander still runs and manages distributions normally — the container filter is simply hidden.

### Install usbipd-win (for USB sharing)

USB device sharing requires [usbipd-win](https://github.com/dorssel/usbipd-win). Install it via `winget`:

```powershell
winget install usbipd
```

Or download the installer from the [usbipd-win releases page](https://github.com/dorssel/usbipd-win/releases).

> **Note:** Without `usbipd-win`, the USB page will still open but no devices will be listed and attach/detach actions will not be available.

> **Note:** Binding and unbinding USB devices may trigger a Windows UAC prompt if WSL Commander is not already running with administrator privileges.

---

## Auto-Attach, Tray & Startup

WSL Commander can keep USB devices auto-attached to WSL in the background:

- **Keep after restart** (USB page): marks an auto-attach session as persistent. Persistent
  sessions survive app restarts and Windows logoffs, and are restored automatically when
  WSL Commander starts. Non-persistent sessions are cleaned up automatically when the app
  exits (even after a crash, via a Windows job object).
- **Auto-attach is a desired state**: once a device is on the auto-attach list, WSL Commander
  keeps trying to honour it. If the device is unplugged, not shared yet, or an attach attempt
  fails, the entry stays and the app automatically re-attaches as soon as the device shows up
  again (checked every few seconds, with a short backoff so a persistently failing device
  never rapid-fires). Only removing it from the list (or toggling auto-attach off) stops it.
- **System tray icon**: while any auto-attach session is active, the tray icon shows a green
  badge with the device count. Right-click the icon to stop a running session, restart a saved
  one, or open the app. Auto-attach events also arrive as native Windows notifications.
- **Recent devices submenu**: the tray menu lists the most recently *plugged-in* USB devices —
  any device, not just shared ones — each with a live indicator (● attached, ○ not attached)
  and the time it was last connected ("just now", "5 min ago", …), so you can tell which of
  several identically-named "USB Input Device" entries is the one you just plugged in. From
  there you can start **Auto attach** or **Keep after restart** for a device in one click, or
  stop it if it is already running. The USB page table shows the same
  **Last connected** column (a freshly plugged device is highlighted).
- **Attach / detach notifications**: a lightweight background watcher polls `usbipd list` and
  raises a Windows notification whenever a device becomes attached to (or detached from) WSL —
  including devices you attach by hand or that auto-attach re-connects on their own.
- **Settings page**: choose which notifications you want (auto-attach started / stopped,
  errors, device attached / detached), plus **Minimize to tray** and **Start at login**
  toggles.
- **Close to tray** (off by default): when enabled, closing the window minimizes to the
  notification area. Use the tray menu's **Quit** to exit.
- **Start at login**: the tray menu toggle registers WSL Commander in the per-user Windows
  Run key (`HKCU\...\CurrentVersion\Run`) and launches it minimized (`--tray`), so persisted
  auto-attach sessions come back after a reboot without any manual step.
- **Single instance**: launching the app again while it runs in the tray simply raises the
  existing window.

Settings (persisted auto-attach sessions, tray, startup and notification preferences) are
stored in `%APPDATA%\WSLCommander\settings.json`.

---

## Running the Application

### Option 1 — Pre-built Executable (no Python required)

1. Download the latest release from the [GitHub Releases](https://github.com/hpconcept/WSLCommander/releases) page.
2. Extract the zip archive.
3. Run `WSLCommander.exe` from the extracted folder.

> **Note:** The executable is self-contained. No Python or additional dependencies need to be installed.

### Option 2 — Run from Source

**Prerequisites:** Python 3.10 or higher.

#### 1. Clone the repository

```bash
git clone https://github.com/hpconcept/WSLCommander.git
cd WSLCommander
```

#### 2. Create a virtual environment (recommended)

```powershell
python -m venv .venv
.venv\Scripts\activate
```

#### 3. Install dependencies

```powershell
pip install -r requirements.txt
```

#### 4. Run

```powershell
python main.py
```

---

## Building the Executable

The repository includes a PyInstaller spec file for building the standalone executable yourself.

#### 1. Install dev dependencies

```powershell
pip install -r requirements-dev.txt
```

#### 2. Build

```powershell
pyinstaller WSLCommander.spec --noconfirm
```

The output will be placed in `dist\WSLCommander\WSLCommander.exe`.

---

## Project Structure

```
WSLCommander/
├── app/
│   ├── main_window.py        # Main application window
│   ├── models/               # Data models (Distro, Container, UsbDevice)
│   ├── pages/                # UI pages (Distributions, Install, USB)
│   ├── services/             # App-level services (auto-attach manager, tray icon)
│   ├── utils/                # Helpers (settings, startup, single instance, job object, elevation, wslc locator)
│   └── workers/              # Background QThread workers (WSL, containers, USB)
├── assets/
│   ├── distros/              # Distribution logo images
│   └── icon/                 # Application icon
├── docs/
│   └── screenshots/          # Application screenshots
├── main.py                   # Entry point
├── requirements.txt          # Runtime dependencies
├── requirements-dev.txt      # Dev/build dependencies (includes PyInstaller)
├── WSLCommander.spec         # PyInstaller build spec
├── CHANGELOG.md              # Version history and release notes
├── LICENSE                   # MIT License
└── README.md
```

---

## Dependencies

### Runtime

| Package | License | Purpose |
|---|---|---|
| `PyQt6>=6.6.0` | GPLv3 / Commercial | Qt6 Python bindings |
| `PyQt6-Fluent-Widgets[full]>=1.6.0` | GPLv3 (free tier) | Fluent Design UI component library |

### Dev / Build

| Package | License | Purpose |
|---|---|---|
| `PyInstaller` | GPL + exception | Packaging into a standalone executable |

---

## License

The **source code** of WSL Commander is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

The **pre-built binary** bundles [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) and
[PyQt6-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets), both of which are licensed
under **GPLv3**. As a result, redistribution of the compiled binary is subject to the terms of the
[GNU General Public License v3](https://www.gnu.org/licenses/gpl-3.0.html).

If you require a license that is compatible with proprietary use, a commercial license for PyQt6 is
available from [Riverbank Computing](https://www.riverbankcomputing.com/software/pyqt/) and a
commercial license for PyQt-Fluent-Widgets is available at
[qfluentwidgets.com](https://qfluentwidgets.com/pages/pro).
