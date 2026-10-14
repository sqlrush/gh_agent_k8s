"""池化副容器:控制接口与绑定状态机(方案 §5–§7、§9)。

同一个 Pod 里的两个容器在这里各是一个线程:副容器是 poolctl.Sandbox + HTTP,主容器是真的 poolrun 守护进程
驱动假 opencode(tests/fake_opencode.py);两者只经 ctl 目录与 state 卷上的回执交互,与线上一致。
"""
import base64
import json
import os
import pathlib
import socket
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "docker"))

import poolctl  # noqa: E402
import poolrun  # noqa: E402

FAKE = pathlib.Path(__file__).with_name("fake_opencode.py")
KEY = "k" * 32


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _rows(path: pathlib.Path) -> int:
    con = sqlite3.connect(path)
    try:
        return con.execute("select count(*) from t").fetchone()[0]
    finally:
        con.close()


class Main:
    """主容器:真的 poolrun 守护进程跑在后台线程里。"""

    def __init__(self, tmp: pathlib.Path, ctl: pathlib.Path, state: pathlib.Path, port: int):
        self.out = tmp / "fake-out"
        cfg = poolrun.Config(ctl_dir=ctl, state=state, kb_dir=tmp / "kb",
                             opencode_cmd=[sys.executable, str(FAKE)],
                             podctl_cmd=[sys.executable, str(_ROOT / "docker" / "podctl.py")],
                             port=port, reports_port=_free_port(), poll_s=0.05, stop_timeout_s=5,
                             health_every_s=5, env_file=None)
        (tmp / "kb").mkdir(exist_ok=True)
        env = dict(os.environ, FAKE_OUT=str(self.out), GRMP_API_HOST="10.0.0.1")
        self.sup = poolrun.Supervisor(cfg, env=env, log=lambda _m: None)
        self._halt = threading.Event()
        self.t = threading.Thread(target=self._loop, daemon=True)
        self.t.start()

    def _loop(self):
        while not self._halt.is_set():
            self.sup.tick()
            time.sleep(0.05)

    def kill(self):
        """主容器失联:守护进程不再处理指令,opencode 也没了。"""
        self._halt.set()
        self.t.join(5)
        if self.sup.oc is not None and self.sup.oc.poll() is None:
            self.sup.oc.kill()
            self.sup.oc.wait(5)
        if self.sup.reports is not None and self.sup.reports.poll() is None:
            self.sup.reports.kill()

    def sigterm(self):
        self._halt.set()
        self.t.join(5)
        self.sup.term = True
        self.sup.tick()


class Rig:
    def __init__(self, tmp: pathlib.Path, main: bool = True, users: pathlib.Path = None, **over):
        self.tmp = tmp
        tmp.mkdir(parents=True, exist_ok=True)
        self.users, self.state, self.ctl, self.priv = (tmp / n for n in ("users", "state", "ctl", "priv"))
        if users is not None:
            self.users = users                          # 同一块 NAS,另一个沙箱
        for p in (self.users, self.state, self.ctl):
            p.mkdir(exist_ok=True)
        self.oc_port = _free_port()
        self.main = Main(tmp, self.ctl, self.state, self.oc_port) if main else None
        opts = dict(sync_seconds=3600, ready_seconds=10, stop_wait_seconds=5, snap_wait_seconds=5,
                    owner_refresh_seconds=3600)
        opts.update(over)
        self.cfg = poolctl.Config(key=KEY, sandbox_id="sandbox-07", pod="sandbox-07", users_dir=self.users,
                                  state_dir=self.state, ctl_dir=self.ctl, priv_dir=self.priv, port=_free_port(),
                                  opencode_url="http://127.0.0.1:%d" % self.oc_port, **opts)
        self.logs = []
        self.sb = poolctl.Sandbox(self.cfg, log=self.logs.append)
        self.srv = poolctl.make_server(self.sb, self.cfg, host="127.0.0.1")
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.cfg.port

    def call(self, method, path, body=None, key=KEY):
        data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if key is not None:
            req.add_header("X-Sandbox-Key", key)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def bind(self, user="u1234"):
        return self.call("POST", "/sandbox/bind", {"user_id": user})

    def unbind(self, user="u1234", reason="logout"):
        return self.call("POST", "/sandbox/unbind", {"user_id": user, "reason": reason})

    def oc(self, path, password):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.oc_port, path))
        req.add_header("Authorization", "Basic " + base64.b64encode(("opencode:" + password).encode()).decode())
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status

    def close(self):
        self.srv.shutdown()
        if self.main is not None:
            self.main.kill()


@pytest.fixture()
def rig(tmp_path):
    r = Rig(tmp_path)
    yield r
    r.close()


def _me(rig) -> pathlib.Path:
    return rig.users / "u1234"


# --- 鉴权与校验 -----------------------------------------------------------------

def test_healthz_needs_no_key_and_says_nothing_about_users(rig):
    rig.bind()
    code, body = rig.call("GET", "/healthz", key=None)
    assert code == 200 and body == {"ok": True}


@pytest.mark.parametrize("key", [None, "", "wrong", KEY[:-1]])
def test_wrong_or_missing_key_is_a_bare_401(rig, key):
    assert rig.call("GET", "/sandbox/status", key=key) == (401, {})
    assert rig.call("POST", "/sandbox/bind", {"user_id": "u1234"}, key=key) == (401, {})
    assert rig.sb.state == "idle"


@pytest.mark.parametrize("uid", ["../u9999", "a/b", "", None, 42, "-x"])
def test_bad_user_id_is_400_and_the_sandbox_stays_idle(rig, uid):
    code, body = rig.bind(uid)
    assert code == 400 and body["reason"] == "bad_user_id" and rig.sb.state == "idle"


def test_oversized_or_malformed_bodies_are_rejected(rig):
    req = urllib.request.Request(rig.base + "/sandbox/bind", data=b"x" * 5000, method="POST")
    req.add_header("X-Sandbox-Key", KEY)
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=5)
    assert ei.value.code == 413
    req = urllib.request.Request(rig.base + "/sandbox/bind", data=b"[1,2]", method="POST")
    req.add_header("X-Sandbox-Key", KEY)
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=5)
    assert ei.value.code == 400


def test_config_refuses_to_start_without_a_control_key():
    with pytest.raises(poolctl.ConfigError):
        poolctl.Config.from_env({})
    with pytest.raises(poolctl.ConfigError):
        poolctl.Config.from_env({"SANDBOX_CONTROL_KEY": "short"})


# --- 绑定 -----------------------------------------------------------------------

def test_bind_loads_starts_opencode_and_returns_a_fresh_password(rig):
    (_me(rig) / "workspace").mkdir(parents=True)
    (_me(rig) / "workspace" / "old.sql").write_text("select 1", encoding="utf-8")
    code, body = rig.bind("U1234")                      # 先转小写
    assert code == 200 and body["state"] == "bound" and body["user_id"] == "u1234"
    pw = body["opencode"]["password"]
    assert len(pw) >= 24 and rig.oc("/global/health", pw) == 200
    assert body["loaded"]["files"] == 1 and body["loaded"]["unclean_last_time"] is False
    assert (rig.state / "workspace" / "old.sql").exists()
    assert (_me(rig) / ".state-dirty").exists() and json.loads((_me(rig) / ".owner").read_text())["pod"] == "sandbox-07"
    status = rig.call("GET", "/sandbox/status")[1]
    assert status["state"] == "bound" and status["user_id"] == "u1234"
    assert pw not in json.dumps(status), "状态接口不返回口令"
    env = json.loads((rig.main.out / "env.json").read_text(encoding="utf-8"))
    assert env["GSDB_USER_ID"] == "u1234" and env["SANDBOX_CONTROL_KEY"] is None


def test_bind_is_idempotent_for_the_same_user_and_refuses_others(rig):
    c1, b1 = rig.bind()
    c2, b2 = rig.bind()
    assert c1 == c2 == 200 and b1["opencode"]["password"] == b2["opencode"]["password"]
    code, body = rig.bind("u5678")
    assert code == 409 and body == {"reason": "state", "state": "bound"}


def test_owner_busy_is_409_with_holder_and_retry_after_and_the_sandbox_stays_idle(rig):
    _me(rig).mkdir()
    (_me(rig) / ".owner").write_text(json.dumps({"pod": "sandbox-03", "ts": time.time() - 30}), encoding="utf-8")
    code, body = rig.bind()
    assert code == 409 and body["reason"] == "owner_busy" and body["holder"] == "sandbox-03"
    assert 55 <= body["retry_after"] <= 61 and body["state"] == "idle"
    assert rig.sb.state == "idle" and not (rig.ctl / "run.json").exists()
    code, body = rig.bind("u5678")                      # 这个沙箱还能分给别人
    assert code == 200 and body["user_id"] == "u5678"


@pytest.mark.skipif(os.geteuid() == 0, reason="root 不受目录权限限制")
def test_unwritable_users_dir_is_500_with_the_fix_and_retires(rig):
    rig.users.chmod(0o555)
    try:
        code, body = rig.bind()
    finally:
        rig.users.chmod(0o755)
    assert code == 500 and body["reason"] == "users_not_writable" and "chown -R 1000:1000" in body["message"]
    assert rig.sb.state == "retired"


def test_opencode_not_ready_is_504_and_retires_releasing_the_owner(tmp_path):
    r = Rig(tmp_path, main=False, ready_seconds=1, stop_wait_seconds=1)
    try:
        code, body = r.bind()
        assert code == 504 and body["reason"] == "opencode_not_ready" and body["state"] == "retired"
        assert not (r.users / "u1234" / ".owner").exists(), "退役时要释放独占,用户才能马上换一个沙箱"
        assert r.bind()[0] == 409
    finally:
        r.close()


# --- 定期回写 -------------------------------------------------------------------

def test_periodic_sync_pushes_files_and_a_fresh_snapshot(rig):
    pw = rig.bind()[1]["opencode"]["password"]
    assert rig.oc("/global/health", pw) == 200
    (rig.state / "workspace" / "new.sql").write_text("select 2", encoding="utf-8")
    con = sqlite3.connect(rig.state / "xdg-data" / "opencode" / "opencode.db")
    con.execute("insert into t values (9)")
    con.commit()
    con.close()
    last = rig.sb.sync_once()
    assert last["snapshot_confirmed"] and last["dbs"] == 1
    assert (_me(rig) / "workspace" / "new.sql").read_text(encoding="utf-8") == "select 2"
    assert _rows(_me(rig) / "xdg-data" / "opencode" / "opencode.db") == 2
    assert rig.call("GET", "/sandbox/status")[1]["last_sync"]["dbs"] == 1


def test_a_silent_main_container_does_not_cost_the_nas_db(rig):
    """主容器不给快照(失联)时,定期回写不能把 NAS 上的库当成已删。"""
    _mkdb_path = _me(rig) / "xdg-data" / "opencode" / "opencode.db"
    _mkdb_path.parent.mkdir(parents=True)
    con = sqlite3.connect(_mkdb_path)
    con.execute("create table t (i integer)")
    con.executemany("insert into t values (?)", [(i,) for i in range(6)])
    con.commit()
    con.close()
    rig.bind()
    rig.main.kill()
    rig.sb.cfg.snap_wait_seconds = 0.5
    last = rig.sb.sync_once()
    assert last["snapshot_confirmed"] is False
    assert _rows(_mkdb_path) == 6


# --- 解绑 -----------------------------------------------------------------------

def test_unbind_with_the_wrong_user_is_409(rig):
    rig.bind()
    code, body = rig.unbind("u5678")
    assert code == 409 and body["reason"] == "user_mismatch" and rig.sb.state == "bound"


def test_unbind_aborts_running_turns_persists_the_last_words_and_retires(rig):
    pw = rig.bind()[1]["opencode"]["password"]
    assert rig.oc("/global/health", pw) == 200
    (rig.main.out / "busy").write_text("1", encoding="utf-8")
    code, body = rig.unbind()
    assert code == 200 and body["state"] == "retired" and body["aborted_running"] is True
    assert (rig.main.out / "aborted").exists()
    assert _rows(_me(rig) / "xdg-data" / "opencode" / "opencode.db") == 2, "停之前才落库的那一行要在 NAS 上"
    assert body["backup"] and (_me(rig) / "backup" / body["backup"]).exists()
    assert not (_me(rig) / ".state-dirty").exists() and not (_me(rig) / ".owner").exists()
    assert rig.bind()[1] == {"reason": "state", "state": "retired"}, "退役的沙箱谁都不许再绑"
    assert rig.bind("u5678")[0] == 409


def test_unbind_when_the_main_container_never_confirms_is_500_and_keeps_dirty(rig):
    rig.bind()
    rig.sb.sync_once()                                  # 有一份定期快照
    rig.main.kill()
    code, body = rig.unbind()
    assert code == 500 and body["reason"] == "stop_not_confirmed" and body["state"] == "retired"
    assert (_me(rig) / ".state-dirty").exists(), "没确认停止 = 可能丢了最后一段,下次绑定要按非正常退出处理"
    assert not (_me(rig) / ".owner").exists()
    assert _rows(_me(rig) / "xdg-data" / "opencode" / "opencode.db") == 1


def test_next_bind_after_an_unconfirmed_stop_reports_unclean_last_time(tmp_path):
    r1 = Rig(tmp_path / "a")
    try:
        r1.bind()
        r1.sb.sync_once()
        r1.main.kill()
        r1.unbind()
    finally:
        r1.close()
    r2 = Rig(tmp_path / "b", users=r1.users)
    try:
        code, body = r2.bind()
        assert code == 200 and body["loaded"]["unclean_last_time"] is True
        assert list((r1.users / "u1234").glob(".unclean-*"))
    finally:
        r2.close()


# --- 独占丢失、副容器重启、停止信号 -------------------------------------------------

def test_losing_the_owner_mark_stops_writing_and_retires(rig):
    rig.bind()
    (_me(rig) / ".owner").write_text(json.dumps({"pod": "sandbox-99", "ts": time.time()}), encoding="utf-8")
    assert rig.sb.refresh_owner() is False
    assert rig.sb.state == "retired"
    assert json.loads((rig.ctl / "run.json").read_text())["state"] == "stop"
    assert rig.sb.sync_once() is None, "被接管之后一个字节都不能再往 NAS 写"
    assert json.loads((_me(rig) / ".owner").read_text())["pod"] == "sandbox-99", "不能删别人的标记"


def test_sidecar_restart_recovers_the_binding_and_the_snapshot_sequence(rig):
    pw = rig.bind()[1]["opencode"]["password"]
    rig.sb.sync_once()
    rig.sb.sync_once()
    sb2 = poolctl.Sandbox(rig.cfg, log=lambda _m: None)
    assert sb2.state == "bound" and sb2.user == "u1234" and sb2.password == pw
    assert sb2.snap_seq == 2, "序号从 ctl 里接上,否则主容器会把新请求当旧的忽略"
    assert sb2.sync_once()["snapshot_confirmed"]
    sb2._stop_loops.set()


def test_sidecar_restart_during_binding_retires(rig):
    rig.cfg.priv_dir.mkdir(exist_ok=True)
    _me(rig).mkdir()
    (rig.cfg.priv_dir / "state.json").write_text(json.dumps({"state": "binding", "user_id": "u1234",
                                                            "started": False}), encoding="utf-8")
    (_me(rig) / ".owner").write_text(json.dumps({"pod": "sandbox-07", "ts": time.time()}), encoding="utf-8")
    sb2 = poolctl.Sandbox(rig.cfg, log=lambda _m: None)
    assert sb2.state == "retired" and not (_me(rig) / ".owner").exists()


def test_a_failure_inside_the_finish_still_answers_and_retires(rig):
    """10-14 e2e:收尾前本人 NAS 目录被删,退出备份抛 FileNotFoundError,处理线程直接死掉——
    平台收到断开的连接,沙箱卡在「解绑中」、独占一直攥着。必须回 500 并退役。"""
    import shutil
    rig.bind()
    shutil.rmtree(_me(rig))
    code, body = rig.unbind()
    assert code == 500 and body["state"] == "retired" and rig.sb.state == "retired"


def test_sigterm_during_an_unbind_waits_for_it_to_finish(rig):
    """平台在 unbind 没回应时去删 Pod:SIGTERM 落在「解绑中」。进程要等收尾做完再退出。"""
    rig.bind()
    rig.main.kill()                                   # 让收尾卡在停止握手上(stop_wait 5 秒)
    t = threading.Thread(target=rig.unbind)
    t.start()
    assert rig.sb._wait(lambda: rig.sb.state == "unbinding", 5)
    t0 = time.monotonic()
    rig.sb.on_sigterm()
    assert rig.sb.state == "retired", "on_sigterm 不能在收尾做完之前返回"
    assert time.monotonic() - t0 > 1
    assert not (_me(rig) / ".owner").exists()
    t.join(10)


def test_pod_deletion_is_equivalent_to_unbind(rig):
    """直接删 Pod:两个容器同时收到 SIGTERM,没有先后。副容器等主容器的最终回执再收尾。"""
    rig.bind()
    t = threading.Thread(target=rig.main.sigterm)
    t.start()
    rig.sb.on_sigterm()
    t.join(10)
    assert rig.sb.state == "retired"
    assert _rows(_me(rig) / "xdg-data" / "opencode" / "opencode.db") == 2
    assert not (_me(rig) / ".state-dirty").exists() and not (_me(rig) / ".owner").exists()
