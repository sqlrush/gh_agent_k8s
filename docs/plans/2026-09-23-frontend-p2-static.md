# 前端镜像 · 第二份计划：按实例分目录 + 纯静态大盘站 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 报告按数据库实例分目录存档；一个纯静态的 `gaussdb-agent-frontend` 镜像（nginx）端四个大盘页面，浏览器自己经网关取本人报告、自己画图、深挖在新标签页打开 opencode 会话。做完后登录 → 打开 `/dash/health` 看到自己最近一次健康检查。

**Architecture:** 三方分工不变——runtime 生成 JSON（4097 端出），frontend 只端页面程序，浏览器组装。存档目录从 `reports/<skill>/` 改为 `reports/<skill>/<实例键>/`，加 `targets.json` 供大盘切实例；知识库不分实例。前端是原生 ES 模块 + 一份 CSS，零构建；设计稿 `docs/prototypes/*-r1.html` 直接演化。跨语言契约由 `frontend/lib/contract.json` + pytest 守卫钉住。

**Tech Stack:** Python 3.9+/3.12（存档、4097、守卫）；原生 JS（ES2020 模块）+ CSS；`nginxinc/nginx-unprivileged:1.27-alpine`（多架构已确认）；headless Chrome（Mac 上 `/Applications/Google Chrome.app`）做页面验收；K8s 清单。

**Spec:** `docs/specs/2026-09-22-frontend-dashboards-design.md` §3 D2/D5/D7、§4.1–4.3、§4.4（本计划修正为按实例分目录）、§4.7–4.10、§6、§7、§8 阶段 3–5

## Global Constraints

- 实例键 = `conn` 规整：`[^A-Za-z0-9._-]` → `_`，空 → `_default`。显示名用 `conn` 原文。知识库（`kb/`）**不分实例**。
- 存档失败只 warn 不抛；未设 `GSDB_REPORTS_DIR` 不存档——第一份计划的约束不变。
- 前端：**零构建、零 npm 依赖**，`frontend/` 目录里的文件就是发布内容；不引第三方图表库；页面里不出现任何平台配置取值（红线第 7 条）。
- 前端 Pod：nginx 非 root（uid 101，监听 8080）、只读根文件系统、无状态、不挂 NAS、**NetworkPolicy 出站一条都不放**。
- 深挖：点击瞬间同步 `window.open('about:blank')`，会话建好再设地址；失败关掉那个标签页并在页面提示。前端代码里**不得**有任何创建/查询 Pod 的逻辑。
- 新增可配项一律三处齐（代码 / ConfigMap / 映射），守卫会拦。本计划不新增网关配置项。
- 技能侧改动（Task 0）是两仓库改动，Task 12 同步到 gh_skill。
- 提交信息中文 conventional commit，结尾两行：
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01NFTD813rWESLz4NzmGi4NZ`
- 测试与容器命令在 Mac 上跑：`ssh sqlrush@192.168.128.1 'export PATH=$HOME/.orbstack/bin:/usr/local/bin:$PATH; cd ~/gh_agent_k8s && …'`；kubectl 带 `--context orbstack`；本地集群用 `kubectl apply -k k8s/local`（不是 base）。ssh 里不要用 `$K` 这类变量直接当命令（zsh 不分词），要么 `bash -s`，要么写全。
- 直接在 `main` 上做，每个任务一次提交。一次性工号 `u9400`，**不碰 u9001–u9003 / u9100 / u9300**。

---

## 文件结构

| 文件 | 职责 | 任务 |
|---|---|---|
| `agent/common/reports.py` | `instance_key()`；`archive(..., instance=)` 分目录 + `targets.json` | 0 |
| `agent/tests/test_reports_units.py` | 分目录与 targets 的单测 | 0 |
| 四个技能钩子（health/topsql/wdr/sqltune） | 传 `instance=payload["conn"]`；topsql/sqltune 补 `conn` | 0 |
| `agent/tests/test_reports_hooks_units.py` | 钩子按实例落目录 | 0 |
| `docker/podctl.py` | 4097 加 `/whoami.json` | 1 |
| `docker/tests/test_podctl.py` | whoami 测试 | 1 |
| `frontend/index.html`、`styles.css`、`lib/{shell,data,dig,charts,fmt}.js` | 外壳、取数、深挖、图表、格式化 | 2 |
| `frontend/pages/{health,topsql,wdr,kb}.js` | 四个页面 | 3–6 |
| `frontend/lib/contract.json`、`agent/tests/test_frontend_contract_units.py` | 跨语言契约守卫 | 7 |
| `scripts/dash-preview.sh`、`scripts/k8s/e2e-dash.sh` | 本地预览（样例 JSON）与 headless Chrome 页面验收 | 8 |
| `docker/Dockerfile.frontend`、`docker/nginx-dash.conf`、`docker/tests/test_nginx_conf_units.py` | 镜像与配置守卫 | 9 |
| `k8s/base/frontend.yaml`、`kustomization.yaml`、`networkpolicy.yaml`、`k8s/local/networkpolicy-local.yaml`、`gateway/tests/test_manifest_guards_units.py` | 清单与守卫 | 10 |
| `scripts/build-images.sh`、`scripts/package-images.sh`、`scripts/k8s/tests/test_package_completeness_units.py` | 第四个镜像进构建与打包 | 11 |
| gh_skill 对应文件、`agent/UPSTREAM` | 同步 Task 0 | 12 |
| `scripts/k8s/e2e-reports.sh`（改路径）、本地集群端到端 | 验收 | 13 |
| 六份文档 | 第四个镜像、实例切换、`/dash` | 14 |

---

### Task 0: 报告按实例分目录

**Files:**
- Modify: `agent/common/reports.py`
- Modify: `agent/skills/gaussdb-health/scripts/health.py`（`reports.archive("health", d)` 一处）
- Modify: `agent/skills/gaussdb-topsql/scripts/topsql.py`（payload 加 `conn`；archive 加 instance）
- Modify: `agent/skills/gaussdb-wdr/scripts/wdr.py`（archive 加 instance）
- Modify: `agent/skills/gaussdb-sqltune/scripts/sqltune.py`（`_to_jsonable` 加 `conn`；`archive_tune` 加 instance）
- Test: `agent/tests/test_reports_units.py`、`agent/tests/test_reports_hooks_units.py`

**Interfaces:**
- Produces: `reports.instance_key(conn: str) -> str`；`reports.archive(skill, payload, *, name=None, keep=50, now=None, instance: Optional[str] = None)`：`instance` 非 `None` 时目录为 `<dir>/<skill>/<instance_key(instance)>/`，并维护 `<dir>/<skill>/targets.json`：`[{"key","conn","last_at","count"}]` 按 `last_at` 降序（`count` = 该实例 index 条数）；`instance is None` 时行为与第一份计划完全一样（kb 用）。
- topsql payload：`{"conn": str, "by", "limit", "rows"}`；sqltune `_to_jsonable` 多一个 `"conn"`。

- [ ] **Step 1: 写失败的测试**

`test_reports_units.py` 追加：

```python
def test_instance_key_sanitizes_conn_name():
    assert reports.instance_key("api/10-0-0-9-postgres") == "api_10-0-0-9-postgres"
    assert reports.instance_key("og5") == "og5"
    assert reports.instance_key("") == "_default"
    assert reports.instance_key("../x") == ".._x", "斜杠必须换掉,键要能当路径段"


def test_archive_with_instance_goes_into_subdir_and_updates_targets(monkeypatch, tmp_path):
    monkeypatch.setenv("GSDB_REPORTS_DIR", str(tmp_path))
    reports.archive("health", {"overall": 1, "conn": "api/10-0-0-9-postgres"}, instance="api/10-0-0-9-postgres", now="20260923T010000Z")
    reports.archive("health", {"overall": 2, "conn": "og5"}, instance="og5", now="20260923T020000Z")
    reports.archive("health", {"overall": 0, "conn": "og5"}, instance="og5", now="20260923T030000Z")
    assert (tmp_path / "health" / "api_10-0-0-9-postgres" / "latest.json").is_file()
    assert (tmp_path / "health" / "og5" / "latest.json").is_file()
    assert not (tmp_path / "health" / "latest.json").exists(), "分实例后技能根目录不该再有 latest"
    t = json.loads((tmp_path / "health" / "targets.json").read_text(encoding="utf-8"))
    assert [x["key"] for x in t] == ["og5", "api_10-0-0-9-postgres"], "按最近一次降序"
    assert t[0] == {"key": "og5", "conn": "og5", "last_at": "20260923T030000Z", "count": 2}
    og5_idx = json.loads((tmp_path / "health" / "og5" / "index.json").read_text(encoding="utf-8"))
    assert [e["overall"] for e in og5_idx] == [2, 0], "各实例各自的 index,不混"


def test_archive_without_instance_is_unchanged(monkeypatch, tmp_path):
    monkeypatch.setenv("GSDB_REPORTS_DIR", str(tmp_path))
    reports.archive("kb", {"a": 1}, name="health")
    assert (tmp_path / "kb" / "health.json").is_file() and not (tmp_path / "kb" / "targets.json").exists()
```

`test_reports_hooks_units.py`：把四个已有断言里的路径改成带实例键——health 两条测试里 `rdir / "health" / "latest.json"` → `rdir / "health" / "og" / "latest.json"`（测试里 `resolved_name` 被替换成 `"og"`）；topsql 的 `glob("*.avg.json")` 与 `latest.json` 改到 `rdir / "topsql" / "og"`，并断言 `latest["conn"] == "og"`；wdr 改到 `rdir / "wdr" / "og"`；sqltune 的 `archive_tune({"sql_id": "1a9f", "conn": "og", "x": 1})` → 文件在 `rdir / "sqltune" / "og" / "1a9f.json"`，没有 `conn` 时落 `_default/`。再加一条：

```python
def test_topsql_payload_carries_conn(rdir, monkeypatch):
    _load("gaussdb-topsql", "topsql", "render")
    import topsql  # noqa: E402
    monkeypatch.setattr(topsql.access, "for_conn", lambda *a, **k: object())
    monkeypatch.setattr(topsql, "top_sql", lambda runner, by, limit: [])
    monkeypatch.setattr(topsql.common.config, "resolved_name", lambda c: "api/10-0-0-9-postgres")
    assert topsql.main(["-c", "x", "--format", "json"]) == 0
    latest = json.loads((rdir / "topsql" / "api_10-0-0-9-postgres" / "latest.json").read_text(encoding="utf-8"))
    assert latest["conn"] == "api/10-0-0-9-postgres"
```

- [ ] **Step 2: 跑，确认失败**

Run: `python3 -m pytest agent/tests/test_reports_units.py agent/tests/test_reports_hooks_units.py -q`
Expected: `instance_key` AttributeError；`archive() got an unexpected keyword argument 'instance'`；钩子那几条 latest.json 路径不存在。

- [ ] **Step 3: 实现 `reports.py`**

```python
TARGETS = "targets.json"
_KEY_BAD = re.compile(r"[^A-Za-z0-9._-]")


def instance_key(conn: str) -> str:
    """实例键:conn 规整成能当路径段的串。容器里 conn 形如 api/10-0-0-9-postgres(已含 dataIp 与库名)。"""
    key = _KEY_BAD.sub("_", (conn or "").strip())
    return key or "_default"


def _update_targets(skill_dir: pathlib.Path, key: str, conn: str, stamp: str, count: int) -> None:
    path = skill_dir / TARGETS
    items = [t for t in _load_index(path) if t.get("key") != key]
    items.append({"key": key, "conn": conn, "last_at": stamp, "count": count})
    items.sort(key=lambda t: t.get("last_at", ""), reverse=True)
    _write_atomic(path, json.dumps(items, ensure_ascii=False, indent=2))
```

`archive()` 签名加 `instance: Optional[str] = None`；`d = base / skill` 之后：`if instance is not None: key = instance_key(instance); d = d / key`；写完 index 之后 `if instance is not None: _update_targets(base / skill, key, instance, stamp, len(idx))`。`import re` 补上。

- [ ] **Step 4: 实现四个钩子**

- health.py：`reports.archive("health", d, instance=d.get("conn", ""))`
- topsql.py：`payload = {"conn": common.config.resolved_name(args.conn), "by": ..., "limit": ..., "rows": ...}`；`reports.archive("topsql", payload, name=..., instance=payload["conn"])`
- wdr.py：`reports.archive("wdr", ev.to_dict(), instance=ev.conn)`
- sqltune.py：`_to_jsonable` 返回的 dict 开头加 `"conn": ...`——`_to_jsonable(tr)` 没有 args；改成 `main` 里 `payload = _to_jsonable(tr); payload["conn"] = common.config.resolved_name(args.conn)`；`archive_tune(payload)` 内 `reports.archive("sqltune", payload, name=sql_id, instance=payload.get("conn", ""))`。

- [ ] **Step 5: 跑，确认通过；再跑 agent/tests 全部**

Run: `python3 -m pytest agent/tests -q`
Expected: 全 passed

- [ ] **Step 6: 提交**

```bash
git add agent/common/reports.py agent/skills/gaussdb-health/scripts/health.py agent/skills/gaussdb-topsql/scripts/topsql.py agent/skills/gaussdb-wdr/scripts/wdr.py agent/skills/gaussdb-sqltune/scripts/sqltune.py agent/tests/test_reports_units.py agent/tests/test_reports_hooks_units.py
git commit -m "feat(reports): 报告按实例分目录 + targets.json——三个大盘都是按库统计的,不能把两个库的趋势串成一条"
```

---

### Task 1: 4097 加 `/whoami.json`

**Files:**
- Modify: `docker/podctl.py`（`make_reports_server` 加参数 `user_id: str = ""`；`main` 里传 `os.environ.get("GSDB_USER_ID", "")`）
- Test: `docker/tests/test_podctl.py`

**Interfaces:**
- Produces: `GET /whoami.json` → `{"user_id": "<工号>"}`，`Cache-Control: no-store`；不读文件。其它路径规则不变。

- [ ] **Step 1: 写失败的测试（追加）**

```python
def test_reports_server_whoami_comes_from_env_not_from_files(tmp_path):
    root = tmp_path / "reports"; root.mkdir()
    srv = podctl.make_reports_server(root, 0, user_id="u1234")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        st, h, body = _get(srv.server_address[1], "/whoami.json")
        assert st == 200 and json.loads(body) == {"user_id": "u1234"} and h["cache-control"] == "no-store"
        assert not (root / "whoami.json").exists(), "不是文件,是环境变量"
    finally:
        srv.shutdown(); srv.server_close()
```

- [ ] **Step 2: 跑，确认失败**（`TypeError: unexpected keyword 'user_id'`）
- [ ] **Step 3: 实现**：`do_GET` 开头 `if rel == "whoami.json": data = json.dumps({"user_id": user_id}).encode(); ...200 + no-store; return`。`main` 的 `serve-reports` 分支传 `user_id=os.environ.get("GSDB_USER_ID", "")`。
- [ ] **Step 4: 跑 docker/tests 全部，通过**
- [ ] **Step 5: 提交** `feat(pod): 4097 加 /whoami.json——大盘侧栏显示工号,来自环境变量不读文件`

---

### Task 2: 前端外壳与公共库

**Files:**
- Create: `frontend/index.html`、`frontend/styles.css`（从 `docs/prototypes/proto.css` 复制，去掉 `.note` 那段）、`frontend/lib/shell.js`、`lib/data.js`、`lib/dig.js`、`lib/charts.js`、`lib/fmt.js`

**Interfaces:**
- `data.js`：`fetchJson(path) -> {ok:true,data} | {ok:false,status,error}`；`fetchTargets(skill)`、`fetchLatest(skill,key)`、`fetchIndex(skill,key)`、`fetchSqltune(key,sqlId)`、`fetchKbHealth()`、`fetchKbQueries()`、`fetchWhoami()`。路径全部以 `/reports/` 开头，`fetch` 默认 `credentials: 'same-origin'`，`cache: 'no-store'`。
- `dig.js`：`openDig(prompt)`：`const tab = window.open('about:blank')` → `POST /session {title}` → `POST /session/{id}/prompt_async {parts:[{type:'text',text:prompt}]}` → `tab.location = '/' + b64url('/data/state/workspace') + '/session/' + id`；任一失败 `tab.close()` 并 `shell.toast('打开会话失败：…')`。`b64url` 用 `btoa` 后替换 `+/` 为 `-_` 去 `=`。
- `shell.js`：`mount(page)`：渲染侧栏（四个大盘链接 + 「新对话 ↗」「最近对话 ↗」新标签页链接到 `/` + b64url(workspace) + 当前工号 + 当前实例下拉）；`route()` 按 `location.pathname` 末段（`health|topsql|wdr|kb`，默认 health）`import('./pages/<name>.js')` 并调 `render(main, ctx)`；`ctx = {key, targets, whoami, setKey(k)}`，实例键存 `localStorage['dash.instance']`，不在 targets 里就回退到第一个；`toast(msg)`。
- `charts.js`：`ring(dims)`、`stack(parts)`、`bar(pct, level)`、`sparkline(values, color)`、`timeline(levels)`——全部返回 SVG/HTML 字符串，逻辑从设计稿 `<script>` 抽出。
- `fmt.js`：`fmtInt`、`fmtSec`、`fmtMs`、`mmdd(isoOrStamp)`、`LV = ['ok','notice','warn','crit']`、`LVN = ['正常','关注','告警','严重']`。
- `index.html`：`<script type="module">import {mount} from './lib/shell.js'; mount();</script>`；所有资源用**相对路径**（页面在 `/dash/` 下端出）。

- [ ] **Step 1: 写文件**（外壳先只放一个「页面加载中」占位，页面任务再填）
- [ ] **Step 2: 本地预览能开**：`scripts/dash-preview.sh`（Task 8 才写，这里先手工）——在 Mac 上 `cd frontend && python3 -m http.server 8090`，Chrome `--headless --dump-dom http://127.0.0.1:8090/index.html` 里有侧栏四个链接、console 无 `Uncaught`。
- [ ] **Step 3: 提交** `feat(frontend): 静态大盘站外壳——侧栏/路由/取数/深挖/图表,零构建`

---

### Task 3: 健康检查页 `frontend/pages/health.js`

**Files:** Create `frontend/pages/health.js`

**Interfaces:** `export async function render(root, ctx)`。取 `fetchLatest('health', ctx.key)` + `fetchIndex('health', ctx.key)`；无报告（404）→ 渲染引导卡「还没有健康检查报告 · 在会话里跑一次 →」（`openDig('请对当前库做一次健康检查')`）。区块 id：`#hd-head #hd-ring #hd-band #hd-sub #hd-findings #hd-dims #hd-history #hd-trend`。映射按 spec §4.9：页头 ← `conn/overall/user_id(whoami)/index[-1].at`；环与带 ← `dims[].{dimension,available}` + 该维度 findings 取最坏；发现榜 ← `findings[]`；维度卡 ← `dims[].{headline,headers,rows,note}`；子技能 ← `sub_skills[]`；历史 ← `index[]` 的 `overall` 与 `counts`；「比上次」← 最近两份 `index` 的 counts 差。维度标题表：`overview 总览 / slowsql 慢 SQL / xact 事务 / conn 连接 / logs 日志 / repl 主备复制 / schema 对象与索引 / concurrency 事务并发 / locks 锁等待 / waits 等待事件 / lwlock LWLock / bloat 膨胀`，未知维度原样显示。

- [ ] 从 `docs/prototypes/health-r1.html` 的 `<script>` 移植，`DATA` 换成取回的数据；样例 JSON 预览（Task 8 的 `dash-preview.sh` 提供 `/reports/` 假目录）下 8 个区块非空。
- [ ] 提交 `feat(frontend): 健康检查大盘`

### Task 4: Top SQL 页 `frontend/pages/topsql.js`
- 页签 = 五个 by；每个 by 各取 `fetchLatest('topsql', key)`?——`latest.json` 只是最后一次跑的 by。改为：读 `index.json`，按文件名后缀 `.<by>.json` 找每个 by 最近一份，再 `fetchJson('/reports/topsql/<key>/<file>')`；没有的 by 页签灰显。占比 = `rows[i].total_sec / Σrows.total_sec`（**没有全量合计**，报告只有 Top N，页面上写「上榜合计」不写「占总耗时」——比设计稿诚实一步）。逐条卡：`fetchSqltune(key, sql_id)`，404 → 「尚未调优 · 调优 →」（`openDig('请调优 sql_id 为 <id> 的语句')`）。区块 id：`#ts-tabs #ts-kpis #ts-stack #ts-rows #ts-cards`。
- [ ] 移植、预览、提交 `feat(frontend): Top SQL 大盘`

### Task 5: WDR 页 `frontend/pages/wdr.js`
- `fetchLatest('wdr', key)` + `fetchIndex('wdr', key)`；上一份 = index 倒数第二条对应文件（有则 ▲▼，无则「无对比窗口」）。KPI 从 `dims[loadprofile|dbstat].rows` 按第一列名取值（页面里写一张「行名 → KPI」的映射表，找不到就显示「未采集」）；DB Time 构成/等待 ← `dims[waits].rows`；Top SQL ← `dims[topsql].rows`；三小卡 ← `dims[checkpoint|cache|fileio]`；`wdr_enabled=false` → 整页一张「怎么开 WDR」卡；`native.generated && native.saved_path` → 「下载 HTML →」链接到 `/reports/wdr/<key>/<basename>`。区块 id：`#wd-head #wd-kpis #wd-dbt #wd-verdict #wd-tsql #wd-waits #wd-small #wd-history`。
- [ ] 移植、预览、提交 `feat(frontend): WDR 分析大盘`

### Task 6: 知识库页 `frontend/pages/kb.js`
- `fetchKbHealth()`（`/reports/kb/health.json`）+ `fetchKbQueries()`（`/reports/kb/queries.jsonl`，按行 `JSON.parse`，取最近 7 天倒序前 20）。四顶卡 ← `status.counts.{docs.case,docs.rule}` + 图边数（`status.graph` 文本里没有数字时显示「图文件」）；三类知识 ← `status.{vector,graph,mode}`、`index_state`；健康自检 ← `pending[]`、`file_warnings[]`、`status.reason`；缺口清单 ← `misses[]`；最近检索 ← queries。`status.attached=false` → 一张「未接入」卡显示 `reason`。区块 id：`#kb-head #kb-top #kb-stores #kb-health #kb-misses #kb-queries`。
- [ ] 移植、预览、提交 `feat(frontend): 知识库大盘`

---

### Task 7: 跨语言契约守卫

**Files:**
- Create: `frontend/lib/contract.json`
- Create: `agent/tests/test_frontend_contract_units.py`

**Interfaces:** `contract.json`：`{"health": ["conn","overall","dims[].dimension","dims[].available","dims[].headline","dims[].rows","findings[].severity","findings[].dimension","findings[].code","findings[].metric","findings[].value","findings[].threshold","findings[].evidence","findings[].sql_id","sub_skills[].skill","sub_skills[].ok","sub_skills[].error"], "topsql": ["conn","by","rows[].sql_id","rows[].query","rows[].calls","rows[].total_sec","rows[].avg_ms","rows[].rows"], "wdr": ["conn","window.begin_id","window.end_id","window.begin_ts","window.end_ts","window.duration_min","window.wdr_enabled","dims[].dimension","dims[].available","dims[].headers","dims[].rows","findings[].severity","overall","native.generated","native.saved_path"], "kb_health": ["status.attached","status.reason","status.counts","status.vector","status.graph","status.mode","readonly","inbox","file_warnings","index_state","pending","misses[].code","misses[].n"], "index": ["at","file","overall","counts"], "targets": ["key","conn","last_at","count"]}`。

- [ ] **Step 1: 写测试**：用 Python 侧构造**非空**样例（health：`HealthEvidence` 带一个 `DimResult(rows=[[...]])` 与一个 `Finding`，经 `report.health_dict`；wdr：`Evidence` 带一个 `DimResult` 与 `Finding`，`native.generated=True, saved_path="x"`，`to_dict()`；topsql：`{"conn":..,"by":..,"limit":..,"rows":[StmtRow(...).__dict__]}`；kb：`kb.health_dict(tmp, KbStatus(attached=True, counts={...}), ["w"], False)`；index/targets：`reports.archive` 跑一次后读出来）。对 `contract.json` 每条路径 `a.b[].c` 解析：`[]` 表示列表的每个元素都要有下一个键。缺一条就 `AssertionError("前端引用了报告里不存在的字段: health → dims[].headline")`。
- [ ] **Step 2: 跑，通过**（第一次就该绿——这是钉子，不是新功能；故意改 contract.json 加一个假字段验证它会红，再改回）
- [ ] **Step 3: 提交** `test(frontend): 报告契约守卫——页面引用的每个字段路径都要在 Python 侧的样例报告里存在`

---

### Task 8: 本地预览与 headless Chrome 页面验收

**Files:**
- Create: `scripts/dash-preview.sh`：在临时目录拼一个假站——`frontend/` 软链到 `dash/`，`reports/` 下按契约放样例 JSON（由 `agent/tests/test_frontend_contract_units.py` 同款构造函数 `python3 -c` 生成到目录：`health/og5/{latest,index}.json`、`health/targets.json`、`topsql/og5/{20260923T000000Z.time.json,latest,index}.json`、`wdr/og5/...`、`sqltune/og5/1a9f.json`、`kb/{health.json,queries.jsonl}`、`whoami.json`）；`python3 -m http.server 8090`；打印地址。
- Create: `scripts/k8s/e2e-dash.sh`：对 `BASE`（默认预览站 `http://127.0.0.1:8090/dash/`）用 headless Chrome `--dump-dom` 打开四个页面，断言各页的区块 id 非空（`hd-ring hd-findings hd-dims / ts-tabs ts-rows / wd-kpis wd-dbt / kb-stores kb-misses`）、`--enable-logging=stderr` 里没有 `Uncaught`；任一失败非零退出。
- [ ] 写两个脚本，跑通（Mac：Chrome 在 `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`）
- [ ] 提交 `test(frontend): 样例站预览 + headless Chrome 四页验收——只有真浏览器能抓 window.top 那类错`

---

### Task 9: 前端镜像

**Files:**
- Create: `docker/Dockerfile.frontend`

```dockerfile
# gaussdb-agent-frontend:四个大盘的静态页面。只端文件,不读数据、不挂 NAS、不出网。
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY docker/nginx-dash.conf /etc/nginx/conf.d/default.conf
COPY frontend/ /usr/share/nginx/html/dash/
LABEL org.opencontainers.image.title="gaussdb-agent-frontend" \
      org.opencontainers.image.source="https://github.com/sqlrush/gh_agent_k8s" \
      org.opencontainers.image.description="大盘前端:纯静态,nginx 非 root 端出"
EXPOSE 8080
```

- Create: `docker/nginx-dash.conf`：`server { listen 8080; root /usr/share/nginx/html; autoindex off; add_header Cache-Control "no-cache"; location = /healthz { return 200 'ok'; add_header Content-Type text/plain; } location /dash/ { try_files $uri $uri/ /dash/index.html; } location / { return 404; } }`
- Create: `docker/tests/test_nginx_conf_units.py`：钉住 `listen 8080`、`autoindex off`、`no-cache`、`/healthz`、`try_files … /dash/index.html`、`location / { return 404`。
- Modify: `scripts/build-images.sh`：网关那段之后加 frontend（独立 Dockerfile，`-f docker/Dockerfile.frontend`），`echo built:` 加 frontend；`docker/tests/test_image_contents_units.py` 若按 Dockerfile 名遍历则不受影响。
- [ ] 写文件、守卫测试通过、`scripts/build-images.sh dev-dash` 构建成功、`docker run -p 18090:8080 gaussdb-agent-frontend:dev-dash` 后 `curl /healthz` 200、`/dash/` 200 且含 `lib/shell.js`、`/` 404、`/dash/../etc/passwd` 404。
- [ ] 提交 `feat(frontend): 第四个镜像——nginx 非 root 端静态大盘,< 30 MB`

---

### Task 10: 清单

**Files:**
- Create: `k8s/base/frontend.yaml`：Deployment `frontend`（labels `app: gaussdb-frontend`；1 副本；`image: gaussdb-agent-frontend:agent-v0.9.3-oc1.18.27`（与 configmap 里的用户镜像标签同一版本号规则，升级随 `USER_IMAGE_TAG` 一起改——注意：网关不管前端镜像，它是普通 Deployment，`set image` 升级）；`enableServiceLinks: false`；`securityContext: runAsNonRoot, readOnlyRootFilesystem`；`emptyDir` 挂 `/tmp` 与 `/var/cache/nginx`（unprivileged 镜像的 pid 文件在 `/tmp`）；readiness `httpGet /healthz:8080`；resources 50m/64Mi–200m/128Mi）+ Service `frontend`（80 → 8080）。
- Modify: `k8s/base/kustomization.yaml` 加 `frontend.yaml`。
- Modify: `k8s/base/networkpolicy.yaml`：新增 `frontend-policy`（入站只放行 `app: agent-gateway` 到 8080；`policyTypes: [Ingress, Egress]` 且 `egress: []`）；`gateway-policy` 出站加 `- to: [{podSelector: {matchLabels: {app: gaussdb-frontend}}}] ports: [{port: 8080}]`。
- Modify: `k8s/local/networkpolicy-local.yaml`：`gateway-policy` 同样加那条（本地覆盖层按名字整个替换——第一份计划踩过）。
- Modify: `gateway/tests/test_manifest_guards_units.py`：`_POD_MANIFESTS` 加 `frontend.yaml`（`enableServiceLinks` 守卫）；新增 `test_frontend_is_isolated_and_reachable_only_from_gateway`：frontend-policy 存在、`egress: []`、ingress 只有 gateway；base 与 local 的 gateway-policy 出站都有 `gaussdb-frontend` + 8080；frontend.yaml 无 `hostPath`、无 `persistentVolumeClaim`、`readOnlyRootFilesystem: true`。
- [ ] 写测试红 → 改清单绿 → `kubectl apply -k k8s/local --dry-run=client` 通过 → 提交 `feat(k8s): frontend Deployment/Service/策略——只读根、不出网、只有网关能到`

---

### Task 11: 构建与打包进第四个镜像

- Modify: `scripts/package-images.sh`：`for T in runtime kb-import gateway frontend`；MANIFEST 文案。
- Modify: `scripts/k8s/tests/test_package_completeness_units.py`：`packaged == {"runtime","kb-import","gateway","frontend"}`；`test_builder_and_packager_agree` 里 `built.add("frontend")`；新增 `test_frontend_base_image_is_multiarch_pinned`：Dockerfile.frontend 的 `FROM` 必须带明确标签（不是 latest）。
- [ ] 测试红 → 改 → 绿 → 提交 `feat(package): 离线包打第四个镜像 frontend`

---

### Task 12: 同步 Task 0 到 gh_skill

- 同第一份计划 Task 9 的做法：`git diff <Task0 前>..<Task0> -- agent/common/reports.py agent/skills agent/tests | git apply -p2`（sqltune/topsql/wdr/health 四个钩子 + 两份测试）；gh_skill 全套 passed；两边提交、`agent/UPSTREAM` 追加。

---

### Task 13: 端到端

- Modify: `scripts/k8s/e2e-reports.sh`：② 与 ④⑤ 的路径改为先取 `/reports/health/targets.json` 拿 `key`，再 `/reports/health/<key>/{index,latest}.json`；③ 之前先记 targets（可能为空 → `before=0`）。
- 本地集群：`scripts/build-images.sh dev-dash`（四个镜像）→ `kubectl apply -k k8s/local` → `patch gateway-config USER_IMAGE_TAG=dev-dash` → `set image deploy/gateway … deploy/frontend …` → `rollout status` 两个 → `e2e-reports.sh`（现在 ⑥ 应变成 `/dash/health` **200** 且正文含 `lib/shell.js`——脚本里那条改掉）→ `curl` 经网关 `/dash/lib/data.js` 200、`/reports/whoami.json` 回 `{"user_id":"u9400"}` → 还原 `USER_IMAGE_TAG`。
- headless Chrome 对着真集群：Chrome 加不了信任头，所以真集群只验到「经网关拿到页面与报告 JSON」；页面渲染用 Task 8 的预览站验（同一份代码）。
- [ ] 提交 `test(e2e): 报告端到端改按实例路径;/dash 经网关 200`

---

### Task 14: 文档

- `docs/参数手册.md`：无新参数；「平台注入」表 `GSDB_REPORTS_DIR` 一行补「按实例分目录」。
- `docs/功能清单-容器版.md`：加「四个大盘」一节（含实例切换、深挖新标签页、前端不碰数据）。
- `docs/仓库结构说明.md`：顶层加 `frontend/`；`docker/` 表加 `Dockerfile.frontend`、`nginx-dash.conf`；镜像数 4。
- `docs/部署手册-从零到上线.md`：镜像四个（拉取/载入/推送各加一行）；阶段 6 之后加「启动大盘前端」（`rollout status deploy/frontend`，`curl /dash/ → 200`）；升级流程加 `set image deploy/frontend`。
- `docs/快速搭建-K8s.md`、`docs/命令卡-测试环境搭建.md`：拉第四个镜像、tag/push 各加一行；验证加 `curl …/dash/` → 200。
- `docs/接入手册-SSO与K8s.md` §0.2「四个镜像」→ 五个（含向量库基础镜像）与前端一段；`docs/对接清单-各方要做什么.md` §3.2 镜像表加 frontend、§3.4 放行表加「网关 → frontend 8080」。
- README 交付文档表不变；`docs/prototypes` 保留（不进包）。
- [ ] 改、出 HTML、打包守卫绿、提交 `docs: 四个大盘与第四个镜像写进六份手册`

---

## 收尾

- [x] 全套 `python3 -m pytest -q` 全 passed；`git push`；`agent/UPSTREAM` 已记。
- [x] 计划末尾追加执行记录（提交号与偏差）。
- [x] 离线包重打（四个镜像 × 两架构 + 清单 + 文档）；GHCR 推四个镜像（多架构）——见执行记录末行。

---

## 执行记录（2026-09-23）

| 任务 | 提交 | 偏差 |
|---|---|---|
| 0 按实例分目录 | `9126c41` | 这一项是 user 中途确认「三个诊断大盘按实例统计」后加进计划的，先于其余任务做 |
| 1 4097 `/whoami.json` | `319d1be` | 无 |
| 2 外壳与公共库 | `6e08847` | 无 |
| 3–4 健康检查、Top SQL 页 | `4bf2f68` | 无 |
| 5–6 WDR、知识库页 | `709750a` | 无 |
| 7 契约守卫 | `9f23854` | 无 |
| 8 预览 + headless Chrome | `4bda7ed` | 四页在真浏览器里全部块非空、控制台零错。`e2e-dash.sh` 里 `declare -A` 在 macOS 自带 bash 3.2 不可用，改 `case` 函数 |
| 9 前端镜像 | `fe86bdf` | 21 MB，只读根文件系统下冒烟通过 |
| 10 清单 | `529bad3` | `enableServiceLinks: false` 行尾带注释让守卫正则失配，注释挪到独立行 |
| 11 构建与打包 | `17fec94` | `build-images` 的测试原断言 3 次 docker 调用，改 4 |
| 12 同步 gh_skill | gh_skill `13bcd4e`，本仓库 `a9e85b0` | 无 |
| 13 e2e | `39b2a91` | 本地集群一轮绿：u9400，实例键 `api_10-0-0-9-postgres`，overall=2 / dims=8 / findings=17，`/dash/health` 200，whoami 是本人。跑完把测试环境 `USER_IMAGE_TAG` 还原为 `agent-v0.9.3-oc1.18.27` |
| 14 文档 | `0dc857f` | 计划写六份，实际九份都动了（单机演示、参数手册、功能清单也有）。**交付版本 v1.0.4 → v1.1.0**：网关与 runtime 镜像里都有新代码（三路分发、超时修复、4097、按实例存档），不能沿用旧标签只补一个前端；`gateway.yaml` / `frontend.yaml` 的 `image` 标签对齐到 `agent-v1.1.0-oc1.18.27`，升级示例改用 v1.2.0 |
| 收尾：发布 | 标签 `agent-v1.1.0` | 全套单测 2654 passed / 57 skipped。GHCR 一次双架构推送在 frontend 一层上遇 `tls: bad record MAC`（网络），脚本退回「逐架构推 `-amd64`/`-arm64` + `imagetools create` 合并」，四个镜像核对各含两个架构；GHCR 上因此多出带架构后缀的标签，无害。离线包两架构各五个镜像，三个 sha256 均 OK。**验包时发现老漏洞**：`env-contract.md`、`k8s-deploy.md`、`delivery-容器化交付手册.md` 在仓库结构说明里写着「不随包发出」，v1.0.4 的包里却带着（版本号停在 v0.4.1/v0.7、指向 specs/）。改为排除，并加守卫让「内部件」表与打包脚本逐项一致 |

## 全面测试与 v1.1.1(2026-09-23 下午)

user 要求「全方位测一遍,我测的时候不能有任何 bug」。v1.1.0 的单测、契约守卫、样例站预览全绿,
真集群 + 真浏览器又抓到下面这些。**根因是样例数据按设计稿编,不是技能真实输出**,页面对着样例写。

| # | 问题 | 怎么发现 | 修在 |
|---|---|---|---|
| 1 | NAS 上坏库 → 本地无库 → 自检当首次启动,历史静默清空 | 离线包装回后的镜像冒烟 | `a5c4261` |
| 2 | WDR 8 个 KPI 全「未采集」(维度名、行列形状与真技能不符) | 真集群截图 | `c836ab7` |
| 3 | 采集全失败显示绿色「正常」 | 第二实例连不上时的截图 | `e93d45e` + `c836ab7` |
| 4 | 大盘原样显示中间件地址与内部接口路径(红线第 5 条) | 同上 | `e93d45e` |
| 5 | 子技能失败原因显示成「--timeout 不会生效」提示 | 同上 | `e93d45e` |
| 6 | 显式 --save-html 时大盘下载 404 | 存档核对 | `e93d45e` |
| 7 | Top SQL 页「已过滤系统对象」是写死的假话;系统 SQL 每行挂着注定被拒的「调优 →」 | 模型调优被策略跳过 | `e93d45e` + `c836ab7` |
| 8 | 深挖首句不带实例,新会话只能反问;侧栏显示内部连接名 | 读代码 | `e93d45e` + `c836ab7` |
| 9 | 知识库关系图显示原始值 none;缺口带内部前缀 q: | 截图 | `c836ab7` |
| 10 | 表格长文本挤成一字一行、SQL 溢出卡片、只有一次巡检时趋势图空白 | 截图 | `c836ab7` |
| 11 | 部署手册 / 命令卡的浏览器入口还是 /nas/me/workspace(应为 /data/state/workspace) | 读代码 | `3f074f7` |

新守卫:页面维度名必须是技能 DIM_* 常量、WDR KPI 列名必须在采集表头里(旧代码上三条都红)。
新工具:`e2e-dash-full.sh`(两实例五技能 + 存档核对 + 真浏览器 + 空状态)、`dash-browser-check.mjs`、`sso-sim.py`。
最后一轮(dev-t4):全套单测 2685 passed / 44 skipped;集群全链路 90 项全过;两架构镜像冒烟除上述两条外全过、修后复测见发布记录。
测试工具自己的坑:CDP 写死 9333 连上过本机别的项目的 Chrome;node 不退出占住端口;脚本 PATH 里 python 不对。都已修。

### v1.1.2(同日傍晚)

v1.1.1 上继续测,又修三处:两张表都关 autovacuum 时健康检查两条发现一字不差(`eb2a597`,证据写表名);
窗口变窄时布局被挤 —— 1366 笔记本开 125% 缩放、分屏半边都会碰到(`e229d51`,两列 / 单列断点、网格可收缩、宽表横滑);
「上榜总耗时 0.5 s = 0 s」(`82aa29d`)。浏览器检查加 PC 常见宽度 1920/1440/1280/1024 与数字截断。
