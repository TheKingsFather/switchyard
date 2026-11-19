# Switchyard

Switchyard is a Linux desktop utility with a volume mixer, system monitor, network and disk panels, clipboard tools, and a command palette. Open it from the application menu or a supported system tray.
Its windows and tray panels follow the system’s light or dark appearance as it changes.

## Install a release

Requirements are Python 3.10 or newer, PySide6 with Qt 6.8 or newer, PyGObject with Gio, psutil, and a graphical Linux session with a user D-Bus. The release archive contains source and works with matching system packages on either glibc or musl. For audio routing, install `pactl` and run PulseAudio or PipeWire with its PulseAudio compatibility service.

### Fedora

```sh
sudo dnf install python3-pyside6 python3-gobject python3-psutil pulseaudio-utils curl
```

### Arch Linux

```sh
sudo pacman -Syu pyside6 python-gobject python-psutil libpulse curl
```

### Debian, Ubuntu, and Linux Mint

Use the system PyGObject package and the installer’s virtual environment for PySide6.

```sh
sudo apt install python3 python3-venv python3-gi python3-psutil pulseaudio-utils curl \
    libgl1 libegl1 libfontconfig1 fonts-dejavu-core libxkbcommon0 libxkbcommon-x11-0 \
    libx11-xcb1 libxrender1 libsm6 libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 \
    libxcb-randr0 libxcb-shape0 libxcb-sync1 libxcb-xfixes0 libxcb-xinerama0 \
    libwayland-client0 libwayland-cursor0 libwayland-egl1
```

This path uses the [Qt for Python wheels](https://doc.qt.io/qtforpython-6/gettingstarted.html) compatible with your Python, architecture, and glibc.

### Alpine Linux

Use Alpine’s native packages on musl. Run this command as root or with `doas`.

```sh
apk add python3 py3-pyside6 py3-gobject3 py3-psutil pulseaudio-utils curl font-dejavu
```

On other distributions, install the native PySide6, PyGObject, and psutil packages for the same Python interpreter. On glibc-based distributions, `--venv` is also available when PySide6 wheels support your Python and architecture.

### Install for your user

Download `switchyard-1.0.0.tar.gz` and `SHA256SUMS` from [GitHub Releases](https://github.com/TheKingsFather/switchyard/releases), then run these commands in the download directory.

```sh
sha256sum -c SHA256SUMS
tar -xzf switchyard-1.0.0.tar.gz
cd switchyard-1.0.0
python3 install.py
```

On Debian, Ubuntu, or Mint with the dependencies above, use `/usr/bin/python3 install.py --venv` in the last step. This keeps the Python dependencies and launcher under your user account. Open **Switchyard** in the application menu or run `~/.local/bin/switchyard` if that directory is outside your PATH.

To upgrade, quit Switchyard and run the installer from the new release. It keeps your snippets and shelf entries.

To uninstall, quit the app and remove `~/.local/bin/switchyard`, `$XDG_DATA_HOME/switchyard`, and `$XDG_DATA_HOME/applications/io.switchyard.Utility.desktop`. The default data directory is `~/.local/share`. The icon files are named `io.switchyard.Utility.png` under `icons/hicolor` in the same data directory. Your saved preferences are in `~/.config/Switchyard`.

To run from a checkout with the dependencies already installed:

```sh
python3 switchyard.py
```

## Use Switchyard

On desktops with a system tray, closing the window keeps the tray icon available. Use **Quit** in the tray menu to exit. Run `~/.local/bin/switchyard --tray` to start with the window hidden.

Click the tray icon to open the compact Mixer, System, Network, and Disks panels. Use **Open in Switchyard** for the selected panel in the full window. The tray context menu opens the full Switchyard window directly.
KWin places the compact window at the bottom right of the primary display’s usable area each time you open it.

With a compact panel tab focused, **←** and **→** browse panels and **↓** goes back to the previous window. Press **Tab** to move into the controls.

Press **Ctrl+K** inside Switchyard to open Commands. The tray menu and the launcher’s **Open Commands** action also open it. To assign a global shortcut in KDE, use `~/.local/bin/switchyard --commands` as its command.

## Desktop integration

| Feature | Linux implementation |
| --- | --- |
| CPU, memory, and home filesystem usage | Live system readings via psutil |
| Focus, snap, maximize, minimize, and keep windows above | KWin scripting and Plasma’s existing tiling shortcuts |
| Volume mixer | Choose default output and microphone, set master volume, and route, mute, or adjust active app playback streams. On Plasma, route system sounds through the notification-audio stream entry when available. |
| System | CPU temperature and history, process CPU breakdown, memory usage, and GPU temperature and usage where the driver exposes them. Intel render usage may cover only visible clients. |
| Network | Live transfer history and app-session totals for physical interfaces where available, local IPv4 addresses, and established connections from visible apps. The optional Cloudflare speed test transfers about 6 MB. |
| Disks | Mounted volumes, free and used space, per-volume read/write history and session totals, and read-only SMART data from UDisks where supported. |
| Clipboard history | Klipper history on Plasma. Other desktops collect the text Qt can observe during the current session. Clipboard access varies by compositor. |
| Snippets | Editable multiline text stored in local Qt settings |
| File shelf | Saved file and folder locations. Drag or paste files in, then drag out, copy, or reveal them. Removing a shelf entry leaves the original in place. |
| Commands and calculator | Search installed apps and Switchyard actions. Start an expression with `=` to calculate and copy a result. |
| Keep Awake | KDE power and screen-idle inhibitors with optional 15, 30, 60, or 120-minute timers |
| Do Not Disturb | KDE notification inhibition held until switched off or the app exits |
| Night Light | Pause and resume KDE’s adjustment while it is enabled |
| Screen color picker | Pick a pixel with KWin and copy its HEX or RGB value |
| Region screenshots and recording | KDE Spectacle’s region selectors |

System, network, disk usage, clipboard text, snippets, the file shelf, commands, and the mixer can run across desktop environments. KWin window controls, corner placement, the screen color picker, and Night Light use Plasma interfaces. Klipper supplies Plasma clipboard history, `plasma-pa` supplies system-sound routing, and PowerDevil supplies Plasma power inhibition. For capture actions, install Spectacle. SMART data requires UDisks2, and the on-demand speed test uses `curl`. Tray support depends on the desktop’s notification area. The main window works on desktops without a tray.

Switchyard reads window titles on request. Clipboard text collected outside Klipper stays in memory. Snippets and shelf locations are stored locally in `~/.config/Switchyard/Switchyard.conf` by default. KWin scripts are unloaded after use.

## Vorssaint reference

[Vorssaint v3.4.4](https://github.com/vorssaint/vorssaint-utils/releases/tag/v3.4.4) was the reference for the feature set. REA identified the released disk image and its SHA-256. Static inspection of the extracted ARM64 executable confirmed imports for AppKit, ApplicationServices, Core Audio, ScreenCaptureKit, and SwiftUI. Switchyard uses its own UI and Linux desktop APIs.
