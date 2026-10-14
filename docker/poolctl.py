"""池化沙箱的副容器:4098 控制接口与绑定状态机(方案 §5–§7)。

    空闲 ──bind──▶ 绑定中 ──载入完成、opencode 就绪──▶ 使用中 ──unbind / SIGTERM──▶ 解绑中 ──▶ 已退役
      ▲ │                    └─失败或超时──────────────────────────────────────────────▶ 已退役
      └─┘ bind 时该工号的目录被别的沙箱持有:409 owner_busy,保持空闲(还没碰任何人的数据)

**一个沙箱一生只服务一个人**:进入过「绑定中」就不再回到空闲,失败也直接退役(用完即换,方案 §3.2)。

这里挂着全部用户目录,所以:
  · 控制密钥只注入本容器,常量时间比较;不对回 401 不说原因;/healthz 不带任何用户信息;
  · 身份只认接口传进来的工号,**从不从共享卷读身份**;
  · 对 state 卷与 NAS 的一切读写都经 poolsync/fdtree(按文件描述符、不跟随链接);绑定之后不用 SQLite 打开 state 卷。

主容器那一侧见 poolrun.py:这里写 ctl 的 run.json / snap.json,读 state 卷上的 .pool/ack.json(当作不可信输入)。
"""
from __future__ import annotations

import base64
import hmac
import http.server
import json
import os
import pathlib
import secrets
import signal
import socket
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, Mapping, Optional, Tuple

import fdtree
import podctl
import poolsync

RUN_FILE = "run.json"
SNAP_FILE = "snap.json"
PRIV_STATE = "state.json"
ACK_REL = ("." + "pool", "ack.json")      # state 卷上的 .pool/ack.json
MAX_BODY = 4096

Reply = Tuple[int, dict]


class ConfigError(ValueError):
    pass


@dataclass
class Config:
    key: str
    sandbox_id: str
    pod: str
    users_dir: pathlib.Path = pathlib.Path("/pool/users")
    state_dir: pathlib.Path = pathlib.Path("/pool/state")
    ctl_dir: pathlib.Path = pathlib.Path("/pool/ctl")
    priv_dir: pathlib.Path = pathlib.Path("/pool/priv")
    port: int = 4098
    opencode_url: str = "http://127.0.0.1:4096"
    sync_seconds: float = 60.0          # 定期回写周期(与按需模式 STATE_SYNC_SECONDS 同一个旋钮)
    ready_seconds: float = 60.0         # bind 等 opencode 就绪的时限
    stop_wait_seconds: float = 40.0     # 解绑等主容器最终回执的时限(解绑总时限 50 秒之内)
    snap_wait_seconds: float = 20.0     # 定期回写前等快照的时限
    owner_refresh_seconds: float = 30.0

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Config":
        key = env.get("SANDBOX_CONTROL_KEY", "")
        if len(key) < 16:
            raise ConfigError("缺少 SANDBOX_CONTROL_KEY 或不足 16 个字符(共享 Secret sandbox-control,只注入副容器)")
        host = socket.gethostname()
        return cls(key=key, sandbox_id=env.get("SANDBOX_ID") or host, pod=env.get("POD_NAME") or host,
                   sync_seconds=float(env.get("STATE_SYNC_SECONDS", "60")),
                   ready_seconds=float(env.get("POOL_READY_SECONDS", "60")),
                   stop_wait_seconds=float(env.get("POOL_STOP_WAIT_SECONDS", "40")))


class Sandbox:
    def __init__(self, cfg: Config, log: Callable[[str], None] = print):
        self.cfg = cfg
        self.log = log
        self.lock = threading.Lock()            # 只护状态迁移,不在锁里做长操作
        self.sync_lock = threading.Lock()       # 定期回写与收尾互斥
        self.state = "idle"
        self.user: Optional[str] = None
        self.password: Optional[str] = None
        self.since: Optional[float] = None
        self.bound_at: Optional[float] = None
        self.started = False                    # 已给主容器下过 run 指令
        self.loaded: dict = {}
        self.last_sync_at: Optional[float] = None
        self.last_sync: Optional[dict] = None
        self.snap_seq = 0
        self.snap_seen: Dict[str, poolsync.Ident] = {}
        self.user_fd: Optional[int] = None
        self._stop_loops = threading.Event()
        self.users_fd = fdtree.open_root(cfg.users_dir)
        self.state_fd = fdtree.open_root(cfg.state_dir)
        cfg.priv_dir.mkdir(parents=True, exist_ok=True)
        self._recover()

    # -- 私有目录:副容器重启后知道自己绑定的是谁 ----------------------------------

    def _persist(self) -> None:
        podctl._write_private(self.cfg.priv_dir / PRIV_STATE, json.dumps({
            "state": self.state, "user_id": self.user, "password": self.password, "since": self.since,
            "bound_at": self.bound_at, "started": self.started}))

    def _recover(self) -> None:
        try:
            saved = json.loads((self.cfg.priv_dir / PRIV_STATE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        snap = self._read_json(self.cfg.ctl_dir / SNAP_FILE)
        self.snap_seq = int(snap.get("seq", 0)) if snap else 0      # 主容器已处理到的序号不会倒回去
        st = saved.get("state")
        self.user, self.password = saved.get("user_id"), saved.get("password")
        self.since, self.bound_at, self.started = saved.get("since"), saved.get("bound_at"), bool(saved.get("started"))
        if st in ("idle", None):
            return
        if st == "retired" or not self.user:
            self.state = "retired"
            return
        try:
            self.user_fd = poolsync.open_user(self.users_fd, self.user)
        except (poolsync.PoolError, ValueError, OSError) as exc:
            self.log("副容器重启:打不开 users/%s(%s),退役" % (self.user, exc))
            self._retire_quietly()
            return
        if not poolsync.owner_refresh(self.user_fd, self.cfg.pod):
            self.log("副容器重启:%s 的独占标记已被别的沙箱接管,不再回写,退役" % self.user)
            self._retire_quietly(stop_main=True)
            return
        if st == "bound":
            self.state = "bound"
            self.log("副容器重启:恢复为「使用中」(%s),继续定期回写" % self.user)
            self._start_owner_refresher()
            self._start_loops()
            return
        self.log("副容器重启时处在「%s」,收尾后退役" % st)   # binding / unbinding
        self.state = "unbinding"
        self._finish("recover")

    def _retire_quietly(self, stop_main: bool = False) -> None:
        if stop_main:
            self._write_run("stop")
        self.state = "retired"
        self._persist()

    # -- ctl 与回执 -------------------------------------------------------------

    @staticmethod
    def _read_json(path: pathlib.Path) -> Optional[dict]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _write_run(self, state: str) -> None:
        podctl._write_private(self.cfg.ctl_dir / RUN_FILE, json.dumps({
            "state": state, "since": self.since, "user_id": self.user, "password": self.password}))

    def _ack(self) -> dict:
        """主容器写的回执。**不可信**:按描述符读、限大小,读不出来就当没有。"""
        try:
            d = fdtree.open_path(self.state_fd, ACK_REL[0], create=False)
        except fdtree.UnsafePath:
            return {}
        if d is None:
            return {}
        try:
            raw = fdtree.read_small(d, ACK_REL[1], limit=65536)
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (fdtree.UnsafePath, UnicodeDecodeError, ValueError, OSError):
            data = {}
        finally:
            os.close(d)
        return data if isinstance(data, dict) else {}

    def _wait(self, cond: Callable[[], bool], seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while True:
            if cond():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.2)

    def _opencode_ready(self) -> bool:
        req = urllib.request.Request(self.cfg.opencode_url + "/global/health")
        req.add_header("Authorization", "Basic " + base64.b64encode(
            ("opencode:%s" % self.password).encode()).decode())
        try:
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.status == 200
        except OSError:
            return False

    # -- 绑定 -------------------------------------------------------------------

    def _bound_reply(self) -> Reply:
        return 200, {"sandbox": self.cfg.sandbox_id, "state": "bound", "user_id": self.user,
                     "opencode": {"port": 4096, "username": "opencode", "password": self.password},
                     "reports_port": 4097, "loaded": self.loaded}

    def bind(self, user_id: object) -> Reply:
        uid = user_id.strip().lower() if isinstance(user_id, str) else ""
        if not poolsync.USER_ID_RE.match(uid):
            return 400, {"reason": "bad_user_id", "message": "工号格式不对"}
        with self.lock:
            if self.state == "bound" and self.user == uid:
                return self._bound_reply()          # 对同一工号幂等
            if self.state != "idle":
                return 409, {"reason": "state", "state": self.state}
            try:
                ufd = poolsync.open_user(self.users_fd, uid)
            except poolsync.PoolError as exc:
                self.state = "retired"
                self._persist()
                self.log("绑定 %s 失败:%s" % (uid, exc))
                return 500, {"reason": exc.reason, "message": str(exc), "state": "retired"}
            ok, holder, retry = poolsync.owner_acquire(ufd, self.cfg.pod)
            if not ok:
                os.close(ufd)
                return 409, {"reason": "owner_busy", "holder": holder, "retry_after": retry, "state": "idle"}
            self.state, self.user, self.user_fd = "binding", uid, ufd
            self._persist()
        self.log("绑定 %s:开始载入" % uid)
        phase = "load"
        try:
            moved, unclean = poolsync.load(ufd, self.state_fd, log=self.log)
            check = poolsync.selfcheck(self.cfg.state_dir, ufd, self.state_fd, log=self.log)
            poolsync.mark_dirty(ufd)
            phase = "start"
            self.password, self.since, self.started = secrets.token_urlsafe(24), time.time(), True
            self._write_run("run")
            self._persist()
            self._start_owner_refresher()
            if not self._wait(self._opencode_ready, self.cfg.ready_seconds):
                return self._fail_bind(504, "opencode_not_ready",
                                       "opencode 未在 %d 秒内就绪" % self.cfg.ready_seconds)
        except Exception as exc:                # noqa: BLE001  任何意外都要走退役,不能卡在「绑定中」还攥着独占
            return self._fail_bind(500, "load_failed" if phase == "load" else "start_failed", str(exc))
        self.loaded = dict(moved.to_json(), unclean_last_time=unclean, selfcheck=check)
        with self.lock:
            self.state, self.bound_at = "bound", time.time()
            self._persist()
        self._start_loops()
        self.log("绑定 %s 完成:%s" % (uid, self.loaded))
        return self._bound_reply()

    def _fail_bind(self, code: int, reason: str, message: str) -> Reply:
        self.log("绑定 %s 失败(%s):%s。沙箱退役" % (self.user, reason, message))
        with self.lock:
            self.state = "unbinding"
        self._finish("bind_failed")
        return code, {"reason": reason, "message": message, "state": "retired"}

    # -- 解绑 -------------------------------------------------------------------

    def unbind(self, user_id: object, reason: object = "") -> Reply:
        uid = user_id.strip().lower() if isinstance(user_id, str) else ""
        with self.lock:
            if self.state != "bound":
                return 409, {"reason": "state", "state": self.state}
            if uid != self.user:
                return 409, {"reason": "user_mismatch", "message": "工号与当前绑定的不一致"}
            self.state = "unbinding"
            self._persist()
        self.log("解绑 %s(原因 %s)" % (self.user, reason if isinstance(reason, str) else "?"))
        return self._finish(reason if isinstance(reason, str) else "")

    def _finish(self, why: str) -> Reply:
        """停止握手 → 收尾回写 → 退出备份 → 清 dirty → 释放独占 → 退役(方案 §6.2)。"""
        self._stop_loops.set()
        with self.sync_lock:
            ack: dict = {}
            confirmed = False
            if self.started:
                self._write_run("stop")
                confirmed = self._wait(lambda: self._final_ack(ack), self.cfg.stop_wait_seconds)
            moved = poolsync.Moved()
            name = None
            if self.user_fd is not None and (confirmed or self.started):
                moved = poolsync.sync(self.user_fd, self.state_fd, self.snap_seen, may_delete=True)
                name = poolsync.backup(self.user_fd, self.state_fd)
            clean = (confirmed or not self.started) and not moved.errors
            if self.user_fd is not None:
                if clean:
                    poolsync.clear_dirty(self.user_fd)
                poolsync.owner_release(self.user_fd, self.cfg.pod)
                os.close(self.user_fd)
                self.user_fd = None
            with self.lock:
                self.state = "retired"
                self._persist()
        body = {"sandbox": self.cfg.sandbox_id, "state": "retired", "user_id": self.user,
                "aborted_running": bool(ack.get("aborted_running")), "synced": moved.to_json(), "backup": name}
        if clean:
            self.log("收尾完成(%s):%s" % (why, body["synced"]))
            return 200, body
        if self.started and not confirmed:
            body.update(reason="stop_not_confirmed",
                        message="主容器 %d 秒内没确认停止;已回写最近一次定期快照,dirty 标记保留"
                                % self.cfg.stop_wait_seconds)
        else:
            body.update(reason="sync_errors", message="; ".join(moved.errors[:3]))
        self.log("收尾不完整(%s):%s" % (why, body["message"]))
        return 500, body

    def _final_ack(self, out: dict) -> bool:
        a = self._ack()
        if a.get("final") and a.get("since") == self.since:
            out.update(a)
            return True
        return False

    # -- 后台:定期回写与独占刷新 -------------------------------------------------

    def _start_owner_refresher(self) -> None:
        threading.Thread(target=self._owner_loop, name="owner", daemon=True).start()

    def _start_loops(self) -> None:
        threading.Thread(target=self._sync_loop, name="sync", daemon=True).start()

    def _owner_loop(self) -> None:
        while not self._stop_loops.wait(self.cfg.owner_refresh_seconds):
            if not self.refresh_owner():
                return

    def refresh_owner(self) -> bool:
        with self.lock:
            if self.state not in ("binding", "bound") or self.user_fd is None:
                return False
            if poolsync.owner_refresh(self.user_fd, self.cfg.pod):
                return True
            # 被别的沙箱接管(本副容器停顿超过 90 秒):再写就是两边互相覆盖。停主容器、不回写、退役。
            self.log("独占标记被别的沙箱接管:%s 不再回写,沙箱退役" % self.user)
            self._stop_loops.set()
            self._write_run("stop")
            self.state = "retired"
            self._persist()
            return False

    def _sync_loop(self) -> None:
        while not self._stop_loops.wait(self.cfg.sync_seconds):
            self.sync_once()

    def sync_once(self) -> Optional[dict]:
        with self.sync_lock:
            if self.state != "bound" or self.user_fd is None:
                return None
            self.snap_seq += 1
            podctl._write_private(self.cfg.ctl_dir / SNAP_FILE, json.dumps({"seq": self.snap_seq}))
            seq = self.snap_seq
            confirmed = self._wait(lambda: (self._ack().get("snap_seq") or 0) >= seq, self.cfg.snap_wait_seconds)
            moved = poolsync.sync(self.user_fd, self.state_fd, self.snap_seen, may_delete=True)
            self.last_sync_at = time.time()
            self.last_sync = dict(moved.to_json(), snapshot_confirmed=confirmed)
            if not confirmed:
                self.log("定期回写:主容器 %d 秒内没给出新快照,库沿用上一份" % self.cfg.snap_wait_seconds)
            return self.last_sync

    # -- 状态与停止信号 -----------------------------------------------------------

    def status(self) -> dict:
        return {"sandbox": self.cfg.sandbox_id, "state": self.state, "user_id": self.user,
                "bound_at": self.bound_at, "last_sync_at": self.last_sync_at, "last_sync": self.last_sync}

    def on_sigterm(self) -> None:
        """Pod 被删:与 unbind 等价。绑定中就等它结束(结束后是使用中或已退役)。"""
        self._wait(lambda: self.state != "binding", self.cfg.ready_seconds + 10)
        with self.lock:
            if self.state != "bound":
                return
            self.state = "unbinding"
            self._persist()
        self.log("收到停止信号:按解绑收尾")
        self._finish("sigterm")


# --- HTTP ------------------------------------------------------------------------

def make_server(sb: Sandbox, cfg: Config, host: str = "0.0.0.0") -> http.server.ThreadingHTTPServer:
    key = cfg.key.encode("utf-8")

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_a):
            pass

        def _send(self, code: int, obj: dict) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _authed(self) -> bool:
            return hmac.compare_digest(self.headers.get("X-Sandbox-Key", "").encode("utf-8"), key)

        def do_GET(self) -> None:
            if self.path == "/healthz":
                return self._send(200, {"ok": True})
            if not self._authed():
                return self._send(401, {})
            if self.path == "/sandbox/status":
                return self._send(200, sb.status())
            return self._send(404, {})

        def do_POST(self) -> None:
            if not self._authed():
                return self._send(401, {})
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if n < 0 or n > MAX_BODY:
                return self._send(413 if n > MAX_BODY else 400, {"reason": "bad_request"})
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._send(400, {"reason": "bad_request"})
            if not isinstance(body, dict):
                return self._send(400, {"reason": "bad_request"})
            if self.path == "/sandbox/bind":
                return self._send(*sb.bind(body.get("user_id")))
            if self.path == "/sandbox/unbind":
                return self._send(*sb.unbind(body.get("user_id"), body.get("reason", "")))
            return self._send(404, {})

    srv = http.server.ThreadingHTTPServer((host, cfg.port), H)
    srv.daemon_threads = True
    return srv


def serve(env: Mapping[str, str] = os.environ) -> int:
    try:
        cfg = Config.from_env(env)
    except ConfigError as exc:
        print("配置错误: %s" % exc, flush=True)
        return 2
    sb = Sandbox(cfg, log=lambda m: print(m, flush=True))
    srv = make_server(sb, cfg)
    term = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_a: term.set())
    signal.signal(signal.SIGINT, lambda *_a: term.set())
    threading.Thread(target=srv.serve_forever, name="http", daemon=True).start()
    print("副容器就绪:沙箱 %s,控制接口 :%d,状态 %s" % (cfg.sandbox_id, cfg.port, sb.state), flush=True)
    term.wait()
    sb.on_sigterm()                  # 收尾期间接口仍在服务(平台可查状态)
    srv.shutdown()
    return 0
