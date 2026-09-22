# gh_agent_k8s

把 opencode + [gh_skill](https://github.com/sqlrush/gh_skill) 的 GaussDB/openGauss DBA 技能集放进 Kubernetes：每个用户一个 Agent Pod，Pod 无状态，状态全部在 NAS 上；知识库导入与诊断运行分成两个镜像。

## 与 gh_skill 的关系（开发规范）

- **gh_skill**：技能集本身，继续服务**非 Pod 的 opencode 独立部署**。只收与部署方式无关的技能功能修复。
- **本仓库 `agent/`**：从 gh_skill 标签（`agent/UPSTREAM` 记录）复制来的技能代码，加上 Pod 适配改动。镜像从这里构建。
- 规则：Pod 架构相关的适配代码只放本仓库；skill 本身的功能改动两个仓库同时改。首次导入用 `scripts/vendor-from-gh-skill.sh`，之后靠 cherry-pick 同步，不整目录覆盖。

## 设计

[docs/specs/2026-09-15-k8s-containerization-design.md](docs/specs/2026-09-15-k8s-containerization-design.md)

要点：

| 项 | 决定 |
|---|---|
| 状态 | 全部在 NAS（RWX PVC），Pod 用 `subPath` 只挂自己那一层 |
| SQLite | 在 runtime Pod 内运行，`opencode.db` 直接位于 NAS 挂载路径；NFSv4.1 + `local_lock=all` + 单 Pod 独占 + 优雅停机 + 启动自检 + 退出备份 |
| 镜像 | `gaussdb-agent-runtime`（诊断 skill + 知识库查询）、`gaussdb-agent-kb-import`（知识库导入）、`gaussdb-agent-frontend`（deepseek-harness 前端改造，无状态） |
| 接入 | 后端 Pod 内只跑 `opencode serve`；Web 走 frontend；CLI 用 `opencode attach`；frontend 在后端镜像完成后做 |
| 网关 | 平台按工号建 Pod、回收 Pod；本仓库提供 Pod 模板与环境变量契约 |

## 组件

| 组件 | 数量 | 状态 | 谁做 |
|---|---|---|---|
| 接入网关 Pod | 1 个 Deployment，2 副本 | 无状态 | 平台（或本仓库出 `gaussdb-agent-gateway`） |
| frontend Pod | 1 个 Deployment | 无状态 | 本仓库，第二版 |
| runtime Pod | 每人 1 个 | 无状态，数据在 NAS `users/<工号>/` | 本仓库出镜像，网关创建 |
| kb-import Pod | 1 个，管理员共用 | 无状态，数据在 NAS `kb/` | 本仓库出镜像 |
| NAS | 1 个 RWX PVC | 唯一的状态所在 | 平台提供 |

用户的历史会话不需要「还原」：新 Pod 挂上 `users/<工号>/`，opencode 打开里面的 `opencode.db`，历史自动出现。

## 目录（规划）

```
agent/       技能代码(common/ skills/ scripts/ tools/ tests/ …),来自 gh_skill + Pod 适配改动;UPSTREAM 记录来源
docker/      Dockerfile(base → runtime / kb-import)、entrypoint.sh、podctl.py(+tests)、opencode.jsonc.tmpl
k8s/         base/(命名空间、NAS PVC、ConfigMap、角色表、NetworkPolicy) templates/(按工号渲染的 runtime / kb-import) local/(Mac 专用覆盖)
scripts/     vendor-from-gh-skill.sh、build-images.sh、smoke-image.sh(+tcp-forward.py)、k8s/provision.py(网关建 Pod 的参考实现)、k8s/verify-cluster.sh
docs/        交付文档(见下) + 内部件:specs/ 设计、plans/ 计划、security/ 扫描报告、env-contract.md 环境契约
gateway/     接入网关(认工号、按工号建/收容器、反向代理),第四个镜像
```

## 交付文档

`docs/` 下这几份会随交付包发出去（`specs/`、`plans/`、`security/` 不发）：

| 文档 | 给谁 | 内容 |
|---|---|---|
| `快速搭建-K8s.md` | 实施 | 最简路径，每条命令带预期输出 |
| `单机演示-不用K8s.md` | 售前 / 试用 | 一台 Docker 机器跑起来，含状态持久化与异常终止的演示脚本 |
| `命令卡-测试环境搭建.md` | 实施 | 逐条操作 + 排查，不要求懂 K8s |
| `部署手册-从零到上线.md` | 实施 / 运维 | 十个阶段，从拉镜像到上线检查表 |
| `接入手册-SSO与K8s.md` | 架构 / 安全 | 架构、隔离三层、生命周期、待确认事项 |
| `对接清单-各方要做什么.md` | 各团队 | 按团队分节的接口契约与验收，可单独转发 |
| `参数手册.md` | 运维 | 全部参数、默认值、下限、配错的后果 |
| `功能清单-容器版.md` | 决策 | 相对非容器版新增的能力，与三条如实说明 |
| `仓库结构说明.md` | 开发 / 接手 | 每个目录与重点文件的作用，附「我要改的东西在哪」对照表 |

## 构建与冒烟（Mac + OrbStack）

```bash
scripts/build-images.sh dev                       # 本机架构;--platform linux/amd64,linux/arm64 交叉
PATH=$HOME/p2venv/bin:$PATH python3 -m pytest docker/tests -q   # podctl 单测
bash scripts/smoke-image.sh dev                   # 12 项冒烟,需要 ~/kf-verify 的 8779 mock 与 ~/.gdaa/grmp.env
```

## 状态

- 2026-09-17 阶段 1 完成（`agent-v0.1`）：技能侧 Pod 适配，gh_skill 同步发布 `skills-v12.10`。
- 2026-09-17 阶段 2 完成（`agent-v0.2`）：`gaussdb-agent-runtime` / `gaussdb-agent-kb-import` 两个镜像（x86_64 + aarch64）可构建，13 项冒烟通过。
- 2026-09-17 阶段 3 完成（`agent-v0.3`）：k8s 清单、`provision.py`、OrbStack 集群 13 项验证通过（含 NetworkPolicy 生效）。
- 2026-09-17 Web 路径端到端通过（`agent-v0.3.1`）：`opencode serve` 自带的 Web 界面经基本认证可用，API 建会话 → 模型经中间件诊断 → 导入引导 → 会话落 NAS；顺带修了只读根文件系统下配置加载失败的问题。
- 2026-09-17 `agent-v0.3.2`：工作目录首次启动 git 初始化，Web 界面按项目路由落地（无头浏览器截图验证）。
- 2026-09-17 阶段 4 完成（`agent-v0.4`）：`scripts/package-images.sh` 出离线交付包（两架构镜像 tar.gz + sha256 + k8s 清单与文档），从包里装回镜像再跑冒烟 14/14；`docs/delivery-容器化交付手册.md`（交付物、平台必做、SQLite/NAS 客户确认表、压测方案、冒烟、回滚）。
- 2026-09-18 `agent-v0.3.3`：CLI `opencode attach` 与管理员经模型导入两条路径端到端验证；`agent-v0.4.1`：知识库文件模式检索缺口修复（纯中文现象提问命中案例；gh_skill 同步 `skills-v12.11`）、网关文件上传契约定稿、镜像 trivy 扫描与加固（`docs/security/trivy-scan.md`）。
- 2026-09-18 `agent-v0.5`：客户中间件加固——GRMP 请求头带 Appkey / Timestamp / Signature（纯 Python SM2/SM3，旋钮全可配，gh_skill 同步 `skills-v12.12`）；私钥走共享 Secret `grmp-sm2`；`k8s/overlays/{test,prod}` 样例（同一镜像只换 ConfigMap）；签名 e2e 10/10 含场景矩阵 60/60。
- 2026-09-18 `agent-v0.6`：新 skill `gaussdb-kb-init`（入库前标准化：word / pdf / txt / 照片 / 表格 → 六要素统一 md，再交 kb-import 入库；照片由模型看图，不做 OCR），**只在 kb-import 镜像里**；抽文本逻辑搬到 `common/kb/extract.py` 共用；gh_skill 同步 `skills-v12.13`。
- 2026-09-18 `agent-v0.6.1`：标准化→导入的交接（案例出处指回客户原件、已定字段核对）。
- 2026-09-19 `agent-v0.7`：SM2 签名换雅可比坐标 + 固定基点预计算 + 缓存公钥/ZA，单次 10.3 ms → 0.72 ms（**14x**，纯 Python 零依赖）；gh_skill 同步 `skills-v12.15`。
- 剩下的在客户侧：NFS 压测 5 天、客户确认、网关（阶段 5）、**签名口径逐项确认**（交付手册 §7）；frontend 改造看客户要求。路线图见 `docs/plans/2026-09-17-roadmap.md`。

## 安全

本仓库公开。不放客户名称、地址、令牌、凭据；Secret 与 ConfigMap 只有样例。
