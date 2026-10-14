"""模拟 SSO(k8s/tools/sso-sim.py):现场测试时站在 SSO 的位置上给网关加身份头(docs/现场测试-模拟SSO.md)。

它决定「网关认为这是谁」,所以核对的是头:加了该加的、丢了浏览器自带的、头名跟得上网关的配置。
"""
import http.server
import importlib.util
import json
import pathlib
import socket
import threading
import urllib.request

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_PATH = _ROOT / "k8s" / "tools" / "sso-sim.py"


def _load(monkeypatch, **env):
    for k in ("SSO_USER_HEADER", "SSO_ROLES_HEADER"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("sso_sim_under_test", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture()
def upstream():
    """假网关:把收到的请求头原样回给调用方。"""
    seen = {}

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_a):
            pass

        def do_GET(self):
            seen["headers"] = {k.lower(): v for k, v in self.headers.items()}
            body = json.dumps(seen["headers"]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


def _through(mod, upstream_port, roles="", browser_headers=None):
    port = _free_port()
    srv = mod.Server(("127.0.0.1", port), mod.make_handler(upstream_port, "u0001", "s" * 20, roles))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request("http://127.0.0.1:%d/global/health" % port, headers=browser_headers or {})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())
    finally:
        srv.shutdown()


def test_adds_user_and_trust_and_drops_what_the_browser_claims(monkeypatch, upstream):
    mod = _load(monkeypatch)
    got = _through(mod, upstream, browser_headers={"X-Agent-User": "boss", "X-Agent-Trust": "guess",
                                                   "X-Agent-Roles": "kb-admin"})
    assert got["x-agent-user"] == "u0001" and got["x-agent-trust"] == "s" * 20
    assert "x-agent-roles" not in got, "浏览器自带的角色头必须丢掉:模拟的是 SSO 覆盖身份,不是透传"


def test_roles_are_sent_only_when_given(monkeypatch, upstream):
    mod = _load(monkeypatch)
    assert _through(mod, upstream, roles="kb-admin")["x-agent-roles"] == "kb-admin"


def test_header_names_follow_the_gateway_config(monkeypatch, upstream):
    """贵方改了 USER_HEADER / ROLES_HEADER 时,模拟 SSO 要用同样的名字,否则网关一律 403。"""
    mod = _load(monkeypatch, SSO_USER_HEADER="X-Employee-Id", SSO_ROLES_HEADER="X-Groups")
    got = _through(mod, upstream, roles="kb-admin",
                   browser_headers={"X-Employee-Id": "boss", "X-Groups": "root"})
    assert got["x-employee-id"] == "u0001" and got["x-groups"] == "kb-admin"
    assert "x-agent-user" not in got and got["x-agent-trust"] == "s" * 20, "信任头名网关写死,不跟着改"


def test_usage_and_empty_trust_file_are_refused(monkeypatch, tmp_path, capsys):
    mod = _load(monkeypatch)
    assert mod.main(["1", "2"]) == 2
    empty = tmp_path / "t"
    empty.write_text("", encoding="utf-8")
    assert mod.main(["18090", "18089", "u0001", str(empty)]) == 2
    assert "空的" in capsys.readouterr().err


def test_listens_only_on_loopback():
    src = _PATH.read_text(encoding="utf-8")
    assert 'Server(("127.0.0.1", listen)' in src, "只许监听本机:谁连得上谁就是那个工号"
