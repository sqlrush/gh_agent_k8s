"""闲置回收:什么时候可以把一个人的 Pod 收掉。

**这是整个网关最容易做错的一块**,因为「闲置」的定义不直观:

① opencode 的事件流(SSE)每 10 秒发一次 server.heartbeat(2026-09-21 实测,连续 75 秒
   零遗漏)。用户开着页面发呆时,**有字节流动但没有新请求**。按「N 分钟无新请求」
   判闲置,会把正开着页面的人的 Pod 收掉。所以活动时间由代理层在转发字节时更新,
   不是按请求计数。
② 刚建好还没人连的 Pod,last-activity 是 0。不能把它当成「1970 年就没动过」立刻收掉 ——
   那会让用户登录后 Pod 刚起来就被杀。没有活动记录时按创建时间算。
③ 回收必须有宽限:模型跑一个长任务可能几分钟不产生任何流量(它在等 LLM 回包)。
   宽限期要明显大于单次回合的时长。

两种模式(k8s-deploy.md §4):
  delete  对话结束就删 Deployment,下次登录重建(NAS 数据在,不需要还原)
  scale   缩到 0 副本,重连再拉起来 —— 省了镜像拉取与冷启动,代价是占着 Deployment
"""
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from gateway import reaper  # noqa: E402

NOW = 1_700_000_000.0
IDLE = 1800          # 30 分钟


def _dep(name, last_activity=None, created=None, replicas=1):
    ann = {} if last_activity is None else {"gaussdb-agent/last-activity": str(int(last_activity))}
    meta = {"name": name, "annotations": ann}
    if created is not None:
        meta["creationTimestamp"] = created
    return {"metadata": meta, "spec": {"replicas": replicas}, "status": {"availableReplicas": replicas}}


def test_recently_active_is_kept():
    d = _dep("runtime-u1", last_activity=NOW - 60)
    assert not reaper.should_reap(d, now=NOW, idle_s=IDLE)


def test_long_idle_is_reaped():
    d = _dep("runtime-u1", last_activity=NOW - IDLE - 1)
    assert reaper.should_reap(d, now=NOW, idle_s=IDLE)


def test_exactly_at_the_threshold_is_kept():
    """边界上留着 —— 宁可多留一轮,也不要在用户刚好回来的那一刻杀掉。"""
    assert not reaper.should_reap(_dep("runtime-u1", last_activity=NOW - IDLE), now=NOW, idle_s=IDLE)


def test_fresh_pod_without_activity_is_not_reaped():
    """**踩点**:刚建好还没人连,注解是空的。按创建时间算,不能当成 1970 年。"""
    d = _dep("runtime-u1", created="2023-11-14T22:13:20Z")     # = NOW
    assert not reaper.should_reap(d, now=NOW, idle_s=IDLE)


def test_old_pod_without_activity_is_reaped():
    """建了很久却从来没有活动记录 —— 那是真没人用(或者网关换过,注解丢了)。"""
    d = _dep("runtime-u1", created="2023-11-14T12:00:00Z")     # NOW 之前 10+ 小时
    assert reaper.should_reap(d, now=NOW, idle_s=IDLE)


def test_unparseable_timestamps_keep_the_pod():
    """时间戳读不懂时**不回收**。误留一个 Pod 只是占资源;误杀会打断正在干活的人。"""
    d = _dep("runtime-u1", created="不是时间")
    d["metadata"]["annotations"]["gaussdb-agent/last-activity"] = "abc"
    assert not reaper.should_reap(d, now=NOW, idle_s=IDLE)


def test_already_scaled_to_zero_is_not_reaped_again():
    """scale 模式下已经缩到 0 的,不用再处理一遍。"""
    d = _dep("runtime-u1", last_activity=NOW - IDLE - 1, replicas=0)
    assert not reaper.should_reap(d, now=NOW, idle_s=IDLE)


def test_user_id_is_derived_from_the_deployment_name():
    assert reaper.user_of("runtime-u1234") == "u1234"
    assert reaper.user_of("kb-import-u1234") == "u1234"
    assert reaper.user_of("vectordb") is None, "不是用户 Pod 的一律不碰"
    assert reaper.user_of("gateway") is None


def test_grace_is_configurable_and_defaults_generously():
    """模型跑长任务时可能几分钟没有任何流量(它在等 LLM 回包),宽限要明显大于单个回合。"""
    assert reaper.DEFAULT_IDLE_SECONDS >= 1800
