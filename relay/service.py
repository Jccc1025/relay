"""Relay as a service of the user's: started by `relay setup`, kept running, stopped by `relay stop`.

A harness pointed at Relay cannot reach its model while Relay is down, so Relay runs under the
system's service manager, which starts it at login and again if it exits: a systemd user unit on
Linux, a launchd agent on macOS. Where there is neither (a container, a minimal system), Relay
runs as a process of its own, outside the session that started it, until the machine restarts.
What runs it is recorded in Relay's directory (`service.json`), for `relay stop` to undo.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from . import settings

NAME = "relay"
LABEL = "com.relay.serve"  # launchd
PACKAGE = Path(__file__).resolve().parent.parent  # where `python -m relay.cli` finds Relay


def command() -> list[str]:
    return [sys.executable, "-m", "relay.cli", "serve"]


def log_file() -> Path:
    return settings.home() / "relay.log"


def address() -> str:
    return f"http://{settings.value('host', '127.0.0.1')}:{settings.value('port', '8787')}"


def status(timeout: float = 2.0) -> dict[str, Any] | None:
    """What the running Relay reports (`/relay/v1/status`), or None if none answers."""

    try:
        with urllib.request.urlopen(f"{address()}/relay/v1/status", timeout=timeout) as response:
            return json.loads(response.read())
    except (OSError, ValueError):
        return None


def wait(strategy: str | None = None, seconds: float = 30.0) -> dict[str, Any] | None:
    """The running Relay's status once it answers (and runs `strategy`, if given)."""

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if (now := status()) is not None and (strategy is None or now.get("strategy") == strategy):
            return now
        time.sleep(0.3)
    return None


def manager() -> str:
    """What can keep Relay running here: "systemd", "launchd" or "process"."""

    if sys.platform == "darwin" and shutil.which("launchctl"):
        return "launchd"
    if shutil.which("systemctl") and subprocess.run(["systemctl", "--user", "show-environment"],
                                                     capture_output=True).returncode == 0:
        return "systemd"
    return "process"


def record() -> dict[str, Any]:
    try:
        return json.loads((settings.home() / "service.json").read_text())
    except (OSError, ValueError):
        return {}


def _remember(entry: dict[str, Any] | None) -> None:
    path = settings.home() / "service.json"
    if entry is None:
        path.unlink(missing_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entry, indent=2) + "\n")


def unit() -> str:
    """The systemd user unit."""

    return (f"[Unit]\nDescription=Relay, context management for coding agents\n\n[Service]\n"
            f"ExecStart={' '.join(command())}\nWorkingDirectory={PACKAGE}\nRestart=always\nRestartSec=2\n"
            f"StandardOutput=append:{log_file()}\nStandardError=append:{log_file()}\n\n"
            f"[Install]\nWantedBy=default.target\n")


def agent() -> str:
    """The launchd agent."""

    arguments = "".join(f"<string>{part}</string>" for part in command())
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0"><dict>'
            f"<key>Label</key><string>{LABEL}</string><key>ProgramArguments</key><array>{arguments}</array>"
            f"<key>WorkingDirectory</key><string>{PACKAGE}</string><key>RunAtLoad</key><true/>"
            f"<key>KeepAlive</key><true/><key>StandardOutPath</key><string>{log_file()}</string>"
            f"<key>StandardErrorPath</key><string>{log_file()}</string></dict></plist>\n")


def start() -> str:
    """Run Relay under the service manager (started now, and at every login); which one."""

    kind = manager()
    log_file().parent.mkdir(parents=True, exist_ok=True)
    if kind == "systemd":
        path = Path("~/.config/systemd/user/relay.service").expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(unit())
        _run(["systemctl", "--user", "daemon-reload"])
        _run(["systemctl", "--user", "enable", "--now", f"{NAME}.service"])
        _run(["systemctl", "--user", "restart", f"{NAME}.service"])  # (an older one: the current settings)
        # Without lingering, a user's services stop when their last session ends (a remote machine).
        subprocess.run(["loginctl", "enable-linger", os.environ.get("USER", "")], capture_output=True)
        _remember({"manager": kind, "path": str(path)})
    elif kind == "launchd":
        path = Path(f"~/Library/LaunchAgents/{LABEL}.plist").expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(agent())
        domain = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], capture_output=True)
        _run(["launchctl", "bootstrap", domain, str(path)])
        _remember({"manager": kind, "path": str(path)})
    else:
        stop_process()
        with log_file().open("ab") as log:
            process = subprocess.Popen(command(), cwd=PACKAGE, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       start_new_session=True)  # (outside the caller's session and its signals)
        _remember({"manager": kind, "pid": process.pid})
    return kind


def restart() -> None:
    """Start Relay again with its current settings (where it listens, its upstreams, its store)."""

    kind = record().get("manager")
    if kind == "systemd":
        _run(["systemctl", "--user", "restart", f"{NAME}.service"])
    elif kind == "launchd":
        _run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LABEL}"])
    else:
        start()


def stop() -> str | None:
    """Stop Relay and remove its service; what had kept it running."""

    entry = record()
    kind = entry.get("manager")
    if kind == "systemd":
        subprocess.run(["systemctl", "--user", "disable", "--now", f"{NAME}.service"], capture_output=True)
        Path(entry["path"]).unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    elif kind == "launchd":
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
        Path(entry["path"]).unlink(missing_ok=True)
    else:
        stop_process()
    _remember(None)
    return kind


def stop_process() -> None:
    pid = record().get("pid")
    if not pid:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.1)


def _run(arguments: list[str]) -> None:
    result = subprocess.run(arguments, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(arguments)}: {(result.stderr or result.stdout).strip()[:300]}")
