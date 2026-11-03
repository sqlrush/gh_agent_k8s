# 发版台实施计划(2026-11-03)

设计见 `docs/specs/2026-11-03-release-console-design.md`。每项先写测试再写实现;每项完成跑 `release/tests` + 受影响目录全量。
分支 `feat/release-console`。

| # | 任务 | 测试 | 状态 |
|---|---|---|---|
| 0 | 网关 `/admin/pool` 摘要带 busy / busy_at;池子安静重启的容器 ID 缺陷 | 网关单测;管家单测 + 测试环境重放 | 完成 |
| 1 | 镜像入库:`release/oci.py` 读 OCI 目录、`release/registry.py` 推 v2(Basic / Bearer、跨仓库挂载、回读 digest) | 假仓库单测;真包(v1.4.3 arm64)推本地 registry:2 + docker 拉回 | 完成 |
| 2 | release.yaml:`k8s/release/changes.yaml`(v1.4.0 起逐版本变化)+ 同步 `docs/delivery/`(scripts.sql)+ 白名单解析(`release/whitelist.py`,含核对);`package-images.sh` 末尾 `release/pack.py` 合成 release.yaml(含各镜像平台清单 digest、白名单全量) | 合成 / 校验单测;changes.yaml 首版本与清单一致、增量文件存在的守卫;md5 对上增量文件自检 12 条;真包合成(Python 3.9,1 秒)digest 与推送回读一致;mock 中间件库 128 条全部一致 | 完成 |
| 3 | 发布包目录 `release/package.py`:发现包(子目录或平铺)、核 sha256(与 release.yaml 交叉)、digest 与 OCI 一致;旧包(无 release.yaml)可作起点 / 回滚目标 | 单测(假包、旧包) | 完成 |
| 4 | 状态库 `release/store.py`(SQLite):资源配置版本、密钥指纹、发布 run / step / 日志、操作记录;脱敏;重启后中断与关卡保留 | 单测(含落盘不含集群密钥) | 完成 |
| 5 | `release/kube.py`:kubectl 子进程封装(get / apply / diff / patch / exec / port-forward / auth can-i);密钥只走 stdin | 假 kubectl 单测;真集群读、can-i、port-forward /healthz | 完成 |
| 6 | 资源扫描 `release/scan.py`(9 项) | 单测(假集群 / 假仓库 / 假模型) | |
| 7 | 升级计划 `release/plan.py`:识别当前版本、累计 history、首次部署 / 升级 / 回滚 / 接着发、池子替换方式 | 单测(各起点版本);真集群识别 v1.4.3 13 处一致 | 完成 |
| 8 | 配置迁移与渲染 `release/render.py`:ConfigMap 保留现值 + 新增项、镜像前缀与标签、网络策略外部规则沿用集群的、render-pool(首次)/ 模板换镜像(升级);diff | 单测(真 k8s/base + 仿本地覆盖的集群);真集群同版本空跑 diff 只剩环境本身的偏离 | 完成 |
| 9 | 白名单核对:只读账号直连(psycopg2)接到 `whitelist.from_rows`;核对步骤与关卡 | 单测(假游标) | 解析与比对已在第 2 项完成 |
| 10 | 池子替换 `release/pool.py`:实验记录里的算法 + 按 Pod 整体替换;网关 /admin/pool 经 kubectl exec(密钥不出容器);旧网关无 busy 时退到 last_seen | 单测(假池子按实验行为:忙、超时交运维、写回不干净、核验不过、换管家、整体替换);真集群空闲槽位来回换各约 3 秒 | 完成 |
| 11 | 执行引擎 `release/engine.py`(关卡持久化、暂停、失败续跑、取消、重启后接着等原关卡)+ 各步骤 `release/steps.py` | 引擎单测(连跑 5 遍稳定) | 引擎完成;步骤进行中 |
| 12 | 冒烟 `release/smoke.py`:登录 403/200(用 GET /session,/global/health 是非交互路径不分配槽位)、真实对话、报告、知识库 | 单测(假网关);真环境 u9500 + og5 全过,34 秒 | 完成 |
| 13 | 接口 `release/server.py` + 前端 `release/web/`(R1 原型接接口,按设计 §7 末尾更正) | 接口单测;无头浏览器截图 | |
| 14 | 镜像 `docker/Dockerfile.release`、`scripts/build-images.sh` 加 release、启动说明 | 构建 + 起容器 | |
| 15 | 端到端(OrbStack):本地 registry:2;新命名空间从零部署;现有环境 v1.4.3 → v1.4.4(u9500 保持会话);回滚一次 | 真跑记录 | |

**已知风险**:完整构建 runtime 镜像要从 models.dev 下载模型清单,测试机网络连不上(11-03 实测)→ 出 v1.4.4 完整离线包前要解决(代理或预置文件);
OrbStack 的 k8s 能否从 `127.0.0.1:5005` 拉镜像待验证;测试机同时跑别的负载时 k8s API 会超时。
