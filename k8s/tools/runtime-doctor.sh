#!/usr/bin/env bash
# 一键诊断 / 一键修复:用户对话页报「Cannot connect to API」、用户容器反复重启、
# 登录数据库连不上中间件 —— 这几类都是部署配置问题,脚本逐项查清,给出诊断意见与修复命令,并修能自动修的。
#
#   bash runtime-doctor.sh <工号>                              只诊断,不改任何配置
#   bash runtime-doctor.sh <工号> --fix                        诊断后修复能自动修的项,再复查一遍
#   bash runtime-doctor.sh <工号> --fix --grmp 10.1.2.3:8080    顺带把中间件地址改成实际值
#   bash runtime-doctor.sh <工号> --fix --proxy 10.1.2.9:8080   用户容器经公司代理访问模型服务
#
# 查六样:
#   ① 配置与开通:模型地址/模型名/key;中间件地址是不是 localhost 或样例值;这个工号开通了没有
#   ② NAS:个人目录 users/<工号> 在不在、容器的 uid 1000 能不能写(写不进的话容器一启动就退出)
#   ③ 用户容器:在不在、就绪没有、重启了几次、上一次为什么退出
#   ④ 出网:用两个临时 Pod 实际连一次模型与中间件 —— 一个带用户容器的标签(受出网策略约束),
#      一个不带(不受约束)。按「TCP 连没连上」对比:前者没连上、后者连上了 = 出网策略挡的;
#      两个都没连上 = 集群本身出不去;TCP 连上了但 TLS 失败 = 多半要经公司代理或缺公司根证书
#   ⑤ 出网策略里模型、中间件两条规则现在放行的地址段
#   ⑥ 运行本脚本的这台电脑的代理设置(Windows 系统代理 / 环境变量),用来给出代理相关的修复命令
# 最后的「诊断意见」:每个问题一条,写原因判断与可以尝试的命令(有的话)。
# --fix 做的事(每一条都打印改了什么):
#   · 出网策略挡了模型 → runtime-policy / kb-import-policy 的模型规则放宽到 --model-cidr(默认 0.0.0.0/0)
#   · 给了 --proxy → agent-config 加上 HTTPS_PROXY / NO_PROXY,模型规则改成只放行代理的 IP 与端口
#   · 个人目录缺失或写不进 → 用一个临时 root Pod 建好目录、属主改成 1000
#   · 给了 --grmp → 改 agent-config 的中间件地址,出网策略的中间件规则改成这个地址与端口
#   · 改了目录、代理或中间件地址 → 重启这个用户的容器,让它重新读取
# 诊断会建几个临时 Pod(名字以 doctor- 开头),结束时自动删掉。
#
# 只依赖 bash 与 kubectl(Windows Git Bash / Linux / macOS 都能跑)。
# 可选环境变量:NS(命名空间,默认 gaussdb-agent)、CONTEXT(kubectl 上下文)。
set -u
export MSYS_NO_PATHCONV=1      # Git Bash:别把补丁里的 /spec/... 和 reg 的 /v 改写成 Windows 路径

NS=${NS:-gaussdb-agent}
POD_WAIT=90                    # 临时 Pod 最多等多久(秒)
READY_WAIT=180                 # 修复后等用户容器就绪最多多久(秒)
U=""; FIX=0; GRMP=""; PROXY=""; MODEL_CIDR="0.0.0.0/0"; NPOS=0; POSLIST=""
CMD="bash $0"                  # 诊断意见里给出的命令,沿用这次的调用方式
IPV4='([0-9]{1,3}\.){3}[0-9]{1,3}'

usage() {
  echo "用法:bash runtime-doctor.sh <工号> [--fix] [--grmp IP:端口] [--proxy IP:端口] [--model-cidr 地址段]"
  echo "  例如:bash runtime-doctor.sh u0001        (前面的 bash 是运行脚本的命令,工号只写一个)"
  echo "  不带 --fix 只诊断;--grmp / --proxy 要和 --fix 一起用"
}
parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --fix) FIX=1 ;;
      --grmp) GRMP=${2:-}; shift ;;
      --proxy) PROXY=${2:-}; shift ;;
      --model-cidr) MODEL_CIDR=${2:-}; shift ;;
      -h|--help) usage; exit 0 ;;
      -*) echo "不认识的参数:$1" >&2; usage >&2; exit 2 ;;
      *) NPOS=$((NPOS + 1)); POSLIST="${POSLIST}「$1」"; U=$(printf '%s' "$1" | tr 'A-Z' 'a-z') ;;
    esac
    shift
  done
  # 2026-10 现场:敲成了 ./runtime-doctor.sh bash u0001,第一版把最后一个当工号、悄悄忽略了前一个
  if [ "$NPOS" -gt 1 ]; then
    echo "只能给一个工号,这次收到了 ${POSLIST}。" >&2; usage >&2; exit 2
  fi
  if ! printf '%s' "$U" | grep -Eq '^[a-z0-9]([a-z0-9-]{0,52}[a-z0-9])?$'; then usage >&2; exit 2; fi
  local pair opt val
  for pair in "--grmp:$GRMP" "--proxy:$PROXY"; do
    opt=${pair%%:*}; val=${pair#*:}
    [ -n "$val" ] || continue
    [ "$FIX" = 1 ] || { echo "$opt 要和 --fix 一起用" >&2; exit 2; }
    printf '%s' "$val" | grep -Eq "^${IPV4}:[0-9]{1,5}\$" \
      || { echo "$opt 要写成 IP:端口,例如 10.1.2.3:8080(出网策略只能按 IP 放行,不能写域名)" >&2; exit 2; }
  done
  printf '%s' "$MODEL_CIDR" | grep -Eq "^${IPV4}/[0-9]{1,2}\$" \
    || { echo "--model-cidr 要写成地址段,例如 0.0.0.0/0 或 10.20.0.0/16" >&2; exit 2; }
}

K() { kubectl ${CONTEXT:+--context "$CONTEXT"} -n "$NS" "$@"; }
G() { K get "$@" 2>/dev/null | tr -d '\r'; }          # 取值;取不到就是空串
ok()   { echo "  [正常] $*"; }
bad()  { echo "  [问题] $*"; }
note() { echo "         $*"; }
KN="kubectl -n $NS"            # 诊断意见里的命令前缀

# 诊断意见:$1 问题 $2 原因与意见 $3… 可以尝试的命令(每条一个参数,可以没有)
ADV=""; ADV_N=0
advise() {
  ADV_N=$((ADV_N + 1))
  local t="${ADV_N}. $1
   意见:$2" first=1 c
  shift 2
  for c in "$@"; do
    [ -n "$c" ] || continue
    if [ "$first" = 1 ]; then t="${t}
   可以试:${c}"; first=0
    else t="${t}
          ${c}"; fi
  done
  ADV="${ADV}${t}
"
}


cleanup() { K delete pod -l "doctor-run=$RUN" --ignore-not-found --wait=false >/dev/null 2>&1; }

# 临时 Pod 的清单。$1 名字 $2 uid $3 额外标签 $4 NAS 挂载 none|ro|rw $5 环境变量(flow 列表)
# $6 脚本 $7 是否带上 agent-config 的环境变量(yes 时与真实用户容器一样走 HTTPS_PROXY 等)
pod_yaml() {
  local mounts="" vols="" ro=true envfrom=""
  if [ "$4" != none ]; then
    [ "$4" = rw ] && ro=false
    mounts="    volumeMounts: [{name: nas, mountPath: /nas, readOnly: ${ro}}]"
    vols="  volumes: [{name: nas, persistentVolumeClaim: {claimName: nas}}]"
  fi
  [ "${7:-no}" = yes ] && envfrom="    envFrom: [{configMapRef: {name: agent-config}}]"
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
${envfrom}
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

# 出网策略:找第一条「放行这个端口、且按地址段放行」的规则下标;给了 $3 就只认放行这个地址段的
# (代理端口可能和中间件同是 8080,只按端口找会找到中间件那条)
rule_index() {
  local i=0 ports cidrs
  while IFS='|' read -r ports cidrs; do
    case " $ports " in *" $2 "*)
      [ -n "$cidrs" ] && { [ -z "${3:-}" ] || [ "$cidrs" = "$3" ]; } && { echo "$i"; return 0; } ;; esac
    i=$((i + 1))
  done <<EOF
$(G networkpolicy "$1" -o jsonpath='{range .spec.egress[*]}{.ports[*].port}{"|"}{.to[*].ipBlock.cidr}{"\n"}{end}')
EOF
  return 1
}
rule_cidrs() { G networkpolicy "$1" -o jsonpath="{.spec.egress[$2].to[*].ipBlock.cidr}"; }
rule_ports() { G networkpolicy "$1" -o jsonpath="{.spec.egress[$2].ports[*].port}"; }
# 改一条规则;带 test:改之前那条规则被别人动过,就拒绝而不是覆盖
patch_rule() {  # $1 策略 $2 下标 $3 新的 to(JSON) [$4 新的 ports(JSON)]
  local cur ops
  cur=$(G networkpolicy "$1" -o jsonpath="{.spec.egress[$2].to}")
  [ -n "$cur" ] || return 1
  ops="{\"op\":\"test\",\"path\":\"/spec/egress/$2/to\",\"value\":${cur}},{\"op\":\"replace\",\"path\":\"/spec/egress/$2/to\",\"value\":$3}"
  [ -n "${4:-}" ] && ops="${ops},{\"op\":\"replace\",\"path\":\"/spec/egress/$2/ports\",\"value\":$4}"
  K patch networkpolicy "$1" --type json -p "[${ops}]" >/dev/null
}

# ---- 探测结果的判读 ---------------------------------------------------------------
# 探测输出一行:<标记> <curl 退出码> <HTTP 码> <TCP 连上的次数> <curl 原始报错…>
# **按 TCP 连没连上判断出网策略**(curl 的 num_connects:0 = TCP 没连上;经代理时数的是连上代理)。
# 不能用 time_connect:runtime 镜像的 curl 7.88 在 TLS 失败时把它报成 0(本地实测)。
# 2026-10 现场:受约束的 Pod 连接超时、
# 不受约束的 Pod TLS 握手失败,第一版把两边都算「连不上」,结论成了「集群本身不通、不是策略的问题」——
# 其实是两层叠加:策略挡了 TCP,放开之后 TLS 也还过不去。
tc_ok() { printf '%s' "$1" | grep -q '[1-9]'; }
classify() {  # $1 退出码 $2 num_connects → OK DNS PROXY_DNS REFUSED TCP_TIMEOUT SLOW TLS CERT CUT LINKED OTHER
  case "$1" in 0) echo OK; return ;; 5) echo PROXY_DNS; return ;; 6) echo DNS; return ;; esac
  if tc_ok "$2"; then
    case "$1" in
      51|60|77|83) echo CERT ;;
      35|53|54|58|59|80|90|91) echo TLS ;;
      28) echo SLOW ;;
      52) echo OK ;;
      7|56) echo CUT ;;
      *) echo LINKED ;;
    esac
  else
    case "$1" in 7) echo REFUSED ;; 28) echo TCP_TIMEOUT ;; *) echo OTHER ;; esac
  fi
}
tcp_ok() { case "$1" in OK|CERT|TLS|SLOW|CUT|LINKED) return 0 ;; *) return 1 ;; esac; }
describe() {  # $1 类别 $2 HTTP 码 $3 退出码
  case "$1" in
    OK) echo "通(HTTP $2)" ;;
    DNS) echo "域名解析失败" ;;
    PROXY_DNS) echo "代理地址解析失败" ;;
    REFUSED) echo "连接被拒绝" ;;
    TCP_TIMEOUT) echo "连接超时(TCP 没连上)" ;;
    SLOW) echo "TCP 连上了,之后超时" ;;
    TLS) echo "TCP 连上了,TLS 握手失败" ;;
    CERT) echo "TCP 连上了,证书校验失败" ;;
    CUT) echo "TCP 连上了,随后被对方断开" ;;
    *) echo "失败(curl 退出码 $3)" ;;
  esac
}
# 把一行探测结果拆成 P_RC P_HTTP P_TC P_ERR P_CLS
parse_probe() {
  set -f; set -- $1; set +f      # 报错原文里可能有 * ?,别让它展开成文件名
  P_RC=${1:-}; P_HTTP=${2:-000}; P_TC=${3:-0}; shift 3 2>/dev/null; P_ERR="$*"
  P_CLS=$(classify "$P_RC" "$P_TC")
}

# ---- 运行本脚本的这台电脑的代理 ------------------------------------------------------
# 现场常见:电脑上的程序能连外网(走了公司代理),集群里的容器不行(没有代理设置,直连被拦)。
detect_local_proxy() {
  LP=""; LP_SRC=""; LP_PAC=""
  local p=${HTTPS_PROXY:-${https_proxy:-}}
  if [ -n "$p" ]; then
    LP=$(printf '%s' "$p" | sed -E 's#^[A-Za-z]+://##; s#^[^@]*@##; s#/.*$##'); LP_SRC="本机环境变量 HTTPS_PROXY"; return 0
  fi
  command -v reg >/dev/null 2>&1 || return 0
  local key='HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings' en srv one
  en=$(reg query "$key" /v ProxyEnable 2>/dev/null | tr -d '\r' | awk '/ProxyEnable/ {print $NF}')
  srv=$(reg query "$key" /v ProxyServer 2>/dev/null | tr -d '\r' | awk '/ProxyServer/ {print $NF}')
  LP_PAC=$(reg query "$key" /v AutoConfigURL 2>/dev/null | tr -d '\r' | awk '/AutoConfigURL/ {print $NF}')
  if [ "$en" = "0x1" ] && [ -n "$srv" ]; then
    # 「10.1.2.3:8080」或「http=10.1.2.3:8080;https=10.1.2.3:8080」
    one=$(printf '%s' "$srv" | tr ';' '\n' | awk -F= 'NF == 1 { print; exit } $1 == "https" { h = $2 } $1 == "http" && !x { x = $2 } END { if (h) print h; else if (x) print x }' | head -1)
    LP=${one}; LP_SRC="Windows 系统代理"
  fi
}
# 经代理出网的修复命令与说明(按本机能查到的代理给)
proxy_hint() {
  local h=${LP%:*}
  if [ -n "$LP" ] && printf '%s' "$LP" | grep -Eq "^${IPV4}:[0-9]+\$" && [ "${h#127.}" = "$h" ]; then
    PH_TEXT="这台电脑走的是 ${LP_SRC} ${LP};集群里的容器没有代理设置。可以让用户容器也经这个代理出网(出网策略只放行代理,比放开全网更窄)"
    PH_CMD="${CMD} ${U} --fix --proxy ${LP}"
  elif [ -n "$LP" ] && { [ "${h#127.}" != "$h" ] || [ "$h" = localhost ]; }; then
    PH_TEXT="这台电脑的代理是 ${LP}(只监听本机,多半是本机的代理客户端),集群里的容器连不到它。要向网络管理员要公司代理服务器的 IP 与端口"
    PH_CMD="${CMD} ${U} --fix --proxy <公司代理IP:端口>"
  elif [ -n "$LP" ]; then
    PH_TEXT="这台电脑走的是 ${LP_SRC} ${LP},但出网策略只能按 IP 放行:先查出代理的 IP(nslookup ${h}),再用 IP 执行修复"
    PH_CMD="${CMD} ${U} --fix --proxy <代理IP>:${LP##*:}"
  elif [ -n "$LP_PAC" ]; then
    PH_TEXT="这台电脑用自动配置脚本 ${LP_PAC} 决定走哪个代理:在浏览器打开它,找到访问外网用的 PROXY 地址(IP:端口)"
    PH_CMD="${CMD} ${U} --fix --proxy <代理IP:端口>"
  else
    PH_TEXT="公司出网若要经代理:向网络管理员要 HTTPS 代理的 IP 与端口"
    PH_CMD="${CMD} ${U} --fix --proxy <代理IP:端口>"
  fi
}

# ---- 各项检查 ------------------------------------------------------------------------
# 先等 8 秒:出网策略对新 Pod 要过一会儿才生效(OrbStack 实测约 1 秒,其他网络插件可能更久),
# 抢在生效前测,受约束的 Pod 也会「连得上」—— 第一版就因此把被挡的模型误报成通的。
PROBE_SH='sleep 8
probe() {
  o=$(curl -sS -m "$3" -o /dev/null -w "%{http_code} %{num_connects}" "$2" 2>/tmp/e); rc=$?
  echo "$1 $rc ${o:-000 0} $(tr "\n" " " </tmp/e | cut -c1-180)"
}
probe MODEL "$MODEL_URL" 10
if [ -n "$GRMP_URL" ]; then probe GRMP "$GRMP_URL" 6; fi
echo "VIA ${HTTPS_PROXY:-${https_proxy:-}}"'
# 以容器的 uid 1000 判断能不能写(只查权限,不写):比看属主准 —— 有的存储会映射属主
NAS_SH='for d in $DIRS; do
  if [ -d "/nas/$d" ]; then
    if [ -w "/nas/$d" ]; then w=yes; else w=no; fi
    echo "DIR $d $(stat -c %u "/nas/$d") $w"
  else echo "DIR $d missing -"; fi
done'
FIX_SH='set -e
for d in $DIRS; do mkdir -p "/nas/$d"; chown 1000:1000 "/nas/$d"; chmod 775 "/nas/$d"; echo "DIR $d $(stat -c %u "/nas/$d")"; done'

user_dirs() { if [ -n "$(G deploy "kb-import-$U" -o name)" ]; then echo "users/$U admins/$U"; else echo "users/$U"; fi; }

check_config() {
  echo "== ① 配置与开通 =="
  MODEL_URL=$(G configmap agent-config -o jsonpath='{.data.MODEL_BASE_URL}'); MODEL_URL=${MODEL_URL%/}
  MODEL_ID=$(G configmap agent-config -o jsonpath='{.data.MODEL_ID}')
  GH=$(G configmap agent-config -o jsonpath='{.data.GRMP_API_HOST}')
  GP=$(G configmap agent-config -o jsonpath='{.data.GRMP_API_PORT}')
  CUR_PROXY=$(G configmap agent-config -o jsonpath='{.data.HTTPS_PROXY}')
  local keylen restart="${KN} rollout restart deploy/${D}"
  keylen=$(G secret model-api -o jsonpath='{.data.MODEL_API_KEY}' | wc -c | tr -d ' ')
  if ! printf '%s' "$MODEL_URL" | grep -Eq '^https?://[A-Za-z0-9._:/-]+$' || printf '%s' "$MODEL_URL" | grep -q 'example\.internal'; then
    bad "模型地址 MODEL_BASE_URL 没填、格式不对或还是样例值:「${MODEL_URL}」"
    advise "模型地址没配对:「${MODEL_URL}」" "填模型服务的实际地址(OpenAI 兼容接口,一般以 /v1 结尾),改完重启用户容器" \
      "${KN} patch configmap agent-config --type merge -p '{\"data\":{\"MODEL_BASE_URL\":\"<模型服务地址,如 https://api.deepseek.com/v1>\"}}'" "$restart"
  else ok "模型地址 ${MODEL_URL},模型 ${MODEL_ID:-（未填）}"; fi
  if [ -z "$MODEL_ID" ]; then
    bad "模型名 MODEL_ID 没填"
    advise "模型名 MODEL_ID 没填" "填模型服务里的模型名,改完重启用户容器" \
      "${KN} patch configmap agent-config --type merge -p '{\"data\":{\"MODEL_ID\":\"<模型名>\"}}'" "$restart"
  fi
  if [ "${keylen:-0}" -gt 1 ]; then ok "模型 key 已配置(Secret model-api,不打印内容)"
  else
    bad "Secret model-api 里没有 MODEL_API_KEY"
    advise "没有模型 key" "建 Secret model-api,改完重启用户容器" \
      "${KN} create secret generic model-api --from-literal=MODEL_API_KEY='<模型 key>'" "$restart"
  fi
  [ -n "$CUR_PROXY" ] && note "agent-config 配了代理 HTTPS_PROXY=${CUR_PROXY}(用户容器经它访问模型服务)"
  case "$GH" in
    ""|localhost|127.*|*example.internal)
      GRMP_BAD=1
      bad "中间件地址是「${GH:-（空）}:${GP}」—— 容器里的 localhost 指容器自己,登录数据库会连不上"
      advise "中间件地址是「${GH:-（空）}:${GP}」" "容器里的 localhost 指容器自己;要填中间件的实际 IP 与端口。脚本会同时改地址与对应的出网规则,并重启用户容器" \
        "${CMD} ${U} --fix --grmp <中间件IP:端口>" ;;
    *) ok "中间件地址 ${GH}:${GP}" ;;
  esac
  if [ -n "$(G secret "grmp-token-$U" -o name)" ]; then ok "已开通(有 Secret grmp-token-${U})"
  else
    bad "没开通:没有 Secret grmp-token-${U}(登录时网关回 503「您的环境尚未开通」)"
    advise "工号 ${U} 没开通" "建这个工号的中间件令牌 Secret" \
      "${KN} create secret generic grmp-token-${U} --from-literal=GRMP_AUTH_TOKEN='<该工号的中间件令牌>'"
  fi
}

check_nas() {
  echo "== ② NAS 个人目录 =="
  if [ -z "$IMG" ]; then
    bad "找不到 runtime 镜像名,跳过 ②④(先让这个工号登录一次,或检查 gateway-config 的 USER_IMAGE_TAG)"
    advise "找不到 runtime 镜像名,目录与出网没查" "让这个工号先登录一次,或检查 gateway-config 的 USER_IMAGE_TAG,然后重跑本脚本" \
      "${KN} get configmap gateway-config -o jsonpath='{.data.USER_IMAGE_TAG}'"
    return
  fi
  local nasout d owner w line
  nasout=$(wait_pod "$p_nas")
  if printf '%s' "$nasout" | grep -q '^TIMEOUT'; then
    bad "挂 NAS 的临时 Pod 起不来 —— NAS 本身挂不上(用户容器也会卡住):"
    printf '%s\n' "$nasout" | grep '^EVENT' | sed 's/^EVENT /         /'
    advise "NAS 挂不上" "看上面的事件:多为 PV 里的 NFS 地址或导出路径不对(不能填 localhost)。没有 NAS 的单节点测试环境可改用节点本机目录(部署手册 §2.2)" \
      "${KN} describe pvc nas | tail -15" "kubectl get pv -o wide"
    return
  fi
  for d in $dirs; do
    line=$(printf '%s\n' "$nasout" | awk -v d="$d" '$1 == "DIR" && $2 == d { print $3, $4 }')
    owner=${line%% *}; w=${line##* }
    if [ "$owner" = missing ]; then
      NEED_DIR=1; bad "$d 还没建:首次登录时会被自动建成 root 属主,容器写不进"
      advise "$d 还没建" "首次登录时 kubelet 会把它建成 root 属主,容器以 uid 1000 写不进。--fix 用临时 root Pod 先建好并改属主" "${CMD} ${U} --fix"
    elif [ "$w" = yes ]; then
      if [ "$owner" = 1000 ]; then ok "$d 存在,属主 1000,可写"
      else ok "$d 存在,uid 1000 可写(属主显示 uid ${owner},这个存储映射了属主)"; fi
    elif [ -n "$owner" ]; then
      NEED_DIR=1; bad "$d 属主是 uid ${owner},容器的 uid 1000 写不进(容器会一启动就退出)"
      advise "$d 写不进(属主 uid ${owner})" "容器以 uid 1000 运行;--fix 把属主改成 1000 并重启用户容器。NAS 若开了 root_squash,root 也改不了,要在 NAS 侧给 uid 1000 授权" "${CMD} ${U} --fix"
    else
      NEED_DIR=1; bad "$d 查不到状态:$(printf '%s' "$nasout" | tail -1)"
      advise "$d 查不到状态" "--fix 会重建并改属主;仍不行把本输出发给我方" "${CMD} ${U} --fix"
    fi
  done
}

runtime_status() {
  echo "== ③ 用户容器 ${D} =="
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
  [ "$waiting" = CrashLoopBackOff ] && waiting="CrashLoopBackOff(反复退出,等着下次重启)"
  bad "${D} 没就绪:重启 ${restarts} 次,上次退出 ${last},当前 ${waiting:-运行中}"
  [ -n "$pod" ] || { advise "${D} 没就绪" "先修本输出里的其他问题;都正常了还起不来,把下面两条命令的输出发给我方" \
      "${KN} describe pod -l user=${U} | tail -25"; return; }
  # 上一个容器的日志可能已被回收(只剩一句 unable to retrieve),和当前容器的合在一起看
  logs=$({ K logs "$pod" --previous --tail=15; K logs "$pod" --tail=15; } 2>/dev/null | tr -d '\r' | grep -v '^unable to retrieve container logs')
  local why="先修本输出里的其他问题;都正常了还起不来,把下面两条命令的输出发给我方"
  if [ "${NEED_DIR:-0}" = 1 ]; then
    why="多半就是 ② 的个人目录写不进:容器以 uid 1000 运行,写不进就退出。--fix 修好目录后会重启它"
  elif printf '%s' "$logs" | grep -q 'Permission denied'; then
    note "日志里有 Permission denied:个人目录写不进(见 ②)"; why="个人目录写不进(见 ②),--fix 修目录后会重启它"
  elif printf '%s' "$logs" | grep -q '独占标记'; then
    note "日志停在「独占标记」:上一个容器还占着这个人的目录,90 秒内自动过期"; why="上一个容器还占着这个人的目录,等 90 秒自动过期后它会自己起来"
  fi
  if [ -n "$logs" ]; then note "最近的日志:"; printf '%s\n' "$logs" | tail -8 | sed 's/^/           | /'; fi
  K get events --field-selector "involvedObject.name=$pod" --sort-by=.lastTimestamp \
    -o jsonpath='{range .items[*]}{.reason}: {.message}{"\n"}{end}' 2>/dev/null | tr -d '\r' \
    | grep -E 'Failed|BackOff|Unhealthy' | tail -3 | sed 's/^/         事件 /'
  advise "${D} 没就绪(重启 ${restarts} 次,上次退出 ${last})" "$why" \
    "${KN} logs ${pod} --previous --tail=50" "${KN} describe pod ${pod} | tail -25"
}

# TCP 连上了但应用层不通:证书 / TLS / 被断开 —— 不是出网策略的问题
tls_advice() {  # $1 名称 $2 目标 $3 类别 $4 curl 原始报错 $5 前缀说明
  local via=${VIA:-}
  proxy_hint
  if [ -n "$via" ]; then
    advise "$5$1 $2 经代理 ${via} 仍不通:$(describe "$3")" "代理连上了,但到 $2 这一段失败(${4:-无报错})。检查代理是否允许访问该地址、是否要认证;或代理在解密 HTTPS(需要公司根证书)" \
      "${KN} get configmap agent-config -o jsonpath='{.data.HTTPS_PROXY}'"
  elif [ "$3" = CERT ]; then
    advise "$5$1 $2:$(describe "$3")" "${4:-}。公司网络多半在解密 HTTPS(中间人证书),容器不信任公司根证书。若公司规定外网要经代理访问,可先试经代理(${PH_TEXT});若代理也解密,本版还不支持给容器加载公司根证书,请把本输出发给我方" \
      "$PH_CMD"
  else
    advise "$5$1 $2:$(describe "$3")" "${4:-}。这台电脑上能连、集群里直连不行,多半是公司出口不允许直连外网,要经公司代理。${PH_TEXT}" \
      "$PH_CMD"
  fi
}

# $1 名称 $2 标记 $3 受约束 Pod 的输出 $4 不受约束 Pod 的输出 $5 目标
probe_verdict() {
  local a b ca cb ea eb ha hb rca rcb
  a=$(field "$2" "$3"); b=$(field "$2" "$4")
  VIA=$(field VIA "$3")
  # 只设了 HTTPS 代理:只有 https 的模型地址经它;中间件是 http,又在 NO_PROXY 里,是直连
  case "$2 $5" in "MODEL https://"*) ;; *) VIA="" ;; esac
  if [ -z "$a" ]; then
    bad "$1:临时 Pod 没跑出结果 $(printf '%s\n' "$3" | grep '^EVENT' | head -1 | sed 's/^EVENT //')"
    advise "$1:出网实测没跑成" "临时 Pod 没跑出结果(集群若禁止建 root / 普通 Pod,会在上面看到报错);把本输出发给我方"
    return
  fi
  parse_probe "$a"; ca=$P_CLS; ea=$P_ERR; ha=$P_HTTP; rca=$P_RC
  if [ "$ca" = OK ]; then
    ok "$1 $5:用户容器能连上,$(describe OK "$ha")${VIA:+(经代理 $VIA)}"
    [ "$2" = MODEL ] && [ "$ha" = 401 ] && note "401 是因为探测没带 key,网络是通的"
    return
  fi
  if tcp_ok "$ca"; then
    bad "$1 $5:用户容器 $(describe "$ca" "$ha" "$rca")${ea:+ —— $ea}"
    tls_advice "$1" "$5" "$ca" "$ea" ""
    return
  fi
  if [ -z "$b" ]; then
    bad "$1 $5:用户容器 $(describe "$ca" "$ha" "$rca");对照用的 Pod 没跑出结果,分不清是不是出网策略挡的"
    advise "$1 连不上(原因未定)" "对照用的临时 Pod 没跑出结果;把本输出发给我方"
    return
  fi
  parse_probe "$b"; cb=$P_CLS; eb=$P_ERR; hb=$P_HTTP; rcb=$P_RC
  if tcp_ok "$cb"; then
    bad "$1 $5:用户容器 $(describe "$ca" "$ha" "$rca"),不受出网策略约束的 Pod TCP 能连上($(describe "$cb" "$hb" "$rcb"))→ 被出网策略挡了"
    if [ "$2" = MODEL ]; then
      MODEL_BLOCKED=1; proxy_hint
      advise "模型服务被出网策略挡了(runtime-policy 的模型规则不放行 $5)" "放宽模型服务的出网规则。--fix 放宽到 ${MODEL_CIDR} 的 443/80(用户容器能访问任意 HTTPS 地址);公司出网要经代理时,用 --proxy 只放行代理更窄" \
        "${CMD} ${U} --fix" "$PH_CMD"
    else
      GRMP_BLOCKED=1
      advise "中间件被出网策略挡了(runtime-policy 的中间件规则不放行 $5)" "把中间件那条出网规则改成中间件的实际 IP 与端口" "${CMD} ${U} --fix --grmp ${GH}:${GP}"
    fi
    [ "$cb" = OK ] || tls_advice "$1" "$5" "$cb" "$eb" "放开出网策略之后还会遇到 —— "
    return
  fi
  bad "$1 $5:用户容器 $(describe "$ca" "$ha" "$rca"),不受约束的 Pod 也 $(describe "$cb" "$hb" "$rcb") → 集群本身连不上,不是出网策略的问题"
  if [ "$cb" = DNS ] || [ "$cb" = PROXY_DNS ]; then
    proxy_hint
    advise "$1 $5:集群里域名解析失败" "集群的 DNS 解析不了这个域名(内网 DNS 往往不解析外网域名)。改用 IP,或经公司代理访问(代理负责解析)。${PH_TEXT}" "$PH_CMD"
  elif [ "$2" = MODEL ]; then
    proxy_hint
    advise "模型服务 $5:集群里连不上(${eb:-无报错})" "集群节点到这个地址不通。这台电脑上能连的话,多半电脑走了公司代理而集群没有。${PH_TEXT}" "$PH_CMD"
  else
    advise "中间件 $5:集群里连不上(${eb:-无报错})" "检查中间件 IP 与端口是否填对、中间件是否允许集群节点访问(防火墙 / 白名单)"
  fi
}

check_all() {
  MODEL_BLOCKED=0; GRMP_BLOCKED=0; GRMP_BAD=0; NEED_DIR=0; ADV=""; ADV_N=0
  PASS=$((PASS + 1))
  p_nas=doctor-nas-$RUN-$PASS; p_rt=doctor-rt-$RUN-$PASS; p_free=doctor-free-$RUN-$PASS
  check_config

  # 三个临时 Pod 一起起,下面按顺序取结果
  dirs=$(user_dirs)
  local grmp_url=""
  [ "$GRMP_BAD" = 0 ] && grmp_url="http://$GH:$GP/"
  local probe_env="[{name: MODEL_URL, value: \"$MODEL_URL/models\"}, {name: GRMP_URL, value: \"$grmp_url\"}]"
  if [ -n "$IMG" ]; then
    start_pod "$p_nas" 1000 "app: gaussdb-agent-doctor" rw "[{name: DIRS, value: \"$dirs\"}]" "$NAS_SH"
    start_pod "$p_rt" 1000 "app: gaussdb-agent, role: runtime, user: doctor-probe" none "$probe_env" "$PROBE_SH" yes
    start_pod "$p_free" 1000 "app: gaussdb-agent-doctor" none "$probe_env" "$PROBE_SH" yes
  fi

  check_nas
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
    if i=$(rule_index runtime-policy 443); then note "模型服务规则(第 $i 条)放行 $(rule_cidrs runtime-policy "$i") 的 $(rule_ports runtime-policy "$i")"
    elif [ -n "$CUR_PROXY" ]; then note "模型服务经代理 ${CUR_PROXY} 访问(没有直连 443 的规则)"
    else note "没找到放行 443 的规则(模型服务)"; fi
    if i=$(rule_index runtime-policy "${GP:-8080}"); then note "中间件规则(第 $i 条)放行 $(rule_cidrs runtime-policy "$i") 的 ${GP:-8080}"
    else note "没找到放行端口 ${GP:-8080} 的规则(中间件)"; fi
  fi

  echo "== ⑥ 运行本脚本的这台电脑的代理 =="
  if [ -n "$LP" ]; then note "${LP_SRC}:${LP}"
  elif [ -n "$LP_PAC" ]; then note "自动配置脚本(PAC):${LP_PAC}"
  else note "没发现代理设置"; fi
}

# 单元测试只加载上面的函数:DOCTOR_LIB=1 . runtime-doctor.sh
[ "${DOCTOR_LIB:-0}" = 1 ] && { return 0 2>/dev/null || exit 0; }

parse_args "$@"
command -v kubectl >/dev/null 2>&1 || { echo "找不到 kubectl,请在能执行 kubectl 的机器上运行" >&2; exit 2; }
K get configmap agent-config >/dev/null 2>&1 \
  || { echo "读不到命名空间 ${NS} 里的 agent-config:kubectl 连不上集群,或命名空间不对(可用 NS=... 指定)" >&2; exit 2; }

D=runtime-$U
# 工号没开通、也从没登录过:多半是参数写错了(2026-10 现场把 bash 当成了工号诊断了一遍)
if [ -z "$(G deploy "$D" -o name)" ] && [ -z "$(G secret "grmp-token-$U" -o name)" ]; then
  echo "工号「${U}」没开通(没有 Secret grmp-token-${U}),也从没登录过(没有 ${D})。"
  echo "  · 参数写错了的话:bash runtime-doctor.sh <工号>,例如 bash runtime-doctor.sh u0001"
  echo "  · 是新工号的话,先开通再登录:${KN} create secret generic grmp-token-${U} --from-literal=GRMP_AUTH_TOKEN='<该工号的中间件令牌>'"
  exit 2
fi

RUN=$(date +%s | tail -c 7)    # 本次运行的编号:临时 Pod 都打上它,退出时按它一起删
PASS=0
trap cleanup EXIT

IMG=$(G deploy "$D" -o jsonpath='{.spec.template.spec.containers[0].image}')
[ -n "$IMG" ] || IMG=$(G deploy -l role=runtime -o jsonpath='{.items[0].spec.template.spec.containers[0].image}')
if [ -z "$IMG" ]; then
  TAG=$(G configmap gateway-config -o jsonpath='{.data.USER_IMAGE_TAG}')
  [ -n "$TAG" ] && IMG="gaussdb-agent-runtime:${TAG}"
fi
detect_local_proxy

echo "诊断工号 ${U}(命名空间 ${NS})…… 约 30 秒到 1 分钟"
check_all

if [ "$FIX" = 1 ]; then
  echo
  echo "== 修复 =="
  RESTART=0; CHANGED=0
  if [ -n "$PROXY" ]; then
    ph=${PROXY%:*}; pp=${PROXY##*:}
    np="localhost,127.0.0.1,.svc,.cluster.local"
    gh_new=${GRMP%:*}; [ -n "$gh_new" ] || gh_new=$GH
    printf '%s' "$gh_new" | grep -Eq "^${IPV4}\$" && np="${np},${gh_new}"
    # 只设 HTTPS 代理:发给中间件的是 http 请求,不受影响;NO_PROXY 再兜一层
    if [ "$CUR_PROXY" = "http://${PROXY}" ] && [ "$(G configmap agent-config -o jsonpath='{.data.NO_PROXY}')" = "$np" ]; then
      echo "  [不用改] agent-config 已经是 HTTPS_PROXY=http://${PROXY},NO_PROXY=${np}"
    elif K patch configmap agent-config --type merge -p "{\"data\":{\"HTTPS_PROXY\":\"http://${PROXY}\",\"https_proxy\":\"http://${PROXY}\",\"NO_PROXY\":\"${np}\",\"no_proxy\":\"${np}\"}}" >/dev/null; then
      echo "  [已改] agent-config 加上 HTTPS_PROXY=http://${PROXY},NO_PROXY=${np}"; RESTART=1; CHANGED=1
    else echo "  [失败] 改不了 agent-config"; fi
    for pol in runtime-policy kb-import-policy; do
      [ -n "$(G networkpolicy "$pol" -o name)" ] || continue
      if i=$(rule_index "$pol" 443) && patch_rule "$pol" "$i" "[{\"ipBlock\":{\"cidr\":\"${ph}/32\"}}]" "[{\"port\":${pp},\"protocol\":\"TCP\"}]"; then
        echo "  [已改] ${pol} 第 $i 条(模型服务)→ 只放行代理 ${ph}/32 的 ${pp}"; CHANGED=1
      elif i=$(rule_index "$pol" "$pp" "${ph}/32"); then
        echo "  [不用改] ${pol} 第 $i 条已经只放行代理 ${ph}/32 的 ${pp}"
      else echo "  [失败] ${pol}:没找到模型服务规则(放行 443 的那条),或它刚被别人改过 —— 请人工检查"; fi
    done
  elif [ "$MODEL_BLOCKED" = 1 ]; then
    for pol in runtime-policy kb-import-policy; do
      [ -n "$(G networkpolicy "$pol" -o name)" ] || continue
      if i=$(rule_index "$pol" 443) && patch_rule "$pol" "$i" "[{\"ipBlock\":{\"cidr\":\"${MODEL_CIDR}\"}}]"; then
        echo "  [已改] ${pol} 第 $i 条(模型服务)→ 放行 ${MODEL_CIDR} 的 443/80"; CHANGED=1
      else echo "  [失败] ${pol}:没找到模型服务规则,或它刚被别人改过 —— 请人工检查"; fi
    done
  fi
  if [ -n "$GRMP" ]; then
    h=${GRMP%:*}; p=${GRMP##*:}
    if i=$(rule_index runtime-policy "$p" "$h/32"); then
      echo "  [不用改] runtime-policy 第 $i 条(中间件)已经只放行 $h/32 的 $p"
    elif i=$(rule_index runtime-policy "${GP:-8080}") \
       && patch_rule runtime-policy "$i" "[{\"ipBlock\":{\"cidr\":\"$h/32\"}}]" "[{\"port\":$p,\"protocol\":\"TCP\"}]"; then
      echo "  [已改] runtime-policy 第 $i 条(中间件)→ 只放行 $h/32 的 $p"; CHANGED=1
    elif [ -n "$(G networkpolicy runtime-policy -o name)" ]; then
      echo "  [失败] runtime-policy 里没找到中间件规则(端口 ${GP:-8080}),请人工添加 $h/32 的 $p"
    fi
    if [ "$GH" = "$h" ] && [ "$GP" = "$p" ]; then
      echo "  [不用改] agent-config 中间件地址已经是 $h:$p"
    elif K patch configmap agent-config --type merge -p "{\"data\":{\"GRMP_API_HOST\":\"$h\",\"GRMP_API_PORT\":\"$p\"}}" >/dev/null; then
      echo "  [已改] agent-config 中间件地址 → $h:$p"; RESTART=1; CHANGED=1
    else echo "  [失败] 改不了 agent-config"; fi
  elif [ "$GRMP_BLOCKED" = 1 ]; then
    if printf '%s' "$GH" | grep -Eq "^${IPV4}\$" && i=$(rule_index runtime-policy "${GP:-8080}") \
       && patch_rule runtime-policy "$i" "[{\"ipBlock\":{\"cidr\":\"$GH/32\"}}]"; then
      echo "  [已改] runtime-policy 第 $i 条(中间件)→ 放行 $GH/32 的 ${GP}"; CHANGED=1
    else echo "  [未改] 中间件地址 $GH 不是 IP,出网策略不能按域名放行:用 --fix --grmp <IP:端口> 重跑"; fi
  fi
  if [ "$NEED_DIR" = 1 ] && [ -n "$IMG" ]; then
    pf=doctor-fix-$RUN
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
  if [ -n "$GRMP" ] || [ -n "$PROXY" ]; then
    others=$(G deploy -l role=runtime -o jsonpath='{range .items[*]}{.metadata.name} {.spec.replicas}{"\n"}{end}' \
             | awk -v d="$D" '$1 != d && $2 > 0' | wc -l | tr -d ' ')
    [ "${others:-0}" -gt 0 ] && echo "  注意:另有 ${others} 个用户容器在运行,仍用旧的配置;需要时执行 ${KN} rollout restart deploy -l role=runtime"
  fi

  echo
  echo "======== 复查 ========"
  check_all
fi

echo
echo "================ 诊断意见 ================"
if [ "$ADV_N" = 0 ]; then
  echo "全部正常。对话页刷新后重试即可。"
  exit 0
fi
printf '%s' "$ADV"
if [ "$FIX" = 0 ] && printf '%s' "$ADV" | grep -q -- "${U} --fix\$"; then
  echo "提示:可以试里带 --fix 的命令会先修再复查一遍,改了什么逐条打印;不加 --fix 只诊断、不改任何东西。"
fi
exit 1
