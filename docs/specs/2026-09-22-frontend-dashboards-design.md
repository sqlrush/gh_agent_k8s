# 前端镜像（第四个镜像）：四个大盘 —— 设计 spec

状态：设计稿 R1 已通过（user 2026-09-22，`docs/prototypes/index.html`），三个待拍板项按推荐方案定案。本文是实施依据。

## 1. 一句话

一个**纯静态**的 Web 前端镜像 `gaussdb-agent-frontend`（nginx 端四个大盘页面，没有对话页）；大盘的数字来自技能在用户 Pod 里跑出来的报告（落盘到本人 NAS 目录，经 Pod 内只读端口取），浏览器自己取数、自己画图；「深挖」在新标签页打开该用户 opencode 的会话。

> 2026-09-23 user 定为纯静态（方案 B），不再基于 deepseek-harness：四个只读大盘背不起一整套对话应用框架——绝大部分机器要禁掉，坑一个不少（dsh-k8s 的面板注册竞态、热更清空注册表、滚动窗口丢插件包全来自 Host 本身）；R1 设计稿本来就按真实 JSON 契约渲染，直接演化成产品代码。网关三路分发、4097、报告契约、深挖跳转与方案 A 完全一样。

## 2. 目标与非目标

目标
- 四个大盘：健康检查 / Top SQL / WDR 分析 / 知识库。信息架构与视觉按 R1 设计稿，实现时页底黄色说明不出现。
- 零按钮：只有文字链。数字全部来自技能脚本的确定性判定；模型不改级别。
- 前端 Pod 无状态、不挂 NAS、不持有任何用户口令；隔离仍由网关按工号保证。
- 没有对话页；侧栏「对话」是跳到 opencode Web 的链接（新标签页）。

非目标（本期）
- 持续采集器 / 分钟级曲线：没有采集器就没有曲线，历史只按「报告次数」。要曲线是另一个镜像。
- Top SQL 的 DB Time / CPU / IO 多维度堆叠条：topsql 脚本只取六列，客户白名单加列要走版本 DML，先不做。
- 在前端镜像里再做一套对话 UI：对话继续用 opencode 自带 Web。
- 移动端布局。

## 3. 已定决策

| # | 决策 | 定案 |
|---|---|---|
| D1 | 报告存哪、前端怎么读 | **技能落盘到本人 NAS 目录 + 用户 Pod 内多开一个只读端口（4097）**，网关把 `/reports/*` 代理过去。前端 Pod 不挂 NAS。 |
| D2 | 「深挖 →」跳到哪 | **调 opencode API 建会话并发首句，在新标签页打开 opencode Web 的该会话页**（user 2026-09-22 问及后定为新标签页：大盘留在原页，对话开在旁边，「回大盘没入口」的缺口随之消失）。浏览器直接经网关调，前端 Pod 不转手。「新对话 ↗」「最近对话 ↗」同样新标签页。 |
| D3 | Top SQL 维度 | 先按六列做，一根「当前维度」占比条。 |
| D4 | 知识库「最近检索」 | 要。`kb query` 追加检索日志到本人目录，缺口清单从它来。 |
| D5 | 前端形态 | **纯静态站**：原生 ES 模块 + 一份 CSS，**零构建、零 npm 依赖**，nginx（非 root 变体）端出。`frontend/` 目录就是发布内容。（user 2026-09-23 由方案 A「基于 deepseek-harness 插件」改为 B） |
| D6 | 图表 | 自绘 SVG（Bars / StackedBar / RankList / Sparkline / Ring），不引第三方图表库。 |
| D7 | 不基于 deepseek-harness | 不 vendored 上游、不追它的版本、没有插件/插槽机制。将来真要插件化的复杂面板再换，网关与数据口不用动。 |

## 4. 架构

### 4.1 组件

```
浏览器（SSO 后）
  └─ 客户反向代理（加 X-Agent-User / X-Agent-Trust）
       └─ 接入网关（现有）—— 三路分发，按路径前缀：
            /dash/*      → frontend Service（全体共用、无状态、1 个 Deployment）
            /reports/*   → runtime-<工号>:4097（Pod 内只读报告端口，新增）
            其它          → runtime-<工号>:4096（opencode serve，不变）
```

frontend Pod 里只有 nginx 端静态文件（HTML / JS / CSS）。**它不读任何业务数据**——大盘的取数由浏览器直接 `fetch('/reports/...')` 经网关到用户自己的 Pod。这样前端 Pod 拿不到任何人的报告，也不需要口令，连出网都不需要。

### 4.2 一次打开大盘的数据流

```
GET /dash/health                         → frontend：返回 SPA（静态）
GET /reports/health/latest.json          → 网关按工号 → runtime-u1234:4097 → 读 /nas/me/reports/health/latest.json
GET /reports/health/index.json           → 最近 N 份的清单（时间、overall、findings 计数）——历史两卡用
```

### 4.3 「深挖」数据流

```
tab = window.open('about:blank')                 ← 在点击事件里同步开，否则异步之后再开会被弹窗拦截
POST /session                                   → runtime-u1234:4096（网关注入口令）→ {id}
POST /session/{id}/prompt_async  {parts:[{text}]} → 204
tab.location = /<base64url(/data/state/workspace)>/session/{id}   ← opencode Web 的会话页（经网关）
```
建会话失败时把那个空标签页关掉并在大盘上提示，不留一个空白页。
首句模板按发现类型：慢 SQL 带 sql_id 与 avg/calls；对象类带对象名；WDR 带窗口 id；Top SQL 「调优」直接是 sqltune 的请求；知识库缺口带 finding code。**首句里只放该条发现的数据，不放平台配置**（红线第 7 条）。

**深挖不建 Pod、不管 Pod（user 2026-09-22 明确）。** 它只是又一次经网关的请求，复用网关 `ensure_ready()` 的现有语义：Pod 在跑 → 直接用（热路径不 apply）；被闲置回收缩到 0 → 拉起同一个 Deployment（会话还在，实测 7.6 s）；从没建过 → 才建（实测 6 s）。前端代码里**不得**出现任何创建 / 查询 Pod 的逻辑，也不调控制 API。

### 4.4 报告存档契约（技能侧，两仓库）

目录：`$GSDB_REPORTS_DIR/<skill>/`，容器里 `GSDB_REPORTS_DIR=/nas/me/reports`（entrypoint 设，与 `GSDB_HOME` 同级）；非容器线不设则不存档，行为不变。

```
reports/
├── health/  20260922T060312Z.json   latest.json → 最新一份的副本   index.json
├── topsql/  同上（文件名带 by：20260922T061000Z.time.json）
├── wdr/     同上 + 原生 WDR html（native.saved_path 指向这里）
├── sqltune/ <sql_id>.json（按 sql_id 覆盖，Top SQL 卡片按 sql_id 查）
└── kb/      health.json（每次 health 覆盖）· queries.jsonl（每次 query 追加一行）
```

- 写入用 `common/kb/atomic.py` 同款临时文件 + `os.replace`，前端不会读到半个文件。
- `index.json` 由写入方维护：最近 50 条 `{at, file, overall, counts}`；超过 50 份删最旧的，**只删本技能目录里的**。
- 存档失败**不影响技能本身的输出**（stdout 照常），但要打一行 `[warn] 报告未存档：<原因>`——大盘会显示「无报告」而不是静默空白。
- 实现：`common/reports.py`（`archive(skill, payload, *, name=None)`），health / topsql / wdr / sqltune / kb 各在输出末尾调一次。这是 skill 改动，**同步到 gh_skill**。

### 4.5 报告只读端口（Pod 侧）

- `podctl serve-reports --dir $GSDB_REPORTS_DIR --port 4097`：标准库 `http.server`，**只 GET、只在该目录内、只 `.json` / `.jsonl` / `.html`**，路径规范化后必须仍在目录内（防 `..`），目录列表关闭，`Cache-Control: no-store`。
- `entrypoint.sh` 在 `opencode serve` 之后拉起，作为后台进程；停机时随 1 号进程退出。
- `runtime.yaml`：`ports` 加 4097；NetworkPolicy 入站从网关放行 4097；Service 加端口。kb-import Pod 不开（它没有大盘）。
- 探针不动：报告端口不是就绪条件。

### 4.6 网关改动

- `server.py::_proxy`：按路径前缀分三路（见 4.1）。`/reports/*` 的 Upstream 端口 4097；`/dash/*` 的 Upstream 是 `frontend.<ns>.svc:80`，且**不注入 opencode 口令**、不 touch 活动时间（看静态页不算用）。
- 配置项新增 `GATEWAY_FRONTEND_SERVICE`（默认 `frontend`），走 ConfigMap 键 `FRONTEND_SERVICE` —— 三处一起改（代码 / ConfigMap / gateway.yaml 映射），守卫已有。
- `/reports/*` 也要 `require_prerequisites`+`ensure_ready`：用户 Pod 没起就没有报告，回 503 同现有语义。

### 4.7 前端镜像

- `docker/Dockerfile.frontend`：单阶段，`nginxinc/nginx-unprivileged:1.27-alpine`（非 root，监听 8080），`COPY frontend/ /usr/share/nginx/html/dash/`，`COPY docker/nginx-dash.conf /etc/nginx/conf.d/default.conf`。**没有构建步骤**：`frontend/` 目录里的文件就是发布内容。
- `nginx-dash.conf`：只服务 `/dash/`（网关转发时路径带着 `/dash` 前缀）；`/dash/` 与 `/dash/index.html` 之外的路径按文件返回，找不到的 `.html` 回落到 `index.html`（前端自己按 `location.pathname` 切页）；`Cache-Control: no-cache`（大盘改了要立刻生效）；不开目录列表；`/healthz` 回 200 给探针。
- 镜像体积目标 < 30 MB。基础镜像随离线包一起打（同 openGauss 镜像的做法，`package-images.sh` 加 `FRONTEND_BASE_IMAGE`）。
- `k8s/base/frontend.yaml`：Deployment（1 副本，只读根文件系统 + `emptyDir` 给 nginx 的 cache/run 目录）+ Service `frontend`（80 → 8080）；NetworkPolicy：入站只放行网关，**出站一条都不放**（静态站不需要 DNS）。

### 4.8 `frontend/` 目录（发布内容 = 源码）

```
frontend/
├── index.html            外壳:侧栏(四个大盘 + 对话链接 + 当前工号/目标)+ 主区容器;按 pathname 装页面
├── styles.css            由 docs/prototypes/proto.css 演化,token 不变
├── lib/
│   ├── data.js           取数:fetchLatest(skill) / fetchIndex(skill) / fetchSqltune(sqlId) / fetchKbHealth() / fetchKbQueries()
│   │                     全部 fetch('/reports/...'),同源无凭据;404 → {missing:true};网络错误 → {error:msg}
│   ├── dig.js            深挖:openDig(prompt) —— 点击瞬间 window.open('about:blank'),再 POST /session → prompt_async,
│   │                     成功后给新标签页设地址,失败关掉它并在页面上提示(§4.3)
│   ├── charts.js         自绘 SVG:ring / stack / bars / sparkline / rank(从设计稿抽出)
│   ├── fmt.js            数字/时间/耗时格式化(设计稿里的 fmt / sec / mmdd)
│   └── shell.js          侧栏与页头渲染、路由(/dash/health 等四条)、工号与目标显示(来自 /reports/whoami,见下)
└── pages/
    ├── health.js         健康检查大盘(§4.9 映射)
    ├── topsql.js
    ├── wdr.js
    └── kb.js
```

- **原生 ES 模块、没有框架、没有构建**。页面 = 一个 `render(root, data)` 函数 + 模板字符串，与设计稿同一写法；设计稿里的 `DATA` 常量换成 `data.js` 取回的真报告。
- 「当前工号 / 目标库」从哪来：网关在转发 `/dash/*` 与 `/reports/*` 时都带 `X-Forwarded-User`，但浏览器看不到请求头。给 4097 加一个 `/whoami.json`（`podctl serve-reports` 直接返回 `{"user_id": $GSDB_USER_ID}`，不读文件）；目标库取自最近一份报告的 `target`。
- **报告契约的钉子**：`frontend/lib/contract.json` 列出每个页面读到的字段路径（如 `health: ["dims[].dimension","dims[].available","findings[].severity",...]`）；`agent/tests/test_frontend_contract_units.py` 拿 Python 侧的样例报告（`health_dict` / wdr `to_dict` / topsql payload / kb `health_dict`）逐条核对路径存在。这是跨语言契约唯一的守卫，没有 JS 工具链也能跑。
- 页面级验收：`scripts/k8s/e2e-dash.sh` 用 headless Chrome `--dump-dom` 打开四个页面（对着本地集群，或对着一个用样例 JSON 起的临时 `python3 -m http.server`），断言各区块非空、console 零错误——设计稿阶段 `window.top` 那类只有真浏览器能抓的问题就是这么发现的。

### 4.9 四个大盘的数据映射

| 大盘 | 主来源 | 页面区块 ← 字段 | 降级态 |
|---|---|---|---|
| 健康检查 | `health/latest.json` + `index.json` | 页头 ← `target/conn/overall/user_id/generated`；环与状态带 ← `dims[].{dimension,available,worst}`（worst 由该维度 findings 取最坏）；发现榜 ← `findings[]`；维度卡 ← `dims[].{headline,headers,rows}`；子技能 ← `sub_skills[]`；历史 ← `index.json` | 无报告 → 引导卡；某维度 `available=false` → 灰显划线并写 `note` |
| Top SQL | `topsql/latest.<by>.json` + `sqltune/<sql_id>.json` | 页签 ← 五个 by；汇总/占比/榜单 ← 行的六个字段与其和；逐条卡 ← 行 + sqltune 报告（有则显示建议与优化后 SQL） | 无该 by 的报告 → 页签灰 + 引导；sqltune 缺 → 「尚未调优 · 调优 →」 |
| WDR | `wdr/latest.json` + 上一份 + `index.json` | 页头 ← `window/scope/native/wdr_enabled`；8 KPI ← `dims[loadprofile,dbstat]` 的 rows，▲▼ ← 上一份同名指标；DB Time 构成/等待 ← `dims[waits]`；Top SQL ← `dims[topsql]`；三小卡 ← `dims[checkpoint,cache,fileio]`；结论 ← `findings[]`；历史 ← `index.json` | `wdr_enabled=false` → 整页一张「怎么开」卡；无上一份 → KPI 不显示 ▲▼ 并标「无对比窗口」 |
| 知识库 | `kb/health.json` + `kb/queries.jsonl` | 四顶卡与三类知识 ← `status.{mode,attached,reason,counts,vector,graph}` + `index_state` + `readonly` + `inbox`；健康自检 ← `pending[]` + `file_warnings[]`；缺口清单 ← `misses[]`；最近检索 ← `queries.jsonl` 最近 7 天 | `attached=false` → 一张「未接入」卡并显示 `reason` |

### 4.10 对话在哪发生（user 2026-09-22 确认）

对话全部在用户自己 Pod 的 opencode Web 里，前端镜像没有对话组件。两个界面同域名、同网关、同工号，只是路径不同（`/dash/*` 是大盘，其余是 opencode Web）。侧栏「对话」分组与所有「深挖 →」都**在新标签页打开**（D2）：大盘留在原页，对话开在旁边，一边看数字一边问。

被否掉的两种：同标签页跳转（大盘没了，opencode Web 不可定制、回不来）；同源 iframe 嵌进主区（未验证 opencode 是否允许，且主区宽度装对话体验一般）。

## 5. 技能侧改动（两仓库）

| # | 改动 | 归属 |
|---|---|---|
| S1 | `common/reports.py` 存档 + 五个技能末尾调用 + `GSDB_REPORTS_DIR` | 两仓库 |
| S2 | `gaussdb-kb health --json`：输出 `{status, readonly, inbox, file_warnings, index_state, pending, misses}`，文本输出不变 | 两仓库 |
| S3 | `gaussdb-kb query` 追加一行到 `kb/queries.jsonl`：`{at, q, hits_cases, hits_rules, how:[semantic|keyword], elapsed_ms}`；`_misses_top` 改从它统计（现有实现保留为退路） | 两仓库 |
| S4 | `gaussdb-wdr` 原生报告落到 `reports/wdr/`（`native.saved_path`），不再落临时目录 | 两仓库 |

## 6. 安全

- 前端 Pod：nginx 非 root、只读根文件系统、无状态、不挂 NAS、不持口令、不调 K8s API，NetworkPolicy **出站一条都不放**（静态站连 DNS 都不需要）。
- 报告端口：只读、只本目录、只三种后缀、路径规范化；只有网关能连（NetworkPolicy）；网关按工号路由，所以一个人永远只能读到自己的 `reports/`。
- 大盘显示的是用户自己登录的库的诊断数据（目标 IP、库名是他自己填的连接信息），不显示任何平台配置取值（红线第 7 条）。
- 深挖首句只含发现数据。

## 7. 测试与验收

- 单测：`common/reports.py`（原子写、index 维护、超 50 删最旧只删本目录、失败不影响输出）；`podctl serve-reports`（`..` 拒绝、非白名单后缀 404、目录列表 404、只 GET）；网关三路分发（前缀匹配、`/dash` 不注入口令不 touch）；`kb health --json` 形状；`kb query` 日志行。
- 前端：`contract.json` 对 Python 样例报告的字段路径守卫（pytest）；`nginx-dash.conf` 的路由守卫（pytest 起真 nginx 太重，改为对 conf 文本钉住 `/dash/`、`no-cache`、`autoindex off`、`/healthz`）；四个页面在 **headless Chrome `--dump-dom`** 下各区块非空、console 零错误（脚本，设计稿阶段抓到的 `window.top` 那类问题只有真浏览器能抓）。
- e2e（本地集群）：新工号登录 → 会话里跑一次 health → `/reports/health/latest.json` 200 → 打开 `/dash/health` 出现维度环 → 点深挖 → 落到 opencode 会话页且首句已发出。
- 交付守卫：离线包含四个镜像；`docs/prototypes`、`docs/specs` 不进包；参数手册、仓库结构说明、功能清单补前端镜像。

## 8. 分阶段

1. 技能侧 S1–S4（两仓库，独立可交付：即使前端没做，报告也已经在 NAS 上）。
2. Pod 侧：`serve-reports` + entrypoint + 清单 + 网关三路分发。
3. 前端：`frontend/` 静态站（外壳 + 取数 + 深挖 + 四个页面）、`/whoami.json`、契约守卫、`Dockerfile.frontend` + `nginx-dash.conf`。
4. 清单与文档：`k8s/base/frontend.yaml`、NetworkPolicy、打包脚本、三份文档。
5. e2e 验收 + 离线包重打。
