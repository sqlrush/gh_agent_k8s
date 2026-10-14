#!/usr/bin/env python3
"""池化沙箱的平台模拟器——本地端到端的工具,也是给客户平台的参考实现(方案 §6.4、§10)。

  pool-sim.py up [--size 16]            预建到总数(不少于 size)
  pool-sim.py login  <工号>             有沙箱(含倒计时中)就接回;没有就取空闲的 bind,没空闲就现建一个
  pool-sim.py logout <工号>             unbind(持久化) → 删 Pod → 总数不足就补建
  pool-sim.py close  <工号> [--countdown 1800]   关浏览器:开始倒计时
  pool-sim.py tick                       倒计时到点的按 timeout 解绑;再补建
  pool-sim.py oc <工号> GET|POST <路径> [JSON]   以平台身份转发到该用户沙箱的 4096(注入 Basic 口令)
  pool-sim.py status | down

平台要做、我方不做的事都在这里:池子数量、工号 → 沙箱映射、倒计时、补建、口令保管(口令只留在平台,不下发浏览器)。
接口调用的分支全部按 §7 的返回码:409 owner_busy 看 holder 与 retry_after;409 state 换一个;500/504 删掉换一个。

传输:集群内的平台直接访问 Pod IP(--via direct,默认);本地 Mac 访问不到 Pod IP,用 --via port-forward。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import re
import secrets
import string
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, List, Optional, Tuple

ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "k8s" / "templates" / "sandbox.yaml"
NS = "gaussdb-agent"
CONTROL_PORT, OC_PORT = 4098, 4096
Reply = Tuple[int, dict]


def render(template_text: str, sandbox_id: str, image: str, pull: str) -> str:
    return string.Template(template_text).substitute(SANDBOX_ID=sandbox_id, IMAGE=image, IMAGE_PULL_POLICY=pull)


# --- k8s(kubectl;集群内的平台换成自己的客户端) -----------------------------------

class Kube:
    def __init__(self, ctx: str, ns: str = NS):
        self.ctx, self.ns = ctx, ns

    def _k(self, *args: str, stdin: Optional[str] = None, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(["kubectl", "--context", self.ctx, "-n", self.ns, *args], input=stdin, text=True,
                              capture_output=True, check=check)

    def apply(self, manifest: str) -> None:
        self._k("apply", "-f", "-", stdin=manifest)

    def delete_pod(self, name: str) -> None:
        # 正常删除:两个容器收到停止信号,副容器按停止握手收尾。**不要 --force --grace-period=0**
        self._k("delete", "pod", name, "--wait=false", "--ignore-not-found", check=False)

    def sandboxes(self) -> List[dict]:
        out = json.loads(self._k("get", "pods", "-l", "app=gaussdb-agent,role=sandbox", "-o", "json").stdout)
        pods = []
        for p in out.get("items", []):
            conds = {c["type"]: c["status"] for c in p.get("status", {}).get("conditions", [])}
            pods.append({"name": p["metadata"]["name"], "ip": p.get("status", {}).get("podIP"),
                         "ready": conds.get("Ready") == "True",
                         "deleting": bool(p["metadata"].get("deletionTimestamp"))})
        return pods

    def wait_ready(self, name: str, timeout_s: int = 180) -> bool:
        return self._k("wait", "--for=condition=Ready", "pod/" + name, "--timeout=%ds" % timeout_s,
                       check=False).returncode == 0

    def secret(self, name: str, key: str) -> str:
        raw = self._k("get", "secret", name, "-o", "jsonpath={.data.%s}" % key).stdout
        return base64.b64decode(raw).decode("utf-8")


# --- 传输 -----------------------------------------------------------------------

def _http(url: str, method: str, body: Optional[dict], headers: Dict[str, str], timeout: float) -> Reply:
    data = json.dumps(body).encode("utf-8") if body is not None else (b"{}" if method == "POST" else None)
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, (json.loads(raw) if raw else {})
        except ValueError:
            return e.code, {"raw": raw.decode("utf-8", "replace")}


class Direct:
    """集群内的平台:按 Pod IP 访问(sandbox-policy 只放行带平台标签的来源)。"""

    def __init__(self, kube: Kube):
        self.kube = kube

    def call(self, pod: str, port: int, method: str, path: str, body=None, headers=None, timeout=120.0) -> Reply:
        ip = next((p["ip"] for p in self.kube.sandboxes() if p["name"] == pod), None)
        if not ip:
            return 0, {"reason": "no_pod_ip"}
        return _http("http://%s:%d%s" % (ip, port, path), method, body, headers or {}, timeout)


class PortForward:
    """本地 Mac 访问不到 Pod IP:每次调用临时开一个 kubectl port-forward。"""

    def __init__(self, kube: Kube):
        self.kube = kube

    def call(self, pod: str, port: int, method: str, path: str, body=None, headers=None, timeout=120.0) -> Reply:
        proc = subprocess.Popen(["kubectl", "--context", self.kube.ctx, "-n", self.kube.ns, "port-forward",
                                 "pod/" + pod, ":%d" % port], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)
        try:
            m = re.search(r"127\.0\.0\.1:(\d+)", proc.stdout.readline())
            if not m:
                return 0, {"reason": "port_forward_failed", "message": proc.stderr.read()[-300:]}
            return _http("http://127.0.0.1:%s%s" % (m.group(1), path), method, body, headers or {}, timeout)
        finally:
            proc.terminate()
            proc.wait(10)


# --- 平台 -----------------------------------------------------------------------

class PlatformError(RuntimeError):
    def __init__(self, reason: str, detail: dict):
        super().__init__("%s:%s" % (reason, json.dumps(detail, ensure_ascii=False)))
        self.reason, self.detail = reason, detail


class Platform:
    def __init__(self, kube, transport, key: str, image: str, pull: str, target: int,
                 state_path: pathlib.Path, template_text: Optional[str] = None,
                 log: Callable[[str], None] = print, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        self.kube, self.t, self.key = kube, transport, key
        self.image, self.pull, self.target = image, pull, target
        self.state_path = state_path
        self.template = template_text if template_text is not None else TEMPLATE.read_text(encoding="utf-8")
        self.log, self.clock, self.sleep = log, clock, sleep
        self.users: Dict[str, dict] = {}
        if state_path.exists():
            self.users = json.loads(state_path.read_text(encoding="utf-8")).get("users", {})

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_name(self.state_path.name + ".tmp")
        tmp.write_text(json.dumps({"users": self.users}, ensure_ascii=False, indent=1), encoding="utf-8")
        os.chmod(tmp, 0o600)                # 里面有各人的 opencode 口令:口令只留在平台
        os.replace(tmp, self.state_path)

    def _ctl(self, pod: str, method: str, path: str, body=None) -> Reply:
        return self.t.call(pod, CONTROL_PORT, method, path, body, {"X-Sandbox-Key": self.key})

    # -- 池子 --

    def _live(self) -> List[dict]:
        return [p for p in self.kube.sandboxes() if not p["deleting"]]

    def create(self) -> str:
        sid = secrets.token_hex(3)
        self.kube.apply(render(self.template, sid, self.image, self.pull))
        self.log("新建沙箱 sandbox-%s" % sid)
        return "sandbox-" + sid

    def refill(self) -> List[str]:
        """总数不足 target 就补建(§6.4);超出的不删——那是正在用的人。"""
        return [self.create() for _ in range(max(0, self.target - len(self._live())))]

    def _idle(self, exclude: set) -> Optional[str]:
        mapped = {u["sandbox"] for u in self.users.values()}
        for p in self._live():
            if p["ready"] and p["name"] not in mapped and p["name"] not in exclude:
                code, st = self._ctl(p["name"], "GET", "/sandbox/status")
                if code == 200 and st.get("state") == "idle":
                    return p["name"]
                exclude.add(p["name"])
        return None

    # -- 登录 / 退出 / 倒计时 --

    def login(self, user: str) -> dict:
        user = user.strip().lower()
        if user in self.users:                       # 同一个人同一时刻只能有一个沙箱:接回原沙箱,取消倒计时
            self.users[user]["deadline"] = None
            self._save()
            return dict(self.users[user], reused=True)
        tried: set = set()
        for _ in range(self.target + 3):
            pod = self._idle(tried)
            if pod is None:                          # 没空闲的:现建一个(第 17 个人多等几秒)
                pod = self.create()
                if not self.kube.wait_ready(pod):
                    tried.add(pod)
                    continue
            code, body = self._ctl(pod, "POST", "/sandbox/bind", {"user_id": user})
            if code == 200:
                self.users[user] = {"sandbox": pod, "password": body["opencode"]["password"], "deadline": None,
                                    "loaded": body.get("loaded")}
                self._save()
                self.log("%s → %s(载入 %s)" % (user, pod, body.get("loaded")))
                return dict(self.users[user], reused=False)
            reason = body.get("reason")
            if code == 409 and reason == "owner_busy":
                # 映射表里没有这个人,却有沙箱攥着他的目录:多半是上一个沙箱被强杀了,标记最长 90 秒过期
                raise PlatformError("owner_busy", body)
            tried.add(pod)
            if code in (500, 504):                   # 沙箱已退役:删掉换一个
                self.log("bind %s 在 %s 失败(%s %s),删掉换一个" % (user, pod, code, reason))
                self.kube.delete_pod(pod)
        raise PlatformError("no_sandbox", {"tried": sorted(tried)})

    def logout(self, user: str, reason: str = "logout") -> Reply:
        u = self.users.get(user)
        if u is None:
            raise PlatformError("not_logged_in", {"user_id": user})
        code, body = self._ctl(u["sandbox"], "POST", "/sandbox/unbind", {"user_id": user, "reason": reason})
        if code == 409:
            raise PlatformError("unbind_conflict", body)   # 映射表与沙箱对不上:先核对,别删
        if code != 200:
            self.log("unbind %s 回 %s(%s),照样删除并告警" % (user, code, body.get("reason")))
        self.kube.delete_pod(u["sandbox"])
        del self.users[user]
        self._save()
        self.refill()
        return code, body

    def close(self, user: str, countdown_s: float) -> float:
        self.users[user]["deadline"] = self.clock() + countdown_s
        self._save()
        return self.users[user]["deadline"]

    def tick(self) -> List[Tuple[str, int]]:
        now = self.clock()
        done = []
        for user in [u for u, v in self.users.items() if v.get("deadline") and v["deadline"] <= now]:
            done.append((user, self.logout(user, reason="timeout")[0]))
        self.refill()
        return done

    def oc(self, user: str, method: str, path: str, body=None) -> Reply:
        u = self.users[user]
        auth = "Basic " + base64.b64encode(("opencode:%s" % u["password"]).encode()).decode()
        return self.t.call(u["sandbox"], OC_PORT, method, path, body, {"Authorization": auth}, timeout=600)

    def status(self) -> List[dict]:
        by_pod = {v["sandbox"]: u for u, v in self.users.items()}
        rows = []
        for p in self.kube.sandboxes():
            row = {"pod": p["name"], "ready": p["ready"], "deleting": p["deleting"], "user": by_pod.get(p["name"])}
            if p["ready"] and not p["deleting"]:
                code, st = self._ctl(p["name"], "GET", "/sandbox/status")
                row["state"] = st.get("state") if code == 200 else "?%s" % code
            rows.append(row)
        return rows

    def down(self) -> None:
        for user in list(self.users):
            try:
                self.logout(user, reason="admin")
            except PlatformError as exc:
                self.log("下线 %s:%s" % (user, exc))
        self.target = 0
        for p in self.kube.sandboxes():
            self.kube.delete_pod(p["name"])


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="pool-sim")
    ap.add_argument("--context", default=os.environ.get("CONTEXT", "orbstack"))
    ap.add_argument("--via", choices=("direct", "port-forward"), default=os.environ.get("POOL_VIA", "direct"))
    ap.add_argument("--image", default=os.environ.get("POOL_IMAGE", "gaussdb-agent-runtime:dev"))
    ap.add_argument("--pull", default=os.environ.get("POOL_PULL", "IfNotPresent"))
    ap.add_argument("--size", type=int, default=int(os.environ.get("POOL_SIZE", "16")))
    ap.add_argument("--state", default=os.environ.get("POOL_SIM_STATE", str(pathlib.Path.home() / ".pool-sim.json")))
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("up", "tick", "status", "down"):
        sub.add_parser(name)
    for name in ("login", "logout"):
        sub.add_parser(name).add_argument("user")
    p = sub.add_parser("close")
    p.add_argument("user")
    p.add_argument("--countdown", type=float, default=1800)
    p = sub.add_parser("oc")
    p.add_argument("user")
    p.add_argument("method", choices=("GET", "POST", "DELETE"))
    p.add_argument("path")
    p.add_argument("body", nargs="?")
    a = ap.parse_args(argv)
    kube = Kube(a.context)
    key = os.environ.get("SANDBOX_CONTROL_KEY") or kube.secret("sandbox-control", "KEY")
    plat = Platform(kube, PortForward(kube) if a.via == "port-forward" else Direct(kube), key, a.image, a.pull,
                    a.size, pathlib.Path(a.state).expanduser(), log=lambda m: print(m, file=sys.stderr))
    try:
        if a.cmd == "up":
            print(json.dumps({"created": plat.refill()}))
        elif a.cmd == "login":
            r = plat.login(a.user)
            print(json.dumps({k: r[k] for k in ("sandbox", "reused", "loaded") if k in r}, ensure_ascii=False))
        elif a.cmd == "logout":
            code, body = plat.logout(a.user)
            print(json.dumps(dict(body, http=code), ensure_ascii=False))
            return 0 if code == 200 else 1
        elif a.cmd == "close":
            print(json.dumps({"deadline": plat.close(a.user, a.countdown)}))
        elif a.cmd == "tick":
            print(json.dumps({"timed_out": plat.tick()}))
        elif a.cmd == "oc":
            code, body = plat.oc(a.user, a.method, a.path, json.loads(a.body) if a.body else None)
            print(json.dumps(body, ensure_ascii=False))
            return 0 if 200 <= code < 300 else 1
        elif a.cmd == "status":
            for row in plat.status():
                print(json.dumps(row, ensure_ascii=False))
        elif a.cmd == "down":
            plat.down()
    except PlatformError as exc:
        print(json.dumps({"error": exc.reason, "detail": exc.detail}, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
