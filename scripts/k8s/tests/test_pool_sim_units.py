"""平台模拟器(参考实现)的逻辑:池子数量、映射、倒计时、§7 返回码的分支。用假 k8s 与假传输驱动。"""
import importlib.util
import pathlib

import pytest
import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("pool_sim", _ROOT / "scripts" / "k8s" / "pool-sim.py")
sim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sim)


class FakeKube:
    def __init__(self):
        self.pods = {}          # name -> dict
        self.deleted = []

    def apply(self, manifest):
        doc = yaml.safe_load(manifest)
        name = doc["metadata"]["name"]
        self.pods[name] = {"name": name, "ip": "10.1.0.%d" % len(self.pods), "ready": True, "deleting": False}

    def delete_pod(self, name):
        self.deleted.append(name)
        self.pods.pop(name, None)

    def sandboxes(self):
        return list(self.pods.values())

    def wait_ready(self, name, timeout_s=180):
        return True


class FakeSandboxes:
    """每个沙箱一个最小状态机,返回码按 §7。"""

    def __init__(self):
        self.state = {}
        self.script = {}        # pod -> 下一次 bind 的 (code, body)
        self.calls = []

    def call(self, pod, port, method, path, body=None, headers=None, timeout=120.0):
        self.calls.append((pod, port, method, path, body))
        st = self.state.setdefault(pod, {"state": "idle", "user": None})
        if port == 4096:
            return 200, {"ok": True, "auth": headers.get("Authorization")}
        assert headers == {"X-Sandbox-Key": "k" * 32}
        if path == "/sandbox/status":
            return 200, {"state": st["state"], "user_id": st["user"]}
        if path == "/sandbox/bind":
            if pod in self.script:
                code, b = self.script.pop(pod)
                if code >= 500:
                    st["state"] = "retired"
                return code, b
            if st["state"] != "idle":
                return 409, {"reason": "state", "state": st["state"]}
            st.update(state="bound", user=body["user_id"])
            return 200, {"opencode": {"password": "pw-" + pod}, "loaded": {"files": 0}}
        if path == "/sandbox/unbind":
            if st["user"] != body["user_id"]:
                return 409, {"reason": "user_mismatch"}
            st.update(state="retired")
            return 200, {"state": "retired", "reason_seen": body["reason"]}
        return 404, {}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture()
def plat(tmp_path):
    kube, sbx, clock = FakeKube(), FakeSandboxes(), Clock()
    p = sim.Platform(kube, sbx, "k" * 32, "img:t", "IfNotPresent", 4, tmp_path / "state.json",
                     log=lambda _m: None, clock=clock, sleep=lambda _s: None)
    p.kube_, p.sbx_, p.clock_ = kube, sbx, clock
    return p


def test_render_fills_the_three_placeholders_and_refuses_missing_ones():
    out = sim.render(sim.TEMPLATE.read_text(encoding="utf-8"), "ab12cd", "img:t", "Never")
    doc = yaml.safe_load(out)
    assert doc["metadata"]["name"] == "sandbox-ab12cd"
    assert {c["imagePullPolicy"] for c in doc["spec"]["containers"]} == {"Never"}
    with pytest.raises(KeyError):
        sim.string.Template("${SANDBOX_ID} ${OTHER}").substitute(SANDBOX_ID="x")


def test_up_fills_to_target_and_does_not_overfill(plat):
    assert len(plat.refill()) == 4
    assert plat.refill() == [] and len(plat.kube_.pods) == 4


def test_login_binds_an_idle_sandbox_and_relogin_reconnects(plat):
    plat.refill()
    r = plat.login("U9500")
    assert r["reused"] is False and r["password"] == "pw-" + r["sandbox"]
    plat.close("u9500", 60)
    r2 = plat.login("u9500")
    assert r2["reused"] is True and r2["sandbox"] == r["sandbox"] and r2["deadline"] is None, "倒计时内回来:接回、取消倒计时"
    assert (plat.state_path.stat().st_mode & 0o777) == 0o600, "映射表里有口令"


def test_the_seventeenth_user_triggers_a_fresh_sandbox(plat):
    plat.refill()
    pods = {plat.login("u95%02d" % i)["sandbox"] for i in range(4)}
    assert len(pods) == 4
    r = plat.login("u9599")
    assert r["sandbox"] not in pods and len(plat.kube_.pods) == 5


def test_logout_unbinds_deletes_and_refills(plat):
    plat.refill()
    pod = plat.login("u9500")["sandbox"]
    code, body = plat.logout("u9500")
    assert code == 200 and body["reason_seen"] == "logout"
    assert pod in plat.kube_.deleted and pod not in plat.kube_.pods
    assert len(plat.kube_.pods) == 4, "退出后补建,总数回到目标"
    assert "u9500" not in plat.users


def test_countdown_expiry_unbinds_with_reason_timeout(plat):
    plat.refill()
    plat.login("u9500")
    plat.login("u9501")
    plat.close("u9500", 30)
    plat.clock_.t += 10
    assert plat.tick() == []
    plat.clock_.t += 25
    assert plat.tick() == [("u9500", 200)]
    assert "u9500" not in plat.users and "u9501" in plat.users
    unbinds = [c for c in plat.sbx_.calls if c[3] == "/sandbox/unbind"]
    assert unbinds[-1][4] == {"user_id": "u9500", "reason": "timeout"}


def test_owner_busy_is_surfaced_with_retry_after(plat):
    plat.refill()
    first = next(iter(plat.kube_.pods))          # 模拟器按创建顺序挑空闲沙箱
    plat.sbx_.script[first] = (409, {"reason": "owner_busy", "holder": "sandbox-old", "retry_after": 42})
    with pytest.raises(sim.PlatformError) as ei:
        plat.login("u9500")
    assert ei.value.reason == "owner_busy" and ei.value.detail["retry_after"] == 42
    assert "u9500" not in plat.users


def test_a_failed_bind_deletes_that_sandbox_and_tries_another(plat):
    plat.refill()
    first = next(iter(plat.kube_.pods))          # 模拟器按创建顺序挑空闲沙箱
    plat.sbx_.script[first] = (504, {"reason": "opencode_not_ready"})
    r = plat.login("u9500")
    assert r["sandbox"] != first and first in plat.kube_.deleted


def test_a_state_conflict_moves_on_without_deleting(plat):
    plat.refill()
    first = next(iter(plat.kube_.pods))          # 模拟器按创建顺序挑空闲沙箱
    plat.sbx_.script[first] = (409, {"reason": "state", "state": "binding"})
    r = plat.login("u9500")
    assert r["sandbox"] != first and first not in plat.kube_.deleted


def test_an_unbind_conflict_does_not_delete_the_pod(plat):
    plat.refill()
    pod = plat.login("u9500")["sandbox"]
    plat.sbx_.state[pod]["user"] = "someone-else"
    with pytest.raises(sim.PlatformError):
        plat.logout("u9500")
    assert pod in plat.kube_.pods, "映射对不上时先核对,别把别人的沙箱删了"


def test_a_vanished_sandbox_is_dropped_from_the_map_and_the_user_gets_a_new_one(plat):
    plat.refill()
    pod = plat.login("u9500")["sandbox"]
    plat.kube_.pods.pop(pod)                          # 被删 / 被强杀 / 节点故障
    r = plat.login("u9500")
    assert r["reused"] is False and r["sandbox"] != pod


def test_down_logs_everyone_out_and_leaves_no_sandbox_behind(plat):
    plat.refill()
    plat.login("u9500")
    plat.login("u9501")
    plat.down()
    assert plat.kube_.pods == {} and plat.users == {}, "退出时的补建不能在下线时再建出沙箱"


def test_a_dropped_connection_on_unbind_still_deletes_and_moves_on(plat):
    plat.refill()
    pod = plat.login("u9500")["sandbox"]
    real = plat.sbx_.call
    plat.sbx_.call = lambda p, port, m, path, *a, **k: (0, {"reason": "transport"}) if path == "/sandbox/unbind" else real(p, port, m, path, *a, **k)
    code, body = plat.logout("u9500")
    assert code == 0 and pod in plat.kube_.deleted and "u9500" not in plat.users


def test_http_turns_a_dropped_connection_into_a_failure_not_an_exception():
    import socket
    import threading
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def slam():
        c, _ = srv.accept()
        c.recv(1024)
        c.close()                                     # 不回应就断开:RemoteDisconnected
    threading.Thread(target=slam, daemon=True).start()
    code, body = sim._http("http://127.0.0.1:%d/x" % srv.getsockname()[1], "POST", {}, {}, 5)
    srv.close()
    assert code == 0 and body["reason"] == "transport"


def test_oc_passthrough_injects_the_password(plat):
    plat.refill()
    plat.login("u9500")
    code, body = plat.oc("u9500", "GET", "/session")
    assert code == 200 and body["auth"].startswith("Basic ")


def test_state_survives_a_restart_of_the_simulator(plat, tmp_path):
    plat.refill()
    pod = plat.login("u9500")["sandbox"]
    p2 = sim.Platform(plat.kube_, plat.sbx_, "k" * 32, "img:t", "IfNotPresent", 4, tmp_path / "state.json",
                      log=lambda _m: None)
    assert p2.login("u9500")["sandbox"] == pod
