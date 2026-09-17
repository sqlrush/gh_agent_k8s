#!/usr/bin/env bash
# 镜像冒烟(Mac + OrbStack):用本地目录模拟 NAS,8779 mock 经转发器给容器用。
# 用法: scripts/smoke-image.sh <TAG>
# 前提: ~/kf-verify 验收环境已就绪(kf_env_up:8779 mock 含实例 10.0.0.9 → og5),令牌在 ~/.gdaa/grmp.env,
#       知识库样例在 ~/kb-verify/modes/kb。
set -u
TAG=${1:?tag}
HERE=$(cd "$(dirname "$0")/.." && pwd)
NAS=${NAS:-/tmp/agent-nas}
U=u12345
RT=gaussdb-agent-runtime:$TAG
KI=gaussdb-agent-kb-import:$TAG
HOSTGW=${HOSTGW:-host.docker.internal}
pass=0; fail=0
ok(){ pass=$((pass+1)); echo "[PASS] $1"; }
bad(){ fail=$((fail+1)); echo "[FAIL] $1"; echo "   | $(printf '%s' "${out:-}" | head -6 | tr '\n' ' ' | cut -c1-400)"; }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }
has(){ printf '%s' "$out" | grep -q -- "$1"; }
set -a; . "$HOME/.gdaa/grmp.env"; set +a
ENV=(-e GSDB_USER_ID=$U -e GRMP_API_HOST=$HOSTGW -e GRMP_API_PORT=8781 -e GRMP_AUTH_TOKEN="$GRMP_AUTH_TOKEN"
     -e MODEL_BASE_URL=http://$HOSTGW:9 -e MODEL_API_KEY=x -e MODEL_ID=none)
python3 "$HERE/scripts/tcp-forward.py" 8781 8779 & FWD=$!
cleanup(){ docker rm -f smoke-rt smoke-rt2 smoke-ki >/dev/null 2>&1; kill $FWD 2>/dev/null; }
trap cleanup EXIT
rm -rf "$NAS"; mkdir -p "$NAS/users/$U" "$NAS/admins/$U"
cp -R "$HOME/kb-verify/modes/kb" "$NAS/kb"; rm -f "$NAS/kb/.lock"
chmod -R a+rwX "$NAS"

run_rt(){ docker run -d --name "$1" "${ENV[@]}" -e POD_NAME="$1" -v "$NAS/users/$U:/nas/me" -v "$NAS/kb:/nas/kb:ro" -p "$2:4096" "$RT" >/dev/null; }
wait_ready(){ for _ in $(seq 1 40); do curl -fsS -m 2 "http://127.0.0.1:$1/global/health" >/dev/null 2>&1 && return 0; sleep 1; done; return 1; }
X(){ docker exec "$1" bash -c ". /data/env.sh; $2" 2>&1; }
S=/opt/agent/config/opencode/skills

# 1 启动与探针
run_rt smoke-rt 14096
wait_ready 14096 || { out=$(docker logs smoke-rt 2>&1); bad "runtime 40 秒内没就绪"; }
out=$(curl -s http://127.0.0.1:14096/global/health)
check "runtime 启动,/global/health 返回 healthy"              'has "\"healthy\":true"'
out=$(X smoke-rt "id -u; ls -la /nas/me; cat /nas/me/.owner")
check "以 uid 1000 运行,/nas/me 下有 .owner、gdaa、xdg-data"    'has "^1000" && has ".owner" && has "gdaa" && has "xdg-data"'
out=$(X smoke-rt 'cat $GSDB_HOME/config.yaml; ls -la /data/oc')
check "config.yaml 与 opencode.json 已按环境变量渲染(0600)"    'has "host: '"$HOSTGW"'" && has "port: 8781" && has -- "-rw-------.*opencode.json"'

# 2 经 mock 登录并跑诊断,报告带执行人
out=$(X smoke-rt "python3 $S/gaussdb-login/scripts/login.py --ip 10.0.0.9 --database postgres")
H=$(printf '%s' "$out" | grep -oE -- '--session [a-z0-9]{12}' | head -1 | awk '{print $2}')
check "登录 10.0.0.9/postgres 得到句柄"                        '[ -n "$H" ]'
out=$(X smoke-rt "python3 $S/gaussdb-health/scripts/health.py --session $H")
check "health 经中间件出报告并标明执行人"                      'has "Health Evidence" && has "执行人：'"$U"'"'

# 3 知识库:只读查询可用,导入命令是桩
out=$(X smoke-rt "python3 $S/gaussdb-kb/scripts/kb.py health")
check "kb health:文件模式 + 知识库只读"                        'has "模式:文件" && has "知识库只读"'
out=$(X smoke-rt "python3 $S/gaussdb-kb/scripts/kb.py search 索引台账")
check "kb search 命中样例条款(按行 grep,命中 rule/keywords 行)"   'has "rules/idx.yaml" && has "索引台账"'
out=$(X smoke-rt "python3 $S/gaussdb-kb/scripts/kb.py ingest /tmp/x.xlsx; echo rc=\$?")
check "runtime 里 ingest 是桩:指向 kb-import,退出码 2"          'has "gaussdb-kb-import" && has "rc=2"'
out=$(X smoke-rt "touch /nas/kb/probe 2>&1; echo rc=\$?")
check "runtime 对 /nas/kb 只读"                                 'has "Read-only" || has "rc=1"'

# 4 .owner 冲突:同一用户目录第二个 Pod 必须等待,不打开 db
run_rt smoke-rt2 14097; sleep 8
out=$(docker logs smoke-rt2 2>&1; echo "running=$(docker inspect -f '{{.State.Running}}' smoke-rt2)")
check "第二个 Pod 看到新鲜的 .owner 后等待,不启动 opencode"      '! has "数据库自检" && (has "running=true" || has "仍持有")'
docker rm -f smoke-rt2 >/dev/null 2>&1

# 5 优雅停机:WAL 合并、备份、释放标记
docker stop -t 60 smoke-rt >/dev/null
out=$(docker logs smoke-rt 2>&1 | tail -6; ls -la "$NAS/users/$U/xdg-data/opencode" "$NAS/users/$U/backup" 2>&1; test -e "$NAS/users/$U/.owner" && echo OWNER-STILL-THERE)
check "停机后 WAL 已合并、backup/ 有文件、.owner 已删"           'has "WAL 已合并" && has "退出备份" && has "opencode.db.20" && ! has "OWNER-STILL-THERE"'
docker rm -f smoke-rt >/dev/null 2>&1

# 6 损坏恢复:写坏 db,重启应从备份恢复并报出
printf 'garbage%.0s' $(seq 1 2000) > "$NAS/users/$U/xdg-data/opencode/opencode.db"
run_rt smoke-rt 14096; wait_ready 14096; out=$(docker logs smoke-rt 2>&1)
check "启动自检发现损坏,从备份恢复并写明"                     'has "损坏" && has "已从备份"'
docker rm -f smoke-rt >/dev/null 2>&1

# 7 kb-import 镜像:可写知识库,导入命令真实存在,只有两个 kb skill
docker run -d --name smoke-ki -e GSDB_USER_ID=$U -e POD_NAME=kb-import-$U -e MODEL_BASE_URL=http://$HOSTGW:9 -e MODEL_API_KEY=x -e MODEL_ID=none \
  -e GRMP_API_HOST=unused -v "$NAS/admins/$U:/nas/me" -v "$NAS/kb:/nas/kb" -p 14098:4096 "$KI" >/dev/null
wait_ready 14098 || { out=$(docker logs smoke-ki 2>&1); bad "kb-import 40 秒内没就绪"; }
out=$(X smoke-ki "ls $S | grep gaussdb- | tr '\n' ' '; python3 $S/gaussdb-kb-import/scripts/kb.py validate --kb /nas/kb | tail -1; python3 $S/gaussdb-kb/scripts/kb.py health | head -3")
check "kb-import 只有两个 kb skill,validate 可跑,health 不报只读"  'has "gaussdb-kb-import" && ! has "gaussdb-health" && ! has "知识库只读"'

echo "== 汇总: PASS $pass, FAIL $fail"
[ $fail -eq 0 ]
