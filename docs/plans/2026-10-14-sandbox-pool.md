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

- [x] Task 1 `fdtree.py` + `docker/tests/test_fdtree_units.py`：链接、中间级目录链接、FIFO、检查后被替换（在两步之间插入替换）、
      跨设备拒绝、O_EXCL 临时文件、原子改名、保留纳秒修改时间、`prune` 不跟随、小文件读写不跟随。
- [x] Task 2 `statesync.py` 改用 fdtree 复制普通文件；M1（`.state-sync.json` 记录、`-wal` 非空不跳过、目标缺失照拷）；现有测试全绿 + M1 测试。
- [x] Task 3 `poolsync.py` + `test_poolsync_units.py`：载入（含库原样搬、dirty 时先做 `.unclean-*` 快照）、回写（排除库与 `.pool/`、
      搬快照、传播删除）、备份轮转、库自检（备份先按 fd 拷进 state 再查）、`.owner` 取得/刷新/释放（409 所需 holder 与 retry_after）、
      `users/` 不可写时说清属主；§9 的攻击用例全部在这里。
- [x] Task 4 `poolrun.py` + `test_poolrun_units.py`：用假 opencode（可执行脚本，起 HTTP、写库）驱动：run → 就绪；snap 请求 → 快照与 M1；
      stop → 中止忙着的会话、停进程、最终快照、`final` 回执；opencode 崩溃重拉；SIGTERM。
- [x] Task 5 `poolctl.py` + `test_poolctl_units.py`：状态机全路径、401/400/409（`state` / `owner_busy`）/500/504、幂等 bind、unbind 工号不一致、
      停止握手超时、定期回写、丢失独占即退役、副容器重启后恢复、healthz 不带用户信息、常量时间比较。
- [x] Task 6 podctl 子命令、entrypoint `POOL_MODE=1` 分支、Dockerfile COPY、镜像内容守卫与 entrypoint 守卫。
- [x] Task 7 `sandbox.yaml`、`sandbox-policy`（base + local）、`secret-sandbox-control.example.yaml`、共享 `grmp-token` 样例、清单守卫
      （不共享进程空间、主容器不挂 `users/`、两个容器的安全设置、emptyDir 有上限不是 tmpfs、enableServiceLinks false）。
- [x] Task 8 `pool-sim.py` + 单测（渲染、补建数量规则、映射与倒计时、409 分支）。
- [x] Task 9 构建 dev 镜像，`e2e-pool.sh` 跑 §14 的本地集群 e2e 1–8（9 载入曲线、10 清空复用视时间）。
- [x] Task 10 文档。

## 执行记录

| 任务 | 提交 | 结果 |
|---|---|---|
| 设计评审修订 | `4ef0946` | 7 处补进方案;Q7 按能用双容器模板、Q8 继续池化 |
| 1 fdtree | `a717af4` | 19 条;**macOS 全绿、Linux 红 9 条**:overlayfs 上对旧目录描述符第一次 scandir 返回空——改为列目录一律用现开的描述符;新增 `scripts/linux-pytest.sh` |
| 2 statesync | `c3e154e` | M1 四条 + 链接一条;「库一律复制」那条测试按 M1 改写;镜像守卫扩到 import 传递闭包(删掉 fdtree 的 COPY 会红,已验) |
| 3 poolsync | `b341525` | §9 全部攻击用例 |
| 4 poolrun | `3809991` | 假 opencode 驱动真进程 11 条 |
| 5 poolctl | `9d80ae6` | 28 条;**写测试时抓到 poolsync 会丢历史的缺陷**:库只搬快照、保留集合里却没有活着的库,主容器还没给快照的那一轮删除传播会删掉 NAS 上的 opencode.db(拿掉修复测试会红,已验) |
| 6 镜像接线 | `4fa9247` | 没有控制密钥时副容器拒绝启动(退出码 2) |
| 7 清单 | `1c7d3ac` | 结构化守卫第一轮就抓到模板注释里的 `$HOME` 会让 string.Template 渲染失败;渲染后服务端 dry-run 通过 |
| 8 pool-sim | `8a7fdd7` | 14 条(含后加的) |
| 9 e2e | 见下 | 三轮:37/3 → 39/1 → **41/0** |
| 10 文档 | 见下 | 新增《池化接入手册》;参数手册、功能清单、仓库结构说明、接入手册、部署手册 |

**e2e 三轮抓到的(都已修,有回归测试)**

1. **收尾中途抛异常,沙箱卡在「解绑中」**:本人 NAS 目录在收尾前被删,退出备份抛 FileNotFoundError,处理线程死掉——
   平台收到断开的连接,独占一直攥着。现在每一步单独兜住,必定走到退役并回 500。
2. **解绑中收到 SIGTERM 直接退出**:`on_sigterm` 只等「绑定中」。平台在 unbind 没回应时删 Pod 正好是这种情况。
3. **模拟器一次断连接就整个中断**:`RemoteDisconnected` 没接住,`down` 中断、沙箱残留在集群;`down` 还先让人退出、后清零目标数,
   退出时的补建又建出新沙箱。
4. **日志说假话**:上次绑定后一次回写都没来得及就被强杀时,NAS 上没有可快照的内容,日志却写「已先快照到 .unclean-…」。
5. 退出备份与 NAS 上的库是 0644(快照按 umask 落盘),按需模式是 0600;响应里 `sandbox` 返回的是编号而不是 Pod 名。
6. e2e 自身:`--force --grace-period=0` 模拟不出强杀(容器仍收到停止信号,副容器一两秒内收尾干净——方案 §6.3 据此修正);
   改用 SIGSTOP 冻住副容器再强删。

**第三轮实测(OrbStack k8s v1.35.6,镜像在节点上,池子 16)**:16 个就绪 7 秒;16 人登录 37 秒(每次两回 port-forward);
第 17 人现建 + 绑定 5 秒;跨沙箱接续含 DeepSeek 真模型两轮(新沙箱里记得上一句);埋的三条链接(别人的库、全部用户目录、
/proc/self/environ)一条都没上 NAS;强杀后 owner_busy 等 58 秒过期,再绑报 unclean_last_time,快照里有被杀前回写过的文件。
日志 Mac `~/kf-verify/e2e-pool-20261014-1458.log`。

**没做 / 没验的**

- 网关形态的全套 e2e 回归(方案 §14 第 8 条):本地测试床已失效(11 天前起网关与前端 ImagePullBackOff、本机没有任何
  gaussdb-agent 镜像、hostPath NAS 目录已不存在),本轮没去修它。网关形态这次只改了 statesync(普通文件改走 fdtree、M1),
  由单测覆盖;上线前要在修好的测试床上跑一遍 `e2e-dash-full.sh` 与场景矩阵。
- §14 第 7 条(工号随 GRMP 请求发出、签名 mock 校验):本轮没起 GRMP mock。只验了 4097 `/whoami.json` 回绑定的工号
  ——技能读的是同一个 `GSDB_USER_ID`。
- §14 第 9 条(50/200/500 MB 历史的载入曲线)、第 10 条(清空复用):未做。
- M2(老会话归档):默认关闭的功能,等 Q9 定天数,本轮没写。
