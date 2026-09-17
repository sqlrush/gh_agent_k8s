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
k8s/         Deployment / PVC / NetworkPolicy / Secret、ConfigMap 样例(阶段 3)
scripts/     vendor-from-gh-skill.sh(首次导入)、build-images.sh、smoke-image.sh(+tcp-forward.py)
docs/        specs/ 设计、plans/ 计划、env-contract.md 环境契约
```

## 构建与冒烟（Mac + OrbStack）

```bash
scripts/build-images.sh dev                       # 本机架构;--platform linux/amd64,linux/arm64 交叉
PATH=$HOME/p2venv/bin:$PATH python3 -m pytest docker/tests -q   # podctl 单测
bash scripts/smoke-image.sh dev                   # 12 项冒烟,需要 ~/kf-verify 的 8779 mock 与 ~/.gdaa/grmp.env
```

## 状态

- 2026-09-17 阶段 1 完成（`agent-v0.1`）：技能侧 Pod 适配，gh_skill 同步发布 `skills-v12.10`。
- 2026-09-17 阶段 2 完成（`agent-v0.2`）：`gaussdb-agent-runtime` / `gaussdb-agent-kb-import` 两个镜像（x86_64 + aarch64）可构建，13 项冒烟通过。
- 下一步阶段 3：k8s 清单与 OrbStack 集群验证。路线图见 `docs/plans/2026-09-17-roadmap.md`。

## 安全

本仓库公开。不放客户名称、地址、令牌、凭据；Secret 与 ConfigMap 只有样例。
