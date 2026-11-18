# Xboard-Node

[Xboard](https://github.com/Furina-Lee/Xboard) 节点服务。内置 sing-box 和 Xray 双内核，支持单节点、机器、多实例和独立运行模式。面板可通过 WebSocket 推送配置，节点也会定期通过 REST 核对状态。节点支持按用户限速、设备限制、在线统计和流量上报。

此分支还支持 **MX**。Xboard-Node 会为 MX 节点生成配置并启动独立的 `mx-proxy-node` 进程。MX 配置和用户列表来自面板，流量由 MX 进程上报。机器模式通过仅本机可访问的适配接口转发请求，MX 进程使用临时凭据。

## 选一种安装方式

| 方式 | 适用场景 |
| --- | --- |
| [Docker Compose](#docker-compose) | 已用 Compose 管理节点，希望挂载配置文件 |
| [Docker 命令](#docker-命令) | 单台 Linux 节点，配置通过环境变量传入 |
| [systemd 安装脚本](#systemd-安装脚本) | Linux 主机直接运行节点服务 |
| [手动安装二进制](#手动安装二进制) | 自行管理服务、权限或离线分发 |
| [从源码构建](#从源码构建) | 开发调试或自定义构建 |

预编译包和镜像支持 Linux AMD64、ARM64。首次安装前，先在面板创建机器或节点。机器模式使用**机器 SID 和机器 Token**，单节点模式使用**节点 ID 和服务器通讯密钥**。

### Docker Compose

```bash
git clone -b compose --depth 1 https://github.com/Furina-Lee/Xboard-Node.git xboard-node
cd xboard-node
# 编辑 config/config.yml，填入自己的面板地址和凭据
docker compose up -d
docker compose logs --tail=100 xboard-node
```

详细配置和更新方法见 [Compose 中文说明](https://github.com/Furina-Lee/Xboard-Node/tree/compose)。

### Docker 命令

机器模式可以从面板发现一台机器下的多个节点，包括 MX 节点。替换下面的占位值：

```bash
docker run -d --name xboard-node --restart unless-stopped --network host \
  -v xboard-node-data:/etc/xboard-node \
  -e API_HOST=https://panel.example.com \
  -e MACHINE_ID=12 \
  -e MACHINE_TOKEN=YOUR_MACHINE_TOKEN \
  ghcr.io/furina-lee/xboard-node:v1.13-furina.5
```

单节点模式改用 `API_KEY` 和 `NODE_ID`，并移除 `MACHINE_ID` 与 `MACHINE_TOKEN`。单独部署 MX 时还需设置 `NODE_TYPE=mx`。也可以挂载包含 `config.yml` 的目录到 `/etc/xboard-node`。

### systemd 安装脚本

下载对应版本的脚本：

```bash
curl -fsSL https://raw.githubusercontent.com/Furina-Lee/Xboard-Node/v1.13-furina.5/install.sh -o xboard-node-install.sh
```

机器模式：

```bash
sudo bash xboard-node-install.sh install --mode machine \
  --panel https://panel.example.com --token YOUR_MACHINE_TOKEN --machine-id 12 \
  --version v1.13-furina.5
```

单节点模式：

```bash
sudo bash xboard-node-install.sh install --mode node \
  --panel https://panel.example.com --token YOUR_SERVER_TOKEN --node-id 7 \
  --version v1.13-furina.5
```

单节点 MX 在命令中加入 `--node-type mx`。脚本会按 CPU 架构安装节点程序、`xbctl` 和 MX 进程。配置目录为 `/etc/xboard-node`。

### 手动安装二进制

从 [GitHub Releases](https://github.com/Furina-Lee/Xboard-Node/releases) 下载同一版本、同一架构的 `xboard-node-linux-<架构>`、`xbctl-linux-<架构>` 和 `mx-proxy-node-linux-<架构>`。下面以 AMD64 为例：

```bash
sudo install -m 755 xboard-node-linux-amd64 /usr/local/bin/xboard-node
sudo install -m 755 xbctl-linux-amd64 /usr/local/bin/xbctl
sudo install -m 755 mx-proxy-node-linux-amd64 /usr/local/bin/mx-proxy-node
sudo install -d -m 750 /etc/xboard-node
sudo install -m 600 config.yml.example /etc/xboard-node/config.yml
# 编辑 /etc/xboard-node/config.yml，再前台启动核对日志
sudo xboard-node -c /etc/xboard-node/config.yml
```

`config.yml.example` 可从本仓库取得。稳定后可用 systemd 托管相同启动命令。已有配置时保留原来的 `config.yml`。

### 从源码构建

安装 Go 1.26 和 Make。以下命令构建 Linux AMD64 附件。ARM64 使用 `make build-linux-arm64`。

```bash
git clone https://github.com/Furina-Lee/Xboard-Node.git
cd Xboard-Node
make test
make build-linux
```

MX 的 Linux 二进制位于 `bin/mx/`，按目标架构随节点程序安装。多实例和独立模式的配置见 [配置模板](config.yml.example)。

## 在面板启用 MX

1. 在 Xboard 创建 MX 节点，配置监听端口、传输和 TLS，并在机器模式下绑定已有机器。
2. 升级该机器上的 Xboard-Node 到 `v1.13-furina.5`。发现 MX 节点后，进程会自动拉取配置和用户。单节点模式请明确指定 `node_type: mx`。
3. TLS 使用本机已有证书时，在节点配置的 `cert.cert_file` 和 `cert.key_file` 设置文件路径。Docker 中使用容器可读的挂载路径。启用正式 TLS 时请用客户端验证证书信任链与域名。
4. 查看日志，确认 MX 已拉取用户并监听面板设置的端口。MX 流量走适配接口，需用真实 MX 客户端验证用量上报及配额耗尽后的存量连接行为，再用于计费生产流量。

默认 MX 可执行文件位于 `/usr/local/bin/mx-proxy-node`。自管二进制可以用 `XBOARD_MX_BINARY` 覆盖为绝对路径。

## 更新现有节点

先确认面板已更新到包含流量配额修复的版本，并完成 `node_traffic_reports` 数据库迁移。然后逐台更新节点。

**systemd**：下载本版本脚本后执行升级。脚本使用已有配置，备份并重启服务。

```bash
curl -fsSL https://raw.githubusercontent.com/Furina-Lee/Xboard-Node/v1.13-furina.5/install.sh -o xboard-node-install.sh &&
sudo bash xboard-node-install.sh upgrade --version v1.13-furina.5
sudo xbctl version
sudo systemctl is-active xboard-node
```

首次升级到 MX 版本请用上述脚本，以便同时安装 MX 进程。已有 MX 进程的部署应继续用脚本更新配套二进制。

**Docker Compose**：在现有部署目录把 `XBOARD_NODE_IMAGE` 或 Compose 中的 `image:` 改为 `ghcr.io/furina-lee/xboard-node:v1.13-furina.5`，再执行：

```bash
docker compose config --images
docker compose pull xboard-node &&
docker compose up -d xboard-node
docker compose exec xboard-node xboard-node -v
docker compose logs --tail=100 xboard-node
```

**直接运行的 Docker 容器**：先拉取新版镜像，再沿用原有环境变量、网络参数和挂载重建容器。**手动安装**：停止服务后替换同一发布版本的三份二进制，保留配置，再重启。

## 管理与扩展

```bash
sudo xbctl list
sudo xbctl status
sudo journalctl -u xboard-node -n 100 --no-pager
```

- [自定义出站](docs-custom-outbounds.md)
- [自定义路由](docs-custom-routes.md)
- [DNS-01 证书提供商](docs-dns-providers.md)
