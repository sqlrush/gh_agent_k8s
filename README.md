# gh_agent_k8s

把 opencode + [gh_skill](https://github.com/sqlrush/gh_skill) 的 GaussDB/openGauss DBA 技能集放进 Kubernetes：每个用户一个 Agent Pod，Pod 无状态，状态全部在 NAS 上；知识库导入与诊断运行分成两个镜像。

## 与 gh_skill 的关系

- **gh_skill**：技能集本身（17 个 `gaussdb-*` skill、`common/`、白名单脚本）。按标签发布（`skills-v12.9` 起）。
- **本仓库**：Dockerfile、entrypoint、k8s 清单、环境变量契约、验证脚本、交付文档。镜像构建时按标签拉取 gh_skill，不复制技能代码。
- 容器化需要的技能侧改动（gaussdb-kb 拆分、`GSDB_HOME` 守卫、会话不存中间件地址等）在 gh_skill 仓库完成并发布为新标签，本仓库只引用。

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

## 目录（规划）

```
docker/      Dockerfile、entrypoint.sh、opencode.jsonc 模板
k8s/         Deployment / PVC / NetworkPolicy / Secret、ConfigMap 样例
scripts/     镜像冒烟、集群验证脚本
docs/        设计、环境变量契约、交付文档
```

## 状态

2026-09-16：设计已定稿，待实施。实施顺序见设计文档 §15。

## 安全

本仓库公开。不放客户名称、地址、令牌、凭据；Secret 与 ConfigMap 只有样例。
