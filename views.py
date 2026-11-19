"""Qt views for the desktop controls."""

import os
from pathlib import Path

import psutil
from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from desktop import CLIPBOARD, DesktopError, clipboard_call, copy_text, has_service
from theme import Dropdown


USER_ROLE = Qt.ItemDataRole.UserRole


def count_label(count, singular):
    return f"{count} {singular}{'' if count == 1 else 's'}"


def write_settings(settings, key, value):
    path = Path(settings.fileName())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
        os.close(descriptor)
        path.chmod(0o600)
    except OSError as exc:
        raise DesktopError("Could not open the local settings file.") from exc
    settings.setValue(key, value)
    settings.sync()
    if settings.status() != QSettings.Status.NoError:
        raise DesktopError("Could not save changes to disk.")


def label(text, role="muted"):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setProperty("role", role)
    widget.setWordWrap(True)
    return widget


def button(text, action, role=""):
    widget = QPushButton(text)
    widget.setProperty("role", role)
    widget.clicked.connect(action)
    return widget


def panel():
    widget = QFrame()
    widget.setProperty("role", "panel")
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(10)
    return widget, layout


class Page(QWidget):
    def __init__(self, title, detail):
        super().__init__()
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(30, 25, 30, 24)
        self.body.setSpacing(12)
        self.body.addWidget(label(title, "pageTitle"))
        self.body.addWidget(label(detail))


class Overview(Page):
    def __init__(self, toggle_awake, screenshot):
        super().__init__("Overview", "Monitor your system and reach common desktop controls.")
        grid = QHBoxLayout()
        grid.setSpacing(12)
        self.metrics = {}
        for title in ("CPU", "Memory", "Storage"):
            card, layout = panel()
            value = label("…", "metric")
            bar = QProgressBar()
            bar.setTextVisible(False)
            bar.setAccessibleName(title + " usage")
            layout.addWidget(label(title))
            layout.addWidget(value)
            layout.addWidget(bar)
            grid.addWidget(card, 1)
            self.metrics[title] = value, bar
        self.body.addLayout(grid)
        self.body.addSpacing(5)
        self.body.addWidget(label("Quick actions", "section"))
        self.awake_duration = Dropdown()
        self.awake_duration.setAccessibleName("Keep Awake duration")
        for caption, minutes in (("Until turned off", 0), ("15 minutes", 15),
                                 ("30 minutes", 30), ("1 hour", 60), ("2 hours", 120)):
            self.awake_duration.addItem(caption, minutes)
        self.awake_button = self.add_action(
            "Keep Awake", "Pause automatic sleep and screen locking.",
            "Turn on", toggle_awake, self.awake_duration,
        )
        self.add_action("Screenshot", "Select a region with KDE Spectacle.", "Capture", screenshot)
        self.body.addStretch()
        psutil.cpu_percent()
        self.refresh()

    def add_action(self, title, detail, action_name, callback, option=None):
        card, layout = panel()
        row = QHBoxLayout()
        text = QVBoxLayout()
        text.addWidget(label(title, "section"))
        text.addWidget(label(detail))
        row.addLayout(text, 1)
        if option:
            row.addWidget(option)
        action_button = button(action_name, callback)
        row.addWidget(action_button)
        layout.addLayout(row)
        self.body.addWidget(card)
        return action_button

    def refresh(self):
        values = (
            psutil.cpu_percent(),
            psutil.virtual_memory().percent,
            psutil.disk_usage(Path.home()).percent,
        )
        for (value, bar), percent in zip(self.metrics.values(), values):
            value.setText(f"{percent:.0f}%")
            bar.setValue(round(percent))


class WindowPage(Page):
    requested = Signal(str, str, str)

    def __init__(self):
        super().__init__("Windows", "Find, focus, or arrange open windows with KWin.")
        toolbar = QHBoxLayout()
        self.search = QLineEdit(placeholderText="Filter windows…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter_windows)
        self.refresh_button = button("Refresh", self.refresh)
        toolbar.addWidget(self.search, 1)
        toolbar.addWidget(self.refresh_button)
        self.body.addLayout(toolbar)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self.update_actions)
        self.list.itemActivated.connect(lambda *_: self.run("activate"))
        self.body.addWidget(self.list, 1)
        controls = QHBoxLayout()
        self.layout_choice = Dropdown()
        for name, value in (
            ("Left half", "left"), ("Right half", "right"),
            ("Top left", "topLeft"), ("Top right", "topRight"),
            ("Bottom left", "bottomLeft"), ("Bottom right", "bottomRight"),
            ("Maximize", "maximize"),
        ):
            self.layout_choice.addItem(name, value)
        self.layout_button = button("Arrange", lambda: self.run("layout"))
        self.more = QPushButton("More")
        menu = QMenu(self.more)
        self.minimize_action = menu.addAction("Minimize", lambda: self.run("minimize"))
        self.pin_action = menu.addAction("Keep above other windows", lambda: self.run("above"))
        self.more.setMenu(menu)
        self.focus_button = button("Focus window", lambda: self.run("activate"), "primary")
        controls.addWidget(self.layout_choice)
        controls.addWidget(self.layout_button)
        controls.addWidget(self.more)
        controls.addStretch()
        controls.addWidget(self.focus_button)
        self.body.addLayout(controls)
        self.status = label("")
        self.body.addWidget(self.status)
        self.busy = False
        self.update_actions()

    def refresh(self):
        self.requested.emit("list", "", "")

    def run(self, operation):
        item = self.list.currentItem()
        if item and not item.isHidden() and not self.busy:
            self.requested.emit(operation, item.data(USER_ROLE)["id"], self.layout_choice.currentData())

    def set_busy(self, busy):
        self.busy = busy
        self.refresh_button.setDisabled(busy)
        self.update_actions()

    def update_actions(self, *_):
        item = self.list.currentItem()
        enabled = bool(item and not item.isHidden() and not self.busy)
        for widget in (self.focus_button, self.layout_button, self.layout_choice, self.more):
            widget.setEnabled(enabled)
        if enabled:
            window = item.data(USER_ROLE)
            self.minimize_action.setEnabled(not window["minimized"])
            self.pin_action.setText(
                "Stop keeping above" if window.get("above") else "Keep above other windows"
            )

    def filter_windows(self):
        query = self.search.text().casefold()
        for index in range(self.list.count()):
            item = self.list.item(index)
            item.setHidden(query not in item.text().casefold())
        current = self.list.currentItem()
        if not current or current.isHidden():
            first = next((i for i in range(self.list.count()) if not self.list.item(i).isHidden()), -1)
            self.list.setCurrentRow(first)
        self.update_actions()

    def load(self, windows):
        old = self.list.currentItem()
        selected_id = old.data(USER_ROLE)["id"] if old else None
        self.list.clear()
        for window in windows:
            title = window["title"] or "Untitled window"
            flags = []
            if window["minimized"]:
                flags.append("Minimized")
            if window.get("above"):
                flags.append("Above")
            state = "  ·  " + ", ".join(flags) if flags else ""
            item = QListWidgetItem(f"{title}\n{window['app']}{state}")
            item.setToolTip(title)
            item.setData(USER_ROLE, window)
            self.list.addItem(item)
            if selected_id == window["id"]:
                self.list.setCurrentItem(item)
        self.filter_windows()
        self.status.setText(count_label(len(windows), "open window") if windows else "No open windows found.")


class ClipboardPage(Page):
    def __init__(self, bus):
        use_klipper = has_service(bus, CLIPBOARD)
        detail = ("Browse and copy text from KDE’s Klipper history." if use_klipper else
                  "Browse text observed during this session. Your desktop controls clipboard access.")
        super().__init__("Clipboard", detail)
        self.bus = bus
        self.use_klipper = use_klipper
        self.history = []
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self.update_selection)
        self.list.itemActivated.connect(lambda *_: self.copy())
        self.body.addWidget(self.list, 1)
        controls = QHBoxLayout()
        controls.addWidget(button("Refresh", self.refresh))
        controls.addStretch()
        if use_klipper:
            controls.addWidget(button("Open Klipper", self.open_klipper))
        else:
            QGuiApplication.clipboard().dataChanged.connect(self.clipboard_changed)
        self.copy_button = button("Copy selected", self.copy, "primary")
        controls.addWidget(self.copy_button)
        self.body.addLayout(controls)
        self.status = label("")
        self.body.addWidget(self.status)
        self.update_selection()

    def update_selection(self, *_):
        self.copy_button.setEnabled(self.list.currentRow() >= 0)

    def refresh(self):
        if self.use_klipper:
            try:
                self.history, = clipboard_call(self.bus, "getClipboardHistoryMenu")
            except DesktopError as exc:
                self.list.clear()
                self.status.setText(str(exc))
                return
        else:
            self.capture_clipboard()
        self.show_history()

    def capture_clipboard(self):
        data = QGuiApplication.clipboard().mimeData()
        text = data.text() if data is not None and data.hasText() else ""
        if text and (not self.history or self.history[0] != text):
            self.history = [text, *(entry for entry in self.history if entry != text)][:50]

    def clipboard_changed(self):
        self.capture_clipboard()
        if self.isVisible():
            self.show_history()

    def show_history(self):
        self.list.clear()
        for entry in self.history:
            self.list.addItem(" ".join(entry.split())[:150] or "(Non-text item)")
        self.status.setText(f"{len(self.history)} history entries" if self.history else "Clipboard history is empty.")

    def copy(self):
        index = self.list.currentRow()
        if index < 0:
            return
        if not self.use_klipper:
            copy_text(self.bus, self.history[index])
            self.status.setText("Copied to clipboard.")
            return
        try:
            history, = clipboard_call(self.bus, "getClipboardHistoryMenu")
            if history != self.history:
                self.refresh()
                self.status.setText("History changed. Select the item again.")
                return
            text, = clipboard_call(self.bus, "getClipboardHistoryItem", index)
            if not text:
                self.status.setText("Open Klipper to select this non-text item.")
                return
            clipboard_call(self.bus, "setClipboardContents", text)
        except DesktopError as exc:
            self.status.setText(str(exc))
        else:
            self.refresh()
            self.status.setText("Copied to clipboard.")

    def open_klipper(self):
        try:
            clipboard_call(self.bus, "showKlipperPopupMenu")
        except DesktopError as exc:
            self.status.setText(str(exc))


class SnippetDialog(QDialog):
    def __init__(self, parent, title="", content=""):
        super().__init__(parent)
        self.setWindowTitle("Edit snippet" if title else "New snippet")
        self.resize(480, 260)
        form = QFormLayout(self)
        self.title = QLineEdit(title)
        self.content = QPlainTextEdit(content)
        form.addRow("Name", self.title)
        form.addRow("Text", self.content)
        actions = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        actions.accepted.connect(self.accept)
        actions.rejected.connect(self.reject)
        save = actions.button(QDialogButtonBox.StandardButton.Save)
        self.title.textChanged.connect(lambda: save.setEnabled(self.valid()))
        self.content.textChanged.connect(lambda: save.setEnabled(self.valid()))
        save.setEnabled(self.valid())
        form.addRow(actions)

    def valid(self):
        return bool(self.title.text().strip() and self.content.toPlainText().strip())


class SnippetsPage(Page):
    def __init__(self, bus):
        super().__init__("Snippets", "Save text you reuse and copy it with one click.")
        self.bus = bus
        self.settings = QSettings()
        self.list = QListWidget()
        self.list.itemActivated.connect(lambda *_: self.copy())
        self.list.currentItemChanged.connect(self.update_selection)
        self.body.addWidget(self.list, 1)
        controls = QHBoxLayout()
        controls.addWidget(button("New snippet", lambda: self.edit(False)))
        self.edit_button = button("Edit", lambda: self.edit(True))
        self.remove_button = button("Remove", self.remove)
        self.copy_button = button("Copy snippet", self.copy, "primary")
        controls.addWidget(self.edit_button)
        controls.addWidget(self.remove_button)
        controls.addStretch()
        controls.addWidget(self.copy_button)
        self.body.addLayout(controls)
        self.status = label("")
        self.body.addWidget(self.status)
        self.snippets = self.settings.value("snippets", [], list)
        self.populate()

    def update_selection(self, *_):
        for widget in (self.edit_button, self.remove_button, self.copy_button):
            widget.setEnabled(self.list.currentRow() >= 0)

    def populate(self, row=-1):
        self.list.clear()
        for snippet in self.snippets:
            self.list.addItem(snippet["title"])
        self.list.setCurrentRow(row)
        self.status.setText("" if self.snippets else "No snippets yet.")
        self.update_selection()

    def persist(self, row=-1):
        self.populate(row)
        try:
            write_settings(self.settings, "snippets", self.snippets)
        except DesktopError as exc:
            self.status.setText(str(exc))

    def edit(self, existing):
        row = self.list.currentRow() if existing else len(self.snippets)
        values = self.snippets[row] if existing else {}
        dialog = SnippetDialog(self, **values)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.snippets[row:row + 1] = [{
                "title": dialog.title.text().strip(),
                "content": dialog.content.toPlainText(),
            }]
            self.persist(row)

    def remove(self):
        row = self.list.currentRow()
        if row >= 0 and QMessageBox.question(self, "Remove snippet", "Remove this saved snippet?") == QMessageBox.StandardButton.Yes:
            del self.snippets[row]
            self.persist(min(row, len(self.snippets) - 1))

    def copy(self):
        row = self.list.currentRow()
        if row >= 0:
            try:
                copy_text(self.bus, self.snippets[row]["content"])
            except DesktopError as exc:
                self.status.setText(str(exc))
            else:
                self.status.setText("Copied to clipboard.")
