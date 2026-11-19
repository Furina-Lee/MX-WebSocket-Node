import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import secrets
import signal
import sqlite3
import sys
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, TraceConfig, WSMsgType, web

MAX_REPORT = 2 * 1024 * 1024
MAX_SNAPSHOT = 32 * 1024 * 1024
METHODS = dict(config="GET", user="GET", alivelist="GET",
               push="POST", alive="POST", status="POST")
LOG = logging.getLogger("mx-node")


def validated_url(value, scheme):
    url = urlsplit(value)
    if (url.scheme != scheme or not url.hostname or url.username is not None
            or url.password is not None or url.query or url.fragment):
        raise ValueError(f"required a {scheme} URL without credentials, query or fragment")
    return url


def load_config(path):
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    panel = config["panel"]
    validated_url(panel["api_host"], "https")
    if type(panel["node_id"]) is not int or panel["node_id"] <= 0:
        raise ValueError("panel.node_id must be a positive integer")
    if not isinstance(panel["api_key"], str) or not panel["api_key"].strip():
        raise ValueError("panel.api_key must contain the panel server token")
    if config.get("panels"):
        raise ValueError("use one configuration and service per node")
    if options := config.get("bridge"):
        if options.get("ws_url"):
            validated_url(options["ws_url"], "wss")
    return config


class Bridge:
    def __init__(self, config, session, state_dir):
        self.session = session
        self.panel = config["panel"]
        self.options = config.get("bridge", {})
        self.base = self.panel["api_host"].rstrip("/")
        self.node_id = str(self.panel["node_id"])
        self.node_type = self.panel["node_type"]
        self.credentials = dict(token=self.panel["api_key"], node_id=self.node_id,
                                node_type=self.node_type)
        if machine_id := self.options.get("machine_id"):
            self.credentials["machine_id"] = str(machine_id)
        self.api_prefix = ("/api/v2/server/" if machine_id
                           else "/api/v1/server/UniProxy/")
        self.local_token = secrets.token_hex(32)
        self.snapshot = None
        self.changed = asyncio.Event()
        self.closed = False
        self.queue = sqlite3.connect(state_dir / f"traffic-{self.node_id}.sqlite3")
        self.queue.execute("PRAGMA synchronous=FULL")
        self.queue.execute("""CREATE TABLE IF NOT EXISTS outbox (
            seq INTEGER PRIMARY KEY, report_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL
        )""")
        # Keep a persisted backlog bound to the original panel and protocol.
        self.queue.execute("CREATE TABLE IF NOT EXISTS binding (owner TEXT NOT NULL)")
        owner = self.base + "|" + self.node_type
        saved = self.queue.execute("SELECT owner FROM binding").fetchone()
        if saved is not None and saved[0] != owner:
            self.queue.close()
            raise ValueError("state directory belongs to a different panel or protocol")
        if saved is None:
            self.queue.execute("INSERT INTO binding VALUES (?)", (owner,))
        self.queue.commit()

    def config_response(self, data, request):
        data = dict(data)
        data["base_config"] = {"pull_interval": 10, "push_interval": 15}
        payload = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
        etag = '"' + hashlib.sha256(payload).hexdigest() + '"'
        if request.headers.get("If-None-Match") == etag:
            return web.Response(status=304, headers={"ETag": etag})
        return web.Response(body=payload, content_type="application/json",
                            headers={"ETag": etag})

    async def handle(self, request):
        if (not hmac.compare_digest(request.query.get("token", "").encode(),
                                    self.local_token.encode())
                or request.query.get("node_id") != self.node_id):
            raise web.HTTPUnauthorized()
        endpoint = request.match_info["endpoint"]
        if METHODS.get(endpoint) != request.method:
            raise web.HTTPNotFound()
        data = None
        if request.method == "POST":
            try:
                data = await request.json()
                if not isinstance(data, dict):
                    raise ValueError()
                for field in ("token", "node_id", "node_type", "machine_id"):
                    data.pop(field, None)
            except (ValueError, UnicodeError):
                raise web.HTTPBadRequest(text="invalid report") from None

        if endpoint == "push":
            if data:
                if len(data) > 50000 or any(not re.fullmatch(r"[1-9][0-9]{0,18}", uid)
                       or int(uid) >= 2**63
                       or not isinstance(row, list) or len(row) != 2
                       or any(type(value) is not int or not 0 <= value < 2**63
                              for value in row) for uid, row in data.items()):
                    raise web.HTTPBadRequest(text="invalid traffic counters")
                (size,) = self.queue.execute(
                    "SELECT coalesce(sum(length(body)), 0) FROM outbox").fetchone()
                body = json.dumps(data, separators=(",", ":"))
                if size + len(body) > 128 * MAX_REPORT:
                    raise web.HTTPServiceUnavailable(text="traffic backlog is full")
                with self.queue:
                    self.queue.execute("INSERT INTO outbox(report_id, body) VALUES (?, ?)",
                                       (secrets.token_hex(16), body))
                self.changed.set()
            return web.json_response({"data": True})

        if endpoint == "config" and self.snapshot is not None:
            return self.config_response(self.snapshot, request)

        headers = {}
        if endpoint != "config" and (etag := request.headers.get("If-None-Match")):
            headers["If-None-Match"] = etag
        try:
            async with self.session.request(
                request.method, self.base + self.api_prefix + endpoint,
                params=self.credentials, json=data, headers=headers, allow_redirects=False,
            ) as response:
                if response.status not in (200, 304):
                    LOG.warning("Panel %s returned HTTP %s", endpoint, response.status)
                    raise web.HTTPBadGateway(text="panel rejected request")
                if endpoint == "config":
                    data = await response.json()
                    if not self.valid_config(data):
                        raise web.HTTPBadGateway(text="invalid panel configuration")
                    return self.config_response(data, request)
                payload = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    payload.extend(chunk)
                    if len(payload) > MAX_SNAPSHOT:
                        raise web.HTTPBadGateway(text="oversized panel response")
                headers = {"ETag": response.headers["ETag"]} if "ETag" in response.headers else {}
                return web.Response(status=response.status, body=bytes(payload),
                                    headers=headers, content_type="application/json")
        except (asyncio.TimeoutError, ClientError, ValueError, OSError) as error:
            LOG.warning("Panel %s failed (%s)", endpoint, type(error).__name__)
            raise web.HTTPBadGateway(text="panel unavailable") from None

    def valid_config(self, data):
        return (isinstance(data, dict) and data.get("protocol") == self.node_type
                and type(data.get("server_port")) is int and 1 <= data["server_port"] <= 65535)

    async def send_reports(self, socket, acknowledgements):
        while not self.closed:
            row = self.queue.execute(
                "SELECT report_id, body FROM outbox ORDER BY seq LIMIT 1").fetchone()
            if row is None:
                self.changed.clear()
                await self.changed.wait()
                continue
            report_id, body = row
            data = {"report_id": report_id, "traffic": json.loads(body)}
            if self.options.get("machine_id"):
                data["node_id"] = int(self.node_id)
            await socket.send_json({"event": "report.traffic", "data": data})
            while True:
                ack = await asyncio.wait_for(acknowledgements.get(), timeout=20)
                if ack.get("report_id") == report_id:
                    if ack.get("accepted") is not True:
                        raise RuntimeError("panel declined traffic")
                    break
            if self.closed:
                return
            with self.queue:
                self.queue.execute("DELETE FROM outbox WHERE report_id=?", (report_id,))

    async def read_panel(self, socket, acknowledgements):
        async for message in socket:
            if message.type != WSMsgType.TEXT:
                continue
            packet = message.json()
            event, data = packet.get("event"), packet.get("data")
            if event == "ping":
                await socket.send_json({"event": "pong"})
            elif event == "sync.config" and isinstance(data, dict):
                config = data.get("config")
                if (str(data.get("node_id", self.node_id)) == self.node_id
                        and self.valid_config(config)):
                    self.snapshot = config
                    LOG.info("Received configuration for node %s", self.node_id)
            elif event == "report.traffic.ack" and isinstance(data, dict):
                if not acknowledgements.full():
                    acknowledgements.put_nowait(data)
        raise ConnectionError("panel WebSocket closed")

    async def websocket(self):
        delay = 2
        while True:
            try:
                ws_url = self.options.get("ws_url")
                if not ws_url:
                    async with self.session.get(
                        self.base + "/api/v2/server/handshake", params=self.credentials,
                        allow_redirects=False,
                    ) as response:
                        response.raise_for_status()
                        ws = (await response.json()).get("websocket", {})
                        if not ws.get("enabled"):
                            raise RuntimeError("WebSocket disabled at panel")
                        ws_url = ws["ws_url"]
                destination = validated_url(ws_url, "wss")
                origin = validated_url(self.base, "https")
                if not self.options.get("ws_url") and (
                    destination.hostname, destination.port or 443
                ) != (origin.hostname, origin.port or 443):
                    raise ValueError("set bridge.ws_url explicitly for a different WebSocket host")
                async with self.session.ws_connect(
                    ws_url, params={"token": self.panel["api_key"], "node_id": self.node_id},
                    heartbeat=25, max_msg_size=MAX_SNAPSHOT,
                ) as socket:
                    first = await asyncio.wait_for(socket.receive_json(), timeout=12)
                    if (first.get("event") != "auth.success"
                            or str(first.get("data", {}).get("node_id")) != self.node_id):
                        raise RuntimeError("WebSocket authentication rejected")
                    acknowledgements = asyncio.Queue(maxsize=16)
                    tasks = [asyncio.create_task(self.read_panel(socket, acknowledgements))]
                    if first["data"].get("traffic_report_ack") is True:
                        tasks.append(asyncio.create_task(self.send_reports(socket, acknowledgements)))
                        LOG.info("WebSocket connected with traffic acknowledgements")
                    else:
                        LOG.warning("Panel traffic patch missing; reports remain in the local queue")
                    delay = 2
                    try:
                        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                        for task in done:
                            task.result()
                    finally:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
            except Exception as error:
                LOG.warning("WebSocket unavailable (%s); reconnect in %ss", type(error).__name__, delay)
            finally:
                self.snapshot = None
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)


async def reject_redirect(session, context, params):
    raise ClientError("panel redirects are disabled")


async def run(path, config=None, bridges=None, snapshots=None):
    if config is None:
        config = load_config(path)
    state_dir = Path(config.get("bridge", {}).get("state_dir", path.parent / "state")).resolve()
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    executable = Path(config.get("bridge", {}).get("binary", path.parent / "proxy-node")).resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError("bridge.binary must point to an executable proxy-node")
    trace = TraceConfig()
    trace.on_request_redirect.append(reject_redirect)
    async with ClientSession(timeout=ClientTimeout(total=15), trace_configs=[trace],
                             headers={"User-Agent": "MX-WebSocket-Node/1.0"}) as session:
        bridge = Bridge(config, session, state_dir)
        app = web.Application(client_max_size=MAX_REPORT)
        for prefix in ("/api/v1/server/UniProxy/", "/api/v2/server/"):
            app.router.add_route("*", prefix + "{endpoint}", bridge.handle)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        config_file = state_dir / "worker.json"
        ws_task = worker = None
        try:
            await site.start()
            port = runner.addresses[0][1]
            worker_config = {key: value for key, value in config.items() if key != "bridge"}
            worker_config["panel"] = dict(config["panel"], api_host=f"http://127.0.0.1:{port}",
                                          api_key=bridge.local_token, pull_interval=10,
                                          push_interval=15)
            temp = state_dir / "worker.json.tmp"
            temp.write_text(json.dumps(worker_config), encoding="utf-8")
            temp.replace(config_file)
            if bridges is None:
                ws_task = asyncio.create_task(bridge.websocket())
            else:
                node_id = int(bridge.node_id)
                bridges[node_id] = bridge
                snapshot = snapshots.get(node_id)
                if bridge.valid_config(snapshot):
                    bridge.snapshot = snapshot
            env = {key: os.environ[key] for key in ("PATH", "TZ", "LANG") if key in os.environ}
            env["HOME"] = str(state_dir)
            worker = await asyncio.create_subprocess_exec(
                str(executable), "-c", str(config_file), cwd=state_dir, env=env)
            return await worker.wait()
        finally:
            if worker is not None and worker.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    worker.terminate()
                try:
                    await asyncio.wait_for(worker.wait(), 8)
                except asyncio.TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        worker.kill()
                    await worker.wait()
            if ws_task is not None:
                ws_task.cancel()
                await asyncio.gather(ws_task, return_exceptions=True)
            if bridges is not None:
                bridges.pop(int(bridge.node_id), None)
            await runner.cleanup()
            bridge.closed = True
            bridge.changed.set()
            bridge.queue.close()
            config_file.unlink(missing_ok=True)


async def main():
    task = asyncio.current_task()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    path = Path(sys.argv[1]).resolve()
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    if config.get("machine"):
        from machine import run_machine
        return await run_machine(path, config)
    return await run(path)


if __name__ == "__main__":
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(sys.argv) != 2:
        sys.exit("Usage: python3 mx_node.py /absolute/path/config.json")
    try:
        sys.exit(asyncio.run(main()))
    except asyncio.CancelledError:
        sys.exit(0)
    except Exception as error:
        LOG.error("Node startup failed (%s)", type(error).__name__)
        sys.exit(1)

