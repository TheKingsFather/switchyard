#!/usr/bin/env python3
"""Install Switchyard for the current Linux user."""

import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import venv

from version import VERSION


SOURCE = Path(__file__).resolve().parent
FILES = (
    "switchyard.py", "audio_view.py", "desktop.py", "monitor.py", "theme.py",
    "tools_view.py", "views.py", "version.py", "style.qss", "windows.js",
)
MARKER = "# Managed by the Switchyard installer."
CHECK = """
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Switchyard requires Python 3.10 or newer.")
from gi.repository import Gio, GLib
import psutil
from PySide6 import QtCore, QtDBus, QtQml, QtWidgets
if tuple(map(int, QtCore.qVersion().split(".")[:2])) < (6, 8):
    raise SystemExit("Switchyard requires PySide6 with Qt 6.8 or newer.")
"""


def run(arguments):
    subprocess.run([str(arg) for arg in arguments], check=True)


def python_runtime(root, build_venv):
    if not build_venv:
        return Path(sys.executable).absolute()
    run([sys.executable, "-c", "from gi.repository import Gio, GLib"])
    environment = root / "venv"
    python = environment / "bin/python"
    if not python.is_file():
        venv.EnvBuilder(system_site_packages=True, with_pip=True).create(environment)
    run([python, "-m", "pip", "install", "PySide6>=6.8,<7", "psutil>=5.9,<9"])
    return python


def write_launcher(target, python, script):
    target.parent.mkdir(parents=True, exist_ok=True)
    line = " ".join(shlex.quote(str(value)) for value in (python, script))
    with tempfile.NamedTemporaryFile(mode="w", dir=target.parent, delete=False) as output:
        candidate = Path(output.name)
        output.write(f'#!/bin/sh\n{MARKER}\nexec {line} "$@"\n')
    try:
        candidate.chmod(0o755)
        candidate.replace(target)
    finally:
        candidate.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venv", action="store_true",
                        help="Install Qt and psutil from PyPI into a per-user virtual environment.")
    parser.add_argument("--bin-dir", type=Path, default=Path.home() / ".local/bin",
                        help="Directory for the switchyard launch command.")
    args = parser.parse_args()
    if not sys.platform.startswith("linux") or sys.version_info < (3, 10):
        parser.error("Switchyard requires Linux and Python 3.10 or newer.")

    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share").expanduser().absolute()
    root = data / "switchyard"
    target = args.bin_dir.expanduser().absolute() / "switchyard"
    if target.is_symlink() or (target.exists() and (
            not target.is_file() or MARKER not in target.read_text(errors="replace")[:200])):
        parser.error(f"{target} already exists. Choose a different --bin-dir.")
    try:
        python = python_runtime(root, args.venv)
        run([python, "-c", CHECK])
        versions = root / "releases"
        versions.mkdir(parents=True, exist_ok=True)
        # Keep installs independent so a running version can finish using its assets.
        with tempfile.TemporaryDirectory(prefix=".stage-", dir=versions) as folder:
            stage = Path(folder)
            for filename in FILES:
                shutil.copy2(SOURCE / filename, stage / filename)
            run([python, stage / "switchyard.py", "--version"])
            release = versions / f"{VERSION}-{stage.name.removeprefix('.stage-')}"
            stage.rename(release)
        script = release / "switchyard.py"
        env = dict(os.environ, XDG_DATA_HOME=str(data))
        subprocess.run([str(python), str(script), "--install-launcher"], check=True, env=env)
        write_launcher(target, python, script)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Install failed. Check the prerequisites in README.md.\n{exc}", file=sys.stderr)
        return 1
    print(f"Installed Switchyard {VERSION}. Open it from the application menu or run {target}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
