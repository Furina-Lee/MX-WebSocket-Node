# xboard-node — Docker Compose

## Quick start

```bash
git clone -b compose --depth 1 https://github.com/Furina-Lee/Xboard-Node.git xboard-node
cd xboard-node
# Edit config/config.yml: panel.url, panel.token, panel.node_id
docker compose up -d
```

For machine mode, set the panel URL and add this to `config/config.yml` with the SID and token from Xboard's machine management page:

```yaml
machine:
  machine_id: 1
  token: "your-machine-token"
```

For an environment-based setup, provide `API_HOST`, `MACHINE_ID`, and `MACHINE_TOKEN` to the container.

## Update an existing Compose node

Upgrade Xboard and verify its `node_traffic_reports` migration first. Then, in the node's existing Compose directory, set the following in the Compose project's `.env` file. If `compose.yml` has a hard-coded `image:` instead, update that line to the same image. Keep the existing `config/config.yml` and mounts.

```dotenv
XBOARD_NODE_IMAGE=ghcr.io/furina-lee/xboard-node:v1.13-furina.3
```

```bash
docker compose config --images
docker compose pull xboard-node &&
docker compose up -d xboard-node
docker compose exec xboard-node xboard-node -v
docker compose logs --tail=100 xboard-node
```

If you use additional Compose files, pass the same `-f` arguments used for this deployment.

## Layout

| Path | Purpose |
|------|---------|
| `compose.yml` | Service definition |
| `config/config.yml` | Bundled config template. Keep local token edits out of Git. |
| `.env` | Optional local image override |

## Host network

`network_mode: host` is set so the node uses the host network stack (same as `docker run --network host`). Free the ports your protocols need.

## Image

Default: `ghcr.io/furina-lee/xboard-node:v1.13-furina.3`. Set `XBOARD_NODE_IMAGE` in the Compose project environment to use a different image or tag.
