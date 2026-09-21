"""网关的 Pod 生命周期策略:建什么、什么时候不重建、活动时间怎么记。

两个必须钉死的行为,都是踩过或查出来的:

① **口令不能在重建时被轮换。** scripts/k8s/provision.py:81 无条件 `secrets.token_hex(24)`
   重新生成 pod-auth-<工号>。2026-09-21 我为了验证改动重建了三次 u9003 的 Pod,
   用户正开着页面 —— 口令变了,他那边下一次请求就是 401,而页面上看不出为什么。
   网关必须:Secret 已存在就沿用,只有不存在时才生成。

② **活动时间要节流。** 每个请求都去 PATCH 一次 Deployment 注解的话,一个用户开着
   SSE 每 10 秒一跳就是每 10 秒一次 k8s 写操作,几十个用户就把 apiserver 打满了。
"""
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from gateway import pods  # noqa: E402


class FakeKube:
    """只记下被调用了什么,不真连 k8s。"""

    def __init__(self, existing=None):
        self.objects = dict(existing or {})     # (kind, name) -> manifest
        self.applied = []
        self.deleted = []
        self.patched = []

    def get(self, api, kind, name):
        return self.objects.get((kind, name))

    def list(self, api, kind, label_selector=""):
        return {"items": [v for (k, _n), v in self.objects.items() if k == kind]}

    def apply(self, api, kind, name, manifest):
        self.applied.append((kind, name, manifest))
        self.objects[(kind, name)] = manifest
        return manifest

    def patch(self, api, kind, name, patch):
        self.patched.append((kind, name, patch))
        return {}

    def delete(self, api, kind, name):
        self.deleted.append((kind, name))
        return self.objects.pop((kind, name), None) is not None


def _mgr(kube, **kw):
    opts = dict(namespace="gaussdb-agent", image_tag="t1", pull_policy="IfNotPresent",
                templates=_ROOT / "k8s" / "templates")
    opts.update(kw)
    return pods.Manager(kube, **opts)


# --- 口令:重建时必须沿用 -------------------------------------------------------

def test_password_is_generated_when_absent():
    k = FakeKube()
    pw = _mgr(k).ensure_password("u1234")
    assert len(pw) >= 32
    assert ("secrets", "pod-auth-u1234") in [(kind, name) for kind, name, _ in k.applied]


def test_existing_password_is_reused_not_rotated():
    """**核心**:重建 Pod 不能让用户手上的口令失效。"""
    import base64
    existing = base64.b64encode(b"already-issued-password").decode()
    k = FakeKube({("secrets", "pod-auth-u1234"): {"data": {"OPENCODE_SERVER_PASSWORD": existing}}})
    assert _mgr(k).ensure_password("u1234") == "already-issued-password"
    assert not k.applied, "已有口令时不该再写 Secret"


def test_corrupt_password_secret_is_replaced():
    """Secret 在但内容坏了(手工改过 / key 写错):重新生成,不要抛异常把用户挡在外面。"""
    k = FakeKube({("secrets", "pod-auth-u1234"): {"data": {}}})
    assert len(_mgr(k).ensure_password("u1234")) >= 32
    assert k.applied


# --- 建 Pod -------------------------------------------------------------------

def test_normal_user_gets_runtime_only():
    k = FakeKube()
    _mgr(k).ensure("u1234", kb_admin=False)
    names = [name for kind, name, _ in k.applied if kind == "deployments"]
    assert names == ["runtime-u1234"]


def test_kb_admin_also_gets_kb_import():
    k = FakeKube()
    _mgr(k).ensure("u2001", kb_admin=True)
    names = sorted(name for kind, name, _ in k.applied if kind == "deployments")
    assert names == ["kb-import-u2001", "runtime-u2001"]


def test_ensure_is_idempotent_on_the_deployment():
    """server-side apply,重复调用不报错也不产生第二份。"""
    k = FakeKube()
    m = _mgr(k)
    m.ensure("u1234", kb_admin=False)
    m.ensure("u1234", kb_admin=False)
    assert len([n for kind, n, _ in k.applied if kind == "deployments"]) == 2   # 两次 apply
    assert len([key for key in k.objects if key[0] == "deployments"]) == 1      # 但只有一份对象


def test_delete_removes_pods_and_secrets_but_not_nas():
    k = FakeKube({("deployments", "runtime-u1234"): {}, ("services", "runtime-u1234"): {},
                  ("secrets", "pod-auth-u1234"): {}})
    _mgr(k).remove("u1234")
    kinds = {kind for kind, _ in k.deleted}
    assert kinds <= {"deployments", "services", "secrets"}
    assert not any("persistentvolumeclaims" == kind for kind, _ in k.deleted), "NAS 与 PVC 绝不能删"


# --- 活动时间 -----------------------------------------------------------------

def test_activity_is_recorded_as_an_annotation():
    k = FakeKube()
    _mgr(k).touch("u1234", now=1000.0)
    kind, name, patch = k.patched[0]
    assert kind == "deployments" and name == "runtime-u1234"
    assert pods.ANNOTATION_ACTIVITY in patch["metadata"]["annotations"]


def test_activity_writes_are_throttled():
    """**核心**:SSE 每 10 秒一跳,不节流就是每 10 秒一次 k8s 写。"""
    k = FakeKube()
    m = _mgr(k, activity_interval_s=60)
    m.touch("u1234", now=1000.0)
    m.touch("u1234", now=1005.0)          # 5 秒后,不该再写
    m.touch("u1234", now=1005.0)
    assert len(k.patched) == 1
    m.touch("u1234", now=1070.0)          # 过了节流窗口
    assert len(k.patched) == 2


def test_throttle_is_per_user():
    k = FakeKube()
    m = _mgr(k, activity_interval_s=60)
    m.touch("u1234", now=1000.0)
    m.touch("u5678", now=1001.0)
    assert len(k.patched) == 2, "一个人的活动不能把另一个人的记录压掉"
