"""Local system, network and disk monitors."""

from collections import Counter, deque
import ipaddress
import os
from pathlib import Path
import socket
from time import monotonic
import uuid

from gi.repository import Gio, GLib
import psutil
from PySide6.QtCore import QPointF, QProcess, Qt, Signal, QObject
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QScrollArea,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from theme import Dropdown, colors
from views import button, label, panel


SPEED_ENDPOINT = "https://speed.cloudflare.com"


def quantity(amount, rate=False):
    size = float(amount)
    unit = "B"
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if size < 1000 or unit == "PB":
            break
        size /= 1000
    precision = 1 if unit != "B" and size < 100 else 0
    return f"{size:.{precision}f} {unit}" + ("/s" if rate else "")


class History(QWidget):
    def __init__(self, maximum=None):
        super().__init__()
        self.setMinimumHeight(52)
        self.setMaximumHeight(76)
        self.series = [deque(maxlen=60), deque(maxlen=60)]
        self.maximum = maximum

    def sample(self, *values):
        for points, value in zip(self.series, values):
            if value is not None:
                points.append(value)
        self.update()

    def reset(self):
        for points in self.series:
            points.clear()
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = colors()
        baseline = self.height() - 5
        for y in (5, (baseline + 5) // 2, baseline):
            painter.setPen(QPen(QColor(palette["chartGrid"]), 1))
            painter.drawLine(0, y, self.width(), y)
        largest = self.maximum or max([1, *(max(line, default=0) for line in self.series)])
        for values, accent in zip(self.series, (palette["chartFirst"], palette["chartSecond"])):
            if not values:
                continue
            stride = max(1, self.width() - 4) / (values.maxlen - 1)
            points = [QPointF(2 + i * stride, baseline - (baseline - 5) * min(1, value / largest))
                      for i, value in enumerate(values, values.maxlen - len(values))]
            if len(points) > 1:
                area = QPainterPath(points[0])
                for point in points[1:]:
                    area.lineTo(point)
                area.lineTo(points[-1].x(), baseline)
                area.lineTo(points[0].x(), baseline)
                area.closeSubpath()
                fill = QColor(accent)
                fill.setAlpha(26)
                painter.fillPath(area, fill)
            painter.setPen(QPen(QColor(accent), 1.7))
            if len(points) == 1:
                painter.drawPoint(points[0])
            else:
                painter.drawPolyline(QPolygonF(points))


class MonitorPage(QWidget):
    def __init__(self, title, compact):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(*((12, 8, 12, 10) if compact else (30, 25, 30, 24)))
        outer.setSpacing(9)
        outer.addWidget(label(title, "section" if compact else "pageTitle"))
        scroll = QScrollArea(widgetResizable=True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        container = QWidget()
        self.body = QVBoxLayout(container)
        self.body.setContentsMargins(0, 0, 5, 0)
        self.body.setSpacing(10)
        scroll.setWidget(container)
        outer.addWidget(scroll, 1)

    def card(self, title=""):
        card, layout = panel()
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(7)
        if title:
            layout.addWidget(label(title, "section"))
        self.body.addWidget(card)
        return layout


def number_tile(caption):
    card, layout = panel()
    card.setProperty("role", "metricTile")
    layout.setContentsMargins(10, 8, 10, 8)
    layout.setSpacing(3)
    value = label("—", "metricValue")
    layout.addWidget(label(caption))
    layout.addWidget(value)
    return card, value


def meter_line(layout, caption):
    row = QHBoxLayout()
    row.addWidget(label(caption, "section"))
    rail = QProgressBar()
    rail.setTextVisible(False)
    rail.setAccessibleName(caption)
    row.addWidget(rail, 1)
    text = label("—", "readout")
    row.addWidget(text)
    layout.addLayout(row)
    return rail, text


def update_meter(meter, value):
    rail, text = meter
    rail.setValue(round(min(100, max(0, value or 0))))
    rail.setVisible(value is not None)
    text.setText("Unavailable" if value is None else f"{value:.0f}%")


def read_number(path):
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


class SystemState:
    def __init__(self):
        self.cpus = max(1, psutil.cpu_count() or 1)
        self.before_gpu = None
        self.cards = [card for card in Path("/sys/class/drm").glob("card*") if card.name[4:].isdigit()]
        psutil.cpu_percent()

    def render_counters(self, pids):
        samples = {}
        for pid in pids:
            proc = Path(f"/proc/{pid}")
            try:
                for fd in (proc / "fd").iterdir():
                    try:
                        if not os.readlink(fd).startswith("/dev/dri/"):
                            continue
                        raw = (proc / "fdinfo" / fd.name).read_text()
                    except OSError:
                        continue
                    info = dict(line.split(":", 1) for line in raw.splitlines() if ":" in line)
                    client = info.get("drm-client-id")
                    render = info.get("drm-engine-render", "").split()
                    if client and render and render[0].isdigit():
                        key = info.get("drm-pdev"), client
                        samples[key] = max(samples.get(key, 0), int(render[0]))
            except OSError:
                continue
        return samples

    def gpu(self, pids):
        frequency = next((mhz for card in self.cards
                          if (mhz := read_number(card / "gt_act_freq_mhz")) is not None), None)
        busy = next((value for card in self.cards
                     if (value := read_number(card / "device/gpu_busy_percent")) is not None), None)
        if busy is not None:
            return busy, "GPU usage", frequency
        now, counters = monotonic(), self.render_counters(pids)
        before, self.before_gpu = self.before_gpu, (now, counters)
        if before and counters and (previous := counters.keys() & before[1].keys()):
            units = sum(max(0, counters[key] - before[1][key]) for key in previous)
            return min(100.0, units / (now - before[0]) / 1e7), "GPU render · visible clients", frequency
        return None, "GPU usage", frequency

    def sample(self):
        processes = []
        for process in psutil.process_iter(["name", "username"]):
            try:
                name = process.info["name"] or str(process.pid)
                usage = process.cpu_percent() / self.cpus
                if not process.info["username"] or process.info["username"] == "root":
                    processes.append((usage, name, None))
                else:
                    processes.append((usage, name, process.pid))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        sensors = psutil.sensors_temperatures()
        cpu_sensors = sensors.get("coretemp") or sensors.get("k10temp") or sensors.get("zenpower") or []
        package = [sensor.current for sensor in cpu_sensors
                   if sensor.label.startswith(("Package", "Tdie", "Tctl"))]
        gpu_sensors = [sensor for chip, values in sensors.items()
                       if chip.startswith(("amdgpu", "nouveau", "nvidia", "i915", "xe"))
                       for sensor in values]
        gpu, gpu_label, frequency = self.gpu([pid for _, _, pid in processes if pid])
        return {
            "cpu": psutil.cpu_percent(), "memory": psutil.virtual_memory().percent,
            "cpu_temp": max(package or [sensor.current for sensor in cpu_sensors], default=None),
            "gpu_temp": max((sensor.current for sensor in gpu_sensors), default=None),
            "gpu": gpu, "gpu_label": gpu_label, "frequency": frequency,
            "processes": sorted(processes, key=lambda item: (-item[0], item[1].casefold()))[:12],
        }


class SystemPage(MonitorPage):
    def __init__(self, compact=False):
        super().__init__("System", compact)
        heat = self.card("Temperatures")
        heat_row = QHBoxLayout()
        self.temperatures = {}
        for key, title in (("cpu_temp", "CPU temperature"), ("gpu_temp", "GPU temperature")):
            tile, self.temperatures[key] = number_tile(title)
            heat_row.addWidget(tile, 1)
        heat.addLayout(heat_row)
        usage = self.card("Hardware usage")
        self.cpu = meter_line(usage, "CPU")
        self.cpu_graph = History(100)
        self.cpu_graph.setAccessibleName("CPU usage history")
        usage.addWidget(self.cpu_graph)
        self.gpu = meter_line(usage, "GPU usage")
        self.gpu_caption = label("")
        usage.addWidget(self.gpu_caption)
        self.memory = meter_line(usage, "Memory")
        self.processes = QWidget()
        self.processes.setObjectName("processRows")
        self.processes.setToolTip("Process CPU as a percentage of total CPU capacity.")
        process_grid = QGridLayout(self.processes)
        process_grid.setContentsMargins(2, 0, 2, 0)
        process_grid.setColumnStretch(1, 1)
        process_grid.setVerticalSpacing(3)
        self.process_rows = []
        self.process_limit = 6 if compact else 12
        self.process_icons = {}
        for index in range(12):
            icon, name, value = QLabel(), label(""), label("", "readout")
            icon.setFixedSize(18, 18)
            name.setWordWrap(False)
            name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            for column, control in enumerate((icon, name, value)):
                alignment = Qt.AlignmentFlag.AlignRight if column == 2 else Qt.AlignmentFlag.AlignVCenter
                process_grid.addWidget(control, index, column, alignment)
            self.process_rows.append((icon, name, value))
        detail = QToolButton(text="Top CPU processes", checkable=True, checked=True, autoRaise=True)
        detail.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        detail.setProperty("role", "disclosure")
        detail.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        detail.setArrowType(Qt.ArrowType.DownArrow)
        detail.toggled.connect(lambda on: detail.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow))
        detail.toggled.connect(self.processes.setVisible)
        usage.addWidget(detail, alignment=Qt.AlignmentFlag.AlignLeft)
        usage.addWidget(self.processes)
        self.expand = button("Show all 12", self.toggle_processes)
        if compact:
            detail.toggled.connect(self.expand.setVisible)
            usage.addWidget(self.expand, alignment=Qt.AlignmentFlag.AlignLeft)
        else:
            self.expand.setHidden(True)
        self.current_processes = []
        self.body.addStretch()

    def update_state(self, state):
        for key, target in self.temperatures.items():
            temp = state[key]
            target.setText("No reading" if temp is None else f"{temp:.0f} °C")
            device = "GPU" if key == "gpu_temp" else "CPU"
            target.parentWidget().setToolTip(
                f"Linux does not expose a {device} temperature reading on this machine."
                if temp is None else ""
            )
        update_meter(self.cpu, state["cpu"])
        self.cpu_graph.sample(state["cpu"])
        frequency = state["frequency"]
        update_meter(self.gpu, state["gpu"])
        update_meter(self.memory, state["memory"])
        if state["gpu_label"].endswith("clients"):
            caption = "Estimated render activity from visible apps"
        elif state["gpu"] is None and frequency is not None:
            caption = f"GPU clock: {frequency} MHz"
        else:
            caption = ""
        self.gpu_caption.setText(caption)
        self.gpu_caption.setVisible(bool(caption))
        self.current_processes = state["processes"]
        self.show_processes()

    def toggle_processes(self):
        self.process_limit = 6 if self.process_limit == 12 else 12
        self.expand.setText("Show all 12" if self.process_limit == 6 else "Show top 6")
        self.show_processes()

    def show_processes(self):
        for index, (icon, title, amount) in enumerate(self.process_rows):
            visible = index < min(self.process_limit, len(self.current_processes))
            for widget in (icon, title, amount):
                widget.setVisible(visible)
            if visible:
                value, name, _pid = self.current_processes[index]
                if name not in self.process_icons:
                    self.process_icons[name] = QIcon.fromTheme(
                        name, QIcon.fromTheme("application-x-executable")
                    ).pixmap(16)
                icon.setPixmap(self.process_icons[name])
                title.setText(name)
                title.setToolTip(name)
                amount.setText(f"{value:.1f}%")


class RatePair:
    def __init__(self):
        self.previous = {}
        self.time = monotonic()
        self.totals = [0, 0]

    def sample(self, counters):
        now = monotonic()
        totals = [sum(max(0, counters[key][i] - self.previous[key][i])
                      for key in counters.keys() & self.previous.keys()) for i in range(2)]
        rates = [value / max(0.001, now - self.time) for value in totals]
        self.previous, self.time = counters, now
        self.totals = [old + new for old, new in zip(self.totals, totals)]
        return rates, list(self.totals)


def rate_tiles(layout, titles):
    row = QHBoxLayout()
    values = []
    for title in titles:
        tile, value = number_tile(title)
        row.addWidget(tile, 1)
        values.append(value)
    layout.addLayout(row)
    history = History()
    history.setAccessibleName(" and ".join(titles) + " history")
    layout.addWidget(history)
    totals = label("This session · —")
    layout.addWidget(totals)
    return values, history, totals


def show_rates(controls, sample, glyphs=("↓", "↑")):
    values, history, totals = controls
    rates, amounts = sample
    for target, rate in zip(values, rates):
        target.setText(quantity(rate, True))
    history.sample(*rates)
    totals.setText("This session   " + "    ".join(
        f"{glyph} {quantity(amount)}" for glyph, amount in zip(glyphs, amounts)
    ))


class NetworkState:
    def __init__(self):
        self.rates = RatePair()
        self.rates.sample(self.counters())
        self.last_connections = -100
        self.apps = {}

    def counters(self):
        interfaces = psutil.net_io_counters(pernic=True)
        names = [name for name in interfaces if Path("/sys/class/net", name, "device").exists()]
        return {name: (interfaces[name].bytes_recv, interfaces[name].bytes_sent)
                for name in (names or [name for name in interfaces if name != "lo"])}

    def sample(self):
        stats = psutil.net_if_stats()
        addresses = [
            (address.address, name)
            for name, entries in psutil.net_if_addrs().items()
            if name in stats and stats[name].isup
            for address in entries if address.family == socket.AF_INET
            and not ipaddress.ip_address(address.address).is_loopback
        ]
        if monotonic() - self.last_connections >= 3:
            self.last_connections = monotonic()
            connections = Counter()
            try:
                for conn in psutil.net_connections(kind="inet"):
                    if conn.pid and conn.raddr and conn.status == psutil.CONN_ESTABLISHED:
                        if not ipaddress.ip_address(conn.raddr.ip).is_loopback:
                            connections[conn.pid] += 1
            except (psutil.AccessDenied, OSError):
                self.apps = None
            else:
                self.apps = Counter()
                for pid, count in connections.items():
                    try:
                        self.apps[psutil.Process(pid).name()] += count
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
        return self.rates.sample(self.counters()), addresses, self.apps


class SpeedTest(QObject):
    changed = Signal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.finished.connect(self.complete)
        self.process.errorOccurred.connect(self.failed)
        self.direction = ""
        self.down = 0.0

    def toggle(self):
        if self.direction:
            self.direction = ""
            self.process.kill()
            self.changed.emit("Speed test canceled.", False)
        elif self.process.state() == QProcess.ProcessState.NotRunning:
            self.run("Downloading")

    def run(self, direction):
        self.direction = direction
        self.changed.emit(direction + "…", True)
        options = ["-q", "--silent", "--show-error", "--fail", "--max-time", "18", "--output", "/dev/null"]
        if direction == "Downloading":
            stamp = uuid.uuid4().hex
            args = ["--max-filesize", "4000000", "--write-out", "%{size_download}:%{speed_download}",
                    f"{SPEED_ENDPOINT}/__down?bytes=4000000&test={stamp}"]
        else:
            args = ["--write-out", "%{size_upload}:%{speed_upload}", "--data-binary", "@-", f"{SPEED_ENDPOINT}/__up"]
        self.process.start("curl", options + args)
        if direction == "Uploading":
            self.process.write(b"\0" * 2_000_000)
            self.process.closeWriteChannel()

    def failed(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.direction = ""
            self.changed.emit("Speed test requires curl.", False)

    def complete(self, code, _status):
        direction, self.direction = self.direction, ""
        result = bytes(self.process.readAllStandardOutput())
        self.process.readAllStandardError()
        if not direction:
            return
        try:
            amount, rate = map(float, result.split(b":"))
        except ValueError:
            amount, rate = 0, 0
        if code or amount != (4_000_000 if direction == "Downloading" else 2_000_000):
            self.changed.emit("Cloudflare speed test did not complete.", False)
        elif direction == "Downloading":
            self.down = rate * 8 / 1_000_000
            self.run("Uploading")
        else:
            self.changed.emit(f"↓ {self.down:.1f} Mbps    ↑ {rate * 8 / 1_000_000:.1f} Mbps", False)


class NetworkPage(MonitorPage):
    def __init__(self, test, compact=False):
        super().__init__("Network", compact)
        traffic = self.card()
        self.transfer = rate_tiles(traffic, ("Download", "Upload"))
        identity = self.card("Local IPv4")
        self.address = label("")
        identity.addWidget(self.address)
        activity = self.card("Apps with connections")
        self.connections = label("")
        activity.addWidget(self.connections)
        test_card = self.card("Connection speed")
        self.test = button("Run speed test", test.toggle)
        self.test.setToolTip("Uses Cloudflare’s speed test service and transfers about 6 MB.")
        self.result = label("Cloudflare · About 6 MB per test")
        test_row = QHBoxLayout()
        test_row.addWidget(self.result, 1)
        test_row.addWidget(self.test)
        test_card.addLayout(test_row)
        self.body.addStretch()
        test.changed.connect(self.speed_result)

    def speed_result(self, result, active):
        self.result.setText(result)
        self.test.setText("Cancel test" if active else "Run speed test")

    def update_state(self, state):
        transfer, addresses, connections = state
        show_rates(self.transfer, transfer)
        self.address.setText("\n".join(f"{address}  ·  {interface}" for address, interface in addresses)
                             or "No active IPv4 address found.")
        if connections is None:
            status = "Connection information is unavailable."
        else:
            status = "\n".join(f"{name}  ·  {count}" for name, count in connections.most_common(6))
            status = status or "No established connections with a visible process."
        self.connections.setText(status)


UDISKS = "org.freedesktop.UDisks2"
PROPERTIES = "org.freedesktop.DBus.Properties"


def disk_health(kernel_name):
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM)

        def call(path, interface, method, parameters):
            return bus.call_sync(UDISKS, path, interface, method, parameters, None,
                                 Gio.DBusCallFlags.NO_AUTO_START, 600, None).unpack()[0]

        block = f"/org/freedesktop/UDisks2/block_devices/{kernel_name.replace('-', '_2d')}"
        drive = call(block, PROPERTIES, "Get", GLib.Variant("(ss)", (UDISKS + ".Block", "Drive")))
        if drive == "/":
            return {}
        nvme = UDISKS + ".NVMe.Controller"
        record = call(drive, PROPERTIES, "GetAll", GLib.Variant("(s)", (nvme,)))
        if record.get("SmartUpdated"):
            details = {
                "Status": ", ".join(record["SmartCriticalWarning"]) or "No critical warnings",
                "Temperature": f"{record['SmartTemperature'] - 273.15:.0f} °C" if record["SmartTemperature"] else "",
                "Power on": f"{record['SmartPowerOnHours']:,} hours",
            }
            try:
                extras = call(drive, nvme, "SmartGetAttributes", GLib.Variant(
                    "(a{sv})", ({"auth.no_user_interaction": GLib.Variant("b", True)},)))
            except GLib.Error:
                return details
            for key, name in (("total_data_read", "Lifetime read"), ("total_data_written", "Lifetime written")):
                if key in extras:
                    details[name] = quantity(extras[key])
            if "percent_used" in extras:
                details["Estimated life remaining"] = f"{max(0, 100 - extras['percent_used'])}%"
            if "power_cycles" in extras:
                details["Power cycles"] = f"{extras['power_cycles']:,}"
            return details
        ata = call(drive, PROPERTIES, "GetAll", GLib.Variant("(s)", (UDISKS + ".Drive.Ata",)))
        if ata.get("SmartUpdated"):
            return {
                "Status": "Failing" if ata.get("SmartFailing") else "No failure reported",
                "Temperature": f"{ata['SmartTemperature'] - 273.15:.0f} °C" if ata.get("SmartTemperature") else "",
                "Power on": f"{ata.get('SmartPowerOnSeconds', 0) // 3600:,} hours",
            }
    except GLib.Error:
        pass
    return {}


class DiskState:
    def __init__(self):
        self.meters = {}
        for name, data in (psutil.disk_io_counters(perdisk=True) or {}).items():
            meter = self.meters.setdefault(name, RatePair())
            meter.sample({name: (data.read_bytes, data.write_bytes)})
        self.health = {}

    def sample(self, selected=None):
        volumes = {}
        for entry in psutil.disk_partitions():
            if entry.device.startswith("/dev/"):
                volumes.setdefault(os.path.realpath(entry.device), entry)
        selected = selected if selected in volumes else next(iter(volumes), None)
        streams = psutil.disk_io_counters(perdisk=True) or {}
        rates = {}
        for name, data in streams.items():
            meter = self.meters.setdefault(name, RatePair())
            rates[name] = meter.sample({name: (data.read_bytes, data.write_bytes)})
        if not selected:
            return volumes, None, None, None, {}
        item = volumes[selected]
        try:
            usage = psutil.disk_usage(item.mountpoint)
        except OSError:
            usage = None
        name = Path(selected).name
        if monotonic() - self.health.get(name, (-1000, {}))[0] > 60:
            self.health[name] = (monotonic(), disk_health(name))
        return volumes, selected, usage, rates.get(name), self.health[name][1]


class DiskPage(MonitorPage):
    selected = Signal()

    def __init__(self, compact=False):
        super().__init__("Disks", compact)
        volume = self.card("Select volume")
        self.volumes = Dropdown()
        self.volumes.setAccessibleName("Volume")
        self.volumes.activated.connect(self.selection_changed)
        volume.addWidget(self.volumes)
        self.usage = meter_line(volume, "Disk usage")
        self.summary = label("")
        volume.addWidget(self.summary)
        self.transfer = rate_tiles(self.card("Live activity"), ("Read", "Write"))
        self.health_grid = QGridLayout()
        self.card("Drive health · SMART").addLayout(self.health_grid)
        self.body.addStretch()
        self.last_health = None
        self.last_volume = None

    def selection_changed(self, _index):
        self.selected.emit()

    def update_state(self, state):
        volumes, current, usage, rates, health = state
        options = [(path, f"{'System' if item.mountpoint == '/' else item.mountpoint}  ·  {item.fstype.upper()}")
                   for path, item in volumes.items()]
        if options != [(self.volumes.itemData(i), self.volumes.itemText(i)) for i in range(self.volumes.count())]:
            self.volumes.clear()
            for path, title in options:
                self.volumes.addItem(title, path)
        self.volumes.setCurrentIndex(self.volumes.findData(current))
        self.volumes.setEnabled(bool(volumes))
        if current != self.last_volume:
            self.transfer[1].reset()
        self.last_volume = current
        update_meter(self.usage, usage.percent if usage else None)
        self.summary.setText(
            f"{quantity(usage.used)} of {quantity(usage.total)} used\n"
            f"{quantity(usage.free)} available" if usage else "Volume usage is unavailable."
        )
        if rates:
            show_rates(self.transfer, rates, ("R", "W"))
        else:
            self.transfer[1].reset()
            for value in self.transfer[0]:
                value.setText("—")
            self.transfer[2].setText("No volume I/O counters available.")
        if health != self.last_health:
            self.last_health = health
            while self.health_grid.count():
                self.health_grid.takeAt(0).widget().deleteLater()
            for row, (title, value) in enumerate(health.items()):
                self.health_grid.addWidget(label(title), row, 0)
                self.health_grid.addWidget(label(value, "readout"), row, 1, Qt.AlignmentFlag.AlignRight)
            if not health:
                self.health_grid.addWidget(label("SMART data is unavailable for this drive."), 0, 0)
