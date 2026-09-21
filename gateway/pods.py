"""按工号建 / 查 / 删 Pod,以及记活动时间。

逻辑与 scripts/k8s/provision.py 同源(那个脚本是给人用的参考实现与本地验证工具),
但有两处**必须不同**:

① **口令沿用,不轮换。** provision.py 每次都 `secrets.token_hex(24)` 重新生成
   pod-auth-<工号>。手工跑没关系,网关不行:用户正开着页面时重建 Pod,口令一变
   他下一次请求就是 401,而页面上看不出为什么(2026-09-21 我就这么打断过一次测试)。
   这里只在 Secret 不存在或内容坏掉时才生成。

② **活动时间要节流。** 每个请求都去 PATCH 一次注解的话,一个用户开着 SSE 每 10 秒
   一跳就是每 10 秒一次 k8s 写;几十个用户足以把 apiserver 压出问题。

状态一律落在 k8s 对象上(Deployment 注解),不放进网关进程内存 —— 网关是 2 副本,
而且会重启;把「最后活动时间」放内存等于重启后全体 Pod 立刻显得「刚活动过」或
「早就该回收」,两种都错。
"""
from __future__ import annotations

import base64
import pathlib
import secrets
import string
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

APPS = "/apis/apps/v1"
CORE = "/api/v1"

ANNOTATION_ACTIVITY = "gaussdb-agent/last-activity"
SECRET_KEY_PASSWORD = "OPENCODE_SERVER_PASSWORD"

# 「本人 Pod」的两种形态。顺序有意义:runtime 先建,它是浏览器落地的那个。
KIND_RUNTIME = "runtime"
KIND_KB_IMPORT = "kb-import"


@dataclass(frozen=True)
class PodState:
    user_id: str
    kind: str
    exists: bool
    ready: bool
    last_activity: float = 0.0


class Manager:
    def __init__(self, kube, namespace: str, image_tag: str, pull_policy: str,
                 templates: pathlib.Path, activity_interval_s: int = 60):
        self.kube = kube
        self.namespace = namespace
        self.image_tag = image_tag
        self.pull_policy = pull_policy
        self.templates = pathlib.Path(templates)
        self.activity_interval_s = activity_interval_s
        self._last_touch: Dict[str, float] = {}
        self._lock = threading.Lock()

    # -- 口令 ---------------------------------------------------------------

    def ensure_password(self, user_id: str) -> str:
        """已有就沿用,没有(或坏了)才生成。**绝不轮换正在用的口令。**"""
        name = "pod-auth-%s" % user_id
        cur = self.kube.get(CORE, "secrets", name)
        if cur:
            raw = (cur.get("data") or {}).get(SECRET_KEY_PASSWORD)
            if raw:
                try:
                    return base64.b64decode(raw).decode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    pass        # 内容坏了 → 往下走重新生成,不要把用户挡在外面
        pw = secrets.token_hex(24)
        self.kube.apply(CORE, "secrets", name, {
            "apiVersion": "v1", "kind": "Secret",
            "metadata": {"name": name, "namespace": self.namespace},
            "type": "Opaque", "stringData": {SECRET_KEY_PASSWORD: pw}})
        return pw

    # -- 建 -----------------------------------------------------------------

    def _render(self, kind: str, user_id: str) -> Dict[str, Any]:
        text = (self.templates / ("%s.yaml" % kind)).read_text(encoding="utf-8")
        rendered = string.Template(text).substitute(
            USER_ID=user_id,
            IMAGE="gaussdb-agent-%s:%s" % (kind, self.image_tag),
            IMAGE_PULL_POLICY=self.pull_policy,
            SUBPATH_ROOT="admins" if kind == KIND_KB_IMPORT else "users")
        return _split_yaml_docs(rendered)

    def kinds_for(self, kb_admin: bool) -> List[str]:
        return [KIND_RUNTIME] + ([KIND_KB_IMPORT] if kb_admin else [])

    def ensure(self, user_id: str, kb_admin: bool) -> str:
        """幂等:建齐该有的对象,返回这个人的 Pod 口令。"""
        pw = self.ensure_password(user_id)
        for kind in self.kinds_for(kb_admin):
            for doc in self._render(kind, user_id):
                k8s_kind = _plural(doc["kind"])
                api = APPS if doc["kind"] == "Deployment" else CORE
                self.kube.apply(api, k8s_kind, doc["metadata"]["name"], doc)
        return pw

    # -- 查 -----------------------------------------------------------------

    def state(self, user_id: str, kind: str = KIND_RUNTIME) -> PodState:
        name = "%s-%s" % (kind, user_id)
        d = self.kube.get(APPS, "deployments", name)
        if not d:
            return PodState(user_id, kind, exists=False, ready=False)
        ready = int((d.get("status") or {}).get("availableReplicas") or 0) >= 1
        ann = ((d.get("metadata") or {}).get("annotations") or {})
        try:
            last = float(ann.get(ANNOTATION_ACTIVITY) or 0)
        except ValueError:
            last = 0.0
        return PodState(user_id, kind, exists=True, ready=ready, last_activity=last)

    # -- 删 -----------------------------------------------------------------

    def remove(self, user_id: str) -> None:
        """删 Deployment / Service / Secret。**PVC 与 NAS 目录绝不动** —— 用户的会话、
        历史、上传的文件全在那里,下次登录挂回来就恢复,不需要任何「还原」步骤。"""
        for kind in (KIND_RUNTIME, KIND_KB_IMPORT):
            name = "%s-%s" % (kind, user_id)
            self.kube.delete(APPS, "deployments", name)
            self.kube.delete(CORE, "services", name)
        for s in ("grmp-token-%s" % user_id, "pod-auth-%s" % user_id):
            self.kube.delete(CORE, "secrets", s)
        with self._lock:
            self._last_touch.pop(user_id, None)

    # -- 活动时间 -----------------------------------------------------------

    def touch(self, user_id: str, now: Optional[float] = None) -> bool:
        """记一次活动。节流:窗口内重复调用直接返回 False,不写 k8s。"""
        now = time.time() if now is None else now
        with self._lock:
            last = self._last_touch.get(user_id, 0.0)
            if now - last < self.activity_interval_s:
                return False
            self._last_touch[user_id] = now
        self.kube.patch(APPS, "deployments", "%s-%s" % (KIND_RUNTIME, user_id), {
            "metadata": {"annotations": {ANNOTATION_ACTIVITY: "%d" % int(now)}}})
        return True


def _plural(kind: str) -> str:
    return {"Deployment": "deployments", "Service": "services", "Secret": "secrets"}[kind]


def _split_yaml_docs(text: str) -> List[Dict[str, Any]]:
    """模板里一个文件放了 Deployment + Service,用 `---` 分隔。

    只用标准库解析不了 YAML,所以这里用 PyYAML —— 它已经是 agent 的依赖之一,
    网关镜像照装,不引入新包。
    """
    import yaml
    return [d for d in yaml.safe_load_all(text) if d]
