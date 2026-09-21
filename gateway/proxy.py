"""反向代理:把请求转给 runtime-<工号>:4096,顺手注入 Basic 口令并记活动。

三条硬约束,都是查出来的:

① **绝不缓冲。** opencode 的事件流是 SSE(`text/event-stream`),每 10 秒一跳心跳。
   任何一层把响应体攒起来再发,页面就一直转圈、消息迟迟不出来 —— 表现不像"断了",
   像"卡住了",最难排查。所以响应按块转发、每块 flush。
② **不设响应读超时。** SSE 是永不结束的连接。给它设读超时等于定期斩断它;
   前端会自动重连(带 Last-Event-ID,3 秒起退避),但重连瞬间的状态残留正是
   "已中断"的土壤(2026-09-21 查过)。连接超时该设,响应读取不设。
③ **活动时间在转发字节时更新,不是按请求计数。** 用户开着页面发呆时只有心跳,
   没有新请求;按请求计数判闲置会把正在用的人的 Pod 收掉。

口令只在网关内存里,从 Secret 读出来后注入 Authorization 头;客户的 SSO 层
永远看不到它,少一个泄露面。
"""
from __future__ import annotations

import base64
import http.client
from typing import Callable, Dict, Iterable, List, Optional, Tuple

# 逐跳头:按 RFC 7230 不能转发。Content-Length 也要去掉 —— 我们按块转发,长度会变。
_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-length", "host",
})
# 网关自己用的头,不往后端传。
_GATEWAY_ONLY = frozenset({"x-agent-trust", "authorization"})

CHUNK = 8192
UPSTREAM_USERNAME = "opencode"


def basic_auth(password: str, username: str = UPSTREAM_USERNAME) -> str:
    return "Basic " + base64.b64encode(("%s:%s" % (username, password)).encode("utf-8")).decode("ascii")


def forward_headers(incoming: Iterable[Tuple[str, str]], password: str,
                    user_id: str) -> List[Tuple[str, str]]:
    """过滤逐跳头与网关自用头,注入 Basic 口令与 X-Forwarded-User。"""
    out = []
    for k, v in incoming:
        lk = k.lower()
        if lk in _HOP_BY_HOP or lk in _GATEWAY_ONLY:
            continue
        out.append((k, v))
    out.append(("Authorization", basic_auth(password)))
    # 后端(opencode)不认这个头,但它会进后端的访问日志,排查时能对上人。
    out.append(("X-Forwarded-User", user_id))
    return out


def response_headers(raw: Iterable[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """回给客户端的头:同样去掉逐跳头。`x-accel-buffering: no` 若后端带了就保留 ——
    再往外还可能有一层客户的 nginx,那一层要靠它关缓冲。"""
    return [(k, v) for k, v in raw if k.lower() not in _HOP_BY_HOP]


class Upstream:
    """一次转发。connect_timeout 有值,**读响应不设超时**(SSE 永不结束)。"""

    def __init__(self, host: str, port: int = 4096, connect_timeout: float = 10.0):
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout

    def send(self, method: str, path: str, headers: List[Tuple[str, str]],
             body: Optional[bytes]) -> http.client.HTTPResponse:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.connect_timeout)
        conn.request(method, path, body=body, headers=dict(headers))
        resp = conn.getresponse()
        # 拿到响应头之后把超时摘掉:后面读的可能是一条挂几小时的 SSE 流。
        if conn.sock is not None:
            conn.sock.settimeout(None)
        return resp


def pump(resp: http.client.HTTPResponse, write: Callable[[bytes], None],
         flush: Callable[[], None], on_bytes: Optional[Callable[[int], None]] = None) -> int:
    """按块转发响应体,**每块都 flush**。返回转发的字节数。

    不 flush 的话 SSE 的心跳会被攒在缓冲里,页面表现为一直转圈。
    """
    total = 0
    while True:
        chunk = resp.read(CHUNK)
        if not chunk:
            break
        write(chunk)
        flush()
        total += len(chunk)
        if on_bytes is not None:
            on_bytes(len(chunk))
    return total
