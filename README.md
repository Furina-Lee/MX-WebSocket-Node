# xboard-node

Node backend for [Xboard](https://github.com/Furina-Lee/Xboard). Supports `sing-box` / `xray-core` dual kernels.

> **Disclaimer**: This project is for educational and learning purposes only.

## Features

- Protocols: V2Ray family, Trojan, Shadowsocks, Hysteria2, TUIC, AnyTLS
- Sync: WebSocket push + REST polling dual channel
- User controls: speed limit, device limit, alive-IP tracking, hot update
- Deploy modes: node mode, machine mode, standalone mode
- Multi-instance: single process binding multiple panels / nodes

## Install

### Docker

```bash
docker run -d --restart=always --network=host \
  -e apiHost=https://panel.com -e apiKey=TOKEN -e nodeID=1 \
  ghcr.io/furina-lee/xboard-node:latest
```

For machine mode, set `MACHINE_ID` instead of `nodeID`, and pass the machine token as `MACHINE_TOKEN`. The image supports Linux AMD64 and ARM64.

### Docker Compose

```bash
git clone -b compose --depth 1 https://github.com/Furina-Lee/Xboard-Node.git xboard-node
cd xboard-node
vim config/config.yml   # set panel.url / token / node_id
docker compose up -d
```

### Installer (Linux systemd)

```bash
# Node mode
curl -fsSL https://raw.githubusercontent.com/Furina-Lee/Xboard-Node/v1.13-furina.3/install.sh | \
  sudo bash -s -- --mode node --panel https://panel.example.com --token TOKEN --node-id 1 --version v1.13-furina.3

# Machine mode
curl -fsSL https://raw.githubusercontent.com/Furina-Lee/Xboard-Node/v1.13-furina.3/install.sh | \
  sudo bash -s -- --mode machine --panel https://panel.example.com --token TOKEN --machine-id 1 --version v1.13-furina.3
```

### Update an existing node

For the traffic quota fix, update Xboard first and verify that the panel migration `node_traffic_reports` has run. Then upgrade the node. [Panel upgrade instructions](https://github.com/Furina-Lee/Xboard#升级到流量配额修复版)

**Linux systemd**

Run this on each node host. The fork's installer downloads the matching AMD64 or ARM64 binaries, backs up the existing installation, and restarts the service using the existing node configuration.

```bash
curl -fsSL https://raw.githubusercontent.com/Furina-Lee/Xboard-Node/v1.13-furina.3/install.sh -o xboard-node-install.sh &&
sudo bash xboard-node-install.sh upgrade --version v1.13-furina.3
sudo xbctl version
sudo systemctl is-active xboard-node
```

If your installed `xbctl` already uses the Furina-Lee release repository, you can also run `sudo xbctl upgrade --version v1.13-furina.3`.

**Docker**

Use `ghcr.io/furina-lee/xboard-node:v1.13-furina.3` and recreate the node container with its existing configuration and mounts. For Compose, follow the [Compose update commands](https://github.com/Furina-Lee/Xboard-Node/tree/compose#update-an-existing-compose-node).

## xbctl

Run `xbctl` after installation for help. Common commands:

```bash
xbctl list                          # list all instances
xbctl status                        # running status
xbctl bind add-node --panel URL --token TOKEN --node-id 1
xbctl bind add-machine --panel URL --token TOKEN --machine-id 1
xbctl bind remove-node --panel URL --node-id 1
xbctl service restart
```

## Configuration

Legacy single-panel config is fully compatible. Appending bindings auto-migrates to `instances` format. See `config.yml.example`.

## Extensions

- Custom routes: [docs-custom-routes.md](docs-custom-routes.md)
- Custom outbounds: [docs-custom-outbounds.md](docs-custom-outbounds.md)
- DNS providers (ACME DNS-01): [docs-dns-providers.md](docs-dns-providers.md)

## License

MPL-2.0.
