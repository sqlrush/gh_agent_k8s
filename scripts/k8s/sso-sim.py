#!/usr/bin/env python3
"""本机验证用的「模拟 SSO」:给每个请求加上工号头与信任凭据,再转给网关。

客户现场是 SSO / 反向代理认完人之后往请求里注入 `X-Agent-User` 与 `X-Agent-Trust`;
本地没有 SSO,浏览器又加不了这两个头,于是大盘与对话页在浏览器里都是 403。
这个代理就站在 SSO 的位置上:浏览器 → 本代理(加头)→ 网关(经 port-forward)。

只在本机用,只监听 127.0.0.1,不进镜像。流式响应(SSE,对话页靠它)逐块转发。

用法: sso-sim.py <监听端口> <网关端口> <工号> <信任凭据文件>
      浏览器打开 http://127.0.0.1:<监听端口>/dash/health
"""
import http.client
import http.server
import pathlib
import socketserver
import sys

HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade",
       "proxy-authorization", "proxy-authenticate"}
# 浏览器自己带的同名头一律丢掉:模拟的是「SSO 覆盖掉客户端传来的身份头」,不是透传
IDENTITY = {"x-agent-user", "x-agent-trust", "x-agent-roles"}


def make_handler(target: int, user: str, trust: str):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _proxy(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            conn = http.client.HTTPConnection("127.0.0.1", target, timeout=900)
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in HOP and k.lower() not in IDENTITY and k.lower() != "host"}
            headers["Host"] = "127.0.0.1:%d" % target
            headers["X-Agent-User"] = user
            headers["X-Agent-Trust"] = trust
            try:
                conn.request(self.command, self.path, body=body, headers=headers)
                resp = conn.getresponse()
            except OSError as exc:
                msg = ("模拟 SSO 连不上网关 127.0.0.1:%d(%s)。port-forward 还在吗?" % (target, exc)).encode()
                self.send_response(502)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
                return
            self.send_response(resp.status, resp.reason)
            stream = "text/event-stream" in (resp.getheader("Content-Type") or "")
            for k, v in resp.getheaders():
                if k.lower() in HOP or k.lower() == "content-length":
                    continue
                self.send_header(k, v)
            chunked = stream or resp.getheader("Content-Length") is None
            if chunked:
                self.send_header("Transfer-Encoding", "chunked")
            else:
                self.send_header("Content-Length", resp.getheader("Content-Length"))
            self.end_headers()
            try:
                while True:
                    chunk = resp.read1(8192) if stream else resp.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk) if chunked else chunk)
                    self.wfile.flush()
                if chunked:
                    self.wfile.write(b"0\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                conn.close()

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _proxy

        def log_message(self, *_a):
            pass

    return Handler


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main(argv):
    if len(argv) != 4:
        print(__doc__.strip().splitlines()[-2].strip(), file=sys.stderr)
        return 2
    listen, target, user = int(argv[0]), int(argv[1]), argv[2]
    trust = pathlib.Path(argv[3]).read_text(encoding="utf-8").strip()
    if not trust:
        print("信任凭据文件是空的:%s" % argv[3], file=sys.stderr)
        return 2
    print("模拟 SSO:http://127.0.0.1:%d → 网关 127.0.0.1:%d,工号 %s" % (listen, target, user), flush=True)
    Server(("127.0.0.1", listen), make_handler(target, user, trust)).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
