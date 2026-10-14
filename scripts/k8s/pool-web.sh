#!/usr/bin/env bash
# 本地手测池化沙箱:用浏览器打开某个已登录工号的 opencode Web 界面。
#
#   scripts/k8s/pool-web.sh <工号> [端口,默认 18095]      前台运行,Ctrl-C 结束
#
# 池化下没有网关:口令只在平台(这里是 pool-sim.py 的映射表 ~/.pool-sim.json)。浏览器带不了 Basic 头,
# 所以和以前截图验证一样,经 auth-proxy.py 在本机注入口令,再经 kubectl port-forward 到沙箱的 4096。
# 只在本机用;口令写进 0600 的临时文件,退出即删。先用 pool-sim.py login <工号> 登录。
set -euo pipefail
HERE=$(cd "$(dirname "$0")/../.." && pwd)
U=${1:?用法: pool-web.sh <工号> [端口]}; PORT=${2:-18095}
C=${CONTEXT:-orbstack}; NS=gaussdb-agent
STATE=${POOL_SIM_STATE:-$HOME/.pool-sim.json}
PY=${PY:-$HOME/p2venv/bin/python3}
export PATH=$HOME/.orbstack/bin:/usr/local/bin:$PATH
WORK=$(mktemp -d); chmod 700 "$WORK"
"$PY" - "$STATE" "$U" "$WORK/pw" > "$WORK/pod" <<'PY'
import json, os, sys
state, user, pwfile = sys.argv[1:4]
u = json.load(open(state)).get("users", {}).get(user)
if not u:
    sys.exit("映射表 %s 里没有 %s:先 scripts/k8s/pool-sim.py login %s" % (state, user, user))
fd = os.open(pwfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
os.write(fd, u["password"].encode())
os.close(fd)
print(u["sandbox"])
PY
POD=$(cat "$WORK/pod")
kubectl --context "$C" -n "$NS" port-forward "pod/$POD" :4096 > "$WORK/pf.log" 2>&1 &
PF=$!
trap 'kill $PF $AP 2>/dev/null; wait $PF 2>/dev/null; rm -rf "$WORK"' EXIT INT TERM
AP=0
for _ in $(seq 1 20); do
  TP=$(grep -Eo '127\.0\.0\.1:[0-9]+' "$WORK/pf.log" | head -1 | cut -d: -f2 || true)
  [ -n "$TP" ] && break; sleep 0.5
done
[ -n "${TP:-}" ] || { echo "port-forward 没起来:$(cat "$WORK/pf.log")"; exit 1; }
"$PY" "$HERE/scripts/k8s/auth-proxy.py" "$PORT" "$TP" opencode "$WORK/pw" &
AP=$!
sleep 1
# /data/state/workspace 的 base64url:opencode Web 界面按项目路由(与网关形态同一路径)
echo "工号 ${U} → ${POD}"
echo "浏览器打开: http://127.0.0.1:${PORT}/L2RhdGEvc3RhdGUvd29ya3NwYWNl"
echo "(Ctrl-C 结束;口令由本机代理注入,不用输入)"
wait "$AP"
