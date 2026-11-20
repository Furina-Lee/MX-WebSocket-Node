# MX-WebSocket-Node

面向 Xboard 的独立 Linux 节点端。原版 `proxy-node` 接收客户端连接，节点端负责从面板获取配置和上报流量。支持 Linux amd64 和 arm64。其他代理协议应先在测试节点验证面板配置与原版二进制兼容。

## 选择部署方式

| 部署方式 | 适用场景 | 凭据 |
| --- | --- | --- |
| [A. 服务器管理一键安装](#a-服务器管理一键安装) | 一台机器由面板统一管理一个或多个节点 | 机器 ID 和机器 Token，由面板加入安装命令 |
| [B. 手动安装独立单节点](#b-手动安装独立单节点) | 节点独立部署，或单独测试 MX | 节点 ID 和面板服务端通信密钥 `api_key` |
| [C. 旧版 Docker Compose](#c-旧版-docker-compose) | 继续维护既有的旧版 Xboard-Node 容器 | 见旧版 Compose 文档 |

A、B 使用同一个安装脚本和 systemd 服务。安装器每台主机只安装一个服务实例。需要在同一台机器管理多个节点时，选 A。

## 安装前检查

- **面板**使用已集成流量回执支持的 [Furina-Lee/Xboard 镜像](https://github.com/Furina-Lee/Xboard#docker-镜像)，确认已执行 `node_traffic_reports` 数据库迁移。启用面板 WebSocket，反向代理应支持 `/ws` 升级。自编译旧版面板时，可先检查并应用 [面板补丁](xboard-panel.patch)，再重建面板并重启 WebSocket worker。补丁基于 Xboard 提交 `64b5e15`。
- **节点机**需要 Linux、systemd、Python 3.10+、`venv` 和 curl。安装时需要 root，并能访问面板 HTTPS、GitHub Raw 和 Python 依赖源。Debian/Ubuntu 使用系统 Python 且缺少 `ensurepip` 时，脚本会通过 apt 安装 `python3-venv`；其他环境请预先安装对应的 venv 依赖。
- **原版二进制**需自行准备。运行 `uname -m`，`x86_64` 选 amd64，`aarch64` 选 arm64。从对应原版压缩包解压出 `proxy-node`。安装器会检查 ELF 架构，并依次查找当前目录的 `proxy-node`、`/opt/proxy-node/proxy-node`、`/usr/local/bin/mx-proxy-node`、`/usr/local/bin/proxy-node`。也可以用 `--binary /绝对路径/proxy-node` 指定文件。
- **迁移**先备份旧配置，确认绑定端口及维护窗口。若旧 `xboard-node.service` 或 `proxy-node.service` 仍在运行，安装器会退出。停用旧服务会影响它管理的节点。已有本服务配置时，安装器会退出并保留配置。

## A. 服务器管理一键安装

1. 在 Xboard「服务器管理」创建或选择机器，把要运行的节点绑定到该机器并启用。
2. 在「系统配置 → 节点设置」确认安装地址指向下方地址。清除旧版本覆盖。需要固定版本时，只使用包含本安装脚本及配套文件的标签或提交。

   ```text
   https://raw.githubusercontent.com/Furina-Lee/MX-WebSocket-Node/dev/install.sh
   ```

3. 在「服务器管理」打开目标机器的详情并复制安装命令。在节点机运行。全新机器先把原版 `proxy-node` 解压到运行命令的目录。命令会包含 `--mode machine`、`--panel`、`--token` 和 `--machine-id`，因此无需另行输入节点 ID 或服务端通信密钥。
4. 安装器会先验证机器凭据，再安装并启动 `mx-websocket-node.service`。运行中自动发现该机器下启用的节点，并在同一个 WebSocket 上接收配置和上报各节点流量。

复制的安装命令含机器 Token。请只在自己的节点机执行，分享日志或截图前遮住 Token。

## B. 手动安装独立单节点

先在面板创建一个独立部署的 MX 节点，记下节点 ID。将下方最后一行的路径换成实际二进制路径：

```bash
git clone --branch dev --depth 1 https://github.com/Furina-Lee/MX-WebSocket-Node.git
cd MX-WebSocket-Node
sudo bash install.sh --binary /path/to/extracted/proxy-node
```

按提示填写面板 HTTPS 地址、节点 ID、面板服务端通信密钥 `api_key`。密钥输入不回显。此模式使用面板的**服务端通信密钥**，机器 Token 用于 A 的机器模式。

## C. 旧版 Docker Compose

已有旧版 Xboard-Node 容器的站点，继续使用 [`compose` 分支的维护说明](https://github.com/Furina-Lee/MX-WebSocket-Node/blob/compose/README.md)。该分支使用原有 `ghcr.io/furina-lee/xboard-node:v1.13-furina.7` 镜像。要部署此 README 的新版独立节点，使用 A 或 B。

## 验收与维护

```bash
sudo systemctl status mx-websocket-node --no-pager
sudo journalctl -u mx-websocket-node -n 80 --no-pager
sudo ss -ltnup
```

- **配置与监听**：核对进程监听的端口是否与面板下发一致。在测试节点修改配置后，查看相应日志和实际监听，确认原版进程应用了变更。
- **计费**：有 WebSocket 时，日志应显示已连接并支持流量回执。仅使用 HTTPS 时，确认面板能访问 `/api/v2/server/report` 且没有持续的上报失败日志。用真实客户端选中测试节点访问目标并收到响应，再到面板确认账户流量入账。
- **连接失败**：机器模式先查节点是否绑定且启用、面板是否接受机器 Token。能登录但没有回执，再查面板镜像、数据库迁移和 `/ws` 反代。面板 WebSocket 使用独立主机或端口时，可在 `bridge.ws_url` 显式填写受信任的 `wss://` 地址。
- **配置文件**为 `/etc/mx-websocket-node/config.json`。证书字段见 [示例配置](config.example.json)。修改配置后执行 `sudo systemctl restart mx-websocket-node`。使用外部证书时，服务账号需能读取它。低端口可能需要额外的绑定权限。
- **运行数据**位于 `/var/lib/mx-websocket-node`，包括流量队列和子进程临时配置。升级或回滚时保留该目录。切回旧节点服务时，先停用新服务，再启用原先使用的旧服务。

## 通信与计费

节点通过 WebSocket 接收 `sync.config`，原版工作进程再从本机中继拉取配置，配置的拉取间隔为 10 秒。WebSocket 断开时改用面板 HTTPS 查询配置。用户列表、在线设备和状态走 HTTPS。

流量先写入本地 SQLite，通过 WebSocket 上报，收到计费回执后才从队列删除。断线补发沿用同一报告 ID。WebSocket 不可用或没有流量回执能力时，节点使用 `POST /api/v2/server/report`，沿用原报告 ID，收到 HTTPS 成功回执后再删除队列记录。两条发送路径按节点串行切换。若两条路径都不可用，流量留在本地队列。面板默认保留去重记录 7 天，若超过该窗口才补发已入账但未确认的记录，可能重复计费。子进程临时配置中的密钥仅用于本机中继。
