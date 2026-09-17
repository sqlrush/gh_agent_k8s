# opencode + gaussdb skills 容器化设计

设计日期：2026-09-15　技能基线：gh_skill `skills-v12.9`（`6190e4a`，分支 `release/v12.9`）　状态：已定稿，待实施

## 0. 仓库关系

| 仓库 | 内容 | 本项目里的角色 |
|---|---|---|
| [gh_skill](https://github.com/sqlrush/gh_skill) | 17 个 `gaussdb-*` skill、`common/`、白名单脚本、验收 | **非 Pod 的 opencode 独立部署**继续用它；只收与部署方式无关的技能功能修复 |
| **gh_agent_k8s**（本仓库） | `agent/`：从 gh_skill 标签复制来的技能代码 + Pod 适配改动；`docker/`、`k8s/`、`scripts/`、`docs/` | 容器化本身；镜像从 `agent/` 构建 |

**开发规范（2026-09-17 定）**

1. 和 Pod 架构相关的适配代码全部放本仓库 `agent/`，不动 gh_skill。
2. 如果是 skill 本身的功能改动（与部署方式无关的修复或增强），两个仓库同时改：本仓库 `agent/` 一份，gh_skill 一份同样的 diff。这样 gh_skill 继续适配非 Pod 的独立部署场景。
3. `agent/UPSTREAM` 记录它基于 gh_skill 的哪个标签、哪个提交；首次导入用 `scripts/vendor-from-gh-skill.sh`，之后靠 cherry-pick 同步，不整目录覆盖。
4. 每项改动在计划里标明归属：「仅本仓库」或「两仓库」。

## 1. 目标与边界

**目标**

1. opencode + skills 放进 k8s Pod；所有 Pod 无状态，状态全部在 NAS 上。
2. 两种镜像：runtime（诊断 skill + 知识库查询）和 kb-import（知识库导入）。
3. runtime 的状态在 SQLite（`opencode.db`）里，db 文件在 NAS 挂载路径上运行；知识库 md 文件在 NAS 上。
4. 每个用户一个 runtime Pod，实现人、库、会话隔离。

**不做**

- 向量库、图谱库 Pod。runtime 走知识库文件模式（gh_skill 09-05 验证首选条款与案例和带库模式逐条一致）。
- 按工号建 Pod、回收 Pod 的网关：平台做，本项目提供 Pod 模板与环境变量契约。
- Web 界面与 CLI 接入：镜像做完后另起项目（见 §9）。
- 修改 opencode 源码。

## 2. 已定决策（2026-09-15）

| # | 决策 | 结果 |
|---|---|---|
| 1 | SQLite 放哪 | 在 runtime Pod 里运行，db 文件通过 NAS 挂载进 Pod（不用 Litestream）。§6 列出降低风险的措施 |
| 2 | 镜像数量 | 三个镜像：runtime、kb-import、frontend。runtime 与 kb-import 的 skill 集合物理分开，gaussdb-kb 拆成查询 / 导入两个 skill；frontend 基于 deepseek-harness 的前端改造（2026-09-16 补） |
| 3 | 用户接入 | Pod 内 `opencode serve` 为唯一后端；Web 走 frontend 镜像；CLI 用 opencode 自带的 `opencode attach`（ttyd 备选）。frontend 在两个后端镜像做完后做 |
| 4 | 网关 | 平台做。两种模式：对话结束回收 Pod / 保留 Pod 等待重连 |
| 5 | 仓库 | 容器化放本仓库；gh_skill 只做技能侧改动 |
| 6 | 接入网关 | 共享的控制面组件（1 个 Deployment、2 副本、无状态）：鉴权、按工号找 / 建 / 回收 runtime Pod、代理。不是每人一个。用户状态不需要「还原」，挂载即恢复（2026-09-16） |
| 7 | 代码归属 | Pod 适配代码只在本仓库 `agent/`；技能本身的功能改动两仓库同时改，gh_skill 继续服务非 Pod 部署（2026-09-17，见 §0 开发规范） |

## 3. 总体架构

```
  浏览器 ──SSO──▶ frontend Pod（共享，无状态）──▶ 接入网关 ──▶ runtime-<工号> Pod ──▶ GRMP ──▶ GaussDB
  本机 opencode attach ────────────────────────▶ 接入网关 ──▶ kb-import-<工号> Pod          模型服务
                                                                   │                           ▲
                                                     ┌─────────────┴───────────────┐           │
                                                     ▼                             ▼           │
                                            NAS users/<工号>/（本人状态）   NAS kb/（知识库）   两种后端 Pod 都出站到模型服务
                                            runtime 读写                   runtime 只读
                                                                           kb-import 读写
```

frontend 不挂 NAS、不带 skill，只把浏览器和某个用户的 `opencode serve` 接起来；按工号找 Pod 的事仍由网关做。

**组件清单**

| 组件 | 数量 | 状态 | 谁做 |
|---|---|---|---|
| 接入网关 Pod | 1 个 Deployment，2 副本 | 无状态 | 平台（或本仓库出第四个镜像，见 §8） |
| frontend Pod | 1 个 Deployment | 无状态 | 本仓库，第二版 |
| runtime Pod | 每人 1 个 | 无状态，数据在 NAS `users/<工号>/` | 本仓库出镜像，网关创建 |
| kb-import Pod | 1 个，管理员共用 | 无状态，数据在 NAS `kb/` | 本仓库出镜像 |
| NAS | 1 个 RWX PVC | 唯一的状态所在 | 平台提供 |
| GRMP 中间件、模型服务 | 既有 | — | 既有 |

## 4. NAS 目录布局

一个 RWX PVC（NFS）。Pod 用 `subPath` 只挂自己的那一层。

```
/nas
├── kb/                          知识库唯一真相。kb-import 读写，runtime 只读
│   ├── rules/ cases/ graph/ sources/ errata/ guides/ archive/
│   ├── INDEX.md RULES.md CASES.md VERSION kb.yaml
│   ├── inbox/                   导入工作区（上传文件、候选、review.md）
│   ├── index/                   过程产物（misses.log 等）
│   ├── eval/feedback.yaml
│   └── .lock                    写入互斥
├── users/<工号>/                runtime Pod 的全部状态
│   ├── xdg-data/opencode/       opencode.db(+wal/shm)、tool-output/、snapshot/、log/
│   ├── xdg-state/opencode/      上次模型、输入历史
│   ├── workspace/               工作目录：上传文件、报告
│   ├── gdaa/                    GSDB_HOME：sessions/、config.yaml（每次启动从镜像模板重生成）
│   ├── backup/                  opencode.db 的退出时副本
│   └── .owner                   独占标记
└── admins/<工号>/               kb-import Pod 的本人状态，布局同 users/
```

**NFS 要求**：NFSv4.1 以上；导出目录对镜像运行 uid（1000）可写；PV `mountOptions: [nfsvers=4.1, hard, local_lock=all]`。`local_lock=all` 让 SQLite 的文件锁只在本 Pod 生效，绕开 NFS 服务端锁的实现问题——前提是 §6 的单 Pod 独占必须成立。

## 5. 镜像

三个镜像。两个后端镜像出自同一个 Dockerfile（多阶段，两个 target），技能代码来自本仓库 `agent/`，不在构建时拉 gh_skill；frontend 单独一个 Dockerfile。

**frontend（`gaussdb-agent-frontend`）**：deepseek-harness 的 Web 前端改造成对接 opencode 的 HTTP API 与 SSE 事件流。无状态、不挂 NAS、不含 skill，一个共享 Deployment 服务所有用户；SSO 后由网关把请求转到该用户的 runtime Pod。在两个后端镜像完成后再做，见 §9。

**两个后端镜像**：

| | `gaussdb-agent-runtime` | `gaussdb-agent-kb-import` |
|---|---|---|
| 基础层（共用） | Python 3.9+、psycopg2、cryptography、PyYAML、opencode、ripgrep、`models.json`、`opencode.jsonc`、AGENTS.md、`common/`、`scripts/registry/`、`entrypoint.sh` | 同左 |
| skills | 16 个诊断 skill + `gaussdb-kb`（查询） | `gaussdb-kb`（查询）+ `gaussdb-kb-import` |
| `kb/` 挂载 | 只读 | 读写 |
| GRMP 令牌 | 有 | 没有，不接触任何数据库 |
| 出站网络 | GRMP、模型服务、DNS | 模型服务、DNS |
| 谁能拿到 | 所有用户 | 知识库管理员 |

镜像内固定内容：

- `opencode.jsonc`：`share: "disabled"`；模型 provider 的 key 与地址写 `{env:MODEL_API_KEY}`、`{env:MODEL_BASE_URL}`；默认模型写死，避免依赖 state 目录里的 `model.json`。
- `XDG_CONFIG_HOME`、`XDG_CACHE_HOME` 指向镜像内目录（`models.json`、`rg` 预置，内网不联外）。
- 以 uid 1000 运行，不是 root。
- 架构：x86_64 与 aarch64（鲲鹏）各一份；psycopg2 用预编译轮子。
- 镜像标签：`<skills 标签>-oc<opencode 版本>`，如 `skills-v13.0-oc1.18.27`。

**不做的事**：不在 Pod 里 `opencode auth login`（`auth.json` 不应出现）；不装 sshd。

## 6. runtime Pod 规格

```yaml
Deployment runtime-<工号>   replicas: 1   strategy: Recreate
  securityContext: { runAsUser: 1000, fsGroup: 1000 }
  terminationGracePeriodSeconds: 60
  containers:
    - opencode: entrypoint.sh → opencode serve --hostname 0.0.0.0 --port 4096
      env: 见下表
      lifecycle.preStop: 等待网关摘流量（sleep 5）
      readinessProbe: HTTP 4096（路径实施时确认）
  volumes:
    - nas: PVC subPath users/<工号> → /nas/me
    - kb:  PVC subPath kb, readOnly: true → /nas/kb
```

**环境变量契约**（平台 → 镜像的唯一接口）

| 变量 | 值 | 来源 |
|---|---|---|
| `XDG_DATA_HOME` | `/nas/me/xdg-data` | entrypoint 固定 |
| `XDG_STATE_HOME` | `/nas/me/xdg-state` | entrypoint 固定 |
| `GSDB_HOME` | `/nas/me/gdaa` | entrypoint 固定 |
| `GSDB_KB_DIR` | `/nas/kb` | entrypoint 固定 |
| `GSDB_USER_ID` | 工号 | 平台 |
| `GRMP_API_HOST` | 中间件地址 | ConfigMap |
| `GRMP_AUTH_TOKEN` | 本人令牌 | Secret，按人 |
| `MODEL_API_KEY`、`MODEL_BASE_URL` | 模型服务 | Secret / ConfigMap |
| `OPENCODE_SERVER_PASSWORD` | 本 Pod 随机口令 | 网关生成，只有网关知道 |
| `GSDB_KB_INBOX` | 仅 kb-import：`/nas/kb/inbox/uploads` | entrypoint 固定 |

工作目录固定 `/nas/me/workspace`；opencode 会话按目录路径归属项目，路径重建前后一致。

### 6.1 风险机制

SQLite 在网络文件系统上出问题只有两个机制：

| 机制 | 说明 | 触发场景 |
|---|---|---|
| 文件锁不可靠 | SQLite 靠 `fcntl` 建议锁协调读写；NFS 的锁由客户端和服务端协作实现，实现差异多 | 两个进程（尤其在两台机器上）同时打开同一个 db |
| WAL 的 `-shm` 是共享内存 | WAL 用 mmap 的 `-shm` 在多个进程间同步索引；跨机器时不一致，SQLite 官方因此声明 WAL 不支持网络文件系统 | 同上；单进程时 `-shm` 只在本机页缓存里 |

所有措施围绕一件事：**同一个 db 文件任何时刻只有一个 Pod、一个进程打开。**

### 6.2 措施

**① NFSv4.1 + 挂载参数**（PV `mountOptions`）
- `nfsvers=4.1`：锁在协议内，不依赖 NLM。
- `hard`：NAS 短暂不可达时请求挂起等待，不把 EIO 交给 SQLite（`soft` 会直接损坏）。
- `local_lock=all`：锁只在本 Pod 内核生效，绕开服务端锁；代价是失去跨机器互斥，完全依赖 ②。
- 验证：冒烟脚本检查 `mount | grep /nas` 的参数。

**② 单 Pod 独占，两道保险**
- 第一道在平台：Deployment `strategy: Recreate`，删除完成后才允许再创建。
- 第二道在 entrypoint，`/nas/me/.owner`：

```
启动:
  读 .owner → {pod, ts}
  若存在且 ts 距今 < 90 秒 且 pod ≠ 自己 → 每 5 秒重读，最多等 55 秒
      仍被占 → 日志「另一个 Pod 仍持有 db」，exit 1
  写入自己的名字和时间戳
运行: 后台每 30 秒刷新 ts
退出: 删除 .owner
```
- 90 秒 = 3 个刷新周期，容忍 NAS 偶发慢；旧 Pod 强杀没删标记时，新 Pod 最多等 90 秒接管。

**③ 优雅停机**
- `terminationGracePeriodSeconds: 60`；`preStop: sleep 5` 给网关摘流量。
- opencode 收 SIGTERM 正常退出，WAL 合并回主库。
- entrypoint 在 opencode 退出后补 `PRAGMA wal_checkpoint(TRUNCATE)`（Python 自带 sqlite3），保证 `-wal` 清空。
- 验证：正常删除 Pod 后 `-wal` 大小为 0。

**④ 启动自检**
- `PRAGMA integrity_check`（超过 500 MB 改 `quick_check`）。
- 不通过：坏文件改名 `opencode.db.corrupt-<时间>` 留证 → 从 `backup/` 最新一份恢复 → 再检 → 日志与 Pod 事件里明确写「db 损坏，已从 <时间> 备份恢复」。备份也不可用则空库启动，同样报出。**不静默。**

**⑤ 备份**
- Pod 正常退出时（③ 之后）复制 `opencode.db` 到 `/nas/me/backup/opencode.db.<时间>`，保留 3 份。
- NAS 层每日快照（平台要求）。

**⑥ 客户环境压测**
- 客户 NAS，20 个 runtime Pod 跨多节点同时在线，脚本驱动 `opencode run` 连续对话含长输出，5 个工作日。
- 采集：`SQLITE_BUSY` / `database is locked` 次数；每轮对话延迟 p50 / p95（对照组：emptyDir 本地盘）；每日 `integrity_check`。
- 通过：零损坏；locked 为零或仅在 NAS 故障期间；p95 与对照组差距可接受。
- 不通过：改用「本地运行 + Litestream 复制到 NAS」，这是唯一退路，提前告知客户。

### 6.3 交付文档措辞

> 运行环境把 opencode 的会话数据库（SQLite，WAL 模式）放在 NAS 挂载路径上运行。SQLite 官方文档指出 WAL 模式不支持网络文件系统，主要风险是多进程同时访问导致的数据损坏。本部署通过以下措施将该风险控制在单点故障范围内：NFSv4.1 挂载、`local_lock=all`、平台保证同一用户同一时刻仅一个 Pod、Pod 启动时的独占标记、优雅停机与 WAL 合并、启动完整性自检、退出时备份与 NAS 每日快照。
>
> 上线前在贵方 NAS 环境完成 5 个工作日压测，结果见附件。若压测未通过，改用「本地运行 + 持续复制到 NAS」方案。
>
> 残余风险：Pod 被强制终止且平台未能保证独占时，可能出现数据库损坏，影响范围为该用户的对话历史，可从最近一次备份恢复。
>
> 贵方确认知悉上述风险并接受本部署方式。　确认人：＿＿＿　日期：＿＿＿

## 7. kb-import Pod 规格

与 runtime 同结构，差异：镜像换 kb-import；`kb/` 读写；无 GRMP 令牌；`subPath admins/<工号>`。

- **写入互斥**：kb-import Deployment 第一版 replicas=1，管理员共用；`kb.py` 的写入子命令再取 `/nas/kb/.lock`（O_EXCL 建文件，含 Pod 名与时间，10 分钟无刷新视为过期）。
- **原子写**：`index` 重写 `INDEX.md`、`RULES.md`、`CASES.md` 先写临时文件再改名。runtime 每次调用重新扫目录，导入后立即可见。
- Git 审计（每次 apply 提交到 `/nas/kb.git`）：第二阶段可选。

## 8. 接入网关

**定位**：所有用户请求的入口，共享的控制面组件。1 个 Deployment、2 副本、无状态；不是每人一个。

**职责**

1. SSO 鉴权，得到工号。
2. 查 k8s：`runtime-<工号>` 是否存在且 Ready。不存在则创建 Deployment：`subPath users/<工号>`、`GSDB_USER_ID`、本人 GRMP 令牌的 Secret 引用、随机 `OPENCODE_SERVER_PASSWORD`；等待 Ready（冷启动约 10 秒）。
3. 反向代理到该 Pod 的 4096 端口，带基本认证。`opencode attach` 的连接走同一条路。
4. 按生命周期模式回收 Pod。

**没有「还原」步骤**：用户的会话、对话记录、压缩摘要、上传文件、skill 登录会话全在 NAS `users/<工号>/`。新 Pod 挂上这个目录，opencode 打开其中的 `opencode.db`，历史会话自动出现。网关不复制、不恢复任何数据；「按用户名确定历史」= 用工号拼出挂载路径。

**授权：谁能用哪个镜像，在网关这一步决定。** 平台不保存用户信息，身份来自统一认证；网关只需要一张很小的授权映射：

| 角色 | 能建的 Pod | 映射来源（二选一） |
|---|---|---|
| 普通用户 | `runtime-<工号>`（runtime 镜像，`kb/` 只读，带本人 GRMP 令牌） | 存在 Secret `grmp-token-<工号>` 即视为已开通 |
| 知识库管理员 | 另加 `kb-import-<工号>`（kb-import 镜像，`kb/` 读写，无 GRMP 令牌） | 统一认证里的组（如 AD 组 `kb-admins`）→ 网关按组判定；没有组就用 ConfigMap `agent-roles` 列出管理员工号 |

镜像的选择只发生在网关挑 Deployment 模板的那一刻，用户碰不到 k8s API，不能自选镜像。runtime 模板里 `kb/` 是 `readOnly: true`、没有令牌以外的凭据，即使拿到别人的 Pod 也改不了知识库。

**自身状态**：无。工号到 Pod 的映射是命名约定；闲置时间记在 Deployment 的 annotation 上，网关重启不丢。

**需要**：SSO 对接；k8s RBAC（本命名空间内 Deployment 的创建 / 删除 / 查询）；每人 GRMP 令牌的来源。

**谁做**

| 客户平台现状 | 做法 | 本仓库交付 |
|---|---|---|
| 已有带 SSO 的 API 网关 | 在其后加一个「Pod 供给器」，按本节契约建 / 回收 Pod 并代理 | 契约 + Pod 模板 |
| 没有 | 本仓库出第四个镜像 `gaussdb-agent-gateway`：鉴权对接、供给、代理 | 镜像 + 清单 |

待客户确认平台现状后定。

**生命周期**：两种模式，镜像不感知。

| 模式 | 网关动作 | 说明 |
|---|---|---|
| 对话结束回收 | 会话结束 → 删除 Deployment（走 60 秒 grace） | 下次登录重新创建，挂同一 `users/<工号>`，历史自动回来 |
| 保留 Pod | 不删，重连复用 | 省冷启动；建议加闲置超时（如 8 小时无请求缩到 0），否则离线用户长期占内存 |

网关**必须**保证：同一工号任一时刻只有一个 runtime Pod（Recreate + 删除完成后再创建）。§6 的 `.owner` 是第二道保险。

## 9. 用户接入（后端镜像完成后做）

- 后端 Pod 内只有 `opencode serve`（无界面的 HTTP 服务）。
- opencode 自带 `opencode web`：同一个服务加内置的浏览器界面，1.18.27 已确认存在。可作为 frontend 完成前的过渡，启动命令换一个词；界面是通用编程 agent 的，不可定制。是否用它做第一版待定。
- Web：frontend 镜像，deepseek-harness 前端改造，对接 opencode HTTP API 与 SSE 事件流；改造量待后端镜像完成后评估。
- CLI：opencode 自带的远程终端客户端，命令是 `opencode attach <url>`——用户本机装 opencode，跑 `opencode attach https://网关/<工号>`，本机 TUI 连远端的 `serve`，看到的会话与 Web 一致。桌面不能装软件时用 ttyd 在浏览器里跑 `opencode attach http://127.0.0.1:4096`。
- 认证：`OPENCODE_SERVER_PASSWORD` 由网关生成注入，Pod 入站只放行网关与 frontend。

## 10. 技能侧改动（在本仓库 `agent/` 完成；标「两仓库」的同步到 gh_skill）

| # | 改动 | 原因 | 归属 | 测试 |
|---|---|---|---|---|
| 1 | gaussdb-kb 拆成 `gaussdb-kb`（query/health/search/cite-check）与 `gaussdb-kb-import`（ingest/index/validate/setup/feedback/eval/propose/review/apply/contract） | 两镜像物理分离 | 仅本仓库 | 结构闸门；两份 SKILL.md；`test_kb_*` 按路径迁移；红线数 17→18；AGENTS.md 技能匹配改两条 |
| 2 | `GSDB_HOME` 未设且默认目录不可写 → `ConfigError` 明确提示 | 默认值在 skills 目录里，镜像只读；独立部署下同样是「一句话代替堆栈」 | 两仓库 | 只读临时目录断言文案 |
| 3 | `common/kb/lock.py` 写入互斥（`.lock`，10 分钟过期接管）；`common/kb/atomic.py` 临时文件 + `os.replace` | 多 import、读写并发；独立部署的共享沙箱同样多人共用一个知识库目录 | 两仓库 | 取得 / 冲突 / 过期接管；两进程并发 apply e2e |
| 4 | api 模式加载会话时忽略文件里的 host/port，每次从 `GRMP_API_HOST` → `config.yaml` 取 | 会话进 NAS 后 Pod 重建不带旧地址；独立部署下就是 09-14 的现场问题 | 两仓库 | 旧地址会话 + 新环境变量断言；会话 e2e 加一例 |
| 5 | findings JSON 信封加 `user_id`，health 报告标题下「执行人：<GSDB_USER_ID>」 | 审计落到人 | 仅本仓库 | 设 / 不设两种 |
| 6 | 查询版 `kb.py` 保留导入子命令名作桩：「本环境不含知识库导入功能」退出码 2；`health` 加「知识库只读」行 | 静默失败比报错危险 | 仅本仓库 | 桩文案；只读目录 health |

gh_skill 收到 #2、#3、#4 后发布 `skills-v12.10`；本仓库 `agent/` 的版本号独立（镜像标签用本仓库标签）。

`contract --apply` 改成构建期工具：仓库里跑一次，结果随镜像发布，SKILL.md 不再引导运行时执行。

顺序：2 → 4 → 5 → 1 → 3 → 6。每项独立提交。不改 opencode；安全红线、会话契约、白名单不变。

## 11. 安全

- 每人 Pod 只挂自己的 `users/<工号>`；`kb/` 只读。
- NetworkPolicy：入站只放行网关；出站按 §5。Pod 之间不通。
- 非 root；Secret 只以环境变量注入；`share` 禁用；无 `auth.json`。
- 网关的 k8s RBAC 只限本命名空间内 Deployment 的创建 / 删除 / 查询；`OPENCODE_SERVER_PASSWORD` 只有网关知道。
- 库的隔离仍依赖 GRMP 按人令牌与授权；Pod 负责把本人令牌送到中间件。
- 本仓库公开：不放客户名称、地址、令牌；Secret / ConfigMap 只有样例。

## 12. 验证

本地 OrbStack k8s，hostPath 目录当 NAS（无法模拟 NFS 锁，NFS 相关项在客户环境压测）。接 GRMP mock 与模型服务。

1. 隔离：A 登录后，B 的 Pod 里看不到 A 的会话、文件、句柄。
2. 无状态：kill A 的 Pod，重建后对话历史、上传文件、登录会话都在；`.owner` 在旧 Pod 未退出时挡住新 Pod。
3. 知识库流转：kb-import 导入一个案例，runtime 下一次 `kb.py query` 命中；runtime 镜像里没有 ingest 命令。
4. 只读：runtime 对 `/nas/kb` 写入失败且不影响检索。
5. 自检与恢复：人为损坏 `opencode.db`，启动时从 backup 恢复并报出。
6. gh_skill 的现有验收在 Pod 内重跑：单测、矩阵、harness、会话 e2e、kb 三态。
7. 客户环境：NFS 上 5 个工作日压测。

## 13. 交付物

| 产出 | 仓库 |
|---|---|
| 后端 `Dockerfile`（两个 target）、`entrypoint.sh`、`opencode.jsonc` 模板；frontend `Dockerfile` | 本仓库 `docker/` |
| k8s 清单模板（runtime / kb-import Deployment、PVC、NetworkPolicy、Secret/ConfigMap 样例） | 本仓库 `k8s/` |
| 环境变量契约、网关契约、交付手册容器化章节（含 §6.3 与客户确认表） | 本仓库 `docs/` |
| 镜像冒烟、集群验证脚本 | 本仓库 `scripts/` |
| 技能侧改动与测试 | 本仓库 `agent/`；#2、#3、#4 同步到 gh_skill，发布 `skills-v12.10` |
| 网关镜像 `gaussdb-agent-gateway`（仅当平台没有网关） | 本仓库，待客户确认 |
| 离线镜像包（`docker save`）+ 校验值 | 交付时生成，不入库 |

## 14. 风险与待办

| 项 | 处理 |
|---|---|
| SQLite on NFS | §6 措施 + 客户环境压测 + 客户确认 |
| harness web 插件与 opencode API 的差距 | 镜像完成后评估，另起项目 |
| 银行桌面不能装 opencode | ttyd 备选 |
| kb-import 多管理员并发 | 第一版 replicas=1 + 锁 |
| `opencode.db` 无限增长 | 每人 2 GB 配额；用户可删会话；后续加清理策略 |
| opencode 升级迁移不可回退 | 升级前 NAS 快照 |
| 跨对话长期记忆 | opencode 没有此功能。可选特性：`users/<工号>/workspace/memory.md`，AGENTS.md 引导模型开始时读、结束时更新；文件在 NAS 上随目录回来。不影响架构 |
| 网关由谁做 | 待客户确认平台是否已有带 SSO 的网关 |

## 15. 实施顺序

| 步 | 内容 | 产出 | 验证 | 估计 |
|---|---|---|---|---|
| 1. 技能侧改动 | 先把 gh_skill `skills-v12.9` 导入本仓库 `agent/`；§10 六项在 `agent/` 做；#2、#3、#4 同步到 gh_skill 发 `skills-v12.10` | `agent/` 6 个提交 + 测试；gh_skill 3 个提交 | `agent/` 下跑 gh_skill 的全部验收层；gh_skill 那边同样跑 | 3–4 天 |
| 2. 镜像 | Dockerfile 两 target、entrypoint、`opencode.jsonc` 模板、预置 `models.json` / `rg` / opencode 二进制；两架构 | 两个镜像 | `docker run` 挂本地目录当 NAS 跑冒烟；`.owner` 与自检单独测 | 2 天 |
| 3. k8s 清单与集群验证 | OrbStack k8s、hostPath PV、runtime × 2 + kb-import × 1 | 清单模板 + 验证脚本 | §12 第 1–6 项 | 3 天 |
| 4. 文档与发布 | 契约文档、手册章节、离线镜像包 | 交付包 | 从包装出副本再跑冒烟 | 1 天 |
| 5. 客户环境 | NFS 压测、客户签字、平台对接网关 | 压测报告 | §6.2 ⑥ | 客户侧 1–2 周 |
| 6. frontend 镜像与 CLI | deepseek-harness 前端改造为 frontend 镜像；`attach` / ttyd 接入说明 | frontend 镜像 + 清单 | 浏览器经 frontend 完整走一次登录、诊断、知识库查询 | 后端镜像做完再评估 |

第 5 步前需要客户提供：NAS 的 NFS 版本、`kubectl get storageclass` 输出。

gh_skill 的 `release/v12.9` 分支不动，现场热修走它。
