"""固定池化的管家(poolsteward + poolslot,2026-10-23)。

真的槽位守护进程(slotrun,线程里 tick)+ 假 opencode(tests/fake_opencode.py)+ 管家,目录全在 tmp 里。
「容器重启」由测试台模拟:换一个新的 SlotSupervisor(新 boot)在同一组目录上启动,containerID 换一个。

同一个测试进程里跑几个槽位,和生产不一样的两处已关掉:kill(-1)(生产里每个槽位有自己的进程空间)
与收养孤儿(会把别的槽位的子进程收走)。
"""
import base64
import http.client
import json
import os
import pathlib
import socket
import sqlite3
import sys
import threading
import time
import urllib.request

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "docker"))

import fdtree  # noqa: E402
import poolauth  # noqa: E402
import poolrun  # noqa: E402
import poolslot  # noqa: E402
import poolsteward  # noqa: E402
import poolsync  # noqa: E402
import slotrun  # noqa: E402

FAKE = pathlib.Path(__file__).with_name("fake_opencode.py")
KEY = b"c" * 32
POD = "pool-0"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _podctl_cmd() -> list:
    cmd = [sys.executable, str(_ROOT / "docker" / "podctl.py")]
    sp = os.environ.get("PYTHONPATH")
    return ["env", "PYTHONPATH=" + sp] + cmd if sp else cmd


def _rows(path: pathlib.Path) -> int:
    con = sqlite3.connect(path)
    try:
        return con.execute("select count(*) from t").fetchone()[0]
    finally:
        con.close()


def _wait(cond, timeout=15.0, step=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(step)
    return False


class SlotRuntime:
    """一个槽位容器:SlotSupervisor 在线程里 tick;exit 之后按 kubelet 的样子重启(新 boot、新 containerID)。"""

    def __init__(self, pod: "Pod", i: int):
        self.pod, self.i = pod, i
        self.restarts = 0
        self.auto_restart = True
        self.sup = None
        self.thread = None
        self.running = False
        self._start()

    @property
    def container_id(self) -> str:
        return "c-%d-%d" % (self.i, self.restarts)

    def _start(self):
        p = self.pod
        run = poolrun.Config(ctl_dir=p.slot_dir(self.i) / "ctl", state=p.slot_dir(self.i) / "state", kb_dir=p.kb,
                             opencode_cmd=[sys.executable, str(FAKE)], podctl_cmd=_podctl_cmd(),
                             port=p.ports[self.i][0], reports_port=p.ports[self.i][1], poll_s=0.05,
                             stop_timeout_s=5, health_every_s=1, env_file=p.slot_dir(self.i) / "data" / "env.sh")
        scfg = slotrun.SlotConfig(slot=self.i, run=run, data=p.slot_dir(self.i) / "data", agent=p.agent,
                                  wipe=(p.slot_dir(self.i) / "data",), busy_every_s=0.2)
        env = dict(os.environ, FAKE_OUT=str(p.tmp / ("fake-%d" % self.i)), GRMP_API_HOST="10.0.0.1",
                   MODEL_BASE_URL="http://model", MODEL_API_KEY="k", MODEL_ID="m")
        self.sup = slotrun.SlotSupervisor(scfg, env=env, log=p.logs.append)
        self.sup.boot()
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        while self.running:
            if not self.sup.tick():
                self.sup._close_placeholders()
                self.running = False
                if self.auto_restart and not self.pod.closed:
                    self.restart()
                return
            time.sleep(0.05)

    def restart(self):
        """容器重启:旧进程没了(这里先让它收尾),新进程清盘、换 boot。"""
        if self.running:
            self.running = False
            self.thread.join(5)
            self.sup._terminate(self.sup.oc)
            self.sup._stop_reports()
            self.sup._close_placeholders()
        self.restarts += 1
        self._start()

    def stop(self):
        self.running = False
        if self.thread is not None:
            self.thread.join(5)
        if self.sup is not None:
            self.sup._terminate(self.sup.oc)
            self.sup._stop_reports()
            self.sup._close_placeholders()


class Pod:
    def __init__(self, tmp: pathlib.Path, n: int = 2, verdict=None, **settings):
        self.tmp, self.n, self.closed = tmp, n, False
        self.users, self.priv, self.kb, self.agent = tmp / "users", tmp / "priv", tmp / "kb", tmp / "agent"
        for p in (self.users, self.priv, self.kb, self.agent / "config" / "opencode"):
            p.mkdir(parents=True)
        (self.agent / "config" / "opencode" / "AGENTS.md").write_text("rules", encoding="utf-8")
        self.ports = {i: (_free_port(), _free_port()) for i in range(n)}
        for i in range(n):
            for sub in ("ctl", "state", "data"):
                (self.slot_dir(i) / sub).mkdir(parents=True)
        self.logs = []
        self.slots = [SlotRuntime(self, i) for i in range(n)]
        base = dict(sync_seconds=3600, ready_seconds=15, stop_wait_seconds=8, snap_wait_seconds=5, hello_seconds=5,
                    owner_refresh_seconds=3600, evict_timeout_seconds=3600)
        base.update(settings)
        self.settings = poolslot.Settings(pod=POD, **base)
        self.verdict = verdict
        self.st = None
        self.new_steward()

    def slot_dir(self, i: int) -> pathlib.Path:
        return self.tmp / "slots" / str(i)

    def new_steward(self, start: bool = True):
        if self.st is not None:
            self.st.stop.set()
        cfg = poolsteward.Config(key=KEY, pod=POD, slots=self.n, users_dir=self.users, slots_dir=self.tmp / "slots",
                                 priv_dir=self.priv, port=_free_port(), monitor_seconds=0.1, settings=self.settings,
                                 slot_ports=lambda i: self.ports[i], slot_uid=lambda i: os.getuid())
        self.st = poolsteward.Steward(cfg, log=self.logs.append, verdict=self.verdict)
        if start:
            self.st.start()
        return self.st

    def all_free(self):
        assert _wait(lambda: all(s.state == poolslot.FREE for s in self.st.slots)), self.logs[-20:]

    def bind(self, slot=0, user="u1234", grmp="", expect=None):
        body = {"slot": slot, "user_id": user, "expect_in_use": self.st.in_use() if expect is None else expect,
                "container_id": self.slots[slot].container_id}
        if grmp:
            body["grmp_token"] = poolauth.seal(KEY, grmp)
        return self.st.bind(body)

    def resume(self, slot=0, user="u1234", expect=None):
        return self.st.resume({"slot": slot, "user_id": user, "container_id": self.slots[slot].container_id,
                               "expect_in_use": self.st.in_use() if expect is None else expect})

    def user_fd(self, user="u1234") -> int:
        root = fdtree.open_root(self.users)
        try:
            return poolsync.open_user(root, user)
        finally:
            os.close(root)

    def owner(self, user="u1234") -> dict:
        try:
            return json.loads((self.users / user / poolsync.OWNER).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

    def close(self):
        self.closed = True
        if self.st is not None:
            self.st.stop.set()
        for s in self.slots:
            s.stop()


def _healthy(port: int, pw: str) -> bool:
    req = urllib.request.Request("http://127.0.0.1:%d/global/health" % port)
    req.add_header("Authorization", "Basic " + base64.b64encode(("opencode:" + pw).encode()).decode())
    try:
        urllib.request.urlopen(req, timeout=1).read()
        return True
    except OSError:
        return False


@pytest.fixture()
def pod(tmp_path, monkeypatch):
    monkeypatch.setattr(slotrun.SlotSupervisor, "_kill_others", lambda self: None)
    monkeypatch.setattr(slotrun.SlotSupervisor, "_reap_orphans", lambda self: None)
    p = Pod(tmp_path)
    p.all_free()
    yield p
    p.close()


# -- 新 Pod ----------------------------------------------------------------------------------

def test_fresh_pod_slots_handshake_into_free(pod):
    st = pod.st.status()
    assert st["free"] == 2 and st["in_use"] == 0 and st["pod"] == POD
    for s in pod.st.slots:
        assert s.rec["boot"] == pod.slots[s.i].sup.boot_id and s.rec["quiet"]


# -- 绑定 ------------------------------------------------------------------------------------

def test_bind_starts_the_users_opencode_with_per_user_credentials(pod):
    code, body = pod.bind(grmp="grmp-of-u1234")
    assert code == 200, body
    pw, rt = body["opencode"]["password"], body["reports"]["token"]
    assert _healthy(body["opencode"]["port"], pw)
    req = urllib.request.Request("http://127.0.0.1:%d/whoami.json" % body["reports"]["port"])
    req.add_header("Authorization", "Bearer " + rt)
    assert json.loads(urllib.request.urlopen(req, timeout=2).read()) == {"user_id": "u1234"}
    env = json.loads((pod.tmp / "fake-0" / "env.json").read_text(encoding="utf-8"))
    assert env["GRMP_AUTH_TOKEN"] == "grmp-of-u1234" and env["GSDB_USER_ID"] == "u1234"
    assert pod.owner()["pod"] == "%s/0" % POD, "独占标记按「pod/槽位」整串记"
    fd = pod.user_fd()
    assert poolsync.gen_read(fd) == pod.st.slots[0].rec["gen"] and poolsync.is_dirty(fd)
    os.close(fd)
    text = json.dumps(pod.st.status())
    for secret in (pw, rt, "grmp-of-u1234"):
        assert secret not in text, "status 不能带秘密"


def test_bind_is_idempotent_for_the_same_user_and_cas_guards_the_pod(pod):
    code, first = pod.bind()
    assert code == 200
    code, again = pod.bind(expect=7)
    assert code == 200 and again["idempotent"] and again["opencode"]["password"] == first["opencode"]["password"]
    code, body = pod.bind(slot=1, user="u2000", expect=0)
    assert code == 409 and body["reason"] == "pod_changed" and body["in_use"] == 1, "网关以为 Pod 是空的,其实已有 1 人"
    code, _ = pod.bind(slot=1, user="u2000", expect=1)
    assert code == 200


def test_bind_refuses_a_user_held_elsewhere_without_touching_the_slot(pod):
    fd = pod.user_fd()
    assert poolsync.owner_acquire(fd, "pool-7/3")[0]
    os.close(fd)
    code, body = pod.bind()
    assert code == 409 and body["reason"] == "owner_busy" and body["holder"] == "pool-7/3"
    assert pod.st.slots[0].state == poolslot.FREE


def test_bind_refuses_a_plaintext_grmp_token(pod):
    code, body = pod.st.bind({"slot": 0, "user_id": "u1234", "expect_in_use": 0, "grmp_token": "plain"})
    assert code == 400 and body["reason"] == "bad_token"


# -- 转保留与恢复 ---------------------------------------------------------------------------------

def _retain(pod, slot=0, user="u1234"):
    return pod.st.retain({"slot": slot, "user_id": user})


def test_retain_writes_everything_back_and_releases_the_owner(pod):
    code, b = pod.bind()
    assert code == 200
    code, body = _retain(pod)
    assert code == 200 and body["state"] == poolslot.RETAINED and not body["dirty"], body
    nas_db = pod.users / "u1234" / "xdg-data" / "opencode" / "opencode.db"
    assert _rows(nas_db) == 2, "停之前最后一段对话也写回了"
    assert pod.owner() == {} and not poolsync.is_dirty(pod.user_fd())
    s = pod.st.slots[0]
    assert s.rec["quiet"] and s.rec["fingerprint"] and not _healthy(b["opencode"]["port"], b["opencode"]["password"])
    assert pod.st.in_use() == 0


def test_resume_reuses_the_slot_files_with_a_new_password_and_gen(pod):
    _c, b = pod.bind()
    gen1 = pod.st.slots[0].rec["gen"]
    _retain(pod)
    code, body = pod.resume()
    assert code == 200 and body["resumed"], body
    assert body["opencode"]["password"] != b["opencode"]["password"]
    assert _healthy(body["opencode"]["port"], body["opencode"]["password"])
    assert pod.st.slots[0].rec["gen"] != gen1 and pod.owner()["pod"] == "%s/0" % POD


def test_resume_after_the_user_worked_elsewhere_is_stale(pod):
    pod.bind()
    _retain(pod)
    fd = pod.user_fd()
    assert poolsync.owner_acquire(fd, "ondemand-pod")[0]
    poolsync.gen_rotate(fd)                 # 按需模式的 Pod 取独占时也换令牌
    poolsync.owner_release(fd, "ondemand-pod")
    os.close(fd)
    code, body = pod.resume()
    assert code == 409 and body["reason"] == "stale"
    s = pod.st.slots[0]
    assert s.rec["stale"] and pod.owner() == {}, "对不上就放掉独占,交给网关腾挪"
    assert pod.st.evict({"slot": 0, "container_id": pod.slots[0].container_id})[0] == 202


def test_resume_while_the_user_is_active_elsewhere_is_owner_busy(pod):
    pod.bind()
    _retain(pod)
    fd = pod.user_fd()
    poolsync.owner_acquire(fd, "pool-5/1")
    os.close(fd)
    code, body = pod.resume()
    assert code == 409 and body["reason"] == "owner_busy" and pod.st.slots[0].rec["stale"]


def test_a_dirty_retained_slot_keeps_the_owner_and_refuses_eviction(pod, monkeypatch):
    pod.bind()
    real = poolsync.sync
    monkeypatch.setattr(poolsync, "sync", lambda *a, **kw: poolsync.Moved(errors=["NAS 写满了"]))
    code, body = _retain(pod)
    assert code == 200 and body["dirty"]
    s = pod.st.slots[0]
    assert pod.owner()["pod"] == "%s/0" % POD, "写回不干净:继续持有独占,他到别处登录会被挡回这里"
    code, body = pod.st.evict({"slot": 0, "container_id": pod.slots[0].container_id})
    assert code == 409 and body["reason"] == "not_synced"
    monkeypatch.setattr(poolsync, "sync", real)
    s.next_sync = 0                         # 让后台的重试马上来一轮
    assert _wait(lambda: not s.rec["dirty"]), pod.logs[-10:]
    assert pod.owner() == {}
    assert pod.st.evict({"slot": 0, "container_id": pod.slots[0].container_id})[0] == 202


# -- 腾挪与核验 -----------------------------------------------------------------------------------

def test_evict_restarts_the_container_and_verify_needs_a_new_container_id(pod):
    pod.bind()
    _retain(pod)
    old_cid, old_boot = pod.slots[0].container_id, pod.st.slots[0].rec["boot"]
    assert pod.st.evict({"slot": 0, "container_id": old_cid})[0] == 202
    assert _wait(lambda: pod.slots[0].restarts == 1 and pod.slots[0].running)
    code, body = pod.st.verify({"slot": 0, "container_id": old_cid})
    assert code == 409 and body["reason"] == "not_restarted", "containerID 没变不认"
    code, body = pod.st.verify({"slot": 0, "container_id": pod.slots[0].container_id})
    assert code == 200 and body["state"] == poolslot.FREE, body
    s = pod.st.slots[0]
    assert s.rec["boot"] != old_boot and s.rec["user"] is None and s.disk_is_clean()
    assert not (pod.slot_dir(0) / "state" / "xdg-data" / "opencode" / "opencode.db").exists()


def test_verify_before_the_restart_is_refused(pod):
    pod.slots[0].auto_restart = False
    assert pod.st.evict({"slot": 0, "container_id": "c-0-0"})[0] == 202
    code, body = pod.st.verify({"slot": 0, "container_id": "c-0-new"})
    assert code == 409 and body["reason"] in ("no_hello", "same_boot"), body


def test_an_eviction_that_never_verifies_disables_the_slot_and_can_still_recover(tmp_path, monkeypatch):
    monkeypatch.setattr(slotrun.SlotSupervisor, "_kill_others", lambda self: None)
    monkeypatch.setattr(slotrun.SlotSupervisor, "_reap_orphans", lambda self: None)
    p = Pod(tmp_path, n=1, evict_timeout_seconds=0.5)
    try:
        p.all_free()
        p.slots[0].auto_restart = False
        p.st.evict({"slot": 0, "container_id": "c-0-0"})
        assert _wait(lambda: p.st.slots[0].state == poolslot.DISABLED, 10)
        assert p.st.slots[0].rec["error"] == "evict_timeout"
        p.slots[0].restart()
        assert p.st.verify({"slot": 0, "container_id": p.slots[0].container_id})[0] == 200
    finally:
        p.close()


def test_evict_is_refused_for_slots_in_use(pod):
    pod.bind()
    code, body = pod.st.evict({"slot": 0, "container_id": "x"})
    assert code == 409 and body["reason"] == "state"


# -- 重启过的槽位 ----------------------------------------------------------------------------------

def test_a_restarted_active_slot_is_treated_as_lost_and_goes_to_eviction(pod):
    pod.bind()
    pod.slots[0].restart()                  # 1 号进程崩溃重启:盘被新进程清空
    s = pod.st.slots[0]
    assert _wait(lambda: s.state == poolslot.EVICTING), pod.logs[-10:]
    assert s.rec["error"] == "restarted" and pod.owner() == {}, "独占要放掉,他才能到别处重新载入"
    assert pod.st.verify({"slot": 0, "container_id": pod.slots[0].container_id})[0] == 200


def test_a_restarted_free_slot_is_accepted_after_checking_the_wipe(pod):
    old = pod.st.slots[0].rec["boot"]
    pod.slots[0].restart()
    s = pod.st.slots[0]
    assert _wait(lambda: s.rec["boot"] != old and s.state == poolslot.FREE)


# -- 管家自己重启 ---------------------------------------------------------------------------------

def test_steward_restart_keeps_an_active_slot_active(pod):
    _c, b = pod.bind()
    st2 = pod.new_steward()
    s = st2.slots[0]
    assert _wait(lambda: s.ufd is not None), "恢复时重新打开本人的 NAS 目录"
    assert s.state == poolslot.ACTIVE and _healthy(b["opencode"]["port"], b["opencode"]["password"])
    assert s.sync_once()["snapshot_confirmed"]
    code, again = pod.bind(expect=1)
    assert code == 200 and again["opencode"]["password"] == b["opencode"]["password"], "网关重建缓存时能取回凭证"


def test_steward_restart_mid_retain_finishes_the_retain(pod):
    pod.bind()
    pod.st.slots[0]._set(state=poolslot.RETAINING)
    st2 = pod.new_steward()
    assert _wait(lambda: st2.slots[0].state == poolslot.RETAINED)
    assert not st2.slots[0].rec["dirty"] and pod.owner() == {}


def test_steward_restart_mid_load_never_writes_back_and_evicts(pod):
    (pod.users / "u1234" / "workspace").mkdir(parents=True)
    (pod.users / "u1234" / "workspace" / "keep.md").write_text("on nas", encoding="utf-8")
    pod.st.stop.set()
    with pod.st.lock:
        assert pod.st.slots[0].begin_bind("u1234", "c-0-0") is None   # 取了独占、还没载入就「崩了」
    st2 = pod.new_steward()
    assert _wait(lambda: st2.slots[0].state == poolslot.EVICTING)
    assert (pod.users / "u1234" / "workspace" / "keep.md").exists(), "半份载入不能回写(删除传播)"
    assert pod.owner() == {}


# -- 停止信号 ----------------------------------------------------------------------------------------

def test_sigterm_retains_every_active_slot_in_parallel(pod):
    assert pod.bind(slot=0, user="u1111")[0] == 200
    assert pod.bind(slot=1, user="u2222")[0] == 200
    t0 = time.monotonic()
    pod.st.on_sigterm()
    took = time.monotonic() - t0
    for i, u in ((0, "u1111"), (1, "u2222")):
        assert pod.st.slots[i].state == poolslot.RETAINED and not pod.st.slots[i].rec["dirty"]
        assert _rows(pod.users / u / "xdg-data" / "opencode" / "opencode.db") == 2 and pod.owner(u) == {}
    assert took < 2 * pod.settings.stop_wait_seconds


# -- 监视 ------------------------------------------------------------------------------------------

def test_busy_sessions_and_touch_feed_the_status(pod):
    pod.bind()
    (pod.tmp / "fake-0" / "busy").write_text("1", encoding="utf-8")
    assert _wait(lambda: pod.st.status()["slots"][0]["busy"])
    now = time.time()
    assert pod.st.touch({"activity": [[0, "u1234", now], [1, "u9999", now], ["x", 1, 2]]}) == (200, {"touched": 2})
    assert pod.st.slots[0].rec["last_active"] == pytest.approx(now)
    assert pod.st.slots[1].rec["last_active"] is None, "不在用的槽位不记活动"


# -- 外来监听者(单元级:非 root 造不出别的 uid) ------------------------------------------------------

@pytest.fixture()
def bare(tmp_path):
    """不起槽位进程的管家:只测状态机与监视的判断。槽位 uid 取 5000+i。"""
    verdicts = {}
    users, priv = tmp_path / "users", tmp_path / "priv"
    users.mkdir()
    for i in range(3):
        for sub in ("ctl", "state"):
            (tmp_path / "slots" / str(i) / sub).mkdir(parents=True)
    cfg = poolsteward.Config(key=KEY, pod=POD, slots=3, users_dir=users, slots_dir=tmp_path / "slots", priv_dir=priv,
                             port=_free_port(), settings=poolslot.Settings(pod=POD, hello_seconds=0.2,
                                                                           ready_seconds=0.5, stop_wait_seconds=0.3),
                             slot_ports=lambda i: (6100 + 10 * i, 6101 + 10 * i), slot_uid=lambda i: 5000 + i)
    logs = []
    st = poolsteward.Steward(cfg, log=logs.append, verdict=lambda port, uid: verdicts.get(port, "ok"))
    for s in st.slots:
        s._set(state=poolslot.FREE, boot="b%d" % s.i, quiet=True)
    yield st, verdicts, logs
    st.close()


def test_a_foreign_listener_is_flagged_and_traced_to_the_offending_slot(bare):
    st, verdicts, logs = bare
    verdicts[6100] = "foreign:5002"
    st.monitor_once()
    out = st.status()["slots"]
    assert out[0]["listener"] == "foreign:5002" and out[2]["offender"] and not out[1]["offender"]
    assert any("冒充" in m for m in logs)
    verdicts.clear()
    st.monitor_once()
    assert st.status()["slots"][0]["listener"] == "ok" and not st.status()["slots"][2]["offender"]


def test_bind_onto_a_squatted_port_fails_and_is_retained_not_served(bare):
    st, verdicts, _logs = bare
    verdicts[6100] = "foreign:5001"
    code, body = st.bind({"slot": 0, "user_id": "u1234", "expect_in_use": 0, "container_id": "c"})
    assert code in (502, 504) and st.slots[0].state == poolslot.RETAINED, body


STATE_MATRIX = [
    # (状态, 附加字段, 操作, 期望状态码, 期望原因)
    (poolslot.FREE, {}, "resume", 409, "state"),
    (poolslot.FREE, {}, "retain", 409, "state"),
    (poolslot.FREE, {}, "verify", 409, "state"),
    (poolslot.ACTIVE, {}, "bind_other", 409, "state"),
    (poolslot.ACTIVE, {}, "resume_other", 409, "state"),
    (poolslot.ACTIVE, {}, "retain_other", 409, "user_mismatch"),
    (poolslot.ACTIVE, {}, "evict", 409, "state"),
    (poolslot.ACTIVE, {}, "disable", 409, "state"),
    (poolslot.RETAINED, {}, "bind_other", 409, "state"),
    (poolslot.RETAINED, {}, "retain", 409, "state"),
    (poolslot.RETAINED, {}, "resume_other", 409, "user_mismatch"),
    (poolslot.RETAINED, {"stale": True}, "resume", 409, "stale"),
    (poolslot.RETAINED, {"container_id": "c-old"}, "resume", 409, "restarted"),
    (poolslot.RETAINED, {"dirty": True}, "evict", 409, "not_synced"),
    (poolslot.RETAINING, {}, "evict", 409, "state"),
    (poolslot.RETAINING, {}, "disable", 409, "state"),
    (poolslot.EVICTING, {"evict": {"boot": "b0", "container_id": "c", "at": 0}}, "bind", 409, "state"),
    (poolslot.EVICTING, {"evict": {"boot": "b0", "container_id": "c", "at": 0}}, "resume", 409, "state"),
    (poolslot.EVICTING, {"evict": {"boot": "b0", "container_id": "c", "at": 0}}, "evict", 409, "state"),
    (poolslot.EVICTING, {"evict": {"boot": "b0", "container_id": "c", "at": 0}}, "verify_same", 409, "not_restarted"),
    (poolslot.DISABLED, {}, "verify", 409, "state"),
    (poolslot.DISABLED, {}, "bind", 409, "state"),
    (poolslot.BOOTING, {}, "bind", 409, "state"),
    (poolslot.BOOTING, {}, "evict", 409, "state"),
]


@pytest.mark.parametrize("state,extra,op,code,reason", STATE_MATRIX)
def test_state_matrix(bare, state, extra, op, code, reason):
    st, _v, _l = bare
    st.slots[0]._set(**dict({"state": state, "user": "u1234", "container_id": "c"}, **extra))
    in_use = st.in_use()
    calls = {
        "bind": lambda: st.bind({"slot": 0, "user_id": "u1234", "expect_in_use": in_use}),
        "bind_other": lambda: st.bind({"slot": 0, "user_id": "u5555", "expect_in_use": in_use}),
        "resume": lambda: st.resume({"slot": 0, "user_id": "u1234", "expect_in_use": in_use, "container_id": "c"}),
        "resume_other": lambda: st.resume({"slot": 0, "user_id": "u5555", "expect_in_use": in_use}),
        "retain": lambda: st.retain({"slot": 0, "user_id": "u1234"}),
        "retain_other": lambda: st.retain({"slot": 0, "user_id": "u5555"}),
        "evict": lambda: st.evict({"slot": 0, "container_id": "c"}),
        "verify": lambda: st.verify({"slot": 0, "container_id": "c-new"}),
        "verify_same": lambda: st.verify({"slot": 0, "container_id": "c"}),
        "disable": lambda: st.disable({"slot": 0, "reason": "ops"}),
    }
    got_code, body = calls[op]()
    assert (got_code, body.get("reason")) == (code, reason), body
    assert st.slots[0].state == state, "被拒绝的操作不能改状态"


# -- 控制接口 --------------------------------------------------------------------------------------

def _call(st, method, path, body=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
    hdrs = headers if headers is not None else poolauth.sign_request(KEY, method, path, POD, data)
    c = http.client.HTTPConnection("127.0.0.1", st.cfg.port, timeout=10)
    c.request(method, path, body=data, headers=dict(hdrs, **{"Content-Type": "application/json"}))
    r = c.getresponse()
    out = r.read()
    sig = r.getheader(poolauth.HDR_RESP_SIG) or ""
    c.close()
    return r.status, (json.loads(out) if out else None), hdrs, out, sig


def test_control_api_is_signed_both_ways_and_refuses_replays(bare):
    st, _v, _l = bare
    srv = poolsteward.make_server(st, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert _call(st, "GET", "/healthz", headers={})[:2] == (200, {"ok": True})
        assert _call(st, "GET", "/pool/status", headers={})[:2] == (401, {})
        code, body, hdrs, raw, sig = _call(st, "GET", "/pool/status")
        assert code == 200 and len(body["slots"]) == 3
        assert poolauth.check_response(KEY, POD, hdrs[poolauth.HDR_NONCE], code, raw, sig), "应答要带管家的签名"
        assert _call(st, "GET", "/pool/status", headers=hdrs)[0] == 401, "同一个签名不能重放"
        other = poolauth.sign_request(KEY, "GET", "/pool/status", "pool-9", b"")
        assert _call(st, "GET", "/pool/status", headers=other)[0] == 401, "签给别的 Pod 的不认"
        code, body, *_ = _call(st, "POST", "/pool/disable", {"slot": 1, "reason": "ops"})
        assert code == 200 and st.slots[1].state == poolslot.DISABLED
        assert _call(st, "POST", "/pool/bind", raw=b"x" * (poolsteward.MAX_BODY + 1), headers={})[0] == 413
        assert _call(st, "POST", "/pool/nope", {})[0] == 404
    finally:
        srv.shutdown()


def test_steward_port_squatted_by_a_slot_evicts_that_slot(bare, monkeypatch):
    st, _v, logs = bare
    calls = []

    def fake_make_server(_st):
        calls.append(1)
        if len(calls) == 1:
            raise OSError(98, "Address already in use")
        return "srv"
    monkeypatch.setattr(poolsteward, "make_server", fake_make_server)
    monkeypatch.setattr(poolsteward.netcheck, "listeners",
                        lambda port, files: [poolsteward.netcheck.Listener("0", port, 5001, 1)])
    assert poolsteward.bind_server(st, attempts=3, wait_s=0) == "srv"
    assert st.slots[1].state == poolslot.EVICTING and st.slots[1].rec["error"] == "squatted_steward_port"
    run = json.loads((st.slots[1].ctl_dir / "run.json").read_text(encoding="utf-8"))
    assert run == {"state": "exit", "boot": "b1"}, "按可信的 boot 发 exit"


def test_ctl_files_are_group_readable_and_status_never_has_secrets(bare):
    st, _v, _l = bare
    s = st.slots[0]
    s._set(state=poolslot.ACTIVE, user="u1234", password="pw-secret-123456789", reports_token="rt-secret-12345678",
           since=1.0)
    s._write_run("run", "grmp-secret")
    assert oct((s.ctl_dir / "run.json").stat().st_mode & 0o777) == oct(poolslot.CTL_MODE)
    text = json.dumps(st.status())
    assert "pw-secret" not in text and "rt-secret" not in text and "grmp-secret" not in text
    assert oct((st.cfg.priv_dir / "slot-0.json").stat().st_mode & 0o777) == oct(0o600), "口令只落在管家私有目录"


def test_config_from_env():
    c = poolsteward.Config.from_env({"SANDBOX_CONTROL_KEY": "k" * 32, "POD_NAME": "pool-3", "SLOTS_PER_POD": "4",
                                     "STATE_SYNC_SECONDS": "30"})
    assert c.pod == "pool-3" and c.slots == 4 and c.settings.sync_seconds == 30 and c.settings.pod == "pool-3"
    assert c.slot_uid(3) == 2003 and c.slot_ports(1) == (4110, 4111)
    for bad in ({}, {"SANDBOX_CONTROL_KEY": "short"}, {"SANDBOX_CONTROL_KEY": "k" * 32, "SLOTS_PER_POD": "0"},
                {"SANDBOX_CONTROL_KEY": "k" * 32, "SLOTS_PER_POD": "x"}):
        with pytest.raises(poolsteward.ConfigError):
            poolsteward.Config.from_env(bad)
