# 前端镜像 · 第一份计划：报告存档 + Pod 只读端口 + 网关三路分发 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 技能跑完把报告 JSON 存到本人 NAS 目录；用户 Pod 多开一个只读端口把它端出来；网关把 `/reports/*` 按工号代理过去、把 `/dash/*` 代理到（尚未存在的）前端 Service。做完后**没有前端也能验收**：在会话里跑一次健康检查，`curl` 经网关拿到 `/reports/health/latest.json`。

**Architecture:** 一个新模块 `common/reports.py` 负责存档（原子写 + `latest.json` + `index.json` + 每技能保留 50 份，失败只 warn 不抛）；五个技能在输出末尾各调一次。Pod 侧 `podctl serve-reports` 是标准库 `http.server` 的只读静态服务（只 GET、只本目录、只三种后缀），`entrypoint.sh` 在 `opencode serve` 之后拉起。网关 `_proxy` 按路径前缀分三路。技能侧改动同步到 gh_skill。

**Tech Stack:** Python 3.9+（Mac 上跑测试）/ 3.12（镜像）；标准库 `http.server`、`json`、`os.replace`；pytest（`--import-mode=importlib`，见根 `pytest.ini`）；K8s 清单 YAML；bash。

**Spec:** `docs/specs/2026-09-22-frontend-dashboards-design.md` §3 D1、§4.1–4.6、§4.9 的字段、§5 S1–S4、§6、§7、§8 阶段 1–2

## Global Constraints

- 环境变量名 `GSDB_REPORTS_DIR`；容器里 entrypoint 设为 `$NAS_ME/reports`；**未设则不存档、行为与现在完全一致**（非容器线不受影响）。
- 存档失败**不影响技能自身的 stdout 输出与退出码**，但必须在 stderr 打一行 `[warn] 报告未存档：<原因>`。
- 写文件一律 `common/kb/atomic.py` 同款：临时文件 + `os.replace`。
- 只读端口 `4097`：只 GET、路径规范化后必须仍在目录内、只 `.json` / `.jsonl` / `.html`、目录列表 404、`Cache-Control: no-store`。不是就绪条件（探针不动）。
- 网关新配置项：代码读 `GATEWAY_FRONTEND_SERVICE`（默认 `frontend`），ConfigMap 键 `FRONTEND_SERVICE`，`gateway.yaml` 映射进容器 —— **三处一起改**，`gateway/tests/test_manifest_guards_units.py` 的守卫会拦漏的。
- `/dash/*` 不注入 opencode 口令、不记活动时间、不 `ensure_ready`；`/reports/*` 与其它路径一样要 `require_prerequisites` + `ensure_ready`。
- 技能侧改动（Task 1–5）是**两仓库**改动，Task 9 同步到 `~/gh_skill/opencode_skill-main-v2-0729`。
- 提交信息中文 conventional commit，正文写清为什么；结尾两行：
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01NFTD813rWESLz4NzmGi4NZ`
- 本机没有 Python/docker/kubectl。测试与容器命令一律在 Mac 上跑：
  `ssh sqlrush@192.168.128.1 'export PATH=$HOME/.orbstack/bin:/usr/local/bin:$PATH; cd ~/gh_agent_k8s && python3 -m pytest <路径> -q'`
  （同一棵工作树，改完文件直接 ssh 过去跑，不用同步）。全套：`python3 -m pytest -q`，发版前必须一次跑全。
- 直接在 `main` 上做，每个任务一次提交（本仓库现有做法）。

---

## 文件结构

| 文件 | 职责 | 任务 |
|---|---|---|
| `agent/common/reports.py`（新） | 存档：目录解析、`archive()`、`append_jsonl()`、`index.json` 维护、保留 50 份 | 1 |
| `agent/tests/test_reports_units.py`（新） | 存档模块单测 | 1 |
| `agent/skills/gaussdb-health/scripts/report.py` | 抽出 `health_dict()`，`render_health_json` 用它 | 2 |
| `agent/skills/gaussdb-health/scripts/health.py:169-172` | 输出后 `archive("health", d)` | 2 |
| `agent/skills/gaussdb-topsql/scripts/topsql.py:119-123` | 输出后 `archive("topsql", rows, name=<ts>.<by>)` | 2 |
| `agent/skills/gaussdb-wdr/scripts/wdr.py:70-78` | 输出后 `archive("wdr", ev.to_dict())`；原生 HTML 默认落 reports/wdr | 2, 5 |
| `agent/skills/gaussdb-sqltune/scripts/sqltune.py:587-591` | 输出后 `archive("sqltune", _to_jsonable(tr), name=<sql_id>)` | 2 |
| `agent/tests/test_reports_hooks_units.py`（新） | 四个技能 main 级的存档钩子测试（monkeypatch 取数） | 2, 5 |
| `agent/skills/gaussdb-kb/scripts/kb.py` | `health --json`（`health_dict()`）、`query` 追加检索日志 | 3, 4 |
| `agent/tests/test_kb_skill_reports_units.py`（新） | kb health JSON 形状 + query 日志行 | 3, 4 |
| `docker/podctl.py` | `serve_reports()` + `serve-reports` 子命令 | 6 |
| `docker/entrypoint.sh` | 导出 `GSDB_REPORTS_DIR`；拉起/收掉报告服务 | 6 |
| `docker/tests/test_podctl.py` | 只读服务的行为测试 | 6 |
| `docker/tests/test_entrypoint_units.py`（新） | entrypoint 契约（导出、拉起、收掉） | 6 |
| `k8s/templates/runtime.yaml`、`k8s/base/networkpolicy.yaml` | 4097 端口与放行 | 7 |
| `gateway/tests/test_manifest_guards_units.py` | 4097 守卫 | 7 |
| `gateway/config.py`、`k8s/base/configmap-gateway.yaml`、`k8s/base/gateway.yaml` | `frontend_service` 三处 | 8 |
| `gateway/proxy.py` | `forward_headers(password=None)` 不注入 | 8 |
| `gateway/server.py:_proxy` | 三路分发 | 8 |
| `gateway/tests/test_config_units.py`、`test_server_units.py` | 配置与路由测试 | 8 |
| gh_skill 对应文件 | 同步 Task 1–5 | 9 |
| `scripts/k8s/e2e-reports.sh`（新） | 本地集群端到端 | 10 |
| `docs/参数手册.md`、`docs/仓库结构说明.md`、`docs/对接清单-各方要做什么.md` | 新参数、新端口、新模块 | 11 |

---

### Task 1: `common/reports.py` 存档核心

**Files:**
- Create: `agent/common/reports.py`
- Test: `agent/tests/test_reports_units.py`

**Interfaces:**
- Produces:
  - `reports_dir() -> Optional[pathlib.Path]`：读 `GSDB_REPORTS_DIR`，空/未设返回 `None`。
  - `archive(skill: str, payload: Any, *, name: Optional[str] = None, keep: int = 50, now: Optional[str] = None) -> Optional[pathlib.Path]`：写 `<dir>/<skill>/<name or 时间戳>.json`，同时覆盖 `<dir>/<skill>/latest.json`，维护 `<dir>/<skill>/index.json`（最近 `keep` 条 `{"at","file","overall","counts"}`，`overall` 取 `payload.get("overall")`，`counts` 是 `payload.get("findings", [])` 按 severity 计数），超过 `keep` 份时删最旧的 **且只删本 skill 目录里名字在 index 里的**。任何 OSError → stderr 一行 `[warn] 报告未存档：...`，返回 `None`。`reports_dir()` 为 `None` 时静默返回 `None`。
  - `append_jsonl(skill: str, name: str, row: dict) -> bool`：往 `<dir>/<skill>/<name>.jsonl` 追加一行 JSON；失败同样 warn 不抛。
  - `utc_stamp(now=None) -> str`：`20260922T060312Z` 形态（与 `docker/podctl.py:_utc_stamp` 同格式）。

- [ ] **Step 1: 写失败的测试**

```python
"""common.reports —— 报告存档:目录未设即不存档;原子写;latest/index;保留 N 份;失败只 warn。"""
import json
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from common import reports  # noqa: E402


def test_unset_env_means_no_archive(monkeypatch, tmp_path):
    monkeypatch.delenv("GSDB_REPORTS_DIR", raising=False)
    assert reports.reports_dir() is None
    assert reports.archive("health", {"overall": 0}) is None
    assert not list(tmp_path.iterdir())


def test_archive_writes_file_latest_and_index(monkeypatch, tmp_path):
    monkeypatch.setenv("GSDB_REPORTS_DIR", str(tmp_path))
    p = reports.archive("health", {"overall": 2, "findings": [{"severity": 2}, {"severity": 1}]},
                        now="20260922T060312Z")
    assert p == tmp_path / "health" / "20260922T060312Z.json"
    assert json.loads(p.read_text(encoding="utf-8"))["overall"] == 2
    latest = json.loads((tmp_path / "health" / "latest.json").read_text(encoding="utf-8"))
    assert latest["overall"] == 2
    idx = json.loads((tmp_path / "health" / "index.json").read_text(encoding="utf-8"))
    assert idx == [{"at": "20260922T060312Z", "file": "20260922T060312Z.json",
                    "overall": 2, "counts": {"1": 1, "2": 1}}]


def test_explicit_name_is_used_and_index_dedupes_by_file(monkeypatch, tmp_path):
    monkeypatch.setenv("GSDB_REPORTS_DIR", str(tmp_path))
    reports.archive("sqltune", {"a": 1}, name="1a9f", now="20260922T000000Z")
    reports.archive("sqltune", {"a": 2}, name="1a9f", now="20260922T000100Z")
    idx = json.loads((tmp_path / "sqltune" / "index.json").read_text(encoding="utf-8"))
    assert [e["file"] for e in idx] == ["1a9f.json"], "同名覆盖时 index 不该出现两条"
    assert json.loads((tmp_path / "sqltune" / "1a9f.json").read_text())["a"] == 2


def test_keep_prunes_oldest_only_within_skill_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("GSDB_REPORTS_DIR", str(tmp_path))
    (tmp_path / "health").mkdir()
    (tmp_path / "health" / "stranger.json").write_text("{}", encoding="utf-8")   # 不在 index 里,不能被删
    for i in range(4):
        reports.archive("health", {"overall": 0}, keep=3, now="2026092%dT000000Z" % i)
    names = sorted(p.name for p in (tmp_path / "health").glob("*.json"))
    assert "20260920T000000Z.json" not in names, "最旧的一份该被删"
    assert "stranger.json" in names, "只删 index 里登记过的"
    idx = json.loads((tmp_path / "health" / "index.json").read_text(encoding="utf-8"))
    assert len(idx) == 3


def test_archive_failure_warns_and_never_raises(monkeypatch, tmp_path, capsys):
    """**存档失败不能拖垮技能。** 目录是个文件 → mkdir 失败 → 只 warn。"""
    blocker = tmp_path / "blocked"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("GSDB_REPORTS_DIR", str(blocker))
    assert reports.archive("health", {"overall": 0}) is None
    assert "[warn] 报告未存档" in capsys.readouterr().err


def test_append_jsonl(monkeypatch, tmp_path):
    monkeypatch.setenv("GSDB_REPORTS_DIR", str(tmp_path))
    assert reports.append_jsonl("kb", "queries", {"q": "慢 SQL"}) is True
    assert reports.append_jsonl("kb", "queries", {"q": "锁"}) is True
    lines = (tmp_path / "kb" / "queries.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["q"] for x in lines] == ["慢 SQL", "锁"]


def test_write_is_atomic_no_tmp_left_behind(monkeypatch, tmp_path):
    monkeypatch.setenv("GSDB_REPORTS_DIR", str(tmp_path))
    reports.archive("wdr", {"overall": 0}, now="20260922T000000Z")
    assert not list((tmp_path / "wdr").glob("*.tmp"))
```

- [ ] **Step 2: 跑，确认失败**

Run: `ssh sqlrush@192.168.128.1 'cd ~/gh_agent_k8s && python3 -m pytest agent/tests/test_reports_units.py -q'`
Expected: `ModuleNotFoundError: No module named 'common.reports'`

- [ ] **Step 3: 最小实现**

```python
"""报告存档 —— 技能跑完把 JSON 留在本人 NAS 目录,大盘从这里读。

只在设了 GSDB_REPORTS_DIR 时工作(容器里 entrypoint 设成 $NAS_ME/reports);
没设就什么都不做,非容器线的行为一字不变。

**存档失败不能拖垮技能。** 报告已经打到 stdout 了,存档只是附加动作:
失败打一行 warn 让人知道大盘会缺这一份,然后照常返回。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import sys
from typing import Any, Dict, List, Optional

ENV_DIR = "GSDB_REPORTS_DIR"
LATEST = "latest.json"
INDEX = "index.json"
DEFAULT_KEEP = 50


def utc_stamp(now: Optional[str] = None) -> str:
    return now or _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def reports_dir() -> Optional[pathlib.Path]:
    raw = (os.environ.get(ENV_DIR) or "").strip()
    return pathlib.Path(raw).expanduser() if raw else None


def _warn(msg: str) -> None:
    print("[warn] 报告未存档:%s" % msg, file=sys.stderr)


def _write_atomic(path: pathlib.Path, text: str) -> None:
    """同 common/kb/atomic.py:读的一方(Pod 内只读端口)任何时刻拿到的都是完整文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _counts(payload: Any) -> Dict[str, int]:
    out: Dict[str, int] = {}
    findings = payload.get("findings") if isinstance(payload, dict) else None
    for f in findings or []:
        sev = str(f.get("severity")) if isinstance(f, dict) else None
        if sev is not None:
            out[sev] = out.get(sev, 0) + 1
    return out


def _load_index(path: pathlib.Path) -> List[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def archive(skill: str, payload: Any, *, name: Optional[str] = None,
            keep: int = DEFAULT_KEEP, now: Optional[str] = None) -> Optional[pathlib.Path]:
    base = reports_dir()
    if base is None:
        return None
    stamp = utc_stamp(now)
    fname = (name or stamp) + ".json"
    d = base / skill
    try:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        _write_atomic(d / fname, text)
        _write_atomic(d / LATEST, text)
        idx = [e for e in _load_index(d / INDEX) if e.get("file") != fname]
        idx.append({"at": stamp, "file": fname,
                    "overall": payload.get("overall") if isinstance(payload, dict) else None,
                    "counts": _counts(payload)})
        idx.sort(key=lambda e: e.get("at", ""))
        for old in idx[:-keep] if keep > 0 else []:
            try:
                (d / str(old.get("file"))).unlink()      # 只删 index 里登记过的
            except OSError:
                pass
        idx = idx[-keep:] if keep > 0 else idx
        _write_atomic(d / INDEX, json.dumps(idx, ensure_ascii=False, indent=2))
        return d / fname
    except (OSError, TypeError, ValueError) as exc:
        _warn("%s/%s:%s" % (skill, fname, exc))
        return None


def append_jsonl(skill: str, name: str, row: Dict[str, Any]) -> bool:
    base = reports_dir()
    if base is None:
        return False
    path = base / skill / (name + ".jsonl")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except (OSError, TypeError, ValueError) as exc:
        _warn("%s/%s.jsonl:%s" % (skill, name, exc))
        return False
```

- [ ] **Step 4: 跑，确认通过**

Run: 同 Step 2
Expected: `7 passed`

- [ ] **Step 5: 提交**

```bash
git add agent/common/reports.py agent/tests/test_reports_units.py
git commit -m "feat(reports): 报告存档模块——GSDB_REPORTS_DIR 未设即不存档,原子写,latest/index,保留 50 份,失败只 warn"
```

---

### Task 2: 四个技能接上存档（health / topsql / wdr / sqltune）

**Files:**
- Modify: `agent/skills/gaussdb-health/scripts/report.py:64-70`
- Modify: `agent/skills/gaussdb-health/scripts/health.py:169-172`
- Modify: `agent/skills/gaussdb-topsql/scripts/topsql.py:119-123`
- Modify: `agent/skills/gaussdb-wdr/scripts/wdr.py:70-78`
- Modify: `agent/skills/gaussdb-sqltune/scripts/sqltune.py:587-591`
- Test: `agent/tests/test_reports_hooks_units.py`

**Interfaces:**
- Consumes: `common.reports.archive(skill, payload, *, name=None)`（Task 1）。
- Produces: `report.health_dict(ev, sub_results=()) -> dict`（health 的 JSON 形状，`render_health_json` 改为 `json.dumps(health_dict(...))`）。存档文件形状：`health/<ts>.json` = `health_dict`；`topsql/<ts>.<by>.json` = `[row.__dict__...]` 外再包一层 `{"by": by, "limit": limit, "rows": [...]}`；`wdr/<ts>.json` = `ev.to_dict()`；`sqltune/<sql_id>.json` = `_to_jsonable(tr)`（无 sql_id 时不存档）。

- [ ] **Step 1: 写失败的测试**

```python
"""四个技能的存档钩子:跑一次 main(取数被替换成假的),GSDB_REPORTS_DIR 下就该有 latest.json。

不测取数,不测渲染 —— 那些各有各的测试;这里只钉「输出之后确实存档了」和「格式对得上」。
"""
import json
import pathlib
import sys
import types

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
from common import reports  # noqa: E402
from common.finding import Finding, Severity  # noqa: E402


def _load(scripts: str, *purge: str):
    """按 test_health_units.py 的做法:清掉同名模块缓存再把 scripts 目录压到 sys.path 头。"""
    for m in purge:
        sys.modules.pop(m, None)
    sys.path.insert(0, str(_ROOT / "skills" / scripts / "scripts"))


@pytest.fixture()
def rdir(monkeypatch, tmp_path):
    monkeypatch.setenv("GSDB_REPORTS_DIR", str(tmp_path))
    return tmp_path


def test_health_main_archives_json_shape(rdir, monkeypatch):
    _load("gaussdb-health", "model", "thresholds", "util", "collectors", "report", "health", "aggregate")
    import health, model  # noqa: E402
    ev = model.HealthEvidence(conn="og", target="10.0.0.9/postgres", overall=Severity.WARN,
                              findings=[Finding("slowsql", "SLOW_AVG_MS", Severity.WARN, "avg_ms", "8412", ">1000", "x")])
    monkeypatch.setattr(health.access, "for_conn", lambda *a, **k: object())
    monkeypatch.setattr(health.aggregate, "collect_all", lambda *a, **k: [])
    monkeypatch.setattr(health, "run_health", lambda *a, **k: ev)
    monkeypatch.setattr(health.common.config, "resolved_name", lambda c: "og")
    assert health.main(["-c", "og", "--format", "json"]) == 0
    latest = json.loads((rdir / "health" / "latest.json").read_text(encoding="utf-8"))
    assert latest["overall"] == 2 and latest["findings"][0]["code"] == "SLOW_AVG_MS"
    assert "sub_skills" in latest and "dims" in latest
    idx = json.loads((rdir / "health" / "index.json").read_text(encoding="utf-8"))
    assert idx[-1]["overall"] == 2 and idx[-1]["counts"] == {"2": 1}


def test_health_markdown_output_still_archives_json(rdir, monkeypatch):
    _load("gaussdb-health", "model", "thresholds", "util", "collectors", "report", "health", "aggregate")
    import health, model  # noqa: E402
    monkeypatch.setattr(health.access, "for_conn", lambda *a, **k: object())
    monkeypatch.setattr(health.aggregate, "collect_all", lambda *a, **k: [])
    monkeypatch.setattr(health, "run_health", lambda *a, **k: model.HealthEvidence(conn="og"))
    monkeypatch.setattr(health.common.config, "resolved_name", lambda c: "og")
    assert health.main(["-c", "og"]) == 0
    assert (rdir / "health" / "latest.json").is_file(), "markdown 输出时也要存 JSON,大盘只认 JSON"


def test_topsql_main_archives_with_by_in_name(rdir, monkeypatch):
    _load("gaussdb-topsql", "topsql", "render")
    import topsql  # noqa: E402
    rows = [topsql.StmtRow(sql_id="1a9f", query="select 1", calls=3, total_sec=1.5, avg_ms=500.0, rows=3)]
    monkeypatch.setattr(topsql.access, "for_conn", lambda *a, **k: object())
    monkeypatch.setattr(topsql, "top_sql", lambda runner, by, limit: rows)
    assert topsql.main(["-c", "og", "--by", "avg", "--format", "json"]) == 0
    files = sorted(p.name for p in (rdir / "topsql").glob("*.avg.json"))
    assert len(files) == 1, "文件名要带 by:%s" % list((rdir / "topsql").iterdir())
    latest = json.loads((rdir / "topsql" / "latest.json").read_text(encoding="utf-8"))
    assert latest["by"] == "avg" and latest["rows"][0]["sql_id"] == "1a9f"


def test_wdr_collect_archives_evidence(rdir, monkeypatch):
    _load("gaussdb-wdr", "model", "thresholds", "util", "collectors", "report", "native", "snaps",
          "interp", "recheck", "finalreport", "ansi", "wdr")
    import wdr, model  # noqa: E402
    ev = model.Evidence(conn="og", target="t")
    ev.window.begin_id, ev.window.end_id = 1915, 1916
    monkeypatch.setattr(wdr.access, "for_conn", lambda *a, **k: object())
    monkeypatch.setattr(wdr, "collect_evidence", lambda runner, opt: ev)
    monkeypatch.setattr(wdr.common.config, "resolved_name", lambda c: "og")
    assert wdr.main(["collect", "-c", "og", "--begin", "1915", "--end", "1916", "--format", "json"]) == 0
    latest = json.loads((rdir / "wdr" / "latest.json").read_text(encoding="utf-8"))
    assert latest["window"]["begin_id"] == 1915


def test_sqltune_archive_named_by_sql_id_and_skipped_without_it(rdir, monkeypatch):
    _load("gaussdb-sqltune", "sqltune")
    import sqltune  # noqa: E402
    payload = {"sql_id": "1a9f", "x": 1}
    sqltune.archive_tune(payload)                       # 有 sql_id → 存
    assert (rdir / "sqltune" / "1a9f.json").is_file()
    sqltune.archive_tune({"sql_id": "", "x": 2})        # 没有 → 不存,不报错
    assert not (rdir / "sqltune" / ".json").exists()
    assert len(list((rdir / "sqltune").glob("*.json"))) == 3   # 1a9f.json + latest.json + index.json
```

- [ ] **Step 2: 跑，确认失败**

Run: `ssh sqlrush@192.168.128.1 'cd ~/gh_agent_k8s && python3 -m pytest agent/tests/test_reports_hooks_units.py -q'`
Expected: 5 failed（health 那两个是 `latest.json` 不存在；topsql/wdr 同；sqltune 是 `AttributeError: archive_tune`）。若 wdr 的 `model.Evidence` 构造签名不同（`window` 不是可赋值属性），按 `model.py:120-155` 的 `Evidence`/`Window` 定义改测试构造方式 —— 改测试的构造，不改断言。

- [ ] **Step 3: 实现 —— health**

`report.py:64-70` 改为：

```python
def health_dict(ev: HealthEvidence, sub_results=()) -> dict:
    """JSON 形态。--format json 打它,存档也存它 —— 大盘读的就是这个形状,一份定义。"""
    d = ev.to_dict()
    d["sub_skills"] = [{"skill": r.skill, "ok": r.ok, "error": r.error}
                       for r in sub_results]
    d["uncovered_capabilities"] = list(aggregate.NEEDS_TARGET)
    d["kb_refs"] = kb_section(ev.findings)[1]
    return d


def render_health_json(ev: HealthEvidence, sub_results=()) -> str:
    return json.dumps(health_dict(ev, sub_results=sub_results), ensure_ascii=False, indent=2)
```

`health.py:169-172` 改为：

```python
        d = report.health_dict(ev, sub_results=sub_results)
        if args.format == "json":
            print(json.dumps(d, ensure_ascii=False, indent=2))
        else:
            print(render_health(ev, sub_results=sub_results), end="")
        # 存档在输出之后:大盘读 latest.json;失败只 warn,不影响这次的输出与退出码
        reports.archive("health", d)
```

在 `health.py` 顶部 import 区加 `from common import reports` 与 `import report`（若已 `from report import render_health, render_health_json` 则改成也导入 `health_dict`，按文件现状取一种）。

- [ ] **Step 4: 实现 —— topsql**

`topsql.py:119-123` 改为：

```python
        rows = top_sql(runner, args.by, args.limit)
        payload = {"by": args.by, "limit": args.limit, "rows": [r.__dict__ for r in rows]}
        if args.format == "json":
            print(json.dumps(payload["rows"], ensure_ascii=False, indent=2))   # stdout 形状不变
        else:
            print(stmt_table("Top SQL by " + args.by, rows), end="")
        reports.archive("topsql", payload, name="%s.%s" % (reports.utc_stamp(), args.by))
        return 0
```

顶部加 `from common import reports`。

- [ ] **Step 5: 实现 —— wdr**

`wdr.py:70-78`（`_cmd_collect` 里）改为：

```python
        ev = collect_evidence(runner, opt)
        ev.conn = common.config.resolved_name(args.conn)
        if args.format == "json":
            print(render_evidence_json(ev))
        else:
            print(render_evidence(ev), end="")
        reports.archive("wdr", ev.to_dict())
        return 0
```

顶部加 `from common import reports`。

- [ ] **Step 6: 实现 —— sqltune**

在 `sqltune.py` 的 `_to_jsonable` 之后加：

```python
def archive_tune(payload: dict) -> None:
    """按 sql_id 存档(大盘的逐条卡按 sql_id 找)。--sql-stdin 那条路没有 sql_id,不存。"""
    sql_id = str(payload.get("sql_id") or "").strip()
    if sql_id:
        reports.archive("sqltune", payload, name=sql_id)
```

`sqltune.py:587-591` 改为：

```python
        payload = _to_jsonable(tr)
        if args.format == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(sqltune_report(tr), end="")
        archive_tune(payload)
        return 0
```

顶部加 `from common import reports`。

- [ ] **Step 7: 跑，确认通过；再跑四个技能原有测试确认没动坏**

Run: `ssh sqlrush@192.168.128.1 'cd ~/gh_agent_k8s && python3 -m pytest agent/tests/test_reports_hooks_units.py agent/tests/test_health_units.py agent/tests/test_wdr_units.py agent/tests/test_health_aggregate_units.py -q'`
Expected: 全 passed

- [ ] **Step 8: 提交**

```bash
git add agent/skills/gaussdb-health/scripts/report.py agent/skills/gaussdb-health/scripts/health.py \
        agent/skills/gaussdb-topsql/scripts/topsql.py agent/skills/gaussdb-wdr/scripts/wdr.py \
        agent/skills/gaussdb-sqltune/scripts/sqltune.py agent/tests/test_reports_hooks_units.py
git commit -m "feat(reports): health/topsql/wdr/sqltune 输出后存档——大盘读的形状与 --format json 同一份定义"
```

---

### Task 3: `gaussdb-kb health --json`

**Files:**
- Modify: `agent/skills/gaussdb-kb/scripts/kb.py:162-194`（`cmd_health`）、`:213-215`（health 子命令加 `--json`）
- Test: `agent/tests/test_kb_skill_reports_units.py`

**Interfaces:**
- Produces: `health_dict(kb: pathlib.Path, status, file_warnings: list, readonly: bool) -> dict`，形状：
  `{"status": {attached, reason, version, counts, vector, graph, mode}, "readonly": bool, "inbox": str, "file_warnings": [...], "index_state": {...或 {}}, "pending": [...], "misses": [{"code": str, "n": int}, ...]}`。
  `cmd_health` 在 `--json` 时打印它并**同时** `reports.archive("kb", d, name="health")`（即 `kb/health.json`，每次覆盖）；文本输出路径不变，也存档。退出码规则不变。

- [ ] **Step 1: 写失败的测试**

```python
"""gaussdb-kb 的 health --json 与 query 检索日志 —— 大盘知识库页的两个数据来源。"""
import json
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "skills" / "gaussdb-kb" / "scripts"))
sys.path.insert(0, str(_ROOT))

import kb as kbcli  # noqa: E402
from common.kb import query as kbquery  # noqa: E402


class _Sess:
    def __init__(self, status):
        self._s = status
        self.pg = type("PG", (), {"warnings": ["cases/bad.md: 缺 frontmatter"]})()
    def status(self): return self._s
    def close(self): pass


def _fake_open(status):
    return lambda kb: _Sess(status)


def test_health_json_shape_when_attached(monkeypatch, tmp_path, capsys):
    kbdir = tmp_path / "kb"; (kbdir / "index").mkdir(parents=True)
    (kbdir / "index" / "misses.log").write_text("2026-09-20\tNOPK_BIGTABLE\tq\n2026-09-21\tNOPK_BIGTABLE\tq\n2026-09-21\tREPL_LAG\tq\n", encoding="utf-8")
    st = kbquery.KbStatus(attached=True, version="3", counts={"docs.case": 286, "docs.rule": 412},
                          vector="DataVec(覆盖 93%)", graph="图文件", mode="向量库+图文件")
    monkeypatch.setattr(kbquery.KbSession, "open", staticmethod(_fake_open(st)))
    monkeypatch.setenv("GSDB_REPORTS_DIR", str(tmp_path / "reports"))
    rc = kbcli.main(["health", "--kb", str(kbdir), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["status"]["counts"]["docs.case"] == 286 and out["status"]["mode"] == "向量库+图文件"
    assert out["misses"] == [{"code": "NOPK_BIGTABLE", "n": 2}, {"code": "REPL_LAG", "n": 1}]
    assert out["file_warnings"] == ["cases/bad.md: 缺 frontmatter"]
    assert out["readonly"] is False and out["inbox"].endswith("inbox/uploads")
    assert out["pending"] == [] and out["index_state"] == {}
    archived = json.loads((tmp_path / "reports" / "kb" / "health.json").read_text(encoding="utf-8"))
    assert archived["status"]["counts"]["docs.rule"] == 412


def test_health_json_when_not_attached_keeps_reason_and_exit_2(monkeypatch, tmp_path, capsys):
    kbdir = tmp_path / "kb"; kbdir.mkdir()
    st = kbquery.KbStatus(attached=False, reason="未接入")
    monkeypatch.setattr(kbquery.KbSession, "open", staticmethod(_fake_open(st)))
    monkeypatch.delenv("GSDB_REPORTS_DIR", raising=False)
    rc = kbcli.main(["health", "--kb", str(kbdir), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["status"]["attached"] is False and out["status"]["reason"] == "未接入"


def test_health_text_output_unchanged_and_still_archives(monkeypatch, tmp_path, capsys):
    kbdir = tmp_path / "kb"; kbdir.mkdir()
    st = kbquery.KbStatus(attached=True, counts={"docs.case": 1, "docs.rule": 2, "docs.raw": 0}, mode="文件")
    monkeypatch.setattr(kbquery.KbSession, "open", staticmethod(_fake_open(st)))
    monkeypatch.setenv("GSDB_REPORTS_DIR", str(tmp_path / "reports"))
    kbcli.main(["health", "--kb", str(kbdir)])
    text = capsys.readouterr().out
    assert text.startswith("> 知识库") and "缺口清单  : 无记录" in text
    assert (tmp_path / "reports" / "kb" / "health.json").is_file()
```

- [ ] **Step 2: 跑，确认失败**

Run: `ssh sqlrush@192.168.128.1 'cd ~/gh_agent_k8s && python3 -m pytest agent/tests/test_kb_skill_reports_units.py -q -k health'`
Expected: `error: unrecognized arguments: --json`（argparse 退出）或 `SystemExit: 2`

- [ ] **Step 3: 实现**

`kb.py` 顶部加 `from common import reports`。`cmd_health` 重写为：

```python
def health_dict(kb: pathlib.Path, status, file_warnings: list, readonly: bool) -> dict:
    """大盘知识库页的数据形状。文本输出与它同源,一份定义。"""
    from common.kb import inbox as kbinbox
    return {
        "status": dict(status.__dict__),
        "readonly": readonly,
        "inbox": str(kbinbox.inbox_dir(kb)),
        "file_warnings": list(file_warnings),
        "index_state": indexer.read_state(kb) or {},
        "pending": _pending(kb),
        "misses": [{"code": code, "n": n} for code, n in _misses_top(kb)],
    }


def cmd_health(args: argparse.Namespace) -> int:
    kb = kbconfig.resolve_kb_dir(args.kb)
    if not kb.is_dir():
        raise KbError(f"KB 目录不存在:{kb}")
    sess = kbquery.KbSession.open(kb)
    try:
        status = sess.status()
        file_warnings = list(getattr(sess.pg, "warnings", ()))[:5]    # 文件模式:坏文件在这里露头
    finally:
        sess.close()
    readonly = not os.access(kb, os.W_OK)
    d = health_dict(kb, status, file_warnings, readonly)
    if getattr(args, "json", False):
        print(json.dumps(d, ensure_ascii=False, indent=2))
    else:
        print(render.status_line(status))
        if readonly:
            print("知识库只读  : 是(本环境不能导入;由知识库管理员在导入环境维护)")
        print(f"收件目录  : {d['inbox']}(用户要导入自己电脑上的文件时,先上传到这里再 ingest)")
        for w in file_warnings:
            print(f"[warn ] 文件:{w}")
        state = d["index_state"]
        if state:
            print(f"上次索引  : {state.get('indexed_at', '?')} · 文档新写 {state.get('docs_indexed', '?')} · "
                  f"覆盖 {state.get('chunk_embedded', '?')}/{state.get('chunk_total', '?')} · 图 {state.get('graph', '?')}")
        print("待处理    : " + ("; ".join(d["pending"]) if d["pending"] else "无"))
        if d["misses"]:
            print("缺口清单  : 近期查不到条款/案例的发现 Top —— " +
                  "、".join(f"{m['code']}×{m['n']}" for m in d["misses"]) + "(补这类材料收益最大)")
        else:
            print("缺口清单  : 无记录")
        if not status.attached:
            print(f"[error] 知识库未接入:{status.reason}")
    reports.archive("kb", d, name="health")
    if not status.attached:
        return 2
    return 2 if d["pending"] else 0
```

health 子命令处（`:213-215`）加 `p.add_argument("--json", action="store_true")`。

- [ ] **Step 4: 跑，确认通过；再跑 kb 原有测试**

Run: `ssh sqlrush@192.168.128.1 'cd ~/gh_agent_k8s && python3 -m pytest agent/tests/test_kb_skill_reports_units.py agent/tests/test_kb_query_skill_units.py agent/tests/test_kb_query_units.py -q'`
Expected: 全 passed

- [ ] **Step 5: 提交**

```bash
git add agent/skills/gaussdb-kb/scripts/kb.py agent/tests/test_kb_skill_reports_units.py
git commit -m "feat(kb): health --json 与存档——知识库大盘的数据形状,文本输出同源不变"
```

---

### Task 4: `gaussdb-kb query` 追加检索日志

**Files:**
- Modify: `agent/skills/gaussdb-kb/scripts/kb.py:114-125`（`cmd_query`）
- Test: `agent/tests/test_kb_skill_reports_units.py`（追加）

**Interfaces:**
- Consumes: `reports.append_jsonl("kb", "queries", row)`（Task 1）。
- Produces: `kb/queries.jsonl` 每行 `{"at": ISO8601Z, "q": str, "key": str, "hits_cases": int, "hits_rules": int, "how": "semantic"|"keyword", "elapsed_ms": int}`；`how` 的口径：`status.mode` 非空且不是 `文件`、且 `status.vector` 不含 `超时` → `semantic`，否则 `keyword`。`--from-findings` 时每个 finding 一行（`key` = finding code，`q` = 该项 `query`）。

- [ ] **Step 1: 写失败的测试（追加到同一文件）**

```python
def _qr(mode, vector, items):
    st = kbquery.KbStatus(attached=True, mode=mode, vector=vector)
    return kbquery.QueryResult(status=st, items=tuple(items), elapsed_ms=42)


def _ref(i):
    return kbquery.Ref(id=i, kind="case", title=i, score=1.0)


def test_query_appends_one_log_line_per_item(monkeypatch, tmp_path, capsys):
    items = [kbquery.FindingRefs(key="q", label="q", query="慢 SQL 全表扫描",
                                 cases=(_ref("case:1"), _ref("case:2")), clauses=(_ref("rule:1"),))]
    monkeypatch.setattr(kbquery, "from_text", lambda q, kb_dir: _qr("向量库+图文件", "DataVec(覆盖 93%)", items))
    monkeypatch.setenv("GSDB_REPORTS_DIR", str(tmp_path / "reports"))
    (tmp_path / "kb").mkdir()
    assert kbcli.main(["query", "--kb", str(tmp_path / "kb"), "--q", "慢 SQL 全表扫描", "--json"]) == 0
    lines = (tmp_path / "reports" / "kb" / "queries.jsonl").read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[-1])
    assert row["q"] == "慢 SQL 全表扫描" and row["hits_cases"] == 2 and row["hits_rules"] == 1
    assert row["how"] == "semantic" and row["elapsed_ms"] == 42 and row["at"].endswith("Z")


def test_query_how_is_keyword_in_file_mode_or_when_vector_timed_out(monkeypatch, tmp_path, capsys):
    items = [kbquery.FindingRefs(key="q", label="q", query="x")]
    monkeypatch.setenv("GSDB_REPORTS_DIR", str(tmp_path / "reports"))
    (tmp_path / "kb").mkdir()
    monkeypatch.setattr(kbquery, "from_text", lambda q, kb_dir: _qr("文件", "未启用", items))
    kbcli.main(["query", "--kb", str(tmp_path / "kb"), "--q", "x", "--json"])
    monkeypatch.setattr(kbquery, "from_text", lambda q, kb_dir: _qr("向量库", "DataVec·本次超时未用", items))
    kbcli.main(["query", "--kb", str(tmp_path / "kb"), "--q", "x", "--json"])
    hows = [json.loads(l)["how"] for l in (tmp_path / "reports" / "kb" / "queries.jsonl").read_text().splitlines()]
    assert hows == ["keyword", "keyword"]


def test_query_without_reports_dir_logs_nothing_and_output_unchanged(monkeypatch, tmp_path, capsys):
    items = [kbquery.FindingRefs(key="q", label="q", query="x")]
    monkeypatch.setattr(kbquery, "from_text", lambda q, kb_dir: _qr("文件", "未启用", items))
    monkeypatch.delenv("GSDB_REPORTS_DIR", raising=False)
    (tmp_path / "kb").mkdir()
    assert kbcli.main(["query", "--kb", str(tmp_path / "kb"), "--q", "x", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["elapsed_ms"] == 42
    assert not (tmp_path / "reports").exists()
```

- [ ] **Step 2: 跑，确认失败**

Run: `ssh sqlrush@192.168.128.1 'cd ~/gh_agent_k8s && python3 -m pytest agent/tests/test_kb_skill_reports_units.py -q -k query'`
Expected: 2 failed（`queries.jsonl` 不存在）、1 passed

- [ ] **Step 3: 实现**

在 `kb.py` 的 `cmd_query` 之前加：

```python
def _how(status) -> str:
    """大盘要让用户知道自己看到的是语义命中还是关键词命中:文件模式下只有关键词。"""
    semantic = bool(status.mode) and status.mode != kbquery.MODE_FILES and "超时" not in (status.vector or "")
    return "semantic" if semantic else "keyword"


def _log_queries(result) -> None:
    import datetime as _dt
    at = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for it in result.items:
        reports.append_jsonl("kb", "queries", {
            "at": at, "q": it.query, "key": it.key,
            "hits_cases": len(it.cases), "hits_rules": len(it.clauses),
            "how": _how(result.status), "elapsed_ms": result.elapsed_ms})
```

`cmd_query` 在 `print` 之后、`return` 之前加一行 `_log_queries(result)`。

- [ ] **Step 4: 跑，确认通过**

Run: 同 Step 2 去掉 `-k`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add agent/skills/gaussdb-kb/scripts/kb.py agent/tests/test_kb_skill_reports_units.py
git commit -m "feat(kb): query 追加检索日志(命中数与语义/关键词)——知识库大盘「最近检索」的来源"
```

---

### Task 5: WDR 原生报告默认落到 reports/wdr

**Files:**
- Modify: `agent/skills/gaussdb-wdr/scripts/wdr.py:69-72`（构造 `Options` 处）
- Test: `agent/tests/test_reports_hooks_units.py`（追加）

**Interfaces:**
- Produces: `wdr.default_native_path(now: Optional[str] = None) -> str`：`reports_dir()` 为 `None` 返回 `""`；否则返回 `<dir>/wdr/<ts>.native.html`（**并建好 wdr 目录**）。`--save-html` 显式给了就用用户的。

- [ ] **Step 1: 写失败的测试（追加）**

```python
def test_wdr_native_html_defaults_into_reports_dir(rdir, monkeypatch):
    _load("gaussdb-wdr", "model", "thresholds", "util", "collectors", "report", "native", "snaps",
          "interp", "recheck", "finalreport", "ansi", "wdr")
    import wdr, model  # noqa: E402
    seen = {}
    def fake_collect(runner, opt):
        seen["save_html"] = opt.save_html
        return model.Evidence(conn="og")
    monkeypatch.setattr(wdr.access, "for_conn", lambda *a, **k: object())
    monkeypatch.setattr(wdr, "collect_evidence", fake_collect)
    monkeypatch.setattr(wdr.common.config, "resolved_name", lambda c: "og")
    wdr.main(["collect", "-c", "og", "--begin", "1", "--end", "2", "--format", "json"])
    assert seen["save_html"].startswith(str(rdir / "wdr")) and seen["save_html"].endswith(".native.html")
    assert (rdir / "wdr").is_dir(), "目录要先建好,否则 native.py 落盘会失败并把失败写进 note"
    wdr.main(["collect", "-c", "og", "--begin", "1", "--end", "2", "--save-html", "/tmp/x.html", "--format", "json"])
    assert seen["save_html"] == "/tmp/x.html", "用户显式给的路径优先"


def test_wdr_native_path_empty_without_reports_dir(monkeypatch):
    monkeypatch.delenv("GSDB_REPORTS_DIR", raising=False)
    _load("gaussdb-wdr", "wdr")
    import wdr  # noqa: E402
    assert wdr.default_native_path() == ""
```

- [ ] **Step 2: 跑，确认失败**

Run: `ssh sqlrush@192.168.128.1 'cd ~/gh_agent_k8s && python3 -m pytest agent/tests/test_reports_hooks_units.py -q -k native'`
Expected: `AttributeError: module 'wdr' has no attribute 'default_native_path'`

- [ ] **Step 3: 实现**

`wdr.py` 加：

```python
def default_native_path(now: Optional[str] = None) -> str:
    """原生 WDR 报告的默认落点:本人 reports/wdr/<ts>.native.html,大盘上「下载 HTML →」指它。
    没设 GSDB_REPORTS_DIR 时返回空串 —— 与现在一样不落盘。"""
    base = reports.reports_dir()
    if base is None:
        return ""
    d = base / "wdr"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return ""
    return str(d / (reports.utc_stamp(now) + ".native.html"))
```

`wdr.py:69-72` 的 `Options(...)` 里 `save_html=args.save_html or ""` 改为 `save_html=args.save_html or default_native_path()`。

- [ ] **Step 4: 跑，确认通过**

Run: `ssh sqlrush@192.168.128.1 'cd ~/gh_agent_k8s && python3 -m pytest agent/tests/test_reports_hooks_units.py agent/tests/test_wdr_units.py -q'`
Expected: 全 passed

- [ ] **Step 5: 提交**

```bash
git add agent/skills/gaussdb-wdr/scripts/wdr.py agent/tests/test_reports_hooks_units.py
git commit -m "feat(wdr): 原生 WDR 报告默认落到 reports/wdr——大盘「下载 HTML」指它;显式 --save-html 仍优先"
```

---

### Task 6: Pod 侧 —— `podctl serve-reports` 与 entrypoint 接线

**Files:**
- Modify: `docker/podctl.py`（新函数 + 子命令，插在 `log_prune` 之后、`# --- CLI` 之前；`_parser` 与 `main` 各加一段）
- Modify: `docker/entrypoint.sh:31-32`（导出）、`:75-77`（拉起）、`:90`（收掉）
- Test: `docker/tests/test_podctl.py`（追加）、`docker/tests/test_entrypoint_units.py`（新）

**Interfaces:**
- Produces: `podctl.make_reports_server(root: pathlib.Path, port: int) -> http.server.ThreadingHTTPServer`（`port=0` 让 OS 挑，测试用）；子命令 `serve-reports --dir D --port 4097` 调 `serve_forever()`。
- 协议：只 `GET`（其余 405）；请求路径 URL 解码后规范化（`posixpath.normpath`）、去掉前导 `/`，`root / rel` 的 `resolve()` 必须以 `root.resolve()` 为前缀，否则 404；后缀不在 `{.json, .jsonl, .html}` → 404；目录 → 404；成功 200 + `Content-Type`（json → `application/json; charset=utf-8`，jsonl → `application/x-ndjson; charset=utf-8`，html → `text/html; charset=utf-8`）+ `Cache-Control: no-store` + `Content-Length`。不打访问日志到 stderr（`log_message` 置空），只在异常时打一行。

- [ ] **Step 1: 写失败的测试（追加到 `docker/tests/test_podctl.py`）**

```python
# ---- 报告只读端口 ----------------------------------------------------------------

import http.client
import threading


def _serve(tmp_path):
    root = tmp_path / "reports"
    (root / "health").mkdir(parents=True)
    (root / "health" / "latest.json").write_text('{"overall": 2}', encoding="utf-8")
    (root / "kb").mkdir()
    (root / "kb" / "queries.jsonl").write_text('{"q":"a"}\n', encoding="utf-8")
    (root / "wdr").mkdir()
    (root / "wdr" / "x.native.html").write_text("<html>", encoding="utf-8")
    (root / "health" / "secret.txt").write_text("no", encoding="utf-8")
    (tmp_path / "outside.json").write_text("{}", encoding="utf-8")
    srv = podctl.make_reports_server(root, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _get(port, path, method="GET"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request(method, path)
    r = c.getresponse()
    body = r.read()
    hdrs = {k.lower(): v for k, v in r.getheaders()}
    c.close()
    return r.status, hdrs, body


def test_reports_server_serves_json_jsonl_html_with_no_store(tmp_path):
    srv, port = _serve(tmp_path)
    try:
        st, h, body = _get(port, "/health/latest.json")
        assert st == 200 and body == b'{"overall": 2}'
        assert h["content-type"].startswith("application/json") and h["cache-control"] == "no-store"
        assert _get(port, "/kb/queries.jsonl")[0] == 200
        assert _get(port, "/wdr/x.native.html")[1]["content-type"].startswith("text/html")
    finally:
        srv.shutdown(); srv.server_close()


def test_reports_server_refuses_traversal_other_suffixes_dirs_and_non_get(tmp_path):
    """**这是安全边界**:这个端口只能读本人 reports/ 下三种文件,别的一律 404。"""
    srv, port = _serve(tmp_path)
    try:
        assert _get(port, "/../outside.json")[0] == 404
        assert _get(port, "/health/%2e%2e/%2e%2e/outside.json")[0] == 404
        assert _get(port, "/health/secret.txt")[0] == 404
        assert _get(port, "/health/")[0] == 404 and _get(port, "/")[0] == 404
        assert _get(port, "/health/nope.json")[0] == 404
        assert _get(port, "/health/latest.json", method="POST")[0] == 405
    finally:
        srv.shutdown(); srv.server_close()


def test_serve_reports_subcommand_is_wired(monkeypatch, tmp_path):
    """子命令要能从 CLI 到达 make_reports_server —— 不然 entrypoint 里那行是死的。"""
    called = {}
    class _Srv:
        def serve_forever(self): called["ran"] = True
    monkeypatch.setattr(podctl, "make_reports_server", lambda root, port: called.setdefault("args", (root, port)) and _Srv())
    assert podctl.main(["serve-reports", "--dir", str(tmp_path), "--port", "4097"]) == 0
    assert called["args"] == (tmp_path, 4097) and called["ran"]
```

`docker/tests/test_entrypoint_units.py`（新）：

```python
"""entrypoint.sh 的契约——只 grep 结构,不跑它。

报告只读端口是大盘的数据口:entrypoint 要导出 GSDB_REPORTS_DIR、在 opencode 之后拉起
serve-reports、停机时把它和另外两个后台循环一起收掉。漏任何一条都是「大盘无数据且不报错」。
"""
import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_EP = (_ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")


def test_reports_dir_is_exported_under_nas_me():
    assert re.search(r'^export GSDB_REPORTS_DIR=\$NAS_ME/reports\s*$', _EP, re.M), "报告目录必须在 NAS 上,随 Pod 重建不丢"


def test_reports_server_is_started_after_opencode_and_backgrounded():
    oc = _EP.index("opencode serve --hostname")
    m = re.search(r'\$PODCTL serve-reports --dir "\$GSDB_REPORTS_DIR" --port 4097 &\s*\nREPORTS=\$!', _EP)
    assert m and m.start() > oc, "serve-reports 要在 opencode serve 之后、且放后台并记 PID"


def test_reports_server_is_killed_with_the_other_background_loops():
    assert re.search(r'^kill "\$REFRESHER" "\$SYNCER" "\$REPORTS"', _EP, re.M), "停机要连报告服务一起收掉"
```

- [ ] **Step 2: 跑，确认失败**

Run: `ssh sqlrush@192.168.128.1 'cd ~/gh_agent_k8s && python3 -m pytest docker/tests/test_podctl.py docker/tests/test_entrypoint_units.py -q'`
Expected: 3 failed（`AttributeError: make_reports_server`）+ 3 failed（entrypoint 三条）

- [ ] **Step 3: 实现 podctl**

在 `docker/podctl.py` 的 `log_prune` 之后加：

```python
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

        def do_POST(self): self._status(405)
        do_PUT = do_DELETE = do_PATCH = do_POST

    srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), H)
    srv.daemon_threads = True
    return srv
```

`_parser()` 里加：

```python
    p = sub.add_parser("serve-reports")
    p.add_argument("--dir", required=True)
    p.add_argument("--port", type=int, default=4097)
```

`main()` 的分发链里加：

```python
        elif a.cmd == "serve-reports":
            make_reports_server(pathlib.Path(a.dir), a.port).serve_forever()
```

- [ ] **Step 4: 实现 entrypoint**

`docker/entrypoint.sh:32` 之后加：

```bash
export GSDB_REPORTS_DIR=$NAS_ME/reports             # 技能报告存档;大盘经 4097 只读端口读它
```

`:38` 的 `mkdir -p` 列表里加 `"$GSDB_REPORTS_DIR"`。

`OC=$!`（`:77`）之后加：

```bash
# 报告只读端口:大盘的数据口。不是就绪条件,起不来只影响大盘,不影响对话。
$PODCTL serve-reports --dir "$GSDB_REPORTS_DIR" --port 4097 &
REPORTS=$!
```

`:90` 的 `kill "$REFRESHER" "$SYNCER"` 改为 `kill "$REFRESHER" "$SYNCER" "$REPORTS" 2>/dev/null || true`。

- [ ] **Step 5: 跑，确认通过；再跑整个 docker/tests**

Run: `ssh sqlrush@192.168.128.1 'cd ~/gh_agent_k8s && python3 -m pytest docker/tests -q'`
Expected: 全 passed（含 `test_image_contents_units.py`——本任务没加新 .py 文件，COPY 不用改）

- [ ] **Step 6: 提交**

```bash
git add docker/podctl.py docker/entrypoint.sh docker/tests/test_podctl.py docker/tests/test_entrypoint_units.py
git commit -m "feat(pod): 报告只读端口 4097(只 GET/本目录/三种后缀)+ entrypoint 导出 GSDB_REPORTS_DIR 并拉起收掉"
```

---

### Task 7: 清单 —— 4097 端口与网络放行

**Files:**
- Modify: `k8s/templates/runtime.yaml:33`、`:87`
- Modify: `k8s/base/networkpolicy.yaml:19`、`:73`
- Test: `gateway/tests/test_manifest_guards_units.py`（追加）

- [ ] **Step 1: 写失败的测试（追加）**

```python
def test_runtime_exposes_reports_port_and_policies_allow_it():
    """报告只读端口 4097:容器要暴露、Service 要有、runtime 入站要从网关放行、网关出站要到它。
    四处漏一处的表现都是「大盘无数据」,而 Pod 本身健康。"""
    rt = (_ROOT / "k8s" / "templates" / "runtime.yaml").read_text(encoding="utf-8")
    assert re.search(r"containerPort:\s*4097", rt), "runtime.yaml 容器没暴露 4097"
    assert re.search(r"\{port:\s*4097,\s*targetPort:\s*4097", rt), "runtime Service 没有 4097"
    np = (_ROOT / "k8s" / "base" / "networkpolicy.yaml").read_text(encoding="utf-8")
    runtime_pol = np[np.index("name: runtime-policy"):np.index("name: kb-import-policy")]
    assert "4097" in runtime_pol.split("egress:")[0], "runtime-policy 入站没放行 4097"
    gw_pol = np[np.index("name: gateway-policy"):np.index("name: vectordb-policy")]
    assert "4097" in gw_pol.split("egress:")[1], "gateway-policy 出站没到 4097"
    ki = (_ROOT / "k8s" / "templates" / "kb-import.yaml").read_text(encoding="utf-8")
    assert "4097" not in ki, "kb-import 没有大盘,不开报告端口"
```

- [ ] **Step 2: 跑，确认失败**

Run: `ssh sqlrush@192.168.128.1 'cd ~/gh_agent_k8s && python3 -m pytest gateway/tests/test_manifest_guards_units.py -q -k reports_port'`
Expected: FAIL `runtime.yaml 容器没暴露 4097`

- [ ] **Step 3: 改清单**

`runtime.yaml:33`：`ports: [{containerPort: 4096, name: http}, {containerPort: 4097, name: reports}]`
`runtime.yaml:87`：`ports: [{port: 4096, targetPort: 4096, name: http}, {port: 4097, targetPort: 4097, name: reports}]`
`networkpolicy.yaml:19`（runtime-policy 入站）：`ports: [{port: 4096, protocol: TCP}, {port: 4097, protocol: TCP}]`
`networkpolicy.yaml:73`（gateway-policy 出站到用户 Pod）：`ports: [{port: 4096, protocol: TCP}, {port: 4097, protocol: TCP}]`
`networkpolicy.yaml:52` 的注释改为「出站只要 DNS、k8s API、用户 Pod 的 4096 与 4097(报告只读)」。

- [ ] **Step 4: 跑，确认通过**

Run: `ssh sqlrush@192.168.128.1 'cd ~/gh_agent_k8s && python3 -m pytest gateway/tests/test_manifest_guards_units.py -q'`
Expected: 全 passed

- [ ] **Step 5: 提交**

```bash
git add k8s/templates/runtime.yaml k8s/base/networkpolicy.yaml gateway/tests/test_manifest_guards_units.py
git commit -m "feat(k8s): runtime 暴露 4097 报告端口,策略放行网关→4097;kb-import 不开"
```

---

### Task 8: 网关三路分发 + `FRONTEND_SERVICE` 配置项

**Files:**
- Modify: `gateway/config.py:33-50`（`Config` 加字段）、`:100-114`（`load` 读）
- Modify: `k8s/base/configmap-gateway.yaml`（加键）、`k8s/base/gateway.yaml:99`（映射）
- Modify: `gateway/proxy.py:40-53`（`forward_headers` 的 `password` 允许 `None`）
- Modify: `gateway/server.py:163-215`（`_proxy`）
- Test: `gateway/tests/test_config_units.py`、`gateway/tests/test_server_units.py`

**Interfaces:**
- Produces: `Config.frontend_service: str = "frontend"`（env `GATEWAY_FRONTEND_SERVICE`）。`proxy.forward_headers(incoming, password, user_id)`：`password is None` 时不加 `Authorization`。`server.route_for(path) -> str`：返回 `"dash" | "reports" | "opencode"`；`/dash` 与 `/dash/*` → dash，`/reports/*` → reports，其余 → opencode。`/reports/<rest>` 转发给上游的路径是 `/<rest>`。

- [ ] **Step 1: 写失败的测试**

`test_config_units.py` 追加：

```python
def test_frontend_service_defaults_and_overrides():
    assert cfg.load(_MIN).frontend_service == "frontend"
    assert cfg.load({**_MIN, "GATEWAY_FRONTEND_SERVICE": "dash-web"}).frontend_service == "dash-web"
```

`test_server_units.py`：先把假上游改成也回显路径 —— `_Upstream.do_GET` 的 `body = json.dumps({...})` 里加 `"path": self.path`。`stack` fixture 里把对 `Upstream.__init__` 的 patch 改成**同时记录**要求的 (host, port)：

```python
    asked = []
    monkeypatch.setattr("gateway.proxy.Upstream.__init__",
                        lambda self, host, port=4096, connect_timeout=10.0:
                        (asked.append((host, port)), setattr(self, "host", "127.0.0.1"), setattr(self, "port", up_port),
                         setattr(self, "connect_timeout", 2.0)) and None)
    ...
    app.asked = asked          # 测试从这里看网关本想连谁
```

再追加三条测试：

```python
def test_route_for_is_a_pure_function():
    assert server.route_for("/dash") == "dash" and server.route_for("/dash/health?x=1") == "dash"
    assert server.route_for("/reports/health/latest.json") == "reports"
    assert server.route_for("/dashboard") == "opencode" and server.route_for("/reportsx") == "opencode"
    assert server.route_for("/session") == "opencode" and server.route_for("/") == "opencode"


def test_reports_route_goes_to_4097_with_prefix_stripped_and_still_touches(stack):
    base, kube, app = stack
    code, body = _req(base, "/reports/health/latest.json", {idt.HEADER_TRUST: SECRET, "X-Agent-User": "u1234"})
    assert code == 200
    echoed = json.loads(body)
    assert echoed["path"] == "/health/latest.json", "转发给报告端口的路径要去掉 /reports 前缀"
    assert app.asked[-1] == ("runtime-u1234.ns.svc", 4097)
    assert echoed["auth"] == "", "报告端口没有口令,不该注入 Authorization"
    assert any("last-activity" in json.dumps(p) for _k, _n, p in kube.patched), "看大盘也算在用"


def test_dash_route_goes_to_frontend_without_password_or_touch_or_ensure(stack):
    base, kube, app = stack
    code, body = _req(base, "/dash/health", {idt.HEADER_TRUST: SECRET, "X-Agent-User": "u1234"})
    assert code == 200
    echoed = json.loads(body)
    assert app.asked[-1] == ("frontend.ns.svc", 80)
    assert echoed["auth"] == "" and echoed["fwd_user"] == "u1234"
    assert not any("last-activity" in json.dumps(p) for _k, _n, p in kube.patched), "静态页不记活动"
    assert not [n for kind, n, _ in kube.applied if kind == "secrets"], "/dash 不该碰用户的口令 Secret"


def test_dash_route_still_requires_trust(stack):
    code, _ = _req(base := stack[0], "/dash/health", {"X-Agent-User": "u1234"})
    assert code == 403
```

- [ ] **Step 2: 跑，确认失败**

Run: `ssh sqlrush@192.168.128.1 'cd ~/gh_agent_k8s && python3 -m pytest gateway/tests/test_config_units.py gateway/tests/test_server_units.py -q'`
Expected: `AttributeError: 'Config' object has no attribute 'frontend_service'`、`AttributeError: module 'gateway.server' has no attribute 'route_for'` 等

- [ ] **Step 3: 实现 —— 配置三处**

`config.py` `Config` 加 `frontend_service: str = "frontend"`；`load()` 的 `return Config(...)` 里加 `frontend_service=(env.get("GATEWAY_FRONTEND_SERVICE") or "frontend").strip(),`。

`configmap-gateway.yaml` 在 `USER_IMAGE_TAG` 段后加：

```yaml
  # 大盘前端的 Service 名(/dash/* 代理到它)。没部署前端镜像时这条路回 502,不影响对话。
  FRONTEND_SERVICE: "frontend"
```

`gateway.yaml:99` 后加：

```yaml
            - {name: GATEWAY_FRONTEND_SERVICE, valueFrom: {configMapKeyRef: {name: gateway-config, key: FRONTEND_SERVICE, optional: true}}}
```

- [ ] **Step 4: 实现 —— proxy 与 server**

`proxy.py:40-53` `forward_headers`：签名改 `password: Optional[str]`；注入 `Authorization` 的那一行包在 `if password is not None:` 里（其余逻辑不动）。

`server.py` 在 `_Handler` 之前加：

```python
ROUTE_DASH, ROUTE_REPORTS, ROUTE_OPENCODE = "dash", "reports", "opencode"
REPORTS_PORT = 4097


def route_for(path: str) -> str:
    """按前缀分三路。纯函数,单独可测。"""
    p = path.split("?", 1)[0]
    if p == "/dash" or p.startswith("/dash/"):
        return ROUTE_DASH
    if p.startswith("/reports/"):
        return ROUTE_REPORTS
    return ROUTE_OPENCODE
```

`_proxy` 从「取身份」之后改为：

```python
        route = route_for(self.path)
        body = self._read_body()
        if body is None and int(self.headers.get("Content-Length") or 0) > 0:
            return

        if route == ROUTE_DASH:
            # 大盘是静态页 + 浏览器直接调 /reports 与 opencode:这里只转发,不建 Pod、不注口令、不记活动
            up = proxy.Upstream("%s.%s.svc" % (self.app.cfg.frontend_service, self.app.cfg.namespace), 80)
            headers = proxy.forward_headers(self.headers.items(), None, who.user_id)
            path = self.path
            password = None
        else:
            kb_admin = any(r in self.app.cfg.kb_admin_roles for r in who.roles)
            try:
                password = self.app.ensure_ready(who.user_id, kb_admin)
            except pods.PrerequisiteMissing as exc:
                self.app.log("为 %s 准备环境失败(前置条件):%s" % (who.user_id, exc))
                return self._text(503, "您的环境尚未开通,请联系管理员为您开通后再登录。")
            except TimeoutError as exc:
                return self._text(504, "您的环境正在启动,请稍后重试。(%s)" % exc)
            except Exception as exc:                    # noqa: BLE001
                self.app.log("为 %s 准备环境失败:%s" % (who.user_id, exc))
                return self._text(502, "环境准备失败,请联系管理员。")
            host = "runtime-%s.%s.svc" % (who.user_id, self.app.cfg.namespace)
            if route == ROUTE_REPORTS:
                up = proxy.Upstream(host, REPORTS_PORT)
                headers = proxy.forward_headers(self.headers.items(), None, who.user_id)
                path = self.path[len("/reports"):]        # 报告端口以 reports/ 为根
            else:
                up = proxy.Upstream(host)
                headers = proxy.forward_headers(self.headers.items(), password, who.user_id)
                path = self.path

        try:
            resp = up.send(self.command, path, headers, body)
        except OSError as exc:
            self.app.log("转发到 %s(%s)失败:%s" % (who.user_id, route, exc))
            return self._text(502, "大盘服务未部署或不可达。" if route == ROUTE_DASH
                              else "无法连接到您的环境,请稍后重试。")

        self.send_response(resp.status)
        for k, v in proxy.response_headers(resp.getheaders()):
            self.send_header(k, v)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        touch = (lambda: self.app.mgr.touch(who.user_id)) if route != ROUTE_DASH else (lambda: None)
        touch()
        try:
            proxy.pump(resp, self._write_chunk, self.wfile.flush, on_bytes=lambda _n: touch())
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            resp.close()
```

- [ ] **Step 5: 跑，确认通过；再跑整个 gateway/tests（含清单守卫——它会检查 `GATEWAY_FRONTEND_SERVICE` 三处齐全）**

Run: `ssh sqlrush@192.168.128.1 'cd ~/gh_agent_k8s && python3 -m pytest gateway/tests -q'`
Expected: 全 passed

- [ ] **Step 6: 提交**

```bash
git add gateway/config.py gateway/proxy.py gateway/server.py k8s/base/configmap-gateway.yaml k8s/base/gateway.yaml \
        gateway/tests/test_config_units.py gateway/tests/test_server_units.py
git commit -m "feat(gateway): 三路分发——/dash→前端 Service(不注口令不记活动),/reports→用户 Pod 4097(去前缀),其余→opencode"
```

---

### Task 9: 同步技能侧改动到 gh_skill

**Files:**（目标仓库 `~/gh_skill/opencode_skill-main-v2-0729`，路径对应关系：`agent/common/` ↔ `common/`，`agent/skills/` ↔ `skills/`，`agent/tests/` ↔ `tests/`）
- Copy: `common/reports.py`、`tests/test_reports_units.py`、`tests/test_reports_hooks_units.py`、`tests/test_kb_skill_reports_units.py`
- Apply same edits: `skills/gaussdb-health/scripts/{report.py,health.py}`、`skills/gaussdb-topsql/scripts/topsql.py`、`skills/gaussdb-wdr/scripts/wdr.py`、`skills/gaussdb-sqltune/scripts/sqltune.py`、`skills/gaussdb-kb/scripts/kb.py`
- Modify: `agent/UPSTREAM`（本仓库）追加一行同步记录

- [ ] **Step 1: 拷文件并按 Task 2–5 的 diff 逐个 apply（用 `git -C ~/gh_agent_k8s diff <上一次同步点>..HEAD -- agent/skills/... | git -C ~/gh_skill/... apply -p2`，冲突处手改）**

- [ ] **Step 2: 在 gh_skill 跑全套**

Run: `ssh sqlrush@192.168.128.1 'cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest -q 2>&1 | tail -3'`
Expected: 全 passed（新加的三份测试都在里面）。gh_skill 没有 `common.finding` 时 `test_reports_hooks_units.py` 用 `test_kb_query_units.py` 同款的本地替身。

- [ ] **Step 3: 两边提交**

```bash
cd ~/gh_skill/opencode_skill-main-v2-0729 && git add common/reports.py skills tests && \
  git commit -m "feat(reports): 报告存档 + kb health --json + 检索日志 + wdr 原生报告落 reports(与 gh_agent_k8s 同一份)" && git push
cd ~/gh_agent_k8s && echo "synced $(date +%F): gh_skill <commit> — common/reports.py、五个技能存档钩子、kb health --json 与检索日志、wdr 原生报告落点、三份测试" >> agent/UPSTREAM && \
  git add agent/UPSTREAM && git commit -m "docs(upstream): 记录报告存档一组改动已同步到 gh_skill"
```

---

### Task 10: 本地集群端到端

**Files:**
- Create: `scripts/k8s/e2e-reports.sh`

**Interfaces:**
- 前提：Mac 本地集群（`kubectl --context orbstack`）网关在跑；镜像重建为新标签（`scripts/build-images.sh dev-reports`）并把 `configmap-gateway.yaml` 的 `USER_IMAGE_TAG` 临时指到它；用一次性工号 `u9400`（**不要碰 u9001–u9003、u9100、u9300**，那是用户的测试环境）。

- [ ] **Step 1: 写脚本**

```bash
#!/usr/bin/env bash
# 报告存档端到端:新工号登录 → 会话里跑健康检查 → 经网关 GET /reports/health/latest.json 得到 200 且形状对。
# 用一次性工号,跑完删掉。前提:网关已滚到含三路分发的版本,USER_IMAGE_TAG 指向含 serve-reports 的镜像。
set -euo pipefail
NS=gaussdb-agent; CTX=${CTX:-orbstack}; U=${U:-u9400}
TRUST=$(kubectl --context "$CTX" -n $NS get secret gateway-auth -o jsonpath='{.data.TRUST_SECRET}' | base64 -d)
kubectl --context "$CTX" -n $NS create secret generic grmp-token-$U --from-literal=GRMP_AUTH_TOKEN=e2e --dry-run=client -o yaml | kubectl --context "$CTX" apply -f - >/dev/null
kubectl --context "$CTX" -n $NS port-forward svc/gateway 18081:80 >/dev/null 2>&1 & PF=$!; trap 'kill $PF 2>/dev/null; kubectl --context "$CTX" -n $NS delete deploy,svc runtime-$U --ignore-not-found >/dev/null; kubectl --context "$CTX" -n $NS delete secret grmp-token-$U pod-auth-$U --ignore-not-found >/dev/null' EXIT
sleep 3
H=(-H "X-Agent-Trust: $TRUST" -H "X-Agent-User: $U")
echo "① 建环境";      curl -s -o /dev/null -w "%{http_code}\n" "${H[@]}" http://127.0.0.1:18081/global/health | grep -qx 200
echo "② 报告端口通,但还没报告 → 404"; curl -s -o /dev/null -w "%{http_code}\n" "${H[@]}" http://127.0.0.1:18081/reports/health/latest.json | grep -qx 404
echo "③ 在会话里跑健康检查"
SID=$(curl -s "${H[@]}" -H 'Content-Type: application/json' -d '{"title":"e2e-reports"}' http://127.0.0.1:18081/session | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
curl -s "${H[@]}" -H 'Content-Type: application/json' -d '{"parts":[{"type":"text","text":"先登录 10.0.0.9 的 postgres 库,然后做一次健康检查,简短回答。"}]}' http://127.0.0.1:18081/session/$SID/message >/dev/null
echo "④ 报告到了"
for i in $(seq 1 30); do curl -s -o /tmp/latest.json -w "%{http_code}" "${H[@]}" http://127.0.0.1:18081/reports/health/latest.json | grep -qx 200 && break; sleep 2; done
python3 -c 'import json;d=json.load(open("/tmp/latest.json"));assert "dims" in d and "overall" in d and "sub_skills" in d;print("   overall=%s dims=%d findings=%d"%(d["overall"],len(d["dims"]),len(d["findings"])))'
echo "⑤ index 与越界"
curl -s "${H[@]}" http://127.0.0.1:18081/reports/health/index.json | python3 -c 'import json,sys;i=json.load(sys.stdin);assert len(i)>=1;print("   index 条数",len(i))'
curl -s -o /dev/null -w "%{http_code}\n" "${H[@]}" 'http://127.0.0.1:18081/reports/../gdaa/config.yaml' | grep -qx 404
echo "⑥ /dash 在前端没部署时是 502 且说人话"
curl -s -w "\n%{http_code}\n" "${H[@]}" http://127.0.0.1:18081/dash/health | tail -1 | grep -qx 502
echo "ALL OK"
```

- [ ] **Step 2: 构建镜像、滚网关、跑脚本**

Run（Mac）：
```bash
export PATH=$HOME/.orbstack/bin:/usr/local/bin:$PATH; cd ~/gh_agent_k8s
scripts/build-images.sh dev-reports
kubectl --context orbstack -n gaussdb-agent patch configmap gateway-config --type merge -p '{"data":{"USER_IMAGE_TAG":"dev-reports"}}'
kubectl --context orbstack apply -k k8s/base            # 清单里的 4097 与 FRONTEND_SERVICE
kubectl --context orbstack -n gaussdb-agent set image deploy/gateway gateway=gaussdb-agent-gateway:dev-reports
kubectl --context orbstack -n gaussdb-agent rollout status deploy/gateway --timeout=180s
bash scripts/k8s/e2e-reports.sh
```
Expected: 打印 ①–⑥ 与 `ALL OK`。任一步失败先看 `kubectl -n gaussdb-agent logs deploy/runtime-u9400 | grep -i "报告\|serve-reports"`。

- [ ] **Step 3: 把 gateway-config 的 `USER_IMAGE_TAG` 改回用户测试环境原来的值（`kubectl get cm gateway-config -o yaml` 里改回 `agent-v1.0.4-oc1.18.27`），提交脚本**

```bash
git add scripts/k8s/e2e-reports.sh
git commit -m "test(e2e): 报告存档端到端——登录→会话跑 health→经网关读 /reports/health/latest.json"
```

---

### Task 11: 文档

**Files:**
- Modify: `docs/参数手册.md`（「平台注入」表加 `GSDB_REPORTS_DIR`；`gateway-config` 表加 `FRONTEND_SERVICE`）
- Modify: `docs/仓库结构说明.md`（`agent/common` 表加 `reports.py`；`docker/` 表 `podctl.py` 一句加 `serve-reports`；「加一个可配参数」那行不用改）
- Modify: `docs/对接清单-各方要做什么.md` §3.4 放行表加一行「网关 → 用户容器 4097（报告只读）」；§3.3 不变
- Modify: `docs/功能清单-容器版.md` 「二、接入网关」后加一条「报告存档与只读端口（大盘数据口）」

- [ ] **Step 1: 改四份文档（每处一两行，按上面列的位置）**
- [ ] **Step 2: 重新出 HTML 并跑打包守卫**

Run: `ssh sqlrush@192.168.128.1 'cd ~/gh_agent_k8s && for f in 参数手册 仓库结构说明 对接清单-各方要做什么 功能清单-容器版; do python3 scripts/md2html.py docs/$f.md dist/客户交付-v1.0.4/x-$f.html; done; python3 -m pytest scripts/k8s/tests -q | tail -2'`
Expected: 4 个 HTML 生成，守卫全 passed

- [ ] **Step 3: 提交**

```bash
git add docs/参数手册.md docs/仓库结构说明.md docs/对接清单-各方要做什么.md docs/功能清单-容器版.md
git commit -m "docs: 报告存档目录、报告只读端口 4097、FRONTEND_SERVICE 写进参数手册/结构说明/对接清单/功能清单"
```

---

## 收尾

- [x] 全套：`ssh sqlrush@192.168.128.1 'cd ~/gh_agent_k8s && python3 -m pytest -q | tail -2'` 全 passed。
- [x] `git push`；`agent/UPSTREAM` 已记同步。
- [ ] 第二份计划（前端镜像本体）从这里开始写：上游 vendored 方式、profile 与 bundle 的 `cordis.patch.yml`、`ui-gaussdb-dash` / `ui-gaussdb-brand`、`Dockerfile.frontend`、`k8s/base/frontend.yaml`、四个页面、e2e。

## 执行记录（2026-09-22/23）

| 任务 | 提交 | 偏差 |
|---|---|---|
| 1 存档核心 | `e908666` | 无 |
| 2 四个技能钩子 | `28263aa` | 无 |
| 3 kb health --json | `f5a15aa` | 无 |
| 4 kb 检索日志 | `4315df7` | 无 |
| 5 wdr 原生报告落点 | `7068932` | 无 |
| 6 Pod 只读端口 + entrypoint | `349a325` | 无 |
| 7 清单 4097 | `4987042` + `66be576` | **本地覆盖层 `k8s/local/networkpolicy-local.yaml` 按名字整个替换两条策略，计划漏了它**；e2e 前发现，补上并让守卫连本地覆盖层一起盯 |
| 8 网关三路分发 | `281e643` | 无 |
| 测试隔离修正 | `6855339` | 两份同名 `kb.py` 在全套里互相顶掉，单跑绿全套红；改成按路径、唯一模块名加载（仓库已有做法） |
| 9 同步 gh_skill | gh_skill `9a3768f`，本仓库 `cd60c89` | gh_skill 那边 query/health 在 `kb_store.py` 不在 `kb.py`，补丁那一份手工移植；测试也改成经 `kb.py` 的 `main` 分发 |
| 10 e2e | 本提交 | 跑了五轮才绿，三个原因：① `curl` 在客户端把 `/reports/../x` 规范化，越界检查测的是 curl（改 `--path-as-is`）；② NAS 留着上一轮报告，「还没报告 → 404」假红（改成看 index 份数增加）；③ **真 bug** `8a9b83b`：网关 `HTTPConnection(timeout=connect_timeout)` 同时管读响应，同步的 `POST …/message` 等模型跑完才回头，10 秒就被掐成 502——Web 走 `prompt_async`+SSE 一直没暴露。另：镜像被 kubelet 回收两次（脚本加了预检，立刻报「先 build」而不是 120 秒后 504）；Docker Hub 一次 Bad Gateway 让重建失败、网关滚动卡 1/2，恢复后踢掉卡住的副本即好 |
| 11 文档 | `5f8ec1b` | 无 |

收尾时把测试环境的 `USER_IMAGE_TAG` 还原为 `agent-v0.9.3-oc1.18.27`；网关留在 `dev-reports`（含三路分发与超时修复，向后兼容）。
