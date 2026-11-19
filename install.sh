#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 || $# -ne 1 ]]; then
  echo "用法：sudo bash install.sh /path/to/extracted/proxy-node" >&2
  exit 1
fi

source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
binary=$1
install_dir=/opt/mx-websocket-node
config_dir=/etc/mx-websocket-node
config_file="$config_dir/config.json"

for command in python3 systemctl install getent useradd; do
  command -v "$command" >/dev/null || { echo "缺少命令：$command" >&2; exit 1; }
done
if [[ $(uname -s) != Linux || ! -f $binary || ! -r $binary ]]; then
  echo '请在 Linux 上提供原版压缩包中已解压的 proxy-node 文件。' >&2
  exit 1
fi
python3 - "$binary" <<'PY'
import platform
import sys
with open(sys.argv[1], "rb") as binary:
    header = binary.read(20)
expected = {"x86_64": 62, "aarch64": 183}.get(platform.machine())
if (expected is None or len(header) != 20 or header[:6] != b"\x7fELF\x02\x01"
        or int.from_bytes(header[18:20], "little") != expected):
    sys.exit("proxy-node 文件无效，或架构与本机不匹配。")
PY

# 配置在安装之前由本人输入。密钥只通过管道交给配置生成器。
if [[ -e $config_file ]]; then
  echo "现有配置位于 $config_file。本脚本用于首次安装，现有配置需单独迁移。" >&2
  exit 1
fi
read -r -p 'Xboard 面板 HTTPS 地址：' panel_url </dev/tty
read -r -p '面板节点 ID：' node_id </dev/tty
read -r -s -p '面板服务端通信密钥（api_key）：' api_key </dev/tty
printf '\n'
if [[ -z ${api_key//[[:space:]]/} ]]; then
  echo 'api_key 不能为空。' >&2
  exit 1
fi

scratch=$(mktemp -d)
trap 'unset api_key; rm -f -- "$scratch/config.json"; rmdir -- "$scratch"' EXIT
printf '%s' "$api_key" | python3 -c '
import json, pathlib, sys
from urllib.parse import urlsplit
template, output, url, node_id = sys.argv[1:]
parsed = urlsplit(url)
if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
        or parsed.password is not None or parsed.query or parsed.fragment
        or url != url.strip()):
    sys.exit("请输入不带凭据、查询参数或片段的 HTTPS 面板地址。")
if not node_id.isascii() or not node_id.isdecimal() or int(node_id) <= 0:
    sys.exit("节点 ID 必须是正整数。")
data = json.loads(pathlib.Path(template).read_text(encoding="utf-8"))
data["panel"].update(api_host=url.rstrip("/"), node_id=int(node_id), api_key=sys.stdin.read())
with pathlib.Path(output).open("x", encoding="utf-8") as config:
    json.dump(data, config, ensure_ascii=False, indent=2)
    config.write("\n")
' "$source_dir/config.example.json" "$scratch/config.json" "$panel_url" "$node_id"
unset api_key

getent passwd mxnode >/dev/null || useradd --system --user-group --no-create-home --shell /usr/sbin/nologin mxnode
install -d -m 0755 "$install_dir"
install -d -m 0750 -o root -g mxnode "$config_dir"
install -m 0755 -- "$binary" "$install_dir/proxy-node"
install -m 0644 -- "$source_dir/mx_node.py" "$source_dir/requirements.txt" "$source_dir/node_config.defaults.json" "$install_dir/"
python3 -m venv "$install_dir/.venv"
"$install_dir/.venv/bin/pip" install -r "$install_dir/requirements.txt"
install -m 0644 "$source_dir/mx-websocket-node.service" /etc/systemd/system/
install -m 0640 -o root -g mxnode "$scratch/config.json" "$config_file"
systemctl daemon-reload
systemctl enable --now mx-websocket-node.service
echo '节点服务已启动。可运行 journalctl -u mx-websocket-node -f 检查面板连接。'
