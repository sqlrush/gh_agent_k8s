"""清单里两条容易回退的约束,都是 2026-09-21 真部署时踩出来的。

① **enableServiceLinks 必须显式关掉。** k8s 默认把命名空间里每个 Service 注入成环境变量。
   后果有两层:
   · 撞名:Service 叫 gateway → 注入 GATEWAY_PORT=`tcp://10.x.x.x:80`,和网关自己的
     配置项撞名,网关拒绝启动(fail closed 救了场,但本来不该发生);
   · **跨用户泄露**:用户 Pod 的 env 里会有 RUNTIME_<别人工号>_SERVICE_HOST,
     用户让模型打印环境变量就知道还有谁在用 —— 与「本对话看不到别人的会话」直接矛盾。
     u9003 的 Pod 里实测到了 u9001 与 u9002。

② **emptyDir 必须有 sizeLimit 且不能是 tmpfs。** /data 现在装本地态(库 + git 快照):
   没有 sizeLimit 会把节点磁盘写满;写 medium: Memory 会让用户的历史吃进内存配额;
   而撑爆 sizeLimit 会**驱逐** Pod,驱逐是非优雅终止 = 丢一个同步周期。
"""
import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_POD_MANIFESTS = [
    _ROOT / "k8s" / "templates" / "runtime.yaml",
    _ROOT / "k8s" / "templates" / "kb-import.yaml",
    _ROOT / "k8s" / "base" / "gateway.yaml",
]


@pytest.mark.parametrize("path", _POD_MANIFESTS, ids=lambda p: p.name)
def test_service_links_are_disabled(path):
    text = path.read_text(encoding="utf-8")
    assert re.search(r"^\s*enableServiceLinks:\s*false\s*$", text, re.MULTILINE), (
        "%s 没关 enableServiceLinks —— 会把其他用户的 Service 注入成环境变量" % path.name)


@pytest.mark.parametrize("path", _POD_MANIFESTS[:2], ids=lambda p: p.name)
def test_emptydir_has_a_size_limit_and_is_not_tmpfs(path):
    text = path.read_text(encoding="utf-8")
    empties = re.findall(r"emptyDir:\s*\{([^}]*)\}", text)
    assert empties, "%s 里没有 emptyDir?" % path.name
    for body in empties:
        assert "sizeLimit" in body, "%s 有 emptyDir 没设 sizeLimit:{%s}" % (path.name, body)
        assert "medium" not in body, (
            "%s 的 emptyDir 指定了 medium(tmpfs)——用户的库会吃进内存配额:{%s}" % (path.name, body))


def test_gateway_rbac_cannot_list_secrets():
    """网关能建别人的口令 Secret,但**不能 list** —— 给了 list 就等于一次请求
    拿到全体用户的口令。这条权限边界要有人守着。"""
    text = (_ROOT / "k8s" / "base" / "gateway.yaml").read_text(encoding="utf-8")
    block = text[text.index('resources: ["secrets"]'):]
    verbs = re.search(r'verbs:\s*\[([^\]]*)\]', block).group(1)
    assert '"list"' not in verbs, "网关对 secrets 有 list 权限:%s" % verbs
    assert '"get"' in verbs, "要保留 get —— 「已有口令就沿用」靠它"


def test_gateway_rbac_has_no_exec():
    """网关没有进用户容器的理由。

    **要剔掉注释再查**:清单里有一行注释写着「刻意不给 pods/exec」,
    直接对全文做子串搜索会把那行当成权限(第一版就这么红了)。
    """
    lines = [ln for ln in (_ROOT / "k8s" / "base" / "gateway.yaml")
             .read_text(encoding="utf-8").splitlines()
             if not ln.lstrip().startswith("#")]
    body = "\n".join(lines)
    for forbidden in ("pods/exec", "pods/attach", "pods/portforward"):
        assert forbidden not in body, "网关 RBAC 里出现了 %s" % forbidden
