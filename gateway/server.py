"""网关的 HTTP 层:两个入口。

**代理入口(所有其它路径)** —— 给「没有容器调度能力」的客户:
    浏览器/CLI → 客户 SSO/WAF(认人,把工号放进头) → 本网关 → runtime-<工号>:4096
    网关负责:认这个头凭不凭得过 → 按工号找/建 Pod → 等就绪 → 反代 → 记活动

**控制 API `/admin/...`** —— 给「已经有一套容器管理框架」的客户:
    他们自己的调度器直接调这几个接口,代理那半可以完全不用。
    用独立的 GATEWAY_ADMIN_TOKEN 鉴权(和代理入口的信任来源分开:
    那一层信的是「上游已经认过人」,这一层信的是「调用方持有运维密钥」)。

**/healthz** 不鉴权 —— 探针要用,而且它不泄露任何东西。
"""
from __future__ import annotations

import http.client
import http.cookies
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Tuple

from . import identity as idt
from . import pods, proxy
from .diagnose import NotReady

_MAX_BODY = 64 * 1024 * 1024        # 上传走网关的话,单请求上限;超过直接 413
POLL_S = 1.5                        # 等用户 Pod 就绪时的轮询间隔
DIAG_EVERY = 4                      # 每轮询这么多次读一次 Pod 状态:等下去不会好的就提前返回


ROUTE_DASH, ROUTE_REPORTS, ROUTE_OPENCODE = "dash", "reports", "opencode"
REPORTS_PORT = 4097          # 用户 Pod 里 podctl serve-reports 的端口(docker/entrypoint.sh)

# 对话请求转到哪个容器(知识库管理员才有 kb-import)。路径前缀走不通 —— opencode 网页用根绝对路径,
# 所以按「目标」选:SSO 可给管理员入口加请求头;浏览器访问切换地址设 cookie。都在网关校验身份。
TARGET_HEADER = "X-Agent-Target"
TARGET_COOKIE = "gsa_target"
SWITCH_PATHS = {"/kb-import": pods.KIND_KB_IMPORT, "/runtime": pods.KIND_RUNTIME}
# 切换后落到 opencode 的项目页:base64url("/data/state/workspace"),两个容器同一个工作目录(docker/entrypoint.sh)
LANDING = "/L2RhdGEvc3RhdGUvd29ya3NwYWNl"


def route_for(path: str) -> str:
    """按路径前缀分三路。纯函数,单独可测。

    /dash/*     大盘前端(全体共用的静态页),不建 Pod、不注口令、不记活动
    /reports/*  用户自己 Pod 的报告只读端口,去掉前缀后转发
    其它        opencode serve(对话),原样
    """
    p = path.split("?", 1)[0]
    if p == "/dash" or p.startswith("/dash/"):
        return ROUTE_DASH
    if p.startswith("/reports/"):
        return ROUTE_REPORTS
    return ROUTE_OPENCODE


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "gaussdb-agent-gateway"
    sys_version = ""

    # -- 工具 ---------------------------------------------------------------

    @property
    def app(self):
        return self.server.app          # type: ignore[attr-defined]

    def _peer(self) -> str:
        return self.client_address[0] if self.client_address else ""

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, code: int, msg: str) -> None:
        body = msg.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> Optional[bytes]:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return None
        if n > _MAX_BODY:
            self._text(413, "请求体过大")
            return None
        return self.rfile.read(n)

    def log_message(self, fmt: str, *args) -> None:
        # 默认实现把每个请求打到 stderr,SSE 心跳会把日志刷爆。只留非 2xx。
        try:
            code = int(args[1])
        except (IndexError, ValueError):
            code = 0
        if code and code >= 400:
            self.app.log("%s %s → %s" % (self.command, self.path.split("?")[0], code))

    # -- 路由 ---------------------------------------------------------------

    def do_GET(self):
        self._route()

    def do_POST(self):
        self._route()

    def do_PUT(self):
        self._route()

    def do_PATCH(self):
        self._route()

    def do_DELETE(self):
        self._route()

    def do_HEAD(self):
        self._route()

    def _route(self) -> None:
        path = self.path.split("?")[0]
        self._streaming = False          # 响应头发出去之后就不能再回一个完整的错误响应
        try:
            if path == "/healthz":
                return self._json(200, {"ok": True, "version": self.server_version})
            if path.startswith("/admin/"):
                return self._admin(path)
            return self._proxy()
        except (BrokenPipeError, ConnectionResetError):
            pass                     # 客户端先走了,正常
        except Exception as exc:     # noqa: BLE001
            # **必须兜住。** 不兜的话 socketserver 只是打一段 traceback 到 stderr 并**断开连接**,
            # 客户端看到的是「空响应」—— 比一个 500 难查得多(2026-09-21 真部署时就这样:
            # k8s API 连不上,/admin/pods 返回完全空白,要翻 Pod 日志才看见原因)。
            self.app.log("处理 %s %s 时未捕获的异常:%r" % (self.command, path, exc))
            if self._streaming:
                # 200 与分块头已经发出:再写一个 500 会被当成分块长度,整条流就坏了。只能断开。
                self.close_connection = True
                return
            try:
                self._json(500, {"error": "网关内部错误,请看网关日志(不回传细节)"})
            except OSError:
                pass

    # -- 控制 API -----------------------------------------------------------

    def _admin(self, path: str) -> None:
        token = self.app.cfg.admin_token
        if not token:
            return self._json(404, {"error": "控制 API 未启用(没有配 GATEWAY_ADMIN_TOKEN)"})
        import hmac
        got = (self.headers.get("X-Admin-Token") or "")
        if not hmac.compare_digest(got, token):
            return self._json(403, {"error": "控制 API 密钥不对"})

        parts = [p for p in path.split("/") if p]        # ['admin','pods',...]
        if len(parts) == 2 and parts[1] == "pods" and self.command == "GET":
            return self._json(200, {"pods": self.app.list_pods()})
        if len(parts) == 3 and parts[1] == "pods":
            uid = idt.normalize_user_id(parts[2])
            if not idt.valid_user_id(uid):
                return self._json(400, {"error": "工号格式不合法"})
            if self.command == "GET":
                st = self.app.mgr.state(uid)
                return self._json(200, {"user_id": uid, "exists": st.exists,
                                        "ready": st.ready, "last_activity": st.last_activity})
            if self.command == "POST":
                body = self._read_body()
                want_admin = False
                if body:
                    try:
                        want_admin = bool(json.loads(body).get("kb_admin"))
                    except ValueError:
                        return self._json(400, {"error": "请求体不是合法 JSON"})
                try:
                    self.app.ensure_ready(uid, want_admin)
                except pods.PrerequisiteMissing as exc:
                    # 控制 API 的调用方是运维系统,**要看到完整原因**(缺哪个 Secret),
                    # 不像代理入口那边面对的是最终用户。
                    return self._json(503, {"error": str(exc)})
                except NotReady as exc:
                    return self._json(503 if exc.permanent else 504,
                                      {"error": exc.detail, "category": exc.category, "permanent": exc.permanent})
                st = self.app.mgr.state(uid)
                return self._json(200, {"user_id": uid, "ready": st.ready,
                                        "note": "口令由网关持有并在代理时注入;控制 API 不返回它"})
            if self.command == "DELETE":
                self.app.mgr.remove(uid)
                return self._json(200, {"user_id": uid, "removed": True,
                                        "note": "NAS 上的会话/历史/上传文件保留,下次登录挂回来即恢复"})
        self._json(404, {"error": "没有这个控制接口"})

    # -- 代理 ---------------------------------------------------------------

    def _proxy(self) -> None:
        try:
            who = idt.extract(dict(self.headers.items()), self._peer(),
                              header_name=self.app.cfg.user_header,
                              roles_header=self.app.cfg.roles_header,
                              secret=self.app.cfg.trust_secret,
                              cidrs=self.app.cfg.trust_cidrs)
        except idt.IdentityError:
            # 不回细节:这是安全边界,错误信息会告诉试探者哪一步过了
            return self._text(403, "无法确认身份。请经统一认证入口访问。")

        switch = SWITCH_PATHS.get(self.path.split("?", 1)[0].rstrip("/"))
        if switch is not None:
            return self._switch(who, switch)

        route = route_for(self.path)
        body = self._read_body()
        if body is None and int(self.headers.get("Content-Length") or 0) > 0:
            return                                  # _read_body 已经回了 413

        if route == ROUTE_DASH:
            # 大盘是静态页;浏览器拿到页面后自己经本网关调 /reports 与 opencode。
            # 这里只转发:不建 Pod、不注口令、不记活动(看静态页不算在用)。
            up = proxy.Upstream("%s.%s.svc" % (self.app.cfg.frontend_service, self.app.cfg.namespace), 80)
            headers = proxy.forward_headers(self.headers.items(), None, who.user_id)
            path = self.path
        else:
            kb_admin = self.app.is_kb_admin(who)
            # 报告只在诊断容器里;对话按目标选
            kind = self._target(who, kb_admin) if route == ROUTE_OPENCODE else pods.KIND_RUNTIME
            try:
                password = self.app.ensure_ready(who.user_id, kb_admin, kind)
            except pods.PrerequisiteMissing as exc:
                # 配置缺失,等下去不会好。**日志里写清缺什么**,回给用户的话只说找谁 ——
                # 报成「正在启动,请稍后重试」会让用户一直刷,而运维永远看不到原因。
                self.app.log("为 %s 准备环境失败(前置条件):%s" % (who.user_id, exc))
                return self._text(503, "您的环境尚未开通,请联系管理员为您开通后再登录。")
            except NotReady as exc:
                # 日志里写完整原因(以前 504 在网关日志里一个字都没有);给用户只说是哪一类、找谁
                self.app.log("为 %s 准备环境未就绪:%s" % (who.user_id, exc.detail))
                if exc.permanent:
                    return self._text(503, "您的环境启动失败(%s),等待不会好转,请联系管理员处理。" % exc.category)
                why = (":%s" % exc.category) if exc.category else ""
                return self._text(504, "您的环境正在启动,请稍后重试。(%d 秒内未就绪%s)" % (exc.waited_s, why))
            except Exception as exc:                    # noqa: BLE001
                self.app.log("为 %s 准备环境失败:%s" % (who.user_id, exc))
                return self._text(502, "环境准备失败,请联系管理员。")
            host = "%s-%s.%s.svc" % (kind, who.user_id, self.app.cfg.namespace)
            if route == ROUTE_REPORTS:
                # 报告端口没有口令(鉴权就是「网关只把本人的请求路由过来」);它以 reports/ 为根
                up = proxy.Upstream(host, REPORTS_PORT)
                headers = proxy.forward_headers(self.headers.items(), None, who.user_id)
                path = self.path[len("/reports"):]
            else:
                up = proxy.Upstream(host)
                headers = proxy.forward_headers(self.headers.items(), password, who.user_id)
                path = self.path

        try:
            resp = up.send(self.command, path, headers, body)
        except OSError as exc:
            self.app.log("转发到 %s(%s)失败:%s" % (who.user_id, route, exc))
            return self._text(502, "大盘服务未部署或不可达。" if route == ROUTE_DASH
                              else "无法连接到您的环境,请稍后重试。")

        self.send_response(resp.status)
        for k, v in proxy.response_headers(resp.getheaders()):
            self.send_header(k, v)
        # 1xx / 204 / 304 与 HEAD 的响应按协议没有消息体:不能加分块编码,更不能写结束块。
        # 写了的话那 5 个字节会被浏览器当成同一连接上下一个响应的开头(ERR_INVALID_HTTP_RESPONSE)——
        # 大盘第二次打开时静态文件全是 304,一个模块坏掉整页就空白(2026-10-15 抓到)。
        if self.command == "HEAD" or resp.status in (204, 304) or 100 <= resp.status < 200:
            self.end_headers()
            resp.close()
            return
        # 我们按块转发,长度不可知 → 分块编码。SSE 也走这条路。
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        self._streaming = True
        # 建连时先记一次,免得长连接期间被判闲置;静态页除外
        touch = (lambda: self.app.mgr.touch(who.user_id, kind=kind)) if route != ROUTE_DASH else (lambda: None)
        try:
            touch()
            proxy.pump(resp, self._write_chunk, self.wfile.flush, on_bytes=lambda _n: touch())
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except pods.EnvironmentGone as exc:
            # 用户的 Deployment 在流还开着时被删了(注销 / 卸载重装):背后的 Pod 正在退出,
            # 继续转发只会断在半路 —— 把分块正常收尾,让客户端干净地结束这条连接。
            self.app.log("%s 的环境已被回收,结束这条连接(%s)" % (who.user_id, exc))
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            self.close_connection = True
        except (BrokenPipeError, ConnectionResetError):
            pass                                     # 用户关了页面,正常
        finally:
            resp.close()

    def _target(self, who: idt.Identity, kb_admin: bool) -> str:
        """请求头优先(SSO 给管理员入口加的),其次 cookie(浏览器切换过)。不是管理员一律回诊断容器。"""
        want = (self.headers.get(TARGET_HEADER) or "").strip().lower()
        if not want:
            jar = http.cookies.SimpleCookie()
            try:
                jar.load(self.headers.get("Cookie") or "")
            except http.cookies.CookieError:
                pass
            want = jar[TARGET_COOKIE].value if TARGET_COOKIE in jar else ""
        if want == pods.KIND_KB_IMPORT:
            if kb_admin:
                return pods.KIND_KB_IMPORT
            self.app.log("%s 要进导入容器但不是知识库管理员,转到诊断容器" % who.user_id)
        return pods.KIND_RUNTIME

    def _switch(self, who: idt.Identity, kind: str) -> None:
        """/kb-import/ 切到导入容器,/runtime/ 切回;之后落到对话页。"""
        if kind == pods.KIND_KB_IMPORT and not self.app.is_kb_admin(who):
            return self._text(403, "您不是知识库管理员,没有导入容器。")
        if kind == pods.KIND_KB_IMPORT:
            cookie = "%s=%s; Path=/; HttpOnly; SameSite=Lax" % (TARGET_COOKIE, kind)
        else:
            cookie = "%s=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0" % TARGET_COOKIE
        self.send_response(303)
        self.send_header("Location", LANDING)
        self.send_header("Set-Cookie", cookie)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _write_chunk(self, data: bytes) -> None:
        self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")


class App:
    """把配置、k8s、Pod 管理、回收器串起来。"""

    def __init__(self, cfg, mgr: pods.Manager, log=print):
        self.cfg = cfg
        self.mgr = mgr
        self.log = log
        self._waiters: dict = {}
        self._lock = threading.Lock()

    def is_kb_admin(self, who: idt.Identity) -> bool:
        """角色头里带 KB_ADMIN_ROLES 之一,**或**工号登记在 ConfigMap agent-roles 里(SSO 不传角色时用)。"""
        if any(r in self.cfg.kb_admin_roles for r in who.roles):
            return True
        return who.user_id in self.mgr.kb_admins()

    def ensure_ready(self, user_id: str, kb_admin: bool, kind: str = pods.KIND_RUNTIME) -> str:
        """保证这个人的 Pod(kind 那一个)存在且就绪,返回它的 Basic 口令(两个容器同一个口令)。

        同一个人的并发请求只让第一个去建 —— 否则浏览器一开就是十几个并发请求,
        每个都去 apply 一遍 Deployment。**按 kind 判就绪**:导入容器被回收器缩到 0 后,
        runtime 仍就绪 —— 只看 runtime 的话管理员就再也进不去导入容器了。
        """
        with self._lock:
            lock = self._waiters.setdefault(user_id, threading.Lock())
        with lock:
            st = self.mgr.state(user_id, kind)
            if st.exists and st.ready:
                # **热路径上不做 apply。** 两个原因:
                # ① server-side apply 会按模板重写对象,而模板里没有 last-activity 注解,
                #    于是每个请求都把 touch() 写的活动时间抹掉 —— 回收器因此看不到活动,
                #    退回按创建时间算,最终会把正在用的 Pod 收掉(2026-09-21 实测 last_activity=0)。
                # ② 每个请求少一次 k8s 写,SSE 心跳那种频率下差别很大。
                return self.mgr.ensure_password(user_id)
            self.mgr.require_prerequisites(user_id, kb_admin)
            password = self.mgr.ensure(user_id, kb_admin)
            start = time.time()
            deadline = start + self.cfg.ready_timeout_s
            polls = 0
            while time.time() < deadline:
                if self.mgr.state(user_id, kind).ready:
                    return password
                polls += 1
                if polls % DIAG_EVERY == 0:
                    # 拉不到镜像 / 配置缺失 / 个人目录初始化失败:等满超时也不会好,不让用户白等
                    d = self.mgr.diagnose(user_id, kind)
                    if d is not None and d.permanent:
                        raise NotReady(d, int(time.time() - start))
                time.sleep(POLL_S)
            raise NotReady(self.mgr.diagnose(user_id, kind), self.cfg.ready_timeout_s)

    def list_pods(self) -> list:
        from . import reaper
        items = (self.mgr.kube.list(pods.APPS, "deployments").get("items") or [])
        out = []
        for d in items:
            name = ((d.get("metadata") or {}).get("name") or "")
            uid = reaper.user_of(name)
            if not uid:
                continue
            out.append({"deployment": name, "user_id": uid,
                        "replicas": (d.get("spec") or {}).get("replicas"),
                        "ready": int((d.get("status") or {}).get("availableReplicas") or 0) >= 1,
                        "last_activity": reaper.last_seen(d)})
        return out


def serve(app: App) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("0.0.0.0", app.cfg.port), _Handler)
    httpd.daemon_threads = True          # 用户关页面时不要卡住停机
    httpd.app = app                      # type: ignore[attr-defined]
    return httpd
