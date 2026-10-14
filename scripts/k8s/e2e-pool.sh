#!/usr/bin/env bash
# 池化沙箱本地集群端到端(方案 docs/specs/2026-10-13-sandbox-pool-design.md §14)。在 Mac 上跑:
#
#   scripts/k8s/e2e-pool.sh                 预建 16 个,全套跑完后清理(沙箱 Pod、本轮工号的 NAS 目录)
#   SIZE=4 scripts/k8s/e2e-pool.sh          小池子快速跑
#   KEEP=1 scripts/k8s/e2e-pool.sh          跑完保留现场
#   MODEL=0 scripts/k8s/e2e-pool.sh         不发真模型消息(没配 model-api 时)
#
# 前提:本地有镜像 gaussdb-agent-runtime:$TAG(TAG 默认 pool-dev);命名空间里有 model-api、agent-config。
# 本脚本只新建池化自己的东西(sandbox-control / grmp-token 两个 Secret、sandbox-policy、沙箱 Pod),
# 不动网关、不动现有工号。工号每轮随机前缀(p<6 位>-NN),不和以前的轮次、不和人工测试的工号混。
#
# 平台由 scripts/k8s/pool-sim.py 扮演。Mac 访问不到 Pod IP,它走 kubectl port-forward——port-forward 不经
# Pod 网络、网络策略管不到,所以入站规则在第 11 步用带 / 不带平台标签的临时 Pod 单独验。
set -uo pipefail
HERE=$(cd "$(dirname "$0")/../.." && pwd)
C=${CONTEXT:-orbstack}; NS=gaussdb-agent
SIZE=${SIZE:-16}; TAG=${TAG:-pool-dev}; IMG=gaussdb-agent-runtime:$TAG
PY=${PY:-$HOME/p2venv/bin/python3}
KEEP=${KEEP:-0}; MODEL=${MODEL:-1}
RUN=p$(openssl rand -hex 3)
U(){ printf '%s-%02d' "$RUN" "$1"; }
WORK=$(mktemp -d); STATE=$WORK/pool-sim.json
export PATH=$HOME/.orbstack/bin:/usr/local/bin:$PATH
K(){ kubectl --context "$C" -n "$NS" "$@"; }
SIM(){ "$PY" "$HERE/scripts/k8s/pool-sim.py" --context "$C" --via port-forward --image "$IMG" \
         --pull IfNotPresent --size "$SIZE" --state "$STATE" "$@" 2>>"$WORK/sim.log"; }
J(){ "$PY" -c 'import json,sys
d=json.loads(sys.stdin.read() or "{}")
for k in sys.argv[1].split("."):
    d = d.get(k) if isinstance(d, dict) else None
print("" if d is None else (json.dumps(d, ensure_ascii=False) if isinstance(d, (dict, list)) else d))' "$1"; }
PASS=0; FAIL=0
ok(){ echo "  ✓ $*"; PASS=$((PASS+1)); }
bad(){ echo "  ✗ $*"; FAIL=$((FAIL+1)); }
check(){ local d=$1; shift; if "$@"; then ok "$d"; else bad "$d"; fi; }
pod_of(){ "$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["users"].get(sys.argv[2],{}).get("sandbox",""))' "$STATE" "$1"; }
# 看 NAS:借任意一个活着的副容器(只有它挂 users/)执行一段 Python
any_sidecar(){ K get pods -l role=sandbox --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}'; }
nas(){ K exec "$(any_sidecar)" -c poolctl -- python3 -c "$1"; }
main_sh(){ local pod=$1; shift; K exec "$pod" -c sandbox -- sh -c "$*"; }
echo "=== 池化 e2e:轮次 $RUN,池子 $SIZE,镜像 $IMG,工作目录 $WORK"

# ---- 0. 前提 --------------------------------------------------------------------
echo "--- 0. 前提"
docker image inspect "$IMG" >/dev/null 2>&1 || { echo "本地没有镜像 $IMG"; exit 2; }
K get secret model-api >/dev/null || { echo "缺 Secret model-api"; exit 2; }
K get secret sandbox-control >/dev/null 2>&1 || K create secret generic sandbox-control --from-literal=KEY="$(openssl rand -hex 32)" >/dev/null
K get secret grmp-token >/dev/null 2>&1 || K create secret generic grmp-token --from-literal=GRMP_AUTH_TOKEN="e2e-shared-token" >/dev/null
kubectl kustomize "$HERE/k8s/local" | "$PY" -c 'import sys, yaml
docs = [d for d in yaml.safe_load_all(sys.stdin) if d and d.get("kind") == "NetworkPolicy"
        and d["metadata"]["name"] in ("sandbox-policy", "vectordb-policy")]
print(yaml.safe_dump_all(docs))' | K apply -f - >/dev/null
check "sandbox-policy 已就位" K get networkpolicy sandbox-policy -o name
K delete pod -l role=sandbox --wait=true >/dev/null 2>&1

# ---- 1. 预建 ----------------------------------------------------------------------
echo "--- 1. 预建 $SIZE 个"
t0=$(date +%s); SIM up >/dev/null
K wait --for=condition=Ready pod -l role=sandbox --timeout=240s >/dev/null
n=$(K get pods -l role=sandbox --no-headers | wc -l | tr -d ' ')
check "预建 $SIZE 个全部就绪($(( $(date +%s) - t0 )) 秒)" test "$n" = "$SIZE"
IDLE=$(any_sidecar)
check "空闲沙箱的 4096 不监听(opencode 没启动)" test "$(main_sh "$IDLE" 'curl -s -o /dev/null -w %{http_code} -m 3 127.0.0.1:4096/global/health')" = "000"

# ---- 2. 主容器里能看到什么(§9.1 在真镜像上复验) ------------------------------------
echo "--- 2. 隔离(在主容器里执行,等同模型的命令行)"
check "ls /nas 只有 kb" test "$(main_sh "$IDLE" 'ls /nas')" = "kb"
check "/pool 不存在" sh -c "! kubectl --context $C -n $NS exec $IDLE -c sandbox -- ls /pool >/dev/null 2>&1"
check "不带密钥调 127.0.0.1:4098 → 401" test "$(main_sh "$IDLE" 'curl -s -o /dev/null -w %{http_code} 127.0.0.1:4098/sandbox/status')" = "401"
check "写指令目录 → 只读" sh -c "kubectl --context $C -n $NS exec $IDLE -c sandbox -- sh -c 'touch /run/sandbox/x' 2>&1 | grep -q 'Read-only'"
check "/proc 里找不到控制密钥" sh -c "! kubectl --context $C -n $NS exec $IDLE -c sandbox -- sh -c 'grep -l SANDBOX_CONTROL_KEY /proc/*/environ 2>/dev/null' | grep -q ."
check "没有 ServiceAccount 令牌" sh -c "! kubectl --context $C -n $NS exec $IDLE -c sandbox -- ls /var/run/secrets/kubernetes.io/serviceaccount >/dev/null 2>&1"
check "看不到副容器的进程" sh -c "! kubectl --context $C -n $NS exec $IDLE -c sandbox -- sh -c 'cat /proc/*/cmdline 2>/dev/null | tr \"\\0\" \" \"' | grep -q pool-serve"

# ---- 3. 登录:$SIZE 人秒级绑定,第 $((SIZE+1)) 人现建 -------------------------------------
echo "--- 3. 登录"
t0=$(date +%s); fails=0
for i in $(seq 1 "$SIZE"); do
  out=$(SIM login "$(U "$i")"); [ "$(echo "$out" | J reused)" = "False" ] || [ "$(echo "$out" | J reused)" = "false" ] || { fails=$((fails+1)); echo "    $out"; }
done
check "$SIZE 人全部绑定成功($(( $(date +%s) - t0 )) 秒,含每次 port-forward 开销)" test "$fails" = 0
t0=$(date +%s); out=$(SIM login "$(U 99)")
check "第 $((SIZE+1)) 人触发现建并绑定($(( $(date +%s) - t0 )) 秒)" test -n "$(echo "$out" | J sandbox)"
n=$(K get pods -l role=sandbox --no-headers | grep -vc Terminating)
check "总数 $((SIZE+1))" test "$n" = "$((SIZE+1))"
check "4097 报告端口回绑定的工号" test "$(main_sh "$(pod_of "$(U 7)")" 'curl -s 127.0.0.1:4097/whoami.json')" = "{\"user_id\": \"$(U 7)\"}"

# ---- 4. 同一工号不能同时进两个沙箱 ------------------------------------------------------
echo "--- 4. 独占冲突"
SIM --size $((SIZE+2)) up >/dev/null; sleep 2
K wait --for=condition=Ready pod -l role=sandbox --timeout=180s >/dev/null
spare=$(SIM status | "$PY" -c 'import json,sys
for ln in sys.stdin:
    r = json.loads(ln)
    if r.get("state") == "idle" and not r.get("user"):
        print(r["pod"]); break')
out=$(SIM ctl "$spare" POST /sandbox/bind "{\"user_id\": \"$(U 2)\"}")
check "第二个沙箱 bind 同一工号 → 409 owner_busy" test "$(echo "$out" | J reason)" = "owner_busy"
check "holder 是他正在用的那个沙箱" test "$(echo "$out" | J holder)" = "$(pod_of "$(U 2)")"
check "冲突之后那个沙箱仍是空闲" test "$(SIM ctl "$spare" GET /sandbox/status | J state)" = "idle"

# ---- 5. 对话、埋链接、退出、再登录接着聊 -------------------------------------------------
echo "--- 5. 持久化与跨沙箱接续"
A=$(U 1); PA=$(pod_of "$A")
sid=$(SIM oc "$A" POST /session "{\"title\": \"pool-e2e-$RUN\"}" | J id)
check "建会话" test -n "$sid"
if [ "$MODEL" = 1 ]; then
  SIM oc "$A" POST "/session/$sid/message" '{"parts":[{"type":"text","text":"只回复两个字:收到"}]}' > "$WORK/reply1.json"
  check "真模型回了话(DeepSeek,经 sandbox-policy 出网)" grep -q '"type": "text"' "$WORK/reply1.json"
fi
main_sh "$PA" "echo hello > /data/state/workspace/note.txt
  ln -s /pool/users/$(U 2)/xdg-data/opencode/opencode.db /data/state/workspace/steal.db
  ln -s /pool/users /data/state/workspace/allusers
  ln -s /proc/self/environ /data/state/workspace/env"
out=$(SIM logout "$A")
check "点退出 → 200,已退役" test "$(echo "$out" | J http)-$(echo "$out" | J state)" = "200-retired"
nas "import os,sys
u='/pool/users/$A'
ok = os.path.isfile(u+'/xdg-data/opencode/opencode.db') and os.path.isfile(u+'/workspace/note.txt')
leak = [n for n in ('steal.db','allusers','env') if os.path.lexists(u+'/workspace/'+n)]
clean = not os.path.lexists(u+'/.state-dirty') and not os.path.lexists(u+'/.owner')
backups = [n for n in os.listdir(u+'/backup') if n.startswith('opencode.db.')] if os.path.isdir(u+'/backup') else []
print(ok, leak, clean, len(backups))" > "$WORK/nas5"
check "NAS 有库与工作文件" grep -q '^True ' "$WORK/nas5"
check "埋的三条链接一条都没搬上 NAS" grep -q ' \[\] ' "$WORK/nas5"
check "dirty 已清、独占已释放、有一份退出备份" grep -q ' True 1$' "$WORK/nas5"
out=$(SIM login "$A")
check "再登录分到另一个沙箱" sh -c "test '$(echo "$out" | J sandbox)' != '$PA'"
SIM oc "$A" GET /session > "$WORK/sessions5.json"
check "旧会话在" grep -q "pool-e2e-$RUN" "$WORK/sessions5.json"
check "工作文件在" test "$(main_sh "$(pod_of "$A")" 'cat /data/state/workspace/note.txt')" = "hello"
if [ "$MODEL" = 1 ]; then
  SIM oc "$A" POST "/session/$sid/message" '{"parts":[{"type":"text","text":"我上一句让你回复什么?只回那两个字"}]}' > "$WORK/reply2.json"
  check "在新沙箱里接着聊(上下文还在)" grep -q '收到' "$WORK/reply2.json"
fi

# ---- 6. 关浏览器:倒计时到点持久化 -----------------------------------------------------
echo "--- 6. 倒计时"
B=$(U 3); SIM close "$B" --countdown 3 >/dev/null; sleep 4
out=$(SIM tick)
check "到点按 timeout 解绑成功" sh -c "echo '$out' | grep -q '\"$B\", 200'"

# ---- 8. 平台不调 unbind,直接正常删 Pod(停止握手) -----------------------------------------
echo "--- 8. 直接删 Pod"
E=$(U 5); PE=$(pod_of "$E")
SIM oc "$E" POST /session "{\"title\": \"lastwords-$RUN\"}" >/dev/null
K delete pod "$PE" --wait=true >/dev/null
nas "import os; u='/pool/users/$E'; print(os.path.lexists(u+'/.state-dirty'), os.path.lexists(u+'/.owner'))" > "$WORK/nas8"
check "收尾干净:dirty 已清、独占已释放" grep -q '^False False$' "$WORK/nas8"
SIM login "$E" >/dev/null
SIM oc "$E" GET /session > "$WORK/sessions8.json"
check "删 Pod 前最后建的会话在 NAS 上" grep -q "lastwords-$RUN" "$WORK/sessions8.json"

# ---- 9. 主容器崩溃重启(Pod 还在) -------------------------------------------------------
echo "--- 9. 主容器崩溃"
F=$(U 6); PF=$(pod_of "$F")
cid=$(K get pod "$PF" -o jsonpath='{.status.containerStatuses[?(@.name=="sandbox")].containerID}' | sed 's#docker://##')
docker kill --signal KILL "$cid" >/dev/null 2>&1
for _ in $(seq 1 60); do
  rc=$(K get pod "$PF" -o jsonpath='{.status.containerStatuses[?(@.name=="sandbox")].restartCount}')
  [ "${rc:-0}" -ge 1 ] && SIM oc "$F" GET /global/health >/dev/null && break; sleep 2
done
check "主容器重启后 opencode 按 ctl 指令重新拉起(restartCount=${rc:-0})" SIM oc "$F" GET /global/health
check "副容器没跟着重启" test "$(K get pod "$PF" -o jsonpath='{.status.containerStatuses[?(@.name=="poolctl")].restartCount}')" = "0"

# ---- 10. 定期回写 -----------------------------------------------------------------------
echo "--- 10. 定期回写"
G=$(U 8); PG=$(pod_of "$G")
main_sh "$PG" "echo periodic > /data/state/workspace/periodic.txt"
main_sh "$(pod_of "$(U 4)")" "echo marker > /data/state/workspace/marker.txt"     # 给第 12 步:被杀之前已回写过的内容
echo "    等一个回写周期(60 秒)"; sleep 70
st=$(SIM ctl "$PG" GET /sandbox/status)
check "last_sync 有值且快照已确认" test "$(echo "$st" | J last_sync.snapshot_confirmed)" = "True"
check "使用中的文件已在 NAS" sh -c "kubectl --context $C -n $NS exec $PG -c poolctl -- cat /pool/users/$G/workspace/periodic.txt | grep -q periodic"

# ---- 11. 网络策略 ------------------------------------------------------------------------
echo "--- 11. 网络策略(入站只放行平台)"
IP=$(K get pod "$(pod_of "$(U 9)")" -o jsonpath='{.status.podIP}')
K run "np-ok-$RUN" --image="$IMG" --image-pull-policy=IfNotPresent --restart=Never --labels=gaussdb-agent/platform=true \
  --command -- curl -s -m 5 "http://$IP:4098/healthz" >/dev/null
K run "np-no-$RUN" --image="$IMG" --image-pull-policy=IfNotPresent --restart=Never \
  --command -- sh -c "curl -s -m 5 http://$IP:4098/healthz || echo BLOCKED" >/dev/null
sleep 12
check "带平台标签的 Pod 能到 4098" sh -c "kubectl --context $C -n $NS logs np-ok-$RUN | grep -q '\"ok\": true'"
check "不带标签的 Pod 到不了" sh -c "kubectl --context $C -n $NS logs np-no-$RUN | grep -q BLOCKED"
check "沙箱之间互相到不了" sh -c "kubectl --context $C -n $NS exec $(pod_of "$(U 10)") -c sandbox -- sh -c 'curl -s -m 5 http://$IP:4096/global/health || echo BLOCKED' | grep -q BLOCKED"
K delete pod "np-ok-$RUN" "np-no-$RUN" --wait=false >/dev/null 2>&1

# ---- 12. 强杀:下次绑定 unclean_last_time(放在定期回写之后:NAS 上要先有东西可快照) ----------------------------------------------
echo "--- 12. 强杀整个沙箱"
D=$(U 4); PD=$(pod_of "$D")
# 只 `delete --force --grace-period=0` 模拟不出来:容器照样收到停止信号,副容器在那一两秒里就收尾干净了
# (10-14 第一轮实测)。先在节点侧用 SIGSTOP 冻住副容器(捕获不了),它就到死都没机会收尾——等同 OOM / 节点故障。
cid=$(K get pod "$PD" -o jsonpath='{.status.containerStatuses[?(@.name=="poolctl")].containerID}' | sed 's#docker://##')
docker kill --signal STOP "$cid" >/dev/null 2>&1
K delete pod "$PD" --force --grace-period=0 >/dev/null 2>&1; sleep 3
out=$(SIM login "$D")
check "标记未过期时 → owner_busy 带 retry_after" test "$(echo "$out" | J error)" = "owner_busy"
ra=$(echo "$out" | J detail.retry_after); echo "    等 ${ra:-0} 秒让独占标记过期"; sleep $(( ${ra:-0} + 2 ))
out=$(SIM login "$D")
check "过期后绑定成功,且报 unclean_last_time" test "$(echo "$out" | J loaded.unclean_last_time)" = "True"
nas "import glob; print(glob.glob('/pool/users/$D/.unclean-*/workspace/marker.txt'))" > "$WORK/nas12"
check "NAS 上留了非正常退出快照,里面有被杀之前回写过的文件" grep -q "marker.txt" "$WORK/nas12"

# ---- 收尾 -------------------------------------------------------------------------------
echo "--- 收尾"
if [ "$KEEP" = 1 ]; then
  echo "    KEEP=1:保留现场。状态文件 $STATE;清理:SIM down 后删 NAS 上 users/$RUN-*"
else
  # 先让所有人正常下线(解绑要写本人 NAS 目录——先删目录会让收尾失败,10-14 第一轮就这么残留了沙箱),
  # 再借一个临时空闲沙箱的副容器删本轮工号的目录(只有副容器挂 users/),最后把它也删掉。
  SIM down >/dev/null; echo "    全部下线,沙箱 Pod 已删除"
  SIM --size 1 up >/dev/null; sleep 2; K wait --for=condition=Ready pod -l role=sandbox --timeout=120s >/dev/null
  nas "import shutil,glob
for p in glob.glob('/pool/users/$RUN-*'): shutil.rmtree(p)" && echo "    已删 NAS 上 users/$RUN-*"
  SIM down >/dev/null
  left=$(K get pods -l role=sandbox --no-headers 2>/dev/null | grep -vc Terminating)
  check "收尾后没有残留的沙箱" test "$left" = 0
fi
echo "=== 通过 $PASS,失败 $FAIL(模拟器日志 $WORK/sim.log)"
[ "$FAIL" = 0 ]
