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

- **面板**使用已集成流量回执支持的 [Furina-Lee/Xboard 镜像](https://github.com/Furina-Lee/Xboard#docker-镜像)，确认已执行 `node_traffic_reports` 数据库迁移。WebSocket 为可选实时通道。启用时，反代应支持 `/ws` 升级；只用 HTTPS 时，节点也能同步配置并上报流量，无需对公网开放 `8076`。自编译旧版面板时，可先检查并应用 [面板补丁](xboard-panel.patch)，再重建面板并重启 WebSocket worker。补丁基于 Xboard 提交 `64b5e15`。
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
4. 安装器会先验证机器凭据，再安装并启动 `mx-websocket-node.service`。运行中自动发现该机器下启用的节点，并在 WebSocket 可用时复用一条连接。WebSocket 不可用时，节点会改走面板 HTTPS。

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

## 同机连接多个面板

在每个 Xboard 面板的「系统配置 → 节点设置」填写不同的「节点实例名称」，再从「服务器管理」复制安装命令。也可手动追加 `--instance airport-a`。名称使用 1–32 个小写字母、数字或连字符，首字符须为字母或数字。

命名实例使用 `mx-websocket-node@airport-a.service`、`/etc/mx-websocket-node/airport-a.json` 和 `/var/lib/mx-websocket-node-airport-a/`。程序安装到 `/opt/mx-websocket-node/airport-a/`。请在各面板分配互不冲突的监听地址和端口。留空时使用默认服务和配置。同名 unit 已加载或配置文件已存在时，安装器会退出。同一台机器上的并行安装会排队执行。

管理命名实例时，带上完整服务名：

```bash
sudo systemctl status mx-websocket-node@airport-a.service
sudo systemctl restart mx-websocket-node@airport-a.service
sudo journalctl -u mx-websocket-node@airport-a.service -f
```

## 从旧 Xboard-Node 一键迁移

适用于同一台 Linux 机器上正在运行的 `xboard-node.service`，并使用新版 Xboard 面板的机器管理。先确认要迁移的节点都已绑定到该机器且已启用，协议与当前 `proxy-node` 兼容。记录原节点的监听端口。已有 Docker Compose 节点须先安排停止旧容器，随后使用 [机器一键安装](#a-服务器管理一键安装)。

**执行方法**：在面板「服务器管理」复制该机器的新版安装命令，在末尾追加 `--migrate-xboard-node`，例如：

```bash
curl -fsSL --proto '=https' --proto-redir '=https' https://raw.githubusercontent.com/Furina-Lee/MX-WebSocket-Node/dev/install.sh | sudo bash -s -- --mode machine --panel 'https://panel.example.com' --token '<机器 Token>' --machine-id 28 --migrate-xboard-node
```

使用实际面板地址、机器 Token 和机器 ID。如果安装器找不到原版 `proxy-node`，先解压对应架构的原版发行包到当前目录，或追加 `--binary /path/to/proxy-node`。

脚本在旧服务仍在线时验证机器配置、准备新程序和 Python 依赖，再短暂停用旧服务。新服务须在验收窗口内启动面板分配的所有节点，否则脚本会尝试恢复旧服务。成功后停止并取消旧服务的开机启动，保留旧程序和配置。旧服务独有的本地证书及自定义内核参数不会自动搬迁，请事先核对。启动日志只能确认进程已加载配置，迁移后仍须刷新订阅、实际下载并核对计费。

如需手动回滚，在节点机执行：

```bash
sudo systemctl disable --now mx-websocket-node
sudo systemctl start xboard-node
```

如果旧服务迁移前设为开机启动，回滚后再执行 `sudo systemctl enable xboard-node`。保留新版的配置和状态目录，方便再次切换并保留待上报记录。

## 更新已安装的新版节点

适用于已有 `mx-websocket-node.service` 的 Linux 节点。下面更新的是节点适配器，沿用当前 `proxy-node`、机器凭据和状态目录。重启会短暂断开节点连接。将 `rev` 换成准备部署的已审核提交。

```bash
sudo bash <<'BASH'
set -euo pipefail
app=/opt/mx-websocket-node
rev=8e0e2f2ae3c145abe59e4e5fb9097234e5068ebe
url="https://raw.githubusercontent.com/Furina-Lee/MX-WebSocket-Node/$rev"
backup=$(mktemp -d /var/backups/mx-node-update.XXXXXXXX)

for file in mx_node.py machine.py; do
    cp -p "$app/$file" "$backup/$file"
    curl -fsSL --retry 3 "$url/$file" -o "$backup/new-$file"
done
"$app/.venv/bin/python" -m py_compile "$backup/new-mx_node.py" "$backup/new-machine.py"
for file in mx_node.py machine.py; do
    install -m 0644 "$backup/new-$file" "$app/$file"
done
printf 'Backup: %s\n' "$backup"
systemctl restart mx-websocket-node
BASH
```

用 `systemctl is-active mx-websocket-node`、启动日志和 `ss -ltnup` 核对服务和实际监听。若新版启动异常，把备份目录里的 `mx_node.py` 和 `machine.py` 恢复到 `/opt/mx-websocket-node/`，再重启服务。保留 `/var/lib/mx-websocket-node/`，其中有待上报记录。

## 协议与传输切换

机器模式通过节点发现跟踪同一节点的协议类型。切换协议类型时，会关闭旧进程再启动新进程。在同一协议内更换传输（如 MundoRDP 转 MC1）、监听端口或 TLS 设置时，节点会在收到 WebSocket 配置或下一次 HTTPS 配置查询后，仅重启该节点的 `proxy-node`。这会短暂断开该节点的现有连接。其他节点和流量上报队列继续运行。

独立模式的协议类型来自本地 `panel.node_type`。若要在它上面更换协议类型，还需要更新该字段。两种模式都支持同一协议内的传输、端口和 TLS 自动切换。

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

## 实测连通和流量上报

1. 用测试账号刷新订阅，手动选中目标节点，确认显示的协议、传输、地址和端口匹配面板。验证网页能返回响应。
2. 记录测试账号的已用流量及节点倍率。用这个账号通过该节点完整下载一次约 10 MB 的 HTTPS 文件，随后停止代理流量。
3. 等待一个上报周期后刷新流量，按节点倍率比较增量。上传和协议开销也可能计入。再等一个周期确认没有重复增加同量级的流量。
4. 下载成功但没有入账时，按测试时间检查节点日志里的 `HTTPS traffic report` 错误或 WebSocket 回执能力，并核对面板的 `node_traffic_reports` 迁移。若只有延迟或 TCP 测试成功、网页仍超时，先检查节点实际加载的协议和传输，再查客户端握手。

## 通信与计费

节点通过 WebSocket 接收 `sync.config`，原版工作进程再从本机中继拉取配置，配置的拉取间隔为 10 秒。WebSocket 断开时改用面板 HTTPS 查询配置。用户列表、在线设备和状态走 HTTPS。

流量先写入本地 SQLite，通过 WebSocket 上报，收到计费回执后才从队列删除。断线补发沿用同一报告 ID。WebSocket 不可用或没有流量回执能力时，节点使用 `POST /api/v2/server/report`，沿用原报告 ID，收到 HTTPS 成功回执后再删除队列记录。两条发送路径按节点串行切换。若两条路径都不可用，流量留在本地队列。面板默认保留去重记录 7 天，若超过该窗口才补发已入账但未确认的记录，可能重复计费。子进程临时配置中的密钥仅用于本机中继。
