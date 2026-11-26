#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077

fail() { printf '%s\n' "$*" >&2; exit 1; }
instance=
version=
while (( $# )); do
    case "$1" in
        --instance|--version)
            (( $# >= 2 )) && [[ -n $2 ]] || fail "缺少 $1 的参数。"
            if [[ $1 == --instance ]]; then instance=$2; else version=$2; fi
            shift 2 ;;
        -h|--help)
            printf '%s\n' '用法：sudo bash upgrade.sh [--instance NAME] [--version TAG]'
            exit 0 ;;
        *) fail "未知参数：$1" ;;
    esac
done

[[ $EUID == 0 && $(uname -s) == Linux ]] || fail '请在 Linux 节点机上以 root 执行。'
[[ -z $instance || $instance =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]] || fail '实例名称无效。'
[[ -z $version || $version =~ ^mx-node-[0-9]{8}([.-][0-9]+)?$ ]] || fail '无效的 MX 节点发行标签。'
for command in curl python3 systemctl flock sha256sum; do
    command -v "$command" >/dev/null || fail "缺少命令：$command"
done

app=/opt/mx-websocket-node
config=/etc/mx-websocket-node/config.json
service=mx-websocket-node.service
if [[ -n $instance ]]; then
    app="$app/$instance"
    config="/etc/mx-websocket-node/$instance.json"
    service="mx-websocket-node@$instance.service"
fi
exec 9>/run/lock/mx-websocket-node-install.lock
flock -w 180 9 || fail '另一项节点安装或升级正在运行。'
[[ -f $config && -x $app/.venv/bin/python ]] || fail "未找到 $service 的配置或 Python 环境。"
[[ $(systemctl show -p LoadState --value "$service") == loaded ]] || fail "未找到 systemd 单元 $service。"
case "$(uname -m)" in
    x86_64) arch=amd64 ;;
    aarch64) arch=arm64 ;;
    *) fail '仅支持 Linux amd64 和 arm64。' ;;
esac

download() {
    curl -fsSL --proto '=https' --proto-redir '=https' --retry 2 --connect-timeout 10 --max-time 90 -- "$1"
}

repo=Furina-Lee/MX-WebSocket-Node
if [[ -z $version ]]; then
    version=$(download "https://api.github.com/repos/$repo/releases?per_page=100" | python3 -c '
import json, re, sys
pattern = re.compile(r"mx-node-([0-9]{8})(?:[.-]([0-9]+))?")
releases = []
for release in json.load(sys.stdin):
    tag = release["tag_name"]
    match = pattern.fullmatch(tag)
    if match and not release["draft"] and not release["prerelease"]:
        date, revision = match.groups()
        releases.append((date, int(revision or 0), tag))
if not releases:
    sys.exit("未找到已发布的 MX 节点发行版。")
print(max(releases)[2])
') || fail '无法获取发行版本，可用 --version 指定标签。'
fi

raw="https://raw.githubusercontent.com/$repo/$version"
assets="https://github.com/$repo/releases/download/$version"
kernel="mx-proxy-node-linux-$arch"
files=(mx_node.py machine.py requirements.txt proxy-node)
backup=$(mktemp -d /var/backups/mx-node-update.XXXXXXXX)
stage=
new_env=
switching=0
was_active=0

cleanup() {
    local status=$?
    trap - EXIT
    set +e
    if (( status != 0 && switching )); then
        echo "升级失败，正在恢复 $service。备份在 $backup。" >&2
        local failed=0 file
        for file in "${files[@]}"; do
            cp -p -- "$backup/$file" "$stage/$file.previous" &&
                mv -f -- "$stage/$file.previous" "$app/$file" || failed=1
        done
        if [[ -e $backup/.venv || -L $backup/.venv ]]; then
            if [[ -L $app/.venv ]]; then unlink "$app/.venv" || failed=1; fi
            mv -- "$backup/.venv" "$app/.venv" || failed=1
        fi
        if (( was_active )); then
            systemctl restart "$service" && systemctl is-active --quiet "$service" || failed=1
        else
            systemctl stop "$service" || failed=1
        fi
        (( failed == 0 )) || echo "自动恢复未完成，请检查备份目录 $backup。" >&2
    fi
    [[ -z $stage ]] || rm -rf -- "$stage"
    if [[ -n $new_env && ! $app/.venv -ef $new_env ]]; then rm -rf -- "$new_env"; fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

stage=$(mktemp -d "$app/.upgrade.XXXXXXXX")
echo "正在升级 $service 到 $version（linux-$arch）。"
for file in "${files[@]}"; do
    cp -p -- "$app/$file" "$backup/$file"
    if [[ $file == proxy-node ]]; then
        download "$assets/$kernel" > "$stage/$file"
    else
        download "$raw/$file" > "$stage/$file"
    fi
done
download "$assets/SHA256SUMS" > "$stage/SHA256SUMS"
(
    cd "$stage"
    awk -v name="$kernel" '$2 == name {print $1 "  proxy-node"; count++} END {if (count != 1) exit 1}' SHA256SUMS | sha256sum -c -
    "$app/.venv/bin/python" -m py_compile mx_node.py machine.py
)
chmod 0644 -- "$stage/mx_node.py" "$stage/machine.py" "$stage/requirements.txt"
chmod 0755 -- "$stage/proxy-node"
if ! cmp -s "$backup/requirements.txt" "$stage/requirements.txt"; then
    new_env=$(mktemp -d "$app/.venv-release.XXXXXXXX")
    chmod 0755 "$new_env"
    (
        umask 022
        "$app/.venv/bin/python" -m venv "$new_env"
        "$new_env/bin/python" -m pip install -r "$stage/requirements.txt"
    )
fi

systemctl is-active --quiet "$service" && was_active=1
switching=1
if [[ -n $new_env ]]; then
    mv -- "$app/.venv" "$backup/.venv"
    ln -s -- "$new_env" "$app/.venv"
fi
for file in "${files[@]}"; do
    mv -f -- "$stage/$file" "$app/$file"
done
systemctl restart "$service"
pid=$(systemctl show -p MainPID --value "$service")
sleep 5
[[ $pid =~ ^[1-9][0-9]*$ && $pid == "$(systemctl show -p MainPID --value "$service")" ]] &&
    systemctl is-active --quiet "$service" || fail "$service 未能稳定启动。"
switching=0
printf '已更新 %s 到 %s。备份目录：%s\n' "$service" "$version" "$backup"
