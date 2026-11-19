import asyncio
import copy
import logging
from pathlib import Path
import re
from urllib.parse import urlsplit

from aiohttp import ClientSession, ClientTimeout, TraceConfig, WSMsgType

from mx_node import MAX_SNAPSHOT, reject_redirect, run, validated_url

LOG = logging.getLogger("mx-node.machine")


class Machine:
    def __init__(self, path, config, session):
        self.path = path
        self.config = config
        self.session = session
        self.base = config["panel"]["api_host"].rstrip("/")
        validated_url(self.base, "https")
        self.machine_id = config["machine"]["id"]
        if type(self.machine_id) is not int or self.machine_id <= 0:
            raise ValueError("machine.id must be a positive integer")
        self.auth = {"machine_id": str(self.machine_id), "token": config["panel"]["api_key"]}
        self.state = Path(config["bridge"]["state_dir"])
        self.workers = {}
        self.bridges = {}
        self.snapshots = {}
        self.refresh = asyncio.Event()
        self.registered_ids = None
        self.connected = None

    async def discover(self):
        async with self.session.post(self.base + "/api/v2/server/machine/nodes",
                                     json=self.auth, allow_redirects=False) as response:
            if response.status != 200:
                if response.status in (401, 403):
                    await self.reconcile({})
                raise RuntimeError(f"machine discovery HTTP {response.status}")
            rows = (await response.json())["nodes"]
            if not isinstance(rows, list):
                raise ValueError("invalid machine discovery")
            desired = {}
            for row in rows:
                node_id, protocol = row["id"], row["type"]
                if type(node_id) is not int or node_id <= 0 or not isinstance(protocol, str) or not re.fullmatch(r"[a-z0-9_-]+", protocol):
                    raise ValueError("invalid machine node")
                desired[node_id] = protocol
            await self.reconcile(desired)
            if self.connected is not None and self.registered_ids != set(desired):
                await self.connected.close()

    async def reconcile(self, desired):
        stopping = [(node_id, protocol, task)
                    for node_id, (protocol, task) in self.workers.items()
                    if desired.get(node_id) != protocol or task.done()]
        for _, _, task in stopping:
            task.cancel()
        await asyncio.gather(*(task for _, _, task in stopping), return_exceptions=True)
        for node_id, protocol, _ in stopping:
            del self.workers[node_id]
            LOG.info("Stopped node %s (%s)", node_id, protocol)
        for node_id, protocol in desired.items():
            if node_id not in self.workers:
                config = copy.deepcopy(self.config)
                config.pop("machine")
                config["panel"].update(node_id=node_id, node_type=protocol)
                directory = self.state / f"{node_id}-{protocol}"
                config["bridge"].update(machine_id=self.machine_id, state_dir=str(directory))
                config["mundoproxy"]["asset_path"] = str(directory)
                saved_cert = config["cert"]
                if saved_cert["mode"] == "self":
                    saved_cert.update(cert_file=str(directory / "cert.pem"),
                                      key_file=str(directory / "key.pem"))
                task = asyncio.create_task(self.worker(config, node_id))
                self.workers[node_id] = protocol, task
                LOG.info("Started node %s (%s)", node_id, protocol)

    async def worker(self, config, node_id):
        try:
            status = await run(self.path, config, self.bridges, self.snapshots)
            LOG.warning("Node %s exited with status %s", node_id, status)
        except Exception as error:
            LOG.warning("Node %s failed (%s)", node_id, type(error).__name__)

    async def ws_url(self):
        override = self.config["bridge"].get("ws_url")
        if override:
            validated_url(override, "wss")
            return override
        async with self.session.get(self.base + "/api/v2/server/handshake",
                                    params=self.auth, allow_redirects=False) as response:
            response.raise_for_status()
            ws = (await response.json())["websocket"]
            if not ws["enabled"]:
                raise RuntimeError("panel WebSocket disabled")
            remote = validated_url(ws["ws_url"], "wss")
            local = urlsplit(self.base)
            if (remote.hostname, remote.port or 443) != (local.hostname, local.port or 443):
                raise ValueError("configure bridge.ws_url for a separate endpoint")
            return ws["ws_url"]

    async def dispatch(self, socket, channels):
        async for message in socket:
            if message.type != WSMsgType.TEXT:
                continue
            packet = message.json()
            event = packet.get("event")
            if event == "ping":
                await socket.send_json({"event": "pong"})
                continue
            if event == "sync.nodes":
                self.refresh.set()
                continue
            data = packet.get("data")
            if not isinstance(data, dict):
                continue
            node_id = data.get("node_id")
            if type(node_id) is not int:
                continue
            bridge = self.bridges.get(node_id)
            if event == "sync.config" and node_id in self.registered_ids:
                config = data.get("config")
                self.snapshots[node_id] = config
                if bridge and bridge.valid_config(config):
                    bridge.snapshot = config
                    LOG.info("Received configuration for node %s", node_id)
            elif event == "report.traffic.ack" and node_id in channels:
                channel = channels[node_id][1]
                if not channel.full():
                    channel.put_nowait(data)
        raise ConnectionError("machine WebSocket closed")

    async def send(self, socket, channels):
        try:
            while True:
                for node_id, (bridge, queue, task) in list(channels.items()):
                    if task.done():
                        task.result()
                    if self.bridges.get(node_id) is not bridge:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        del channels[node_id]
                for node_id, bridge in self.bridges.items():
                    if node_id not in channels:
                        queue = asyncio.Queue(maxsize=16)
                        task = asyncio.create_task(bridge.send_reports(socket, queue))
                        channels[node_id] = bridge, queue, task
                await asyncio.sleep(0.5)
        finally:
            tasks = [entry[2] for entry in channels.values()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def websocket(self):
        delay = 2
        while True:
            try:
                async with self.session.ws_connect(await self.ws_url(), params=self.auth,
                                                   heartbeat=25, max_msg_size=MAX_SNAPSHOT) as socket:
                    auth = await asyncio.wait_for(socket.receive_json(), timeout=12)
                    if (auth.get("event") != "auth.success" or
                            auth.get("data", {}).get("machine_id") != self.machine_id):
                        raise RuntimeError("machine WebSocket authentication rejected")
                    self.registered_ids = set(auth["data"].get("node_ids", []))
                    self.connected = socket
                    channels = {}
                    tasks = [asyncio.create_task(self.dispatch(socket, channels))]
                    if auth["data"].get("traffic_report_ack") is True:
                        tasks.append(asyncio.create_task(self.send(socket, channels)))
                        LOG.info("Machine WebSocket connected with traffic acknowledgements")
                    else:
                        LOG.warning("Panel traffic patch missing; reports remain in local queues")
                    self.refresh.set()
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
                LOG.warning("Machine WebSocket unavailable (%s); retry in %ss",
                            type(error).__name__, delay)
            finally:
                self.connected = None
                self.registered_ids = None
                self.snapshots.clear()
                for bridge in self.bridges.values():
                    bridge.snapshot = None
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    async def run(self):
        ws = asyncio.create_task(self.websocket())
        try:
            while True:
                self.refresh.clear()
                try:
                    await self.discover()
                except Exception as error:
                    LOG.warning("Machine discovery failed (%s)", type(error).__name__)
                try:
                    await asyncio.wait_for(self.refresh.wait(), timeout=20)
                except asyncio.TimeoutError:
                    pass
        finally:
            ws.cancel()
            await asyncio.gather(ws, return_exceptions=True)
            await self.reconcile({})


async def run_machine(path, config):
    trace = TraceConfig()
    trace.on_request_redirect.append(reject_redirect)
    async with ClientSession(timeout=ClientTimeout(total=15), trace_configs=[trace]) as session:
        await Machine(path, config, session).run()

