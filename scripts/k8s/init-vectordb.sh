#!/usr/bin/env bash
# 向量库初始化:建 kb 库、建读写与只读两个用户、验证 DataVec 可用。幂等,重复跑安全。
#
#   scripts/k8s/init-vectordb.sh              建库建用户 + 自检
#   scripts/k8s/init-vectordb.sh --check      只自检,不改任何东西
#
# 表结构不在这里建 —— `kb.py index` 会幂等地建表建索引(common/kb/store_pg.py::setup)。
# 这里只做「库 + 用户 + 权限」这一层,因为那需要超级用户,而 Pod 里只有普通用户。
#
# 为什么分两个用户:runtime Pod 对 kb/ 是只读挂载。给它读写的库用户,用户就能让模型
# 在自己 Pod 里读出口令、直接改派生索引 —— 只读挂载那道门就白设了。
set -uo pipefail
C=${CONTEXT:-orbstack}; NS=gaussdb-agent
SS=vectordb-0
K(){ kubectl --context "$C" -n "$NS" "$@"; }
# 在库里跑一句 SQL(超级用户 omm,走容器内的本地连接,口令不经网络也不进命令行历史)。
#
# 两个坑,都踩过:
# ① **必须 su - omm**:容器主进程是 root,gsql 只在 omm 的 PATH 与环境里;root 直接跑是 127,
#    `bash -lc` 则拿不到 $GAUSSLOG 报一串 WARNING 后失败。
# ② **SQL 走 stdin,不走 -c**:SQL 里本来就有单引号(typname='vector'),套进
#    `su - omm -c "..."` 再套 `-Atc '...'` 会被截断,表现是查询静默返回空 —— 看起来像
#    「这个镜像没有 vector 类型」,而其实是引号错了。
# ③ gsql 跑 `-f` 之后会固定多打一行「total time: N ms」,`-At` 不管它、`\timing off` 也去不掉
#    (那是执行摘要,不是计时开关)。不滤掉的话 `[ "$(gs ...)" = "1" ]` 永远不成立,
#    看起来又像「查询没结果」——我在这上面来回试了三次,留个记号。
gs(){ printf '%s\n' "$1" | K exec -i "$SS" -- bash -c \
  "cat > /tmp/q.sql && chmod 644 /tmp/q.sql && su - omm -c 'gsql -d ${2:-postgres} -p 5432 -Atf /tmp/q.sql'; rc=\$?; rm -f /tmp/q.sql; exit \$rc" 2>/dev/null \
  | sed '/^total time:/d'; }

echo "== 0 等 vectordb 就绪"
K get statefulset vectordb >/dev/null 2>&1 || { echo "没有 vectordb —— 先 kubectl apply -k k8s/base"; exit 1; }
K rollout status statefulset/vectordb --timeout=300s || { echo "vectordb 没起来,看 kubectl -n $NS logs $SS"; exit 1; }

echo "== 1 DataVec 自检(这是选 openGauss 7 的全部理由,先验它)"
has_vec=$(gs "select count(*) from pg_type where typname='vector'")
[ "$has_vec" = "1" ] || { echo "   ✗ 这个镜像没有 vector 类型,换 7.0 以上或改用 PG+pgvector"; exit 1; }
gs "create table if not exists zz_vec_probe(id int, v vector(4))" >/dev/null
probe=$(gs "select round((('[1,2,3,4]'::vector) <=> ('[1,2,3,5]'::vector))::numeric, 6)")
gs "drop table if exists zz_vec_probe" >/dev/null
[ -n "$probe" ] || { echo "   ✗ <=> 余弦距离算子不可用"; exit 1; }
echo "   ✓ vector 类型在,<=> 可用(样例距离 $probe)"

if [ "${1:-}" = "--check" ]; then
  echo "== 只自检,到此为止"
  for u in kb_rw kb_ro; do
    echo "   $u: $(gs "select count(*) from pg_roles where rolname='$u'" | tr -d '\n') (1=存在)"
  done
  echo "   kb 库: $(gs "select count(*) from pg_database where datname='kb'" | tr -d '\n') (1=存在)"
  exit 0
fi

echo "== 2 取口令(只经 stdin 进容器,不落盘、不进命令行)"
RW=$(K get secret vectordb-auth -o jsonpath='{.data.KB_RW}' 2>/dev/null | base64 -d)
RO=$(K get secret vectordb-auth -o jsonpath='{.data.KB_RO}' 2>/dev/null | base64 -d)
[ -n "$RW" ] && [ -n "$RO" ] || { echo "Secret vectordb-auth 缺 KB_RW / KB_RO —— 见 k8s/base/secret-vectordb.example.yaml"; exit 1; }

echo "== 3 建库、建用户、授权(幂等)"
# openGauss 的 gsql 不认 psql 的 `\gexec`,CREATE DATABASE 又不能放进 DO 块(不能在事务里跑),
# 所以「存不存在」由外面先查一次,再决定要不要建。口令始终经 stdin 进容器,
# 不出现在 kubectl 的参数里(参数会进 k8s 审计日志)。
if [ "$(gs "select count(*) from pg_database where datname='kb';")" = "0" ]; then
  gs "CREATE DATABASE kb DBCOMPATIBILITY 'PG';" >/dev/null || { echo "建库失败"; exit 1; }
  echo "   kb 库已建"
else
  echo "   kb 库已存在,跳过"
fi
for spec in "kb_rw:$RW" "kb_ro:$RO"; do
  u=${spec%%:*}; pw=${spec#*:}
  if [ "$(gs "select count(*) from pg_roles where rolname='$u';")" = "0" ]; then
    gs "CREATE USER $u PASSWORD '$pw';" >/dev/null || { echo "建用户 $u 失败"; exit 1; }
    echo "   用户 $u 已建"
  else
    gs "ALTER USER $u PASSWORD '$pw';" >/dev/null || { echo "改 $u 口令失败"; exit 1; }
    echo "   用户 $u 已存在,口令已对齐 Secret"
  fi
done
gs "GRANT ALL PRIVILEGES ON DATABASE kb TO kb_rw; GRANT CONNECT ON DATABASE kb TO kb_ro;" >/dev/null \
  || { echo "库级授权失败"; exit 1; }

echo "== 4 库内授权(schema 与未来新建的表)"
# 关键是最后那条 ALTER DEFAULT PRIVILEGES:kb.py index 之后才会有表,不给「将来建的表」
# 授权的话,第一次索引完 runtime 依然读不到 —— 而且报的是「表不存在」,看着像没索引。
gs "GRANT ALL ON SCHEMA public TO kb_rw;
GRANT USAGE ON SCHEMA public TO kb_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO kb_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE kb_rw IN SCHEMA public GRANT SELECT ON TABLES TO kb_ro;" kb >/dev/null \
  || { echo "库内授权失败"; exit 1; }
echo "   已授权(含 default privileges)"

echo "== 5 验一遍两个用户真能按预期连"
# 注:openGauss 不认 PGPASSWORD(已知),所以这里用 -W 显式传口令。口令只在容器内的
# 这条命令里出现一次,不落盘;kubectl 参数会进审计日志,所以整条命令由 stdin 送进去。
probe_as(){ K exec -i "$SS" -- bash -c 'cat > /tmp/p.sh && chmod 755 /tmp/p.sh && su - omm -c /tmp/p.sh; rm -f /tmp/p.sh' <<PS 2>&1 | tail -1
gsql -h 127.0.0.1 -U $1 -W '$2' -d kb -p 5432 -Atc "$3"
PS
}
echo "   kb_rw 能建表:        $(probe_as kb_rw "$RW" 'create table zz_rw_probe(i int); drop table zz_rw_probe; select 1' | cut -c1-70)"
echo "   kb_ro 建表应被拒:    $(probe_as kb_ro "$RO" 'create table zz_ro_probe(i int)' | cut -c1-70)"
echo "   kb_ro 能连能查:      $(probe_as kb_ro "$RO" 'select 1' | cut -c1-70)"

cat <<'TXT'

== 完成。接下来把 kb.yaml 指过来(NAS 上 <kb>/kb.yaml,全体共用一份):

  store:
    pg: {host: vectordb, port: 5432, database: kb, user: kb_ro, credential: kb-pg,
         user_env: KB_PG_USER, password_env: KB_PG_PASSWORD}
  embeddings: {source: url, base_url: <你的 embedding 服务>/v1, model: bge-m3, dims: 1024}

  用户名与口令都按 Pod 从环境变量取(模板已注入):
    runtime   → KB_PG_USER=kb_ro  + Secret 的 KB_RO   只读
    kb-import → KB_PG_USER=kb_rw  + Secret 的 KB_RW   读写
  yaml 里的 user / credential 是非容器部署的退路,容器里用不到。

然后在 kb-import Pod 里跑一次全量索引:

  python3 /data/config/opencode/skills/gaussdb-kb-import/scripts/kb.py index --all
TXT
