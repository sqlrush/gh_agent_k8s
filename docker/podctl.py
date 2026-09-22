#!/usr/bin/env python3
"""Pod 生命周期里的确定性动作,entrypoint.sh 逐条调用。每个子命令对应 spec §6.2 的一项措施。

只用标准库:镜像里没有别的依赖也能跑;所有输出一句话说清发生了什么,不静默。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import pathlib
import re
import shutil
import sqlite3
import sys
import time
from typing import List, Mapping, Optional

OWNER_NAME = ".owner"
PROVIDER_DEFAULT = "model-service"


class ConfigError(ValueError):
    pass


def _utc_stamp(now: Optional[str] = None) -> str:
    return now or _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ---------------------------------------------------------------- 渲染配置

def strip_jsonc(src: str) -> str:
    """去掉 // 与 /* */ 注释和尾逗号(字符串内不动),够解析模板用。"""
    out: List[str] = []
    i, n, in_str = 0, len(src), False
    while i < n:
        ch = src[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        out.append(ch)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def render_opencode_config(env: Mapping[str, str]) -> dict:
    missing = [k for k in ("MODEL_BASE_URL", "MODEL_API_KEY", "MODEL_ID") if not env.get(k)]
    if missing:
        raise ConfigError("缺少环境变量:%s(模型服务地址、密钥、模型 id 由平台注入)" % "、".join(missing))
    name = env.get("MODEL_PROVIDER_NAME") or PROVIDER_DEFAULT
    model = env["MODEL_ID"]
    return {
        "$schema": "https://opencode.ai/config.json",
        "share": "disabled",                              # 绝不把对话分享到 opencode.ai
        "autoupdate": False,                              # 内网,且版本由镜像钉死
        "permission": {"external_directory": "allow"},    # 知识库 /nas/kb 在项目目录之外,模型要能读规则原文
        "provider": {
            name: {
                "npm": "@ai-sdk/openai-compatible",
                "name": name,
                "options": {"baseURL": env["MODEL_BASE_URL"], "apiKey": env["MODEL_API_KEY"]},
                "models": {model: {"name": model}},
            }
        },
        "model": "%s/%s" % (name, model),
    }


def render_gdaa_config(env: Mapping[str, str]) -> str:
    host = env.get("GRMP_API_HOST")
    if not host:
        raise ConfigError("缺少环境变量 GRMP_API_HOST(中间件地址由平台注入)")
    port = int(env.get("GRMP_API_PORT") or 8080)
    # 2026-09-18 中间件加固:配了 Appkey 就必须有 SM2 私钥,启动时就拒——拖到第一次请求会表现成
    # 中间件鉴权失败,排查方向被带到中间件那边。旋钮(GRMP_SIGN_*)由 skill 直接读环境变量,这里不落盘。
    appkey = env.get("GRMP_APPKEY", "")
    if appkey and not env.get("GRMP_SM2_PRIVATE_KEY"):
        raise ConfigError("配了 GRMP_APPKEY=%s 却没有 GRMP_SM2_PRIVATE_KEY(SM2 私钥由平台以 Secret grmp-sm2 注入)" % appkey)
    return ("# 由 podctl 在 Pod 启动时按环境变量生成;不要手工改,重启即覆盖。\n"
            "connection_mode: api\n"
            "api_connection:\n"
            "  - host: %s\n"
            "    port: %d\n"
            "    token_env: GRMP_AUTH_TOKEN\n" % (host, port)
            + ("    appkey_env: GRMP_APPKEY\n" if appkey else ""))


def _write_private(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# ---------------------------------------------------------------- .owner 独占

def _read_owner(path: pathlib.Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_owner(path: pathlib.Path, pod: str) -> None:
    _write_private(path, json.dumps({"pod": pod, "ts": time.time()}))


def owner_acquire(dir_: pathlib.Path, pod: str, stale_s: int = 90, wait_s: int = 55) -> bool:
    """同一个用户目录同一时刻只许一个 Pod 打开 opencode.db。新旧 Pod 交接时最多等 wait_s 秒。"""
    path = pathlib.Path(dir_) / OWNER_NAME
    deadline = time.time() + wait_s
    while True:
        cur = _read_owner(path)
        age = time.time() - float(cur.get("ts") or 0)
        if not cur or cur.get("pod") == pod or age >= stale_s:
            _write_owner(path, pod)
            how = "接管过期标记 %s" % cur.get("pod") if cur and cur.get("pod") != pod else "新建"
            print("独占标记: 本 Pod %s 持有(%s)" % (pod, how))
            return True
        if time.time() >= deadline:
            print("独占标记: 另一个 Pod %s 仍持有 %s(%d 秒前刷新),不启动" % (cur.get("pod"), path, int(age)))
            return False
        time.sleep(5)


def owner_refresh(dir_: pathlib.Path, pod: str) -> bool:
    path = pathlib.Path(dir_) / OWNER_NAME
    if _read_owner(path).get("pod") != pod:
        return False
    _write_owner(path, pod)
    return True


def owner_release(dir_: pathlib.Path, pod: str) -> None:
    path = pathlib.Path(dir_) / OWNER_NAME
    if _read_owner(path).get("pod") == pod:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------- SQLite

def _integrity_ok(db: pathlib.Path) -> bool:
    """**空文件不算通过。**

    SQLite 把 0 字节文件当成一个合法的空库,`integrity_check` 照样回 ok。
    如果拿它当备份恢复,结果是「用一个空库覆盖,并打印恢复成功」——
    用户看到的是正常启动加一个空的对话列表,没有任何报错。
    所以先看文件头:真库前 16 字节是 `SQLite format 3\\0`。
    """
    try:
        if db.stat().st_size == 0:
            return False
        with open(db, "rb") as f:
            if f.read(16) != b"SQLite format 3\x00":
                return False
    except OSError:
        return False
    try:
        con = sqlite3.connect(db)
        try:
            row = con.execute("pragma integrity_check").fetchone()
        finally:
            con.close()
    except sqlite3.DatabaseError:
        return False
    return bool(row) and row[0] == "ok"


# 备份文件名:opencode.db.<UTC 时间戳>,时间戳形如 20260102T000000Z。
# **不能用 `opencode.db.*` 通配。** 那个通配还会命中 SQLite 的侧车文件
# `opencode.db.<时间戳>-wal` / `-shm`,而 `-wal` 按字典序排在同名备份之后 ——
# 「取最新一份」于是取到侧车。侧车常是 0 字节,配合上面那个空库洞,
# 结果是「恢复成功」+ 历史全空。它同时还会把轮转的份数算错。
_BACKUP_RE = re.compile(r"^opencode\.db\.\d{8}T\d{6}Z$")


def _backups(bdir: pathlib.Path) -> List[pathlib.Path]:
    if not bdir.is_dir():
        return []
    return sorted(p for p in bdir.iterdir() if _BACKUP_RE.match(p.name))


def db_check(db: pathlib.Path, backup_dir: pathlib.Path) -> str:
    """返回 ok / absent / restored / reset。损坏时留证、恢复、报出——不静默。"""
    db = pathlib.Path(db)
    if not db.exists():
        print("数据库自检: %s 不存在,首次启动,opencode 将新建" % db)
        return "absent"
    if _integrity_ok(db):
        print("数据库自检: 完整性检查通过(%s)" % db)
        return "ok"
    corrupt = db.with_name("opencode.db.corrupt-%s" % _utc_stamp())
    for suffix in ("", "-wal", "-shm"):
        src = db.with_name(db.name + suffix)
        if src.exists():
            src.rename(corrupt.with_name(corrupt.name + suffix))
    print("数据库自检: %s 损坏,已改名为 %s 留证" % (db.name, corrupt.name))
    for cand in reversed(_backups(pathlib.Path(backup_dir))):
        if _integrity_ok(cand):
            shutil.copy2(cand, db)
            print("数据库自检: 已从备份 %s 恢复;该备份之后的对话丢失" % cand.name)
            return "restored"
        print("数据库自检: 备份 %s 也损坏,跳过" % cand.name)
    print("数据库自检: 没有可用备份,以空库启动;历史对话丢失,请从 NAS 快照找回")
    return "reset"


def db_checkpoint(db: pathlib.Path) -> None:
    db = pathlib.Path(db)
    if not db.exists():
        return
    con = sqlite3.connect(db)
    try:
        con.execute("pragma wal_checkpoint(TRUNCATE)")
    finally:
        con.close()
    print("WAL 已合并回主库(%s)" % db)


def db_backup(db: pathlib.Path, backup_dir: pathlib.Path, keep: int = 3, now: Optional[str] = None) -> pathlib.Path:
    db = pathlib.Path(db)
    bdir = pathlib.Path(backup_dir)
    bdir.mkdir(parents=True, exist_ok=True)
    target = bdir / ("opencode.db.%s" % _utc_stamp(now))
    src = sqlite3.connect(db)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)                       # 一致性快照,不依赖文件系统复制
    finally:
        dst.close()
        src.close()
    os.chmod(target, 0o600)
    for old in (_backups(bdir)[:-keep] if keep > 0 else []):
        old.unlink()
    print("退出备份: %s(保留最近 %d 份)" % (target.name, keep))
    return target


def log_prune(dir_: pathlib.Path, days: int = 7) -> List[pathlib.Path]:
    dir_ = pathlib.Path(dir_)
    if not dir_.is_dir():
        return []
    cutoff = time.time() - days * 86400
    removed = []
    for p in dir_.glob("*.log"):
        if p.stat().st_mtime < cutoff:
            p.unlink()
            removed.append(p)
    return removed


# ---------------------------------------------------------------- 报告只读端口

_REPORT_TYPES = {".json": "application/json; charset=utf-8",
                 ".jsonl": "application/x-ndjson; charset=utf-8",
                 ".html": "text/html; charset=utf-8"}


def make_reports_server(root: pathlib.Path, port: int):
    """本人 reports/ 目录的只读静态服务(4097),大盘经网关按工号读它。

    **只做三件事以外的一律 404**:GET、本目录之内、三种后缀。它和 opencode 同一个 Pod,
    但不共用端口 —— opencode 的 API 有口令,这个端口的鉴权靠网关只把本人的请求路由过来。
    """
    import http.server
    import posixpath
    import urllib.parse
    root = pathlib.Path(root).resolve()

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):          # 不打访问日志:每次刷新大盘都是几条请求
            pass

        def do_GET(self):
            rel = posixpath.normpath(urllib.parse.unquote(self.path.split("?", 1)[0])).lstrip("/")
            target = (root / rel).resolve() if rel else root
            ctype = _REPORT_TYPES.get(target.suffix)
            if (not rel or ctype is None or not str(target).startswith(str(root) + os.sep)
                    or not target.is_file()):
                return self._status(404)
            try:
                data = target.read_bytes()
            except OSError:
                return self._status(404)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _status(self, code: int):
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):
            self._status(405)

        do_PUT = do_DELETE = do_PATCH = do_POST

    srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), H)
    srv.daemon_threads = True
    return srv


# ---------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="podctl")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("render-opencode-config")
    p.add_argument("--out", required=True)
    p = sub.add_parser("render-gdaa-config")
    p.add_argument("--out", required=True)
    for name in ("owner-acquire", "owner-refresh", "owner-release"):
        p = sub.add_parser(name)
        p.add_argument("--dir", required=True)
        p.add_argument("--pod", required=True)
        if name == "owner-acquire":
            p.add_argument("--stale-s", type=int, default=90)
            p.add_argument("--wait-s", type=int, default=55)
    p = sub.add_parser("db-check")
    p.add_argument("--db", required=True)
    p.add_argument("--backup-dir", required=True)
    p = sub.add_parser("db-checkpoint")
    p.add_argument("--db", required=True)
    p = sub.add_parser("db-backup")
    p.add_argument("--db", required=True)
    p.add_argument("--backup-dir", required=True)
    p.add_argument("--keep", type=int, default=3)
    p = sub.add_parser("log-prune")
    p.add_argument("--dir", required=True)
    p.add_argument("--days", type=int, default=7)
    # 2026-09-21 本地态策略:启动 load、运行中定期 sync、收尾 finish。详见 statesync.py
    for name in ("state-load", "state-sync", "state-finish"):
        p = sub.add_parser(name)
        p.add_argument("--nas", required=True, help="$NAS_ME")
        p.add_argument("--local", required=True, help="本地态根目录(/data/state)")
    # 报告只读端口:大盘的数据口(entrypoint 在 opencode 之后拉起)
    p = sub.add_parser("serve-reports")
    p.add_argument("--dir", required=True)
    p.add_argument("--port", type=int, default=4097)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    a = _parser().parse_args(argv)
    try:
        if a.cmd == "render-opencode-config":
            _write_private(pathlib.Path(a.out),
                           json.dumps(render_opencode_config(os.environ), ensure_ascii=False, indent=2) + "\n")
        elif a.cmd == "render-gdaa-config":
            _write_private(pathlib.Path(a.out), render_gdaa_config(os.environ))
        elif a.cmd == "owner-acquire":
            return 0 if owner_acquire(pathlib.Path(a.dir), a.pod, a.stale_s, a.wait_s) else 1
        elif a.cmd == "owner-refresh":
            return 0 if owner_refresh(pathlib.Path(a.dir), a.pod) else 1
        elif a.cmd == "owner-release":
            owner_release(pathlib.Path(a.dir), a.pod)
        elif a.cmd == "db-check":
            db_check(pathlib.Path(a.db), pathlib.Path(a.backup_dir))
        elif a.cmd == "db-checkpoint":
            db_checkpoint(pathlib.Path(a.db))
        elif a.cmd == "db-backup":
            if pathlib.Path(a.db).exists():
                db_backup(pathlib.Path(a.db), pathlib.Path(a.backup_dir), a.keep)
        elif a.cmd == "log-prune":
            n = len(log_prune(pathlib.Path(a.dir), a.days))
            print("日志清理: 删除 %d 个超过 %d 天的 .log" % (n, a.days))
        elif a.cmd == "serve-reports":
            make_reports_server(pathlib.Path(a.dir), a.port).serve_forever()
        elif a.cmd in ("state-load", "state-sync", "state-finish"):
            import statesync
            lay = statesync.Layout(nas=pathlib.Path(a.nas), local=pathlib.Path(a.local))
            if a.cmd == "state-load":
                statesync.load(lay)
                statesync.mark_dirty(lay)     # load 之后才打标记:load 失败时不要留下假痕迹
            elif a.cmd == "state-sync":
                print(statesync.sync(lay).line())
            else:
                statesync.finish(lay)
    except ConfigError as exc:
        print("配置错误: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
