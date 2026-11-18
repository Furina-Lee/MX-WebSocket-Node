# Xboard-Node Docker Compose

适用于 Linux AMD64 和 ARM64。Docker 镜像已经包含 MX 进程。完整的 systemd、手动二进制和源码安装方法见 [Xboard-Node 中文 README](https://github.com/Furina-Lee/Xboard-Node/blob/dev/README.md)。

## 首次安装

```bash
git clone -b compose --depth 1 https://github.com/Furina-Lee/Xboard-Node.git xboard-node
cd xboard-node
# 编辑 config/config.yml，再启动
docker compose up -d
docker compose logs --tail=100 xboard-node
```

### 单节点模式

填写配置中的 `panel.url`、`panel.token` 和 `panel.node_id`。这里的 Token 是面板的服务器通讯密钥。单独部署 MX 时，在 `panel` 下设置 `node_type: mx`。

### 机器模式

填写 `panel.url`，并在同一文件添加下面的机器配置。SID 和 Token 从面板的「机器管理」获取。

```yaml
machine:
  machine_id: 12
  token: "YOUR_MACHINE_TOKEN"
```

在面板创建 MX 节点并绑定该机器后，Xboard-Node 会自动发现并启动 MX 进程。可用以下命令查看版本和日志：

```bash
docker compose exec xboard-node xboard-node -v
docker compose logs --tail=100 xboard-node
```

默认使用主机网络，面板下发的监听端口需要在主机上可用。容器的 `/etc/xboard-node` 对应本机 `./config`。请勿把填了真实 Token 的配置提交到 Git。

## 更新现有节点

先更新面板并确认 `node_traffic_reports` 数据库迁移已执行。进入节点原有的 Compose 目录，保留 `config/config.yml` 和已有挂载，再在项目的 `.env` 中设置：

```dotenv
XBOARD_NODE_IMAGE=ghcr.io/furina-lee/xboard-node:v1.13-furina.6
```

如果旧 Compose 文件直接写了 `image:`，把该行改成同一个镜像。执行：

```bash
docker compose config --images
docker compose pull xboard-node &&
docker compose up -d xboard-node
docker compose exec xboard-node xboard-node -v
docker compose logs --tail=100 xboard-node
```

部署使用多个 Compose 文件时，沿用原有的全部 `-f` 参数。
