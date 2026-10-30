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
| kb-import Pod | 1 个，管理员共用 | 无状态，数据在 NAS `kb/`（知识库索引 `kb/index/store.sqlite` 由它写，runtime 只读） | 本仓库出镜像 |
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
| `从源码构建到K8s运行-完整手册.md` | 实施 / 开发 | 从 GitHub 拉代码、自己构建四个应用镜像（标准知识库镜像从镜像仓 / 离线包取）、推仓库、部署到浏览器里用起来，图文、每步带实测输出 |
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
- 2026-09-23 `agent-v1.1.0`–`v1.1.5`：大盘前端（第四个镜像 `gaussdb-agent-frontend`）与真数据全面测试的修复。
- 2026-10-16 `agent-v1.2.0`：大盘两种风格与直达链接、按时间段筛选；网关起不来时说清原因、知识库管理员入口、读 `agent-roles`；新工号首次登录不再因目录属主崩溃；api 模式库名核对与实例别名；WDR 按时间段定窗口；一键诊断 `runtime-doctor.sh`；池化沙箱；第 5 个镜像（向量库）。逐条见 `docs/版本说明.md`。
- 2026-10-19 `agent-v1.3.0`：**时间段**——「最近 N 分钟 / 某个时间段」的 Top SQL 与时段体检（说明实际评估的是哪一段、为什么）；**死锁日志解析**（用户贴日志段，解析出环、语句、牺牲者与改法）；新 skill `gaussdb-config`（参数体检）；健康检查补冻结水位、占用量、同步提交、WAL 槽、线程池、分区数；膨胀估算；WDR 修正 DB time 少报与内连接漏行、各维度元凶从宽集里找、溢出归到语句；空闲窗口与自相矛盾的比例不再判告警；大盘显示时段报告。白名单新增 13 条、更新 9 条。逐条见 `docs/版本说明.md`。
- 2026-10-26 `agent-v1.4.0`：**固定池化**（部署时固定 N 个 Pod × K 个槽位，粘性保留，回来 0–1 秒恢复，满员排队与提前转保留）；**凭据代理**（中间件令牌、SM2 私钥、模型 Key 不进模型所在的容器）；opencode 工具权限写进配置真正执行；**回答模板化**（各类回答固定版式、两种读者、知识引用上色并注明出处）；**知识库八类类型与 L0–L3 渐进式披露、官方文档集**；故障注入验证后的取数修正。白名单增量 12 更新 + 3 新增（共 126 条）。gh_skill 同步 `skills-v12.19`。
- 2026-10-27 `agent-v1.4.1`：**内网网页导入知识库（网页剪藏）**——需要登录的 Wiki / Confluence / 门户页面，知识库管理员在已登录的浏览器里点书签「导入知识库」，剪藏页交给本人导入容器，模型按现有流程整理（工单六要素、规范条款化、八类模板），原网页挂成 L3 证据；第一次由模型一步一步带着装书签，书签用不了时另存 .mhtml 拖进剪藏页；**扫描件 PDF 看图转录**；**PPT（.pptx）导入**。白名单不变。gh_skill 同步 `skills-v12.20`。
- 2026-10-27 `agent-v1.4.2`：**GaussDB 官方文档集随部署自动装上**——作为标准知识库发布在第五个镜像 `gaussdb-agent-kbstd` 里，`kubectl apply` 后一次性 Job 装进知识库（逐文件核对、同版本跳过、不碰客户知识、不要网络）；离线包变为五个镜像。白名单不变。gh_skill 同步 `skills-v12.21`。
- 2026-10-30 `agent-v1.4.3`：**存储过程源码的 ADM_SOURCE 兜底**——开了对象隔离的库，存储过程诊断与调优在 pg_proc 取不到源码时改查 ADM_SOURCE（包内过程从包体里切出，报告标出出处与包体行号）；取不到时报告写明原因，没有读权限给出授权语句（由客户 DBA 决定）。白名单新增 2 条，共 128 条。
- 2026-10-23 `agent-v1.3.2`：**慢 SQL / Top SQL 排除 GaussDB 自己的系统 SQL**（慢 SQL、Top SQL、健康检查、WDR 都写明排除了几条，`--include-system` 可看全）；**知识库向量库改为 sqlite-vec**（NAS 上一个索引文件，vectordb 组件下线，离线包变为四个镜像）；**知识库导入能看照片**（`MODEL_INPUT_MODALITIES` 声明图片输入，HEIC / TIFF 自动转 jpg）。白名单不变。gh_skill 同步 `skills-v12.18`。逐条见 `docs/版本说明.md`。
- 2026-10-22 `agent-v1.3.1`：**时段锁回溯**（从活跃会话采样事后还原某段时间谁挡了谁、阻塞链的根）；**历史真实执行计划**（文本带占位符时给当时真实执行的计划，WDR 附录直接附上）；sqltune 支持搜索路径串；大盘深挖首句窗口带秒；部署手册写明事后分析依赖的保留期参数。白名单新增 5 条。gh_skill 同步 `skills-v12.17`。逐条见 `docs/版本说明.md`。
- 剩下的在客户侧：NFS 压测 5 天、客户确认、网关（阶段 5）、**签名口径逐项确认**（交付手册 §7）；frontend 改造看客户要求。路线图见 `docs/plans/2026-09-17-roadmap.md`。

## 安全

本仓库公开。不放客户名称、地址、令牌、凭据；Secret 与 ConfigMap 只有样例。
