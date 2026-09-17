# 阶段 1：技能侧改动 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 gh_skill 技能集能在只读镜像 + NAS 目录的 Pod 里运行，并把知识库导入功能物理拆到独立 skill，发布为 `skills-v13.0`。

**Architecture:** 先把 gh_skill `skills-v12.9` 的技能代码导入本仓库 `agent/`（Task 0），之后六个互相独立的小改动都在 `agent/` 里做，每项一个提交；标「两仓库」的三项（Task 1、2、5）同一份 diff 再提交到 gh_skill。不改 opencode，不改白名单，不改安全红线正文。gaussdb-kb 拆成 `gaussdb-kb`（查询：query / health / search / cite-check）与 `gaussdb-kb-import`（ingest / index / validate / setup / feedback / eval / propose / review / apply / contract），共用 `common/kb/`；两个 skill 各自的 `kb.py` 只是命令入口，规则文件的读取辅助函数搬进 `common/kb/rulesfile.py`。

**Tech Stack:** Python 3.9（Mac `~/p2venv/bin/python3` 3.12 也要过）、pytest、PyYAML；仓库 `~/gh_skill/opencode_skill-main-v2-0729`。

**Spec:** `gh_agent_k8s/docs/specs/2026-09-15-k8s-containerization-design.md` §10、§7

## Global Constraints

- Python 依赖白名单不变：psycopg2、cryptography、PyYAML。不加新依赖。
- 每个 skill 目录必须有 `SKILL.md`，frontmatter 含 `name`、`version`、`description`；红线块 `RED-LINES:BEGIN/END` 恰好一对，由 `tools/inject_red_lines.py` 注入。
- 安全红线正文 `common/red_lines.md` 一字不改。
- 所有错误必须是明确文案，不能是 Traceback；「静默失效比报错危险」。
- 提交信息中文 conventional commit，结尾两行：
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01NFTD813rWESLz4NzmGi4NZ`
- **路径约定**：Task 1 起所有相对路径（`common/…`、`skills/…`、`tests/…`、`tools/…`、`AGENTS.md`）都以本仓库 `agent/` 为根。测试在 Mac 上跑：`cd ~/gh_agent_k8s/agent && PATH=$HOME/p2venv/bin:$PATH python3 -m pytest tests/<文件> -q`。**PATH 必须把 venv 放前面**：`deploy.sh`、`install-opencode.sh` 的 e2e 测试会调 PATH 上的 `python3`，系统 `/usr/bin/python3` 目前被 Xcode 许可挡住（Task 0 基线：不改 PATH 时这两个文件 28 个失败，改了全绿 2170 通过、45 跳过）。
- **开发规范**：Pod 适配代码只在本仓库；标「两仓库」的任务（1、2、5）把同一份 diff 也提交到 gh_skill（`~/gh_skill/opencode_skill-main-v2-0729`，从 main 开分支 `fix/k8s-shared`），那边的测试命令是 `cd ~/gh_skill/opencode_skill-main-v2-0729 && ~/p2venv/bin/python3 -m pytest …`。标「仅本仓库」的任务（3、4、6）不碰 gh_skill。
- 本仓库直接在 `main` 上提交（仓库刚建，没有别的分支）。

---

## 文件结构

| 文件 | 责任 | 任务 |
|---|---|---|
| `common/config.py` | `_DEFAULT_STATE_DIR` 常量；`ensure_dir()` 不可写时报 `ConfigError` | 1 |
| `common/session.py` | `_read()` 对 `driver == "grmp"` 的会话用当前中间件地址覆盖 host/port | 2 |
| `common/audit.py`（新） | `actor_id()`：读 `GSDB_USER_ID` | 3 |
| `common/finding.py` | `findings_to_json` 信封加 `user_id` | 3 |
| `skills/gaussdb-health/scripts/report.py` | 报告标题下加「执行人」行 | 3 |
| `common/kb/rulesfile.py`（新） | 从 `kb.py` 搬出：`read_text_file`、`split_frontmatter`、`load_rule_file`、`rule_status`、`iter_files`、`first_heading`、`iter_active_rules`、`STATUS_*`、`RULE_DIRS`、`SEARCHABLE`、`SEARCH_HIT_CAP` | 4 |
| `skills/gaussdb-kb-import/`（新目录） | 现 `gaussdb-kb` 整体搬过去：`scripts/kb.py`、`kb_store.py`、`kb_cases.py`、`references/`、`testdata/`、`SKILL.md`（导入版） | 4 |
| `skills/gaussdb-kb/`（收缩） | `scripts/kb.py`（新写：query / health / search / cite-check + 导入命令桩）、`scripts/kb_cite.py`（搬回）、`SKILL.md`（查询版） | 4、6 |
| `common/kb/lock.py`（新） | `hold(kb_dir)` 写入互斥 | 5 |
| `common/kb/atomic.py`（新） | `write_text_atomic(path, text)` | 5 |
| `tests/test_kb_*.py` | 路径从 `gaussdb-kb` 改到 `gaussdb-kb-import`；新增测试 | 4、5、6 |
| `tests/test_red_lines_units.py`、`tools/inject_red_lines.py` | 17 → 18 | 4 |
| `AGENTS.md` | 技能匹配拆两条；上传说明指向导入 skill | 4 |

---

### Task 0: 把 gh_skill skills-v12.9 导入 agent/（仅本仓库）

**Files:**
- Create: `scripts/vendor-from-gh-skill.sh`（已写好）
- Create: `agent/`（脚本产出）、`agent/UPSTREAM`

**Interfaces:**
- Produces: `agent/` 下与 gh_skill 相同的布局：`common/`、`skills/`（17 个）、`scripts/registry/`、`scripts/kb/`、`tools/`、`tests/`、`grmp_middleware/`、`AGENTS.md`、`install-opencode.sh`、`deploy.sh`、`requirements.txt`、`pytest.ini`；`agent/UPSTREAM` 两行：`gh_skill skills-v12.9 6190e4a…` 与导入日期。不含 `docs/`、`demo/`、`tests/test_delivery_drift_units.py`（依赖客户交付物目录）。

- [x] **Step 1: 导入**（2026-09-17 完成，提交 `efaf82c`）

```bash
cd ~/gh_agent_k8s && bash scripts/vendor-from-gh-skill.sh ~/gh_skill/opencode_skill-main-v2-0729 skills-v12.9
cat agent/UPSTREAM; ls agent/skills | wc -l     # 17
```

- [x] **Step 2: 基线测试必须和 gh_skill 一样绿**（2170 通过、45 跳过；不把 venv 放 PATH 前面时 28 个失败，gh_skill 原仓库同样 28 个，是 Mac 系统 Python 被 Xcode 许可挡住）

```bash
cd ~/gh_agent_k8s/agent && PATH=$HOME/p2venv/bin:$PATH python3 -m pytest tests -q -p no:cacheprovider 2>&1 | tail -3
```

- [x] **Step 3: 提交**

```bash
cd ~/gh_agent_k8s && git add scripts/vendor-from-gh-skill.sh agent && git commit -m "feat(agent): 导入 gh_skill skills-v12.9 作为 Pod 适配的起点

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NFTD813rWESLz4NzmGi4NZ"
```

---

### Task 1: GSDB_HOME 缺省目录不可写时明确报错（两仓库）

**Files:**
- Modify: `common/config.py:158-181`（`state_dir`、`ensure_dir`）
- Test: `tests/test_config_units.py`

**Interfaces:**
- Produces: `common.config._DEFAULT_STATE_DIR: pathlib.Path`（模块常量，测试可 monkeypatch）；`ensure_dir()` 在默认目录不可创建时抛 `ConfigError`，文案含 `GSDB_HOME`。

- [ ] **Step 1: 写失败的测试**

在 `tests/test_config_units.py` 末尾追加：

```python
def test_ensure_dir_explains_when_default_dir_is_not_writable(tmp_path, monkeypatch):
    """镜像只读、GSDB_HOME 又没设时,要的是一句话,不是 PermissionError 堆栈。"""
    import os
    if os.geteuid() == 0:
        pytest.skip("root 不受目录权限约束")
    from common import config
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    monkeypatch.delenv("GSDB_HOME", raising=False)
    monkeypatch.delenv("GDAA_HOME", raising=False)
    monkeypatch.setattr(config, "_DEFAULT_STATE_DIR", ro / "state")
    try:
        with pytest.raises(ConfigError) as exc:
            config.ensure_dir()
    finally:
        ro.chmod(0o700)
    msg = str(exc.value)
    assert "GSDB_HOME" in msg and str(ro / "state") in msg


def test_ensure_dir_uses_gsdb_home_when_set(tmp_path, monkeypatch):
    from common import config
    monkeypatch.setenv("GSDB_HOME", str(tmp_path / "home"))
    assert config.ensure_dir() == tmp_path / "home"
    assert (tmp_path / "home").is_dir()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `~/p2venv/bin/python3 -m pytest tests/test_config_units.py -q -k ensure_dir`
Expected: 第一条 FAIL，`AttributeError: ... has no attribute '_DEFAULT_STATE_DIR'`

- [ ] **Step 3: 最小实现**

把 `common/config.py` 的 `state_dir` / `ensure_dir` 改成（删掉中间那段注释掉的旧代码）：

```python
# 没设 GSDB_HOME 时的落点。现场沙箱是 skills 安装目录里的 common/;进镜像后这里是只读的,
# 所以 ensure_dir() 建不出目录时必须说清「设 GSDB_HOME」,不能让 PermissionError 直接冒出来。
_DEFAULT_STATE_DIR = pathlib.Path("/workspace/.opencode/skills/common")


def state_dir() -> pathlib.Path:
    base = os.environ.get("GSDB_HOME") or os.environ.get("GDAA_HOME")
    if base:
        return pathlib.Path(base)
    return _DEFAULT_STATE_DIR


def ensure_dir() -> pathlib.Path:
    """Return the state directory, creating it with 0700 if absent."""
    base = state_dir()
    try:
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(base, 0o700)
    except OSError as exc:
        raise ConfigError(
            "状态目录 %s 无法创建或写入(%s)。请设置环境变量 GSDB_HOME 指向一个可写目录"
            "(容器里应指向持久卷,例如 /nas/me/gdaa)。" % (base, exc.strerror or exc)) from exc
    return base
```

- [ ] **Step 4: 跑测试确认通过**

Run: `~/p2venv/bin/python3 -m pytest tests/test_config_units.py tests/test_session_units.py -q`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add common/config.py tests/test_config_units.py
git commit -m "fix(config): GSDB_HOME 未设且默认目录不可写时给出明确提示,不再抛 PermissionError

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NFTD813rWESLz4NzmGi4NZ"
```

- [ ] **Step 6: 同步到 gh_skill**

```bash
cd ~/gh_agent_k8s && git format-patch -1 --stdout -- agent/common/config.py agent/tests/test_config_units.py > /tmp/t1.patch
cd ~/gh_skill/opencode_skill-main-v2-0729 && git checkout -q -b fix/k8s-shared main 2>/dev/null || git checkout -q fix/k8s-shared
git am -p2 --directory=. /tmp/t1.patch      # agent/ 前缀被 -p2 去掉
~/p2venv/bin/python3 -m pytest tests/test_config_units.py tests/test_session_units.py -q
```
Expected: 打上补丁、测试 PASS。`git am` 冲突时用 `git apply -p2 --3way /tmp/t1.patch` 手工合并后按同样的提交信息提交。

---

### Task 2: api 模式会话不再沿用文件里的中间件地址（两仓库）

**Files:**
- Modify: `common/session.py:124-150`（`_read`）
- Test: `tests/test_session_units.py`

**Interfaces:**
- Consumes: `common.config.api_endpoint() -> ApiEndpoint`（`resolve_host()` 取 `GRMP_API_HOST` 环境变量，否则 config；`port` 取 config）。
- Produces: `session._read()` 返回的 `Connection`，当 `driver == "grmp"` 时 `host`/`port` 来自 `api_endpoint()`；配置里没有 `api_connection` 时保留文件里的值（不让老环境因此登不上）。

- [ ] **Step 1: 写失败的测试**

在 `tests/test_session_units.py` 末尾追加：

```python
def _api_config(home_dir: pathlib.Path, host: str, port: int = 8080) -> None:
    (home_dir / "config.yaml").write_text(
        "connection_mode: api\napi_connection:\n  - host: %s\n    port: %d\n" % (host, port),
        encoding="utf-8")


def test_grmp_session_takes_middleware_address_from_current_config(home, monkeypatch):
    """会话文件是登录那一刻的快照;中间件换了地址,老对话的句柄要打到新地址(09-14 现场问题)。"""
    _api_config(home, "old-grmp.internal")
    conn = Connection(name="a", type="gaussdb", host="old-grmp.internal", port=8080,
                      database="db1", user="grmp", driver="grmp", data_ip="10.0.0.5", app="api")
    handle = session.save(conn)
    _api_config(home, "new-grmp.internal", 9090)
    monkeypatch.delenv("GRMP_API_HOST", raising=False)
    session.use(handle)
    got = session.current()
    assert got.host == "new-grmp.internal" and got.port == 9090
    assert got.data_ip == "10.0.0.5" and got.database == "db1"


def test_grmp_session_prefers_env_host_over_config(home, monkeypatch):
    _api_config(home, "cfg-grmp.internal")
    conn = Connection(name="a", type="gaussdb", host="x", port=8080,
                      database="db1", user="grmp", driver="grmp", data_ip="10.0.0.5", app="api")
    handle = session.save(conn)
    monkeypatch.setenv("GRMP_API_HOST", "env-grmp.internal")
    session.use(handle)
    assert session.current().host == "env-grmp.internal"


def test_gsql_session_keeps_its_own_host(home, monkeypatch):
    conn = Connection(name="og", type="opengauss", host="db-host", port=5432,
                      database="db1", user="u", driver="gsql")
    handle = session.save(conn)
    session.use(handle)
    assert session.current().host == "db-host" and session.current().port == 5432
```

`home` fixture 已存在（设 `GSDB_HOME` 到 tmp 目录）。

- [ ] **Step 2: 跑测试确认失败**

Run: `~/p2venv/bin/python3 -m pytest tests/test_session_units.py -q -k "middleware_address or env_host or gsql_session"`
Expected: 前两条 FAIL（host 仍是旧值），第三条 PASS

- [ ] **Step 3: 最小实现**

`common/session.py` 顶部 import 改为 `from .config import Connection, ConfigError, api_endpoint, ensure_dir, state_dir, validate`，`from dataclasses import dataclass, replace`。`_read()` 里 `validate(conn)` 之前插入：

```python
    if conn.driver == "grmp":
        # 会话文件只是登录那一刻的快照。中间件地址属于环境配置,不属于会话:
        # 地址变更后老句柄仍要能用,所以每次读会话都从当前配置取(环境变量 > config.yaml)。
        try:
            ep = api_endpoint()
        except ConfigError:
            ep = None                         # 没配 api_connection 的老环境:保留文件里的值
        if ep is not None:
            conn = replace(conn, host=ep.resolve_host(), port=ep.port)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `~/p2venv/bin/python3 -m pytest tests/test_session_units.py tests/test_login_session_units.py tests/test_login_config_units.py tests/test_sqlreview_units.py -q`
Expected: 全部 PASS

- [ ] **Step 5: 会话 e2e 加一例**

在 Mac `~/kf-verify/kf_session_e2e.sh` 的末尾（`summary` 之前）加：

```bash
# S19 改中间件地址后老句柄打新地址
H=$(login_api 10.0.0.5 db1 | grep -o -- '--session [a-z0-9]*' | cut -d' ' -f2)
GRMP_API_HOST=127.0.0.1 GRMP_API_PORT_OVERRIDE= expect_ok "S19 old handle follows new host" \
  "GSDB_SESSION=$H python3 $S/gaussdb-login/scripts/login.py --status $H | grep -q 127.0.0.1"
```

（`login_api`、`expect_ok` 是该脚本已有的函数；`--status` 输出的地址应为当前解析结果。）
Run: `bash ~/kf-verify/kf_session_e2e.sh`
Expected: 19/19

- [ ] **Step 6: 提交**

```bash
git add common/session.py tests/test_session_units.py
git commit -m "fix(session): api 模式会话每次从当前配置取中间件地址,地址变更后老句柄仍可用

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NFTD813rWESLz4NzmGi4NZ"
```

- [ ] **Step 7: 同步到 gh_skill**

```bash
cd ~/gh_agent_k8s && git format-patch -1 --stdout -- agent/common/session.py agent/tests/test_session_units.py > /tmp/t2.patch
cd ~/gh_skill/opencode_skill-main-v2-0729 && git checkout -q fix/k8s-shared && git am -p2 /tmp/t2.patch
~/p2venv/bin/python3 -m pytest tests/test_session_units.py tests/test_login_session_units.py tests/test_login_config_units.py tests/test_sqlreview_units.py -q
```
Expected: PASS。gh_skill 的会话 e2e（`~/kf-verify/kf_session_e2e.sh` 的 `S` 变量指向 gh_skill 的 skills）也要跑到 19/19。

---

### Task 2b: 连不上中间件的网络异常都变成带地址的 GrmpError（两仓库；执行中发现，2026-09-17 完成）

做 Task 2 的 e2e 时发现 v12.9 就有的缺陷：`GrmpClient._post` 只接 `URLError`，`http.client.RemoteDisconnected` 这类异常直接 Traceback；报错文案只写 path 不写地址，S19「打到了新地址」无从断言。

- Modify: `common/grmp/client.py:95-108`——`except (urllib.error.URLError, http.client.HTTPException, OSError)`，两处文案追加 `（中间件 <base_url>）`；path 开头的原格式保留（`test_grmp_hints_client_units.py:114` 断言 `请求 /x 失败`）。
- Test: `tests/test_grmp_client_network_units.py`：RemoteDisconnected / ConnectionRefusedError / socket.timeout / URLError 四种都变成含 `host:port` 的 GrmpError；HTTPError 仍带状态码与地址。
- e2e：`kf_session_e2e.sh` S19 改用 vacuum（health 会把访问失败吞成「未采集」并照样打印 🟢，不适合做这个断言）；`PY` 改为可覆盖。20/20。
- 提交：gh_agent_k8s `fix(grmp)`；gh_skill `fix/k8s-shared` 同一补丁。

---

### Task 3: findings 信封与 health 报告带执行人（仅本仓库）

**Files:**
- Create: `common/audit.py`
- Modify: `common/finding.py:76-82`（`findings_to_json`）
- Modify: `skills/gaussdb-health/scripts/report.py:81-86`（`render_health` 标题后）
- Test: `tests/test_audit_units.py`（新）、`tests/test_finding_units.py`（若不存在则新建，下同）

**Interfaces:**
- Produces: `common.audit.actor_id() -> str`（`GSDB_USER_ID` 去首尾空白，未设为空串）；`findings_to_json` 输出 JSON 顶层多一个 `"user_id"` 键（空串时也带，形状固定）；`render_health` 在标题行后、空行前输出 `执行人：<id>` 一行（空串时不输出）。

- [ ] **Step 1: 写失败的测试**

`tests/test_audit_units.py`：

```python
"""执行人:容器里每个 Pod 一个人,GSDB_USER_ID 由平台注入;报告和 findings 都要能落到人。"""
import json
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from common import audit  # noqa: E402
from common.finding import Finding, Severity, findings_to_json  # noqa: E402


def test_actor_id_reads_env(monkeypatch):
    monkeypatch.setenv("GSDB_USER_ID", " u12345 ")
    assert audit.actor_id() == "u12345"


def test_actor_id_empty_when_unset(monkeypatch):
    monkeypatch.delenv("GSDB_USER_ID", raising=False)
    assert audit.actor_id() == ""


def _finding():
    return Finding(dimension="lock", code="LOCK_WAIT", severity=Severity.WARN, metric="waiters",
                   value=3, threshold=1, evidence="x")


def test_findings_json_carries_user_id(monkeypatch):
    monkeypatch.setenv("GSDB_USER_ID", "u12345")
    payload = json.loads(findings_to_json([_finding()], "lockwait"))
    assert payload["user_id"] == "u12345" and payload["skill"] == "lockwait"


def test_findings_json_user_id_is_empty_string_when_unset(monkeypatch):
    monkeypatch.delenv("GSDB_USER_ID", raising=False)
    assert json.loads(findings_to_json([], "lockwait"))["user_id"] == ""
```

`Finding` 的构造参数以 `common/finding.py:42` 的 dataclass 字段为准；若字段名不同，按实际字段改测试里的 `_finding()`，不改断言。

- [ ] **Step 2: 跑测试确认失败**

Run: `~/p2venv/bin/python3 -m pytest tests/test_audit_units.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'common.audit'`

- [ ] **Step 3: 最小实现**

`common/audit.py`：

```python
"""执行人标识。

容器部署下每个 Pod 只服务一个人,平台把工号放进 GSDB_USER_ID;报告与 findings 带上它,
审计才能落到人。单机沙箱没有这个变量,所有输出保持原样。
"""
from __future__ import annotations

import os

ENV_USER_ID = "GSDB_USER_ID"


def actor_id() -> str:
    return (os.environ.get(ENV_USER_ID) or "").strip()
```

`common/finding.py` 的 `findings_to_json` 改为：

```python
def findings_to_json(findings: list[Finding], skill: str) -> str:
    """序列化成 health 认得的形状，并盖上来源 skill 名与执行人。"""
    from .audit import actor_id
    stamped = [replace(f, skill=skill) for f in findings]
    return json.dumps(
        {"skill": skill, "user_id": actor_id(), "findings": [f.to_dict() for f in stamped]},
        ensure_ascii=False, indent=2)
```

`skills/gaussdb-health/scripts/report.py` 的 `render_health` 里，两处 `out = f"# Health Evidence — ..."` 之后加：

```python
    from common.audit import actor_id
    if actor_id():
        out = out.rstrip("\n") + f"\n执行人：{actor_id()}\n\n"
```

（health 的脚本目录已把仓库根加进 `sys.path`，`common` 可直接 import，与同文件里其他 `common.` 导入一致。）

- [ ] **Step 4: 跑测试确认通过**

Run: `~/p2venv/bin/python3 -m pytest tests/test_audit_units.py tests/test_health*.py tests/test_finding*.py -q`
Expected: 全部 PASS（health 现有测试不设 `GSDB_USER_ID`，输出不变）

- [ ] **Step 5: 提交**

```bash
git add common/audit.py common/finding.py skills/gaussdb-health/scripts/report.py tests/test_audit_units.py
git commit -m "feat(audit): findings 信封与 health 报告带执行人(GSDB_USER_ID),容器里审计落到人

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NFTD813rWESLz4NzmGi4NZ"
```

---

### Task 4: gaussdb-kb 拆成查询与导入两个 skill（仅本仓库）

这是最大的一项，分 4a–4e 五个子步骤，每步可独立验证；4a–4d 各一个提交。gh_skill 保持一个 gaussdb-kb 不拆。

#### 4a. 规则文件辅助函数搬进 `common/kb/rulesfile.py`

**Files:**
- Create: `common/kb/rulesfile.py`
- Modify: `skills/gaussdb-kb/scripts/kb.py:62-93, 157-166, 436-537`（改为从 `common.kb.rulesfile` 导入并保留同名引用）
- Test: `tests/test_kb_rulesfile_units.py`（新）

**Interfaces:**
- Produces（签名与现 `kb.py` 完全一致，只是换了家）：
  `read_text_file(path) -> str`、`split_frontmatter(text) -> tuple[dict|None, str|None]`、`load_rule_file(path) -> tuple[list, str|None]`、`rule_status(entry) -> str`、`iter_files(kb, sub, suffixes) -> list[Path]`、`first_heading(path) -> str`、`iter_active_rules(kb)`、常量 `STATUS_ACTIVE`、`STATUS_DEPRECATED`、`STATUSES`、`RULE_DIRS`、`SEARCHABLE`（即现 `_SEARCHABLE`）、`SEARCH_HIT_CAP`、`RULE_ID_RE`、`SEVERITIES`、`CHECK_KINDS`、`RULE_REQUIRED_FIELDS`、`KB_SUBDIRS`。

- [ ] **Step 1: 写失败的测试**

`tests/test_kb_rulesfile_units.py`：

```python
"""common/kb/rulesfile —— 规则文件读取辅助从 kb.py 搬到 common,查询 skill 与导入 skill 共用。"""
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from common.kb import rulesfile as rf  # noqa: E402


def test_rule_without_status_is_active():
    assert rf.rule_status({"id": "GS-SQ-001"}) == rf.STATUS_ACTIVE


def test_iter_active_rules_drops_deprecated(tmp_path):
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "a.yaml").write_text(
        "- id: GS-SQ-001\n  severity: warn\n  check: advisory\n  rule: x\n"
        "- id: GS-SQ-002\n  severity: warn\n  check: advisory\n  rule: y\n  status: deprecated\n",
        encoding="utf-8")
    [(path, err, active)] = list(rf.iter_active_rules(tmp_path))
    assert err is None and [e["id"] for e in active] == ["GS-SQ-001"]


def test_split_frontmatter_reports_unterminated_block():
    meta, err = rf.split_frontmatter("---\nid: x\nno end")
    assert meta is None and "---" in err


def test_kb_py_still_exposes_the_helpers_by_the_old_names():
    """现有测试与 kb_cases 通过 kb.<name> 取这些函数;搬家不能改名。"""
    import importlib.util
    scripts = _ROOT / "skills" / "gaussdb-kb" / "scripts"
    sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location("kb_rf_probe", scripts / "kb.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in ("split_frontmatter", "load_rule_file", "rule_status", "iter_files",
                 "first_heading", "iter_active_rules", "read_text_file", "STATUS_ACTIVE"):
        assert getattr(mod, name) is getattr(rf, name), name
```

- [ ] **Step 2: 跑测试确认失败**

Run: `~/p2venv/bin/python3 -m pytest tests/test_kb_rulesfile_units.py -q`
Expected: FAIL，`ImportError: cannot import name 'rulesfile'`

- [ ] **Step 3: 搬代码**

新建 `common/kb/rulesfile.py`：把 `kb.py` 里下列内容**剪切**过来（保持函数体逐字不变）：常量 `KB_SUBDIRS`、`RULE_ID_RE`、`SEVERITIES`、`CHECK_KINDS`、`RULE_REQUIRED_FIELDS`、`STATUS_ACTIVE`、`STATUS_DEPRECATED`、`STATUSES`、`RULE_DIRS`、`SEARCH_HIT_CAP`；函数 `read_text_file`、`split_frontmatter`、`load_rule_file`、`rule_status`、`iter_files`、`first_heading`、`iter_active_rules`；`_SEARCHABLE` 改名 `SEARCHABLE`。文件头：

```python
"""规则文件(rules/*.yaml、guides/*.md、errata/*.md)的读取辅助。

从 gaussdb-kb/scripts/kb.py 搬出:查询 skill 与导入 skill 都要读这些文件,而两个 skill 的
脚本目录在镜像里是物理分开的,只能在 common/ 里共用。函数体与原来逐字相同。
"""
from __future__ import annotations

import pathlib
import re

import yaml
```

`kb.py` 里原位置改为：

```python
from common.kb.rulesfile import (  # noqa: F401  —— 旧名字继续可用,kb_cases 与测试按 kb.<name> 取
    CHECK_KINDS, KB_SUBDIRS, RULE_DIRS, RULE_ID_RE, RULE_REQUIRED_FIELDS, SEARCH_HIT_CAP, SEARCHABLE,
    SEVERITIES, STATUS_ACTIVE, STATUS_DEPRECATED, STATUSES, first_heading, iter_active_rules, iter_files,
    load_rule_file, read_text_file, rule_status, split_frontmatter)
_SEARCHABLE = SEARCHABLE
```

（`kb.py` 的 `sys.path` 处理已把仓库根加进去，`common.kb` 可 import。`read_text_file` 若依赖 `KbError`，把 `KbError` 一并搬到 `rulesfile.py` 并在 `kb.py` 里 `from common.kb.rulesfile import KbError`。）

- [ ] **Step 4: 跑测试确认通过**

Run: `~/p2venv/bin/python3 -m pytest tests/test_kb_rulesfile_units.py tests/test_kb_units.py tests/test_kb_cases_units.py tests/test_kb_store_cmds_units.py -q`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add common/kb/rulesfile.py skills/gaussdb-kb/scripts/kb.py tests/test_kb_rulesfile_units.py
git commit -m "refactor(kb): 规则文件读取辅助搬进 common/kb/rulesfile.py,为查询/导入拆分做准备

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NFTD813rWESLz4NzmGi4NZ"
```

#### 4b. 目录搬家：现 gaussdb-kb → gaussdb-kb-import

**Files:**
- Move: `skills/gaussdb-kb/` → `skills/gaussdb-kb-import/`（`git mv`，整目录）
- Modify: 17 个 `tests/test_kb_*.py` 里的 `"gaussdb-kb"` → `"gaussdb-kb-import"`；`tests/test_red_lines_units.py:31` 的 `17` → `18`；`tools/inject_red_lines.py:8` 注释 `17` → `18`
- Modify: `skills/gaussdb-kb-import/SKILL.md` frontmatter `name: gaussdb-kb-import`、`version: 3.0.0`

**Interfaces:**
- Produces: 导入 skill 目录 `skills/gaussdb-kb-import/scripts/{kb.py,kb_store.py,kb_cases.py,kb_cite.py}`，命令集与拆分前完全一致（本步不删任何命令，先保证搬家无损）。

- [ ] **Step 1: 搬目录并改测试路径**

```bash
git mv skills/gaussdb-kb skills/gaussdb-kb-import
sed -i '' 's#"gaussdb-kb"#"gaussdb-kb-import"#g' tests/test_kb_*.py
sed -i '' 's#assert len(_SKILLS) == 17#assert len(_SKILLS) == 17  # 4c 拆出查询 skill 后改 18#' tests/test_red_lines_units.py
```

`SKILL.md` frontmatter 第 2、3 行改成 `name: gaussdb-kb-import`、`version: 3.0.0`。

- [ ] **Step 2: 跑全部 kb 测试与结构闸门**

Run: `~/p2venv/bin/python3 -m pytest tests/test_kb_*.py tests/test_skill_md_structure_units.py tests/test_red_lines_units.py -q`
Expected: 全部 PASS（红线数仍 17，因为查询 skill 还没建）

- [ ] **Step 3: 提交**

```bash
git add -A skills tests tools
git commit -m "refactor(kb): gaussdb-kb 目录整体改名 gaussdb-kb-import(命令不变),查询 skill 下一步新建

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NFTD813rWESLz4NzmGi4NZ"
```

#### 4c. 新建查询 skill gaussdb-kb

**Files:**
- Create: `skills/gaussdb-kb/SKILL.md`、`skills/gaussdb-kb/scripts/kb.py`
- Move: `skills/gaussdb-kb-import/scripts/kb_cite.py` → `skills/gaussdb-kb/scripts/kb_cite.py`（`git mv`）
- Modify: `skills/gaussdb-kb-import/scripts/kb.py`（`build_parser` 不再注册 `search`、`cite-check`；`kb_store.add_subcommands` 不再注册 `query`、`health`）
- Modify: `skills/gaussdb-kb-import/scripts/kb_store.py:306-340`（拆成 `add_admin_subcommands` 与 `add_query_subcommands`）
- Modify: `tests/test_red_lines_units.py:31`（18）、`tests/test_kb_cite_units.py`（路径回到 `gaussdb-kb`）
- Test: `tests/test_kb_query_skill_units.py`（新）

**Interfaces:**
- Consumes: `common.kb.query.from_text / from_findings`、`common.kb.render.render_section / status_line`、`common.kb.config.resolve_kb_dir`、`common.kb.rulesfile.*`、`kb_cite.add_subcommands(sub)`。
- Produces: `skills/gaussdb-kb/scripts/kb.py` 的 `build_parser()` 注册 `query`、`health`、`search`、`cite-check`；`kb_store.add_query_subcommands(sub)`（query、health）与 `kb_store.add_admin_subcommands(sub)`（setup、feedback、eval）。

- [ ] **Step 1: 写失败的测试**

`tests/test_kb_query_skill_units.py`：

```python
"""gaussdb-kb(查询 skill)—— runtime 镜像里只有它;导入命令物理不在。"""
import importlib.util
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
_Q = _ROOT / "skills" / "gaussdb-kb" / "scripts"
_I = _ROOT / "skills" / "gaussdb-kb-import" / "scripts"


def _load(scripts: pathlib.Path, name: str):
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location(f"{scripts.parent.name}_{name}", scripts / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _commands(mod) -> set:
    parser = mod.build_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    return set(sub.choices)


def test_query_skill_has_only_read_commands():
    assert _commands(_load(_Q, "kb")) >= {"query", "health", "search", "cite-check"}
    assert not _commands(_load(_Q, "kb")) & {"ingest", "apply", "index", "propose", "review", "setup", "eval", "feedback"} \
        or True  # 桩命令在 Task 6 加进来,这里只保证四个读命令存在


def test_import_skill_has_no_read_only_duplicates():
    cmds = _commands(_load(_I, "kb"))
    assert {"ingest", "index", "validate", "propose", "review", "apply", "setup", "feedback", "eval", "contract"} <= cmds
    assert not cmds & {"query", "health", "search", "cite-check"}


def test_query_skill_scripts_contain_no_import_code():
    names = {p.name for p in _Q.glob("*.py")}
    assert names == {"kb.py", "kb_cite.py"}


def test_query_search_runs_against_a_kb(tmp_path, capsys):
    kb = tmp_path / "kb"
    (kb / "rules").mkdir(parents=True)
    (kb / "rules" / "sq.yaml").write_text(
        "- id: GS-SQ-001\n  severity: warn\n  check: advisory\n  rule: 禁止 select star\n", encoding="utf-8")
    mod = _load(_Q, "kb")
    assert mod.main(["search", "select star", "--kb", str(kb)]) == 0
    assert "GS-SQ-001" in capsys.readouterr().out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `~/p2venv/bin/python3 -m pytest tests/test_kb_query_skill_units.py -q`
Expected: FAIL，`FileNotFoundError: .../skills/gaussdb-kb/scripts/kb.py`

- [ ] **Step 3: 拆 kb_store 的注册函数**

`skills/gaussdb-kb-import/scripts/kb_store.py` 把 `add_subcommands` 拆成两个（函数体照搬对应段落）：

```python
def add_query_subcommands(sub: "argparse._SubParsersAction") -> None:
    """query / health:查询 skill 用;导入 skill 不再注册它们。"""
    p = sub.add_parser("query", help="检索:--q 自然语言,或 --from-findings findings.json")
    p.add_argument("--kb")
    p.add_argument("--q", help="问题文本")
    p.add_argument("--from-findings", help="skill 输出的 findings json 文件")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("health", help="文本大盘:接入状态、条款/案例/边数、覆盖率、待处理、缺口清单")
    p.add_argument("--kb")
    p.set_defaults(func=cmd_health)


def add_admin_subcommands(sub: "argparse._SubParsersAction") -> None:
    """setup / feedback / eval:只在导入 skill 里。"""
    ...  # 原 add_subcommands 里 setup、feedback、eval 三段,逐字搬过来


def add_subcommands(sub: "argparse._SubParsersAction") -> None:   # 兼容旧调用方与测试
    add_admin_subcommands(sub)
    add_query_subcommands(sub)
```

导入 skill 的 `kb.py build_parser()`：`kb_store.add_subcommands(sub)` 改为 `kb_store.add_admin_subcommands(sub)`；删掉 `kb_cite.add_subcommands(sub)` 与 `import kb_cite`；删掉 `p_search` 那一段和 `cmd_search`、`_grep_file`（它们搬到查询 skill）。

- [ ] **Step 4: 写查询 skill 的 kb.py**

`git mv skills/gaussdb-kb-import/scripts/kb_cite.py skills/gaussdb-kb/scripts/kb_cite.py`，然后新建 `skills/gaussdb-kb/scripts/kb.py`：

```python
#!/usr/bin/env python3
"""gaussdb-kb(查询):query / health / search / cite-check。

导入、索引、审批在 gaussdb-kb-import;runtime 镜像里没有那些代码。
本文件只是命令入口,检索逻辑在 common/kb/。
"""
from __future__ import annotations

import argparse
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))           # 兄弟模块 kb_cite
for _anc in _HERE.parents:                       # common/(仓库根,或装好后的 skills/ 下)
    if (_anc / "common" / "kb" / "__init__.py").exists():
        sys.path.insert(0, str(_anc))
        break

from common.kb import config as kbconfig  # noqa: E402
from common.kb import query as kbquery, render  # noqa: E402
from common.kb.rulesfile import (  # noqa: E402
    SEARCH_HIT_CAP, SEARCHABLE, KbError, iter_files, read_text_file)


def _grep_file(path: pathlib.Path, kb: pathlib.Path, needle: str, prefix: str = "") -> list[str]:
    ...  # 从导入 skill 的 kb.py 逐字搬过来


def cmd_search(args: argparse.Namespace) -> int:
    ...  # 从导入 skill 的 kb.py 逐字搬过来(resolve_kb_dir 用 kbconfig.resolve_kb_dir)


def cmd_query(args: argparse.Namespace) -> int:
    import json
    kb = kbconfig.resolve_kb_dir(args.kb)
    if args.from_findings:
        from common.finding import findings_from_json
        findings = findings_from_json(pathlib.Path(args.from_findings).read_text(encoding="utf-8"))
        result = kbquery.from_findings(findings, kb_dir=kb)
    else:
        result = kbquery.from_text(args.q, kb_dir=kb)
    if args.json:
        print(json.dumps(kbquery.result_to_dict(result), ensure_ascii=False, indent=2))
    else:
        print(render.render_section(result), end="")
    return 0 if result.status.attached else 2


def cmd_health(args: argparse.Namespace) -> int:
    ...  # 从 kb_store.cmd_health 逐字搬过来(indexer.read_state、inbox_dir、_pending、_misses_top 一并搬)


def build_parser() -> argparse.ArgumentParser:
    import kb_cite
    parser = argparse.ArgumentParser(prog="kb.py", description="GaussDB 客户知识库(查询):检索 · 大盘 · 引用核对")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("query", help="检索:--q 自然语言,或 --from-findings findings.json")
    p.add_argument("--kb")
    p.add_argument("--q", help="问题文本")
    p.add_argument("--from-findings", help="skill 输出的 findings json 文件")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("health", help="文本大盘:接入状态、条款/案例/边数、覆盖率、待处理、缺口清单")
    p.add_argument("--kb")
    p.set_defaults(func=cmd_health)

    p = sub.add_parser("search", help="检索知识库(errata 优先;不含已废止条款)")
    p.add_argument("keyword")
    p.add_argument("--kb")
    p.add_argument("--include-archived", action="store_true",
                   help="连 archive/ 里的已废止条款一并列出(仅供人工追溯,不得用于判定)")
    p.set_defaults(func=cmd_search)

    kb_cite.add_subcommands(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KbError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

`cmd_health` 搬走后，导入 skill 的 `kb_store.py` 里删掉 `cmd_query`、`cmd_health`、`_load_findings`、`_misses_top`、`_pending` 与 `add_query_subcommands`（本步结束时两边不重复）。`tests/test_kb_store_cmds_units.py` 里针对 query/health 的用例改为从查询 skill 的 `kb.py` 取（`_load` 指向 `_Q`）。

- [ ] **Step 5: 写查询版 SKILL.md**

`skills/gaussdb-kb/SKILL.md`：frontmatter `name: gaussdb-kb`、`version: 3.0.0`、`description`：「客户知识库查询:用户问"以前有没有类似情况 / 贵行规范怎么说 / 核对一下引用"时用;各诊断 skill 的『客户知识库参照』小节由脚本自动生成,不用你查。导入、建库、更新规范库不在本 skill,见 gaussdb-kb-import。」，`allowed-tools: ["exec", "read"]`，其余 metadata 同导入版。正文保留原 §0 预检（去掉导入相关句）、§3 查询、§5 里 `health` 与 `cite-check` 两条、退出码语义、能力边界（去掉 `.doc/.pdf` 那条）、红线块占位（`<!-- RED-LINES:BEGIN -->` … `<!-- RED-LINES:END -->` 空块，由工具注入）。加一段：

```markdown
## 本 skill 不做的事

导入规范 / 导入工单 / 建知识库 / 更新规范库 / 反馈引用是否有用:这些在 `gaussdb-kb-import`,
只在知识库管理员的环境里有。用户在本环境提出这类请求时,如实告知「本环境不含知识库导入功能,
请联系知识库管理员」,不要尝试写知识库目录。
```

导入版 `skills/gaussdb-kb-import/SKILL.md`：去掉 §3 查询与 `cite-check` 条目，其余不动；`description` 开头加「知识库导入(管理员用):」。

然后：`~/p2venv/bin/python3 tools/inject_red_lines.py` 注入红线；`tests/test_red_lines_units.py:31` 改成 `assert len(_SKILLS) == 18`；`tools/inject_red_lines.py:8` 注释改 18。`rl.main_script()` 对 `gaussdb-kb` 应解析出 `kb.py`（两个 skill 主脚本同名，红线末条按 `{script}` 渲染，各自正确）。

- [ ] **Step 6: 跑测试确认通过**

Run: `~/p2venv/bin/python3 -m pytest tests/test_kb_*.py tests/test_skill_md_structure_units.py tests/test_red_lines_units.py -q`
Expected: 全部 PASS，红线 18 个

- [ ] **Step 7: 提交**

```bash
git add -A skills tests tools
git commit -m "feat(kb): 拆成 gaussdb-kb(查询:query/health/search/cite-check)与 gaussdb-kb-import(导入),runtime 镜像不含导入代码

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NFTD813rWESLz4NzmGi4NZ"
```

#### 4d. AGENTS.md、契约注入、安装脚本、文档

**Files:**
- Modify: `AGENTS.md:69-70, 181`
- Modify: `skills/gaussdb-kb-import/SKILL.md` §4 契约注入段（改为构建期说明）
- Modify: `docs/delivery/10-知识库安装手册.md`（gitignored，用 `git add -f`）
- Test: `tests/test_skill_md_structure_units.py`（新增一条）

- [ ] **Step 1: 写失败的测试**

`tests/test_skill_md_structure_units.py` 末尾追加：

```python
def test_query_kb_skill_never_offers_import_commands():
    """runtime 镜像只有查询 skill;它的 SKILL.md 不能引导模型去跑不存在的导入命令。"""
    text = (_ROOT / "skills" / "gaussdb-kb" / "SKILL.md").read_text(encoding="utf-8")
    for cmd in ("kb.py ingest", "kb.py apply", "kb.py propose", "kb.py review", "kb.py index", "kb.py setup"):
        assert cmd not in text, cmd
    assert "gaussdb-kb-import" in text


def test_agents_md_routes_import_and_query_to_different_skills():
    text = _AGENTS.read_text(encoding="utf-8")
    assert "`gaussdb-kb-import`" in text and "`gaussdb-kb`" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `~/p2venv/bin/python3 -m pytest tests/test_skill_md_structure_units.py -q -k "query_kb or routes_import"`
Expected: 第二条 FAIL（AGENTS.md 里还没有 `gaussdb-kb-import`）

- [ ] **Step 3: 改 AGENTS.md**

第 181 行那一条改成两条，带标记：

```markdown
<!-- ▼▼▼ skills-v13.0 修改（知识库拆成查询 / 导入两个 skill）▼▼▼ -->
- 用户说“知识库里有没有类似案例”“贵行规范怎么说”“核对一下引用”时，优先使用 `gaussdb-kb`
- 用户说“导入规范”“导入工单”“建知识库”“更新规范库”时，优先使用 `gaussdb-kb-import`；本环境没有它时如实告知「本环境不含知识库导入功能，请联系知识库管理员」
<!-- ▲▲▲ skills-v13.0 修改 ▲▲▲ -->
```

第 69–70 行「用户要导入知识库（gaussdb-kb）的文件…」里的 `gaussdb-kb` 改成 `gaussdb-kb-import`。

导入版 SKILL.md §4 契约注入段末尾加一句：「容器部署下 skills 目录只读，`contract --apply` 在构建镜像前于源码仓执行一次，结果随镜像发布，运行时不再执行。」

- [ ] **Step 4: 跑测试确认通过**

Run: `~/p2venv/bin/python3 -m pytest tests/test_skill_md_structure_units.py tests/test_red_lines_units.py -q`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add AGENTS.md skills/gaussdb-kb-import/SKILL.md tests/test_skill_md_structure_units.py
git add -f docs/delivery/10-知识库安装手册.md
git commit -m "docs(kb): AGENTS.md 技能匹配按查询/导入分流;契约注入改为构建期执行

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NFTD813rWESLz4NzmGi4NZ"
```

#### 4e. 端到端复核（不提交代码）

- [ ] 在 Mac 上跑 kb 三态：`bash ~/kb-verify/modes-e2e.sh`（脚本里的 skill 路径若写死 `gaussdb-kb/scripts/kb.py ingest`，改为 `gaussdb-kb-import`；query 路径保持 `gaussdb-kb`）。Expected: 与拆分前一致。
- [ ] `bash install-opencode.sh --dest /tmp/kbsplit-install` 后 `ls /tmp/kbsplit-install` 应有 18 个 skill 目录，`gaussdb-kb/scripts` 只有 `kb.py`、`kb_cite.py`。

---

### Task 5: 知识库写入互斥与原子写（两仓库）

gh_skill 那边没有拆分，接入点在 `skills/gaussdb-kb/scripts/` 下的同名文件，改法相同。

**Files:**
- Create: `common/kb/lock.py`、`common/kb/atomic.py`
- Modify: `skills/gaussdb-kb-import/scripts/kb.py`（`cmd_ingest_any`、`cmd_index_all` 取锁；`cmd_index` 三处 `write_text` 改原子写）
- Modify: `skills/gaussdb-kb-import/scripts/kb_cases.py:563`（`cmd_apply` 取锁；`cases/*.md`、`graph/*.yaml`、`canonical.yaml` 写入改原子写）
- Modify: `skills/gaussdb-kb-import/scripts/kb_store.py`（`cmd_feedback` 取锁）
- Test: `tests/test_kb_lock_units.py`（新）

**Interfaces:**
- Produces:
  `common.kb.lock.hold(kb_dir: pathlib.Path, ttl_s: int = 600) -> contextmanager`：进入时 `O_CREAT|O_EXCL` 建 `<kb>/.lock`，内容 JSON `{"owner": "<hostname>:<pid>", "ts": <epoch>}`；已存在且 `ts` 距今 < ttl 则抛 `common.kb.lock.KbLocked(ValueError)`，文案「知识库正被 <owner> 写入(<n> 秒前),请稍后再试」；过期则删掉重建；退出时删文件。
  `common.kb.atomic.write_text_atomic(path: pathlib.Path, text: str) -> None`：写 `path.with_name(path.name + ".tmp")` 再 `os.replace`。

- [ ] **Step 1: 写失败的测试**

`tests/test_kb_lock_units.py`：

```python
"""知识库写入互斥 + 原子写:导入 Pod 与读 Pod 共享 NAS 目录,写一半的清单不能被读到,两个 apply 不能互相覆盖。"""
import json
import pathlib
import sys
import time

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from common.kb import atomic, lock  # noqa: E402


def test_hold_creates_and_removes_lock_file(tmp_path):
    with lock.hold(tmp_path):
        data = json.loads((tmp_path / ".lock").read_text(encoding="utf-8"))
        assert ":" in data["owner"] and isinstance(data["ts"], (int, float))
    assert not (tmp_path / ".lock").exists()


def test_second_holder_is_refused_with_a_clear_message(tmp_path):
    with lock.hold(tmp_path):
        with pytest.raises(lock.KbLocked) as exc:
            with lock.hold(tmp_path):
                pass
    assert "正被" in str(exc.value) and "稍后" in str(exc.value)


def test_stale_lock_is_taken_over(tmp_path):
    (tmp_path / ".lock").write_text(json.dumps({"owner": "dead:1", "ts": time.time() - 3600}), encoding="utf-8")
    with lock.hold(tmp_path, ttl_s=600):
        assert json.loads((tmp_path / ".lock").read_text(encoding="utf-8"))["owner"] != "dead:1"


def test_lock_is_released_on_exception(tmp_path):
    with pytest.raises(RuntimeError):
        with lock.hold(tmp_path):
            raise RuntimeError("boom")
    assert not (tmp_path / ".lock").exists()


def test_write_text_atomic_leaves_no_partial_file(tmp_path):
    target = tmp_path / "INDEX.md"
    target.write_text("old", encoding="utf-8")
    atomic.write_text_atomic(target, "new")
    assert target.read_text(encoding="utf-8") == "new"
    assert not list(tmp_path.glob("*.tmp"))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `~/p2venv/bin/python3 -m pytest tests/test_kb_lock_units.py -q`
Expected: FAIL，`ImportError: cannot import name 'atomic'`

- [ ] **Step 3: 最小实现**

`common/kb/lock.py`：

```python
"""知识库写入互斥。

导入 Pod 可能不止一个,NAS 上的 graph/*.yaml、INDEX.md 是共享文件;两个 apply 交错写会互相覆盖。
用 O_EXCL 建 <kb>/.lock 做建议锁:能建成就是拿到;建不成读内容,过期(默认 10 分钟)视为持有者已死。
不用 fcntl:NFS 上的 fcntl 锁不可靠,而 O_EXCL 在 NFSv3 以上是原子的。
"""
from __future__ import annotations

import contextlib
import json
import os
import pathlib
import socket
import time
from typing import Iterator

LOCK_NAME = ".lock"


class KbLocked(ValueError):
    pass


def _owner() -> str:
    return "%s:%d" % (socket.gethostname(), os.getpid())


def _read(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


@contextlib.contextmanager
def hold(kb_dir: pathlib.Path, ttl_s: int = 600) -> Iterator[None]:
    path = pathlib.Path(kb_dir) / LOCK_NAME
    payload = json.dumps({"owner": _owner(), "ts": time.time()})
    for _attempt in (1, 2):
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            cur = _read(path)
            age = time.time() - float(cur.get("ts") or 0)
            if age < ttl_s and _attempt == 1:
                raise KbLocked("知识库正被 %s 写入(%d 秒前开始),请稍后再试;确认对方已退出可删除 %s"
                               % (cur.get("owner") or "未知进程", int(age), path))
            try:
                path.unlink()                     # 过期锁:持有者已死,接管
            except FileNotFoundError:
                pass
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        break
    else:                                          # pragma: no cover - 两次都没建成
        raise KbLocked("知识库锁 %s 无法建立" % path)
    try:
        yield
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
```

`common/kb/atomic.py`：

```python
"""先写临时文件再改名:读 Pod 与写 Pod 共享 NAS,任何时刻读到的都是完整文件。"""
from __future__ import annotations

import os
import pathlib


def write_text_atomic(path: pathlib.Path, text: str) -> None:
    path = pathlib.Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
```

接入点（导入 skill）：
- `kb.py cmd_ingest_any` 与 `cmd_index_all` 函数体最外层包 `with lock.hold(kb):`（`kb = resolve_kb_dir(args.kb)` 先算出来）；`cmd_index` 里三处 `(kb / "X.md").write_text(...)` 改为 `write_text_atomic(kb / "X.md", ...)`。
- `kb_cases.cmd_apply` 函数体最外层包 `with lock.hold(kb):`；`path.write_text(_case_markdown(...))`、`gpath.write_text(...)`、`aliases_path.write_text(...)` 改为 `write_text_atomic`。
- `kb_store.cmd_feedback` 追加写改为：读旧内容 + 新行后 `write_text_atomic`，外层 `with lock.hold(kb):`。
- `KbLocked` 在各脚本的 `main()` 里与 `KbError` 一样按 `[error]` 打印、退出码 2。

- [ ] **Step 4: 跑测试确认通过**

Run: `~/p2venv/bin/python3 -m pytest tests/test_kb_lock_units.py tests/test_kb_units.py tests/test_kb_cases_units.py tests/test_kb_store_cmds_units.py -q`
Expected: 全部 PASS

- [ ] **Step 5: 并发 e2e**

Mac 上：

```bash
K=$(mktemp -d); I=~/gh_skill/opencode_skill-main-v2-0729/skills/gaussdb-kb-import/scripts/kb.py
(~/p2venv/bin/python3 - <<EOF
import sys, time; sys.path.insert(0, "$HOME/gh_skill/opencode_skill-main-v2-0729")
from common.kb import lock
with lock.hold(__import__("pathlib").Path("$K")): time.sleep(5)
EOF
) & sleep 1; ~/p2venv/bin/python3 $I index --kb $K; echo "exit=$?"; wait
```

Expected: `index` 打印「知识库正被 … 写入」，exit=2；5 秒后后台进程退出，`$K/.lock` 不存在。

- [ ] **Step 6: 提交**

```bash
git add common/kb/lock.py common/kb/atomic.py skills/gaussdb-kb-import/scripts tests/test_kb_lock_units.py
git commit -m "feat(kb): 写入互斥(.lock,10 分钟过期接管)与清单/案例原子写,导入 Pod 与读 Pod 共享 NAS 目录不再读到半个文件

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NFTD813rWESLz4NzmGi4NZ"
```

- [ ] **Step 7: 同步到 gh_skill**

`common/kb/lock.py`、`common/kb/atomic.py`、`tests/test_kb_lock_units.py` 三个新文件直接复制过去；接入点手工改 gh_skill 的 `skills/gaussdb-kb/scripts/{kb.py,kb_cases.py,kb_store.py}`（同样的位置、同样的改法，只是目录名不同）。

```bash
cd ~/gh_skill/opencode_skill-main-v2-0729 && git checkout -q fix/k8s-shared
cp ~/gh_agent_k8s/agent/common/kb/{lock,atomic}.py common/kb/ && cp ~/gh_agent_k8s/agent/tests/test_kb_lock_units.py tests/
# 手工改三个脚本的接入点后:
~/p2venv/bin/python3 -m pytest tests/test_kb_lock_units.py tests/test_kb_units.py tests/test_kb_cases_units.py tests/test_kb_store_cmds_units.py -q
git add common/kb/lock.py common/kb/atomic.py skills/gaussdb-kb/scripts tests/test_kb_lock_units.py
git commit -m "feat(kb): 写入互斥(.lock,10 分钟过期接管)与清单/案例原子写

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NFTD813rWESLz4NzmGi4NZ"
```

---

### Task 6: 只读知识库的明确提示（仅本仓库）

**Files:**
- Modify: `skills/gaussdb-kb/scripts/kb.py`（`build_parser` 加导入命令桩；`cmd_health` 加只读行）
- Modify: `common/kb/render.py:19-29`（`status_line` 加「只读」）或在 `cmd_health` 里单独打印
- Test: `tests/test_kb_query_skill_units.py`

**Interfaces:**
- Produces: 查询 skill 的 `kb.py` 对 `ingest / index / validate / propose / review / apply / setup / feedback / eval / contract` 十个子命令名给出桩：打印「本环境不含知识库导入功能(gaussdb-kb-import),请联系知识库管理员在导入环境操作。」到 stderr，退出码 2；`kb.py health` 在知识库目录不可写时多打印一行 `知识库只读  : 是(本环境不能导入;由知识库管理员在导入环境维护)`。

- [ ] **Step 1: 写失败的测试**

`tests/test_kb_query_skill_units.py` 追加：

```python
IMPORT_CMDS = ("ingest", "index", "validate", "propose", "review", "apply", "setup", "feedback", "eval", "contract")


@pytest.mark.parametrize("cmd", IMPORT_CMDS)
def test_import_commands_are_stubbed_with_guidance(cmd, capsys):
    mod = _load(_Q, "kb")
    rc = mod.main([cmd])
    err = capsys.readouterr().err
    assert rc == 2 and "gaussdb-kb-import" in err and "知识库管理员" in err


def test_health_says_read_only_when_kb_dir_is_not_writable(tmp_path, capsys):
    import os
    if os.geteuid() == 0:
        pytest.skip("root 不受目录权限约束")
    kb = tmp_path / "kb"
    (kb / "rules").mkdir(parents=True)
    (kb / "rules" / "sq.yaml").write_text(
        "- id: GS-SQ-001\n  severity: warn\n  check: advisory\n  rule: x\n", encoding="utf-8")
    kb.chmod(0o500)
    try:
        _load(_Q, "kb").main(["health", "--kb", str(kb)])
    finally:
        kb.chmod(0o700)
    assert "知识库只读" in capsys.readouterr().out
```

（把 4c 里 `test_query_skill_has_only_read_commands` 的 `or True` 那行删掉，改为断言桩命令**存在于** choices 里但运行返回 2。）

- [ ] **Step 2: 跑测试确认失败**

Run: `~/p2venv/bin/python3 -m pytest tests/test_kb_query_skill_units.py -q -k "stubbed or read_only"`
Expected: 桩用例 FAIL（argparse `invalid choice`，`SystemExit(2)` 但 stderr 无 `gaussdb-kb-import`）

- [ ] **Step 3: 最小实现**

查询 skill `kb.py` 加：

```python
IMPORT_ONLY = ("ingest", "index", "validate", "propose", "review", "apply", "setup", "feedback", "eval", "contract")
IMPORT_ONLY_MSG = ("本环境不含知识库导入功能(gaussdb-kb-import),请联系知识库管理员在导入环境操作。"
                   "本环境可用:query / health / search / cite-check。")


def cmd_import_only(args: argparse.Namespace) -> int:
    print("[error] %s" % IMPORT_ONLY_MSG, file=sys.stderr)
    return 2
```

`build_parser()` 末尾（`kb_cite.add_subcommands(sub)` 之后）：

```python
    for name in IMPORT_ONLY:                      # 模型按记忆调导入命令时,给指引而不是 argparse 的 invalid choice
        p = sub.add_parser(name, help="(本环境不可用,见 gaussdb-kb-import)")
        p.add_argument("rest", nargs=argparse.REMAINDER)
        p.set_defaults(func=cmd_import_only)
```

`cmd_health` 在打印状态行之后加：

```python
    if not os.access(kb, os.W_OK):
        print("知识库只读  : 是(本环境不能导入;由知识库管理员在导入环境维护)")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `~/p2venv/bin/python3 -m pytest tests/test_kb_query_skill_units.py tests/test_kb_*.py -q`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add skills/gaussdb-kb/scripts/kb.py tests/test_kb_query_skill_units.py
git commit -m "feat(kb): 查询 skill 对导入命令给出指引桩,health 标明知识库只读

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NFTD813rWESLz4NzmGi4NZ"
```

---

### Task 7: 全量验收、gh_skill 发布 skills-v12.10、本仓库打 agent-v0.1

**Files:**
- Modify（本仓库 `agent/`）：改过内容的 `SKILL.md` 升号：gaussdb-kb 3.0.0、gaussdb-kb-import 3.0.0、gaussdb-health 补丁号 +1
- Modify（gh_skill）：gaussdb-kb 补丁号 +1（写锁）

- [ ] **Step 1: 本仓库 agent/ 全量单测**

```bash
cd ~/gh_agent_k8s/agent && ~/p2venv/bin/python3 -m pytest tests -q -p no:cacheprovider
```
Expected: 全绿（Task 0 基线数 + 本计划新增 ≈ 30）。

- [ ] **Step 2: 本仓库 agent/ 的场景矩阵、harness、e2e**

Mac 上的验收脚本通过 `S=` 或 `.tarball` 指向被测的 skills 树；这次指向 `~/gh_agent_k8s/agent`：

```bash
cd ~/gh_agent_k8s/agent && ~/p2venv/bin/python3 tools/scenario_matrix.py          # 60×2
S=~/gh_agent_k8s/agent/skills bash ~/kf-verify/kf_session_e2e.sh                   # 19
S=~/gh_agent_k8s/agent/skills bash ~/kb-verify/modes-e2e.sh                        # kb 三态(ingest 路径改 gaussdb-kb-import)
bash ~/kf-verify/kf_harness_run.sh                                                  # 80,harness 从压缩包装出副本:先按 v12.9 的打包命令给 agent/ 打包
```
Expected: 与 v12.9 基线一致，会话 e2e 多一例。

- [ ] **Step 3: 模型级抽查**

`bash ~/kf-verify/model_session_run2.sh`（指向 agent/）；另起一个对话让模型「导入工单」，期望模型只引导「联系知识库管理员」、不尝试写目录。

- [ ] **Step 4: gh_skill 发布 skills-v12.10**

```bash
cd ~/gh_skill/opencode_skill-main-v2-0729 && git checkout -q fix/k8s-shared
~/p2venv/bin/python3 -m pytest tests -q -p no:cacheprovider          # 全绿
~/p2venv/bin/python3 tools/scenario_matrix.py && bash ~/kf-verify/kf_session_e2e.sh
git checkout main && git merge --no-ff fix/k8s-shared -m "merge: skills-v12.10 通用修复(GSDB_HOME 提示、会话不存中间件地址、知识库写锁)"
git tag -a skills-v12.10 -m "skills-v12.10: GSDB_HOME 提示、会话不存中间件地址、知识库写锁与原子写"
git push origin main skills-v12.10
```
打包命令与 v12.9 相同，产出 `gaussdb-skills-<sha>-<日期>.tar.gz` + `.sha256`。

- [ ] **Step 5: 本仓库打标签**

```bash
cd ~/gh_agent_k8s && git tag -a agent-v0.1 -m "agent-v0.1: 技能侧 Pod 适配完成(基于 gh_skill skills-v12.9,含 v12.10 的三项)"
git push origin main agent-v0.1
```
在 `docs/plans/2026-09-17-roadmap.md` 阶段 1 行标「完成 <日期>，agent-v0.1 / skills-v12.10」，提交推送。

---

## Self-Review

- **归属**：两仓库 = Task 1、2、5（各带「同步到 gh_skill」步骤）；仅本仓库 = Task 0、3、4、6。
- **Spec 覆盖**：§10 六项 → Task 1（#2）、Task 2（#4）、Task 3（#5）、Task 4（#1）、Task 5（#3）、Task 6（#6）；§7 互斥与原子写 → Task 5；§5 「runtime 镜像里没有导入代码」→ Task 4c 的 `test_query_skill_scripts_contain_no_import_code`。契约注入改构建期 → Task 4d。
- **占位符**：Task 4c 里 `...  # 逐字搬过来` 出现三处，都指向本仓库里明确的现有函数（`_grep_file`、`cmd_search`、`cmd_health`），执行者按文件行号搬即可，不是待定内容。
- **类型一致**：`lock.hold(kb_dir, ttl_s)`、`atomic.write_text_atomic(path, text)`、`audit.actor_id()`、`kb_store.add_query_subcommands / add_admin_subcommands`、`config._DEFAULT_STATE_DIR` 在各任务中命名一致。

## 执行记录（2026-09-17）

| 任务 | 本仓库提交（分支 `feat/phase1-skills`） | gh_skill 提交（分支 `fix/k8s-shared`） | 备注 |
|---|---|---|---|
| 0 导入 | `efaf82c` | — | 基线 2170 通过、45 跳过 |
| 1 GSDB_HOME 守卫 | `4a5ab0f` | `f9166ae` | |
| 2 会话不存地址 | `50388ea` | `d676a0d` | e2e S19 用 vacuum 断言，20/20 |
| 2b 网络异常带地址 | `93e6263` | `1f05fa5` | 执行中发现的 v12.9 缺陷 |
| 3 执行人 | `cee3c2a` | — | |
| 4a rulesfile | `0df77e3` | — | |
| 4b 目录改名 | `a2551a8` | — | 红线注入器点名 `gaussdb-kb-import` 主脚本仍是 kb.py |
| 4c 查询 skill | `ec012ca` | — | 与 4c 计划的差别：`kb_store.add_query_subcommands` 没有保留，query/health 直接搬进查询 skill 的 kb.py；`contract` 留在导入侧 |
| 4d AGENTS.md 等 | `7ea6e6e` | — | 安装冒烟 18 目录；kb 三态 e2e（file + sample）与 gh_skill 逐字一致 |
| 5 写锁 + 原子写 | `65edeb7` | `aebdc27` | 并发 e2e：第二个 index 被拒、退出码 2、锁释放 |
| 6 导入命令桩 | `15bd431` | — | 桩在 `main()` 解析参数之前拦截；`kb_cite` 改为模块级导入 |
| 7 验收 | 脚本层全绿；模型层被 Kimi 月度配额挡住 | | 见下表 |

**Task 7 结果（2026-09-17）**

| 层 | agent/（gh_agent_k8s `feat/phase1-skills` @ `15bd431`） | gh_skill（`fix/k8s-shared` @ `d2723df`） |
|---|---|---|
| 单测（py3.12，venv） | 2218 通过、45 跳过 | 2188 通过、45 跳过 |
| 场景矩阵 60×2（`GSDB_HOME=~/kf-verify/gdaa-gsql`） | og 60/60、og-grmp 60/60 | og 60/60、og-grmp 60/60 |
| 会话 e2e（含新 S19） | 20/20 | 20/20 |
| kb 三态 e2e（file + sample） | 与 gh_skill 逐字一致，eval 12/12 | eval 12/12 |
| harness 80（交付包解出副本，直连 + 8779 mock） | 80/80（首轮 77/80：3 个 E2-mock 是 env_up 拉起的 mock 进程连备机卡住，重启 mock 后同批 80/80，代码无关） | 未跑（改动与 agent 相同，矩阵与 e2e 已覆盖） |
| 复跑 10 | 10/10 | — |
| 模型级 G1–G4（DeepSeek `deepseek-flash`，Kimi 当月配额已满） | 全部符合预期：G1 不登录只引导、不透露预置会话 A/B；G2 带句柄完成诊断；G3 要求导入 → 答「知识库只读，找管理员在导入环境做」，知识库目录 125 个文件前后一致；G4 走 `kb.py query/search` 命中 `GS-IDX-001`、`GS-OPS-003`、案例 `S3-20250210-…`，引用带 ID 与出处。首轮 G4 空答是隔离配置缺 `permission.external_directory: allow`（知识库目录在项目目录之外），补上后正常；runtime Pod 的配置要带这条 | — |
| py3.9 单测（`/usr/bin/python3`，Xcode 许可接受后） | 2218 通过、45 跳过 | 2188 通过、45 跳过 |

结论：全部通过。合入 main，本仓库打 `agent-v0.1`，gh_skill 打 `skills-v12.10`。

执行中改过的验收脚本（Mac `~/kf-verify`）：`kf_session_e2e.sh` 加 S19（`PY` 可覆盖）；`~/kb-verify/modes-e2e.sh` 加 `ROOT`/`PY` 覆盖并按目录自动选导入 / 查询脚本；`kf_env_up.sh`、`kf_harness.py`、`kf_rerun.py`、`kf_patch_probe.py` 各有 `_venv` 副本（系统 Python 被 Xcode 许可挡住期间用）。
