# MX 原生节点接入 Xboard WebSocket

面向 Linux amd64 或 arm64。流量由原版 `proxy-node` 处理，节点端无需安装 Xboard-Node 或 sing-box。此包提供 WebSocket 适配器和面板补丁。支持服务器管理的机器模式和独立单节点模式。独立模式示例使用节点 105 和协议 `mx`。其他协议须由面板下发与原版二进制兼容的配置，并应逐个验收。

## 通信

- 面板通过 WebSocket 下发 `sync.config`。适配器将收到的原始节点配置通过环回 API 交给原版二进制，工作进程按配置的 10 秒间隔拉取，在二进制自身的刷新规则下应用。断线时配置查询回到面板 HTTPS API。
- 流量写入本地 SQLite 后才向工作进程返回成功。适配器通过 WebSocket 发送 `report.traffic`，收到计费回执后删除队列记录。断线或回执丢失会用相同的报告 ID 补发，面板沿用按节点和报告 ID 去重的计费逻辑。上游默认保留去重记录 7 天，长时间停机超过这个窗口时，已入账且未收到回执的旧记录可能再次计费。面板未启用补丁时，流量留在队列。
- 用户列表、在线设备和系统状态继续走面板 HTTPS API，保持原版工作进程的接口格式。独立节点的 `panel.api_key` 是面板的服务端通信密钥。工作进程临时配置里的密钥由适配器生成，只用于本机环回 API。

## 从 Xboard 服务器管理一键安装

1. 面板需要包含本仓库的 `xboard-panel.patch`，且应用原有 `node_traffic_reports` 数据库迁移。面板补丁以 Xboard 提交 `64b5e151bd0070438cf401678096f7b40e4cb399` 为基线。应用后重新部署面板并重启 WebSocket worker。旧版单节点流量补丁已应用时，请把机器认证能力字段和回执中的 `node_id` 两项变更合并进去。
2. 在机器管理中为该机器绑定并启用需要的节点。进入 **系统配置 → 节点设置**，将节点安装地址设为 `https://raw.githubusercontent.com/Furina-Lee/MX-WebSocket-Node/dev/install.sh`，清空版本覆盖。要固定版本，可以填写包含本安装器和配套文件的 Git 标签或提交。
3. 在 Linux 节点机上准备 Python 3.10+、`venv`、curl 和 systemd。安装器会查找当前目录的 `proxy-node`、`/opt/proxy-node/proxy-node`、`/usr/local/bin/mx-proxy-node` 与 `/usr/local/bin/proxy-node`。全新机器请先把原版对应架构的 `proxy-node` 解压到当前目录。请在迁移窗口停用旧服务以释放监听端口。
4. 在 **服务器管理** 中复制对应机器的安装命令，在节点机执行。面板生成的命令会带上 `--mode machine --panel ... --token ... --machine-id ...`，安装时不需要再次输入密钥。安装器用机器 Token 验证节点归属。服务运行中自动发现该机器下已启用节点，用同一个 WebSocket 连接下发配置和传送各节点流量。
5. 运行 `sudo journalctl -u mx-websocket-node -f` 检查节点启动与 WebSocket 回执。机器模式在服务端每次拉取配置时还会使用对应节点的 ID。对于尚未联调的协议，先用测试节点确认原版二进制可以应用该配置。

如果机器模式需要独立的 WebSocket 主机或端口，可在 `/etc/mx-websocket-node/config.json` 的 `bridge.ws_url` 中填写 `wss://` 地址，再重启服务。

## 手动安装单节点

1. 先用空闲端口和独立测试节点。面板补丁基于 Furina-Lee/Xboard 提交 `64b5e151bd0070438cf401678096f7b40e4cb399`。在面板源码目录执行 `git apply --check /path/to/xboard-panel.patch`，通过后应用补丁。确认已部署面板自带的 `node_traffic_reports` 数据库迁移，然后按原部署流程重建或重新部署面板，重启 WebSocket worker。面板需启用 WebSocket，反代需支持 `/ws` 升级。若面板公布不同主机名或端口的 WebSocket 地址，需要在节点 JSON 的 `bridge.ws_url` 中显式填写受信任的 `wss://` 地址。
2. 在节点机解包同架构的原始 7z。使用其中的 `proxy-node`。部署由以下 systemd 服务接管，可跳过原 `onekey.sh`。先确认测试端口与已有实例不冲突。
3. 在本包目录执行 `sudo bash install.sh /path/to/extracted/proxy-node`。安装程序会要求输入面板 HTTPS 地址、节点 ID 和面板通信密钥 `api_key`。密钥输入不回显，保存到仅 root 和服务组可读的配置。若节点已有本服务配置，安装程序会停止并保留原文件。
4. 执行 `journalctl -u mx-websocket-node -f`，确认收到节点配置及带回执的 WebSocket 连接。再通过真实客户端发起流量，核验面板计费。测试面板下发和流量补发后，再切换原生产监听。按原版 MX 的支持情况配置证书。若所选协议需要绑定小于 1024 的端口，Linux 可能要求向此 systemd 服务授予 `CAP_NET_BIND_SERVICE`。

临时工作配置和计费队列位于 `/var/lib/mx-websocket-node`。保留该目录可以跨重启继续补发。同一状态目录只运行一个实例。

