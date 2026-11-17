"""Fuyao 运行模式的网关部分(2026-11-17):OpenSandbox 客户端、沙箱令牌、路由器、回收器、配置。

OpenSandbox 用真 HTTP 的替身(fake_fuyao.FakeOpenSandbox,按平台实测行为写);envd 用对象替身(它的协议在
test_fuyao_envd_units.py 里对着真 HTTP 测)。要点:
- 新用户:建沙箱 → 下发配置(含签名的沙箱令牌,集群服务能独立核对)→ 探活;第二次请求走缓存,不碰平台;
- 非交互请求不建、不恢复(开着的标签页不能把暂停的沙箱拉起来);
- 暂停的沙箱恢复后换了地址、平台还回旧地址:探活用本人口令,连不上就等,不会串到别人那里;
- 两个网关副本并发建出两台:只留最早的;
- 沙箱组满了回等待页;开通前提缺失当场报出来;
- 回收器:活动写进 metadata、闲置先收尾再暂停、有会话在跑不动、收尾失败不删、暂停超期删除、到期前续期。
"""
import pathlib
import sys
import time

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from gateway import config as gwconfig  # noqa: E402
from gateway import fuyaorouter as fr  # noqa: E402
from gateway import fuyaosweeper as fs  # noqa: E402
from gateway import opensandbox as osb  # noqa: E402
from gateway import pods, poolrouter, sbxtoken  # noqa: E402
from gateway.tests.fake_fuyao import FakeEnvd, FakeOpenSandbox  # noqa: E402

KEY = b"k" * 32
POLICY = fr.Policy(templates={"runtime": "tpl-run", "kb-import": "tpl-imp"},
                   groups={"runtime": "g-run", "kb-import": "g-imp"}, wait_s=0.0, idle_s=600, retain_s=86400)


class Clock:
    def __init__(self, t=None):
        self.t = time.time() if t is None else t

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


@pytest.fixture()
def env():
    fake = FakeOpenSandbox(capacity={"g-run": 3, "g-imp": 1})
    envd = FakeEnvd()
    clock = Clock()
    logs = []
    prereq_calls = []

    def prereq(user, kind):
        prereq_calls.append((user, kind))
        if user == "nobody":
            raise pods.PrerequisiteMissing("缺 Secret grmp-token-nobody")

    router = fr.FuyaoRouter(osb.Client(fake.url, timeout_s=5), envd, POLICY, KEY,
                            password_for=lambda u: "pw-" + u,
                            launch_env=lambda u, k: {"MODEL_ID": "m1", "EMPTY": "", "GSDB_STATE_URL": "http://10.0.0.1:8090"},
                            prereq=prereq, prober=envd.prober, log=logs.append, clock=clock, sleep=clock.sleep)
    yield fake, envd, router, clock, logs, prereq_calls
    fake.close()


def _platform_calls(fake):
    return [(m, p) for m, p, _b, _h in fake.requests]


# -- 路由器 ------------------------------------------------------------------------------------------

def test_a_new_user_gets_a_sandbox_with_signed_identity_and_the_second_request_uses_the_cache(env):
    fake, envd, router, clock, logs, prereq_calls = env
    t = router.target("u1001")
    assert len(fake.boxes) == 1
    bid, box = next(iter(fake.boxes.items()))
    assert box["metadata"] == {"gsdb-app": "gaussdb-agent", "gsdb-user": "u1001", "gsdb-kind": "runtime",
                               "gsdb-active": str(int(clock.t))}
    assert box["group"] == "g-run" and box["template"] == "tpl-run"
    create = [b for m, p, b, _h in fake.requests if m == "POST" and p == "/v1/sandboxes"][0]
    assert create["resourceLimits"] == {"cpu": "2", "memory": "4Gi"} and "volumes" not in create, \
        "平台要求带 resourceLimits;卷在 E2B 后端不生效,不发"
    assert t == poolrouter.Target(pod=bid, slot=0, host=box["ip"], port=4096, password="pw-u1001",
                                  reports_port=4097, reports_token=sbxtoken.reports_token(KEY, bid), since=clock.t)
    host, argv, user, envs = envd.runs[0]
    assert host == box["ip"] and argv[-1] == "fuyao-start" and user == "root"
    claims = sbxtoken.verify(KEY, envs["GSDB_SANDBOX_TOKEN"])
    assert (claims.sandbox_id, claims.user_id, claims.kind) == (bid, "u1001", "runtime")
    assert envs["OPENCODE_SERVER_PASSWORD"] == "pw-u1001" and envs["GSDB_USER_ID"] == "u1001"
    assert envs["MODEL_ID"] == "m1" and "EMPTY" not in envs, "空值不下发"
    assert prereq_calls == [("u1001", "runtime")]
    n = len(fake.requests)
    assert router.target("u1001") is t and len(fake.requests) == n, "已在用:走缓存,不碰平台"


def test_a_sandbox_token_does_not_verify_under_another_key_or_when_tampered(env):
    _fake, envd, router, *_ = env
    router.target("u1001")
    token = envd.runs[0][3]["GSDB_SANDBOX_TOKEN"]
    assert sbxtoken.verify(b"x" * 32, token) is None
    payload, sig = token[len(sbxtoken.PREFIX):].split(".")
    forged = sbxtoken.PREFIX + payload[:-2] + ("AA" if payload[-2:] != "AA" else "BB") + "." + sig
    assert sbxtoken.verify(KEY, forged) is None


def test_non_interactive_requests_neither_create_nor_resume(env):
    fake, _envd, router, *_ = env
    with pytest.raises(poolrouter.NotActive):
        router.target("u1001", interactive=False)
    fake.add({"gsdb-app": "gaussdb-agent", "gsdb-user": "u1002", "gsdb-kind": "runtime"}, state="Paused")
    with pytest.raises(poolrouter.NotActive):
        router.target("u1002", interactive=False)
    assert not [1 for m, p, *_ in fake.requests if m == "POST"], "没有建,也没有恢复"


def test_a_paused_sandbox_is_resumed_and_reached_at_its_new_address(env):
    fake, envd, router, clock, *_ = env
    bid = fake.add({"gsdb-app": "gaussdb-agent", "gsdb-user": "u1001", "gsdb-kind": "runtime", "gsdb-paused": "1"},
                   state="Paused")
    old = fake.ip(bid)
    # 内存快照里 opencode 一直在:恢复后在虚拟机的「当前」地址上应答本人口令
    router.prober = lambda host, pw: host == fake.ip(bid) and pw == "pw-u1001"
    t = router.target("u1001")
    assert fake.boxes[bid]["state"] == "Running" and t.host == fake.ip(bid) != old
    assert "gsdb-paused" not in fake.boxes[bid]["metadata"]
    assert not envd.runs, "恢复的是内存快照,不再跑启动器"


def test_a_stale_address_after_resume_never_reaches_someone_else_and_ends_in_a_wait(env):
    fake, envd, router, *_ = env
    fake.stale_after_resume = True
    bid = fake.add({"gsdb-app": "gaussdb-agent", "gsdb-user": "u1001", "gsdb-kind": "runtime"}, state="Paused")
    victim = fake.add({"gsdb-app": "gaussdb-agent", "gsdb-user": "u2002", "gsdb-kind": "runtime"})
    fake.boxes[victim]["ip"] = fake.ip(bid)                                  # 旧地址已分给了别人
    envd.ready.add(fake.ip(victim))
    real_prober = envd.prober
    router.prober = lambda host, pw: real_prober(host, pw) and pw == "pw-u2002"   # 别人的 opencode 只认别人的口令
    with pytest.raises(poolrouter.PoolWait) as ei:
        router.target("u1001")
    assert ei.value.reason == "address_stale"


def test_concurrent_creation_by_two_gateways_keeps_only_the_oldest(env):
    fake, envd, router, *_ = env
    md = {"gsdb-app": "gaussdb-agent", "gsdb-user": "u1001", "gsdb-kind": "runtime"}

    def other_gateway_was_faster(f, mine):
        f.on_create = None
        older = f.add(md, created=f.boxes[mine]["created"] - 5)
        other_gateway_was_faster.older = older
    fake.on_create = other_gateway_was_faster
    t = router.target("u1001")
    older = other_gateway_was_faster.older
    assert list(fake.boxes) == [older] and t.pod == older, "自己建的那台删掉,用最早的"
    assert envd.runs and envd.runs[-1][0] == fake.ip(older), "那台还没起来:envd 在,补起一次(启动器幂等)"


def test_extra_sandboxes_of_one_user_and_kind_are_removed(env):
    fake, envd, router, *_ = env
    md = {"gsdb-app": "gaussdb-agent", "gsdb-user": "u1001", "gsdb-kind": "runtime"}
    a = fake.add(md, created=100)
    b = fake.add(md, created=200)
    envd.ready.add(fake.ip(a))
    t = router.target("u1001")
    assert t.pod == a and b not in fake.boxes


def test_a_full_group_is_a_waiting_page_and_missing_prerequisites_are_reported(env):
    fake, _envd, router, *_ = env
    for i in range(3):
        fake.add({"gsdb-app": "gaussdb-agent", "gsdb-user": "x%d" % i, "gsdb-kind": "runtime"}, group="g-run")
    with pytest.raises(poolrouter.PoolFull):
        router.target("u1001")
    with pytest.raises(pods.PrerequisiteMissing):
        router.target("nobody")


def test_a_failed_launch_becomes_a_wait_with_the_reason_logged_but_not_the_secrets(env):
    _fake, envd, router, _clock, logs, _p = env
    envd.fail_launch = True
    with pytest.raises(poolrouter.PoolWait) as ei:
        router.target("u1001")
    assert ei.value.reason == "launch_failed"
    text = "\n".join(logs)
    assert "启动失败" in text and "载入状态失败" in text
    assert "pw-u1001" not in text and sbxtoken.PREFIX not in text, "日志里不出现口令与令牌"


def test_the_import_environment_is_its_own_sandbox_from_its_own_template_and_group(env):
    fake, envd, router, *_ = env
    router.target("u1001")
    t = router.target("u1001", kind="kb-import")
    boxes = {v["metadata"]["gsdb-kind"]: v for v in fake.boxes.values()}
    assert set(boxes) == {"runtime", "kb-import"}
    assert boxes["kb-import"]["template"] == "tpl-imp" and boxes["kb-import"]["group"] == "g-imp"
    assert sbxtoken.verify(KEY, envd.runs[-1][3]["GSDB_SANDBOX_TOKEN"]).kind == "kb-import"
    assert t.host == boxes["kb-import"]["ip"]


def test_forget_drops_the_cached_target(env):
    fake, _envd, router, *_ = env
    t = router.target("u1001")
    router.forget_sandbox(t.pod)
    n = len(fake.requests)
    router.target("u1001")
    assert len(fake.requests) > n, "缓存作废后重新查平台"


# -- 回收器 ------------------------------------------------------------------------------------------

def _sweeper(router, clock, busy=lambda h, p: False):
    return fs.FuyaoSweeper(router, sweep_s=30, renew_margin_s=600, busy_check=busy, log=lambda m: None, clock=clock)


def test_activity_is_written_into_sandbox_metadata_and_kept_when_writing_fails(env):
    fake, _envd, router, clock, *_ = env
    t = router.target("u1001")
    clock.t += 100
    router.note_activity("u1001")
    _sweeper(router, clock).sweep()
    assert fake.boxes[t.pod]["metadata"]["gsdb-active"] == str(int(clock.t))
    router.note_activity("u1001")
    fake.close()                                                            # 平台连不上
    _sweeper(router, clock).sweep()
    assert router.drain_activity(), "列举失败:活动放回去,下一轮再写"


def test_an_idle_sandbox_is_synced_then_paused_and_a_busy_one_is_left_alone(env):
    fake, envd, router, clock, *_ = env
    t = router.target("u1001")
    clock.t += 601
    out = _sweeper(router, clock, busy=lambda h, p: True).sweep()
    assert out["busy"] == 1 and fake.boxes[t.pod]["state"] == "Running"
    out = _sweeper(router, clock).sweep()
    assert out["retired"] == 1 and fake.boxes[t.pod]["state"] == "Paused"
    assert fake.boxes[t.pod]["metadata"]["gsdb-paused"] == str(int(clock.t))
    assert [r[1][-1] for r in envd.runs] == ["fuyao-start", "fuyao-stop"], "先收尾,再暂停(暂停不放开独占)"
    assert router.cached() == {}


def test_without_pause_a_sandbox_whose_final_sync_failed_is_not_deleted(env):
    fake, envd, router, clock, *_ = env
    router.policy = POLICY.__class__(**dict(POLICY.__dict__, pause=False))
    t = router.target("u1001")
    clock.t += 601
    envd.fail_stop = True
    sw = _sweeper(router, clock)
    sw.policy = router.policy
    assert sw.sweep()["failed"] == 1 and t.pod in fake.boxes, "删了会丢最后一个同步周期"
    envd.fail_stop = False
    assert sw.sweep()["retired"] == 1 and t.pod not in fake.boxes
    assert envd.runs[-1][1][-2:] == ["fuyao-stop", "--release"], "删除之前放开独占,新沙箱不用等它过期"


def test_a_sandbox_paused_beyond_the_retention_is_deleted(env):
    fake, _envd, router, clock, *_ = env
    bid = fake.add({"gsdb-app": "gaussdb-agent", "gsdb-user": "u1001", "gsdb-kind": "runtime",
                    "gsdb-paused": str(int(clock.t - 86401))}, state="Paused")
    assert _sweeper(router, clock).sweep()["deleted"] == 1 and bid not in fake.boxes


def test_sandboxes_near_expiry_are_renewed_paused_ones_only_until_retention_ends(env):
    fake, _envd, router, clock, *_ = env
    run = fake.add({"gsdb-app": "gaussdb-agent", "gsdb-user": "u1001", "gsdb-kind": "runtime",
                    "gsdb-active": str(int(clock.t))}, expires=clock.t + 300)
    paused = fake.add({"gsdb-app": "gaussdb-agent", "gsdb-user": "u1002", "gsdb-kind": "runtime",
                       "gsdb-paused": str(int(clock.t - 86000))}, state="Paused", expires=clock.t + 300)
    out = _sweeper(router, clock).sweep()
    assert out["renewed"] == 2
    assert fake.boxes[run]["expires"] == pytest.approx(clock.t + POLICY.sandbox_timeout_s, abs=2)
    assert fake.boxes[paused]["expires"] == pytest.approx(clock.t - 86000 + 86400, abs=2), "暂停的只续到保留期满"


def test_admin_summary_and_actions(env):
    fake, envd, router, clock, *_ = env
    t = router.target("u1001")
    foreign = fake.add({"owner": "someone-else"})
    sw = _sweeper(router, clock)
    s = sw.summary()
    assert [x["user_id"] for x in s["sandboxes"]] == ["u1001"] and s["states"] == {"Running": 1}
    assert sw.act("pause", t.pod)[0] == 200 and fake.boxes[t.pod]["state"] == "Paused"
    assert sw.act("resume", t.pod)[0] == 200 and fake.boxes[t.pod]["state"] == "Running"
    code, body = sw.act("delete", t.pod)
    assert code == 200 and body["synced"] is True and t.pod not in fake.boxes
    assert sw.act("delete", foreign)[0] == 404, "不是本智能体的沙箱不动"
    assert sw.act("explode", "x")[0] == 404


# -- 配置 --------------------------------------------------------------------------------------------

BASE = {"GATEWAY_NAMESPACE": "ns", "GATEWAY_IMAGE_TAG": "t", "GATEWAY_TRUST_SECRET": "s" * 32}
FUYAO = dict(BASE, GATEWAY_RUNTIME_MODE="fuyao", GATEWAY_POOL_CONTROL_KEY="c" * 32,
             GATEWAY_FUYAO_API="http://opensandbox-server.flux-system.svc:80",
             GATEWAY_FUYAO_RUNTIME_TEMPLATE="gsdb-runtime-v1-5-0", GATEWAY_FUYAO_RUNTIME_GROUP="sg-gsdb-run",
             GATEWAY_FUYAO_IMPORT_TEMPLATE="gsdb-import-v1-5-0", GATEWAY_FUYAO_IMPORT_GROUP="sg-gsdb-imp",
             GATEWAY_FUYAO_STATE_URL="http://10.96.0.20:8090", GATEWAY_FUYAO_CREDPROXY_URL="http://10.96.0.21:8091")


def test_fuyao_config_parses_and_other_modes_ignore_it():
    c = gwconfig.load(FUYAO)
    assert c.runtime_mode == "fuyao" and c.fuyao_pause is True and c.fuyao_idle_seconds == 1800
    assert c.fuyao_state_url == "http://10.96.0.20:8090"
    assert gwconfig.load(BASE).runtime_mode == "ondemand", "不在 fuyao 模式时缺这些不拒绝"


@pytest.mark.parametrize("patch,needle", [
    ({"GATEWAY_FUYAO_API": ""}, "GATEWAY_FUYAO_API"),
    ({"GATEWAY_FUYAO_STATE_URL": ""}, "GATEWAY_FUYAO_STATE_URL"),
    ({"GATEWAY_POOL_CONTROL_KEY": "short"}, "GATEWAY_POOL_CONTROL_KEY"),
    ({"GATEWAY_FUYAO_RUNTIME_GROUP": "Bad_Group"}, "沙箱组名"),
    ({"GATEWAY_FUYAO_API": "opensandbox:80"}, "http(s)://"),
    ({"GATEWAY_FUYAO_MEMORY": "four"}, "资源量"),
    ({"GATEWAY_FUYAO_PAUSE": "maybe"}, "GATEWAY_FUYAO_PAUSE"),
    ({"GATEWAY_FUYAO_IDLE_SECONDS": "60"}, "太小"),
])
def test_fuyao_config_fails_closed(patch, needle):
    with pytest.raises(gwconfig.ConfigError) as ei:
        gwconfig.load(dict(FUYAO, **patch))
    assert needle in str(ei.value)


# -- 客户端细节 --------------------------------------------------------------------------------------

def test_client_errors_and_parsing():
    fake = FakeOpenSandbox(capacity={"g": 0})
    try:
        c = osb.Client(fake.url, api_key="k1", timeout_s=5)
        with pytest.raises(osb.CapacityError):
            c.create("tpl", "g", {}, "1", "1Gi", 600)
        assert fake.requests[-1][3].get("OPEN-SANDBOX-API-KEY") == "k1", "平台开认证后带 API Key"
        assert c.get("nope") is None and c.delete("nope") is False
        bid = fake.add({"a": "1", "b": "2"})
        fake.add({"a": "1", "b": "3"})
        assert [s.id for s in c.list({"a": "1", "b": "2"})] == [bid]
        assert c.address(bid) == fake.ip(bid)
        c.patch_metadata(bid, {"b": None, "c": "9"})
        assert fake.boxes[bid]["metadata"] == {"a": "1", "c": "9"}
    finally:
        fake.close()
    with pytest.raises(osb.OpenSandboxError):
        osb.Client(fake.url, timeout_s=1).get("x")
    with pytest.raises(ValueError):
        osb.Client("opensandbox:80")
