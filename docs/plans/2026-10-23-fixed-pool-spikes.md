# 固定池化 P0 摸底结果（2026-10-23，Mac OrbStack，镜像 agent-v1.3.2-oc1.18.27）

实验在单独的命名空间 `pool-spike` 里做，做完已删。结论直接影响实施，按「改了什么」写在每条后面。

## S2 容器重启：耗时与退避

一个槽位容器的 PID 1 主动退出，kubelet 重启它（restartPolicy Always，退出码 0 也算）：

| 第几次（10 分钟内连续） | 从下指令到新容器 running |
|---|---|
| 1 | 1.8 秒 |
| 2 | 14.5 秒（CrashLoopBackOff 等 10 秒） |
| 3 | 24.6 秒（等 20 秒） |

- restartCount 与 containerID 在新容器 running 时同步出现在 Pod 状态里，读到的不会比实际晚。
- **任一容器重启期间整个 Pod 的 Ready 变 False** —— 用 headless Service 的 DNS 会让同 Pod 其他人的新连接失败。
- → 网关直连 Pod IP；腾挪优先挑上次重启已超过 10 分钟的槽位；核验超时按 360 秒（退避上限 5 分钟）算。

## S7 重启后的残留

`/dev/shm`（Memory emptyDir）、`/data`、`/tmp` 里上一次写的文件，容器重启后**全都还在**。

- → 槽位新起的 PID 1 先清盘（data / state / tmp / shm），再拷配置、再渲染、再启动；HOME 与配置目录都在清盘范围内。
- `python3 -E -s /opt/agent/podctl.py …` 能正常渲染配置（-E -s 不影响我们的导入方式）。

## S3 端口抢占

同一 Pod 两个容器（uid 2000 / 2001），端口 4100：

- a 在通配地址监听时，b 绑 `0.0.0.0`、`127.0.0.1`、`::`、加 `SO_REUSEPORT` **全部失败**（EADDRINUSE）。
- a 停止监听的空档，b **能抢到**；管家连 `127.0.0.1:4100` 探活会成功 —— 被冒充而不自知。
- 管家读 `/proc/net/tcp` 能看到监听者的 uid（2000 / 2001），能区分。
- → 槽位不跑 opencode 时由 poolrun 自己占住端口；管家按 uid 核对监听者，外来监听即停止路由、告警、腾挪肇事槽位。

## S1 跨 uid 文件（槽位 uid 2000、管家 uid 1000、同组 1000、fsGroup 1000）

直接拿 v1.2.0 池化代码跑：

- **绑定超时（504）**：管家写的 `run.json` 是 0600，槽位读不到；载入的目录是 2700、属主 1000，槽位进不去。
  → ctl 文件 0640、载入目录 2770 / 文件 0660（fdtree 加显式 mode，不继承 NAS 上的 0600 / 0700）。
- 目录改成组共享后：opencode 以 uid 2000 跑**正常**，能读写属主 1000、0660 的 `opencode.db`（读到已有会话、能新建会话，无权限报错）；
  umask 007 下它写出的文件是 0660 / 2770，管家都读得到。
- opencode 自己会建 0700 的锁目录（`xdg-state/opencode/locks/*.lock`），管家进不去；是临时的。
- 技能代码（`common/config.ensure_dir`、`common/session.py`）每次运行都 `chmod 0700` GSDB_HOME 与会话目录：
  目录属主不是槽位就 EPERM（技能直接跑不起来）；是槽位的话，chmod 后管家又读不到。
- → 顶层目录（xdg-data、xdg-state、workspace、gdaa、reports）由槽位自己建；每次写回前（定期快照与最终停止时）由 poolrun 把权限规整回组可读；写回跳过锁目录。

## S4 载入与 opencode 启停

| 历史大小 | 载入（fd 逐级复制，本机盘） | opencode 启动到就绪 | 停止 |
|---|---|---|---|
| 2 MB | 0.00 秒 | 2.5 秒 | 0.02 秒 |
| 100 MB | 0.04 秒 | 2.5 秒 | 0.01 秒 |
| 500 MB | 4.7 秒 | 2.5 秒 | 0.01 秒 |

- **opencode 启动固定约 2.5 秒，与库大小无关**。设计稿写的「回到保留槽位 0.5–1 秒」不成立，实际约 2.5–3 秒（仍远快于按需模式的 6–7.6 秒）；新用户约 3 秒（载入 + 启动）。
- 客户 NAS 的吞吐比本机盘低，大历史的载入要到现场实测。

## S5 opencode 网页闲着时的请求（无头 Chrome 挂 12 分钟）

| 请求 | 次数 | 说明 |
|---|---|---|
| `GET /api/health` | 72（每 10 秒） | 网页自己的定时探活，**普通 JSON 请求，不是事件流** |
| `GET /global/event` | 1（一直不结束） | 事件流（text/event-stream） |
| `/`、静态资源、`/api/session`、`/global/config`、`/path`、`/project`、`/session/status`、`/provider` | 各 1 | 打开页面时 |

- → 「交互活动」不能简单按「非事件流请求」算，否则开着的标签页每 10 秒刷一次活跃、永远不会闲置。
  分类器要排除 `/api/health`、`/global/health` 与事件流（`/global/event`、`/event`）；会话忙由 poolrun 上报补上。

## S6 一个 Pod 8 个槽位

- 8 个槽位容器 + 管家：5.4 秒全部 running，8 个 opencode 6.1 秒内全部就绪（并行启动）。
- 闲置（刚启动、无会话）每个 opencode 实占约 250 MiB，8 个合计约 2 GB；管家 1 MiB。
  结合 10-22 实测（真实使用每人约 0.7 GiB），一个 Pod 8 人满载约 5.6 GiB；保留态停掉 opencode 每槽省 250–500 MiB。

## 对计划的影响

- 时延口径改为：已在用 0 秒、回到保留槽位约 2.5–3 秒、新用户约 3 秒、按需模式 6–7.6 秒。
- P1 / P2 按上面各条的「→」实现；其余阶段不变。
