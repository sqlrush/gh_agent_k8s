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


def test_every_tunable_the_code_reads_is_reachable_from_the_configmap():
    """**配置项必须真的能配到。**

    gateway/config.py 读一堆 GATEWAY_* 变量,但容器里的 env 是 gateway.yaml 一条条
    映射进去的 —— 代码读了、清单没映射的那些,运维在 ConfigMap 里写上去是**静默无效**:
    值收下了,行为一点不变,而且没有任何报错。写参数手册时才发现有四个是这样。

    监听端口是唯一的例外:改它还要同步改 containerPort 与 Service 的 targetPort,
    不是「配一下」的事,所以刻意不给出口,在手册里写明。
    """
    cfg = (_ROOT / "gateway" / "config.py").read_text(encoding="utf-8")
    read_by_code = set(re.findall(r'env\.get\("(GATEWAY_[A-Z_]+)"\)', cfg))
    read_by_code |= set(re.findall(r'_int\(env,\s*"(GATEWAY_[A-Z_]+)"', cfg))
    assert len(read_by_code) >= 10, "变量名提取失掉了?只找到 %r" % (read_by_code,)

    manifest = (_ROOT / "k8s" / "base" / "gateway.yaml").read_text(encoding="utf-8")
    provided = set(re.findall(r"\{name:\s*(GATEWAY_[A-Z_]+)", manifest))

    missing = read_by_code - provided - {"GATEWAY_LISTEN_PORT"}
    assert not missing, (
        "gateway.yaml 没把这些变量映射进容器,在 ConfigMap 里配它们是静默无效的:%s"
        % ", ".join(sorted(missing)))


def test_configmap_keys_are_all_consumed_by_the_deployment():
    """反向:ConfigMap 里有键而清单没引用,同样是「配了没用」。"""
    cm = (_ROOT / "k8s" / "base" / "configmap-gateway.yaml").read_text(encoding="utf-8")
    body = cm[cm.index("data:"):]
    keys = {m.group(1) for m in re.finditer(r"^\s{2}([A-Z][A-Z0-9_]*):", body, re.MULTILINE)}
    assert keys, "configmap-gateway.yaml 的 data 解析不出键?"

    manifest = (_ROOT / "k8s" / "base" / "gateway.yaml").read_text(encoding="utf-8")
    referenced = set(re.findall(r"configMapKeyRef:\s*\{name:\s*gateway-config,\s*key:\s*([A-Z0-9_]+)",
                                manifest))
    orphans = keys - referenced
    assert not orphans, "ConfigMap 里这些键没人读:%s" % ", ".join(sorted(orphans))


def test_nfs_address_lives_in_the_pv_example_not_in_the_pvc():
    """**NFS 地址在 PV 里,不在 PVC 里;文档不能让人去 PVC 里找 nfs:。**

    2026-09-22 客户问「nas-pvc.yaml 是配 NAS 的吗」时翻出来:两份手册都写着
    「编辑 k8s/base/nas-pvc.yaml,把 nfs: 那几行改成…」,而那个文件从头到尾只有一个
    PersistentVolumeClaim,根本没有 nfs: —— PVC 是申请,供给在 PV / StorageClass。
    仓库里给客户用的 PV 样例当时也不存在(只有 Mac 用的 hostPath 那份)。
    客户照着做要么找不到那几行,要么把 nfs: 塞进 PVC 让 apply 报未知字段。
    """
    pvc = (_ROOT / "k8s" / "base" / "nas-pvc.yaml").read_text(encoding="utf-8")
    assert "kind: PersistentVolumeClaim" in pvc and "nfs:" not in pvc.replace("nas-pv-nfs", "")

    pv = _ROOT / "k8s" / "base" / "nas-pv-nfs.example.yaml"
    assert pv.is_file(), "给客户用的静态 PV 样例不存在"
    body = pv.read_text(encoding="utf-8")
    assert "kind: PersistentVolume\n" in body and "\n  nfs:\n" in body
    assert "local_lock=all" in body, "mountOptions 少了 local_lock=all —— 目录独占靠它"
    assert "persistentVolumeReclaimPolicy: Retain" in body, "回收策略必须 Retain,那是用户数据"
    assert "storageClassName: nas" in body and "storageClassName: nas" in pvc, \
        "PV 与 PVC 的 storageClassName 必须一致,否则绑不上"

    # 凡是教读者填 nfs: 的手册,都必须指向真有 nfs: 的那个文件
    for name in ("快速搭建-K8s.md", "部署手册-从零到上线.md", "命令卡-测试环境搭建.md"):
        doc = (_ROOT / "docs" / name).read_text(encoding="utf-8")
        if "nfs:" in doc:
            assert "nas-pv-nfs.example.yaml" in doc, \
                "%s 教人填 nfs: 却没指向 nas-pv-nfs.example.yaml" % name


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
