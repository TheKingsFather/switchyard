"""Device and playback controls for both mixer layouts."""

from PySide6.QtCore import QObject, QSignalBlocker, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QIcon
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu,
    QPushButton, QScrollArea, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from theme import Dropdown
from views import button, count_label, label, panel


class SoundEvents(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.engine = None
        self.root = None
        self.model = None

    def start(self):
        if self.engine:
            return
        self.engine = QQmlEngine(self)
        component = QQmlComponent(self.engine)
        component.setData(
            b"import QtQml\nimport org.kde.plasma.private.volume\n"
            b"QtObject { property var eventModel: StreamRestoreModel {} }", QUrl()
        )
        self.root = component.create() if component.isReady() else None
        if self.root:
            self.root.setParent(self)
            self.model = self.root.property("eventModel")
            for signal in (self.model.rowsInserted, self.model.rowsRemoved,
                           self.model.dataChanged, self.model.modelReset):
                signal.connect(self.changed)
        self.changed.emit()

    def event_object(self):
        if not self.model:
            return None
        roles = {bytes(name): key for key, name in self.model.roleNames().items()}
        for row in range(self.model.rowCount()):
            index = self.model.index(row, 0)
            if self.model.data(index, roles[b"Name"]) == "sink-input-by-media-role:event":
                return self.model.data(index, roles[b"PulseObject"])
        return None

    def route(self, output):
        if event := self.event_object():
            event.setProperty("device", output)


def device_title(device):
    props = device.get("properties", {})
    for value in (props.get("node.nick"), props.get("device.description"),
                  device.get("description"), props.get("node.description")):
        if value and value != "(null)":
            return value
    return device["name"]


def device_options(devices, default):
    return [
        (device["name"], device_title(device) + (" · Default" if device["name"] == default else ""))
        for device in devices
    ]


class DeviceChoice(Dropdown):
    def __init__(self, title, callback):
        super().__init__()
        self.setAccessibleName(title)
        self.setMinimumWidth(108)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setSizeAdjustPolicy(self.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(10)
        self.options = None
        self.activated.connect(lambda _index: callback(self.currentData()))

    def update_choices(self, options, selected, unavailable="Unavailable"):
        with QSignalBlocker(self):
            if options != self.options:
                self.options = options
                self.clear()
                for key, caption in options:
                    self.addItem(caption, key)
                    self.setItemData(self.count() - 1, caption, Qt.ItemDataRole.ToolTipRole)
                self.view().setMinimumWidth(min(460, max(
                    [180, *(self.fontMetrics().horizontalAdvance(text) + 32 for _, text in options)]
                )))
            index = self.findData(selected)
            self.setCurrentIndex(index)
            self.setPlaceholderText(unavailable)
            self.setEnabled(bool(options))
            self.setToolTip(self.currentText() or unavailable)


class Level(QWidget):
    def __init__(self, title, set_volume, set_muted, parent=None):
        super().__init__(parent)
        self.set_volume = set_volume
        self.set_muted = set_muted
        self.title = title
        self.identity = None
        self.levels = []
        self.confirmed = 0
        self.limit = 100
        self.pending = QTimer(self, singleShot=True, interval=140)
        self.pending.timeout.connect(self.commit)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setAccessibleName(f"{title} volume")
        self.slider.valueChanged.connect(self.adjust)
        self.slider.sliderReleased.connect(self.commit)
        self.value = label("—", "readout")
        self.value.setFixedWidth(46)
        self.value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.mute = button("", self.toggle, "icon")
        self.mute.setCheckable(True)
        self.mute.setFixedWidth(33)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.value)
        layout.addWidget(self.mute)

    def adjust(self, value):
        self.value.setText(f"{value}%")
        if not self.slider.isSliderDown():
            self.pending.start()

    def commit(self):
        self.pending.stop()
        value = self.slider.value()
        if self.identity is not None and value != self.confirmed:
            self.confirmed = value
            self.set_volume(self.identity, self.levels, value)

    def toggle(self):
        if self.identity is not None:
            self.set_muted(self.identity, self.mute.isChecked())

    def update_level(self, data, identity, writable=True):
        if self.identity != identity:
            self.pending.stop()
            self.slider.setSliderDown(False)
        self.identity = identity
        self.setEnabled(data is not None)
        if data is None:
            self.value.setText("—")
            return
        self.slider.setEnabled(writable)
        self.levels = [item["value"] for item in data.get("volume", {}).values() if "value" in item]
        if not self.slider.isSliderDown() and not self.pending.isActive():
            self.confirmed = round(sum(self.levels) * 100 / (len(self.levels) * 65536)) if self.levels else 0
            with QSignalBlocker(self.slider):
                self.slider.setRange(0, max(self.limit, self.confirmed))
                self.slider.setValue(self.confirmed)
            self.value.setText(f"{self.confirmed}%")
        muted = data.get("mute", False)
        self.mute.setChecked(muted)
        action = "Unmute" if muted else "Mute"
        self.mute.setToolTip(f"{action} {self.title}")
        self.mute.setAccessibleName(f"{action} {self.title}")
        icon = QIcon.fromTheme("audio-volume-muted" if muted else "audio-volume-high")
        self.mute.setIcon(icon)
        self.mute.setText("×" if icon.isNull() and muted else "♪" if icon.isNull() else "")


class StreamRow(QWidget):
    def __init__(self, audio, stream):
        super().__init__()
        self.audio = audio
        self.index = stream["index"]
        self.stream = stream
        self.available = []
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(3)
        self.icon = QLabel()
        self.icon.setFixedSize(30, 30)
        self.title = label("", "section")
        self.title.setWordWrap(False)
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.route = DeviceChoice("Application output", self.route_to)
        self.more = QPushButton("⋯")
        self.more.setProperty("role", "icon")
        self.more.setFixedWidth(32)
        self.more.setAccessibleName("Audio stream options")
        menu = QMenu(self.more)
        menu.addAction("Reset volume to 100%", lambda: self.audio.set_volume(
            self.index, self.level.levels, 100))
        self.more.setMenu(menu)
        self.level = Level("Application", audio.set_volume, audio.set_mute)
        layout.addWidget(self.icon, 0, 0, 2, 1, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.title, 0, 1)
        layout.addWidget(self.more, 0, 2)
        layout.addWidget(self.route, 0, 3)
        layout.addWidget(self.level, 1, 1, 1, 3)
        layout.setColumnStretch(1, 3)
        layout.setColumnStretch(3, 2)

    def route_to(self, name):
        if name and name != self.stream.get("target"):
            self.audio.move_stream(self.index, name)

    def update_stream(self, stream, options):
        self.stream = stream
        props = stream.get("properties", {})
        name = props.get("application.name") or props.get("media.name") or "Audio stream"
        self.title.setText(name)
        self.title.setToolTip(props.get("media.name") or name)
        icon = QIcon()
        for icon_name in (props.get("application.icon_name"), props.get("application.id"),
                          props.get("application.process.binary"), "audio-x-generic"):
            if icon_name and not (icon := QIcon.fromTheme(icon_name)).isNull():
                break
        self.icon.setPixmap(icon.pixmap(26) if not icon.isNull() else QIcon.fromTheme("applications-multimedia").pixmap(26))
        self.route.setAccessibleName(f"{name} output")
        self.route.update_choices(options, stream.get("target"), "Output unavailable")
        self.level.title = name
        self.level.slider.setAccessibleName(f"{name} volume")
        self.level.update_level(stream, self.index, stream.get("volume_writable", True))


class AudioPage(QWidget):
    def __init__(self, audio, sound_events, audio_settings, compact=False):
        super().__init__()
        self.audio = audio
        self.sound_events = sound_events
        self.rows = {}
        self.sinks = []
        self.default_sink = None
        self.options = []
        body = QVBoxLayout(self)
        body.setContentsMargins(*((12, 8, 12, 10) if compact else (30, 25, 30, 24)))
        body.setSpacing(9)
        body.addWidget(label("Volume mixer", "section" if compact else "pageTitle"))
        scroller = QScrollArea(widgetResizable=True)
        scroller.setFrameShape(QFrame.Shape.NoFrame)
        scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        container = QWidget()
        content = QVBoxLayout(container)
        content.setContentsMargins(0, 0, 5, 0)
        content.setSpacing(10)
        card, devices = panel()
        devices.setContentsMargins(12, 10, 12, 10)
        devices.setSpacing(7)
        self.output = DeviceChoice("Default output", lambda target: self.select_default("sink", target))
        self.microphone = DeviceChoice("Default microphone", lambda target: self.select_default("source", target))
        self.events = DeviceChoice("System sounds output", self.route_events)
        self.output_level = Level("Output", lambda *args: audio.volume("sink", *args),
                                  lambda *args: audio.mute("sink", *args))
        device_rows = QGridLayout()
        device_rows.setColumnStretch(1, 1)
        for row, (name, control) in enumerate((("Output", self.output), ("System sounds", self.events),
                                               ("Microphone", self.microphone))):
            position = row + int(row > 0)
            device_rows.addWidget(label(name, "section"), position, 0)
            device_rows.addWidget(control, position, 1)
            if row == 0:
                device_rows.addWidget(self.output_level, 1, 0, 1, 2)
        devices.addLayout(device_rows)
        content.addWidget(card)
        content.addWidget(label("Applications", "section"))
        streams_card, self.streams = panel()
        self.streams.setContentsMargins(11, 4, 11, 4)
        self.streams.setSpacing(1)
        self.empty = label("Start audio in an application to see it here.")
        self.empty.setContentsMargins(0, 8, 0, 8)
        self.streams.addWidget(self.empty)
        content.addWidget(streams_card)
        self.options_box = QWidget()
        extras = QVBoxLayout(self.options_box)
        extras.setContentsMargins(0, 0, 0, 0)
        boost = QCheckBox("Allow volume above 100%")
        boost.toggled.connect(self.boost)
        extras.addWidget(boost)
        extras.addWidget(button("Open audio settings", audio_settings), alignment=Qt.AlignmentFlag.AlignLeft)
        self.options_box.hide()
        disclosure = QPushButton("Options", checkable=True)

        def show_options(expanded):
            self.options_box.setVisible(expanded)
            if expanded:
                QTimer.singleShot(0, self.options_box,
                                  lambda: scroller.ensureWidgetVisible(self.options_box, 0, 8))

        disclosure.toggled.connect(show_options)
        content.addWidget(disclosure, alignment=Qt.AlignmentFlag.AlignLeft)
        content.addWidget(self.options_box)
        content.addStretch(1)
        scroller.setWidget(container)
        body.addWidget(scroller, 1)
        self.status = label("")
        body.addWidget(self.status)
        self.maximum = 100
        self.audio.changed.connect(self.update_mixer)
        self.sound_events.changed.connect(self.update_events)
        self.update_events()
        if self.audio.snapshot:
            self.update_mixer(self.audio.snapshot, "")

    def activate(self):
        self.sound_events.start()
        self.audio.refresh()

    def select_default(self, kind, target):
        if target:
            self.audio.set_default(kind, target)

    def route_events(self, target):
        if target is not None:
            self.sound_events.route(target)

    def update_events(self):
        event = self.sound_events.event_object()
        options = [("", "Follow default output"), *self.options] if event else []
        self.events.update_choices(options, event.property("device") if event else None,
                                   "System audio unavailable")

    def boost(self, enabled):
        self.maximum = 150 if enabled else 100
        for level in (self.output_level, *(row.level for row in self.rows.values())):
            level.limit = self.maximum
            level.slider.setMaximum(max(level.limit, level.slider.value()))

    def update_mixer(self, snapshot, error):
        self.status.setText(error)
        if error:
            return
        devices, info = snapshot
        self.sinks = devices.get("sinks", [])
        self.default_sink = info.get("default_sink_name")
        self.options = device_options(self.sinks, self.default_sink)
        self.output.update_choices(self.options, self.default_sink)
        current = next((sink for sink in self.sinks if sink["name"] == self.default_sink), None)
        self.output_level.update_level(current, self.default_sink)
        sources = [source for source in devices.get("sources", [])
                   if source.get("properties", {}).get("device.class") != "monitor"
                   and not source.get("name", "").endswith(".monitor")]
        self.microphone.update_choices(device_options(sources, None), info.get("default_source_name"))
        self.update_events()
        names = {sink["index"]: sink["name"] for sink in self.sinks}
        streams = [
            dict(stream, target=names.get(stream.get("sink")))
            for stream in devices.get("sink_inputs", [])
            if stream.get("properties", {}).get("media.role") != "event"
            and stream.get("properties", {}).get("module-stream-restore.id") != "sink-input-by-media-role:event"
        ]
        indices = {stream["index"] for stream in streams}
        for index in self.rows.keys() - indices:
            row = self.rows.pop(index)
            self.streams.removeWidget(row)
            row.deleteLater()
        for stream in streams:
            index = stream["index"]
            if index not in self.rows:
                row = StreamRow(self.audio, stream)
                row.level.limit = self.maximum
                self.rows[index] = row
                self.streams.addWidget(row)
            self.rows[index].update_stream(stream, self.options)
        self.empty.setVisible(not streams)
        if streams:
            self.status.setText(count_label(len(streams), "playback stream"))
