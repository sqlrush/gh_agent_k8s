"""Fuyao 运行模式的后台清扫与运维接口(2026-11-17)。

每一轮:
1. 把网关攒下的交互活动写进各沙箱的 metadata(`gsdb-active`)—— 两个网关副本共享这个时间;写失败的下一轮再写。
2. 在跑的沙箱闲置超过 `idle_s`、且没有会话在跑:先收尾同步(`podctl fuyao-stop`),再暂停(平台不支持暂停时
   `pause=False`,改为删除)。**收尾同步失败时不删除**(删除会丢最后一个同步周期),下一轮再试;
   暂停不丢数据(内存快照完整),所以收尾失败也照样暂停。
3. 暂停超过 `retain_s` 的删除(数据早已在收尾时写回 NAS)。
4. 离平台寿命上限不到 `renew_margin_s` 的续期:在跑的续一个完整寿命;暂停的续到保留期满为止。

平台实测:删除即时生效、虚拟机里收不到退出信号(收尾必须在删除之前由这里触发);暂停约 2 秒、恢复约 3 秒、
内存快照完整续上(docs/specs/2026-11-17-fuyao-sandbox-design.md §2)。
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from . import opensandbox as osb
from .fuyaorouter import APP, MD_ACTIVE, MD_APP, MD_PAUSED, FuyaoRouter, busy, owner

ACTIONS = ("stop", "pause", "resume", "delete")


def _num(text: Optional[str]) -> float:
    try:
        return float(text or 0)
    except ValueError:
        return 0.0


def last_active(s: osb.Sandbox) -> float:
    return max(_num(s.metadata.get(MD_ACTIVE)), s.created_at)


class FuyaoSweeper:
    def __init__(self, router: FuyaoRouter, sweep_s: int = 30, renew_margin_s: int = 3600,
                 busy_check: Callable[[str, str], bool] = busy, log: Callable[[str], None] = print,
                 clock: Callable[[], float] = time.time):
        self.router, self.os, self.policy = router, router.os, router.policy
        self.sweep_s, self.renew_margin_s, self.busy_check = sweep_s, renew_margin_s, busy_check
        self.log, self.clock = log, clock
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- 一轮 ---------------------------------------------------------------------------------------

    def sweep(self, now: Optional[float] = None) -> Dict[str, int]:
        now = self.clock() if now is None else now
        counts = {"active": 0, "retired": 0, "deleted": 0, "renewed": 0, "busy": 0, "failed": 0}
        acts = self.router.drain_activity()
        try:
            boxes = self.os.list({MD_APP: APP})
        except osb.OpenSandboxError as exc:
            self.log("Fuyao 清扫:列举沙箱失败:%s" % exc)
            self.router.restore_activity(acts)
            return counts
        self._flush_activity(boxes, acts)
        for s in boxes:
            user, kind = owner(s)
            seen = max(last_active(s), acts.get((user, kind), 0.0))
            try:
                if s.running:
                    counts["active"] += 1
                    if now - seen >= self.policy.idle_s:
                        r = self.retire(s, now)
                        counts[r] = counts.get(r, 0) + 1
                    elif self._renew(s, now, now + self.policy.sandbox_timeout_s):
                        counts["renewed"] += 1
                elif s.paused:
                    paused_at = _num(s.metadata.get(MD_PAUSED)) or seen
                    if now - paused_at >= self.policy.retain_s:
                        self._delete(s, "暂停超过保留期")
                        counts["deleted"] += 1
                    elif self._renew(s, now, paused_at + self.policy.retain_s):
                        counts["renewed"] += 1
            except osb.OpenSandboxError as exc:
                counts["failed"] += 1
                self.log("Fuyao 清扫:处理沙箱 %s(%s)失败:%s" % (s.id, user, exc))
        return counts

    def _flush_activity(self, boxes: List[osb.Sandbox], acts: Dict[Tuple[str, str], float]) -> None:
        by_owner: Dict[Tuple[str, str], List[osb.Sandbox]] = {}
        for s in boxes:
            by_owner.setdefault(owner(s), []).append(s)
        failed: Dict[Tuple[str, str], float] = {}
        for k, ts in acts.items():
            for s in by_owner.get(k, []):
                if not (s.running or s.paused) or _num(s.metadata.get(MD_ACTIVE)) >= int(ts):
                    continue
                try:
                    self.os.patch_metadata(s.id, {MD_ACTIVE: str(int(ts))})
                except osb.OpenSandboxError:
                    failed[k] = ts
        if failed:
            self.router.restore_activity(failed)

    def retire(self, s: osb.Sandbox, now: float) -> str:
        """闲置:收尾同步 → 暂停(或删除)。返回 retired / busy / failed。"""
        user, _kind = owner(s)
        host = self.os.address(s.id)
        if user and self.busy_check(host, self.router.password_for(user)):
            return "busy"
        synced = self.router.stop(s.id, host, release=not self.policy.pause)
        if self.policy.pause:
            self.os.pause(s.id)
            self.os.patch_metadata(s.id, {MD_PAUSED: str(int(now))})
            self.router.forget_sandbox(s.id)
            self.log("Fuyao:%s 的沙箱 %s 闲置,%s暂停" % (user, s.id, "已收尾并" if synced else "收尾未成,仍"))
            return "retired"
        if not synced:
            return "failed"                         # 不删:删了会丢最后一个同步周期,下一轮再试
        self._delete(s, "闲置(平台不支持暂停,收尾后删除)")
        return "retired"

    def _delete(self, s: osb.Sandbox, why: str) -> None:
        self.os.delete(s.id)
        self.router.forget_sandbox(s.id)
        self.log("Fuyao:删除 %s 的沙箱 %s(%s)" % (owner(s)[0], s.id, why))

    def _renew(self, s: osb.Sandbox, now: float, until: float) -> bool:
        if not s.expires_at or s.expires_at - now >= self.renew_margin_s or until <= s.expires_at:
            return False
        self.os.renew(s.id, until)
        return True

    # -- 运维接口(/admin/fuyao) -------------------------------------------------------------------

    def summary(self) -> dict:
        boxes = self.os.list({MD_APP: APP})
        items = []
        for s in boxes:
            user, kind = owner(s)
            items.append({"sandbox": s.id, "user_id": user, "kind": kind, "state": s.state,
                          "last_active": last_active(s), "paused_at": _num(s.metadata.get(MD_PAUSED)) or None,
                          "expires_at": s.expires_at or None})
        states: Dict[str, int] = {}
        for it in items:
            states[it["state"]] = states.get(it["state"], 0) + 1
        return {"mode": "fuyao", "sandboxes": items, "states": states,
                "policy": {"idle_seconds": self.policy.idle_s, "retain_seconds": self.policy.retain_s,
                           "pause": self.policy.pause}}

    def act(self, verb: str, sandbox_id: str) -> Tuple[int, dict]:
        if verb not in ACTIONS:
            return 404, {"error": "没有这个动作,只能是 %s" % " / ".join(ACTIONS)}
        s = self.os.get(sandbox_id)
        if s is None or s.metadata.get(MD_APP) != APP:
            return 404, {"error": "没有这台沙箱(或不是本智能体的)"}
        now = self.clock()
        if verb == "resume":
            self.os.resume(s.id)
            self.os.patch_metadata(s.id, {MD_PAUSED: None, MD_ACTIVE: str(int(now))})
            return 200, {"sandbox": s.id, "resumed": True}
        if verb == "stop":
            if not s.running:
                return 409, {"error": "沙箱不在运行(%s)" % s.state}
            return 200, {"sandbox": s.id, "synced": self.router.stop(s.id, self.os.address(s.id))}
        synced = None
        if s.running:
            synced = self.router.stop(s.id, self.os.address(s.id), release=verb == "delete")
        if verb == "pause":
            if not s.running:
                return 409, {"error": "沙箱不在运行(%s)" % s.state}
            self.os.pause(s.id)
            self.os.patch_metadata(s.id, {MD_PAUSED: str(int(now))})
            self.router.forget_sandbox(s.id)
            return 200, {"sandbox": s.id, "paused": True, "synced": synced}
        self._delete(s, "运维手动删除")
        return 200, {"sandbox": s.id, "deleted": True, "synced": synced,
                     "note": "用户数据在 NAS 上(收尾同步把最后的状态写回),下次登录重新载入"}

    # -- 线程 ---------------------------------------------------------------------------------------

    def start(self) -> None:
        def loop() -> None:
            while not self._stop.wait(self.sweep_s):
                try:
                    self.sweep()
                except Exception as exc:                # noqa: BLE001 —— 清扫线程不能因为一轮出错就死掉
                    self.log("Fuyao 清扫:这一轮出错:%s" % exc)
        self._thread = threading.Thread(target=loop, name="fuyaosweeper", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
