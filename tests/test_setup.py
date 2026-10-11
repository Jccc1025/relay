"""Relay's settings and its command line: settings a running Relay follows, and `relay setup`,
which points a harness at Relay only once Relay answers."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from starlette.testclient import TestClient

from relay import cli, install, service, settings
from relay.transport import create_app
from tests.fakes import FakeUpstream, local_upstreams, relay_settings, serve


class SettingsTests(unittest.TestCase):
    def test_values_are_json_where_they_parse_and_text_otherwise(self) -> None:
        self.assertEqual(settings.parse("clm_budget=26000"), ("clm_budget", 26000))
        self.assertEqual(settings.parse("clm_nudges=false"), ("clm_nudges", False))
        self.assertEqual(settings.parse("clm_dir=/tmp/ctx"), ("clm_dir", "/tmp/ctx"))
        with self.assertRaises(ValueError):
            settings.parse("clm_budget")

    def test_saved_settings_are_read_as_text_and_unset_with_none(self) -> None:
        with relay_settings(clm_budget=26000, clm_nudges=False):
            self.assertEqual((settings.value("clm_budget"), settings.value("clm_nudges")), ("26000", "false"))
            settings.save({"clm_budget": None})
            self.assertIsNone(settings.value("clm_budget"))
            self.assertEqual(settings.value("strategy", "compaction"), "compaction")


class FollowingTests(unittest.TestCase):
    def test_a_running_relay_follows_the_strategy_from_the_next_request(self) -> None:
        fake = FakeUpstream(tool_calls=0)
        with relay_settings(strategy="compaction", cache_path="off"), serve(fake.app) as upstream:
            from relay.transport import ProxyConfig

            body = {"model": "m", "input": [{"type": "message", "role": "user", "content": "task"}],
                    "tools": [{"type": "function", "name": "sh", "parameters": {}}], "stream": False}
            with TestClient(create_app(config=ProxyConfig(upstreams=local_upstreams(upstream)))) as client:
                self.assertEqual(client.get("/relay/v1/status").json()["strategy"], "compaction")
                client.post("/v1/responses", json=body)
                settings.save({"strategy": "clm"})
                client.post("/v1/responses", json=body)  # the same conversation, its next request
                self.assertEqual(client.get("/relay/v1/status").json()["strategy"], "clm")
        first, second = (json.dumps(b) for b in fake.bodies("/v1/responses"))
        self.assertNotIn("## Managing your context", first)
        self.assertIn("## Managing your context", second)

    def test_settings_it_cannot_apply_leave_the_strategy_running(self) -> None:
        with relay_settings(strategy="compaction", cache_path="off"):
            with TestClient(create_app()) as client:
                settings.save({"strategy": "no such strategy"})
                with self.assertLogs("relay", "ERROR"):
                    self.assertEqual(client.get("/relay/v1/status").json()["strategy"], "compaction")


class SetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.codex = self.root / "codex"
        self.codex.mkdir()
        (self.codex / "config.toml").write_text('model = "m"\n')
        patcher = mock.patch.dict(os.environ, {"RELAY_HOME": str(self.root / "relay"), "CODEX_HOME": str(self.codex)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def test_the_harness_is_left_alone_when_relay_does_not_start(self) -> None:
        with mock.patch.object(service, "status", return_value=None), \
                mock.patch.object(service, "start", return_value="process"), \
                mock.patch.object(service, "wait", return_value=None):
            code, out = self.run_cli("setup", "codex", "--strategy", "clm")
        self.assertEqual(code, 1)
        self.assertIn("left as it was", out)
        self.assertEqual((self.codex / "config.toml").read_text(), 'model = "m"\n')
        self.assertEqual(install.installed(), [])
        self.assertEqual(settings.value("strategy"), "clm")  # (kept for the next try)

    def test_setup_starts_relay_then_points_the_harness_at_it(self) -> None:
        calls = []
        with mock.patch.object(service, "status", return_value=None), \
                mock.patch.object(service, "start", side_effect=lambda: calls.append("start") or "systemd"), \
                mock.patch.object(service, "wait", side_effect=lambda *a, **k: calls.append(("wait", a)) or {"strategy": "clm"}):
            code, out = self.run_cli("setup", "codex", "--strategy", "clm", "--set", "clm_budget=40000")
        self.assertEqual(code, 0, out)
        self.assertEqual(calls, ["start", ("wait", ("clm",))])
        self.assertEqual(install.installed(), ["codex"])
        self.assertIn("127.0.0.1:8787/up/codex-0", (self.codex / "config.toml").read_text())
        self.assertEqual((settings.value("strategy"), settings.value("clm_budget")), ("clm", "40000"))
        self.assertIn("one already open goes on as it was", out)

    def test_changing_the_strategy_reaches_the_running_relay_without_a_restart(self) -> None:
        with mock.patch.object(service, "status", return_value={"strategy": "compaction"}), \
                mock.patch.object(service, "restart") as restart, \
                mock.patch.object(service, "wait", return_value={"strategy": "folding"}) as wait:
            code, out = self.run_cli("strategy", "folding")
        self.assertEqual(code, 0, out)
        restart.assert_not_called()
        wait.assert_called_once_with("folding", seconds=15)
        self.assertIn("every conversation", out)

    def test_settings_read_at_start_restart_the_service(self) -> None:
        with mock.patch.object(service, "status", return_value={"strategy": "compaction"}), \
                mock.patch.object(service, "record", return_value={"manager": "systemd"}), \
                mock.patch.object(service, "restart") as restart, \
                mock.patch.object(service, "wait", return_value={"strategy": "compaction"}):
            self.assertEqual(self.run_cli("set", "cache_ttl_seconds=60")[0], 0)
        restart.assert_called_once()

    def test_an_unknown_strategy_is_refused_before_anything_changes(self) -> None:
        code, out = self.run_cli("set", "strategy=nope")
        self.assertEqual(code, 1)
        self.assertIn("unknown strategy", out)
        self.assertIsNone(settings.value("strategy"))

    def test_help_is_the_guide(self) -> None:
        code, out = self.run_cli("help")
        self.assertEqual(code, 0)
        self.assertIn("relay setup codex --strategy clm", out)
        self.assertIn("For a coding agent asked to set Relay up", out)


class ServiceTests(unittest.TestCase):
    def test_the_service_runs_relay_and_restarts_it(self) -> None:
        with relay_settings():
            unit, agent = service.unit(), service.agent()
        self.assertIn("-m relay.cli serve", unit)
        self.assertIn("Restart=always", unit)
        self.assertIn("<key>KeepAlive</key><true/>", agent)


if __name__ == "__main__":
    unittest.main()
