"""Fuyao 运行模式下「这个请求转到哪」(2026-11-17):每人每种环境(诊断 / 导入)一台沙箱。

接口与固定池化的 PoolRouter 相同(target / note_activity / forget;异常 NotActive / PoolFull / PoolWait;
返回 poolrouter.Target),server.py 的池化转发路径直接复用。多出来的是 `kind` 参数(导入环境也是沙箱)。

  转发   本人沙箱在跑:缓存里有目标就用;没有(网关刚重启 / 另一个副本建的)就现查,口令探活通过即可。
  恢复   本人沙箱已暂停:恢复后重新取地址、探活。平台实测恢复后地址会变而接口可能仍回旧地址 ——
         候选地址(它恢复后报到时状态服务看到的来源地址、平台给的地址)都先**核实身份**再用:经 envd 读沙箱里
         启动器写下的沙箱令牌(root 才能读写),用网关的密钥验签、对上这台沙箱才往那里发口令、跑启动器或收尾。
         核实不了就等;一直核实不了而暂停前已回写的,删掉重建(见 address() 与 unlocated_droppable())。
  地址   不假设集群内不做源地址转换(2026-11-28 现场:Calico 集群把沙箱到状态服务的连接转成了节点地址,
         状态服务看到的来源地址全是节点地址):没恢复过的沙箱用平台给的地址;状态服务的来源地址只是候选,同样要核实。
  新建   本人没有沙箱:查开通前提 → 建沙箱(metadata 带工号与种类)→ 经 envd 下发配置并启动 → 探活。
         两个网关副本可能同时为同一人建:建完再列一次,只留最早的那台,多出来的删掉。
  满员   沙箱组没有空位:回等待页(PoolFull)。
  换版   发版换了模板(gateway-config 的 FUYAO_*_TEMPLATE):沙箱 metadata 记着建它的模板(`gsdb-template`)。
         暂停的旧版沙箱若暂停前已收尾回写(`gsdb-synced=1`)就删掉、按新模板重建;没回写成的照旧恢复,
         等它下次闲置由清扫器收尾后删除。在跑的旧版沙箱不打断,同样等闲置。

**非交互请求不建、不恢复**(探活、事件流),与固定池化同一条规矩:开着的标签页不能把暂停的沙箱拉起来。
"""
from __future__ import annotations

import http.client
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Tuple

from . import opensandbox as osb
from . import proxy, sandboxexec, sbxtoken
from .pods import KIND_KB_IMPORT, KIND_RUNTIME
from .poolrouter import NotActive, PoolFull, PoolWait, Target

APP = "gaussdb-agent"
MD_APP, MD_USER, MD_KIND, MD_ACTIVE, MD_PAUSED = "gsdb-app", "gsdb-user", "gsdb-kind", "gsdb-active", "gsdb-paused"
MD_TEMPLATE, MD_SYNCED = "gsdb-template", "gsdb-synced"     # 建它的模板;暂停前收尾回写是否成功("1" / "0")
MD_EXPIRES = "gsdb-expires"                                  # 平台寿命的到期时刻(只在 sandbox_timeout_s > 0 时有)
# 恢复过(恢复时刻):平台 /endpoints 从此可能回暂停前的旧地址(实测),而旧地址可能已分给别人的新沙箱 ——
# 这台的地址一律先核实身份再用(2026-11-18 起只认报到地址;2026-11-28 改为核实,不依赖报到时的来源地址)
MD_RESUMED = "gsdb-resumed"
# 恢复时「暂停前已收尾回写」("1"):恢复后第一次核实到地址就清掉(之后用户可能写了新内容)。
# 一直核实不了地址、这个标记还在的,删掉重建不丢数据(unlocated_droppable)
MD_RESUMED_SYNCED = "gsdb-resumed-synced"
RESUME_LOCATE_GRACE_S = 60.0                     # 恢复后这么久仍核实不了地址,才按「找不到」处理(正常 3–4 秒报到)
# 启动器写下的沙箱令牌(docker/fuyao.py TOKEN_FILE:/run/gsdb 0700、文件 0600,只有 root 能读写)
TOKEN_PATH = "/run/gsdb/sandbox-token"
IDENTIFY_TIMEOUT_S = 5.0
# 核实结果:是它 / 是别的沙箱(令牌属于别人或验签不过)/ 还没跑过启动器 / envd 连不上
ID_MATCH, ID_OTHER, ID_ABSENT, ID_UNREACHABLE = "match", "other", "absent", "unreachable"
STALE_POLL_S = 0.5                               # 等恢复后的沙箱报到:每这么久问一次状态服务
OPENCODE_PORT, REPORTS_PORT, CLIP_PORT = 4096, 4097, 4095
KINDS = (KIND_RUNTIME, KIND_KB_IMPORT)
RELAUNCH_GAP_S = 30.0
MAX_LAUNCH_FAILS = 3                             # 同一台沙箱里启动器接连失败几次算「起不来」


def _num(text: Optional[str]) -> float:
    try:
        return float(text or 0)
    except ValueError:
        return 0.0


class AddressUnknown(Exception):
    """没有核实过的地址:候选地址(平台给的、报到时看到的)上都不是这台沙箱,或都连不上。"""

    def __init__(self, sandbox_id: str):
        super().__init__("沙箱 %s 的地址核实不了(平台给的与它报到时看到的地址上都不是它)" % sandbox_id)
        self.sandbox_id = sandbox_id


class LaunchError(Exception):
    """启动器在沙箱里失败了(退出码非 0)。"""


class SandboxBroken(Exception):
    """同一台沙箱里启动器接连失败:不再让用户对着「正在准备」干等,回「环境准备失败」并记日志。
    不自动重建 —— 模板本身坏了重建只会循环;运维看日志后经 /admin/fuyao 删除,下次登录重建。"""


@dataclass(frozen=True)
class Policy:
    templates: Mapping[str, str]                 # 种类 → E2B 模板名
    groups: Mapping[str, str]                    # 种类 → 沙箱组
    cpu: str = "2"
    memory: str = "4Gi"
    sandbox_timeout_s: int = 0                   # 0 = 不给平台寿命(实测:不带 timeout 就没有到期时间)。
    # >0:平台到点直接删、不能续期、虚拟机收不到信号(实测,上限 24 小时)—— 清扫器在到期前 expire_margin_s 收尾后删除
    expire_margin_s: int = 900
    idle_s: int = 1800                           # 闲置多久收尾并暂停(或删除)
    retain_s: int = 7 * 86400                    # 暂停多久后删除
    pause: bool = True                           # False:闲置即收尾后删除(平台不支持暂停时)
    wait_s: float = 90.0                         # 一次请求最多等多久;等不到回等待页
    retry_after: int = 30
    launch_argv: Tuple[str, ...] = ("python3", "/opt/agent/podctl.py", "fuyao-start")
    stop_argv: Tuple[str, ...] = ("python3", "/opt/agent/podctl.py", "fuyao-stop")
    launch_timeout_s: float = 150.0
    stop_timeout_s: float = 150.0


def probe(host: str, password: str, port: int = OPENCODE_PORT, timeout: float = 3.0) -> bool:
    """opencode 就绪且口令对得上(连到别人的沙箱会 401)。"""
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", "/global/health", headers={"Authorization": proxy.basic_auth(password)})
        r = conn.getresponse()
        r.read()
        return r.status == 200
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def busy(host: str, password: str, port: int = OPENCODE_PORT, timeout: float = 5.0) -> bool:
    """有会话在跑(opencode 1.18.27:GET /session/status,type 为 busy / retry)。连不上按不忙算。"""
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", "/session/status", headers={"Authorization": proxy.basic_auth(password)})
        r = conn.getresponse()
        data = r.read()
        if r.status != 200:
            return False
        st = json.loads(data or b"{}")
    except (OSError, http.client.HTTPException, ValueError):
        return False
    finally:
        conn.close()
    return isinstance(st, dict) and any(isinstance(v, dict) and v.get("type") in ("busy", "retry") for v in st.values())


def owner(s: osb.Sandbox) -> Tuple[str, str]:
    return s.metadata.get(MD_USER, ""), s.metadata.get(MD_KIND, "")


def outdated(s: osb.Sandbox, policy: Policy) -> bool:
    """不是按当前模板建的(发版换了模板;没记模板的老沙箱也算)。"""
    want = policy.templates.get(owner(s)[1], "")
    return bool(want) and s.metadata.get(MD_TEMPLATE, "") != want


def synced_before_pause(s: osb.Sandbox) -> bool:
    return s.metadata.get(MD_SYNCED) == "1"


def expires_at(s: osb.Sandbox) -> float:
    """平台寿命的到期时刻;0 = 没有。列举接口不回 expiresAt(实测),建沙箱时记进 metadata。"""
    try:
        return s.expires_at or float(s.metadata.get(MD_EXPIRES) or 0)
    except ValueError:
        return 0.0


class FuyaoRouter:
    handles_kb_import = True                     # server.py 据此把导入环境也交给本路由器

    def __init__(self, client: osb.Client, envd: sandboxexec.Envd, policy: Policy, key: bytes,
                 password_for: Callable[[str], str], launch_env: Callable[[str, str], Mapping[str, str]],
                 prereq: Optional[Callable[[str, str], None]] = None,
                 prober: Callable[[str, str], bool] = probe, log: Callable[[str], None] = print,
                 locate: Optional[Callable[[str, str, str], Optional[str]]] = None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep):
        if len(key) < 16:
            raise ValueError("控制密钥至少 16 字节")
        self.os, self.envd, self.policy, self.key = client, envd, policy, key
        self.password_for, self.launch_env, self.prereq, self.prober = password_for, launch_env, prereq, prober
        self.log, self.clock, self.sleep, self.locate = log, clock, sleep, locate
        self._creds: Dict[Tuple[str, str], Target] = {}
        self._locks: Dict[Tuple[str, str], threading.Lock] = {}
        self._launched: Dict[str, float] = {}
        self._fails: Dict[str, int] = {}             # 沙箱 → 启动器接连失败次数(连不上 envd 不算)
        self._where: Dict[str, Tuple[str, str]] = {}  # 沙箱 → (恢复时刻标记, 用过的地址):同一次恢复内地址不变
        self._noted: set = set()                     # 已记过「平台地址上是别的沙箱」的沙箱(等待时每秒重试,只记一次)
        self._activity: Dict[Tuple[str, str], float] = {}
        self._lock = threading.Lock()

    # -- 活动(交互请求才记;回收器定期写进沙箱 metadata) -------------------------------------------

    def note_activity(self, user: str, kind: str = KIND_RUNTIME) -> None:
        with self._lock:
            self._activity[(user, kind)] = self.clock()

    def drain_activity(self) -> Dict[Tuple[str, str], float]:
        with self._lock:
            out, self._activity = self._activity, {}
        return out

    def restore_activity(self, acts: Mapping[Tuple[str, str], float]) -> None:
        with self._lock:
            for k, ts in acts.items():
                if ts > self._activity.get(k, 0.0):
                    self._activity[k] = ts

    def forget(self, user: str, kind: Optional[str] = None) -> None:
        """缓存的目标作废(转发失败 / 沙箱被暂停或删除),下次重新查。"""
        with self._lock:
            for k in [k for k in self._creds if k[0] == user and (kind is None or k[1] == kind)]:
                del self._creds[k]

    def forget_sandbox(self, sandbox_id: str) -> None:
        with self._lock:
            for k in [k for k, t in self._creds.items() if t.pod == sandbox_id]:
                del self._creds[k]
            self._launched.pop(sandbox_id, None)
            self._fails.pop(sandbox_id, None)
            self._where.pop(sandbox_id, None)
            self._noted.discard(sandbox_id)

    def cached(self) -> Dict[Tuple[str, str], Target]:
        with self._lock:
            return dict(self._creds)

    # -- 主入口 -------------------------------------------------------------------------------------

    def target(self, user: str, interactive: bool = True, kind: str = KIND_RUNTIME) -> Target:
        if kind not in KINDS:
            raise ValueError("不认识的环境种类 %r" % kind)
        key = (user, kind)
        with self._lock:
            got = self._creds.get(key)
            lock = self._locks.setdefault(key, threading.Lock())
        if got is not None:
            return got
        with lock:
            with self._lock:
                got = self._creds.get(key)
            if got is not None:
                return got
            deadline = self.clock() + self.policy.wait_s
            while True:
                got, why = self._attempt(user, kind, interactive)
                if got is not None:
                    with self._lock:
                        self._creds[key] = got
                    return got
                if self.clock() >= deadline:
                    raise PoolWait(why, min(self.policy.retry_after, 10))
                self.sleep(STALE_POLL_S if why == "address_stale" else 1.0)

    # -- 各条路 -------------------------------------------------------------------------------------

    def mine(self, user: str, kind: str) -> List[osb.Sandbox]:
        """本人这一种环境的沙箱,最早建的在前(两个副本并发建出两台时,大家都认最早那台)。"""
        boxes = self.os.list({MD_APP: APP, MD_USER: user, MD_KIND: kind})
        return sorted((b for b in boxes if b.running or b.paused or b.state in ("Pending", "Creating", "")),
                      key=lambda b: (b.created_at, b.id))

    def _attempt(self, user: str, kind: str, interactive: bool) -> Tuple[Optional[Target], str]:
        try:
            boxes = self.mine(user, kind)
        except osb.OpenSandboxError as exc:
            self.log("Fuyao:列举 %s 的沙箱失败:%s" % (user, exc))
            return None, "platform_unreachable"
        if boxes and boxes[0].paused and outdated(boxes[0], self.policy) and synced_before_pause(boxes[0]):
            if not interactive:
                raise NotActive(user)
            self._drop(boxes[0].id, "模板已更新,暂停前已收尾回写;按新模板重建")
            boxes = boxes[1:]
        if not boxes:
            if not interactive:
                raise NotActive(user)
            if self.prereq is not None:
                self.prereq(user, kind)              # PrerequisiteMissing 原样抛给 server.py
            created = self._create(user, kind)
            try:
                boxes = self.mine(user, kind)
            except osb.OpenSandboxError:
                boxes = [created]
            keeper = boxes[0] if boxes else created
            if keeper.id != created.id:              # 另一个副本先建好了:用它的,把自己这台删掉
                self._drop(created.id, "并发重复")
            return self._ready(keeper, user, kind, launch=keeper.id == created.id)
        keeper, extra = boxes[0], boxes[1:]
        for b in extra:
            self._drop(b.id, "同一人同一种环境多出来的")
        if keeper.paused:
            if not interactive:
                raise NotActive(user)
            try:
                at = self.resume(keeper.id, synced=synced_before_pause(keeper))
            except osb.OpenSandboxError as exc:
                self.log("Fuyao:恢复 %s 的沙箱 %s 失败:%s" % (user, keeper.id, exc))
                return None, "resume_failed"
            self.log("Fuyao:%s 的%s沙箱 %s 已恢复" % (user, _kind_name(kind), keeper.id))
            return self._ready(keeper, user, kind, launch=False, resumed_at=at)
        if keeper.running:
            return self._ready(keeper, user, kind, launch=False)
        return None, "starting:%s" % (keeper.state or "unknown")

    def resume(self, sandbox_id: str, synced: bool = False) -> float:
        """恢复一台暂停的沙箱,返回恢复时刻。**先**写恢复标记再恢复:标记写不进就不恢复 —— 否则恢复后的沙箱
        不带标记,别的副本 / 清扫器会把平台给的地址当成新建时的地址用。synced:暂停前已收尾回写(记进
        MD_RESUMED_SYNCED,恢复后一直找不到时据此判断能不能删掉重建)。"""
        at = self.clock()
        self.os.patch_metadata(sandbox_id, {MD_RESUMED: str(int(at)), MD_RESUMED_SYNCED: "1" if synced else None})
        self.os.resume(sandbox_id)
        self.os.patch_metadata(sandbox_id, {MD_PAUSED: None, MD_SYNCED: None, MD_ACTIVE: str(int(at))})
        return float(int(at))

    def _create(self, user: str, kind: str) -> osb.Sandbox:
        p = self.policy
        now = self.clock()
        md = {MD_APP: APP, MD_USER: user, MD_KIND: kind, MD_ACTIVE: str(int(now)), MD_TEMPLATE: p.templates[kind]}
        if p.sandbox_timeout_s > 0:          # 建之前取时刻:比平台算的早一点,只会提前收尾
            md[MD_EXPIRES] = str(int(now + p.sandbox_timeout_s))
        try:
            s = self.os.create(p.templates[kind], p.groups[kind], md, p.cpu, p.memory, p.sandbox_timeout_s)
        except osb.CapacityError:
            raise PoolFull(p.retry_after)
        except osb.OpenSandboxError as exc:
            if 500 <= exc.status < 600:
                # 平台满员时,OpenSandbox 把调度器的 503「no available agent」包成 500(鲲鹏 2026-11-18 实测,平台缺陷):
                # 5xx 一律按「平台暂时没有空位」让用户等、自动重试,平台原话记日志;4xx 是请求本身不对,照旧当错误
                self.log("Fuyao:为 %s 建%s沙箱时平台回 %d,按暂时没有空位处理、让用户等待重试(平台原话:%s)"
                         % (user, _kind_name(kind), exc.status, str(exc)[:300]))
                raise PoolWait("platform_busy", p.retry_after)
            raise
        self.log("Fuyao:为 %s 建%s沙箱 %s(模板 %s,沙箱组 %s)" % (user, _kind_name(kind), s.id, p.templates[kind],
                                                         p.groups[kind]))
        return s

    def _drop(self, sandbox_id: str, why: str) -> None:
        try:
            self.os.delete(sandbox_id)
            self.log("Fuyao:删除沙箱 %s(%s)" % (sandbox_id, why))
        except osb.OpenSandboxError as exc:
            self.log("Fuyao:删除沙箱 %s(%s)失败,交给回收器:%s" % (sandbox_id, why, exc))
        self.forget_sandbox(sandbox_id)

    def address(self, s: osb.Sandbox, resumed_at: Optional[float] = None) -> str:
        """这台沙箱现在的地址,往那里发口令、跑启动器或收尾之前必经这里。
        没恢复过:平台给的地址(新建时平台给的就是它的)。核实时发现那里是**别的**沙箱,才退到它报到时状态服务
        看到的来源地址,同样要核实。连不上 envd / 还没跑过启动器的照旧用平台地址,由启动与探活报错。
        恢复过(resumed_at,或 metadata 的恢复时刻):平台可能回暂停前的旧地址(实测),报到时的来源地址可能被
        集群做了源地址转换(2026-11-28 现场:全是节点地址)—— 两个候选都**必须核实是它**才用,否则抛 AddressUnknown。
        同一次恢复内地址不变,核实过的记在 _where,不每次都读令牌。"""
        user, kind = owner(s)
        if resumed_at is None:
            resumed_at = _num(s.metadata.get(MD_RESUMED)) or None
        mark = str(int(resumed_at)) if resumed_at else ""
        with self._lock:
            hit = self._where.get(s.id)
        if hit is not None and hit[0] == mark:
            return hit[1]
        host = self._fresh_address(s, user, kind) if resumed_at is None else \
            self._resumed_address(s, user, kind, resumed_at)
        with self._lock:
            self._where[s.id] = (mark, host)
        return host

    def _fresh_address(self, s: osb.Sandbox, user: str, kind: str) -> str:
        plat = self.os.address(s.id)
        if self.identify(plat, s) != ID_OTHER:
            return plat
        rep = self._reported(s, user, kind, since=None)
        found = bool(rep) and rep != plat and self.identify(rep, s) == ID_MATCH
        with self._lock:
            first = s.id not in self._noted
            self._noted.add(s.id)
        if first:
            self.log("Fuyao:平台给沙箱 %s 的地址上是别的沙箱,%s" % (s.id, "改用它报到时的地址(已核实)" if found
                                                              else "报到地址也核实不了,先不碰它"))
        if found:
            return rep
        raise AddressUnknown(s.id)

    def _resumed_address(self, s: osb.Sandbox, user: str, kind: str, resumed_at: float) -> str:
        cands: List[str] = []
        rep = self._reported(s, user, kind, since=resumed_at)
        if rep:
            cands.append(rep)
        try:
            plat = self.os.address(s.id)
        except osb.OpenSandboxError as exc:
            self.log("Fuyao:取 %s 的地址失败:%s" % (s.id, exc))
            plat = ""
        if plat and plat not in cands:
            cands.append(plat)
        for host in cands:
            if self.identify(host, s) == ID_MATCH:
                # 第一次找到它:清掉「恢复前已回写」—— 之后用户可能写了新内容,不能再按「删了不丢」处理。
                # 清不掉就先不用这个地址(宁可让用户等,也不留着一个可能误删的标记)
                self.os.patch_metadata(s.id, {MD_RESUMED_SYNCED: None})
                return host
        raise AddressUnknown(s.id)

    def _reported(self, s: osb.Sandbox, user: str, kind: str, since: Optional[float]) -> Optional[str]:
        """状态服务最近看到这台沙箱报到的来源地址;since:只认这个时刻之后的(恢复之前的报到是旧地址)。"""
        if self.locate is None or not user:
            return None
        try:
            got = self.locate(s.id, user, kind)
        except Exception as exc:                    # noqa: BLE001 —— 状态服务问不到:只是少一个候选
            self.log("Fuyao:向状态服务问沙箱 %s 的地址失败:%s" % (s.id, exc))
            return None
        if not got:
            return None
        addr, age = got
        if since is not None and age > max(self.clock() - since, 0.0):
            return None
        return addr

    def identify(self, host: str, s: osb.Sandbox) -> str:
        """这个地址上是不是这台沙箱:经 envd 读启动器写下的沙箱令牌(root 才能读写),用网关的密钥验签,
        沙箱 ID、工号、种类都对上才算。令牌只在网关与沙箱之间传,不进日志。"""
        try:
            res = self.envd.run(host, ["cat", TOKEN_PATH], user="root", timeout_s=IDENTIFY_TIMEOUT_S)
        except sandboxexec.ExecError:
            return ID_UNREACHABLE
        if not res.ok:
            return ID_ABSENT
        c = sbxtoken.verify(self.key, res.stdout.strip())
        user, kind = owner(s)
        if c is not None and (c.sandbox_id, c.user_id, c.kind) == (s.id, user, kind):
            return ID_MATCH
        return ID_OTHER

    def unlocated_droppable(self, s: osb.Sandbox) -> bool:
        """恢复后过了宽限期仍核实不了地址、恢复前已收尾回写、恢复后从没找到过(标记还在):删掉重建不丢数据。
        (源地址转换的集群里平台又回旧地址时,恢复的沙箱永远找不到 —— 不删就一直占着名额,用户一直等。)"""
        at = _num(s.metadata.get(MD_RESUMED))
        return bool(at) and s.metadata.get(MD_RESUMED_SYNCED) == "1" and self.clock() - at >= RESUME_LOCATE_GRACE_S

    def _ready(self, s: osb.Sandbox, user: str, kind: str, launch: bool,
               resumed_at: Optional[float] = None) -> Tuple[Optional[Target], str]:
        resumed = resumed_at is not None
        try:
            host = self.address(s, resumed_at=resumed_at)
        except AddressUnknown:
            if self.unlocated_droppable(s):
                self._drop(s.id, "恢复后 %d 秒仍核实不了地址;暂停前已收尾回写,删掉重建"
                           % int(self.clock() - _num(s.metadata.get(MD_RESUMED))))
                return None, "starting"
            return None, "address_stale"
        except osb.OpenSandboxError as exc:
            self.log("Fuyao:取 %s 的地址失败:%s" % (s.id, exc))
            return None, "no_address"
        password = self.password_for(user)
        if launch:
            try:
                self.launch(s.id, host, user, kind, password)
            except (LaunchError, sandboxexec.ExecError) as exc:
                self.log("Fuyao:%s 的%s沙箱 %s 启动失败:%s" % (user, _kind_name(kind), s.id, exc))
                self._failed(s.id, exc)
                return None, "launch_failed"
        if self.prober(host, password):
            return self._ok(s.id, host, password), "ready"
        # 没就绪:另一个副本还在启动它 / 上次启动半途而废(网关重启)/ 恢复后地址还没对上。
        # envd 在、隔一阵没起过 → 再起一次(启动器幂等)。
        now = self.clock()
        if not resumed and now - self._launched.get(s.id, 0.0) >= RELAUNCH_GAP_S and self.envd.healthy(host):
            try:
                self.launch(s.id, host, user, kind, password)
            except (LaunchError, sandboxexec.ExecError) as exc:
                self.log("Fuyao:%s 的沙箱 %s 补起失败:%s" % (user, s.id, exc))
                self._failed(s.id, exc)
                return None, "launch_failed"
            if self.prober(host, password):
                return self._ok(s.id, host, password), "ready"
        return None, "address_stale" if resumed else "starting"

    def _ok(self, sandbox_id: str, host: str, password: str) -> Target:
        with self._lock:
            self._fails.pop(sandbox_id, None)
        return self._target(sandbox_id, host, password)

    def _failed(self, sandbox_id: str, exc: Exception) -> None:
        if not isinstance(exc, LaunchError):
            return                                   # envd 连不上:可能是网络抖动,不算沙箱坏
        with self._lock:
            n = self._fails[sandbox_id] = self._fails.get(sandbox_id, 0) + 1
        if n >= MAX_LAUNCH_FAILS:
            raise SandboxBroken("沙箱 %s 启动器接连失败 %d 次(最近一次:%s);可在 /admin/fuyao 删除后重建"
                                % (sandbox_id, n, exc))

    def launch(self, sandbox_id: str, host: str, user: str, kind: str, password: str) -> None:
        env = sandboxexec.env_from(self.launch_env(user, kind))
        env.update({
            "GSDB_USER_ID": user,
            "GSDB_KIND": kind,
            "GSDB_SANDBOX_ID": sandbox_id,
            "GSDB_SANDBOX_TOKEN": sbxtoken.issue(self.key, sandbox_id, user, kind, now=self.clock()),
            "OPENCODE_SERVER_PASSWORD": password,
            "GSDB_REPORTS_TOKEN": sbxtoken.reports_token(self.key, sandbox_id),
        })
        with self._lock:
            self._launched[sandbox_id] = self.clock()
        res = self.envd.run(host, list(self.policy.launch_argv), user="root", envs=env,
                            timeout_s=self.policy.launch_timeout_s)
        if not res.ok:
            tail = (res.stderr or res.stdout).strip().splitlines()[-3:]
            raise LaunchError("退出码 %d:%s" % (res.exit_code, " / ".join(tail)[:400]))

    def stop(self, sandbox_id: str, host: str, release: bool = False) -> bool:
        """收尾同步(删除 / 暂停之前)。release:同时放开本人目录的独占(删除之前;暂停时不放,回来还是这台)。"""
        argv = list(self.policy.stop_argv) + (["--release"] if release else [])
        try:
            res = self.envd.run(host, argv, user="root", timeout_s=self.policy.stop_timeout_s)
        except sandboxexec.ExecError as exc:
            self.log("Fuyao:沙箱 %s 收尾同步时连不上:%s" % (sandbox_id, exc))
            return False
        if not res.ok:
            tail = (res.stderr or res.stdout).strip().splitlines()[-2:]
            self.log("Fuyao:沙箱 %s 收尾同步失败(退出码 %d):%s" % (sandbox_id, res.exit_code, " / ".join(tail)[:300]))
        return res.ok

    def _target(self, sandbox_id: str, host: str, password: str) -> Target:
        return Target(pod=sandbox_id, slot=0, host=host, port=OPENCODE_PORT, password=password,
                      reports_port=REPORTS_PORT, reports_token=sbxtoken.reports_token(self.key, sandbox_id),
                      since=self.clock())


def _kind_name(kind: str) -> str:
    return "导入" if kind == KIND_KB_IMPORT else "诊断"
