"""Fuyao 运行模式的后台清扫与运维接口(2026-11-17)。

每一轮:
1. 把网关攒下的交互活动写进各沙箱的 metadata(`gsdb-active`)—— 两个网关副本共享这个时间;写失败的下一轮再写。
2. 在跑的沙箱闲置超过 `idle_s`、且没有会话在跑:先收尾同步(`podctl fuyao-stop`),再暂停(平台不支持暂停时
   `pause=False`,改为删除)。**收尾同步失败时不删除**(删除会丢最后一个同步周期),下一轮再试;
   暂停不丢数据(内存快照完整),所以收尾失败也照样暂停。
3. 暂停超过 `retain_s` 的删除(数据早已在收尾时写回 NAS)。
4. 平台寿命(只在 `sandbox_timeout_s > 0` 时有):平台到点直接删、不能续期、虚拟机收不到信号(实测),
   所以离到期不到 `expire_margin_s` 就收尾、放开独占后删除,下次请求按新沙箱重建。有会话在跑的等到最后三分之一再动;
   暂停的若暂停前已回写就直接删,没回写成的只能记日志(恢复后地址拿不准,收尾做不了)。
5. 换版(发版换了模板):旧版沙箱闲置时收尾并放开独占后删除,下次登录按新模板重建;收尾失败就照旧暂停
   (记 `gsdb-synced=0`),下次恢复后再来。已暂停且暂停前收尾成功(`gsdb-synced=1`)的旧版沙箱直接删除。
   在跑、有人在用的旧版沙箱不打断。`summary()` 里 `outdated` 是还剩多少台旧版,发版台据此看换版进度。

平台实测:删除即时生效、虚拟机里收不到退出信号(收尾必须在删除之前由这里触发);暂停约 2 秒、恢复约 3 秒、
内存快照完整续上(docs/specs/2026-11-17-fuyao-sandbox-design.md §2)。
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from . import opensandbox as osb
from .fuyaorouter import (APP, MD_ACTIVE, MD_APP, MD_PAUSED, MD_SYNCED, MD_TEMPLATE, FuyaoRouter, busy, expires_at,
                          outdated, owner, synced_before_pause)

ACTIONS = ("stop", "pause", "resume", "delete")


def _num(text: Optional[str]) -> float:
    try:
        return float(text or 0)
    except ValueError:
        return 0.0


def last_active(s: osb.Sandbox) -> float:
    return max(_num(s.metadata.get(MD_ACTIVE)), s.created_at)


class FuyaoSweeper:
    def __init__(self, router: FuyaoRouter, sweep_s: int = 30,
                 busy_check: Callable[[str, str], bool] = busy, log: Callable[[str], None] = print,
                 clock: Callable[[], float] = time.time):
        self.router, self.os, self.policy = router, router.os, router.policy
        self.sweep_s, self.busy_check = sweep_s, busy_check
        self.log, self.clock = log, clock
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- 一轮 ---------------------------------------------------------------------------------------

    def sweep(self, now: Optional[float] = None) -> Dict[str, int]:
        now = self.clock() if now is None else now
        counts = {"active": 0, "retired": 0, "deleted": 0, "expired": 0, "busy": 0, "failed": 0, "upgraded": 0}
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
                left = self._left(s, now)
                if s.running:
                    counts["active"] += 1
                    if left is not None:
                        r = self.expire(s, left)
                        counts[r] = counts.get(r, 0) + 1
                    elif now - seen >= self.policy.idle_s:
                        r = self.retire(s, now)
                        counts[r] = counts.get(r, 0) + 1
                elif s.paused:
                    paused_at = _num(s.metadata.get(MD_PAUSED)) or seen
                    if outdated(s, self.policy) and synced_before_pause(s):
                        self._delete(s, "模板已更新,暂停前已收尾回写")
                        counts["upgraded"] += 1
                    elif now - paused_at >= self.policy.retain_s:
                        self._delete(s, "暂停超过保留期")
                        counts["deleted"] += 1
                    elif left is not None and synced_before_pause(s):
                        self._delete(s, "快到平台寿命上限,暂停前已收尾回写")
                        counts["expired"] += 1
                    elif left is not None:
                        counts["failed"] += 1
                        self.log("Fuyao 清扫:%s 的沙箱 %s 还有 %d 秒到平台寿命上限,暂停前没回写成,到期会丢最后一个同步周期"
                                 % (user, s.id, int(left)))
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
        """闲置:收尾同步 → 暂停(或删除)。旧版沙箱收尾成功就删除(换版)。返回 retired / upgraded / busy / failed。"""
        user, _kind = owner(s)
        host = self.router.address(s)
        if user and self.busy_check(host, self.router.password_for(user)):
            return "busy"
        old = outdated(s, self.policy)
        synced = self.router.stop(s.id, host, release=old or not self.policy.pause)
        if synced and old:
            self._delete(s, "闲置,模板已更新;收尾后删除,下次登录按新模板重建")
            return "upgraded"
        if self.policy.pause:
            self.pause(s, now, synced)
            self.log("Fuyao:%s 的沙箱 %s 闲置,%s暂停" % (user, s.id, "已收尾并" if synced else "收尾未成,仍"))
            return "retired"
        if not synced:
            return "failed"                         # 不删:删了会丢最后一个同步周期,下一轮再试
        self._delete(s, "闲置(平台不支持暂停,收尾后删除)")
        return "retired"

    def pause(self, s: osb.Sandbox, now: float, synced: Optional[bool]) -> None:
        self.os.pause(s.id)
        self.os.patch_metadata(s.id, {MD_PAUSED: str(int(now)), MD_SYNCED: "1" if synced else "0"})
        self.router.forget_sandbox(s.id)

    def _delete(self, s: osb.Sandbox, why: str) -> None:
        self.os.delete(s.id)
        self.router.forget_sandbox(s.id)
        self.log("Fuyao:删除 %s 的沙箱 %s(%s)" % (owner(s)[0], s.id, why))

    def _left(self, s: osb.Sandbox, now: float) -> Optional[float]:
        """离平台寿命上限还剩几秒;不到 expire_margin_s 才返回,否则 None(没有寿命也是 None)。"""
        e = expires_at(s)
        if not e or e - now >= self.policy.expire_margin_s:
            return None
        return max(e - now, 0.0)

    def expire(self, s: osb.Sandbox, left: float) -> str:
        """快到平台寿命上限:收尾、放开独占后删除(平台到点直接删,虚拟机收不到信号)。返回 expired / busy / failed。"""
        user, _kind = owner(s)
        host = self.router.address(s)
        if user and left > self.policy.expire_margin_s / 3 and self.busy_check(host, self.router.password_for(user)):
            return "busy"                           # 有会话在跑:等到最后三分之一再动
        if not self.router.stop(s.id, host, release=True):
            return "failed"                         # 下一轮再试;真到期平台会删,至多丢最后一个同步周期
        self._delete(s, "快到平台寿命上限(还剩 %d 秒),已收尾回写" % int(left))
        return "expired"

    # -- 运维接口(/admin/fuyao) -------------------------------------------------------------------

    def summary(self) -> dict:
        boxes = self.os.list({MD_APP: APP})
        items = []
        for s in boxes:
            user, kind = owner(s)
            items.append({"sandbox": s.id, "user_id": user, "kind": kind, "state": s.state,
                          "template": s.metadata.get(MD_TEMPLATE, ""), "outdated": outdated(s, self.policy),
                          "last_active": last_active(s), "paused_at": _num(s.metadata.get(MD_PAUSED)) or None,
                          "expires_at": expires_at(s) or None})
        states: Dict[str, int] = {}
        for it in items:
            states[it["state"]] = states.get(it["state"], 0) + 1
        return {"mode": "fuyao", "sandboxes": items, "states": states,
                "templates": dict(self.policy.templates), "outdated": sum(1 for it in items if it["outdated"]),
                "policy": {"idle_seconds": self.policy.idle_s, "retain_seconds": self.policy.retain_s,
                           "pause": self.policy.pause}}

    def act(self, verb: str, sandbox_id: str) -> Tuple[int, dict]:
        if verb not in ACTIONS:
            return 404, {"error": "没有这个动作,只能是 %s" % " / ".join(ACTIONS)}
        # 不用单台查询:平台的 GET /v1/sandboxes/{id} 不带业务 metadata(实测),只能从列举里认是不是本智能体的
        s = next((b for b in self.os.list({MD_APP: APP}) if b.id == sandbox_id), None)
        if s is None:
            return 404, {"error": "没有这台沙箱(或不是本智能体的)"}
        now = self.clock()
        if verb == "resume":
            self.os.resume(s.id)
            self.os.patch_metadata(s.id, {MD_PAUSED: None, MD_SYNCED: None, MD_ACTIVE: str(int(now))})
            return 200, {"sandbox": s.id, "resumed": True}
        if verb == "stop":
            if not s.running:
                return 409, {"error": "沙箱不在运行(%s)" % s.state}
            return 200, {"sandbox": s.id, "synced": self.router.stop(s.id, self.router.address(s))}
        synced = None
        if s.running:
            synced = self.router.stop(s.id, self.router.address(s), release=verb == "delete")
        if verb == "pause":
            if not s.running:
                return 409, {"error": "沙箱不在运行(%s)" % s.state}
            self.pause(s, now, synced)
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
