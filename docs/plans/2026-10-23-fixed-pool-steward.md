# 固定池化 P2–P3：槽位守护与管家（2026-10-23）

代码：`docker/slotrun.py`（槽位 1 号进程）、`docker/poolslot.py`（管家一侧的单个槽位）、`docker/poolsteward.py`（管家进程与控制接口）。
这里记实施时在计划之外定下的几件事，部署 / 参数手册（P8）以此为准。

## 槽位状态

| 状态 | 含义 | 离开 |
|---|---|---|
| booting | 管家还没和这个槽位握过手（新 Pod） | 握手 + 盘是空的 → free；不空 → evicting |
| free | 只有槽位 1 号进程，盘是空的，端口由它占着 | bind |
| binding | 取了独占、换了版本令牌，正在载入 / 起 opencode | 就绪 → active；载入失败 → evicting；启动失败 → retained |
| active | 这个人的 opencode 在跑，每 60 秒回写 | retain |
| retaining | 停 opencode、最终快照、完整写回 | → retained（干净：释放独占；不干净：继续持有独占，dirty） |
| retained | 文件留在槽位里，opencode 已停、后台进程已清 | resume（版本令牌与主库指纹都对得上）；evict（不 dirty 才行） |
| resuming | 重新取独占、核对、起 opencode | 就绪 → active；对不上 → retained（stale，等腾挪） |
| evicting | 已发 exit，等容器重启 | verify 通过 → free；超时（360 秒）→ disabled |
| disabled | 告警，不再分配 | verify 通过 → free（迟到的重启也能自动恢复） |

## 信任从哪里来

槽位里的模型与槽位 1 号进程同 uid，回执、boot.json、hello 应答它都能改。所以：

- 管家发给槽位的每条指令都带 **rec.boot**；它只在「刚由可信的重启产生」时更新：新 Pod 第一次握手（还没有人用过），
  或腾挪核验通过（网关拿 k8s 的 containerID 证明容器确实换过）。新容器的 1 号进程不认旧 boot 的指令。
- 回执里出现别的 boot：**安静**的槽位（空闲，或已确认停下的保留——里面没有模型进程）只可能是真重启，核对清盘后接受；
  其余一律释放独占、按 rec.boot 发 exit、进清理中，等网关核验。模型伪造回执只会把自己的会话断掉。
- 腾挪完成的根是网关给的 containerID（签名请求），不是槽位写的任何文件。

## 写回与独占

- 写回不干净（NAS 写不进、槽位没确认停下）的保留槽位**继续持有独占**：这个人到别处登录会被挡回这里，用的是最新的文件；
  也不许腾挪（409 not_synced）。后台每个同步周期重试，补上了才释放。
- 载入到一半（还没起过 opencode）就中断的槽位**绝不回写**：回写带删除传播，会把 NAS 上还没载入的部分删掉。释放独占、腾挪。
- 独占被别处接管（管家停顿超过 90 秒）：停下、不再回写，这份副本标 stale。

## 跨 uid 文件

- 管家载入的文件属主是 uid 1000（0660 / 2770）。技能每次运行都 chmod 0700 自己的会话目录，属主不是槽位就 EPERM，
  所以槽位在起 opencode 之前把它们**复制成自己的**（`fdtree.adopt`；库文件只需组读写，不复制）。
- 槽位每次出快照、最终停止之前给自己的文件加组读（`fdtree.grant_group`），管家才读得到。
- 管家 umask 007：库自检时 SQLite 产生的侧车文件要让槽位读写得到。

## 控制接口

- 请求与应答都签名（`poolauth`，两份一字不差）；GRMP 令牌在请求体里是密文（`poolauth.seal`）。
- 管家重启的空档，同 Pod 的槽位可以抢 4098：签名让它伪造不了应答，密文让它拿不到令牌；管家起来时按 `/proc/net/tcp`
  认出占端口的槽位 uid，直接让那个槽位腾挪（按它可信的 boot 发 exit），再绑端口。
- bind / resume 带 `expect_in_use`（Pod 级 CAS）与 `container_id`（租期记录，恢复时对不上回 409 restarted）。
- 同一个人再 bind 一次（已在用）幂等地返回凭证：网关重启后靠它取回口令与报告令牌；status 里没有任何秘密。

## 残余风险（写进交付文档）

- opencode 崩溃后重启的约 2.5 秒里端口是空的（opencode 不能接管我们占着的套接字）。同 Pod 的恶意槽位抢到时，
  管家秒级发现（外来监听者，定位到肇事槽位），网关停止转发；在那之前本人的下一个请求可能落到冒充者手里。
- 同 Pod 的槽位共用内核与网络命名空间，隔离强度低于「不同 Pod」；抽象 Unix 套接字没有权限控制（opencode 与技能都不使用）。

## 清单（P4，`k8s/tools/render-pool.py`）

- 渲染而不是 kustomize：容器数 = 槽位数，每个槽位有自己的 uid、端口与 5 块盘（data / state / ctl / tmp / shm），kustomize 生成不了。
  只用标准库，输出确定；守卫 `gateway/tests/test_pool_manifest_units.py` 按结构逐条核对。
- StatefulSet `OnDelete` + `Parallel`，headless Service `publishNotReadyAddresses`，PDB 最多 1 个不可用；
  Pod 级 seccomp RuntimeDefault、不挂 ServiceAccount 令牌；槽位环境里没有任何 GRMP 令牌与控制密钥。
- `--test-override` 只给测试集群缩短时长，渲染结果带标签 `gaussdb-agent/test-overrides`，正式清单里不会有。
- 占位监听回 `503 {"reason": "slot_not_running"}` 再正常关闭：最初用 RST 关连接，测试集群上 `kubectl port-forward`
  遇到 RST 整条断掉；改成先读掉请求再回 503（带着没读的数据关闭，内核照样发 RST）。

## 测试集群核对（2026-10-23，OrbStack，3 个 Pod × 3 个槽位）

| 项 | 结果 |
|---|---|
| 部署 | 3 个 Pod 7 秒内 4/4 Running，9 个槽位握手后全部空闲 |
| 槽位内（slot-1） | uid 2001 / gid 1000；没有 /pool；/nas 下只有 kb；只看得到自己的进程；环境里没有密钥；管家接口 401；抢 slot-0 端口 EADDRINUSE；指令目录与根文件系统只读 |
| 绑定 u9501 | 200，载入 27 个文件、1 个库，自检 ok；本人对话 / 报告端口带凭证 200；slot-1 连 slot-0 的对话与报告都 401；opencode 拿到按人 GRMP 令牌；NAS 独占标记 `agent-pool-0/0`；管家看到的监听者全部 ok |
| 转保留 → 恢复 | 写回干净、独占释放、旧口令打到占位监听（503）；恢复 3.3 秒（含命令行与端口转发），新口令 200、旧口令 401 |
| 腾挪 → 核验 | 4 秒内只重启 slot-0（其他槽位 restartCount 不变）；旧 containerID 核验 409 not_restarted，新的 200 → 空闲；盘上只剩 boot.json 与 ack.json |
