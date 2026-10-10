"""tests.test_webgui: Unit tests for WebGUI state, uptime parser, and Flask endpoints."""

import collections
import json
import time
import unittest

from bot.webgui import (
    HubWebLogHandler,
    WebGuiState,
    create_webgui_app,
    format_uptime_zero_padded,
    normalize_arch,
    parse_and_standardize_uptime,
)


class TestWebGui(unittest.TestCase):
    def test_normalize_arch(self) -> None:
        self.assertEqual(normalize_arch("arm64"), "arm64")
        self.assertEqual(normalize_arch("aarch64"), "arm64")
        self.assertEqual(normalize_arch("armv8"), "arm64")

        self.assertEqual(normalize_arch("armv6l"), "armv6")
        self.assertEqual(normalize_arch("armv7l"), "armv6")
        self.assertEqual(normalize_arch("armhf"), "armv6")

        self.assertEqual(normalize_arch("AMD64", "Windows/10.0"), "win64")
        self.assertEqual(normalize_arch("x86_64", "Windows/11.0"), "win64")
        self.assertEqual(normalize_arch("", "Windows/Server"), "win64")

        self.assertEqual(normalize_arch("x86_64", "Linux/6.8"), "linux")
        self.assertEqual(normalize_arch("amd64", "Linux/Generic"), "linux")
        self.assertEqual(normalize_arch("", "Linux/5.15"), "linux")

    def test_format_uptime_zero_padded(self) -> None:
        self.assertEqual(format_uptime_zero_padded(0), "00d 00h 00m 00s")
        self.assertEqual(format_uptime_zero_padded(65), "00d 00h 01m 05s")
        self.assertEqual(format_uptime_zero_padded(3665), "00d 01h 01m 05s")
        self.assertEqual(format_uptime_zero_padded(90061), "01d 01h 01m 01s")

    def test_parse_linux_uptime(self) -> None:
        sample_days = " 14:23:01 up 14 days,  3:22,  2 users,  load average: 0.15, 0.08, 0.06"
        boot_epoch, formatted = parse_and_standardize_uptime(sample_days)
        self.assertIn("14d 03h 22m 00s", formatted)
        self.assertLess(boot_epoch, time.time() - 14 * 86400)

        sample_min = " 10:00:00 up 45 min,  1 user,  load average: 0.01"
        _, formatted_min = parse_and_standardize_uptime(sample_min)
        self.assertEqual(formatted_min, "00d 00h 45m 00s")

        sample_hm = " 10:00:00 up  2:15,  1 user,  load average: 0.01"
        _, formatted_hm = parse_and_standardize_uptime(sample_hm)
        self.assertEqual(formatted_hm, "00d 02h 15m 00s")

    def test_parse_windows_uptime(self) -> None:
        sample_win = "Uptime: 4d 2h 15m 9s (Booted: 2026-10-06 10:02:37)"
        boot_epoch, formatted = parse_and_standardize_uptime(sample_win)
        self.assertIn("04d 02h 15m 09s", formatted)

    def test_state_lifecycle(self) -> None:
        state = WebGuiState()
        self.assertEqual(len(state.get_agents()), 0)

        # Register agent
        state.register_agent(
            name="rpi5",
            hello={"platform": "Linux/6.6", "arch": "aarch64", "version": "1.0.0"},
            addr="192.168.1.50:8765",
            boot_time=1000.0,
            uptime_str="01d 02h 03m 04s",
        )
        agents = state.get_agents()
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0]["name"], "rpi5")
        self.assertEqual(agents[0]["arch"], "arm64")
        self.assertEqual(agents[0]["arch_icon"], "/static/img/arch/arm64.webp")
        self.assertEqual(agents[0]["uptime_str"], "01d 02h 03m 04s")

        # Update uptime
        state.update_agent_uptime("rpi5", 1000.0, "01d 02h 03m 05s")
        self.assertEqual(state.get_agents()[0]["uptime_str"], "01d 02h 03m 05s")

        # Unregister agent (purges cache)
        state.unregister_agent("rpi5")
        self.assertEqual(len(state.get_agents()), 0)

    def test_logs_ring_buffer_capped_to_50(self) -> None:
        state = WebGuiState()
        for i in range(60):
            state.add_log("info", "test", f"Message {i}")

        logs = state.get_logs()
        self.assertEqual(len(logs), 50)
        self.assertEqual(logs[0]["message"], "Message 10")
        self.assertEqual(logs[-1]["message"], "Message 59")

    def test_flask_endpoints(self) -> None:
        state = WebGuiState()
        state.register_agent(
            name="winhost",
            hello={"platform": "Windows/11.0", "arch": "AMD64", "version": "1.0.0"},
            addr="192.168.1.100:8765",
            boot_time=100.0,
            uptime_str="00d 05h 10m 00s",
        )
        state.add_log("info", "hub", "Agent connected: winhost")

        app = create_webgui_app(state, bot_version="2.0.0")
        client = app.test_client()

        # HTML page
        res = client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Cmdiqo Hub", res.data)
        self.assertIn(b"winhost", res.data)
        self.assertIn(b"00d 05h 10m 00s", res.data)

        # JSON endpoints
        res_agents = client.get("/api/agents")
        self.assertEqual(res_agents.status_code, 200)
        data_agents = json.loads(res_agents.data)
        self.assertEqual(len(data_agents["agents"]), 1)

        res_logs = client.get("/api/logs")
        self.assertEqual(res_logs.status_code, 200)
        data_logs = json.loads(res_logs.data)
        self.assertEqual(len(data_logs["logs"]), 1)

    def test_bot_config_web_port(self) -> None:
        import os
        from bot.app import BotConfig

        old_port = os.environ.get("WEB_PORT")
        old_gui = os.environ.get("WEBGUI")
        try:
            os.environ.pop("WEBGUI", None)
            os.environ["WEB_PORT"] = "8080"
            cfg = BotConfig.from_env()
            self.assertEqual(cfg.web_port, 8080)
            self.assertEqual(cfg.webgui_port, 8080)

            os.environ["WEB_PORT"] = "non-numeric"
            cfg_invalid = BotConfig.from_env()
            self.assertIsNone(cfg_invalid.web_port)

            os.environ.pop("WEB_PORT", None)
            os.environ["WEBGUI"] = "8080"
            cfg_fallback = BotConfig.from_env()
            self.assertEqual(cfg_fallback.web_port, 8080)

            os.environ.pop("WEBGUI", None)
            cfg_none = BotConfig.from_env()
            self.assertIsNone(cfg_none.web_port)
        finally:
            if old_port is not None:
                os.environ["WEB_PORT"] = old_port
            else:
                os.environ.pop("WEB_PORT", None)
            if old_gui is not None:
                os.environ["WEBGUI"] = old_gui
            else:
                os.environ.pop("WEBGUI", None)

    def test_handle_command_standardizes_uptime(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock
        from bot.app import handle_command, trigger_to_regex

        async def _test() -> None:
            state = WebGuiState()
            state.register_agent("srv1", {"platform": "Linux/6.6", "arch": "x86_64"})

            mock_hub = AsyncMock()
            mock_hub.agents = {"srv1": {"platform": "Linux/6.6"}}
            # Return real Linux uptime output
            mock_hub.exec.return_value = " 14:23:01 up 14 days,  3:22,  2 users,  load average: 0.15, 0.08, 0.06"

            spec = {
                "name": "host-uptime",
                "trigger": "/host {host} uptime",
                "command": {"shell": "uptime"},
            }
            commands = [{"spec": spec, "regex": trigger_to_regex(spec["trigger"])}]

            post_mock = AsyncMock()
            await handle_command(
                text="/host srv1 uptime",
                user_id="U1",
                channel_id="C1",
                post=post_mock,
                commands=commands,
                hub=mock_hub,
                webgui_state=state,
            )

            self.assertEqual(post_mock.await_count, 2)
            reply = post_mock.await_args_list[1][0][0]
            # Must be standardized with zero pads
            self.assertIn("14d 03h 22m 00s", reply)

            # WebGuiState cache should also be updated with standardized uptime
            agents = state.get_agents()
            self.assertEqual(len(agents), 1)
            self.assertEqual(agents[0]["uptime_str"], "14d 03h 22m 00s")

        asyncio.run(_test())

    def test_security_headers_and_favicon(self) -> None:
        state = WebGuiState()
        app = create_webgui_app(state, bot_version="2.0.0")
        client = app.test_client()

        resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(resp.headers.get("X-Frame-Options"), "SAMEORIGIN")
        self.assertEqual(resp.headers.get("Referrer-Policy"), "strict-origin-when-cross-origin")

        fav_resp = client.get("/favicon.ico")
        self.assertIn(fav_resp.status_code, (200, 204))


if __name__ == "__main__":
    unittest.main()
