"""KDE session and PipeWire integrations for Switchyard."""

from functools import cache
import json
from pathlib import Path
import tempfile
import uuid

from gi.repository import Gio, GLib
from PySide6.QtCore import ClassInfo, QObject, QProcess, QProcessEnvironment, QTimer, Signal, Slot
from PySide6.QtDBus import QDBusConnection, QDBusMessage
from PySide6.QtGui import QGuiApplication


APP_ID = "io.switchyard.Utility"
KWIN = "org.kde.KWin"
SCRIPTING = "org.kde.kwin.Scripting"
POWER = "org.freedesktop.PowerManagement"
CLIPBOARD = "org.kde.klipper"
SCREEN_SAVER = "org.freedesktop.ScreenSaver"
NOTIFICATIONS = "org.freedesktop.Notifications"
NIGHT_LIGHT = "org.kde.KWin.NightLight"


class DesktopError(Exception):
    pass


def dbus(bus, service, path, interface, method, *args):
    message = QDBusMessage.createMethodCall(service, path, interface, method)
    message.setArguments(list(args))
    reply = bus.call(message, timeout=1200)
    if reply.type() == QDBusMessage.MessageType.ErrorMessage:
        raise DesktopError(reply.errorMessage())
    return reply.arguments()


def clipboard_call(bus, method, *args):
    return dbus(bus, CLIPBOARD, "/klipper", CLIPBOARD + ".klipper", method, *args)


def has_service(bus, service):
    interface = bus.interface() if bus is not None else None
    if interface is None:
        return False
    reply = interface.isServiceRegistered(service)
    return reply.isValid() and reply.value()


def copy_text(bus, text):
    if has_service(bus, CLIPBOARD):
        clipboard_call(bus, "setClipboardContents", text)
    else:
        QGuiApplication.clipboard().setText(text)


@cache
def session_bus():
    # Inhibitors belong to the unique sender and must share a live connection.
    return Gio.bus_get_sync(Gio.BusType.SESSION)


def session_call(endpoint, method, parameters):
    try:
        return session_bus().call_sync(*endpoint, method, parameters, None,
                                       Gio.DBusCallFlags.NONE, 1200, None).unpack()
    except GLib.Error as exc:
        raise DesktopError(str(exc)) from exc


class SessionSwitch:
    def __init__(self, endpoint, start_method, stop_method, parameters=None):
        self.endpoint = endpoint
        self.start_method = start_method
        self.stop_method = stop_method
        self.parameters = parameters
        self.cookie = None

    @property
    def active(self):
        return self.cookie is not None

    def toggle(self):
        if self.active:
            self.stop()
        else:
            self.cookie, = session_call(self.endpoint, self.start_method, self.parameters)

    def stop(self):
        if self.active:
            session_call(self.endpoint, self.stop_method, GLib.Variant("(u)", (self.cookie,)))
            self.cookie = None

    def property(self, name):
        service, path, interface = self.endpoint
        value, = session_call((service, path, "org.freedesktop.DBus.Properties"), "Get",
                              GLib.Variant("(ss)", (interface, name)))
        return value


def focus_switches():
    return (
        SessionSwitch(
            (NOTIFICATIONS, "/org/freedesktop/Notifications", NOTIFICATIONS),
            "Inhibit", "UnInhibit",
            GLib.Variant("(ssa{sv})", (APP_ID, "Do Not Disturb was enabled", {})),
        ),
        SessionSwitch((NIGHT_LIGHT, "/org/kde/KWin/NightLight", NIGHT_LIGHT),
                      "inhibit", "uninhibit"),
    )


class ColorPicker(QObject):
    finished = Signal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pending = False
        self.cancellable = Gio.Cancellable()

    def start(self):
        if self.pending:
            return
        try:
            bus = session_bus()
        except GLib.Error as exc:
            self.finished.emit(None, str(exc))
            return
        self.pending = True
        bus.call(KWIN, "/ColorPicker", "org.kde.kwin.ColorPicker", "pick", None,
                 GLib.VariantType.new("((u))"), Gio.DBusCallFlags.NONE, 120000,
                 self.cancellable, self.complete)

    def complete(self, bus, result):
        self.pending = False
        try:
            (rgba,), = bus.call_finish(result).unpack()
        except GLib.Error as exc:
            name = Gio.DBusError.get_remote_error(exc)
            if name == "org.kde.kwin.ColorPicker.Error.Cancelled":
                self.finished.emit(None, "")
            elif not exc.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                self.finished.emit(None, str(exc))
        else:
            self.finished.emit(rgba, "")


class KeepAwake:
    SERVICES = (
        (POWER, "/org/freedesktop/PowerManagement/Inhibit", POWER + ".Inhibit"),
        (SCREEN_SAVER, "/ScreenSaver", SCREEN_SAVER),
    )

    def __init__(self):
        self.cookies = {}

    @property
    def active(self):
        return bool(self.cookies)

    def start(self):
        try:
            for endpoint in self.SERVICES:
                if endpoint not in self.cookies:
                    self.cookies[endpoint] = session_call(
                        endpoint, "Inhibit",
                        GLib.Variant("(ss)", ("Switchyard", "Keep Awake was enabled")),
                    )[0]
        except DesktopError:
            self.stop()
            raise

    def stop(self):
        errors = []
        for endpoint in tuple(self.cookies):
            try:
                session_call(endpoint, "UnInhibit", GLib.Variant("(u)", (self.cookies[endpoint],)))
            except DesktopError as exc:
                errors.append(str(exc))
            else:
                del self.cookies[endpoint]
        if errors:
            raise DesktopError(errors[0])


@ClassInfo(**{"D-Bus Interface": APP_ID})
class WindowBridge(QObject):
    finished = Signal(object, str)

    def __init__(self, bus, parent=None, reply_path="/Windows"):
        super().__init__(parent)
        self.bus = bus
        self.reply_path = reply_path
        self.scratch = tempfile.TemporaryDirectory(prefix="switchyard-")
        self.request = None
        self.timeout = QTimer(self, singleShot=True, interval=2500)
        self.timeout.timeout.connect(self.expire)
        self.exported = self.bus.registerObject(
            reply_path, self, QDBusConnection.RegisterOption.ExportAllSlots
        )

    def run(self, operation, window_id="", layout=""):
        if self.request:
            return False
        if not self.exported:
            self.finished.emit(None, "Could not register the KWin reply receiver.")
            return False
        token = uuid.uuid4().hex
        name = "switchyard-" + token
        path = Path(self.scratch.name, name + ".js")
        params = [self.bus.baseService(), self.reply_path, token, operation, window_id, layout]
        try:
            script = Path(__file__).with_name("windows.js").read_text()
            path.write_text("const request = " + json.dumps(params) + ";\n" + script)
            self.request = (token, name, path)
            script_id, = dbus(self.bus, KWIN, "/Scripting", SCRIPTING,
                              "loadScript", str(path), name)
            if script_id < 0:
                raise DesktopError("KWin could not load the window action.")
            dbus(self.bus, KWIN, f"/Scripting/Script{script_id}", "org.kde.kwin.Script", "run")
            self.timeout.start()
        except (DesktopError, OSError) as exc:
            self.clear()
            self.finished.emit(None, str(exc))
            return False
        return True

    @Slot(str, str)
    def receive(self, token, reply):
        if self.request and self.request[0] == token:
            # Let the reply return to KWin before unloading the script.
            QTimer.singleShot(0, lambda: self.complete(token, reply))

    def complete(self, token, reply):
        if not self.request or self.request[0] != token:
            return
        self.clear()
        try:
            result = json.loads(reply)
        except ValueError:
            self.finished.emit(None, "KWin returned an unreadable response.")
        else:
            self.finished.emit(result.get("windows"), result.get("error", ""))

    def expire(self):
        self.clear()
        self.finished.emit(None, "KWin did not respond to the window action.")

    def clear(self):
        self.timeout.stop()
        if self.request:
            _, name, path = self.request
            self.request = None
            try:
                dbus(self.bus, KWIN, "/Scripting", SCRIPTING, "unloadScript", name)
            except DesktopError:
                pass
            path.unlink(missing_ok=True)

    def close(self):
        self.clear()
        self.scratch.cleanup()


class Audio(QObject):
    changed = Signal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("LC_ALL", "C.UTF-8")
        self.process.setProcessEnvironment(env)
        self.process.finished.connect(self.complete)
        self.process.errorOccurred.connect(self.failed)
        self.queue = []
        self.pending = None
        self.devices = None
        self.snapshot = None

    def refresh(self):
        reading = ("devices", "defaults")
        if self.pending not in reading and not any(job[0] in reading for job in self.queue):
            self.queue.extend((
                ("devices", ("-f", "json", "list")),
                ("defaults", ("-f", "json", "info")),
            ))
            self.run_next()

    def set_volume(self, index, levels, percent):
        self.volume("sink-input", index, levels, percent)

    def volume(self, kind, target, levels, percent):
        average = sum(levels) / len(levels) if levels else 0
        volumes = [
            f"{min(300, round(percent * value / average)) if average else percent}%"
            for value in (levels or [0])
        ]
        self.request("set", f"set-{kind}-volume", str(target), *volumes)

    def set_mute(self, index, mute):
        self.mute("sink-input", index, mute)

    def mute(self, kind, target, muted):
        self.request("set", f"set-{kind}-mute", str(target), str(int(muted)))

    def set_default(self, kind, name):
        self.request("set", f"set-default-{kind}", name)

    def move_stream(self, index, name):
        self.request("set", "move-sink-input", str(index), name)

    def request(self, kind, *args):
        job = (kind, args)
        if kind == "set" and self.queue and self.queue[-1][1][:2] == args[:2]:
            self.queue[-1] = job
        else:
            self.queue.append(job)
        self.run_next()

    def run_next(self):
        if not self.pending and self.queue:
            self.pending, args = self.queue.pop(0)
            self.process.start("pactl", list(args))

    def failed(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.pending = None
            self.queue.clear()
            self.changed.emit(None, "pactl is unavailable. Install pulseaudio-utils.")

    def complete(self, exit_code, _status):
        kind, self.pending = self.pending, None
        stdout = bytes(self.process.readAllStandardOutput())
        stderr = bytes(self.process.readAllStandardError()).decode(errors="replace").strip()
        if exit_code:
            self.changed.emit(None, stderr or "The audio command failed.")
        elif kind in {"devices", "defaults"}:
            try:
                data = json.loads(stdout)
            except ValueError:
                self.changed.emit(None, "The audio server returned unreadable stream data.")
            else:
                if kind == "devices":
                    self.devices = data
                elif self.devices is not None:
                    self.snapshot = (self.devices, data)
                    self.changed.emit(self.snapshot, "")
                    self.devices = None
        if kind == "set":
            self.refresh()
        self.run_next()
