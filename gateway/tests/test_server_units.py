"""网关 HTTP 层:起真的服务 + 真的假上游,端到端走一遍。

重点不在路由对不对,在这四条:
① 不可信来源打代理入口 → 403,而且**不说原因**(说了就是告诉试探者哪一步过了);
② 没配控制密钥时 /admin 一律 404 —— 默认不暴露运维接口;
③ SSE **按块转发且不缓冲**:客户端要能在上游还没结束时就收到前面的字节;
④ 上游连不上时回 502 并说人话,不要把 traceback 丢给用户。
"""
import dataclasses
import json
import pathlib
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from gateway import config as gwconfig  # noqa: E402
from gateway import identity as idt     # noqa: E402
from gateway import pods, server        # noqa: E402
from gateway.tests.test_pods_units import FakeKube  # noqa: E402

SECRET = "s" * 32
ADMIN = "a" * 32


class _Upstream(BaseHTTPRequestHandler):
    """假的 runtime Pod:/sse 分块慢慢吐,其余回显收到的头。"""
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path.startswith("/slow"):
            # 同步慢响应:头都要等一会儿才发。模拟 POST /session/{id}/message 这类要跑完模型才回的请求
            time.sleep(3)
            body = b'{"slow": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/sse"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("x-accel-buffering", "no")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for i in range(3):
                chunk = b"data: tick%d\n\n" % i
                self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
                self.wfile.flush()
                time.sleep(0.05)
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            return
        body = json.dumps({"auth": self.headers.get("Authorization", ""),
                           "fwd_user": self.headers.get("X-Forwarded-User", ""),
                           "trust": self.headers.get("X-Agent-Trust", ""),
                           "path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture()
def stack(monkeypatch):
    up = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    up_port = up.server_address[1]

    kube = FakeKube()
    # 配置层不接受 port=0(生产上那会变成「随机端口」,是个静默失效),
    # 所以测试里不从环境给 0,而是加载后 replace 成 0 让 OS 挑临时端口。
    cfg = dataclasses.replace(
        gwconfig.load({"GATEWAY_NAMESPACE": "ns", "GATEWAY_IMAGE_TAG": "t",
                       "GATEWAY_TRUST_SECRET": SECRET, "GATEWAY_ADMIN_TOKEN": ADMIN}), port=0)
    mgr = pods.Manager(kube, namespace="ns", image_tag="t", pull_policy="IfNotPresent",
                       templates=_ROOT / "k8s" / "templates")
    # FakeKube 的 apply 不会产生 status,state() 永远 not ready → 直接让它「已就绪」
    monkeypatch.setattr(pods.Manager, "state",
                        lambda self, uid, kind=pods.KIND_RUNTIME:
                        pods.PodState(uid, kind, exists=True, ready=True))
    # 上游指到假 Pod
    # 记下网关本想连的 (host, port),再把它指到假 Pod —— 三路分发的测试靠这个看路由对不对
    asked = []
    monkeypatch.setattr("gateway.proxy.Upstream.__init__",
                        lambda self, host, port=4096, connect_timeout=10.0:
                        (asked.append((host, port)), setattr(self, "host", "127.0.0.1"),
                         setattr(self, "port", up_port), setattr(self, "connect_timeout", 2.0)) and None)
    app = server.App(cfg, mgr, log=lambda _m: None)
    app.asked = asked
    gw = server.serve(app)
    gw.server_address = gw.socket.getsockname()
    threading.Thread(target=gw.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % gw.server_address[1]
    yield base, kube, app
    gw.shutdown(); gw.server_close(); up.shutdown(); up.server_close()


def _req(base, path, headers=None, method="GET", data=None):
    r = urllib.request.Request(base + path, headers=headers or {}, method=method, data=data)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


# --- 安全边界 -----------------------------------------------------------------

def test_healthz_needs_no_auth(stack):
    base, _k, _a = stack
    code, body = _req(base, "/healthz")
    assert code == 200 and json.loads(body)["ok"] is True


def test_proxy_without_trust_is_403_and_says_nothing_useful(stack):
    base, _k, _a = stack
    code, body = _req(base, "/session", {"X-Agent-User": "u1234"})
    assert code == 403
    for leak in ("secret", "cidr", "工号格式", "来源不可信"):
        assert leak not in body, "错误信息泄露了判定细节:%s" % body


def test_admin_is_404_when_no_admin_token(monkeypatch):
    """默认不暴露运维接口 —— 没配密钥就当它不存在,连「需要鉴权」都不说。"""
    kube = FakeKube()
    cfg = dataclasses.replace(
        gwconfig.load({"GATEWAY_NAMESPACE": "ns", "GATEWAY_IMAGE_TAG": "t",
                       "GATEWAY_TRUST_SECRET": SECRET}), port=0)
    mgr = pods.Manager(kube, namespace="ns", image_tag="t", pull_policy="IfNotPresent",
                       templates=_ROOT / "k8s" / "templates")
    gw = server.serve(server.App(cfg, mgr, log=lambda _m: None))
    gw.server_address = gw.socket.getsockname()
    threading.Thread(target=gw.serve_forever, daemon=True).start()
    try:
        code, _ = _req("http://127.0.0.1:%d" % gw.server_address[1], "/admin/pods")
        assert code == 404
    finally:
        gw.shutdown(); gw.server_close()


def test_admin_wrong_token_is_403(stack):
    base, _k, _a = stack
    code, _ = _req(base, "/admin/pods", {"X-Admin-Token": "wrong"})
    assert code == 403


def test_admin_lists_pods(stack):
    base, _k, _a = stack
    code, body = _req(base, "/admin/pods", {"X-Admin-Token": ADMIN})
    assert code == 200 and "pods" in json.loads(body)


def test_admin_rejects_illegal_user_id(stack):
    base, _k, _a = stack
    code, _ = _req(base, "/admin/pods/..%2fetc", {"X-Admin-Token": ADMIN}, method="GET")
    assert code in (400, 404)


# --- 代理 ---------------------------------------------------------------------

def test_proxy_injects_basic_auth_and_strips_trust_header(stack):
    """口令由网关注入;信任头**不能**透给后端(它是网关与上游之间的凭据)。"""
    base, _k, _a = stack
    code, body = _req(base, "/whoami", {idt.HEADER_TRUST: SECRET, "X-Agent-User": "u1234"})
    assert code == 200
    seen = json.loads(body)
    assert seen["auth"].startswith("Basic "), "没注入 Basic 口令"
    assert seen["fwd_user"] == "u1234"
    assert seen["trust"] == "", "信任头透到后端去了"


def test_sse_arrives_in_chunks_not_buffered(stack):
    """**核心**:上游边吐边转,客户端不能等到全部结束才拿到第一块。

    **用裸 socket,不用 urllib。** urllib 会在自己这一层做缓冲读,三块「同时到达」
    分不清是网关缓冲了还是客户端缓冲了 —— 第一版这么写,结果测的是 urllib。
    """
    import socket
    base, _k, _a = stack
    port = int(base.rsplit(":", 1)[1])
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    s.sendall(("GET /sse HTTP/1.1\r\nHost: x\r\n%s: %s\r\nX-Agent-User: u1234\r\n\r\n"
               % (idt.HEADER_TRUST, SECRET)).encode())
    t0 = time.time()
    arrivals, buf, headers_done = [], b"", False
    while time.time() - t0 < 8:
        try:
            data = s.recv(4096)
        except socket.timeout:
            break
        if not data:
            break
        buf += data
        if not headers_done and b"\r\n\r\n" in buf:
            headers_done = True
            head, buf = buf.split(b"\r\n\r\n", 1)
            assert b"x-accel-buffering: no" in head.lower(), "上游的关缓冲头要传下去"
        if headers_done and b"data:" in data:
            arrivals.append(time.time() - t0)
        if b"0\r\n\r\n" in buf:
            break
    s.close()
    assert len(arrivals) >= 3, "只在 %d 次 recv 里看到 data:(上游分 3 次吐)" % len(arrivals)
    assert arrivals[-1] - arrivals[0] > 0.05, \
        "三块在 %.3f 秒内全到 —— 说明被攒起来一次性发了" % (arrivals[-1] - arrivals[0])


def test_activity_is_recorded_while_proxying(stack):
    base, kube, _a = stack
    _req(base, "/whoami", {idt.HEADER_TRUST: SECRET, "X-Agent-User": "u1234"})
    assert any(kind == "deployments" and "last-activity" in json.dumps(p)
               for kind, _n, p in kube.patched), "转发时没记活动时间 —— 会被回收器误判闲置"


def test_upstream_down_is_502_in_plain_language(stack, monkeypatch):
    base, _k, _a = stack
    monkeypatch.setattr("gateway.proxy.Upstream.send",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("connection refused")))
    code, body = _req(base, "/session", {idt.HEADER_TRUST: SECRET, "X-Agent-User": "u1234"})
    assert code == 502
    assert "Traceback" not in body and "OSError" not in body


def test_hot_path_does_not_apply_and_so_keeps_activity_annotation(stack, monkeypatch):
    """**2026-09-21 真跑时踩到。**

    原来每个请求都走 ensure() 做 server-side apply,而模板里没有 last-activity 注解,
    于是 apply 把 touch() 写的活动时间抹掉 —— 回收器看不到活动,退回按创建时间算,
    最终会把**正在用的** Pod 收掉。实测表现是 /admin/pods/<工号> 的 last_activity 恒为 0。

    修法:Pod 已存在且就绪时**不 apply**,只取口令 + touch。
    """
    base, kube, _a = stack
    _req(base, "/whoami", {idt.HEADER_TRUST: SECRET, "X-Agent-User": "u1234"})
    applied_deploys = [n for kind, n, _ in kube.applied if kind == "deployments"]
    assert not applied_deploys, "热路径上 apply 了 Deployment:%s —— 会抹掉活动注解" % applied_deploys
    assert any("last-activity" in json.dumps(p) for _k, _n, p in kube.patched), "活动时间没记上"


def test_missing_prerequisite_is_503_not_504(stack, monkeypatch):
    """缺按人签发的令牌是**配置缺失**,等下去不会好;报 504「正在启动请稍后重试」
    会让用户一直刷,而运维永远看不到真原因。"""
    from gateway import pods as gwpods
    base, _k, _a = stack
    monkeypatch.setattr(gwpods.Manager, "state",
                        lambda self, uid, kind=gwpods.KIND_RUNTIME:
                        gwpods.PodState(uid, kind, exists=False, ready=False))
    monkeypatch.setattr(gwpods.Manager, "require_prerequisites",
                        lambda self, uid, kb: (_ for _ in ()).throw(
                            gwpods.PrerequisiteMissing("缺 Secret grmp-token-%s" % uid)))
    code, body = _req(base, "/session", {idt.HEADER_TRUST: SECRET, "X-Agent-User": "u1234"})
    assert code == 503
    assert "联系管理员" in body
    assert "grmp-token" not in body, "给最终用户的话不该带内部资源名"


# --- 三路分发 -----------------------------------------------------------------

def test_route_for_is_a_pure_function():
    assert server.route_for("/dash") == "dash" and server.route_for("/dash/health?x=1") == "dash"
    assert server.route_for("/reports/health/latest.json") == "reports"
    assert server.route_for("/dashboard") == "opencode" and server.route_for("/reportsx") == "opencode"
    assert server.route_for("/session") == "opencode" and server.route_for("/") == "opencode"


def test_reports_route_goes_to_4097_with_prefix_stripped_and_still_touches(stack):
    base, kube, app = stack
    code, body = _req(base, "/reports/health/latest.json", {idt.HEADER_TRUST: SECRET, "X-Agent-User": "u1234"})
    assert code == 200
    echoed = json.loads(body)
    assert echoed["path"] == "/health/latest.json", "转发给报告端口的路径要去掉 /reports 前缀"
    assert app.asked[-1] == ("runtime-u1234.ns.svc", 4097)
    assert echoed["auth"] == "", "报告端口没有口令,不该注入 Authorization"
    assert any("last-activity" in json.dumps(p) for _k, _n, p in kube.patched), "看大盘也算在用"


def test_dash_route_goes_to_frontend_without_password_or_touch_or_ensure(stack):
    base, kube, app = stack
    code, body = _req(base, "/dash/health", {idt.HEADER_TRUST: SECRET, "X-Agent-User": "u1234"})
    assert code == 200
    echoed = json.loads(body)
    assert app.asked[-1] == ("frontend.ns.svc", 80)
    assert echoed["auth"] == "" and echoed["fwd_user"] == "u1234"
    assert not any("last-activity" in json.dumps(p) for _k, _n, p in kube.patched), "静态页不记活动"
    assert not [n for kind, n, _ in kube.applied if kind == "secrets"], "/dash 不该碰用户的口令 Secret"


def test_dash_route_still_requires_trust(stack):
    base = stack[0]
    code, _ = _req(base, "/dash/health", {"X-Agent-User": "u1234"})
    assert code == 403


def test_slow_synchronous_response_is_not_cut_by_connect_timeout(stack):
    """**2026-09-22 e2e 抓到。** POST /session/{id}/message 是同步的,要等模型跑完才回 ——
    可 HTTPConnection(timeout=connect_timeout) 那个 timeout 同时管读响应,10 秒一到就 timed out → 502,
    而 Pod 里的活其实照跑。connect_timeout 只该管连接;连上之后读响应不设超时(proxy.py 注释写了,代码没做到)。
    夹具里 connect_timeout 是 2 秒,上游 3 秒才回:修好前必 502。"""
    base, _k, _a = stack
    code, body = _req(base, "/slow", {idt.HEADER_TRUST: SECRET, "X-Agent-User": "u1234"})
    assert code == 200, body
    assert json.loads(body)["slow"] is True
