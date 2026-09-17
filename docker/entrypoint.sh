#!/usr/bin/env bash
# 后端 Pod 入口。顺序 = spec §6:目录 → 渲染配置 → .owner 独占 → db 自检 → 日志清理 → opencode serve
#                      → SIGTERM → WAL 合并 → 退出备份 → 释放独占。
# 所有状态在 $NAS_ME 下;镜像文件系统只读也能跑(可写的只有 /data 与 $NAS_ME)。
set -euo pipefail

NAS_ME=${NAS_ME:-/nas/me}
NAS_KB=${NAS_KB:-/nas/kb}
POD_NAME=${POD_NAME:-$(hostname)}
OPENCODE_PORT=${OPENCODE_PORT:-4096}
PODCTL="python3 /opt/agent/podctl.py"

# ---- 目录(spec §4) ---------------------------------------------------------
export XDG_DATA_HOME=$NAS_ME/xdg-data
export XDG_STATE_HOME=$NAS_ME/xdg-state
# opencode 加载配置时会往 $XDG_CONFIG_HOME/opencode/ 写 .gitignore、往 $XDG_CACHE_HOME/opencode/ 写 models.json 临时文件;
# 镜像目录只读(readOnlyRootFilesystem)会让配置加载失败、所有 API 报错(探针 /global/health 不碰配置,发现不了)。
# 所以每次启动把镜像里的 skills/AGENTS.md(3 MB)与 models.json/rg(8 MB)复制到 /data 下再指过去。
export XDG_CONFIG_HOME=/data/config
export XDG_CACHE_HOME=/data/cache
export GSDB_HOME=$NAS_ME/gdaa
export GSDB_KB_DIR=$NAS_KB
export HOME=/data/home                            # 有些库要写 $HOME;放本地可写目录
export OPENCODE_CONFIG=/data/oc/opencode.json     # 渲染出来的运行时配置(含 key,0600)
DB=$XDG_DATA_HOME/opencode/opencode.db
BACKUP=$NAS_ME/backup
mkdir -p "$XDG_DATA_HOME/opencode" "$XDG_STATE_HOME/opencode" "$GSDB_HOME" "$NAS_ME/workspace" "$BACKUP" \
         /data/home /data/oc /data/config/opencode /data/cache/opencode
cp -R /opt/agent/config/opencode/. /data/config/opencode/
cp -R /opt/agent/cache/opencode/. /data/cache/opencode/
chmod 700 "$GSDB_HOME"
[ -n "${GSDB_KB_INBOX:-}" ] && mkdir -p "$GSDB_KB_INBOX"
# exec 进容器排障时 `. /data/env.sh` 即得同样的目录变量;只放路径,不放令牌与密钥
env | grep -E '^(XDG_[A-Z_]+|GSDB_HOME|GSDB_KB_DIR|GSDB_KB_INBOX|OPENCODE_CONFIG|HOME)=' | sed 's/^/export /' > /data/env.sh

# ---- 配置:每次启动重生成,配置不是状态 ----------------------------------------
$PODCTL render-opencode-config --out "$OPENCODE_CONFIG"
$PODCTL render-gdaa-config --out "$GSDB_HOME/config.yaml"

# ---- 独占:同一用户目录同一时刻只许一个 Pod 打开 db --------------------------------
$PODCTL owner-acquire --dir "$NAS_ME" --pod "$POD_NAME"
( while sleep 30; do $PODCTL owner-refresh --dir "$NAS_ME" --pod "$POD_NAME" || exit 0; done ) &
REFRESHER=$!

# ---- 自检与清理 -------------------------------------------------------------
$PODCTL db-check --db "$DB" --backup-dir "$BACKUP"
$PODCTL log-prune --dir "$XDG_DATA_HOME/opencode/log" --days 7

# ---- 工作目录做成 git 仓库(只在首次):opencode 以此识别「项目」,Web 界面按项目列会话,撤销功能也靠它 ----
if [ ! -d "$NAS_ME/workspace/.git" ]; then
  git -C "$NAS_ME/workspace" init -q \
    && git -C "$NAS_ME/workspace" -c user.name=agent -c user.email=agent@pod commit -q --allow-empty -m "workspace init" \
    && echo "工作目录: 已初始化为 git 仓库($NAS_ME/workspace)"
fi

# ---- 启动 opencode;SIGTERM 转发给它,等它退出后再做收尾 -----------------------------
cd "$NAS_ME/workspace"
opencode serve --hostname 0.0.0.0 --port "$OPENCODE_PORT" &
OC=$!
term() { kill -TERM "$OC" 2>/dev/null || true; }
trap term TERM INT
set +e
wait "$OC"; RC=$?
set -e
kill "$REFRESHER" 2>/dev/null || true
$PODCTL db-checkpoint --db "$DB"
$PODCTL db-backup --db "$DB" --backup-dir "$BACKUP" --keep 3
$PODCTL owner-release --dir "$NAS_ME" --pod "$POD_NAME"
echo "opencode 已退出(rc=$RC),状态已落盘"
exit "$RC"
