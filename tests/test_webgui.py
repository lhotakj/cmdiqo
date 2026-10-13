"""tests.test_webgui: Unit tests for WebGUI state, uptime parser, and Flask endpoints."""

import json
import logging
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

        self.assertEqual(normalize_arch("x86_64", "Linux/6.8"), "linux64")
        self.assertEqual(normalize_arch("amd64", "Linux/Generic"), "linux64")
        self.assertEqual(normalize_arch("", "Linux/5.15"), "linux64")
        self.assertEqual(normalize_arch("linux64"), "linux64")
        self.assertEqual(normalize_arch("linux"), "linux64")
        self.assertEqual(normalize_arch("win64"), "win64")
        self.assertEqual(normalize_arch("arm64"), "arm64")
        self.assertEqual(normalize_arch("armv6"), "armv6")

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
        _boot_epoch, formatted = parse_and_standardize_uptime(sample_win)
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

    def test_sortable_table_headers_and_initial_data(self) -> None:
        state = WebGuiState()
        state.register_agent(
            name="host-charlie",
            hello={"platform": "Linux/6.6", "arch": "x86_64", "version": "1.0.0"},
            addr="192.168.1.10:8765",
            boot_time=3000.0,
            uptime_str="03d 00h 00m 00s",
        )
        state.register_agent(
            name="host-alpha",
            hello={"platform": "Windows/11.0", "arch": "AMD64", "version": "1.0.0"},
            addr="192.168.1.11:8765",
            boot_time=1000.0,
            uptime_str="01d 00h 00m 00s",
        )
        state.register_agent(
            name="host-bravo",
            hello={"platform": "Linux/6.6", "arch": "aarch64", "version": "1.0.0"},
            addr="192.168.1.12:8765",
            boot_time=2000.0,
            uptime_str="02d 00h 00m 00s",
        )

        app = create_webgui_app(state, bot_version="2.0.0")
        client = app.test_client()
        resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")

        # 1. Verify sortable headers and columns
        self.assertIn('th-sortable text-center" data-sort-key="arch"', html)
        self.assertIn('th-sortable" data-sort-key="name"', html)
        self.assertIn('th-sortable" data-sort-key="platform"', html)
        self.assertIn('th-sortable text-right" data-sort-key="uptime"', html)

        # 2. Verify ▲ and ▼ arrow icons
        self.assertIn('class="arrow-up" data-dir="asc"', html)
        self.assertIn('class="arrow-down" data-dir="desc"', html)
        self.assertIn("▲", html)
        self.assertIn("▼", html)

        # 3. Verify localStorage key and default sort constants in JS
        self.assertIn("cmdiqo_agents_sort", html)
        self.assertIn("DEFAULT_SORT = { col: 'name', dir: 'asc' }", html)

        # 4. Verify initial JSON payload script tag is present with all agents
        self.assertIn('<script id="initial-agents-data" type="application/json">', html)
        self.assertIn('"name": "host-alpha"', html)
        self.assertIn('"name": "host-bravo"', html)
        self.assertIn('"name": "host-charlie"', html)

        # 5. Verify server-side default sort order (alpha, bravo, charlie)
        pos_alpha = html.find("host-alpha")
        pos_bravo = html.find("host-bravo")
        pos_charlie = html.find("host-charlie")
        self.assertTrue(0 <= pos_alpha < pos_bravo < pos_charlie)
        # 6. Verify auto-scroll localStorage key and persistence logic
        self.assertIn("cmdiqo_autoscroll", html)
        self.assertIn("loadAutoScrollPreference", html)
        self.assertIn("saveAutoScrollPreference", html)

    def test_log_reverse_order_default_and_toggle(self) -> None:
        state = WebGuiState()
        state.add_log("info", "hub", "Event Alpha 1")
        state.add_log("warn", "hub", "Event Bravo 2")
        state.add_log("error", "hub", "Event Charlie 3")

        app = create_webgui_app(state, bot_version="2.0.0")
        client = app.test_client()
        resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")

        # 1. Verify latest entries appear first by default in server-rendered log rows
        pos_charlie = html.find("Event Charlie 3")
        pos_bravo = html.find("Event Bravo 2")
        pos_alpha = html.find("Event Alpha 1")
        self.assertTrue(0 <= pos_charlie < pos_bravo < pos_alpha)

        # 2. Verify reverse-order checkbox control is present and checked by default
        self.assertIn('id="reverse-order-toggle"', html)
        self.assertIn("<span>Reverse order</span>", html)
        self.assertIn('aria-label="Toggle reverse order to show latest log entries first"', html)

        # 3. Verify initial logs json script tag contains all logs in chronological order
        self.assertIn('<script id="initial-logs-data" type="application/json">', html)
        self.assertIn('"message": "Event Alpha 1"', html)
        self.assertIn('"message": "Event Charlie 3"', html)

        # 4. Verify localStorage persistence keys and JavaScript functions
        self.assertIn("cmdiqo_log_reverse", html)
        self.assertIn("loadReverseOrderPreference", html)
        self.assertIn("saveReverseOrderPreference", html)
        self.assertIn("scrollToActivePosition", html)

    def test_websockets_connection_open_and_debug_levels(self) -> None:
        state = WebGuiState()
        handler = HubWebLogHandler(state)

        # 1. Test websockets.server connection open record normalization to DEBUG
        rec = logging.LogRecord(
            name="websockets.server",
            level=logging.INFO,
            pathname="server.py",
            lineno=42,
            msg="connection open",
            args=(),
            exc_info=None,
        )
        handler.emit(rec)
        logs = state.get_logs()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["level"], "DEBUG")
        self.assertEqual(logs[0]["source"], "websockets.server")
        self.assertEqual(logs[0]["message"], "connection open")

        # 2. Add an explicit DEBUG log entry
        state.add_log("DEBUG", "hub", "Verbose internal trace")

        # 3. Test index.html renders .level-debug CSS and contains level-debug class
        app = create_webgui_app(state, bot_version="2.0.0")
        client = app.test_client()
        resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")
        self.assertIn(".level-debug", html)
        self.assertIn("level-debug", html)
        self.assertIn("var(--text-muted)", html)

    def test_action_column_and_menu_modal_markup(self) -> None:
        state = WebGuiState()
        state.register_agent(
            name="host-alpha",
            hello={"platform": "Windows/11.0", "arch": "AMD64", "version": "1.0.0"},
            addr="192.168.1.11:8765",
            boot_time=1000.0,
            uptime_str="01d 00h 00m 00s",
        )

        app = create_webgui_app(state, bot_version="2.0.0")
        client = app.test_client()
        resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")

        # 1. Verify Action column header in table
        self.assertIn('style="width: 80px;"', html)
        self.assertIn("<span>Action</span>", html)

        # 2. Verify Action button in table row
        self.assertIn('class="btn-action"', html)
        self.assertIn('data-host="host-alpha"', html)
        self.assertIn('data-platform="Windows/11.0"', html)
        self.assertIn('title="Actions for host-alpha"', html)

        # 3. Verify Action Context Menu and Terminal Modal containers
        self.assertIn('id="action-context-menu"', html)
        self.assertIn('class="action-menu"', html)
        self.assertIn('id="terminal-modal"', html)
        self.assertIn('class="terminal-modal-overlay"', html)
        self.assertIn('class="terminal-modal-window"', html)
        self.assertIn('id="terminal-output"', html)
        self.assertIn('id="terminal-copy-btn"', html)
        self.assertIn('id="terminal-close-btn"', html)
        self.assertIn('id="terminal-status-badge"', html)

        # 4. Verify JavaScript command categories tree
        self.assertIn("COMMAND_CATEGORIES", html)
        self.assertIn("System Health", html)
        self.assertIn("Power & Session", html)
        self.assertIn("Network & Diagnostics", html)
        self.assertIn("openTerminalAndExecute", html)
        self.assertIn("openActionMenu", html)
        self.assertIn("menu-sub-pills", html)
        self.assertIn("sub-pill-btn", html)
        self.assertIn("has-interactive", html)
        self.assertIn("Logon Hours", html)
        self.assertIn("restrict set", html)

        # 5. Verify Terminal rich formatting & emoji styling
        self.assertIn("formatSlackToHtml", html)
        self.assertIn("SLACK_EMOJI_MAP", html)
        self.assertIn(":information_source:", html)
        self.assertIn(".term-bold", html)
        self.assertIn(".term-inline-code", html)
        self.assertIn("color: #e2e8f0;", html)
        self.assertIn(".term-code-block", html)
        self.assertIn(".term-bullet", html)
        self.assertIn(".term-emoji", html)
        self.assertIn(".term-italic", html)
        self.assertIn(".term-quote", html)
        self.assertIn(".term-link", html)
        self.assertIn(".menu-input-btn:disabled", html)

    def test_terminal_slack_formatting_and_emojis(self) -> None:
        """Verify formatSlackToHtml correctly converts emojis, bold, inline code, italics, and bullets."""
        import json
        import shutil
        import subprocess

        state = WebGuiState()
        app = create_webgui_app(state, bot_version="2.0.0")
        client = app.test_client()
        resp = client.get("/")
        html = resp.get_data(as_text=True)

        self.assertIn("function formatSlackToHtml(rawText)", html)
        self.assertIn("SLACK_EMOJI_MAP", html)

        if shutil.which("node"):
            js_script = f"""
            {html[html.find('const SLACK_EMOJI_MAP ='):html.find('function openTerminalAndExecute')]}
            const sample = ":information_source: *Parental Controls on `pi`: NOT CONFIGURED*\\n• **Target:** Linux (`pi` /etc/hosts)\\n• Note: _this is in italics_\\n• No parental block found in hosts. Run `/host pi parental on` to enable.";
            const res = formatSlackToHtml(sample);
            console.log(JSON.stringify(res));
            """
            proc = subprocess.run(
                ["node", "-e", js_script],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, f"Node script error: {proc.stderr}")
            formatted = json.loads(proc.stdout.strip())

            # Verify emoji converted to Unicode
            self.assertIn("ℹ️", formatted)
            self.assertIn('class="term-emoji"', formatted)
            self.assertNotIn(":information_source:", formatted)

            # Verify bold formatting
            self.assertIn('class="term-bold"', formatted)
            self.assertIn('<strong class="term-bold">Target:</strong>', formatted)

            # Verify italics formatting
            self.assertIn('class="term-italic"', formatted)
            self.assertIn('<i class="term-italic">this is in italics</i>', formatted)

            # Verify inline code
            self.assertIn('<code class="term-inline-code">pi</code>', formatted)
            self.assertIn('<code class="term-inline-code">/host pi parental on</code>', formatted)

            # Verify bullets
            self.assertIn('<span class="term-bullet">•</span>', formatted)

            # Verify explicit _something_ and _foo_bar_ formatting
            js_something = f"""
            {html[html.find('const SLACK_EMOJI_MAP ='):html.find('function openTerminalAndExecute')]}
            console.log(JSON.stringify(formatSlackToHtml("Notice: _something_ on `pi` with _foo_bar_")));
            """
            proc2 = subprocess.run(
                ["node", "-e", js_something],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc2.returncode, 0, f"Node script error: {proc2.stderr}")
            res2 = json.loads(proc2.stdout.strip())
            self.assertIn('<i class="term-italic">something</i>', res2)
            self.assertIn('<i class="term-italic">foo_bar</i>', res2)

    def test_api_exec_validation_and_custom_executor(self) -> None:
        state = WebGuiState()
        app = create_webgui_app(state, bot_version="2.0.0")
        client = app.test_client()

        # 1. Missing command parameter yields 400
        bad_resp = client.post("/api/exec", json={})
        self.assertEqual(bad_resp.status_code, 400)
        data = bad_resp.get_json()
        self.assertFalse(data["success"])
        self.assertIn("Missing", data["error"])

        # 2. Custom executor hook execution
        def mock_executor(cmd: str) -> dict:
            return {
                "success": True,
                "command": cmd,
                "output": f"Mock output for {cmd}",
                "target": "alpha",
            }

        state.custom_executor = mock_executor
        ok_resp = client.post("/api/exec", json={"command": "/host alpha uptime"})
        self.assertEqual(ok_resp.status_code, 200)
        ok_data = ok_resp.get_json()
        self.assertTrue(ok_data["success"])
        self.assertEqual(ok_data["output"], "Mock output for /host alpha uptime")

        # 3. Shorthand host + cmd payload
        short_resp = client.post("/api/exec", json={"host": "alpha", "cmd": "memory"})
        self.assertEqual(short_resp.status_code, 200)
        short_data = short_resp.get_json()
        self.assertTrue(short_data["success"])
        self.assertIn("memory", short_data["output"])

    def test_api_exec_full_execution_flow(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock

        from bot.app import trigger_to_regex

        async def _run_test() -> None:
            state = WebGuiState()
            state.loop = asyncio.get_running_loop()
            state.register_agent("node1", {"platform": "Linux/6.6", "arch": "x86_64"})

            mock_hub = AsyncMock()
            mock_hub.agents = {"node1": {"platform": "Linux/6.6"}}
            mock_hub.exec.return_value = " 12:00:00 up 2 days,  1:00,  1 user,  load average: 0.10, 0.05, 0.01"

            spec = {
                "name": "host-uptime",
                "trigger": "/host {host} uptime",
                "command": {"shell": "uptime"},
            }
            state.hub = mock_hub
            state.commands = [{"spec": spec, "regex": trigger_to_regex(spec["trigger"])}]

            # Execute command synchronously via WebGUI state
            res = await asyncio.to_thread(state.execute_command_sync, "/host node1 uptime")
            self.assertTrue(res["success"])
            self.assertIn("02d 01h 00m 00s", res["output"])

            # Verify uptime cache updated in state
            agents = state.get_agents()
            self.assertEqual(len(agents), 1)
            self.assertEqual(agents[0]["uptime_str"], "02d 01h 00m 00s")

            # Verify log entry was registered
            logs = state.get_logs()
            self.assertTrue(any("Executed: /host node1 uptime" in l["message"] for l in logs))

        asyncio.run(_run_test())

    def test_api_exec_restrict_set_and_status(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock

        from bot.app import load_commands

        async def _run_test() -> None:
            state = WebGuiState()
            state.loop = asyncio.get_running_loop()
            state.register_agent("node1", {"platform": "Windows 11", "arch": "AMD64"})

            mock_hub = AsyncMock()
            mock_hub.agents = {"node1": {"platform": "Windows 11"}}

            async def mock_exec(host, kind, cmd, timeout=30):
                if "$action = 'status'" in cmd:
                    return "STATUS:jonas:Monday 08:00 - 20:00"
                if "$action = 'set'" in cmd:
                    return "SUCCESS:set:jonas:M-F,08:00-20:00"
                if "$action = 'off'" in cmd:
                    return "SUCCESS:off:jonas"
                return ""

            mock_hub.exec = mock_exec
            state.hub = mock_hub
            state.commands = load_commands("config/commands.yaml")

            # 1. Test restrict set via WebGUI state
            res_set = await asyncio.to_thread(
                state.execute_command_sync, "/host node1 restrict set M-F,08:00-20:00"
            )
            self.assertTrue(res_set["success"])
            self.assertIn("Logon hours restriction successfully applied", res_set["output"])

            # 2. Test restrict status via WebGUI state
            res_status = await asyncio.to_thread(
                state.execute_command_sync, "/host node1 restrict status"
            )
            self.assertTrue(res_status["success"])
            self.assertIn("Logon Hours Status on `node1`", res_status["output"])

            # 3. Test restrict off via WebGUI state
            res_off = await asyncio.to_thread(
                state.execute_command_sync, "/host node1 restrict off"
            )
            self.assertTrue(res_off["success"])
            self.assertIn("Logon hours restriction removed", res_off["output"])

        asyncio.run(_run_test())

    def test_api_exec_blockapp_status_and_actions(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock

        from bot.app import load_commands

        async def _run_test() -> None:
            state = WebGuiState()
            state.loop = asyncio.get_running_loop()
            state.register_agent("AMD", {"platform": "Windows 11", "arch": "AMD64"})

            mock_hub = AsyncMock()
            mock_hub.agents = {"AMD": {"platform": "Windows 11"}}

            async def mock_exec(host, kind, cmd, timeout=60):
                if "$action = 'status'" in cmd or "$Action = 'status'" in cmd or "$ACTION = 'status'" in cmd:
                    return "STATUS:roblox:BLOCKED\nSTATUS:steam:ALLOWED"
                if "$action = 'on'" in cmd or "$Action = 'on'" in cmd or "$ACTION = 'on'" in cmd:
                    return "SUCCESS:on:roblox\nSUCCESS:on:steam"
                if "$action = 'off'" in cmd or "$Action = 'off'" in cmd or "$ACTION = 'off'" in cmd:
                    return "SUCCESS:off:roblox\nSUCCESS:off:steam"
                return "STATUS:roblox:ALLOWED"

            mock_hub.exec = mock_exec
            state.hub = mock_hub
            state.commands = load_commands("config/commands.yaml")

            # 1. Test /host AMD blockapp status
            res_status = await asyncio.to_thread(
                state.execute_command_sync, "/host AMD blockapp status"
            )
            self.assertTrue(res_status["success"])
            self.assertIn("Application Execution Status on `AMD`", res_status["output"])
            self.assertIn("Roblox", res_status["output"])

            # 2. Test /host AMD blockapp on
            res_on = await asyncio.to_thread(
                state.execute_command_sync, "/host AMD blockapp on"
            )
            self.assertTrue(res_on["success"])
            self.assertIn("Application execution BLOCKED on `AMD`", res_on["output"])

            # 3. Test /host AMD blockapp off
            res_off = await asyncio.to_thread(
                state.execute_command_sync, "/host AMD blockapp off"
            )
            self.assertTrue(res_off["success"])
            self.assertIn("Application execution UNBLOCKED on `AMD`", res_off["output"])

        asyncio.run(_run_test())

    def test_api_exec_failure_logging_and_status(self) -> None:
        """Verify that when command execution reports a failure, it returns success: False and logs as ERROR."""
        import asyncio
        from unittest.mock import AsyncMock

        from bot.app import load_commands

        async def _run_test() -> None:
            state = WebGuiState()
            state.loop = asyncio.get_running_loop()
            state.register_agent("JONAS-PC", {"platform": "Windows 11", "arch": "AMD64"})

            mock_hub = AsyncMock()
            mock_hub.agents = {"JONAS-PC": {"platform": "Windows 11"}}

            async def mock_exec(host, kind, cmd, timeout=30):
                # Simulate remote failure output
                return "ERROR:Failed setting logon hours for user Jonas: Time supplied is not exactly on the hour."

            mock_hub.exec = mock_exec
            state.hub = mock_hub
            state.commands = load_commands("config/commands.yaml")

            res = await asyncio.to_thread(
                state.execute_command_sync, "/host JONAS-PC restrict set M-F,08:00-20:00"
            )
            # Must return success False on failure
            self.assertFalse(res["success"])
            self.assertIn("Failed on `JONAS-PC`", res["output"])

            # Must log as ERROR in WebGUI logs buffer
            logs = state.get_logs()
            error_logs = [l for l in logs if l["level"] == "ERROR"]
            self.assertTrue(len(error_logs) > 0)
            self.assertTrue(any("Command failed" in l["message"] for l in error_logs))

        asyncio.run(_run_test())


if __name__ == "__main__":
    unittest.main()



