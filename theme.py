"""Switchyard colors for Qt's current system appearance."""

from pathlib import Path
from string import Template
from tempfile import TemporaryDirectory

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QComboBox, QFrame


# Each pair is dark, then light.
COLORS = {
    "background": ("#181e1c", "#f2f6f3"),
    "text": ("#edf3ec", "#20322a"),
    "sidebar": ("#202a26", "#e7eee9"),
    "divider": ("#354039", "#c5d3ca"),
    "metric": ("#c9eed1", "#225c43"),
    "metricValue": ("#d8eee7", "#225c50"),
    "readout": ("#c9ddd3", "#385247"),
    "muted": ("#b9c8bf", "#4a6256"),
    "keyText": ("#d9e8df", "#344d40"),
    "keyBorder": ("#53665b", "#8da398"),
    "keyHint": ("#adbbb3", "#4b6257"),
    "awake": ("#b5ebc4", "#256945"),
    "panel": ("#242e29", "#ffffff"),
    "panelBorder": ("#37463d", "#c6d6cc"),
    "tile": ("#2a3933", "#eaf2ee"),
    "tileBorder": ("#354940", "#cfddd4"),
    "control": ("#2c3932", "#edf3ef"),
    "controlBorder": ("#48594d", "#a7baae"),
    "hover": ("#384b3e", "#deebe3"),
    "focus": ("#a7ddba", "#2c7552"),
    "disabledText": ("#819087", "#62766b"),
    "disabledBorder": ("#37443c", "#c9d6ce"),
    "navHover": ("#293831", "#dce9e1"),
    "navSelected": ("#344e3e", "#cde5d5"),
    "navSelectedText": ("#d2f5d9", "#22543c"),
    "tab": ("#232e2a", "#e8f0eb"),
    "tabBorder": ("#35473d", "#bfd0c6"),
    "tabSelected": ("#356058", "#cde8df"),
    "tabSelectedBorder": ("#69a59a", "#659e8e"),
    "tabSelectedText": ("#e5f7f2", "#194e42"),
    "tabHoverBorder": ("#78ac9f", "#508674"),
    "toggled": ("#58423a", "#f2ddd2"),
    "toggledBorder": ("#936857", "#ad7d66"),
    "disclosure": ("#a9e0cf", "#256a57"),
    "primary": ("#aee1be", "#2e7451"),
    "primaryText": ("#182b20", "#ffffff"),
    "primaryHover": ("#c2ebcf", "#236543"),
    "primaryFocus": ("#e2f6e8", "#193d2d"),
    "primaryDisabled": ("#304238", "#dbe8df"),
    "primaryDisabledText": ("#9bab9f", "#62766b"),
    "primaryDisabledBorder": ("#425447", "#bfcec4"),
    "input": ("#202a25", "#fafcfb"),
    "inputBorder": ("#405045", "#afc2b5"),
    "selection": ("#375343", "#cde6d6"),
    "selectionText": ("#edf8ef", "#1d4933"),
    "inputFocus": ("#89c49f", "#377f58"),
    "meterTrack": ("#425149", "#cbd9d0"),
    "sliderTrack": ("#506357", "#bdcfc3"),
    "accent": ("#a9e5be", "#34855c"),
    "sliderHandle": ("#c2ebcf", "#2e7451"),
    "scrollTrack": ("#202a25", "#e9f0ec"),
    "scrollHandle": ("#536b5c", "#859f90"),
    "scrollHover": ("#789682", "#5e8571"),
    "chartGrid": ("#35463d", "#dce7e0"),
    "chartFirst": ("#88d6c8", "#167369"),
    "chartSecond": ("#e4b990", "#a25c20"),
}

PALETTES = tuple({key: pair[index] for key, pair in COLORS.items()} for index in (0, 1))


class Dropdown(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Qt puts the native list in its own popup window.
        view = self.view()
        self.popup = view.window()
        self.popup.setObjectName("dropdownPopup")
        self.popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        view.setObjectName("dropdownList")
        view.viewport().setObjectName("dropdownViewport")
        for widget in (self.popup, view, view.viewport()):
            # The native list can be polished before its style selectors are set.
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        self.popup.installEventFilter(self)

    def showPopup(self):
        self.popup.setFrameShape(QFrame.Shape.NoFrame)
        self.popup.setContentsMargins(4, 4, 4, 4)
        super().showPopup()

    def eventFilter(self, widget, event):
        if widget is self.popup and event.type() == QEvent.Type.Paint:
            palette = colors()
            painter = QPainter(widget)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QColor(palette["control"]))
            painter.setPen(QPen(QColor(palette["controlBorder"]), 1))
            frame = QRectF(widget.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            painter.drawRoundedRect(frame, 7, 7)
            painter.end()
            return True
        return super().eventFilter(widget, event)


def scheme_index():
    scheme = QGuiApplication.styleHints().colorScheme()
    if scheme == Qt.ColorScheme.Unknown:
        background = QGuiApplication.palette().color(QPalette.ColorRole.Window)
        return int(background.lightness() >= 128)
    return int(scheme == Qt.ColorScheme.Light)


def colors():
    return PALETTES[scheme_index()]


def save_chevron(path, color):
    image = QImage(24, 16, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(2, 2)
    painter.setPen(QPen(QColor(color), 1.5, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    chevron = QPainterPath()
    chevron.moveTo(2, 2.5)
    chevron.lineTo(6, 6)
    chevron.lineTo(10, 2.5)
    painter.drawPath(chevron)
    painter.end()
    if not image.save(str(path)):
        raise OSError(f"Could not save dropdown arrow to {path}")
    return path.as_posix()


def follow_system_theme(app):
    template = Template(Path(__file__).with_name("style.qss").read_text())
    assets = TemporaryDirectory(prefix="switchyard-style-")
    stylesheets = []
    for index, palette in enumerate(PALETTES):
        arrow = save_chevron(Path(assets.name) / f"dropdown-{index}.png", palette["muted"])
        disabled = save_chevron(Path(assets.name) / f"dropdown-disabled-{index}.png", palette["disabledText"])
        stylesheets.append(template.substitute(palette, comboArrow=arrow, comboArrowDisabled=disabled))
    app.aboutToQuit.connect(lambda: assets.cleanup())
    applied = None

    def apply(*_):
        nonlocal applied
        scheme = scheme_index()
        if scheme != applied:
            applied = scheme
            app.setStyleSheet(stylesheets[scheme])

    # Qt refreshes the native palette after emitting colorSchemeChanged.
    app.styleHints().colorSchemeChanged.connect(apply, Qt.ConnectionType.QueuedConnection)
    app.paletteChanged.connect(apply, Qt.ConnectionType.QueuedConnection)
    apply()
