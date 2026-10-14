#!/usr/bin/env bash
# 一键诊断 / 一键修复:用户对话页报「Cannot connect to API」、用户容器反复重启、
# 登录数据库连不上中间件 —— 这几类都是部署配置问题,脚本逐项查清,并修能自动修的。
#
#   bash runtime-doctor.sh <工号>                           只诊断,不改任何配置
#   bash runtime-doctor.sh <工号> --fix                     诊断后修复能自动修的项,再复查一遍
#   bash runtime-doctor.sh <工号> --fix --grmp 10.1.2.3:8080 顺带把中间件地址改成实际值
#
# 查五样:
#   ① 配置:模型地址/模型名/key 填了没有;中间件地址是不是还停在 localhost 或样例值
#   ② NAS:个人目录 users/<工号> 在不在、属主是不是 1000(不是的话容器写不进、一启动就退出)
#   ③ 用户容器:在不在、就绪没有、重启了几次、上一次为什么退出
#   ④ 出网:用两个临时 Pod 实际连一次模型与中间件 —— 一个带用户容器的标签(受出网策略约束),
#      一个不带(不受约束)。前者不通后者通 = 出网策略挡的;两个都不通 = 集群本身出不去
#   ⑤ 出网策略里模型、中间件两条规则现在放行的地址段
# --fix 做的事(每一条都打印改了什么):
#   · 出网策略挡了模型 → runtime-policy / kb-import-policy 的模型规则放宽到 --model-cidr(默认 0.0.0.0/0)
#   · 个人目录缺失或属主不对 → 用一个临时 root Pod 建好目录、属主改成 1000
#   · 给了 --grmp → 改 agent-config 的中间件地址,出网策略的中间件规则改成这个地址与端口
#   · 改了目录或中间件地址 → 重启这个用户的容器,让它重新读取
# 诊断会建几个临时 Pod(名字以 doctor- 开头),结束时自动删掉。
#
# 只依赖 bash 与 kubectl(Windows Git Bash / Linux / macOS 都能跑),不需要 python、jq。
# 可选环境变量:NS(命名空间,默认 gaussdb-agent)、CONTEXT(kubectl 上下文)。
set -u
export MSYS_NO_PATHCONV=1      # Git Bash:别把补丁里的 /spec/... 改写成 Windows 路径

NS=${NS:-gaussdb-agent}
POD_WAIT=90                    # 临时 Pod 最多等多久(秒)
READY_WAIT=180                 # 修复后等用户容器就绪最多多久(秒)
U=""; FIX=0; GRMP=""; MODEL_CIDR="0.0.0.0/0"

usage() {
  echo "用法:bash runtime-doctor.sh <工号> [--fix] [--grmp IP:端口] [--model-cidr 地址段]"
  echo "  不带 --fix 只诊断;--fix 修复能自动修的项;--grmp 要和 --fix 一起用"
}
while [ $# -gt 0 ]; do
  case "$1" in
    --fix) FIX=1 ;;
    --grmp) GRMP=${2:-}; shift ;;
    --model-cidr) MODEL_CIDR=${2:-}; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "不认识的参数:$1" >&2; usage >&2; exit 2 ;;
    *) U=$(printf '%s' "$1" | tr 'A-Z' 'a-z') ;;
  esac
  shift
done
IPV4='([0-9]{1,3}\.){3}[0-9]{1,3}'
if ! printf '%s' "$U" | grep -Eq '^[a-z0-9]([a-z0-9-]{0,52}[a-z0-9])?$'; then usage >&2; exit 2; fi
if [ -n "$GRMP" ]; then
  [ "$FIX" = 1 ] || { echo "--grmp 要和 --fix 一起用" >&2; exit 2; }
  printf '%s' "$GRMP" | grep -Eq "^${IPV4}:[0-9]{1,5}\$" \
    || { echo "--grmp 要写成 IP:端口,例如 10.1.2.3:8080(出网策略只能按 IP 放行,不能写域名)" >&2; exit 2; }
fi
printf '%s' "$MODEL_CIDR" | grep -Eq "^${IPV4}/[0-9]{1,2}\$" \
  || { echo "--model-cidr 要写成地址段,例如 0.0.0.0/0 或 10.20.0.0/16" >&2; exit 2; }
command -v kubectl >/dev/null 2>&1 || { echo "找不到 kubectl,请在能执行 kubectl 的机器上运行" >&2; exit 2; }

K() { kubectl ${CONTEXT:+--context "$CONTEXT"} -n "$NS" "$@"; }
G() { K get "$@" 2>/dev/null | tr -d '\r'; }          # 取值;取不到就是空串
ok()   { echo "  [正常] $*"; }
bad()  { echo "  [问题] $*"; }
note() { echo "         $*"; }
SUMMARY=""
remember() { SUMMARY="${SUMMARY}  - $*
"; }

K get configmap agent-config >/dev/null 2>&1 \
  || { echo "读不到命名空间 ${NS} 里的 agent-config:kubectl 连不上集群,或命名空间不对(可用 NS=... 指定)" >&2; exit 2; }

D=runtime-$U
RUN=$(date +%s | tail -c 7)    # 本次运行的编号:临时 Pod 都打上它,退出时按它一起删
PASS=0
cleanup() { K delete pod -l "doctor-run=$RUN" --ignore-not-found --wait=false >/dev/null 2>&1; }
trap cleanup EXIT

# 临时 Pod 的清单。$1 名字 $2 uid $3 额外标签 $4 NAS 挂载 none|ro|rw $5 环境变量(flow 列表) $6 脚本
pod_yaml() {
  local mounts="" vols="" ro=true
  if [ "$4" != none ]; then
    [ "$4" = rw ] && ro=false
    mounts="    volumeMounts: [{name: nas, mountPath: /nas, readOnly: ${ro}}]"
    vols="  volumes: [{name: nas, persistentVolumeClaim: {claimName: nas}}]"
  fi
  cat <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: $1
  namespace: ${NS}
  labels: {doctor-run: "${RUN}", $3}
spec:
  restartPolicy: Never
  enableServiceLinks: false
  terminationGracePeriodSeconds: 0
  securityContext: {runAsUser: $2}
  containers:
  - name: c
    image: ${IMG}
    imagePullPolicy: IfNotPresent
    env: $5
    command: ["sh", "-c"]
    args:
    - |
$(printf '%s\n' "$6" | sed 's/^/      /')
${mounts}
${vols}
EOF
}
start_pod() {
  local err
  err=$(pod_yaml "$@" | K apply -f - 2>&1 >/dev/null) || bad "建临时 Pod $1 失败:${err}"
}
# 等临时 Pod 跑完并打印它的输出;起不来就打印它的事件(比如 NAS 挂不上)
wait_pod() {
  local i=0 phase
  while [ $i -lt $POD_WAIT ]; do
    phase=$(G pod "$1" -o jsonpath='{.status.phase}')
    case "$phase" in
      Succeeded|Failed) K logs "$1" 2>/dev/null | tr -d '\r'; return 0 ;;
      "") [ -n "$(G pod "$1" -o name)" ] || { echo "TIMEOUT"; echo "EVENT 临时 Pod 没建出来(见上面的报错)"; return 1; } ;;
    esac
    sleep 2; i=$((i + 2))
  done
  echo "TIMEOUT"
  K get events --field-selector "involvedObject.name=$1" \
    -o jsonpath='{range .items[*]}{.reason}: {.message}{"\n"}{end}' 2>/dev/null | tr -d '\r' | grep -v '^[[:space:]]*$' | tail -4 | sed 's/^/EVENT /'
  return 1
}
field() { printf '%s\n' "$2" | awk -v t="$1" '$1 == t { $1 = ""; sub(/^ /, ""); print; exit }'; }

# 出网策略:找第一条「放行这个端口、且按地址段放行」的规则下标
rule_index() {
  local i=0 ports cidrs
  while IFS='|' read -r ports cidrs; do
    case " $ports " in *" $2 "*) [ -n "$cidrs" ] && { echo "$i"; return 0; } ;; esac
    i=$((i + 1))
  done <<EOF
$(G networkpolicy "$1" -o jsonpath='{range .spec.egress[*]}{.ports[*].port}{"|"}{.to[*].ipBlock.cidr}{"\n"}{end}')
EOF
  return 1
}
rule_cidrs() { G networkpolicy "$1" -o jsonpath="{.spec.egress[$2].to[*].ipBlock.cidr}"; }
# 改一条规则;带 test:改之前那条规则被别人动过,就拒绝而不是覆盖
patch_rule() {  # $1 策略 $2 下标 $3 新的 to(JSON) [$4 新的 ports(JSON)]
  local cur ops
  cur=$(G networkpolicy "$1" -o jsonpath="{.spec.egress[$2].to}")
  [ -n "$cur" ] || return 1
  ops="{\"op\":\"test\",\"path\":\"/spec/egress/$2/to\",\"value\":${cur}},{\"op\":\"replace\",\"path\":\"/spec/egress/$2/to\",\"value\":$3}"
  [ -n "${4:-}" ] && ops="${ops},{\"op\":\"replace\",\"path\":\"/spec/egress/$2/ports\",\"value\":$4}"
  K patch networkpolicy "$1" --type json -p "[${ops}]" >/dev/null
}
# curl 退出码 → 人话
say_rc() {
  case "$1" in
    0) echo "通(HTTP $2)" ;;
    6) echo "域名解析失败" ;;
    7) echo "连接被拒绝" ;;
    28) echo "连接超时" ;;
    35|60) echo "TLS 握手失败(可能被代理拦截)" ;;
    52|56) echo "端口通,但对方没按 HTTP 回应" ;;
    *) echo "失败(curl 退出码 $1)" ;;
  esac
}
reachable() { case "$1" in 0|52|56) return 0 ;; *) return 1 ;; esac; }

# 先等 8 秒:出网策略对新 Pod 要过一会儿才生效(OrbStack 实测约 1 秒,其他网络插件可能更久),
# 抢在生效前测,受约束的 Pod 也会「连得上」—— 第一版就因此把被挡的模型误报成通的。
PROBE_SH='sleep 8
r=$(curl -sS -m 10 -o /dev/null -w "%{http_code}" "$MODEL_URL" 2>&1); echo "MODEL $? $r"
if [ -n "$GRMP_URL" ]; then r=$(curl -sS -m 6 -o /dev/null -w "%{http_code}" "$GRMP_URL" 2>&1); echo "GRMP $? $r"; fi'
NAS_SH='for d in $DIRS; do
  if [ -d "/nas/$d" ]; then echo "DIR $d $(stat -c %u "/nas/$d")"; else echo "DIR $d missing"; fi
done'
FIX_SH='set -e
for d in $DIRS; do mkdir -p "/nas/$d"; chown 1000:1000 "/nas/$d"; chmod 775 "/nas/$d"; echo "DIR $d $(stat -c %u "/nas/$d")"; done'

user_dirs() { if [ -n "$(G deploy "kb-import-$U" -o name)" ]; then echo "users/$U admins/$U"; else echo "users/$U"; fi; }

check_all() {
  MODEL_BLOCKED=0; GRMP_BLOCKED=0; GRMP_BAD=0; NEED_DIR=0; SUMMARY=""
  PASS=$((PASS + 1))
  local p_nas=doctor-nas-$RUN-$PASS p_rt=doctor-rt-$RUN-$PASS p_free=doctor-free-$RUN-$PASS

  echo "== ① 配置 =="
  MODEL_URL=$(G configmap agent-config -o jsonpath='{.data.MODEL_BASE_URL}'); MODEL_URL=${MODEL_URL%/}
  MODEL_ID=$(G configmap agent-config -o jsonpath='{.data.MODEL_ID}')
  GH=$(G configmap agent-config -o jsonpath='{.data.GRMP_API_HOST}')
  GP=$(G configmap agent-config -o jsonpath='{.data.GRMP_API_PORT}')
  local keylen; keylen=$(G secret model-api -o jsonpath='{.data.MODEL_API_KEY}' | wc -c | tr -d ' ')
  if ! printf '%s' "$MODEL_URL" | grep -Eq '^https?://[A-Za-z0-9._:/-]+$'; then
    bad "模型地址 MODEL_BASE_URL 没填或格式不对:「${MODEL_URL}」"; remember "改 agent-config 的 MODEL_BASE_URL(需人工)"
  elif printf '%s' "$MODEL_URL" | grep -q 'example\.internal'; then
    bad "模型地址还是样例值 ${MODEL_URL}"; remember "改 agent-config 的 MODEL_BASE_URL(需人工)"
  else ok "模型地址 ${MODEL_URL},模型 ${MODEL_ID:-（未填）}"; fi
  [ -n "$MODEL_ID" ] || { bad "模型名 MODEL_ID 没填"; remember "改 agent-config 的 MODEL_ID(需人工)"; }
  if [ "${keylen:-0}" -gt 1 ]; then ok "模型 key 已配置(Secret model-api,不打印内容)"
  else bad "Secret model-api 里没有 MODEL_API_KEY"; remember "建 Secret model-api(需人工)"; fi
  case "$GH" in
    ""|localhost|127.*|*example.internal)
      GRMP_BAD=1
      bad "中间件地址是「${GH:-（空）}:${GP}」—— 容器里的 localhost 指容器自己,登录数据库会连不上"
      remember "中间件地址要改成实际值:加 --fix --grmp <中间件IP:端口> 重跑" ;;
    *) ok "中间件地址 ${GH}:${GP}" ;;
  esac

  # 三个临时 Pod 一起起,下面按顺序取结果
  local dirs grmp_url=""; dirs=$(user_dirs)
  [ "$GRMP_BAD" = 0 ] && grmp_url="http://$GH:$GP/"
  local probe_env="[{name: MODEL_URL, value: \"$MODEL_URL/models\"}, {name: GRMP_URL, value: \"$grmp_url\"}]"
  if [ -n "$IMG" ]; then
    start_pod "$p_nas" 0 "app: gaussdb-agent-doctor" ro "[{name: DIRS, value: \"$dirs\"}]" "$NAS_SH"
    start_pod "$p_rt" 1000 "app: gaussdb-agent, role: runtime, user: doctor-probe" none "$probe_env" "$PROBE_SH"
    start_pod "$p_free" 1000 "app: gaussdb-agent-doctor" none "$probe_env" "$PROBE_SH"
  fi

  echo "== ② NAS 个人目录 =="
  if [ -z "$IMG" ]; then
    bad "找不到 runtime 镜像名,跳过 ②④(先让这个工号登录一次,或检查 gateway-config 的 USER_IMAGE_TAG)"
    remember "找不到 runtime 镜像名(需人工)"
  else
    local nasout d owner
    nasout=$(wait_pod "$p_nas")
    if printf '%s' "$nasout" | grep -q '^TIMEOUT'; then
      bad "挂 NAS 的临时 Pod 起不来 —— NAS 本身挂不上(用户容器也会卡住):"
      printf '%s\n' "$nasout" | grep '^EVENT' | sed 's/^EVENT /         /'
      remember "NAS 挂不上:按上面的事件查 PV 里的 NFS 地址与导出路径(部署手册 §2.2)(需人工)"
    else
      for d in $dirs; do
        owner=$(printf '%s\n' "$nasout" | awk -v d="$d" '$1 == "DIR" && $2 == d { print $3 }')
        case "$owner" in
          1000) ok "$d 存在,属主 1000" ;;
          missing) NEED_DIR=1; bad "$d 还没建:首次登录时会被自动建成 root 属主,容器写不进"
                   remember "建好 $d 并把属主改成 1000(--fix 可自动修)" ;;
          "") NEED_DIR=1; bad "$d 查不到状态:$(printf '%s' "$nasout" | tail -1)"
              remember "检查 $d(--fix 会重建并改属主)" ;;
          *) NEED_DIR=1; bad "$d 属主是 uid ${owner},容器以 1000 运行,写不进(容器会一启动就退出)"
             remember "把 $d 属主改成 1000(--fix 可自动修)" ;;
        esac
      done
    fi
  fi

  echo "== ③ 用户容器 ${D} =="
  runtime_status

  echo "== ④ 出网实测(从集群里连) =="
  if [ -n "$IMG" ]; then
    local rt free
    rt=$(wait_pod "$p_rt"); free=$(wait_pod "$p_free")
    probe_verdict "模型服务" MODEL "$rt" "$free" "$MODEL_URL"
    if [ "$GRMP_BAD" = 1 ]; then note "中间件:地址是 localhost 或样例值,不测"
    else probe_verdict "中间件" GRMP "$rt" "$free" "http://$GH:$GP"; fi
  fi

  echo "== ⑤ 出网策略 runtime-policy 现状 =="
  if [ -z "$(G networkpolicy runtime-policy -o name)" ]; then
    note "没有 runtime-policy:用户容器出网不受限"
  else
    local i
    if i=$(rule_index runtime-policy 443); then note "模型服务规则(第 $i 条)放行 $(rule_cidrs runtime-policy "$i") 的 443"
    else note "没找到放行 443 的规则(模型服务)"; fi
    if i=$(rule_index runtime-policy "${GP:-8080}"); then note "中间件规则(第 $i 条)放行 $(rule_cidrs runtime-policy "$i") 的 ${GP:-8080}"
    else note "没找到放行端口 ${GP:-8080} 的规则(中间件)"; fi
  fi
}

# $1 名称 $2 标记 $3 受约束 Pod 的输出 $4 不受约束 Pod 的输出 $5 目标
probe_verdict() {
  local a b rc_a rc_b
  a=$(field "$2" "$3"); b=$(field "$2" "$4")
  rc_a=${a%% *}; rc_b=${b%% *}
  if [ -z "$a" ]; then
    bad "$1:临时 Pod 没跑出结果 $(printf '%s\n' "$3" | grep '^EVENT' | head -1 | sed 's/^EVENT //')"
    remember "${1}:出网实测没跑成,把本输出发给我方"; return
  fi
  if reachable "$rc_a"; then
    ok "$1 $5:用户容器能连上,$(say_rc "$rc_a" "${a#* }")"
    [ "$2" = MODEL ] && [ "${a#* }" = 401 ] && note "401 是因为探测没带 key,网络是通的"
  elif [ -z "$b" ]; then
    bad "$1 $5:用户容器 $(say_rc "$rc_a");对照用的 Pod 没跑出结果,分不清是不是出网策略挡的"
    remember "${1}连不上(原因未定),把本输出发给我方"
  elif reachable "$rc_b"; then
    bad "$1 $5:用户容器 $(say_rc "$rc_a"),不受出网策略约束的 Pod 却 $(say_rc "$rc_b" "${b#* }") → 被出网策略挡了"
    if [ "$2" = MODEL ]; then MODEL_BLOCKED=1; remember "放行模型服务出网(--fix 可自动修,放行 ${MODEL_CIDR} 的 443/80)"
    else GRMP_BLOCKED=1; remember "放行中间件出网(--fix 可自动修;地址不是 IP 时用 --fix --grmp IP:端口)"; fi
  else
    bad "$1 $5:用户容器 $(say_rc "$rc_a"),不受约束的 Pod 也 $(say_rc "$rc_b") → 集群本身连不上,不是出网策略的问题"
    remember "${1}连不上:检查集群节点到 $5 的网络(DNS、路由、是否要走代理)(需人工)"
  fi
}

runtime_status() {
  RT_REP=$(G deploy "$D" -o jsonpath='{.spec.replicas}')
  if [ -z "$RT_REP" ]; then note "还没有 ${D}:这个工号还没登录过,首次登录时网关会创建"; return; fi
  if [ "$RT_REP" = 0 ]; then note "副本数 0:闲置被回收了,下次登录自动拉起(正常)"; return; fi
  local ready line pod restarts last waiting logs
  ready=$(G deploy "$D" -o jsonpath='{.status.readyReplicas}')
  line=$(G pod -l "role=runtime,user=$U" -o jsonpath='{range .items[*]}{.metadata.name} {.status.containerStatuses[0].restartCount} {.status.containerStatuses[0].lastState.terminated.reason}:{.status.containerStatuses[0].lastState.terminated.exitCode} {.status.containerStatuses[0].state.waiting.reason}{"\n"}{end}' | head -1)
  set -- $line
  pod=${1:-}; restarts=${2:-0}; last=${3:-:}; waiting=${4:-}
  [ "$last" = ":" ] && last="无"
  if [ "${ready:-0}" -ge 1 ]; then ok "${D} 就绪(历史重启 ${restarts} 次)"; return; fi
  bad "${D} 没就绪:重启 ${restarts} 次,上次退出 ${last},当前 ${waiting:-运行中}"
  remember "${D} 没就绪:先修本输出里的其他问题;都正常了还起不来,把本输出发给我方"
  [ -n "$pod" ] || return
  logs=$(K logs "$pod" --previous --tail=15 2>/dev/null | tr -d '\r')
  [ -n "$logs" ] || logs=$(K logs "$pod" --tail=15 2>/dev/null | tr -d '\r')
  if printf '%s' "$logs" | grep -q 'Permission denied'; then
    note "日志里有 Permission denied:个人目录写不进(见 ②)"
  elif printf '%s' "$logs" | grep -q '独占标记'; then
    note "日志停在「独占标记」:上一个容器还占着这个人的目录,90 秒内自动过期"
  fi
  if [ -n "$logs" ]; then note "最近的日志:"; printf '%s\n' "$logs" | tail -8 | sed 's/^/           | /'; fi
  K get events --field-selector "involvedObject.name=$pod" \
    -o jsonpath='{range .items[*]}{.reason}: {.message}{"\n"}{end}' 2>/dev/null | tr -d '\r' \
    | grep -E 'Failed|BackOff|Unhealthy' | tail -3 | sed 's/^/         事件 /'
}

IMG=$(G deploy "$D" -o jsonpath='{.spec.template.spec.containers[0].image}')
[ -n "$IMG" ] || IMG=$(G deploy -l role=runtime -o jsonpath='{.items[0].spec.template.spec.containers[0].image}')
if [ -z "$IMG" ]; then
  TAG=$(G configmap gateway-config -o jsonpath='{.data.USER_IMAGE_TAG}')
  [ -n "$TAG" ] && IMG="gaussdb-agent-runtime:${TAG}"
fi

echo "诊断工号 ${U}(命名空间 ${NS})…… 约 30 秒到 1 分钟"
check_all

if [ "$FIX" = 1 ]; then
  echo
  echo "== 修复 =="
  RESTART=0; CHANGED=0
  if [ "$MODEL_BLOCKED" = 1 ]; then
    for pol in runtime-policy kb-import-policy; do
      [ -n "$(G networkpolicy "$pol" -o name)" ] || continue
      if i=$(rule_index "$pol" 443) && patch_rule "$pol" "$i" "[{\"ipBlock\":{\"cidr\":\"${MODEL_CIDR}\"}}]"; then
        echo "  [已改] ${pol} 第 $i 条(模型服务)→ 放行 ${MODEL_CIDR} 的 443/80"; CHANGED=1
      else echo "  [失败] ${pol}:没找到模型服务规则,或它刚被别人改过 —— 请人工检查"; fi
    done
  fi
  if [ -n "$GRMP" ]; then
    h=${GRMP%:*}; p=${GRMP##*:}
    if i=$(rule_index runtime-policy "${GP:-8080}") \
       && patch_rule runtime-policy "$i" "[{\"ipBlock\":{\"cidr\":\"$h/32\"}}]" "[{\"port\":$p,\"protocol\":\"TCP\"}]"; then
      echo "  [已改] runtime-policy 第 $i 条(中间件)→ 只放行 $h/32 的 $p"; CHANGED=1
    elif [ -n "$(G networkpolicy runtime-policy -o name)" ]; then
      echo "  [失败] runtime-policy 里没找到中间件规则(端口 ${GP:-8080}),请人工添加 $h/32 的 $p"
    fi
    if K patch configmap agent-config --type merge -p "{\"data\":{\"GRMP_API_HOST\":\"$h\",\"GRMP_API_PORT\":\"$p\"}}" >/dev/null; then
      echo "  [已改] agent-config 中间件地址 → $h:$p"; RESTART=1; CHANGED=1
    else echo "  [失败] 改不了 agent-config"; fi
  elif [ "$GRMP_BLOCKED" = 1 ]; then
    if printf '%s' "$GH" | grep -Eq "^${IPV4}\$" && i=$(rule_index runtime-policy "${GP:-8080}") \
       && patch_rule runtime-policy "$i" "[{\"ipBlock\":{\"cidr\":\"$GH/32\"}}]"; then
      echo "  [已改] runtime-policy 第 $i 条(中间件)→ 放行 $GH/32 的 ${GP}"; CHANGED=1
    else echo "  [未改] 中间件地址 $GH 不是 IP,出网策略不能按域名放行:用 --fix --grmp <IP:端口> 重跑"; fi
  fi
  if [ "$NEED_DIR" = 1 ] && [ -n "$IMG" ]; then
    dirs=$(user_dirs); pf=doctor-fix-$RUN
    start_pod "$pf" 0 "app: gaussdb-agent-doctor" rw "[{name: DIRS, value: \"$dirs\"}]" "$FIX_SH"
    out=$(wait_pod "$pf")
    if printf '%s' "$out" | grep -q '^DIR' && ! printf '%s\n' "$out" | awk '$1 == "DIR" && $3 != 1000 { b = 1 } END { exit !b }'; then
      echo "  [已改] 个人目录 $dirs 已建好,属主 1000"; RESTART=1; CHANGED=1
    else
      echo "  [失败] 改不了目录属主(NAS 若开了 root_squash,root 也改不了,要在 NAS 侧给 uid 1000 授权):"
      printf '%s\n' "$out" | tail -3 | sed 's/^/         /'
    fi
  fi
  if [ "$RESTART" = 1 ] && [ "${RT_REP:-0}" != 0 ]; then
    echo "  重启 ${D},让它读取新的配置与目录,最多等 ${READY_WAIT} 秒……"
    K rollout restart deploy "$D" >/dev/null
    if K rollout status deploy "$D" --timeout="${READY_WAIT}s" >/dev/null 2>&1; then echo "  [已恢复] ${D} 就绪"
    else echo "  [未就绪] ${D} 还没起来,见下面的复查"; fi
  elif [ "$RESTART" = 1 ]; then
    echo "  ${D} 当前没有运行,下次登录时会按新的配置与目录拉起"
  fi
  [ "$CHANGED" = 1 ] || echo "  没有可自动修的项"
  if [ -n "$GRMP" ]; then
    others=$(G deploy -l role=runtime -o jsonpath='{range .items[*]}{.metadata.name} {.spec.replicas}{"\n"}{end}' \
             | awk -v d="$D" '$1 != d && $2 > 0' | wc -l | tr -d ' ')
    [ "${others:-0}" -gt 0 ] && echo "  注意:另有 ${others} 个用户容器在运行,仍用旧的中间件地址;需要时执行 kubectl -n ${NS} rollout restart deploy -l role=runtime"
  fi

  echo
  echo "======== 复查 ========"
  check_all
fi

echo
echo "================ 结论 ================"
if [ -z "$SUMMARY" ]; then
  echo "全部正常。对话页刷新后重试即可。"
  exit 0
fi
echo "还有以下问题:"
printf '%s' "$SUMMARY"
if [ "$FIX" = 0 ] && printf '%s' "$SUMMARY" | grep -q -- '--fix 可自动修'; then
  echo "标着「--fix 可自动修」的,执行:bash $0 $U --fix"
fi
exit 1
