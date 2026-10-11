"""Relay command line (`relay help` for the guide).

  relay setup <harness> [--strategy S] [--set name=value ...]
                                    save the settings, start Relay as a service, point the harness at it
  relay strategy [S] [--set ...]    show or change the strategy; the running Relay follows at once
  relay settings | set name=value ... | unset name ...
  relay status                      whether Relay runs, its strategy, the harnesses set up
  relay uninstall <harness>         restore the harness's config
  relay stop                        stop Relay and remove its service
  relay serve                       run Relay in the foreground (what the service runs)
  relay install <harness> [--via]   only point a harness at Relay (through the proxy, or a hook)
  relay run <harness> [args...]     run a harness through a private proxy
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import uvicorn

from . import install, integrations, service, settings
from .harnesses import HARNESSES, Harness
from .strategies import STRATEGIES
from .transport import ProxyConfig, create_app

ALIASES = {"claude": "claude_code", "dsh": "deepseek_harness", "mini": "mini_swe"}
GUIDE = Path(__file__).with_name("HELP.md")


# Settings read only as Relay starts: `relay set` restarts the service for them.
AT_START = {"host", "port", "harness", "trace", "event_log", "log_level", "openai_base_url", "chatgpt_base_url",
            "anthropic_base_url", "gemini_base_url", "openai_api_key", "anthropic_api_key", "cache_path",
            "cache_secret", "cache_ttl_seconds", "cache_max_entries", "cache_max_bytes", "cache_max_nodes"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="relay", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command")
    harnesses = sorted({*HARNESSES, *ALIASES} - {"generic"})
    setup = commands.add_parser("setup", help="save the settings, start Relay as a service, point a harness at it")
    setup.add_argument("harness", choices=harnesses)
    strategy = commands.add_parser("strategy", help="show or change the strategy")
    strategy.add_argument("name", nargs="?", choices=sorted(STRATEGIES))
    for command in (setup, strategy):
        command.add_argument("--set", action="append", default=[], metavar="NAME=VALUE", help="a setting (repeatable)")
    setup.add_argument("--strategy", choices=sorted(STRATEGIES))
    setup.add_argument("--via", choices=["auto", "proxy", "hook"], default="auto",
                       help="integration path (default: the harness's preferred one)")
    commands.add_parser("settings", help="every setting written")
    assign = commands.add_parser("set", help="change settings")
    assign.add_argument("assignments", nargs="+", metavar="NAME=VALUE")
    unset = commands.add_parser("unset", help="return settings to their defaults")
    unset.add_argument("names", nargs="+", metavar="NAME")
    commands.add_parser("status", help="whether Relay runs, its strategy, the harnesses set up")
    commands.add_parser("stop", help="stop Relay and remove its service")
    commands.add_parser("help", help="the guide")
    serve = commands.add_parser("serve", help="run Relay in the foreground")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    install_ = commands.add_parser("install", help="only point a harness at Relay")
    uninstall_ = commands.add_parser("uninstall", help="restore a harness")
    for command in (install_, uninstall_):
        command.add_argument("harness", choices=harnesses)
    install_.add_argument("--url", help="Relay's address (default: the host and port settings)")
    install_.add_argument("--via", choices=["auto", "proxy", "hook"], default="auto",
                          help="integration path (default: the harness's preferred one)")
    run = commands.add_parser("run", help="run a harness through a private proxy")
    run.add_argument("harness", choices=harnesses)
    run.add_argument("args", nargs=argparse.REMAINDER, help="arguments for the harness")
    options = parser.parse_args(argv)
    logging.basicConfig(level=settings.value("log_level", "INFO"), format="%(asctime)s %(name)s %(message)s")
    harness = HARNESSES[ALIASES.get(options.harness, options.harness)] if getattr(options, "harness", None) else None

    try:
        if options.command in (None, "help"):
            print(GUIDE.read_text(encoding="utf-8"))
            return 0
        if options.command == "setup":
            return _setup(harness, options.strategy, options.set, options.via)
        if options.command == "strategy":
            return _strategy(options.name, options.set)
        if options.command == "settings":
            print(json.dumps(settings.load(), indent=2, sort_keys=True))
            return 0
        if options.command in ("set", "unset"):
            changes = dict(map(settings.parse, options.assignments)) if options.command == "set" else {}
            return _change(changes, tuple(getattr(options, "names", ())))
        if options.command == "status":
            return _status()
        if options.command == "stop":
            kind = service.stop()
            print(f"stopped Relay ({kind})" if kind else "Relay was not running as a service")
            return 0
    except (ValueError, RuntimeError) as error:
        print(f"relay {options.command}: {error}", file=sys.stderr)
        return 1
    config = ProxyConfig.from_settings()
    if options.command == "install":
        return _install(harness, options.via, options.url or service.address())
    if options.command == "uninstall":
        return _uninstall(harness)
    if options.command == "run":
        return _run(config, ALIASES.get(options.harness, options.harness), options.args)
    host = getattr(options, "host", None) or config.host
    port = getattr(options, "port", None) or config.port
    uvicorn.run(create_app(config=config), host=host, port=port, ws="none")
    return 0


def _setup(harness: Harness, strategy: str | None, assignments: list[str], via: str) -> int:
    """The settings saved, Relay running with them, then the harness pointed at it."""

    changes = dict(map(settings.parse, assignments)) | ({"strategy": strategy} if strategy else {})
    if changes.get("strategy", "compaction") not in STRATEGIES:
        raise ValueError(f"unknown strategy {changes['strategy']!r}: one of {', '.join(sorted(STRATEGIES))}")
    restart = bool(AT_START & set(changes))
    settings.save(changes)
    wanted = settings.value("strategy", "compaction")
    if service.status() is None or (restart and service.record()):
        kind = service.restart() if service.record() else service.start()
        print(f"started Relay as a service ({kind or service.record().get('manager')})")
        if kind == "process":
            print("  note: no service manager here: Relay runs until this machine restarts; run `relay setup` again then")
    if service.wait(wanted) is None:
        print(f"relay setup: Relay did not answer at {service.address()} running {wanted}; the harness is left as it was."
              f"\n{_tail(service.log_file())}", file=sys.stderr)
        return 1
    print(f"Relay runs {wanted} at {service.address()}")
    if harness.name not in install.installed() and _install(harness, via, service.address()):
        return 1
    print(f"{harness.name} goes through Relay: conversations started from now on use {wanted}; one already "
          f"open goes on as it was until a new one is started (`relay help`, \"Which conversations change\")")
    return 0


def _strategy(name: str | None, assignments: list[str]) -> int:
    if name is None and not assignments:
        now = service.status()
        print(f"strategy: {settings.value('strategy', 'compaction')}"
              + (f" (Relay runs {now['strategy']})" if now else " (Relay is not running)"))
        print("strategies: " + ", ".join(sorted(STRATEGIES)))
        return 0
    return _change(dict(map(settings.parse, assignments)) | ({"strategy": name} if name else {}), ())


def _change(changes: dict, removed: tuple[str, ...]) -> int:
    """Settings changed, and the running Relay following them."""

    if (name := changes.get("strategy")) is not None and name not in STRATEGIES:
        raise ValueError(f"unknown strategy {name!r}: one of {', '.join(sorted(STRATEGIES))}")
    settings.save(changes, removed)
    wanted = settings.value("strategy", "compaction")
    if service.status() is None:
        print(f"saved; Relay is not running (`relay setup <harness>` starts it)")
        return 0
    if AT_START & {*changes, *removed} and service.record():
        service.restart()
    if service.wait(wanted, seconds=15) is None:
        print(f"saved, but the running Relay does not run {wanted}: see `relay status` and {service.log_file()}",
              file=sys.stderr)
        return 1
    print(f"Relay runs {wanted}, for every conversation from its next request")
    return 0


def _status() -> int:
    now, entry = service.status(), service.record()
    print(f"Relay: {'running at ' + service.address() + ', strategy ' + now['strategy'] if now else 'not running'}")
    print(f"service: {entry.get('manager', 'none')}")
    print(f"harnesses set up: {', '.join(install.installed()) or 'none'}")
    print(f"settings: {settings.path()}")
    return 0 if now else 1


def _tail(path: Path, lines: int = 20) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def _install(harness: Harness, via: str, url: str) -> int:
    try:
        path = integrations.choose(harness, via)
        setup = path.installation(url)
        install.install(harness.name, url, setup.settings)
    except ValueError as error:
        print(f"relay install: {error}", file=sys.stderr)
        return 1
    print(f"installed {harness.name} ({path.name}): " + ", ".join(sorted({str(s.path) for s in setup.settings})))
    for note in setup.notes:
        print(f"  note: {note}")
    return 0


def _uninstall(harness: Harness) -> int:
    try:
        paths = install.uninstall(harness.name)
    except ValueError as error:
        print(f"relay uninstall: {error}", file=sys.stderr)
        return 1
    print(f"uninstalled {harness.name}: " + ", ".join(sorted({str(path) for path in paths})))
    return 0


def _run(config: ProxyConfig, harness: str, args: list[str]) -> int:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(create_app(config=config), log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    while not server.started and thread.is_alive():
        time.sleep(0.01)
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    command, env = HARNESSES[harness].launch(url, [a for a in args if a != "--"])
    try:
        return subprocess.run(command, env={**os.environ, **env}, check=False).returncode
    finally:
        server.should_exit = True
        thread.join(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
