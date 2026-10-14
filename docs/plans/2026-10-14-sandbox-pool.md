# 池化沙箱 实施计划（v1.2.0）

**Goal:** 新增第二种部署形态「池化」：平台预建 16 个沙箱 Pod（主容器 opencode + 副容器 poolctl），登录时 bind、
退出或倒计时到点时 unbind 并完整写回 NAS，用完即换。现有按需模式（网关）行为不变。

**Spec:** `docs/specs/2026-10-13-sandbox-pool-design.md`（2026-10-14 评审修订版为准）

**分支:** `feat/sandbox-pool`（基于 `docs/sandbox-pool`），独立克隆 `~/gh_agent_k8s-pool`——主工作树 `~/gh_agent_k8s`
停在 main 上，有 `k8s/tools/diagnose-user.sh` 等未提交改动，不在那里开发。

## Global Constraints

- **state 卷在 bind 之后是不可信输入。** 副容器读写任何文件一律经 `docker/fdtree.py`（逐级 `O_NOFOLLOW`、`fstat` 确认普通文件与同一设备、
  临时文件 `O_EXCL` + 原子改名），**不按路径跟随、不用 SQLite 打开 state 卷**。NAS 本人目录同样按不可信处理（按需模式下主容器对它可写）。
- 库的一致性快照只由主容器守护进程生成（`.pool/snap/`）；副容器只搬快照。回执可被伪造，后果只落在本人数据上。
- 只用标准库（镜像里 Python 依赖白名单三项不变）；不用 ctypes。
- 现有模式：`statesync` 普通文件改走 `fdtree`，库仍走 online backup API；新增 M1（库没变不回写，`-wal` 非空不跳过）。现有单测全绿。
- 测试在 Mac 上跑：`cd ~/gh_agent_k8s-pool && PATH=$HOME/p2venv/bin:$PATH python3 -m pytest -q -p no:cacheprovider docker gateway scripts/k8s`；
  技能包 `agent/` 不改（基线 2491 过 / 1 条 live 失败要真库）。
- 本地集群 `--context orbstack`，命名空间 `gaussdb-agent`；池化 e2e 用一次性工号 `u9500`–`u9520`，**不碰 u9001–u9003 / u9100 / u9300 / u9400 / u9401**。
- 每个任务一次本地提交（中文 conventional commit），**不推送**，等 SqlRush 说推再推。

## 文件结构

| 文件 | 职责 | 任务 |
|---|---|---|
| `docker/fdtree.py`（新） | 按文件描述符的遍历、复制、删除、小文件读写 | 1 |
| `docker/statesync.py` | 普通文件改走 fdtree；M1 | 2 |
| `docker/poolsync.py`（新） | 池化布局：载入、回写、退出备份、非正常退出快照、库自检与恢复、dirty、`.owner` | 3 |
| `docker/poolrun.py`（新） | 主容器守护进程：读 ctl 起停 opencode 与 4097、中止对话、生成快照（含 M1）、写回执、崩溃重拉 | 4 |
| `docker/poolctl.py`（新） | 副容器：4098 控制接口、状态机、停止握手、定期回写、`.owner` 刷新、私有目录恢复、SIGTERM | 5 |
| `docker/podctl.py`、`docker/entrypoint.sh`、`docker/Dockerfile` | `pool-serve` / `pool-run` 子命令；`POOL_MODE=1` 分支；COPY 新模块 | 6 |
| `k8s/templates/sandbox.yaml`、`k8s/base/networkpolicy.yaml`、Secret 样例、`k8s/local/*`、清单守卫 | 模板与策略 | 7 |
| `scripts/k8s/pool-sim.py`（新） | 平台模拟器 / 参考实现 | 8 |
| `scripts/k8s/e2e-pool.sh`（新） | 本地集群端到端 | 9 |
| 接入手册、参数手册、部署手册、功能清单、仓库结构说明 | 池化接入一章 | 10 |

## 指令与回执（Task 4/5 共同约定）

- ctl（副容器写、主容器只读）：`run.json` = `{"state": "run"|"stop", "user_id", "password", "since"}`；`snap.json` = `{"seq": n}`。
- state 卷（主容器写）：`.pool/snap/<子目录>/<相对路径>` 各库快照；`.pool/ack.json` =
  `{"snap_seq", "changed": [...], "running": bool, "final": bool, "aborted_running": bool, "error": str, "ts"}`。
- 定期回写：副容器每 `STATE_SYNC_SECONDS`（60）发 `snap.json` 序号 +1，等 `ack.snap_seq` 追上（最多 20 秒），再搬。
- 停止：副容器写 `run.json` `state=stop`，等 `ack.final`（最多 40 秒）；主容器收到 SIGTERM 时同样走停止并写 `final`。

## 任务

- [ ] Task 1 `fdtree.py` + `docker/tests/test_fdtree_units.py`：链接、中间级目录链接、FIFO、检查后被替换（在两步之间插入替换）、
      跨设备拒绝、O_EXCL 临时文件、原子改名、保留纳秒修改时间、`prune` 不跟随、小文件读写不跟随。
- [ ] Task 2 `statesync.py` 改用 fdtree 复制普通文件；M1（`.state-sync.json` 记录、`-wal` 非空不跳过、目标缺失照拷）；现有测试全绿 + M1 测试。
- [ ] Task 3 `poolsync.py` + `test_poolsync_units.py`：载入（含库原样搬、dirty 时先做 `.unclean-*` 快照）、回写（排除库与 `.pool/`、
      搬快照、传播删除）、备份轮转、库自检（备份先按 fd 拷进 state 再查）、`.owner` 取得/刷新/释放（409 所需 holder 与 retry_after）、
      `users/` 不可写时说清属主；§9 的攻击用例全部在这里。
- [ ] Task 4 `poolrun.py` + `test_poolrun_units.py`：用假 opencode（可执行脚本，起 HTTP、写库）驱动：run → 就绪；snap 请求 → 快照与 M1；
      stop → 中止忙着的会话、停进程、最终快照、`final` 回执；opencode 崩溃重拉；SIGTERM。
- [ ] Task 5 `poolctl.py` + `test_poolctl_units.py`：状态机全路径、401/400/409（`state` / `owner_busy`）/500/504、幂等 bind、unbind 工号不一致、
      停止握手超时、定期回写、丢失独占即退役、副容器重启后恢复、healthz 不带用户信息、常量时间比较。
- [ ] Task 6 podctl 子命令、entrypoint `POOL_MODE=1` 分支、Dockerfile COPY、镜像内容守卫与 entrypoint 守卫。
- [ ] Task 7 `sandbox.yaml`、`sandbox-policy`（base + local）、`secret-sandbox-control.example.yaml`、共享 `grmp-token` 样例、清单守卫
      （不共享进程空间、主容器不挂 `users/`、两个容器的安全设置、emptyDir 有上限不是 tmpfs、enableServiceLinks false）。
- [ ] Task 8 `pool-sim.py` + 单测（渲染、补建数量规则、映射与倒计时、409 分支）。
- [ ] Task 9 构建 dev 镜像，`e2e-pool.sh` 跑 §14 的本地集群 e2e 1–8（9 载入曲线、10 清空复用视时间）。
- [ ] Task 10 文档。

## 执行记录

| 任务 | 提交 | 结果 |
|---|---|---|
