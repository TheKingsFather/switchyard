#!/usr/bin/env python3
"""Switchyard, a desktop utility for Linux."""

import argparse
import os
from pathlib import Path
from shutil import which
import signal
import sys

from PySide6.QtCore import ClassInfo, QProcess, Qt, QTimer, Slot
from PySide6.QtDBus import QDBusConnection
from PySide6.QtGui import QColor, QIcon, QImage, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMenu,
    QMessageBox, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from audio_view import AudioPage, SoundEvents
from desktop import APP_ID, KWIN, Audio, ColorPicker, DesktopError, KeepAwake, WindowBridge, dbus, focus_switches, has_service
from monitor import DiskPage, DiskState, NetworkPage, NetworkState, SpeedTest, SystemPage, SystemState
from theme import follow_system_theme
from tools_view import Command, Commands, FileShelf, ToolsPage
from version import VERSION
from views import ClipboardPage, Overview, SnippetsPage, WindowPage, button, label


PANEL_NAMES = {"mixer": "Mixer", "system": "System", "network": "Network", "disks": "Disks"}
ICON_SIZES = (16, 22, 24, 32, 48, 64)


def icon_image(size):
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setWindow(0, 0, 64, 64)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#243c34"))
    painter.drawRoundedRect(3, 3, 58, 58, 15, 15)
    painter.setPen(QPen(QColor("#a9e5be"), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawLine(17, 22, 47, 22)
    painter.drawLine(17, 42, 32, 42)
    painter.drawLine(32, 42, 43, 31)
    painter.drawLine(43, 31, 47, 31)
    for x, y in ((25, 22), (22, 42)):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#fae0a5"))
        painter.drawEllipse(x - 4, y - 4, 8, 8)
    painter.end()
    return image


def app_icon():
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(QPixmap.fromImage(icon_image(size)))
    return icon


def add_key_hint(layout, keys, caption):
    for key in keys:
        cap = label(key, "keycap")
        cap.setFixedSize(18, 18)
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(cap)
    layout.addWidget(label(caption, "keyHint"))


@ClassInfo(**{"D-Bus Interface": APP_ID})
class Switchyard(QWidget):
    def __init__(self, app, bus, use_tray=True):
        super().__init__()
        self.app = app
        self.bus = bus
        self.kwin = has_service(bus, KWIN)
        self.keep_awake = KeepAwake()
        self.awake_timeout = QTimer(self, singleShot=True)
        self.awake_timeout.setTimerType(Qt.TimerType.PreciseTimer)
        self.awake_timeout.timeout.connect(self.expire_awake)
        self.quiet, self.night_light = focus_switches()
        self.picker = ColorPicker(self)
        self.command_dialog = None
        self.bridge = WindowBridge(bus, self)
        self.bridge.finished.connect(self.finish_window_action)
        self.panel_placement = WindowBridge(bus, self, reply_path="/PanelPlacement")
        self.panel_placement.finished.connect(self.finish_panel_placement)
        self.audio = Audio(self)
        self.sound_events = SoundEvents(self)
        self.system_state = SystemState()
        self.network_state = NetworkState()
        self.disk_state = DiskState()
        self.speed_test = SpeedTest(self)
        self.selected_disk = None
        self.compact = None
        self.compact_key = "mixer"
        self.window_operation = ""
        self.tray = None
        self.setWindowTitle("Switchyard")
        self.resize(904, 575)
        self.setMinimumSize(690, 505)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QFrame(objectName="sidebar")
        sidebar.setFixedWidth(190)
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(14, 22, 14, 16)
        nav.setSpacing(3)
        brand = QHBoxLayout()
        brand.setSpacing(9)
        icon = QLabel()
        icon.setPixmap(app.windowIcon().pixmap(34, 34))
        brand.addWidget(icon)
        brand.addWidget(label("Switchyard", "brand"), 1)
        nav.addLayout(brand)
        nav.addSpacing(11)
        self.command_button = button("Commands   Ctrl+K", self.showCommands)
        nav.addWidget(self.command_button)
        search_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        search_shortcut.activated.connect(self.showCommands)
        nav.addSpacing(9)
        outer.addWidget(sidebar)

        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.overview = Overview(self.toggle_awake, self.screenshot)
        self.windows = WindowPage()
        self.windows.requested.connect(self.start_window_action)
        self.mixer = AudioPage(self.audio, self.sound_events, self.audio_settings)
        self.clipboard = ClipboardPage(bus)
        self.snippets = SnippetsPage(bus)
        self.shelf = FileShelf()
        self.tools = ToolsPage(bus, self.picker, self.quiet, self.night_light,
                               self.record_region, self.night_settings)
        self.system = SystemPage()
        self.network = NetworkPage(self.speed_test)
        self.disks = DiskPage()
        self.disks.selected.connect(lambda: self.update_selected_disk(self.disks))
        self.full_panels = {"mixer": self.mixer, "system": self.system,
                            "network": self.network, "disks": self.disks}
        self.group = QButtonGroup(self)
        self.pages = (
            ("Overview", self.overview), ("Windows", self.windows), ("Audio mixer", self.mixer),
            ("Clipboard", self.clipboard), ("Snippets", self.snippets),
            ("File shelf", self.shelf), ("Desktop tools", self.tools),
            ("System", self.system), ("Network", self.network), ("Disks", self.disks),
        )
        for index, (name, page) in enumerate(self.pages):
            self.stack.addWidget(page)
            control = button(name, lambda _checked=False, i=index: self.select_page(i), "nav")
            control.setCheckable(True)
            self.group.addButton(control, index)
            nav.addWidget(control)
        nav.addStretch()
        self.awake_state = label("Keep Awake is off", "sidebarStatus")
        nav.addWidget(self.awake_state)
        self.group.button(0).setChecked(True)

        if use_tray and QSystemTrayIcon.isSystemTrayAvailable():
            self.setup_tray()
        self.timer = QTimer(self, interval=1200)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        app.aboutToQuit.connect(self.shutdown)

    def setup_tray(self):
        logo = QIcon.fromTheme(APP_ID, self.app.windowIcon())
        self.tray = QSystemTrayIcon(logo, self)
        self.tray.setToolTip("Switchyard")
        self.tray_menu = QMenu(self)
        self.tray_menu.addAction("Open Switchyard", self.showWindow)
        self.tray_menu.addAction("Commands", self.showCommands)
        self.tray_menu.addAction("Windows", lambda: self.open_page(1))
        self.tray_menu.addAction("Audio mixer", lambda: self.showPanel("mixer"))
        for key in ("system", "network", "disks"):
            self.tray_menu.addAction(PANEL_NAMES[key], lambda _checked=False, k=key: self.showPanel(k))
        self.tray_menu.addAction("Clipboard", lambda: self.open_page(3))
        self.tray_menu.addAction("File shelf", lambda: self.open_page(5))
        self.tray_menu.addAction("Desktop tools", lambda: self.open_page(6))
        self.tray_menu.addSeparator()
        self.tray_awake = self.tray_menu.addAction("Keep Awake", self.toggle_awake)
        self.tray_awake.setCheckable(True)
        self.tray_menu.addAction("Quit", self.app.quit)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(self.tray_activated)
        self.tray.show()

    def tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.compact and self.compact.isVisible():
                self.compact.hide()
            else:
                self.showPanel(self.compact_key)

    def make_compact(self):
        self.compact = QWidget(None, Qt.WindowType.Tool)
        self.compact.setWindowTitle("Switchyard panels")
        self.compact.setWindowIcon(self.app.windowIcon())
        self.compact.resize(426, 654)
        self.compact.setMinimumSize(376, 392)
        layout = QVBoxLayout(self.compact)
        layout.setContentsMargins(10, 9, 10, 9)
        layout.setSpacing(5)
        tab_strip = QWidget(self.compact)
        selector = QHBoxLayout(tab_strip)
        selector.setContentsMargins(0, 0, 0, 0)
        selector.setSpacing(5)
        self.compact_tabs = QButtonGroup(self.compact)
        self.compact_stack = QStackedWidget()
        self.compact_panels = {
            "mixer": AudioPage(self.audio, self.sound_events, self.audio_settings, compact=True),
            "system": SystemPage(compact=True),
            "network": NetworkPage(self.speed_test, compact=True),
            "disks": DiskPage(compact=True),
        }
        self.compact_panels["disks"].selected.connect(
            lambda: self.update_selected_disk(self.compact_panels["disks"]))
        for i, (key, title) in enumerate(PANEL_NAMES.items()):
            tab = button(title, lambda _checked=False, k=key: self.select_compact_panel(k), "panelTab")
            tab.setCheckable(True)
            self.compact_tabs.addButton(tab, i)
            selector.addWidget(tab, 1)
            self.compact_stack.addWidget(self.compact_panels[key])
        layout.addWidget(tab_strip)
        for key, action in (
            ("Left", lambda: self.browse_compact(-1)),
            ("Right", lambda: self.browse_compact(1)),
            ("Down", self.compact.hide),
        ):
            shortcut = QShortcut(QKeySequence(key), tab_strip)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(action)
        layout.addWidget(self.compact_stack, 1)
        hints = QHBoxLayout()
        hints.setContentsMargins(2, 2, 0, 2)
        hints.setSpacing(4)
        add_key_hint(hints, "←→", "to browse")
        hints.addSpacing(8)
        add_key_hint(hints, "↓", "to go back")
        hints.addStretch()
        layout.addLayout(hints)
        footer = QHBoxLayout()
        footer.addWidget(button("Open in Switchyard", self.open_compact_page))
        footer.addStretch(1)
        footer.addWidget(button("Close", self.compact.hide))
        layout.addLayout(footer)

    def browse_compact(self, step):
        panels = tuple(PANEL_NAMES)
        index = panels.index(self.compact_key)
        self.select_compact_panel(panels[(index + step) % len(panels)])

    def open_compact_page(self):
        view = self.full_panels[self.compact_key]
        self.compact.hide()
        index = self.stack.indexOf(view)
        self.open_page(index)

    @Slot(str)
    def showPanel(self, key):
        if key not in PANEL_NAMES:
            return
        if self.compact is None:
            self.make_compact()
        if self.kwin:
            screen = self.app.primaryScreen()
            self.panel_placement.clear()
            self.panel_placement.run("anchor-panel", str(os.getpid()), screen.name() if screen else "")
        if self.compact.isMinimized():
            self.compact.showNormal()
        else:
            self.compact.show()
        self.compact.activateWindow()
        self.select_compact_panel(key)

    def finish_panel_placement(self, _result, error):
        if error:
            print(f"Could not place the compact panel: {error}", file=sys.stderr)

    def select_compact_panel(self, key):
        self.compact_key = key
        index = list(PANEL_NAMES).index(key)
        self.compact_tabs.button(index).setChecked(True)
        self.compact_stack.setCurrentIndex(index)
        self.compact_tabs.button(index).setFocus(Qt.FocusReason.ShortcutFocusReason)
        if key == "mixer":
            self.compact_panels[key].activate()
        self.refresh_current()

    def open_page(self, index):
        self.showWindow()
        self.select_page(index)

    @Slot()
    def showWindow(self):
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.activateWindow()

    @Slot()
    def showCommands(self):
        self.showWindow()
        if self.command_dialog:
            self.command_dialog.activateWindow()
            return
        commands = [
            Command(name, "Open panel", lambda i=index: self.open_page(i))
            for index, (name, _widget) in enumerate(self.pages)
        ]
        commands.extend([
            Command("Toggle Keep Awake", "Power", self.toggle_awake, "sleep lock"),
            Command("Capture screenshot", "Screen", self.screenshot, "capture region"),
            Command("Record region", "Screen", self.record_region, "screencast video"),
            Command("Pick a screen color", "Screen", self.pick_color, "hex rgb"),
            Command("Show Plasma window overview", "Windows", self.plasma_overview, "switcher preview"),
            Command("Open Night Light settings", "Display", self.night_settings, "color temperature"),
        ])
        self.command_dialog = Commands(self, commands)
        self.command_dialog.finished.connect(self.finish_command)
        self.command_dialog.open()

    def finish_command(self, _result):
        dialog, self.command_dialog = self.command_dialog, None
        action = dialog.selected
        if action:
            QTimer.singleShot(0, action)
        QTimer.singleShot(0, dialog.deleteLater)

    def select_page(self, index):
        self.group.button(index).setChecked(True)
        self.stack.setCurrentIndex(index)
        current = self.stack.currentWidget()
        if current is self.windows:
            self.windows.refresh()
        elif current is self.clipboard:
            self.clipboard.refresh()
        elif current is self.tools:
            self.tools.refresh()
        elif current is self.mixer:
            self.mixer.activate()
        else:
            self.refresh_current()

    def refresh_current(self):
        current = self.stack.currentWidget() if self.isVisible() else None
        if current is self.overview:
            self.overview.refresh()
        compact = self.compact_panels[self.compact_key] if self.compact and self.compact.isVisible() else None
        if current is self.mixer or (compact and self.compact_key == "mixer"):
            self.audio.refresh()
        for key, observe in (("system", self.system_state.sample),
                             ("network", self.network_state.sample), ("disks", self.sample_disks)):
            full = self.full_panels[key]
            mini = compact and self.compact_key == key
            if current is full or mini:
                snapshot = observe()
                if current is full:
                    full.update_state(snapshot)
                if mini:
                    compact.update_state(snapshot)

    def sample_disks(self):
        state = self.disk_state.sample(self.selected_disk)
        self.selected_disk = state[1]
        return state

    def update_selected_disk(self, view):
        self.selected_disk = view.volumes.currentData()
        self.refresh_current()

    def tick(self):
        if self.isVisible() or (self.compact and self.compact.isVisible()):
            self.refresh_current()
        if self.awake_timeout.isActive():
            self.show_awake_time()

    def toggle_awake(self):
        try:
            if self.keep_awake.active:
                self.keep_awake.stop()
                self.awake_timeout.stop()
            else:
                self.keep_awake.start()
                duration = self.overview.awake_duration.currentData()
                if duration:
                    self.awake_timeout.start(duration * 60000)
        except DesktopError as exc:
            QMessageBox.warning(self, "Keep Awake", str(exc))
        self.show_awake_time()

    def expire_awake(self):
        try:
            self.keep_awake.stop()
        except DesktopError as exc:
            print(f"Could not release Keep Awake after the timer expired: {exc}", file=sys.stderr)
        self.show_awake_time()

    def show_awake_time(self):
        active = self.keep_awake.active
        self.overview.awake_button.setText("Turn off" if active else "Turn on")
        self.overview.awake_duration.setDisabled(active)
        remaining = self.awake_timeout.remainingTime()
        if active and remaining >= 0:
            text = f"Keep Awake · {(remaining + 59999) // 60000} min left"
        else:
            text = "Keep Awake is on" if active else "Keep Awake is off"
        if text != self.awake_state.text():
            self.awake_state.setText(text)
            self.awake_state.setProperty("awake", active)
            self.awake_state.style().unpolish(self.awake_state)
            self.awake_state.style().polish(self.awake_state)
        if self.tray:
            self.tray_awake.setChecked(active)
            self.tray.setToolTip("Switchyard · " + text if active else "Switchyard")

    def screenshot(self):
        self.launch_tool("spectacle", ["--region"], "Screenshot", "KDE Spectacle")

    def record_region(self):
        self.launch_tool("spectacle", ["--record", "region"], "Screen recording", "KDE Spectacle")

    def night_settings(self):
        self.launch_tool("systemsettings", ["kcm_nightlight"], "Night Light", "KDE System Settings")

    def audio_settings(self):
        if self.kwin and which("systemsettings"):
            self.launch_tool("systemsettings", ["kcm_pulseaudio"], "Audio", "KDE System Settings")
        elif which("pavucontrol"):
            self.launch_tool("pavucontrol", [], "Audio", "PulseAudio Volume Control")
        else:
            QMessageBox.information(self, "Audio", "Open your desktop’s sound settings or install pavucontrol.")

    def launch_tool(self, program, arguments, title, name):
        started, _pid = QProcess.startDetached(program, arguments)
        if not started:
            QMessageBox.warning(self, title, f"{name} could not be started.")

    def pick_color(self):
        self.select_page(6)
        self.tools.pick()

    def plasma_overview(self):
        try:
            dbus(self.bus, "org.kde.kglobalaccel", "/component/kwin",
                 "org.kde.kglobalaccel.Component", "invokeShortcut", "Overview")
        except DesktopError as exc:
            QMessageBox.warning(self, "Plasma overview", str(exc))

    def start_window_action(self, operation, window_id, layout):
        if not self.kwin:
            self.windows.status.setText("Window controls require KDE Plasma with KWin.")
            self.windows.refresh_button.setEnabled(False)
            return
        if self.window_operation:
            return
        self.window_operation = operation
        self.windows.set_busy(True)
        self.windows.status.setText("Contacting KWin…")
        self.bridge.run(operation, window_id, layout)

    def finish_window_action(self, windows, error):
        operation, self.window_operation = self.window_operation, ""
        self.windows.set_busy(False)
        if error:
            self.windows.status.setText(error)
        elif windows is not None:
            self.windows.load(windows)
        elif operation in {"activate", "layout"}:
            self.windows.status.setText("Window updated.")

    def closeEvent(self, event):
        if self.tray and self.tray.isVisible():
            event.ignore()
            self.hide()
        else:
            event.accept()

    def shutdown(self):
        self.panel_placement.close()
        if self.compact:
            self.compact.close()
        if self.speed_test.direction:
            self.speed_test.toggle()
        self.bridge.close()
        self.picker.cancellable.cancel()
        for control in (self.keep_awake, self.quiet, self.night_light):
            try:
                control.stop()
            except DesktopError as exc:
                print(f"Could not release desktop setting: {exc}", file=sys.stderr)


def desktop_arg(value):
    # Desktop Entry values and quoted Exec arguments each consume escapes.
    value = value.replace("%", "%%")
    for char in ('\\', '"', '$', chr(96)):
        value = value.replace(char, "\\" + char)
    return '"' + value.replace("\\", "\\\\") + '"'


def install_launcher():
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    target = data / "applications" / f"{APP_ID}.desktop"
    target.parent.mkdir(parents=True, exist_ok=True)
    for size in ICON_SIZES:
        icon = data / f"icons/hicolor/{size}x{size}/apps" / f"{APP_ID}.png"
        icon.parent.mkdir(parents=True, exist_ok=True)
        if not icon_image(size).save(str(icon)):
            raise OSError(f"Could not save the Switchyard icon to {icon}")
    command = " ".join(map(desktop_arg, [sys.executable, str(Path(__file__).resolve())]))
    target.write_text(
        "[Desktop Entry]\n"
        "Type=Application\nName=Switchyard\n"
        "Comment=Desktop utilities for Linux\n"
        f"Exec={command}\n"
        f"Icon={APP_ID}\n"
        f"StartupWMClass={APP_ID}\n"
        "Terminal=false\nCategories=Utility;\nActions=commands;\n"
        "\n[Desktop Action commands]\nName=Open Commands\n"
        f"Exec={command} --commands\n"
    )
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"Switchyard {VERSION}")
    parser.add_argument("--install-launcher", action="store_true", help="Add Switchyard to the application menu.")
    opening = parser.add_mutually_exclusive_group()
    opening.add_argument("--tray", action="store_true", help="Start in the system tray.")
    opening.add_argument("--commands", action="store_true", help="Open the command palette.")
    opening.add_argument("--panel", choices=PANEL_NAMES, help="Open a compact tray panel.")
    args = parser.parse_args()
    if args.install_launcher:
        print(install_launcher())
        return 0
    app = QApplication([sys.argv[0]])
    app.setApplicationName("Switchyard")
    app.setApplicationVersion(VERSION)
    app.setOrganizationName("Switchyard")
    app.setDesktopFileName(APP_ID)
    app.setWindowIcon(app_icon())
    app.setStyle("Fusion")
    follow_system_theme(app)
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        print("Switchyard requires a Linux desktop D-Bus session.", file=sys.stderr)
        return 1
    if not bus.registerService(APP_ID):
        try:
            if args.panel:
                dbus(bus, APP_ID, "/Application", APP_ID, "showPanel", args.panel)
            elif not args.tray:
                dbus(bus, APP_ID, "/Application", APP_ID, "showCommands" if args.commands else "showWindow")
        except DesktopError as exc:
            print(f"Could not contact the running Switchyard instance: {exc}", file=sys.stderr)
            return 1
        return 0
    window = Switchyard(app, bus)
    bus.registerObject("/Application", window, QDBusConnection.RegisterOption.ExportAllSlots)
    app.setQuitOnLastWindowClosed(window.tray is None)
    if args.panel:
        QTimer.singleShot(0, lambda: window.showPanel(args.panel))
    elif args.commands:
        QTimer.singleShot(0, window.showCommands)
    elif not args.tray or window.tray is None:
        window.show()
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
