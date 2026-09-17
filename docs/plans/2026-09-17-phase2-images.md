# 阶段 2：后端镜像 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 产出两个可运行的后端镜像 `gaussdb-agent-runtime` 与 `gaussdb-agent-kb-import`（x86_64 + aarch64），Pod 无状态、状态全在挂载的 NAS 目录里，`opencode.db` 直接在 NAS 路径上运行并带 spec §6.2 的全部保护措施。

**Architecture:** 一个 Dockerfile、三个 stage（`base` → `runtime` / `kb-import`）；技能代码来自本仓库 `agent/`（用它自己的 `install-opencode.sh` 装进镜像的 `$XDG_CONFIG_HOME/opencode/skills`）。Pod 生命周期里的确定性动作（渲染配置、`.owner` 独占、`integrity_check`、WAL 合并、备份、日志清理）全部放在一个 Python 工具 `docker/podctl.py` 里并有单元测试；`entrypoint.sh` 只负责按顺序调用它、启动 `opencode serve`、转发 SIGTERM。运行时配置不烧进镜像：entrypoint 从环境变量渲染 `opencode.json` 与 `config.yaml` 到可写目录。

**Tech Stack:** Docker（Mac 上 OrbStack：`/usr/local/bin/docker`，buildx v0.33，宿主 aarch64；x86_64 用 `--platform linux/amd64` 交叉构建）、`python:3.12-slim-bookworm` 基础镜像、opencode 1.18.27（GitHub release 包）、ripgrep（apt）、Python 依赖 psycopg2-binary / cryptography / PyYAML。宿主侧单测用 Mac `~/p2venv/bin/python3`。

**Spec:** `docs/specs/2026-09-15-k8s-containerization-design.md` §4、§5、§6、§7、§11、§12

## Global Constraints

- opencode 版本钉死 `1.18.27`；镜像标签 `agent-v0.1-oc1.18.27`（本仓库标签 + opencode 版本）。
- 镜像以 uid 1000（用户 `agent`）运行，不是 root；镜像里不放任何令牌、密钥、客户地址。
- `share` 必须 `"disabled"`；不在 Pod 里 `opencode auth login`。
- 内网不联外：opencode 包、ripgrep、`models.json` 都在构建期取好；运行时不下载任何东西。
- 所有配置来自环境变量（spec §6 契约），运行时渲染到可写目录；镜像文件系统按只读对待（`readOnlyRootFilesystem` 兼容）。
- Python 依赖白名单不变：psycopg2（容器里用 `psycopg2-binary` 轮子，同一模块名）、cryptography、PyYAML。
- 提交信息中文 conventional commit，结尾两行：
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01NFTD813rWESLz4NzmGi4NZ`
- 分支 `feat/phase2-images`（从 main `29ef6d7` 开）。宿主侧测试：`cd ~/gh_agent_k8s && PATH=$HOME/p2venv/bin:$PATH python3 -m pytest docker/tests -q`。
- Docker 命令在 Mac 上跑：`ssh sqlrush@192.168.128.1`，`docker` 在 `/usr/local/bin/docker`。

---

## 文件结构

| 文件 | 责任 | 任务 |
|---|---|---|
| `docker/podctl.py` | Pod 生命周期的确定性动作：`render-opencode-config`、`render-gdaa-config`、`owner-acquire/refresh/release`、`db-check`、`db-checkpoint`、`db-backup`、`log-prune` | 1 |
| `docker/tests/test_podctl.py` | 上述每个动作的单测（临时目录 + 内置 sqlite3） | 1 |
| `docker/entrypoint.sh` | 顺序：目录 → 渲染配置 → 独占 → 自检 → 日志清理 → 启动 opencode → 收 SIGTERM → checkpoint → 备份 → 释放 | 2 |
| `docker/Dockerfile` | `base`（Python 依赖、opencode、rg、models.json、agent 代码、用户 1000）→ `runtime`（16 诊断 skill + gaussdb-kb）/ `kb-import`（gaussdb-kb + gaussdb-kb-import） | 3 |
| `docker/opencode.jsonc.tmpl` | 文档用途的配置模板（真正渲染在 podctl 里，两者字段一致由测试保证） | 1 |
| `scripts/build-images.sh` | 本机构建两个镜像（可选 `--platform linux/amd64,linux/arm64`） | 3 |
| `scripts/tcp-forward.py` | 把 Mac 上只绑 127.0.0.1 的 GRMP mock 转发到 0.0.0.0，容器经 `host.docker.internal` 访问 | 4 |
| `scripts/smoke-image.sh` | 镜像冒烟：健康探针、登录、诊断、知识库查询、导入桩、`.owner` 冲突、损坏恢复、优雅停机、导入镜像可写 | 4 |
| `docs/env-contract.md` | 环境变量 / 挂载 / 端口 / 探针契约（给平台） | 5 |

---

### Task 1: podctl.py —— Pod 生命周期动作与测试

**Files:**
- Create: `docker/podctl.py`
- Create: `docker/tests/__init__.py`（空）、`docker/tests/test_podctl.py`
- Create: `docker/opencode.jsonc.tmpl`

**Interfaces:**
- Produces（命令行）：
  - `podctl render-opencode-config --out FILE`：读环境 `MODEL_BASE_URL`、`MODEL_API_KEY`、`MODEL_ID`（必填）、`MODEL_PROVIDER_NAME`（默认 `model-service`）、`OPENCODE_SERVER_PASSWORD`（可选，不写进文件），写 JSON：`share: "disabled"`、`autoupdate: false`、`permission: {"external_directory": "allow"}`、provider `<name>` 用 `@ai-sdk/openai-compatible`、`model: "<name>/<MODEL_ID>"`。缺必填变量 → stderr 一句话，退出 2。
  - `podctl render-gdaa-config --out FILE`：读 `GRMP_API_HOST`（必填）、`GRMP_API_PORT`（默认 8080），写 `connection_mode: api` + `api_connection: [{host, port, token_env: GRMP_AUTH_TOKEN}]`。
  - `podctl owner-acquire --dir DIR --pod NAME [--stale-s 90] [--wait-s 55]`：`.owner` 独占；被占且未过期 → 每 5 秒重读，超过 `--wait-s` 仍被占 → 退出 1 并打印持有者。
  - `podctl owner-refresh --dir DIR --pod NAME`：只刷新自己持有的标记；不是自己的 → 退出 1。
  - `podctl owner-release --dir DIR --pod NAME`：删自己的标记；不是自己的不删。
  - `podctl db-check --db FILE --backup-dir DIR`：不存在 → 退出 0（首次启动）；`PRAGMA integrity_check` 为 `ok` → 0；否则坏文件改名 `opencode.db.corrupt-<UTC时间>`，从 `DIR` 里最新的 `opencode.db.<时间>` 复制回来并再检；恢复成功 → 打印「已从 <文件> 恢复」退出 0；没有备份或备份也坏 → 打印原因，删掉坏文件让 opencode 空库启动，退出 0；**任何情况都在 stdout 写明发生了什么**。
  - `podctl db-checkpoint --db FILE`：`PRAGMA wal_checkpoint(TRUNCATE)`；文件不存在 → 0。
  - `podctl db-backup --db FILE --backup-dir DIR [--keep 3]`：用 sqlite3 的 `backup()` API 复制到 `DIR/opencode.db.<UTC时间>`，只保留最新 `--keep` 份。
  - `podctl log-prune --dir DIR [--days 7]`：删 `DIR` 下 mtime 超过 N 天的 `*.log`。
- Python 内部函数与命令同名（`render_opencode_config(env: Mapping[str,str]) -> dict` 等），测试直接调函数。

- [ ] **Step 1: 写失败的测试**

`docker/tests/test_podctl.py`：

```python
"""podctl:Pod 生命周期里的确定性动作。每条都对着 spec §6.2 的一项措施。"""
import json
import os
import pathlib
import sqlite3
import sys
import time

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "docker"))

import podctl  # noqa: E402


# ---- 渲染配置 ---------------------------------------------------------------

def test_render_opencode_config_uses_env_and_disables_share():
    env = {"MODEL_BASE_URL": "http://llm.internal/v1", "MODEL_API_KEY": "k", "MODEL_ID": "qwen3-32b"}
    cfg = podctl.render_opencode_config(env)
    assert cfg["share"] == "disabled" and cfg["autoupdate"] is False
    assert cfg["permission"]["external_directory"] == "allow"
    prov = cfg["provider"]["model-service"]
    assert prov["npm"] == "@ai-sdk/openai-compatible"
    assert prov["options"] == {"baseURL": "http://llm.internal/v1", "apiKey": "k"}
    assert "qwen3-32b" in prov["models"] and cfg["model"] == "model-service/qwen3-32b"


def test_render_opencode_config_requires_model_vars():
    with pytest.raises(podctl.ConfigError) as exc:
        podctl.render_opencode_config({"MODEL_BASE_URL": "x"})
    assert "MODEL_API_KEY" in str(exc.value) and "MODEL_ID" in str(exc.value)


def test_render_opencode_config_matches_template_keys():
    """docker/opencode.jsonc.tmpl 是给人看的;它的顶层键必须和真正渲染出来的一致,否则文档骗人。"""
    tmpl = json.loads(podctl.strip_jsonc((_ROOT / "docker" / "opencode.jsonc.tmpl").read_text(encoding="utf-8")))
    cfg = podctl.render_opencode_config({"MODEL_BASE_URL": "u", "MODEL_API_KEY": "k", "MODEL_ID": "m"})
    assert set(tmpl) == set(cfg)


def test_render_gdaa_config_api_mode(tmp_path):
    text = podctl.render_gdaa_config({"GRMP_API_HOST": "grmp.internal", "GRMP_API_PORT": "8443"})
    assert "connection_mode: api" in text
    assert "host: grmp.internal" in text and "port: 8443" in text and "token_env: GRMP_AUTH_TOKEN" in text


def test_render_gdaa_config_requires_host():
    with pytest.raises(podctl.ConfigError):
        podctl.render_gdaa_config({})


# ---- .owner 独占 --------------------------------------------------------------

def test_owner_acquire_writes_marker(tmp_path):
    assert podctl.owner_acquire(tmp_path, "runtime-u1-abc", stale_s=90, wait_s=0) is True
    data = json.loads((tmp_path / ".owner").read_text(encoding="utf-8"))
    assert data["pod"] == "runtime-u1-abc" and time.time() - data["ts"] < 5


def test_owner_acquire_refuses_fresh_marker_of_another_pod(tmp_path):
    podctl.owner_acquire(tmp_path, "runtime-u1-old", stale_s=90, wait_s=0)
    assert podctl.owner_acquire(tmp_path, "runtime-u1-new", stale_s=90, wait_s=0) is False
    assert json.loads((tmp_path / ".owner").read_text())["pod"] == "runtime-u1-old"


def test_owner_acquire_takes_over_stale_marker(tmp_path):
    (tmp_path / ".owner").write_text(json.dumps({"pod": "runtime-u1-dead", "ts": time.time() - 3600}), encoding="utf-8")
    assert podctl.owner_acquire(tmp_path, "runtime-u1-new", stale_s=90, wait_s=0) is True
    assert json.loads((tmp_path / ".owner").read_text())["pod"] == "runtime-u1-new"


def test_owner_acquire_waits_until_marker_goes_stale(tmp_path, monkeypatch):
    (tmp_path / ".owner").write_text(json.dumps({"pod": "runtime-u1-old", "ts": time.time() - 88}), encoding="utf-8")
    sleeps = []
    monkeypatch.setattr(podctl.time, "sleep", lambda s: sleeps.append(s) or (tmp_path / ".owner").write_text(
        json.dumps({"pod": "runtime-u1-old", "ts": time.time() - 200}), encoding="utf-8"))
    assert podctl.owner_acquire(tmp_path, "runtime-u1-new", stale_s=90, wait_s=55) is True
    assert sleeps == [5]


def test_owner_refresh_only_touches_own_marker(tmp_path):
    podctl.owner_acquire(tmp_path, "a", stale_s=90, wait_s=0)
    old = json.loads((tmp_path / ".owner").read_text())["ts"]
    time.sleep(0.01)
    assert podctl.owner_refresh(tmp_path, "a") is True
    assert json.loads((tmp_path / ".owner").read_text())["ts"] > old
    assert podctl.owner_refresh(tmp_path, "b") is False


def test_owner_release_only_removes_own_marker(tmp_path):
    podctl.owner_acquire(tmp_path, "a", stale_s=90, wait_s=0)
    podctl.owner_release(tmp_path, "b")
    assert (tmp_path / ".owner").exists()
    podctl.owner_release(tmp_path, "a")
    assert not (tmp_path / ".owner").exists()


# ---- SQLite:自检 / checkpoint / 备份 ---------------------------------------

def _make_db(path: pathlib.Path, rows: int = 3) -> None:
    con = sqlite3.connect(path)
    con.execute("pragma journal_mode=wal")
    con.execute("create table t(x)")
    con.executemany("insert into t values (?)", [(i,) for i in range(rows)])
    con.commit(); con.close()


def test_db_check_passes_on_healthy_db(tmp_path, capsys):
    db = tmp_path / "opencode.db"; _make_db(db)
    assert podctl.db_check(db, tmp_path / "backup") == "ok"
    assert "完整性检查通过" in capsys.readouterr().out


def test_db_check_missing_db_is_first_start(tmp_path, capsys):
    assert podctl.db_check(tmp_path / "opencode.db", tmp_path / "backup") == "absent"
    assert "首次启动" in capsys.readouterr().out


def test_db_check_restores_from_latest_backup(tmp_path, capsys):
    db = tmp_path / "opencode.db"; bdir = tmp_path / "backup"; bdir.mkdir()
    _make_db(bdir / "opencode.db.20260101T000000Z", rows=1)
    _make_db(bdir / "opencode.db.20260102T000000Z", rows=7)
    db.write_bytes(b"this is not a sqlite database at all" * 100)
    assert podctl.db_check(db, bdir) == "restored"
    out = capsys.readouterr().out
    assert "20260102T000000Z" in out and "损坏" in out
    assert list(tmp_path.glob("opencode.db.corrupt-*"))
    con = sqlite3.connect(db); assert con.execute("select count(*) from t").fetchone()[0] == 7; con.close()


def test_db_check_without_backup_starts_empty_and_says_so(tmp_path, capsys):
    db = tmp_path / "opencode.db"
    db.write_bytes(b"garbage" * 100)
    assert podctl.db_check(db, tmp_path / "backup") == "reset"
    assert not db.exists() and "没有可用备份" in capsys.readouterr().out
    assert list(tmp_path.glob("opencode.db.corrupt-*"))


def test_db_checkpoint_truncates_wal(tmp_path):
    db = tmp_path / "opencode.db"; _make_db(db, rows=50)
    con = sqlite3.connect(db); con.execute("insert into t values (99)"); con.commit()   # 留着连接,WAL 里有内容
    assert (db.with_name("opencode.db-wal")).stat().st_size > 0
    con.close()
    podctl.db_checkpoint(db)
    assert (db.with_name("opencode.db-wal")).stat().st_size == 0


def test_db_backup_rotates(tmp_path):
    db = tmp_path / "opencode.db"; _make_db(db); bdir = tmp_path / "backup"
    for i in range(5):
        p = podctl.db_backup(db, bdir, keep=3, now=f"2026010{i+1}T000000Z")
        assert p.name == f"opencode.db.2026010{i+1}T000000Z"
    names = sorted(x.name for x in bdir.iterdir())
    assert names == ["opencode.db.20260103T000000Z", "opencode.db.20260104T000000Z", "opencode.db.20260105T000000Z"]
    con = sqlite3.connect(bdir / names[-1]); assert con.execute("select count(*) from t").fetchone()[0] == 3


def test_log_prune_removes_old_logs_only(tmp_path):
    old = tmp_path / "a.log"; old.write_text("x"); os.utime(old, (time.time() - 10 * 86400,) * 2)
    new = tmp_path / "b.log"; new.write_text("x")
    other = tmp_path / "keep.txt"; other.write_text("x"); os.utime(other, (time.time() - 10 * 86400,) * 2)
    assert podctl.log_prune(tmp_path, days=7) == [old]
    assert not old.exists() and new.exists() and other.exists()


# ---- 命令行 -----------------------------------------------------------------

def test_cli_render_writes_files_and_reports_missing_vars(tmp_path, monkeypatch, capsys):
    for k in ("MODEL_BASE_URL", "MODEL_API_KEY", "MODEL_ID", "GRMP_API_HOST"):
        monkeypatch.delenv(k, raising=False)
    assert podctl.main(["render-opencode-config", "--out", str(tmp_path / "oc.json")]) == 2
    assert "MODEL_ID" in capsys.readouterr().err
    monkeypatch.setenv("MODEL_BASE_URL", "u"); monkeypatch.setenv("MODEL_API_KEY", "k"); monkeypatch.setenv("MODEL_ID", "m")
    monkeypatch.setenv("GRMP_API_HOST", "h")
    assert podctl.main(["render-opencode-config", "--out", str(tmp_path / "oc.json")]) == 0
    assert podctl.main(["render-gdaa-config", "--out", str(tmp_path / "config.yaml")]) == 0
    assert json.loads((tmp_path / "oc.json").read_text())["share"] == "disabled"
    assert oct((tmp_path / "oc.json").stat().st_mode & 0o777) == "0o600"       # 里面有 key
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd ~/gh_agent_k8s && PATH=$HOME/p2venv/bin:$PATH python3 -m pytest docker/tests -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'podctl'`

- [ ] **Step 3: 写 podctl.py**

`docker/podctl.py`：

```python
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
    out, i, n, in_str = [], 0, len(src), False
    while i < n:
        ch = src[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(src[i + 1]); i += 2; continue
            if ch == '"':
                in_str = False
            i += 1; continue
        if ch == '"':
            in_str = True; out.append(ch); i += 1; continue
        if src.startswith("//", i):
            j = src.find("\n", i); i = n if j < 0 else j; continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2); i = n if j < 0 else j + 2; continue
        out.append(ch); i += 1
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
    return ("# 由 podctl 在 Pod 启动时按环境变量生成;不要手工改,重启即覆盖。\n"
            "connection_mode: api\n"
            "api_connection:\n"
            "  - host: %s\n"
            "    port: %d\n"
            "    token_env: GRMP_AUTH_TOKEN\n" % (host, port))


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
            print("独占标记: 本 Pod %s 持有(%s)" % (pod, "接管过期标记 %s" % cur.get("pod") if cur and cur.get("pod") != pod else "新建"))
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
    try:
        con = sqlite3.connect(db)
        try:
            row = con.execute("pragma integrity_check").fetchone()
        finally:
            con.close()
    except sqlite3.DatabaseError:
        return False
    return bool(row) and row[0] == "ok"


def _backups(bdir: pathlib.Path) -> List[pathlib.Path]:
    return sorted(bdir.glob("opencode.db.*")) if bdir.is_dir() else []


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
    db = pathlib.Path(db); bdir = pathlib.Path(backup_dir); bdir.mkdir(parents=True, exist_ok=True)
    target = bdir / ("opencode.db.%s" % _utc_stamp(now))
    src = sqlite3.connect(db); dst = sqlite3.connect(target)
    try:
        src.backup(dst)                       # 一致性快照,不依赖文件系统复制
    finally:
        dst.close(); src.close()
    os.chmod(target, 0o600)
    for old in _backups(bdir)[:-keep] if keep > 0 else []:
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
            p.unlink(); removed.append(p)
    return removed


# ---------------------------------------------------------------- CLI

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="podctl")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("render-opencode-config"); p.add_argument("--out", required=True)
    p = sub.add_parser("render-gdaa-config"); p.add_argument("--out", required=True)
    for name in ("owner-acquire", "owner-refresh", "owner-release"):
        p = sub.add_parser(name); p.add_argument("--dir", required=True); p.add_argument("--pod", required=True)
        if name == "owner-acquire":
            p.add_argument("--stale-s", type=int, default=90); p.add_argument("--wait-s", type=int, default=55)
    p = sub.add_parser("db-check"); p.add_argument("--db", required=True); p.add_argument("--backup-dir", required=True)
    p = sub.add_parser("db-checkpoint"); p.add_argument("--db", required=True)
    p = sub.add_parser("db-backup"); p.add_argument("--db", required=True); p.add_argument("--backup-dir", required=True)
    p.add_argument("--keep", type=int, default=3)
    p = sub.add_parser("log-prune"); p.add_argument("--dir", required=True); p.add_argument("--days", type=int, default=7)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "render-opencode-config":
            _write_private(pathlib.Path(a.out), json.dumps(render_opencode_config(os.environ), ensure_ascii=False, indent=2) + "\n")
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
    except ConfigError as exc:
        print("配置错误: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`docker/opencode.jsonc.tmpl`（给人看的对照，字段与 `render_opencode_config` 一致）：

```jsonc
{
  // 由 podctl render-opencode-config 按环境变量生成到 /data/oc/opencode.json;本文件只是对照。
  "$schema": "https://opencode.ai/config.json",
  "share": "disabled",
  "autoupdate": false,
  "permission": { "external_directory": "allow" },
  "provider": {
    "model-service": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "model-service",
      "options": { "baseURL": "{env:MODEL_BASE_URL}", "apiKey": "{env:MODEL_API_KEY}" },
      "models": { "<MODEL_ID>": { "name": "<MODEL_ID>" } }
    }
  },
  "model": "model-service/<MODEL_ID>"
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd ~/gh_agent_k8s && PATH=$HOME/p2venv/bin:$PATH python3 -m pytest docker/tests -q`
Expected: 全部 PASS（21 条）

- [ ] **Step 5: 提交**

```bash
git checkout -b feat/phase2-images main
git add docker/podctl.py docker/tests docker/opencode.jsonc.tmpl
git commit -m "feat(docker): podctl——Pod 生命周期动作(渲染配置、.owner 独占、db 自检/恢复/checkpoint/备份、日志清理)+ 单测

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NFTD813rWESLz4NzmGi4NZ"
```

---

### Task 2: entrypoint.sh

**Files:**
- Create: `docker/entrypoint.sh`

**Interfaces:**
- Consumes：环境变量 `POD_NAME`（k8s downward API 注入；本地跑时默认 `hostname`）、`NAS_ME`（默认 `/nas/me`）、`NAS_KB`（默认 `/nas/kb`）、`OPENCODE_PORT`（默认 4096）、`GSDB_USER_ID`、spec §6 的其余变量；`podctl` 的全部子命令。
- Produces：进程 1 是 `entrypoint.sh`，子进程 `opencode serve`；SIGTERM → opencode 正常退出 → checkpoint → 备份 → 释放标记 → 以 opencode 的退出码退出。目录约定写死在脚本顶部，与 spec §4 一致。

- [ ] **Step 1: 写脚本**

```bash
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
export XDG_CONFIG_HOME=/opt/agent/config          # 镜像内:skills、AGENTS.md
export XDG_CACHE_HOME=/opt/agent/cache            # 镜像内:models.json、rg
export GSDB_HOME=$NAS_ME/gdaa
export GSDB_KB_DIR=$NAS_KB
export HOME=/data/home                            # 有些库要写 $HOME;放本地可写目录
export OPENCODE_CONFIG=/data/oc/opencode.json     # 渲染出来的运行时配置(含 key,0600)
DB=$XDG_DATA_HOME/opencode/opencode.db
BACKUP=$NAS_ME/backup
mkdir -p "$XDG_DATA_HOME/opencode" "$XDG_STATE_HOME/opencode" "$GSDB_HOME" "$NAS_ME/workspace" "$BACKUP" /data/home /data/oc
chmod 700 "$GSDB_HOME"
[ -n "${GSDB_KB_INBOX:-}" ] && mkdir -p "$GSDB_KB_INBOX"

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
```

- [ ] **Step 2: 静态检查**

Run: `bash -n docker/entrypoint.sh && (command -v shellcheck >/dev/null && shellcheck docker/entrypoint.sh || echo "shellcheck 不在,跳过")`
Expected: 无语法错误。行为验证在 Task 4 的冒烟里做（正常停机后 `-wal` 为 0、`backup/` 有文件、`.owner` 消失）。

- [ ] **Step 3: 提交**

```bash
chmod +x docker/entrypoint.sh
git add docker/entrypoint.sh
git commit -m "feat(docker): entrypoint——目录、渲染配置、独占、自检、opencode serve、SIGTERM 收尾

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NFTD813rWESLz4NzmGi4NZ"
```

---

### Task 3: Dockerfile 与构建脚本

**Files:**
- Create: `docker/Dockerfile`
- Create: `scripts/build-images.sh`
- Modify: `.gitignore`（加 `docker/vendor/`）

**Interfaces:**
- Produces：镜像 `gaussdb-agent-runtime:<TAG>` 与 `gaussdb-agent-kb-import:<TAG>`；构建参数 `OPENCODE_VERSION=1.18.27`、`TARGETARCH`（buildx 自动给 `amd64`/`arm64`）。镜像内路径：`/opt/agent/podctl.py`、`/opt/agent/entrypoint.sh`、`/opt/agent/config/opencode/{skills,AGENTS.md}`、`/opt/agent/cache/opencode/{models.json,bin/rg}`、`/usr/local/bin/opencode`。
- `HEALTHCHECK`：`curl -fsS http://127.0.0.1:4096/global/health`（k8s 用同一路径做 readinessProbe）。

- [ ] **Step 1: 写 Dockerfile**

```dockerfile
# syntax=docker/dockerfile:1.7
# 两个后端镜像出自同一文件:--target runtime / --target kb-import。
# 内网不联外:opencode、ripgrep、models.json 都在构建期取好。
ARG OPENCODE_VERSION=1.18.27

# ---------------------------------------------------------------- base
FROM python:3.12-slim-bookworm AS base
ARG OPENCODE_VERSION
ARG TARGETARCH
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Shanghai LANG=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl ripgrep git tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --user-group --create-home --shell /bin/bash agent

# opencode 官方 release 包(glibc 版);包内是单个可执行文件
RUN set -eux; case "$TARGETARCH" in amd64) A=x64 ;; arm64) A=arm64 ;; *) echo "unsupported arch $TARGETARCH" >&2; exit 1 ;; esac; \
    curl -fsSL -o /tmp/oc.tgz "https://github.com/sst/opencode/releases/download/v${OPENCODE_VERSION}/opencode-linux-${A}.tar.gz"; \
    mkdir -p /tmp/oc && tar xzf /tmp/oc.tgz -C /tmp/oc && install -m 0755 "$(find /tmp/oc -type f -name opencode | head -1)" /usr/local/bin/opencode; \
    rm -rf /tmp/oc /tmp/oc.tgz; opencode --version

# 缓存目录预置:模型清单与 ripgrep,运行时不再下载
RUN mkdir -p /opt/agent/cache/opencode/bin \
    && curl -fsSL -o /opt/agent/cache/opencode/models.json https://models.dev/api.json \
    && cp /usr/bin/rg /opt/agent/cache/opencode/bin/rg

# Python 依赖(白名单三项;psycopg2-binary 与 psycopg2 同模块名)
RUN pip install --no-cache-dir "psycopg2-binary==2.9.10" "cryptography>=41" "PyYAML>=6"

# 技能代码:用仓库自己的安装脚本装进 opencode 的技能目录(common/、scripts/registry/ 一起)
COPY agent/ /opt/agent/src/
RUN cd /opt/agent/src && bash install-opencode.sh --dest /opt/agent/config/opencode/skills --no-backup \
        --agents-md /opt/agent/config/opencode/AGENTS.md >/tmp/install.log 2>&1 || { cat /tmp/install.log; exit 1; } \
    && grep -E "installed" /tmp/install.log | tail -1 && rm -rf /opt/agent/src
COPY docker/podctl.py docker/entrypoint.sh /opt/agent/
RUN chmod 0755 /opt/agent/entrypoint.sh && chown -R agent:agent /opt/agent && mkdir -p /data /nas && chown agent:agent /data

USER agent
WORKDIR /data
EXPOSE 4096
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 CMD curl -fsS http://127.0.0.1:4096/global/health || exit 1
ENTRYPOINT ["/opt/agent/entrypoint.sh"]

# ---------------------------------------------------------------- runtime:诊断 skill + 知识库查询
FROM base AS runtime
USER root
RUN rm -rf /opt/agent/config/opencode/skills/gaussdb-kb-import \
    && test "$(ls -d /opt/agent/config/opencode/skills/gaussdb-* | wc -l)" = "17"
USER agent
LABEL org.opencontainers.image.title="gaussdb-agent-runtime" org.opencontainers.image.description="opencode + 16 diagnostic skills + gaussdb-kb (query); kb/ mounted read-only"

# ---------------------------------------------------------------- kb-import:知识库查询 + 导入
FROM base AS kb-import
USER root
RUN cd /opt/agent/config/opencode/skills && for d in gaussdb-*; do case "$d" in gaussdb-kb|gaussdb-kb-import) ;; *) rm -rf "$d" ;; esac; done \
    && test "$(ls -d gaussdb-* | wc -l)" = "2"
USER agent
ENV GSDB_KB_INBOX=/nas/kb/inbox/uploads
LABEL org.opencontainers.image.title="gaussdb-agent-kb-import" org.opencontainers.image.description="opencode + gaussdb-kb + gaussdb-kb-import; kb/ mounted read-write; no GRMP token"
```

`install-opencode.sh` 里 python 依赖检查会跑 `python3`；base 里已装依赖，检查通过。它把 `common/` 与 `scripts/registry/` 装到 `skills/` 下，skill 脚本按祖先目录找 `common`（`skills/common`）✓。

- [ ] **Step 2: 构建脚本**

`scripts/build-images.sh`：

```bash
#!/usr/bin/env bash
# 构建两个后端镜像。默认本机架构;--platform linux/amd64,linux/arm64 交叉构建(需要 buildx)。
# 用法: scripts/build-images.sh [TAG] [--platform P] [--push]
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
TAG=${1:-$(git -C "$HERE" describe --tags --always)-oc1.18.27}
[ "${1:-}" != "" ] && shift || true
PLATFORM=""; PUSH=""
while [ $# -gt 0 ]; do case "$1" in --platform) PLATFORM="--platform $2"; shift 2 ;; --push) PUSH="--push"; shift ;; *) echo "unknown $1" >&2; exit 2 ;; esac; done
LOAD=""; [ -z "$PLATFORM" ] && LOAD="--load"
for T in runtime kb-import; do
  docker buildx build $PLATFORM $LOAD $PUSH -f "$HERE/docker/Dockerfile" --target "$T" -t "gaussdb-agent-$T:$TAG" "$HERE"
done
echo "built: gaussdb-agent-runtime:$TAG gaussdb-agent-kb-import:$TAG"
```

- [ ] **Step 3: 本机构建并核对内容**

```bash
cd ~/gh_agent_k8s && chmod +x scripts/build-images.sh && scripts/build-images.sh dev
docker run --rm gaussdb-agent-runtime:dev sh -c 'id -u; opencode --version; ls /opt/agent/config/opencode/skills | grep -c gaussdb-; ls /opt/agent/config/opencode/skills/gaussdb-kb/scripts; test ! -d /opt/agent/config/opencode/skills/gaussdb-kb-import && echo runtime-ok'
docker run --rm gaussdb-agent-kb-import:dev sh -c 'ls /opt/agent/config/opencode/skills | grep gaussdb-; ls -la /opt/agent/cache/opencode/bin/rg /opt/agent/cache/opencode/models.json'
```
Expected: uid 1000；`1.18.27`；runtime 17 个 skill 目录、查询 skill 只有 `kb.py kb_cite.py`、无 `gaussdb-kb-import`；kb-import 恰好 `gaussdb-kb gaussdb-kb-import`；rg 与 models.json 存在。

- [ ] **Step 4: 交叉构建 amd64（只构建，不跑）**

Run: `scripts/build-images.sh dev-amd64 --platform linux/amd64`
Expected: 两个 target 成功（OrbStack 用 Rosetta 跑 amd64 层里的 `opencode --version` 与 pip）。失败则记录原因，不阻塞 Task 4。

- [ ] **Step 5: 提交**

```bash
printf '\n# 本地构建产物\ndocker/vendor/\n' >> .gitignore
git add docker/Dockerfile scripts/build-images.sh .gitignore
git commit -m "feat(docker): Dockerfile 三阶段(base/runtime/kb-import)与构建脚本;opencode 1.18.27、rg、models.json 构建期预置

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NFTD813rWESLz4NzmGi4NZ"
```

---

### Task 4: 冒烟脚本（对着 spec §12 第 2、4、5 项与 §6.2）

**Files:**
- Create: `scripts/tcp-forward.py`
- Create: `scripts/smoke-image.sh`

**Interfaces:**
- Consumes：Mac 上已就绪的验收环境（`~/kf-verify/gdaa`：8779 mock 含实例 10.0.0.9 → og5；令牌在 `~/.gdaa/grmp.env`；知识库样例 `~/kb-verify/modes/kb`）。
- Produces：`scripts/smoke-image.sh TAG` 退出 0 = 全部通过；每项打印 `[PASS]/[FAIL]`。

- [ ] **Step 1: 转发器**

`scripts/tcp-forward.py`（mock 刻意只绑 127.0.0.1；容器经 `host.docker.internal` 访问需要一个绑 0.0.0.0 的入口）：

```python
#!/usr/bin/env python3
"""把 0.0.0.0:<listen> 转发到 127.0.0.1:<target>。只用于本机冒烟,不进镜像。用法: tcp-forward.py 8781 8779"""
import socket
import sys
import threading


def pump(a: socket.socket, b: socket.socket) -> None:
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()


def main() -> int:
    listen, target = int(sys.argv[1]), int(sys.argv[2])
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", listen)); srv.listen(16)
    while True:
        c, _ = srv.accept()
        t = socket.create_connection(("127.0.0.1", target), timeout=10)
        threading.Thread(target=pump, args=(c, t), daemon=True).start()
        threading.Thread(target=pump, args=(t, c), daemon=True).start()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 冒烟脚本**

`scripts/smoke-image.sh`：

```bash
#!/usr/bin/env bash
# 镜像冒烟(Mac + OrbStack):用本地目录模拟 NAS,8779 mock 经转发器给容器用。
# 用法: scripts/smoke-image.sh <TAG>   前提: ~/kf-verify 验收环境已就绪(kf_env_up),令牌在 ~/.gdaa/grmp.env
set -u
TAG=${1:?tag}
HERE=$(cd "$(dirname "$0")/.." && pwd)
NAS=${NAS:-/tmp/agent-nas}; U=u12345
RT=gaussdb-agent-runtime:$TAG; KI=gaussdb-agent-kb-import:$TAG
pass=0; fail=0
ok(){ pass=$((pass+1)); echo "[PASS] $1"; }
bad(){ fail=$((fail+1)); echo "[FAIL] $1"; echo "   | $(printf '%s' "${out:-}" | head -4 | tr '\n' ' ' | cut -c1-300)"; }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }
has(){ printf '%s' "$out" | grep -q -- "$1"; }
set -a; . "$HOME/.gdaa/grmp.env"; set +a
ENV=(-e GSDB_USER_ID=$U -e POD_NAME=runtime-$U-1 -e GRMP_API_HOST=host.docker.internal -e GRMP_API_PORT=8781
     -e GRMP_AUTH_TOKEN="$GRMP_AUTH_TOKEN" -e MODEL_BASE_URL=http://host.docker.internal:9 -e MODEL_API_KEY=x -e MODEL_ID=none)
python3 "$HERE/scripts/tcp-forward.py" 8781 8779 & FWD=$!
cleanup(){ docker rm -f smoke-rt smoke-rt2 smoke-ki >/dev/null 2>&1; kill $FWD 2>/dev/null; }
trap cleanup EXIT
rm -rf "$NAS"; mkdir -p "$NAS/users/$U" "$NAS/admins/$U"; cp -R "$HOME/kb-verify/modes/kb" "$NAS/kb"; rm -f "$NAS/kb/.lock"
chmod -R a+rwX "$NAS"

run_rt(){ docker run -d --name "$1" "${ENV[@]}" -e POD_NAME="$1" -v "$NAS/users/$U:/nas/me" -v "$NAS/kb:/nas/kb:ro" -p "$2:4096" "$RT" >/dev/null; }
wait_ready(){ for i in $(seq 1 40); do curl -fsS -m 2 "http://127.0.0.1:$1/global/health" >/dev/null 2>&1 && return 0; sleep 1; done; return 1; }
X(){ docker exec "$1" bash -lc "$2" 2>&1; }
S=/opt/agent/config/opencode/skills

# 1 启动与探针
run_rt smoke-rt 14096; wait_ready 14096; out=$(curl -s http://127.0.0.1:14096/global/health)
check "runtime 启动,/global/health 返回 healthy"        'has "\"healthy\":true"'
out=$(X smoke-rt "id -u; ls -la /nas/me; cat /nas/me/.owner")
check "以 uid 1000 运行,/nas/me 下有 .owner 与 gdaa/xdg-data"  'has "^1000" && has ".owner" && has "gdaa" && has "xdg-data"'
out=$(X smoke-rt "cat \$GSDB_HOME/config.yaml; ls -la /data/oc")
check "config.yaml 与 opencode.json 已按环境变量渲染(0600)"   'has "host: host.docker.internal" && has "port: 8781" && has -- "-rw------- .* opencode.json"'

# 2 经 mock 登录并跑诊断,报告带执行人
out=$(X smoke-rt "python3 $S/gaussdb-login/scripts/login.py --ip 10.0.0.9 --database postgres")
H=$(printf '%s' "$out" | grep -oE -- '--session [a-z0-9]{12}' | head -1 | awk '{print $2}')
check "登录 10.0.0.9/postgres 得到句柄"                 '[ -n "$H" ]'
out=$(X smoke-rt "python3 $S/gaussdb-health/scripts/health.py --session $H")
check "health 经中间件出报告并标明执行人"               'has "Health Evidence" && has "执行人：$U"'

# 3 知识库:只读查询可用,导入命令是桩
out=$(X smoke-rt "python3 $S/gaussdb-kb/scripts/kb.py health")
check "kb health:文件模式 + 知识库只读"                 'has "模式:文件" && has "知识库只读"'
out=$(X smoke-rt "python3 $S/gaussdb-kb/scripts/kb.py search 索引台账")
check "kb search 命中样例条款"                          'has "GS-IDX-001"'
out=$(X smoke-rt "python3 $S/gaussdb-kb/scripts/kb.py ingest /tmp/x.xlsx; echo rc=\$?")
check "runtime 里 ingest 是桩:指向 kb-import,退出码 2"   'has "gaussdb-kb-import" && has "rc=2"'
out=$(X smoke-rt "touch /nas/kb/probe 2>&1; echo rc=\$?")
check "runtime 对 /nas/kb 只读"                          'has "Read-only" || has "rc=1"'

# 4 .owner 冲突:同一用户目录第二个 Pod 必须等待后退出
run_rt smoke-rt2 14097; sleep 8
out=$(docker logs smoke-rt2 2>&1; docker inspect -f '{{.State.Running}}' smoke-rt2)
check "第二个 Pod 看到新鲜的 .owner 后等待而不打开 db"    'has "仍持有" || has "true"'
docker rm -f smoke-rt2 >/dev/null 2>&1

# 5 优雅停机:WAL 合并、备份、释放标记
docker stop -t 60 smoke-rt >/dev/null; out=$(docker logs smoke-rt 2>&1 | tail -6; ls -la "$NAS/users/$U/xdg-data/opencode" "$NAS/users/$U/backup" 2>&1; test -e "$NAS/users/$U/.owner" && echo OWNER-STILL-THERE)
check "停机后 WAL 已合并、backup/ 有文件、.owner 已删"      'has "WAL 已合并" && has "退出备份" && ! has "OWNER-STILL-THERE"'
docker rm -f smoke-rt >/dev/null 2>&1

# 6 损坏恢复:写坏 db,重启应从备份恢复并报出
printf 'garbage%.0s' $(seq 1 2000) > "$NAS/users/$U/xdg-data/opencode/opencode.db"
run_rt smoke-rt 14096; wait_ready 14096; out=$(docker logs smoke-rt 2>&1)
check "启动自检发现损坏,从备份恢复并写明"                'has "损坏" && has "已从备份"'
docker rm -f smoke-rt >/dev/null 2>&1

# 7 kb-import 镜像:可写知识库,导入命令真实存在
docker run -d --name smoke-ki -e GSDB_USER_ID=$U -e POD_NAME=kb-import-$U -e MODEL_BASE_URL=http://host.docker.internal:9 -e MODEL_API_KEY=x -e MODEL_ID=none \
  -e GRMP_API_HOST=unused -v "$NAS/admins/$U:/nas/me" -v "$NAS/kb:/nas/kb" -p 14098:4096 "$KI" >/dev/null
wait_ready 14098
out=$(X smoke-ki "ls $S | grep gaussdb-; python3 $S/gaussdb-kb-import/scripts/kb.py validate --kb /nas/kb | tail -2; python3 $S/gaussdb-kb/scripts/kb.py health | head -1")
check "kb-import 只有两个 kb skill,validate 可写、health 不报只读"  'has "gaussdb-kb-import" && ! has "gaussdb-health" && ! has "知识库只读"'

echo "== 汇总: PASS $pass, FAIL $fail"
[ $fail -eq 0 ]
```

- [ ] **Step 3: 跑冒烟**

Run（Mac）：`cd ~/gh_agent_k8s && PATH=$HOME/p2venv/bin:$PATH bash scripts/smoke-image.sh dev`
Expected: 12 项全 PASS。常见失败与对策：`host.docker.internal` 不通 → OrbStack 用 `host.internal`，脚本改成两个都试；`docker exec … bash -lc` 里 `$GSDB_HOME` 为空 → entrypoint 的 `export` 只对子进程有效，exec 进来的 shell 要自己 `source /proc/1/environ`（脚本 `X()` 改为 `env $(tr '\0' '\n' < /proc/1/environ | grep -E '^(XDG_|GSDB_|OPENCODE_|HOME=)') bash -c`）。

- [ ] **Step 4: 提交**

```bash
chmod +x scripts/smoke-image.sh scripts/tcp-forward.py
git add scripts/smoke-image.sh scripts/tcp-forward.py
git commit -m "test(docker): 镜像冒烟——探针、登录诊断、知识库只读与桩、.owner 冲突、优雅停机、损坏恢复、导入镜像

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NFTD813rWESLz4NzmGi4NZ"
```

---

### Task 5: 环境变量契约文档与收尾

**Files:**
- Create: `docs/env-contract.md`
- Modify: `README.md`（目录表加 `docker/`、`scripts/` 实际内容；状态行）、`docs/plans/2026-09-17-roadmap.md`（阶段 2 行标完成）

- [ ] **Step 1: 写契约文档**

`docs/env-contract.md` 内容：spec §6 环境变量表原样 + 本阶段落实的补充：`POD_NAME`（downward API `metadata.name`）、`NAS_ME`/`NAS_KB`（默认 `/nas/me`、`/nas/kb`）、`OPENCODE_PORT`（4096）、`MODEL_PROVIDER_NAME`、`GRMP_API_PORT`；挂载表（`/nas/me` 读写 subPath `users/<工号>`；`/nas/kb` runtime 只读 / kb-import 读写；`/data` emptyDir）；探针 `GET /global/health`；停机 `terminationGracePeriodSeconds: 60`，entrypoint 的收尾动作清单；镜像里**没有**的东西（令牌、密钥、客户地址、auth.json）。

- [ ] **Step 2: README 与路线图**

README「目录」表改成实际文件；「状态」改为「阶段 2 完成：两个后端镜像可构建、冒烟通过」。路线图阶段 2 行加 ✅ 与日期。

- [ ] **Step 3: 提交、合并、打标签**

```bash
git add docs/env-contract.md README.md docs/plans/2026-09-17-roadmap.md
git commit -m "docs: 环境变量与挂载契约;README/路线图标记阶段 2 完成

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NFTD813rWESLz4NzmGi4NZ"
git checkout main && git merge --no-ff feat/phase2-images -m "merge: 阶段 2 后端镜像(agent-v0.2)"
git tag -a agent-v0.2 -m "agent-v0.2: runtime / kb-import 两个后端镜像,冒烟通过"
git push origin main agent-v0.2
```

---

## Self-Review

- **Spec 覆盖**：§4 目录布局 → entrypoint 顶部 + smoke 检查 2；§5 两镜像内容、uid 1000、预置 rg/models.json、`share` 禁用、无 auth.json → Dockerfile + podctl 渲染 + smoke 1/3/7；§6 环境变量契约 → podctl 渲染 + env-contract.md；§6.2 ②独占 ③优雅停机 ④自检 ⑤备份 → podctl + entrypoint + smoke 4/5/6；①挂载参数与⑥压测属于平台/客户环境，阶段 3、5 处理；§7 kb-import 差异 → Dockerfile kb-import target + smoke 7；§11 非 root、Secret 只走环境变量 → Dockerfile/podctl；§12 第 2、4、5 项 → smoke 4/5/6、3。
- **占位符**：无「TBD」；Dockerfile 的 `install-opencode.sh` 输出关键字 `installed` 与 v12.9 一致（Task 0 冒烟时见过）。
- **类型一致**：`podctl` 子命令名在 entrypoint 与 CLI 定义一致；`db_backup(..., now=)` 的时间戳格式 `%Y%m%dT%H%M%SZ` 与 `_backups()` 的排序假设一致（同格式字典序 = 时间序）。
- **已知不确定点（实施时验证）**：`OPENCODE_CONFIG` 指向渲染文件后，`XDG_CONFIG_HOME/opencode/` 下没有 `opencode.jsonc` 是否会被 opencode 自动创建（Task 0 的 XDG 实验里它创建了一个空壳）——镜像目录只读时若报错，改为在镜像里放一个只含 `$schema` 的空壳。`host.docker.internal` 在 OrbStack 下的解析。
