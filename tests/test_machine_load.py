import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from aiohttp import ClientSession, web

from machine import Machine, build_load_report, read_load_counters


class MachineLoadTest(unittest.IsolatedAsyncioTestCase):
    async def test_machine_reports_without_workers_and_retries_http_failure(self):
        reports = []
        received = asyncio.Event()

        async def status(request):
            reports.append(await request.json())
            if len(reports) == 1:
                return web.Response(status=503)
            received.set()
            return web.json_response({"data": True})

        app = web.Application()
        app.router.add_post("/api/v2/server/machine/status", status)
        runner = web.AppRunner(app)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]

        with TemporaryDirectory() as directory:
            proc = Path(directory)
            (proc / "net").mkdir()
            (proc / "stat").write_text("cpu  10 0 0 90 0 0 0 0 500 0\n", encoding="ascii")
            (proc / "net/dev").write_text(
                "headers\nheaders\n eth0: 100 0 0 0 0 0 0 0 200 0 0 0 0 0 0 0\n"
                " lo: 999 0 0 0 0 0 0 0 999 0 0 0 0 0 0 0\n",
                encoding="ascii",
            )
            with patch("machine.time.monotonic", return_value=1):
                previous = read_load_counters(proc)
            self.assertEqual(previous, (1, 100, 90, {"eth0": (100, 200)}))
            (proc / "meminfo").write_text(
                "MemTotal: 8000 kB\nMemAvailable: 2000 kB\nSwapTotal: 100 kB\nSwapFree: 75 kB\n",
                encoding="ascii",
            )
            current = (3, 200, 160, {"eth0": (600, 300)})
            following = (5, 300, 230, {"eth0": (1100, 400)})

            async with ClientSession() as session:
                machine = Machine(None, {
                    "panel": {"api_host": "https://example.test", "api_key": "test-token"},
                    "machine": {"id": 35},
                    "bridge": {"state_dir": directory},
                }, session)
                machine.base = f"http://127.0.0.1:{port}"
                with (
                    patch("machine.LOAD_REPORT_INTERVAL", 0.02),
                    patch("machine.read_load_counters", side_effect=[previous, current, following]),
                    patch("machine.build_load_report", side_effect=lambda *args: build_load_report(*args, proc=proc)),
                ):
                    task = asyncio.create_task(machine.report_load())
                    try:
                        await asyncio.wait_for(received.wait(), 5)
                    finally:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)

        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0], reports[1])
        self.assertEqual(reports[1]["machine_id"], "35")
        self.assertEqual(reports[1]["token"], "test-token")
        self.assertEqual(reports[1]["cpu"], 30.0)
        self.assertEqual(reports[1]["mem"], {"total": 8192000, "used": 6144000})
        self.assertEqual(reports[1]["swap"], {"total": 102400, "used": 25600})
        self.assertEqual(reports[1]["net"], {"in_speed": 250.0, "out_speed": 50.0})
        self.assertGreater(reports[1]["disk"]["total"], 0)
