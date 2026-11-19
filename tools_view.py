"""File shelf, screen tools, and command palette."""

import ast
from dataclasses import dataclass
import math
import operator
from pathlib import Path
from typing import Callable

from gi.repository import Gio, GLib
from PySide6.QtCore import QMimeData, QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QDrag, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from desktop import APP_ID, DesktopError, copy_text, session_call
from views import USER_ROLE, Page, button, count_label, label, panel, write_settings


class ShelfList(QListWidget):
    received = Signal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setSelectionMode(self.SelectionMode.ExtendedSelection)

    @staticmethod
    def local_files(mime):
        return [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]

    def dragEnterEvent(self, event):
        if self.local_files(event.mimeData()):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        paths = self.local_files(event.mimeData())
        if paths:
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self.received.emit(paths)

    def urls(self):
        paths = [Path(item.data(USER_ROLE)) for item in self.selectedItems()]
        return [QUrl.fromLocalFile(str(path)) for path in paths if path.exists()]

    def mimeData(self, _items):
        data = QMimeData()
        data.setUrls(self.urls())
        return data

    def startDrag(self, _supported):
        if urls := self.urls():
            data = QMimeData()
            data.setUrls(urls)
            drag = QDrag(self)
            drag.setMimeData(data)
            drag.exec(Qt.DropAction.CopyAction)


class FileShelf(Page):
    def __init__(self):
        super().__init__("File shelf", "Drop files and folders here. Drag them out when you need them.")
        self.settings = QSettings()
        self.paths = self.settings.value("fileShelf", [], list)
        self.list = ShelfList()
        self.list.received.connect(self.add)
        self.list.itemSelectionChanged.connect(self.update_controls)
        self.list.itemActivated.connect(lambda *_: self.open_selected())
        self.body.addWidget(self.list, 1)

        top = QHBoxLayout()
        top.addWidget(button("Add files", self.choose_files))
        top.addWidget(button("Add folder", self.choose_folder))
        top.addStretch()
        top.addWidget(button("Paste files", self.paste))
        self.body.insertLayout(2, top)
        controls = QHBoxLayout()
        self.remove_button = button("Remove", self.remove)
        self.remove_button.setAccessibleName("Remove from shelf")
        self.remove_button.setToolTip("Remove from shelf")
        self.reveal_button = button("Reveal", self.reveal)
        self.paths_button = button("Copy paths", self.copy_paths)
        self.copy_button = button("Copy files", self.copy, "primary")
        controls.addWidget(self.remove_button)
        controls.addStretch()
        controls.addWidget(self.reveal_button)
        controls.addWidget(self.paths_button)
        controls.addWidget(self.copy_button)
        self.body.addLayout(controls)
        self.status = label("")
        self.body.addWidget(self.status)
        self.populate()

    def populate(self, selected=()):
        self.list.clear()
        for value in self.paths:
            path = Path(value)
            exists = path.exists()
            title = path.name or str(path)
            suffix = "" if exists else "  ·  Missing"
            item = QListWidgetItem(f"{title}{suffix}\n{path.parent}")
            item.setData(USER_ROLE, value)
            item.setToolTip(value)
            self.list.addItem(item)
            item.setSelected(value in selected)
        self.status.setText(count_label(len(self.paths), "saved location") if self.paths else
                            "The shelf is empty. Drop files or add them using the buttons above.")
        self.update_controls()

    def save(self, paths, selected):
        try:
            write_settings(self.settings, "fileShelf", paths)
        except DesktopError as exc:
            self.status.setText(str(exc))
        else:
            self.paths = paths
            self.populate(selected)

    def add(self, paths):
        incoming = list(dict.fromkeys(str(Path(path).absolute()) for path in paths if Path(path).exists()))
        if incoming:
            self.save(list(dict.fromkeys([*self.paths, *incoming])), incoming)
        elif paths:
            self.status.setText("Those locations are no longer available.")

    def choose_files(self):
        paths, _type = QFileDialog.getOpenFileNames(self, "Add files to the shelf")
        self.add(paths)

    def choose_folder(self):
        if path := QFileDialog.getExistingDirectory(self, "Add a folder to the shelf"):
            self.add([path])

    def update_controls(self):
        selected = self.list.selectedItems()
        available = self.list.urls()
        self.remove_button.setEnabled(bool(selected))
        self.copy_button.setEnabled(bool(available))
        self.paths_button.setEnabled(bool(available))
        self.reveal_button.setEnabled(bool(available))

    def paste(self):
        paths = ShelfList.local_files(QApplication.clipboard().mimeData())
        if paths:
            self.add(paths)
        else:
            self.status.setText("The clipboard has no local files or folders.")

    def copy(self):
        urls = self.list.urls()
        if urls:
            data = QMimeData()
            data.setUrls(urls)
            QApplication.clipboard().setMimeData(data)
            self.status.setText("Copied " + count_label(len(urls), "location") + ".")

    def copy_paths(self):
        urls = self.list.urls()
        if urls:
            QApplication.clipboard().setText("\n".join(url.toLocalFile() for url in urls))
            self.status.setText("Copied " + count_label(len(urls), "path") + ".")

    def remove(self):
        selected = {item.data(USER_ROLE) for item in self.list.selectedItems()}
        self.save([path for path in self.paths if path not in selected], ())

    def reveal(self):
        urls = self.list.urls()
        if not urls:
            return
        endpoint = ("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                    "org.freedesktop.FileManager1")
        try:
            session_call(endpoint, "ShowItems", GLib.Variant("(ass)", ([url.toString() for url in urls], "")))
        except DesktopError as exc:
            self.status.setText(str(exc))

    def open_selected(self):
        item = self.list.currentItem()
        if item:
            path = item.data(USER_ROLE)
            if not Path(path).exists() or not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
                self.status.setText("Could not open this location.")


class ToolsPage(Page):
    def __init__(self, bus, picker, quiet, night_light, record, night_settings):
        super().__init__("Desktop tools", "Capture your screen and control interruptions.")
        self.bus = bus
        self.picker = picker
        self.quiet = quiet
        self.night_light = night_light
        self.color = QColor()
        self.picker.finished.connect(self.picked)

        scroll = QScrollArea(widgetResizable=True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        contents = QWidget()
        self.cards = QVBoxLayout(contents)
        self.cards.setContentsMargins(0, 0, 0, 0)
        self.cards.setSpacing(10)
        scroll.setWidget(contents)
        self.body.addWidget(scroll, 1)

        self.color_row, info = self.add_tool("Screen color picker", "Choose a pixel to read its color.")
        self.swatch = QFrame()
        self.swatch.setFixedSize(24, 24)
        self.swatch.setAccessibleName("Picked color")
        self.swatch.hide()
        info.addWidget(self.swatch)
        self.pick_button = button("Pick color", self.pick, "primary")
        info.addWidget(self.pick_button)
        output = QHBoxLayout()
        self.hex_value = label("")
        output.addWidget(self.hex_value, 1)
        self.copy_hex = button("Copy HEX", lambda: self.copy_color(self.color.name().upper()))
        self.copy_rgb = button("Copy RGB", lambda: self.copy_color(self.rgb()))
        for control in (self.copy_hex, self.copy_rgb):
            output.addWidget(control)
            control.setDisabled(True)
        self.color_row.addLayout(output)

        _, controls = self.add_tool("Screen recording", "Record a region with KDE Spectacle.")
        controls.addWidget(button("Record region", record))
        _, controls = self.add_tool("Do Not Disturb", "Pause notification popups for this session.")
        self.quiet_button = button("Turn on", lambda: self.toggle(self.quiet))
        controls.addWidget(self.quiet_button)
        _, controls = self.add_tool("Night Light", "Pause KDE’s color temperature adjustment.")
        controls.addWidget(button("Settings", night_settings))
        self.night_button = button("Pause", lambda: self.toggle(self.night_light))
        controls.addWidget(self.night_button)
        self.status = label("")
        self.body.addWidget(self.status)
        self.cards.addStretch()

    def add_tool(self, title, detail):
        card, layout = panel()
        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        row = QHBoxLayout()
        text = QVBoxLayout()
        text.addWidget(label(title, "section"))
        text.addWidget(label(detail))
        row.addLayout(text, 1)
        layout.addLayout(row)
        self.cards.addWidget(card)
        return layout, row

    def refresh(self):
        errors = []
        try:
            external_quiet = self.quiet.property("Inhibited") and not self.quiet.active
        except DesktopError as exc:
            self.quiet_button.setDisabled(not self.quiet.active)
            errors.append(str(exc))
        else:
            self.quiet_button.setText(
                "Turn off" if self.quiet.active else "Already on" if external_quiet else "Turn on"
            )
            self.quiet_button.setDisabled(external_quiet)
        try:
            night_enabled = self.night_light.property("enabled") and self.night_light.property("available")
            external_night = self.night_light.property("inhibited") and not self.night_light.active
        except DesktopError as exc:
            self.night_button.setDisabled(not self.night_light.active)
            errors.append(str(exc))
        else:
            self.night_button.setText(
                "Resume" if self.night_light.active else "Already paused" if external_night else "Pause"
            )
            self.night_button.setEnabled(self.night_light.active or (night_enabled and not external_night))
            self.night_button.setToolTip("" if night_enabled else "Enable Night Light in KDE settings first.")
        if errors:
            self.status.setText(errors[0])

    def toggle(self, switch):
        try:
            switch.toggle()
        except DesktopError as exc:
            self.status.setText(str(exc))
        else:
            self.status.clear()
            self.refresh()

    def pick(self):
        self.pick_button.setDisabled(True)
        self.status.setText("Click a pixel to pick its color. Press Escape to cancel.")
        self.picker.start()

    def picked(self, rgba, error):
        self.pick_button.setEnabled(True)
        if rgba is None:
            self.status.setText(error or "Color selection canceled.")
            return
        self.color = QColor.fromRgba(rgba)
        color = self.color.name().upper()
        self.swatch.setStyleSheet(f"background: {color}; border: 1px solid palette(mid); border-radius: 5px;")
        self.swatch.show()
        self.hex_value.setText(f"{color}   ·   {self.rgb()}")
        self.copy_hex.setEnabled(True)
        self.copy_rgb.setEnabled(True)
        self.status.clear()

    def rgb(self):
        return f"rgb({self.color.red()}, {self.color.green()}, {self.color.blue()})"

    def copy_color(self, value):
        try:
            copy_text(self.bus, value)
        except DesktopError as exc:
            self.status.setText(str(exc))
        else:
            self.status.setText(f"Copied {value}.")


MATH = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow}
SIGNS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def calculate(expression):
    def value(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            result = node.value
        elif isinstance(node, ast.UnaryOp) and type(node.op) in SIGNS:
            result = SIGNS[type(node.op)](value(node.operand))
        elif isinstance(node, ast.BinOp) and type(node.op) in MATH:
            left, right = value(node.left), value(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 100:
                raise ValueError("Exponent is too large")
            result = MATH[type(node.op)](left, right)
        else:
            raise ValueError("Unsupported expression")
        if type(result) not in (int, float) or not math.isfinite(result) or abs(result) > 1e100:
            raise ValueError("Result is out of range")
        return result

    if len(expression) > 120:
        raise ValueError("Expression is too long")
    result = value(ast.parse(expression, mode="eval").body)
    return str(result) if type(result) is int else format(result, ".12g")


@dataclass
class Command:
    title: str
    category: str
    action: Callable[[], None]
    terms: str = ""


class Commands(QDialog):
    def __init__(self, parent, commands):
        super().__init__(parent)
        self.setWindowTitle("Switchyard Commands")
        self.resize(560, 420)
        self.commands = commands
        self.apps = None
        self.matches = []
        self.selected = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.addWidget(label("Commands", "section"))
        self.search = QLineEdit(placeholderText="Find an action or app, or type = 24 * 7")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        self.search.returnPressed.connect(self.choose)
        layout.addWidget(self.search)
        self.results = QListWidget()
        self.results.itemActivated.connect(lambda *_: self.choose())
        layout.addWidget(self.results)
        self.help = label("Use ↑ and ↓ to select, Enter to run, or Esc to close.")
        layout.addWidget(self.help)
        for key, step in (("Up", -1), ("Down", 1)):
            shortcut = QShortcut(QKeySequence(key), self.search)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(lambda n=step: self.move(n))
        self.refresh()
        self.search.setFocus()

    def applications(self):
        if self.apps is None:
            self.apps = [
                Command(app.get_display_name(), "Application", lambda a=app: self.launch(a),
                        " ".join((app.get_id() or "", app.get_description() or "")))
                for app in Gio.AppInfo.get_all()
                if app.should_show() and app.get_id() != APP_ID + ".desktop"
            ]
        return self.apps

    def launch(self, app):
        try:
            app.launch([], None)
        except GLib.Error as exc:
            QMessageBox.warning(self.parentWidget(), "Launch application", str(exc))

    def refresh(self):
        query = self.search.text().strip().casefold()
        self.results.clear()
        self.help.setText("Use ↑ and ↓ to select, Enter to run, or Esc to close.")
        if query.startswith("="):
            try:
                result = calculate(query[1:].strip())
            except (ValueError, SyntaxError, ArithmeticError, RecursionError):
                self.help.setText("Enter a calculation using +, −, *, /, %, or **.")
                self.matches = []
            else:
                self.matches = [Command(result, "Copy result", lambda: QApplication.clipboard().setText(result))]
        elif query:
            words = query.split()
            candidates = [
                command for command in [*self.commands, *self.applications()]
                if all(word in (command.title + " " + command.category + " " + command.terms).casefold()
                       for word in words)
            ]
            self.matches = sorted(candidates, key=lambda command: (
                not command.title.casefold().startswith(query),
                command.category == "Application", command.title.casefold()
            ))[:30]
            if not self.matches:
                self.help.setText("No matching commands or applications.")
        else:
            self.matches = self.commands
        for item in self.matches:
            self.results.addItem(f"{item.title}    ·    {item.category}")
        if self.matches:
            self.results.setCurrentRow(0)

    def move(self, step):
        if self.matches:
            row = max(0, min(self.results.currentRow() + step, len(self.matches) - 1))
            self.results.setCurrentRow(row)

    def choose(self):
        row = self.results.currentRow()
        if row >= 0:
            self.selected = self.matches[row].action
            self.accept()
